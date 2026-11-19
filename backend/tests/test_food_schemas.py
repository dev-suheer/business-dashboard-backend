"""Sheet detection for food.

The portal header is the travel export's header. The worked recon sheet carries
both sides, so it must match neither schema.
"""

from pathlib import Path

from openpyxl import Workbook

from app.expr import Values
from app.recon.excel import detect_sheets, open_workbook
from app.recon.inputs import Upload
from app.runner.inputs import read_upload
from tests.conftest import (
    FOOD_PORTAL,
    FOOD_VENDOR,
    TRAVEL_CLAIMS,
    TRAVEL_PORTAL,
    VENDOR_STATEMENT,
    WORKINGS,
)
from tests.food import food_config, portal_schema, vendor_schema
from tests.travel import schema

PORTAL_SCHEMA = portal_schema()
VENDOR_SCHEMA = vendor_schema()


def test_the_sample_uploads_match_their_own_schema() -> None:
    with open_workbook(FOOD_PORTAL) as workbook:
        assert detect_sheets(workbook, PORTAL_SCHEMA) == ["PORTAL REPORT"]
        assert detect_sheets(workbook, VENDOR_SCHEMA) == []
    with open_workbook(FOOD_VENDOR) as workbook:
        assert detect_sheets(workbook, VENDOR_SCHEMA) == ["VENDOR REPORT"]
        assert detect_sheets(workbook, PORTAL_SCHEMA) == []


def test_the_vendor_header_is_found_under_the_title_row() -> None:
    """The sample vendor tab puts a title on row 1 and the headers on row 2."""
    spec = food_config().input("vendor")
    assert spec is not None
    rows = read_upload(
        spec,
        Upload("vendor", "vendor.xlsx", FOOD_VENDOR),
        max_rows=10_000,
        scope=Values({}),
        detected=[],
        exceptions=[],
    )["vendor"]
    assert len(rows) == 302
    assert rows[0]["order_id"] == "289926686"
    assert rows[0].sheet == "VENDOR REPORT"


def test_a_side_by_side_recon_sheet_is_not_an_input(tmp_path: Path) -> None:
    path = tmp_path / "analysis.xlsx"
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "ANALYSIS & RECON"
    headers = [
        "Partner Order ID",
        "Vertical",
        "Order Total",
        "SG Delivery Expense",
        "Commission Charged",
        "Net Payable Amount to Partner",
        "SG Order ID",
        "MATCH ID'S",
        "order_id",
        "shoppinggate_fees",
        "shoppinggate_percentage",
        "requestedDeliveryDate",
        "original_deliveryFee",
        "promotion_shoppinggate_charge_amount",
        "promotion_chef_charge_amount",
    ]
    for col, header in enumerate(headers, start=1):
        sheet.cell(1, col, header)
    book.save(path)
    book.close()

    with open_workbook(path) as workbook:
        assert detect_sheets(workbook, PORTAL_SCHEMA) == []
        assert detect_sheets(workbook, VENDOR_SCHEMA) == []


def test_travel_and_eshop_are_not_read_as_a_chefz_report() -> None:
    with open_workbook(TRAVEL_PORTAL) as workbook:
        assert detect_sheets(workbook, VENDOR_SCHEMA) == []
        # Same 38 portal columns. The reader drops them because Vertical is not Food.
        assert detect_sheets(workbook, PORTAL_SCHEMA) == ["Hotel", "Flight"]
    with open_workbook(TRAVEL_CLAIMS["13"]) as workbook:
        assert detect_sheets(workbook, VENDOR_SCHEMA) == []
    with open_workbook(WORKINGS) as workbook:
        # The eshop workings file also carries a Chefz export. The portal
        # schema must not claim it; the vendor schema should.
        assert detect_sheets(workbook, PORTAL_SCHEMA) == []
        assert detect_sheets(workbook, VENDOR_SCHEMA) == ["Food Vendor Report 7-12"]
    with open_workbook(VENDOR_STATEMENT) as workbook:
        assert detect_sheets(workbook, VENDOR_SCHEMA) == []
    with open_workbook(FOOD_PORTAL) as workbook:
        assert detect_sheets(workbook, schema("vendor", "claim")) == []
