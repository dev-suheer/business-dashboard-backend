"""Reconciliation runs: start one, list them, open one, download its files.

Every run is kept: its uploads as they arrived, its report and its key figures.
"""

import shutil
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.api.deps import get_current_user
from app.api.routes._uploads import XLSX_MEDIA_TYPE, spool
from app.db.models import Run, RunStatus
from app.db.session import get_session
from app.recon.inputs import Upload
from app.services import configs, storage
from app.services.engines import RunOptions
from app.services.runs import (
    RunFilter,
    RunRequestError,
    execute_run,
    get_run,
    list_runs,
)

router = APIRouter(
    prefix="/runs", tags=["runs"], dependencies=[Depends(get_current_user)]
)

SessionDep = Annotated[Session, Depends(get_session)]


class CategoryRef(BaseModel):
    key: str
    name: str


class RunFilePublic(BaseModel):
    role: str
    filename: str
    size: int
    sha256: str
    manifest: list[dict[str, Any]] | None = None


class KeyFigures(BaseModel):
    counted: int
    not_counted: int
    unit: str
    net_payable: Decimal
    currency: str
    vendor: str | None = None


class ExceptionCounts(BaseModel):
    by_severity: dict[str, int]
    by_code: dict[str, int]


class RunPublic(BaseModel):
    number: int
    category: CategoryRef
    # The configuration version the run was reconciled under.
    version: int | None
    status: RunStatus
    period_start: date | None
    period_end: date | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    parameters: dict[str, Any]
    summary: KeyFigures | None
    exception_counts: ExceptionCounts | None
    error: str | None
    report_filename: str | None
    files: list[RunFilePublic]


class RunPage(BaseModel):
    items: list[RunPublic]
    total: int
    page: int
    page_size: int


def to_public(run: Run) -> RunPublic:
    return RunPublic(
        number=run.id,
        category=CategoryRef(key=run.category.key, name=run.category.name),
        version=run.version.number if run.version else None,
        status=RunStatus(run.status),
        period_start=run.period_start,
        period_end=run.period_end,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=run.duration_ms,
        parameters=run.parameters,
        summary=KeyFigures.model_validate(run.summary) if run.summary else None,
        exception_counts=(
            ExceptionCounts.model_validate(run.exception_counts)
            if run.exception_counts
            else None
        ),
        error=run.error,
        report_filename=run.report_filename,
        files=[
            RunFilePublic(
                role=f.role,
                filename=f.filename,
                size=f.size,
                sha256=f.sha256,
                manifest=f.manifest,
            )
            for f in run.files
        ],
    )


def _form_text(value: Any, name: str) -> str | None:
    if value is None or isinstance(value, UploadFile):
        if isinstance(value, UploadFile):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{name!r} must be text"
            )
        return None
    text = str(value).strip()
    return text or None


def _parse(value: str | None, kind: type, name: str) -> Any:
    if value is None:
        return None
    try:
        if kind is date:
            return date.fromisoformat(value)
        if kind is Decimal:
            return Decimal(value)
    except (ValueError, InvalidOperation):
        pass
    else:
        return value
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        f"{name!r} is not a valid {kind.__name__}",
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=RunPublic,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["category"],
                        "properties": {
                            "category": {"type": "string"},
                        },
                        "additionalProperties": {"type": "string", "format": "binary"},
                    }
                }
            },
        }
    },
)
async def create_run(request: Request, session: SessionDep) -> RunPublic:
    """
    Reconcile one set of files and keep the run.

    Multipart fields: ``category`` (eshop, travel, food), one workbook per input
    role the category takes (see ``GET /categories``). The run's period is
    worked out from the files. A category's rate overrides (eShop
    ``standard_rate``, ``promo_rate``, ``promo_cutoff``; travel ``hotel_rate``,
    ``flight_rate``, ``vat_rate``) are accepted as fields too.

    A file that does not fit its category is a **failed run**, returned with its
    reason and kept in History; the status code is still 201, because the run
    exists. A request that cannot start a run at all (unknown category, a missing
    or unknown file, a file that is not a workbook) is a 4xx and leaves no trace.
    """
    form = await request.form()
    try:
        key = _form_text(form.get("category"), "category")
        if key is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "'category' is required"
            )
        try:
            _, _, config = configs.current(session, key)
        except configs.ConfigurationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
        options = RunOptions(
            overrides={
                p.key: _parse(
                    _form_text(form.get(p.key), p.key),
                    date if p.kind == "date" else Decimal,
                    p.key,
                )
                for p in config.parameters
                if p.overridable
            },
        )
        files = {
            name: value
            for name, value in form.multi_items()
            if isinstance(value, UploadFile) and value.filename
        }

        workdir = Path(tempfile.mkdtemp(prefix="recon-run-"))
        try:
            uploads: dict[str, Upload] = {}
            for role, upload in files.items():
                uploads[role] = await spool(upload, role, workdir)
            run = await run_in_threadpool(execute_run, session, key, uploads, options)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    except RunRequestError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    finally:
        await form.close()
    return to_public(run)


@router.get("", response_model=RunPage)
def read_runs(
    session: SessionDep,
    category: str | None = None,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    period_from: date | None = None,
    period_to: date | None = None,
    run_from: datetime | None = None,
    run_to: datetime | None = None,
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> RunPage:
    """Past runs, newest first. ``q`` matches a run number or part of a file name."""
    where = RunFilter(
        category=category,
        status=run_status,
        period_from=period_from,
        period_to=period_to,
        run_from=run_from,
        run_to=run_to,
        query=q,
    )
    runs, total = list_runs(session, where, page=page, page_size=page_size)
    return RunPage(
        items=[to_public(run) for run in runs],
        total=total,
        page=page,
        page_size=page_size,
    )


def _found(session: Session, number: int) -> Run:
    run = get_run(session, number)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {number}")
    return run


@router.get("/{number}", response_model=RunPublic)
def read_run(number: int, session: SessionDep) -> RunPublic:
    return to_public(_found(session, number))


@router.get(
    "/{number}/report",
    response_class=FileResponse,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}}},
)
def download_report(number: int, session: SessionDep) -> FileResponse:
    run = _found(session, number)
    if run.report_path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run {number} has no report")
    return FileResponse(
        storage.resolve(run.report_path),
        media_type=XLSX_MEDIA_TYPE,
        filename=run.report_filename,
    )


@router.get(
    "/{number}/files/{role}",
    response_class=FileResponse,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}}},
)
def download_file(number: int, role: str, session: SessionDep) -> FileResponse:
    run = _found(session, number)
    stored = next((f for f in run.files if f.role == role), None)
    if stored is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Run {number} has no {role!r} file"
        )
    return FileResponse(
        storage.resolve(stored.path),
        media_type=XLSX_MEDIA_TYPE,
        filename=stored.filename,
    )
