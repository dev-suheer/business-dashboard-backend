"""Runs: a run is reconciled exactly as before, and kept with everything it used."""

import gzip
import hashlib
import json
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config_model.check import problems
from app.config_model.models import ExceptionDef
from app.config_model.standard_tabs import copy_tab
from app.core.config import settings
from app.db.models import Category, CategoryVersion
from app.recon.cleaning import normalize_header
from app.services.configs import config_of
from tests.conftest import (
    FOOD_PORTAL,
    FOOD_VENDOR,
    TRAVEL_CLAIMS,
    TRAVEL_PORTAL,
    VENDOR_STATEMENT,
    WORKINGS,
)
from tests.parity.compare import json_differences, workbook_differences
from tests.parity.scenarios import BASELINE, SCENARIOS, comparable, expected

API = settings.API_V1_STR
RUNS = f"{API}/runs"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _files(paths: dict[str, Path]) -> dict[str, tuple[str, bytes, str]]:
    return {role: (path.name, path.read_bytes(), XLSX) for role, path in paths.items()}


def _start(client: TestClient, category: str, paths: dict[str, Path], **fields: str):
    return client.post(RUNS, data={"category": category, **fields}, files=_files(paths))


def _travel(client: TestClient, **fields: str):
    return _start(
        client,
        "travel",
        {"portal": TRAVEL_PORTAL, "vendor": TRAVEL_CLAIMS["13"]},
        **fields,
    )


def _eshop(client: TestClient, **fields: str):
    return _start(
        client,
        "eshop",
        {"portal": WORKINGS, "promo": WORKINGS, "vendor": VENDOR_STATEMENT},
        **fields,
    )


def _result(store: Path, run: dict[str, Any]) -> dict[str, Any]:
    """The full result a run kept."""
    path = store / "runs" / f"{run['number']:06d}" / "result.json.gz"
    with gzip.open(path) as kept:
        result: dict[str, Any] = json.load(kept)
    return result


def test_categories_list_the_files_each_one_takes(runs_client: TestClient) -> None:
    response = runs_client.get(f"{API}/categories")
    assert response.status_code == 200
    categories = {c["key"]: c for c in response.json()}
    assert list(categories) == ["eshop", "travel", "food"]

    assert {c["version"] for c in categories.values()} == {1}
    travel = categories["travel"]["inputs"]
    assert [i["role"] for i in travel] == ["portal", "vendor", "wallet", "finance"]
    assert [i["required"] for i in travel] == [True, True, False, False]
    # Accepted, but not offered on the upload screen: as today.
    settlements = next(
        i for i in categories["eshop"]["inputs"] if i["role"] == "settlements"
    )
    assert settlements["shown"] is False


@pytest.mark.parametrize(
    "name", ["eshop-wallet-finance", "travel-claim-13-finance", "food-wallet-finance"]
)
def test_a_run_reproduces_the_baseline_and_keeps_its_files(
    runs_client: TestClient, run_store: Path, tmp_path: Path, name: str
) -> None:
    scenario = next(s for s in SCENARIOS if s.name == name)
    response = _start(runs_client, scenario.category, scenario.files)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "completed"
    assert run["error"] is None

    # The full result and the report are the approved ones, unchanged.
    result = _result(run_store, run)
    assert json_differences(comparable(scenario, result), expected(scenario)) == []

    report = runs_client.get(f"{RUNS}/{run['number']}/report")
    assert report.status_code == 200
    assert run["report_filename"] in report.headers["content-disposition"]
    downloaded = tmp_path / "report.xlsx"
    downloaded.write_bytes(report.content)
    assert workbook_differences(downloaded, BASELINE / name / "report.xlsx") == []

    # Every upload comes back byte for byte, with its fingerprint on record.
    kept_files = {f["role"]: f for f in run["files"]}
    assert set(kept_files) == set(scenario.files)
    for role, path in scenario.files.items():
        original = path.read_bytes()
        got = runs_client.get(f"{RUNS}/{run['number']}/files/{role}")
        assert got.content == original
        assert kept_files[role]["sha256"] == hashlib.sha256(original).hexdigest()
        assert kept_files[role]["filename"] == path.name


