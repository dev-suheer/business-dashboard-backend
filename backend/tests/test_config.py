"""Category configuration: version 1 is the approved code, and a run obeys it."""

import json
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config_model.models import CategoryConfig, ExceptionDef
from app.config_model.runtime import use_config
from app.config_model.seed import CATEGORIES, build_seed, load_seed, seed_path
from app.db.models import Category
from app.recon.excel import SheetFormatError
from app.recon.exceptions import ExceptionCode
from app.recon.inputs import ReconInputError, Upload
from app.runner import run
from tests.parity.scenarios import SCENARIOS, Scenario


@pytest.mark.parametrize("category", CATEGORIES)
def test_the_seed_files_are_what_the_code_says(category: str) -> None:
    """Version 1 cannot drift from the approved code: regenerate and compare."""
    assert build_seed(category) == load_seed(category)


@pytest.mark.parametrize("category", CATEGORIES)
def test_version_one_in_the_database_is_the_seed(
    db_session: Session, category: str
) -> None:
    row = db_session.scalar(select(Category).where(Category.key == category))
    assert row is not None and row.current_version is not None
    assert row.current_version.number == 1
    stored = CategoryConfig.model_validate(row.current_version.config)
    assert stored == load_seed(category)
    assert row.current_version.config == json.loads(seed_path(category).read_text())


def _travel_13() -> Scenario:
    return next(s for s in SCENARIOS if s.name == "travel-claim-13")


def _with(config: CategoryConfig, **changes: object) -> CategoryConfig:
    return config.model_copy(update=changes)


def _run_travel(config: CategoryConfig, files: dict[str, Path]) -> dict[str, Any]:
    """Reconcile travel under ``config``, as the run service does."""
    uploads = {
        role: Upload(role, path.name, path)
        for role, path in files.items()
        if role in {"portal", "vendor", "wallet"}
    }
    with use_config(config):
        result = run(config, uploads, max_rows=1_000_000)
    return json.loads(result.model_dump_json())


def _severities(result: dict[str, Any], code: str) -> set[str]:
    return {e["severity"] for e in result["exceptions"] if e["code"] == code}


def test_a_severity_set_in_the_configuration_is_the_one_reported() -> None:
    code = ExceptionCode.BOOKING_BASE_MISMATCH
    seed = load_seed("travel")
    softened = _with(
        seed,
        exceptions=[
            ExceptionDef(code=e.code, severity="info") if e.code == code else e
            for e in seed.exceptions
        ],
    )
    files = _travel_13().files
    assert _severities(_run_travel(seed, files), code) == {"high"}
    assert _severities(_run_travel(softened, files), code) == {"info"}


def test_a_renamed_column_is_read_once_its_new_header_is_configured(
    tmp_path: Path,
) -> None:
    """The vendor-renamed-a-column request, answered by configuration alone."""
    files = _travel_13().files
    renamed = tmp_path / "portal.xlsx"
    book = load_workbook(files["portal"])
    hits = 0
    for sheet in book.worksheets:
        for row in sheet.iter_rows(max_row=30):
            for cell in row:
                if cell.value == "Order Status":
                    cell.value = "Booking Status"
                    hits += 1
    assert hits
    book.save(renamed)
    renamed_files = {**files, "portal": renamed}

    seed = load_seed("travel")
    with pytest.raises((ReconInputError, SheetFormatError)):
        _run_travel(seed, renamed_files)

    portal = seed.input("portal")
    assert portal is not None
    sheet = portal.sheets[0]
    columns = [
        c.model_copy(update={"headers": [*c.headers, "Booking Status"]})
        if "Order Status" in c.headers
        else c
        for c in sheet.columns
    ]
    taught = _with(
        seed,
        inputs=[
            f.model_copy(
                update={"sheets": [sheet.model_copy(update={"columns": columns})]}
            )
            if f.role == "portal"
            else f
            for f in seed.inputs
        ],
    )
    result = _run_travel(taught, renamed_files)
    original = _run_travel(seed, files)
    assert result["metrics"] == original["metrics"]
    assert result["exceptions"] == original["exceptions"]
