"""The report workbook, from a category's output configuration and a run's result."""

from pathlib import Path

from app.render import generic
from app.render.report import render
from app.runner import RunResult


def generic_report(
    result: RunResult, path: Path, sources: dict[str, Path] | None = None
) -> Path:
    """A run's report, under the configuration it ran with."""
    return render(result.config, generic.report_data(result, sources), path, sources)