def test_a_run_keeps_its_key_figures_and_the_sheets_it_read(
    runs_client: TestClient,
) -> None:
    run = _travel(runs_client).json()
    assert run["category"] == {"key": "travel", "name": "Travel"}
    assert run["version"] == 1
    assert run["summary"]["unit"] == "bookings"
    assert run["summary"]["counted"] > 0
    assert run["exception_counts"]["by_severity"]
    assert run["period_start"] and run["period_end"]
    assert run["duration_ms"] is not None
    assert run["parameters"]["flight_rate"] == "0.03"
    vendor = next(f for f in run["files"] if f["role"] == "vendor")
    assert vendor["manifest"] and vendor["manifest"][0]["sheets"]


def test_a_rate_override_is_recorded_with_the_run(runs_client: TestClient) -> None:
    run = _travel(runs_client, flight_rate="0.04").json()
    assert run["parameters"]["flight_rate"] == "0.04"


def test_a_rate_override_changes_the_result(
    runs_client: TestClient, run_store: Path
) -> None:
    """A back-dated recalculation is one run, not a redeploy."""
    baseline = _result(run_store, _eshop(runs_client).json())
    shifted = _eshop(runs_client, promo_cutoff="2026-07-01").json()
    # With the cutoff moved before every order, nothing qualifies for 10%.
    assert Decimal(baseline["metrics"]["promo_base"]) == Decimal("4473.10")
    assert Decimal(_result(run_store, shifted)["metrics"]["promo_base"]) == 0
    assert shifted["parameters"]["promo_cutoff"] == "2026-07-01"

    untaxed = _travel(runs_client, vat_rate="0").json()
    assert Decimal(untaxed["parameters"]["vat_rate"]) == 0
    # With no uplift, an international booking costs the local rate.
    metrics = _result(run_store, untaxed)["metrics"]
    assert Decimal(metrics["vat_uplift_shortfall"]) == 0


def test_a_posted_period_is_ignored(runs_client: TestClient) -> None:
    derived = _eshop(runs_client).json()
    posted = _eshop(
        runs_client, period_start="2026-01-01", period_end="2026-12-31"
    ).json()
    # The period always comes from the files.
    assert posted["period_start"] == derived["period_start"] != "2026-01-01"
    assert posted["period_end"] == derived["period_end"]
    assert posted["exception_counts"] == derived["exception_counts"]


def test_history_is_newest_first_and_can_be_filtered(runs_client: TestClient) -> None:
    _travel(runs_client)
    _start(runs_client, "food", {"portal": FOOD_PORTAL, "vendor": FOOD_VENDOR})

    page = runs_client.get(RUNS).json()
    assert page["total"] == 2
    assert [r["number"] for r in page["items"]] == [2, 1]

    food = runs_client.get(RUNS, params={"category": "food"}).json()
    assert [r["number"] for r in food["items"]] == [2]

    by_file = runs_client.get(RUNS, params={"q": "claim_13"}).json()
    assert [r["number"] for r in by_file["items"]] == [1]

    by_number = runs_client.get(RUNS, params={"q": "0002"}).json()
    assert [r["number"] for r in by_number["items"]] == [2]

    paged = runs_client.get(RUNS, params={"page": 2, "page_size": 1}).json()
    assert [r["number"] for r in paged["items"]] == [1]
    assert paged["total"] == 2


