"""The configuration API: read a category's setup, check it, save it.

A save is a new version and the next run uses it. The acceptance case for the
editor is here: rename a header, add a report column with a formula, change a
status rule, save, and the next run reconciles and reports under the change.
"""

import gzip
import json
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.config_model.seed import load_seed
from app.core.config import settings
from tests.conftest import TRAVEL_CLAIMS, TRAVEL_PORTAL, TRAVEL_WALLET
from tests.parity.compare import workbook_differences
from tests.parity.scenarios import BASELINE

API = settings.API_V1_STR
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CLAIM = TRAVEL_CLAIMS["13"]


def _config(client: TestClient, key: str = "travel") -> dict[str, Any]:
    response = client.get(f"{API}/categories/{key}/config")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _save(
    client: TestClient, config: dict[str, Any], base: int, key: str = "travel"
) -> Any:
    return client.put(
        f"{API}/categories/{key}/config",
        json={"config": config, "note": "test", "base_version": base},
    )


def _run(client: TestClient, files: dict[str, Path], category: str = "travel") -> Any:
    response = client.post(
        f"{API}/runs",
        data={"category": category},
        files={role: (p.name, p.read_bytes(), XLSX) for role, p in files.items()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _sheets(client: TestClient, role: str, path: Path, key: str = "travel") -> Any:
    return client.post(
        f"{API}/categories/{key}/sheets/{role}",
        files={"file": (path.name, path.read_bytes(), XLSX)},
    )


# --- reading ---------------------------------------------------------------------


def test_the_setup_runs_use_is_version_one(runs_client: TestClient) -> None:
    body = _config(runs_client)
    assert body["version"] == 1
    assert body["config"] == json.loads(load_seed("travel").model_dump_json())
    categories = {c["key"]: c for c in runs_client.get(f"{API}/categories").json()}
    assert categories["travel"]["ready"] is True
    assert categories["travel"]["saved_at"]


def test_the_function_library_is_listed(runs_client: TestClient) -> None:
    functions = {f["name"]: f for f in runs_client.get(f"{API}/functions").json()}
    assert functions["SUMX"]["min_args"] == 2
    assert functions["IF"]["call"].startswith("IF(")


def test_the_standard_pieces_are_the_ones_every_category_takes(
    runs_client: TestClient,
) -> None:
    standard = runs_client.get(f"{API}/standard").json()
    travel = load_seed("travel").model_dump(mode="json")
    by_role = {upload["role"]: upload for upload in travel["inputs"]}
    assert standard["wallet_upload"] == by_role["wallet"]
    # Nothing ready-made of any one file: a file to look things up in, its copy
    # and its look-up columns are the editor's own, made by hand.
    assert set(standard) == {"wallet_upload", "tabs"}
    assert set(standard["tabs"]) == {"exceptions", "erp_invoices", "wallet", "copy"}


# --- checking ----------------------------------------------------------------------


def test_a_check_names_each_problem_and_what_each_formula_reads(
    runs_client: TestClient,
) -> None:
    config = _config(runs_client)["config"]
    assert (
        runs_client.post(
            f"{API}/categories/travel/check", json={"config": config}
        ).json()["problems"]
        == []
    )

    config["model"]["rules"][1]["when"] = 'portal_status = "Cancelled" AND'
    config["output"]["tabs"][0]["blocks"][0]["values"][0]["metric"] = "no_such_figure"
    body = runs_client.post(
        f"{API}/categories/travel/check", json={"config": config}
    ).json()
    problems = {p["path"]: p["message"] for p in body["problems"]}
    assert "model.rules[1].when" in problems
    assert "no_such_figure" in problems["output.tabs[0].blocks[0].values[0].metric"]
    uses = {u["path"]: u["uses"] for u in body["lineage"]}
    # The report's base column reads the portal row's Order Total.
    base = next(
        i for i, f in enumerate(config["model"]["fields"]) if f["key"] == "p_base"
    )
    assert uses[f"model.fields[{base}].formula"] == ["portal.order_total"]


# --- saving --------------------------------------------------------------------------


def test_a_save_is_a_new_version_and_the_next_run_uses_it(
    runs_client: TestClient, tmp_path: Path
) -> None:
    config = _config(runs_client)["config"]
    saved = _save(runs_client, config, base=1)
    assert saved.status_code == 200, saved.text
    assert saved.json()["number"] == 2
    assert _config(runs_client)["version"] == 2

    # Unchanged rules: the run is the approved one, under version 2.
    run = _run(
        runs_client, {"portal": TRAVEL_PORTAL, "vendor": CLAIM, "wallet": TRAVEL_WALLET}
    )
    assert run["status"] == "completed", run["error"]
    assert run["version"] == 2
    report = tmp_path / "report.xlsx"
    report.write_bytes(runs_client.get(f"{API}/runs/{run['number']}/report").content)
    assert (
        workbook_differences(report, BASELINE / "travel-claim-13" / "report.xlsx") == []
    )

    versions = runs_client.get(f"{API}/categories/travel/versions").json()
    assert [(v["number"], v["current"]) for v in versions] == [(2, True), (1, False)]
    first = runs_client.get(f"{API}/categories/travel/versions/1").json()
    assert first["config"] == json.loads(load_seed("travel").model_dump_json())


def test_a_save_over_a_newer_version_is_refused(runs_client: TestClient) -> None:
    config = _config(runs_client)["config"]
    assert _save(runs_client, config, base=1).status_code == 200
    stale = _save(runs_client, config, base=1)
    assert stale.status_code == 409
    assert "Version 2" in stale.json()["detail"]


def test_a_setup_that_does_not_check_out_is_not_saved(runs_client: TestClient) -> None:
    config = _config(runs_client)["config"]
    config["model"]["findings"][0]["code"] = "NOT_DECLARED"
    refused = _save(runs_client, config, base=1)
    assert refused.status_code == 422
    paths = [p["path"] for p in refused.json()["detail"]["problems"]]
    assert paths == ["model.findings[0].code"]
    assert _config(runs_client)["version"] == 1


def test_rename_a_header_add_a_column_change_a_rule_and_the_next_run_follows(
    runs_client: TestClient, tmp_path: Path
) -> None:
    """The editor's acceptance case, end to end through the API."""
    # The vendor renames a portal column.
    renamed = tmp_path / "portal.xlsx"
    book = load_workbook(TRAVEL_PORTAL)
    for sheet in book.worksheets:
        for row in sheet.iter_rows(max_row=30):
            for cell in row:
                if cell.value == "Order Status":
                    cell.value = "Booking Status"
    book.save(renamed)
    failed = _run(runs_client, {"portal": renamed, "vendor": CLAIM})
    assert failed["status"] == "failed"

    config = _config(runs_client)["config"]
    # 1. The header is taught its new name.
    portal = config["inputs"][0]["sheets"][0]
    status_column = next(c for c in portal["columns"] if c["field"] == "status")
    status_column["headers"].append("Booking Status")
    # 2. A report column with a formula: what the vendor bills over our base.
    orders = next(t for t in config["output"]["tabs"] if t["key"] == "orders")
    orders["columns"].insert(
        orders["columns"].index(
            next(c for c in orders["columns"] if c["key"] == "verdict")
        ),
        {
            "key": "billed_share",
            "header": "Billed / booked",
            "width": 14,
            "group": "check",
            "kind": "rate",
            "value": "IF(both, vendor_base / portal_base, BLANK())",
        },
    )
    # 3. A status rule changed: a cancelled booking the claim never billed is
    # what a cancellation should look like, so it is not raised as a break.
    finding = next(
        f
        for f in config["model"]["findings"]
        if f["code"] == "CANCELLATION_WITHOUT_VENDOR_CREDIT"
    )
    finding["when"] = finding["when"].replace(
        'refund = "none"', 'refund = "none", match_state = "matched"'
    )
    assert _save(runs_client, config, base=1).status_code == 200

    run = _run(runs_client, {"portal": renamed, "vendor": CLAIM})
    assert run["status"] == "completed", run["error"]
    assert run["version"] == 2
    report = tmp_path / "report.xlsx"
    report.write_bytes(runs_client.get(f"{API}/runs/{run['number']}/report").content)
    sheet = load_workbook(report)["Orders"]
    headers = [c.value for c in next(sheet.iter_rows(min_row=1, max_row=1))]
    column = headers.index("Billed / booked") + 1
    assert headers[column] == "Verdict"
    shares = [sheet.cell(r, column).value for r in range(2, 9)]
    assert all(isinstance(v, int | float) and 0.99 < v < 1.01 for v in shares), shares

    with gzip.open(
        Path(settings.RUN_STORAGE_DIR)
        / "runs"
        / f"{run['number']:06d}"
        / "result.json.gz"
    ) as kept:
        result = json.load(kept)
    codes = [e["code"] for e in result["exceptions"]]
    assert "CANCELLATION_WITHOUT_VENDOR_CREDIT" not in codes


# --- new categories ------------------------------------------------------------------


def test_a_new_category_is_blank_or_a_copy(runs_client: TestClient) -> None:
    blank = runs_client.post(
        f"{API}/categories", json={"key": "hotels", "name": "Hotels"}
    )
    assert blank.status_code == 201, blank.text
    assert blank.json()["ready"] is False
    assert blank.json()["inputs"] == []

    copy = runs_client.post(
        f"{API}/categories",
        json={"key": "travel_b", "name": "Travel B", "copy_of": "travel"},
    )
    assert copy.status_code == 201, copy.text
    assert copy.json()["ready"] is True
    copied = _config(runs_client, "travel_b")
    assert copied["config"]["category"] == "travel_b"
    assert copied["note"] == "Copy of Travel"
    assert copied["config"]["model"] == _config(runs_client)["config"]["model"]

    again = runs_client.post(f"{API}/categories", json={"key": "hotels", "name": "X"})
    assert again.status_code == 422
    bad = runs_client.post(f"{API}/categories", json={"key": "Bad Key", "name": "X"})
    assert bad.status_code == 422


# --- archiving and deleting ----------------------------------------------------------


def _keys(client: TestClient, archived: bool = False) -> dict[str, Any]:
    response = client.get(f"{API}/categories", params={"archived": archived})
    assert response.status_code == 200, response.text
    return {c["key"]: c for c in response.json()}


def test_an_archived_category_is_kept_but_takes_no_runs(
    runs_client: TestClient,
) -> None:
    made = runs_client.post(
        f"{API}/categories",
        json={"key": "travel_qa", "name": "Travel QA", "copy_of": "travel"},
    )
    assert made.status_code == 201, made.text
    run = _run(runs_client, {"portal": TRAVEL_PORTAL, "vendor": CLAIM}, "travel_qa")

    archived = runs_client.post(f"{API}/categories/travel_qa/archive")
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived"] is True
    assert "travel_qa" not in _keys(runs_client)
    listed = _keys(runs_client, archived=True)["travel_qa"]
    assert listed["archived"] is True
    assert listed["runs"] == 1

    # No run starts on it, and it cannot be copied; its run stays in History.
    refused = runs_client.post(
        f"{API}/runs",
        data={"category": "travel_qa"},
        files={"portal": (TRAVEL_PORTAL.name, TRAVEL_PORTAL.read_bytes(), XLSX)},
    )
    assert refused.status_code == 422
    assert "Travel QA is archived" in refused.text
    copy = runs_client.post(
        f"{API}/categories",
        json={"key": "travel_qb", "name": "B", "copy_of": "travel_qa"},
    )
    assert copy.status_code == 404
    assert runs_client.get(f"{API}/runs/{run['number']}").status_code == 200

    restored = runs_client.post(f"{API}/categories/travel_qa/restore")
    assert restored.json()["archived"] is False
    assert "travel_qa" in _keys(runs_client)


def test_a_deleted_category_takes_its_versions_and_runs_with_it(
    runs_client: TestClient, run_store: Path
) -> None:
    runs_client.post(
        f"{API}/categories",
        json={"key": "travel_qa", "name": "Travel QA", "copy_of": "travel"},
    )
    config = _config(runs_client, "travel_qa")
    assert _save(runs_client, config["config"], 1, "travel_qa").status_code == 200
    gone = _run(runs_client, {"portal": TRAVEL_PORTAL, "vendor": CLAIM}, "travel_qa")
    kept = _run(runs_client, {"portal": TRAVEL_PORTAL, "vendor": CLAIM})
    gone_folder = run_store / "runs" / f"{gone['number']:06d}"
    assert gone_folder.exists()

    # Only an archived category, and only with its key repeated.
    active = runs_client.delete(
        f"{API}/categories/travel_qa", params={"confirm": "travel_qa"}
    )
    assert active.status_code == 422
    runs_client.post(f"{API}/categories/travel_qa/archive")
    assert runs_client.delete(f"{API}/categories/travel_qa").status_code == 422
    wrong = runs_client.delete(
        f"{API}/categories/travel_qa", params={"confirm": "travel"}
    )
    assert wrong.status_code == 422
    unknown = runs_client.delete(f"{API}/categories/nope", params={"confirm": "nope"})
    assert unknown.status_code == 404

    deleted = runs_client.delete(
        f"{API}/categories/travel_qa", params={"confirm": "travel_qa"}
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"versions": 2, "runs": 1}
    assert "travel_qa" not in _keys(runs_client, archived=True)
    assert runs_client.get(f"{API}/runs/{gone['number']}").status_code == 404
    assert not gone_folder.exists()
    # Another category's runs are untouched, and the key is free again.
    assert runs_client.get(f"{API}/runs/{kept['number']}").status_code == 200
    again = runs_client.post(
        f"{API}/categories", json={"key": "travel_qa", "name": "Travel QA"}
    )
    assert again.status_code == 201, again.text


# --- a file read for its sheets ----------------------------------------------------


def test_a_file_is_read_for_its_sheets_and_headers_and_not_kept(
    runs_client: TestClient, run_store: Path
) -> None:
    response = _sheets(runs_client, "portal", TRAVEL_PORTAL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filename"] == TRAVEL_PORTAL.name
    sheets = {s["name"]: s for s in body["sheets"]}
    hotel = sheets["Hotel"]
    assert hotel["kind"] == "portal"
    by_header = {c["header"]: c for c in hotel["columns"]}
    assert by_header["Order Status"]["field"] == "status"
    assert by_header["Order Total"]["suggested_field"] == "order_total"
    assert "rows" not in hotel
    # An upload not set up yet: the headers, nothing matched to them.
    fresh = _sheets(runs_client, "claims", CLAIM).json()
    assert fresh["sheets"] and all(
        c["field"] is None for s in fresh["sheets"] for c in s["columns"]
    )
    # Nothing was kept.
    assert not (run_store / "templates").exists()
    assert _config(runs_client)["version"] == 1


def test_a_formula_naming_what_is_not_there_is_not_saved(
    runs_client: TestClient,
) -> None:
    config = _config(runs_client)["config"]
    config["model"]["fields"][0]["formula"] = "no_such_name + 1"
    response = _save(runs_client, config, base=1)
    assert response.status_code == 422
    assert response.json()["detail"]["problems"] == [
        {
            "path": "model.fields[0].formula",
            "message": "Nothing is called 'no_such_name' yet. Check the spelling, "
            "or add a column or field with that name.",
        }
    ]
    assert _config(runs_client)["version"] == 1


def test_an_edited_setup_that_fails_on_the_files_is_a_failed_run(
    runs_client: TestClient,
) -> None:
    # Only the files can tell: a booking the vendor never billed has no base.
    config = _config(runs_client)["config"]
    config["model"]["fields"].append(
        {"key": "per_vendor_riyal", "formula": "1 / N(vendor_base)"}
    )
    assert _save(runs_client, config, base=1).status_code == 200
    run = _run(runs_client, {"portal": TRAVEL_PORTAL, "vendor": CLAIM})
    assert run["status"] == "failed"
    assert "Setup version 2" in run["error"] and "Division by zero" in run["error"]


def test_a_file_read_must_be_a_workbook(runs_client: TestClient) -> None:
    response = runs_client.post(
        f"{API}/categories/travel/sheets/portal",
        files={"file": ("portal.xlsx", BytesIO(b"not a workbook").getvalue(), XLSX)},
    )
    assert response.status_code == 422
    badly_named = _sheets(runs_client, "Bad Name", TRAVEL_PORTAL)
    assert badly_named.status_code == 422


def test_a_rename_comes_back_with_everything_that_used_it(
    runs_client: TestClient,
) -> None:
    config = _config(runs_client, "food")["config"]
    k = next(
        i for i, f in enumerate(config["model"]["fields"]) if f["key"] == "ids_match"
    )
    response = runs_client.post(
        f"{API}/categories/food/rename",
        json={"config": config, "path": f"model.fields[{k}]", "name": "ids_agree"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert f"model.fields[{k}].key" in body["changed"]
    assert "model.sets[4].where" in body["changed"]
    # Nothing is saved: the setup comes back, to be saved with the rest.
    assert _config(runs_client, "food")["version"] == 1
    check = runs_client.post(
        f"{API}/categories/food/check", json={"config": body["config"]}
    ).json()
    assert check["problems"] == []
    assert _save(runs_client, body["config"], base=1, key="food").status_code == 200


def test_a_rename_to_a_name_already_there_is_refused(runs_client: TestClient) -> None:
    config = _config(runs_client, "food")["config"]
    taken = config["model"]["fields"][1]["key"]
    response = runs_client.post(
        f"{API}/categories/food/rename",
        json={"config": config, "path": "model.fields[0]", "name": taken},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "message": f"There is already something called {taken!r} there",
        "path": "model.fields[0]",
    }
