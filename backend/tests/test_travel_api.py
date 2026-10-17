"""The travel endpoint: uploads, formats and the errors it must give back."""

import zipfile
from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient

from app.core.config import settings
from tests.conftest import VENDOR_STATEMENT, WORKINGS

TRAVEL = f"{settings.API_V1_STR}/reconcile/travel"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

type Files = dict[str, tuple[str, bytes, str]]


def test_end_to_end_json(auth_client: TestClient, travel_upload_files: Files) -> None:
    response = auth_client.post(TRAVEL, files=travel_upload_files)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["category"] == "travel"
    assert body["vendor_name"] == "Almosafer"
    assert body["summary"]["portal_rows"] == 206
    assert Decimal(body["summary"]["vendor_net_payable"]) == Decimal("6729.96")
    # The manifest makes a misdetection visible instead of silent.
    detected = {m["role"]: m["sheets"] for m in body["detected"]}
    assert detected["portal"] == ["Hotel", "Flight"]
    assert detected["vendor"] == ["Vedor Report"]
    # The claim's own per-period block is noted under its own role, so it
    # cannot read as the claim having been detected twice.
    assert detected["vendor-ledger"] == ["Sheet2"]
    assert detected["wallet"] == ["Sheet1"]


def test_end_to_end_xlsx(auth_client: TestClient, travel_upload_files: Files) -> None:
    response = auth_client.post(
        TRAVEL, files=travel_upload_files, params={"format": "xlsx"}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == XLSX
    assert (
        "reconciliation-travel-2026-07-26-to-2026-08-22.xlsx"
        in (response.headers["content-disposition"])
    )
    with zipfile.ZipFile(BytesIO(response.content)) as book:
        assert "xl/workbook.xml" in book.namelist()


def test_the_wallet_file_is_optional(
    auth_client: TestClient, travel_upload_files: Files
) -> None:
    del travel_upload_files["wallet"]
    response = auth_client.post(TRAVEL, files=travel_upload_files)
    assert response.status_code == 200, response.text
    assert response.json()["wallet"] is None


def test_rates_can_be_overridden_per_run(
    auth_client: TestClient, travel_upload_files: Files
) -> None:
    response = auth_client.post(
        TRAVEL, files=travel_upload_files, data={"vat_rate": "0"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["rates"]["vat_rate"]) == 0
    # With no uplift, an international booking costs the local rate.
    assert Decimal(body["summary"]["vat_uplift_shortfall"]) == 0


def test_a_missing_file_is_a_validation_error(auth_client: TestClient) -> None:
    assert auth_client.post(TRAVEL).status_code == 422


def test_another_categorys_export_is_rejected_by_name(
    auth_client: TestClient, travel_upload_files: Files
) -> None:
    """The eshop workbook has no travel sheet; saying so beats reconciling it."""
    travel_upload_files["portal"] = ("workings.xlsx", WORKINGS.read_bytes(), XLSX)
    response = auth_client.post(TRAVEL, files=travel_upload_files)
    assert response.status_code == 422
    assert "travel portal export" in response.json()["detail"]


def test_a_workbook_with_no_claim_sheet_is_rejected_by_name(
    auth_client: TestClient, travel_upload_files: Files
) -> None:
    travel_upload_files["vendor"] = (
        "vendor_statement.xlsx",
        VENDOR_STATEMENT.read_bytes(),
        XLSX,
    )
    response = auth_client.post(TRAVEL, files=travel_upload_files)
    assert response.status_code == 422
    assert "vendor claim report" in response.json()["detail"]


def test_a_non_excel_upload_is_refused(
    auth_client: TestClient, travel_upload_files: Files
) -> None:
    travel_upload_files["portal"] = ("portal.csv", b"not a workbook", "text/csv")
    response = auth_client.post(TRAVEL, files=travel_upload_files)
    assert response.status_code == 415


def test_the_endpoint_needs_a_session(
    client: TestClient, travel_upload_files: Files
) -> None:
    assert client.post(TRAVEL, files=travel_upload_files).status_code == 401