def test_a_file_that_does_not_fit_is_a_failed_run_kept_with_its_reason(
    runs_client: TestClient,
) -> None:
    # A food statement where the travel claim belongs.
    response = _start(
        runs_client, "travel", {"portal": TRAVEL_PORTAL, "vendor": FOOD_VENDOR}
    )
    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "failed"
    assert run["error"]
    assert run["report_filename"] is None

    assert runs_client.get(RUNS, params={"status": "failed"}).json()["total"] == 1
    assert runs_client.get(f"{RUNS}/{run['number']}/report").status_code == 404
    # Its files are kept, so the failure can be looked into.
    assert runs_client.get(f"{RUNS}/{run['number']}/files/vendor").status_code == 200


@pytest.mark.parametrize(
    ("category", "paths", "message"),
    [
        ("travel", {"portal": TRAVEL_PORTAL}, "Missing required file(s): 'vendor'"),
        ("cars", {"portal": TRAVEL_PORTAL}, "Unknown category 'cars'"),
        (
            "food",
            {"portal": FOOD_PORTAL, "vendor": FOOD_VENDOR, "promo": FOOD_VENDOR},
            "food takes no file named 'promo'",
        ),
    ],
)
def test_a_request_that_cannot_start_a_run_leaves_no_trace(
    runs_client: TestClient, category: str, paths: dict[str, Path], message: str
) -> None:
    response = _start(runs_client, category, paths)
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert runs_client.get(RUNS).json()["total"] == 0


ESHOP = {"portal": WORKINGS, "promo": WORKINGS, "vendor": VENDOR_STATEMENT}
FOOD = {"portal": FOOD_PORTAL, "vendor": FOOD_VENDOR}
TRAVEL = {"portal": TRAVEL_PORTAL, "vendor": TRAVEL_CLAIMS["13"]}
# Stands for a file that is not a workbook at all, though it is named like one.
DAMAGED = Path("damaged.xlsx")


@pytest.mark.parametrize(
    ("category", "files", "said"),
    [
        (
            "eshop",
            ESHOP | {"portal": VENDOR_STATEMENT},
            ["portal", VENDOR_STATEMENT.name],
        ),
        (
            "eshop",
            ESHOP | {"portal": DAMAGED},
            ["'portal' is not a readable Excel workbook"],
        ),
        ("eshop", ESHOP | {"finance": VENDOR_STATEMENT}, ["finance"]),
        ("food", FOOD | {"portal": TRAVEL_PORTAL}, ["no food orders"]),
        ("food", FOOD | {"vendor": VENDOR_STATEMENT}, ["food vendor report"]),
        (
            "food",
            FOOD | {"finance": DAMAGED},
            ["'finance' is not a readable Excel workbook"],
        ),
        ("food", FOOD | {"finance": FOOD_VENDOR}, ["finance"]),
        ("travel", TRAVEL | {"portal": WORKINGS}, ["travel portal export"]),
        ("travel", TRAVEL | {"vendor": VENDOR_STATEMENT}, ["vendor claim report"]),
        (
            "travel",
            TRAVEL | {"wallet": DAMAGED},
            ["'wallet' is not a readable Excel workbook"],
        ),
        ("travel", TRAVEL | {"finance": TRAVEL_CLAIMS["13"]}, ["finance"]),
    ],
)
def test_the_reason_names_the_file_that_does_not_fit(
    runs_client: TestClient,
    tmp_path: Path,
    category: str,
    files: dict[str, Path],
    said: list[str],
) -> None:
    damaged = tmp_path / DAMAGED.name
    damaged.write_bytes(b"not a workbook")
    paths = {role: damaged if path == DAMAGED else path for role, path in files.items()}
    run = _start(runs_client, category, paths).json()
    assert run["status"] == "failed"
    for words in said:
        assert words in run["error"]


def test_an_oversized_upload_is_refused_and_leaves_no_trace(
    runs_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 1024)
    assert _travel(runs_client).status_code == 413
    assert runs_client.get(RUNS).json()["total"] == 0


