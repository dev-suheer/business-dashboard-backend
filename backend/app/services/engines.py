"""How a run calls the generic runner, summarises its result for the history
list, and names its report.

Everything the runner does, from reading the uploads to the history entry and
the report's name, is in the category's configuration; this is the seam the
run service calls it through.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config_model.models import CategoryConfig
from app.core.config import settings
from app.expr import text
from app.recon.inputs import Upload
from app.render import generic_report
from app.runner import RunResult
from app.runner import run as run_generic


@dataclass(frozen=True)
class RunOptions:
    # Per-run overrides of the configured parameters, by key.
    overrides: dict[str, Any] | None = None


@dataclass(frozen=True)
class Engine:
    key: str
    # Reconcile, returning the result and the parameters it was run with.
    run: Callable[
        [dict[str, Upload], RunOptions, CategoryConfig], tuple[Any, dict[str, Any]]
    ]
    build_report: Callable[[Any, Path, dict[str, Path]], Path]
    # Key figures for the history list.
    summary: Callable[[Any], dict[str, Any]]
    period: Callable[[Any], tuple[date | None, date | None]]
    report_name: Callable[[Any], str]


def _figures(
    counted: int, total: int, unit: str, net: Decimal, vendor: str | None
) -> dict[str, Any]:
    return {
        "counted": counted,
        "not_counted": total - counted,
        "unit": unit,
        "net_payable": str(net),
        "currency": "SAR",
        "vendor": vendor,
    }


# --- The generic runner ---------------------------------------------------------


def _run(
    uploads: dict[str, Upload], options: RunOptions, config: CategoryConfig
) -> tuple[RunResult, dict[str, Any]]:
    result = run_generic(
        config,
        uploads,
        overrides=options.overrides,
        max_rows=settings.MAX_ROWS_PER_SHEET,
    )
    return result, {key: text(value) for key, value in result.parameters.items()}


def _summary(result: RunResult) -> dict[str, Any]:
    h = result.history
    return _figures(
        int(h["counted"]),
        int(h["total"]),
        text(h["unit"]),
        h["net_payable"],
        h["vendor"],
    )


GENERIC = Engine(
    key="generic",
    run=_run,
    build_report=generic_report,
    summary=_summary,
    period=lambda result: (
        result.history["period_start"],
        result.history["period_end"],
    ),
    report_name=lambda result: text(result.history["report_name"]),
)


ENGINES: dict[str, Engine] = {GENERIC.key: GENERIC}
