"""Running a reconciliation, and keeping it for later review.

A run is reconciled under its category's current configuration, which it
records, so History can say which rules produced a report. It is recorded
before any work starts, so a run that fails still has a row with the reason.
Its uploads are copied into the run store as they arrived, with their sha256,
and its report and full result are written next to them.
"""

import gzip
import logging
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.config_model.models import CategoryConfig
from app.config_model.runtime import schema_of, use_config
from app.db.models import Category, Run, RunFile, RunStatus
from app.expr import FormulaError
from app.recon.excel import SheetFormatError
from app.recon.inputs import ReconInputError, Upload
from app.render.lookups import copy_layout
from app.render.report import ConfigurationError as RenderConfigurationError
from app.services import configs, storage
from app.services.engines import ENGINES, RunOptions

logger = logging.getLogger(__name__)

UNEXPECTED_ERROR = (
    "The reconciliation stopped on an unexpected error; see the server log."
)


class RunRequestError(ValueError):
    """The request cannot start a run: unknown category, or files missing or unknown."""


def check_uploads(config: CategoryConfig, uploads: dict[str, Upload]) -> None:
    roles = {f.role for f in config.inputs}
    unknown = sorted(set(uploads) - roles)
    if unknown:
        raise RunRequestError(
            f"{config.category} takes no file named {', '.join(map(repr, unknown))}"
        )
    missing = [f.role for f in config.inputs if f.required and f.role not in uploads]
    if missing:
        raise RunRequestError(
            f"Missing required file(s): {', '.join(map(repr, missing))}"
        )


def execute_run(
    session: Session, category_key: str, uploads: dict[str, Upload], options: RunOptions
) -> Run:
    """Reconcile and record. Returns the run, completed or failed.

    A file that does not fit its category (a missing sheet or column) is a
    failed run, kept with its message. Anything unexpected is recorded as
    failed too, and then raised.
    """
    try:
        category, version, config = configs.current(session, category_key)
    except configs.ConfigurationError as exc:
        raise RunRequestError(str(exc)) from exc
    check_uploads(config, uploads)
    engine = ENGINES[config.engine]

    run = Run(
        category=category, version=version, status=RunStatus.RUNNING, parameters={}
    )
    session.add(run)
    session.commit()
    clock = time.perf_counter()

    directory = storage.run_dir(run.id)
    stored: dict[str, Upload] = {}
    for role, upload in uploads.items():
        target = directory / "inputs" / f"{role}{upload.path.suffix.lower()}"
        size, digest = storage.copy_with_digest(upload.path, target)
        run.files.append(
            RunFile(
                role=role,
                filename=upload.filename,
                size=size,
                sha256=digest,
                path=storage.relative(target),
            )
        )
        stored[role] = Upload(role=role, filename=upload.filename, path=target)
    session.commit()

    try:
        # Reject a reference file (a finance report, say) without its sheet
        # before the recon runs: its copy and the look-ups into it read it.
        for spec in config.inputs:
            if spec.kind == "reference" and spec.role in stored:
                for sheet in spec.sheets:
                    if not sheet.optional:
                        copy_layout(stored[spec.role].path, schema_of(sheet), spec.role)
        report = directory / "report.xlsx"
        # Every upload, for the tables that copy an upload or list its rows.
        sources = {role: u.path for role, u in stored.items()}
        # The pipeline reads its sheet schemas, severities and labels from this.
        with use_config(config):
            result, parameters = engine.run(stored, options, config)
            engine.build_report(result, report, sources)
        result_file = directory / "result.json.gz"
        with gzip.open(result_file, "wt", encoding="utf-8") as sink:
            sink.write(result.model_dump_json())

        run.parameters = parameters
        run.period_start, run.period_end = engine.period(result)
        run.summary = engine.summary(result)
        run.exception_counts = exception_counts(result.exceptions)
        run.report_filename = engine.report_name(result)
        run.report_path = storage.relative(report)
        run.report_size, run.report_sha256 = storage.file_digest(report)
        run.result_path = storage.relative(result_file)
        _attach_manifests(run, [m.model_dump(mode="json") for m in result.detected])
        run.status = RunStatus.COMPLETED
    except (ReconInputError, SheetFormatError) as exc:
        run.status = RunStatus.FAILED
        run.error = str(exc)
    except (FormulaError, RenderConfigurationError) as exc:
        # A configuration saved with a formula that fails on these files.
        run.status = RunStatus.FAILED
        run.error = (
            f"Setup version {version.number} could not reconcile these files: {exc}"
        )
    except Exception:
        logger.exception("run %s failed", run.id)
        run.status = RunStatus.FAILED
        run.error = UNEXPECTED_ERROR
        raise
    finally:
        run.finished_at = datetime.now(UTC)
        run.duration_ms = round((time.perf_counter() - clock) * 1000)
        session.commit()
    return run


def exception_counts(exceptions: Sequence[Any]) -> dict[str, dict[str, int]]:
    return {
        "by_severity": dict(Counter(str(exc.severity) for exc in exceptions)),
        "by_code": dict(Counter(str(exc.code) for exc in exceptions)),
    }


def _attach_manifests(run: Run, manifests: list[dict[str, Any]]) -> None:
    """Which sheets each file was read from. A role may note more than one read:
    travel reads its claim lines and, as ``vendor-ledger``, the claim's summary."""
    for file in run.files:
        mine = [
            m
            for m in manifests
            if m["role"] == file.role or m["role"].startswith(f"{file.role}-")
        ]
        file.manifest = mine or None


# --- Reading history -----------------------------------------------------------


@dataclass(frozen=True)
class RunFilter:
    category: str | None = None
    status: str | None = None
    # Runs whose period overlaps [period_from, period_to].
    period_from: date | None = None
    period_to: date | None = None
    # Runs started in [run_from, run_to).
    run_from: datetime | None = None
    run_to: datetime | None = None
    # A run number, or part of an uploaded file's name.
    query: str | None = None


def _filtered(statement: Select[Any], where: RunFilter) -> Select[Any]:
    if where.category:
        statement = statement.where(Category.key == where.category)
    if where.status:
        statement = statement.where(Run.status == where.status)
    if where.period_from:
        statement = statement.where(Run.period_end >= where.period_from)
    if where.period_to:
        statement = statement.where(Run.period_start <= where.period_to)
    if where.run_from:
        statement = statement.where(Run.started_at >= where.run_from)
    if where.run_to:
        statement = statement.where(Run.started_at < where.run_to)
    if where.query:
        text = where.query.strip()
        by_file = Run.files.any(RunFile.filename.ilike(f"%{text}%"))
        number = text.lstrip("#").lstrip("0") or "0"
        statement = statement.where(
            or_(Run.id == int(number), by_file) if number.isdigit() else by_file
        )
    return statement


def list_runs(
    session: Session, where: RunFilter, *, page: int, page_size: int
) -> tuple[list[Run], int]:
    base = select(Run).join(Run.category)
    total = session.scalar(
        _filtered(select(func.count()).select_from(Run).join(Run.category), where)
    )
    runs = session.scalars(
        _filtered(base, where)
        .order_by(Run.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique()
    return list(runs), total or 0


def get_run(session: Session, number: int) -> Run | None:
    return session.get(Run, number)