def test_temporary_files_do_not_accumulate(runs_client: TestClient) -> None:
    """The spooled uploads go once the run has its own copies, whatever happened."""
    tmp = Path(tempfile.gettempdir())
    before = set(tmp.glob("recon-*"))
    _travel(runs_client)
    _start(runs_client, "travel", {"portal": TRAVEL_PORTAL, "vendor": FOOD_VENDOR})
    _start(runs_client, "travel", {"portal": TRAVEL_PORTAL})
    assert set(tmp.glob("recon-*")) == before


def test_a_non_excel_upload_is_refused(runs_client: TestClient) -> None:
    response = runs_client.post(
        RUNS,
        data={"category": "food"},
        files={
            "portal": ("orders.csv", b"a,b\n", "text/csv"),
            "vendor": (FOOD_VENDOR.name, FOOD_VENDOR.read_bytes(), XLSX),
        },
    )
    assert response.status_code == 415
    assert runs_client.get(RUNS).json()["total"] == 0


def test_an_unknown_run_is_a_404(runs_client: TestClient) -> None:
    assert runs_client.get(f"{RUNS}/999").status_code == 404
    assert runs_client.get(f"{RUNS}/999/report").status_code == 404


def test_runs_need_a_session(client: TestClient) -> None:
    assert client.get(RUNS).status_code == 401
    assert client.get(f"{API}/categories").status_code == 401


def test_a_run_obeys_and_records_the_current_version(
    runs_client: TestClient, db_session: Session
) -> None:
    """Saving a new version changes the next run, and the run says which it used."""
    before = _travel(runs_client).json()["exception_counts"]["by_severity"]
    category = db_session.scalar(select(Category).where(Category.key == "travel"))
    assert category is not None and category.current_version is not None
    config = config_of(category.current_version)
    code = "BOOKING_BASE_MISMATCH"
    softened = config.model_copy(
        update={
            "exceptions": [
                ExceptionDef(code=e.code, severity="info") if e.code == code else e
                for e in config.exceptions
            ]
        }
    )
    version = CategoryVersion(
        category_id=category.id,
        number=2,
        config=softened.model_dump(mode="json"),
        note="Base differences are informational",
    )
    db_session.add(version)
    db_session.flush()
    category.current_version_id = version.id
    db_session.commit()

    run = _travel(runs_client).json()
    assert run["version"] == 2
    after = run["exception_counts"]["by_severity"]
    assert after["high"] == before["high"] - 1
    assert after["info"] == before["info"] + 1
    listed = runs_client.get(f"{API}/categories").json()
    assert next(c for c in listed if c["key"] == "travel")["version"] == 2


def test_a_copy_of_any_upload_reaches_the_report(
    runs_client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    """Every uploaded file reaches the report, not only the portal, vendor and
    finance ones: a copy of eShop's promo upload is written."""
    category = db_session.scalar(select(Category).where(Category.key == "eshop"))
    assert category is not None and category.current_version is not None
    config = config_of(category.current_version)
    promo = config.input("promo")
    assert promo is not None
    copy = copy_tab("promo_copy", "Promo", "promo", promo.sheets[0].key)
    output = config.output.model_copy(update={"tabs": [*config.output.tabs, copy]})
    changed = config.model_copy(update={"output": output})
    assert problems(changed) == []
    version = CategoryVersion(
        category_id=category.id,
        number=2,
        config=changed.model_dump(mode="json"),
        note="A copy of the promo upload",
    )
    db_session.add(version)
    db_session.flush()
    category.current_version_id = version.id
    db_session.commit()

    run = _eshop(runs_client).json()
    assert run["status"] == "completed", run["error"]
    report = runs_client.get(f"{RUNS}/{run['number']}/report")
    downloaded = tmp_path / "report.xlsx"
    downloaded.write_bytes(report.content)
    book = load_workbook(downloaded)
    assert "Promo" in book.sheetnames
    written = {
        normalize_header(cell.value)
        for row in book["Promo"].iter_rows(max_row=5)
        for cell in row
        if cell.value is not None
    }
    assert {normalize_header(h) for h in promo.sheets[0].fingerprint} <= written
