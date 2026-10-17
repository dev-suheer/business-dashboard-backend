"""The Orders sheet matches the client recon layout and its summary block."""

from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from app.recon.categories.food.export import build_report
from app.recon.categories.food.pipeline import FoodRequest, run
from app.recon.inputs import Upload
from tests.conftest import FOOD_PORTAL, FOOD_VENDOR


def _cents(value: object) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def test_the_orders_sheet_has_the_client_totals_and_summary(tmp_path: Path) -> None:
    result = run(
        FoodRequest(
            portal=Upload("portal", FOOD_PORTAL.name, FOOD_PORTAL),
            vendor=Upload("vendor", FOOD_VENDOR.name, FOOD_VENDOR),
            max_rows=10_000,
        )
    )
    path = tmp_path / "food.xlsx"
    build_report(result, path)

    book = load_workbook(path)
    sheet = book["Orders"]
    headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    assert "ORDER AMOUNT DIFFERENCE" in headers
    assert "Additional Del Charges SG BEAR" in headers
    assert "Additional Delivery CHarge CHEFZ BEAR" in headers
    assert "Customer Delivery Charges" in headers

    match_col = headers.index("MATCH ID'S") + 1
    assert sheet.cell(1, match_col).fill.fgColor.rgb == "FFC6EFCE"

    # 298 paired rows plus the 4 vendor-only orders the client totals include.
    last_data = 303
    totals_row = 304
    sg_col = headers.index("Additional Del Charges SG BEAR") + 1
    chefz_col = headers.index("Additional Delivery CHarge CHEFZ BEAR") + 1
    customer_col = headers.index("Customer Delivery Charges") + 1
    fee_col = headers.index("RATE% CHECK SG-VNDR SOA") + 1

    def column_sum(column: int) -> Decimal:
        return sum(
            (
                Decimal(str(sheet.cell(row, column).value or 0))
                for row in range(2, last_data + 1)
            ),
            Decimal(0),
        )

    assert _cents(column_sum(sg_col)) == Decimal("1611.10")
    assert _cents(column_sum(chefz_col)) == Decimal("1298.90")
    assert _cents(column_sum(customer_col)) == Decimal("2179.00")
    assert sheet.cell(totals_row, fee_col).number_format == "#,##0.00"
    assert sheet.cell(2, fee_col).number_format == "#,##0.00"

    vendor_row = None
    for row in range(totals_row, sheet.max_row + 1):
        if sheet.cell(row, 1).value == "Vendor FOOD ORDER Amount":
            vendor_row = row
            break
    assert vendor_row is not None
    assert sheet.cell(vendor_row - 1, 1).value == "SG FOOD ORDER Amount"
    header_values = [sheet.cell(vendor_row, col).value for col in range(1, 20)]
    assert "Food Amount" in header_values
    assert "Delivery" in header_values
    assert "ADD DC" in header_values
    assert "Total Order Amount" in header_values
    assert "T. Remaining" in header_values
    # Labels and values sit on one horizontal block, not a vertical list.
    assert header_values.index("Food Amount") > 1
    assert sheet.cell(vendor_row + 1, 4).value.startswith("=")

    texts = [
        cell.value
        for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row)
        for cell in row
        if isinstance(cell.value, str)
    ]
    assert all("\u2014" not in value for value in texts)

    summary = book["Summary"]
    summary_labels = [summary.cell(row, 1).value for row in range(1, 40)]
    assert all("\u2014" not in (label or "") for label in summary_labels)
    total_row = summary_labels.index("Total order amount") + 1
    headline_row = (
        summary_labels.index("Payable with the food difference added back") + 1
    )
    assert _cents(summary.cell(total_row, 2).value) == Decimal("21088.33")
    assert _cents(summary.cell(headline_row, 2).value) == Decimal("21088.58")
    delivery_row = summary_labels.index("Delivery") + 1
    add_row = summary_labels.index("ADD DC") + 1
    assert _cents(summary.cell(delivery_row, 2).value) == Decimal("2179.00")
    assert _cents(summary.cell(add_row, 2).value) == Decimal("1611.10")
    book.close()
