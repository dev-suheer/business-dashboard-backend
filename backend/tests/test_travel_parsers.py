"""Travel readers and the keys they derive."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from app.recon.categories.travel import parser as travel
from app.recon.categories.travel.models import (
    BillingType,
    BookingStatus,
    PaymentStatus,
    TravelAdjustment,
    TravelInvoiceLine,
    TravelSkippedRow,
    Vertical,
)
from app.recon.cleaning import to_decimal
from app.recon.excel import detect_sheet, detect_sheets, open_workbook
from tests.conftest import TRAVEL_CLAIMS, TRAVEL_PORTAL, TRAVEL_WALLET

MAX_ROWS = 100_000


def read_claim(name: str) -> list[travel.VendorRow]:
    path = TRAVEL_CLAIMS[name]
    with open_workbook(path) as workbook:
        sheets = detect_sheets(workbook, travel.VENDOR_SCHEMA)
        return list(
            travel.read_vendor(workbook, sheets, source=path.name, max_rows=MAX_ROWS)
        )


def test_a_vendor_booking_id_splits_into_its_group_key() -> None:
    assert travel.vendor_booking_key("A260726150195500_7889547") == (
        "A260726150195500",
        "A260726150195500_7889547",
        False,
    )


def test_a_refund_groups_under_the_booking_it_reverses() -> None:
    """Netting a credit against its invoice depends on this."""
    assert travel.vendor_booking_key("A26070648655500_257964_R0") == (
        "A26070648655500",
        "A26070648655500_257964_R0",
        True,
    )


def test_a_portal_booking_id_sheds_its_leg_suffix() -> None:
    assert travel.portal_booking_key("A260813120395500-1") == (
        "A260813120395500",
        1,
    )
    assert travel.portal_booking_key("A260813120395500") == (
        "A260813120395500",
        None,
    )
    assert travel.portal_booking_key("-") is None


def test_a_booking_id_carries_its_own_date() -> None:
    assert travel.booking_date("A260726150195500") == date(2026, 7, 26)
    assert travel.booking_date("H260819107345500") == date(2026, 8, 19)
    assert travel.booking_date("nonsense") is None


def test_the_derived_date_agrees_with_every_invoice_in_every_claim() -> None:
    """The id is the authority, and this is why it can be trusted."""
    checked = 0
    for name in TRAVEL_CLAIMS:
        for row in read_claim(name):
            if not isinstance(row, TravelInvoiceLine):
                continue
            if row.billing_type is BillingType.CREDIT_FOR_RETURNS:
                continue
            assert travel.booking_date(row.booking_id) == row.vendor_booking_date
            checked += 1
    assert checked == 59


def test_a_credit_notes_own_booking_date_is_the_credits_date() -> None:
    """Which is exactly why the id, not the column, places a refund in time."""
    credits = [
        row
        for row in read_claim("10")
        if isinstance(row, TravelInvoiceLine)
        and row.billing_type is BillingType.CREDIT_FOR_RETURNS
    ]
    assert credits
    for row in credits:
        assert row.vendor_booking_date != travel.booking_date(row.booking_id)


def test_the_vertical_is_read_from_both_spellings() -> None:
    assert travel.vertical_from_id("H260819107345500") is Vertical.HOTEL
    assert travel.vertical_from_id("A260726150195500") is Vertical.FLIGHT
    assert travel.vertical_from_text("Flights") is Vertical.FLIGHT
    assert travel.vertical_from_text("Hotel") is Vertical.HOTEL
    # The claim writes the same thing as a single letter.
    assert travel.vertical_from_text("A") is Vertical.FLIGHT
    assert travel.vertical_from_text("H") is Vertical.HOTEL
    assert travel.vertical_from_text("Food") is None


def test_the_service_type_never_disagrees_with_the_booking_id() -> None:
    for name in TRAVEL_CLAIMS:
        for row in read_claim(name):
            if not isinstance(row, TravelInvoiceLine):
                continue
            assert travel.vertical_from_id(row.booking_id) == travel.vertical_from_text(
                row.service_type
            )


def test_a_stated_rate_is_read_as_a_rate() -> None:
    """The portal writes the flight rate as the text "3.00 %"."""
    assert to_decimal("3.00 %") == Decimal("0.03")


def test_a_claims_vat_percent_is_a_percentage_not_a_rate() -> None:
    """The cell holds the number 15, so it must not become 0.15."""
    lines = [r for r in read_claim("13") if isinstance(r, TravelInvoiceLine)]
    percents = {line.vat_percent for line in lines}
    assert percents == {Decimal(0), Decimal(15)}


def test_check_in_dates_stay_text() -> None:
    """They are MM/DD/YYYY, which the shared date parser deliberately rejects —
    teaching it that format would make DD/MM/YYYY ambiguous for every category."""
    lines = [r for r in read_claim("13") if isinstance(r, TravelInvoiceLine)]
    assert "08/23/2026" in {line.check_in_text for line in lines}


def test_a_totals_row_is_dropped_and_an_adjustment_is_kept() -> None:
    rows = read_claim("12")
    adjustments = [r for r in rows if isinstance(r, TravelAdjustment)]
    assert len(adjustments) == 1
    assert adjustments[0].booking_id == "A260629226265500"
    assert adjustments[0].commission == Decimal("-66.82")
    # The trailing totals row is a skip, not an exception-worthy one.
    skipped = [r for r in rows if isinstance(r, TravelSkippedRow)]
    assert {r.reason for r in skipped} == {"totals or spacer row"}


def test_a_claim_headed_booking_number_is_read_and_an_unbooked_row_kept(
    tmp_path: Path,
) -> None:
    headers = (
        "Booking Number",
        "Invoice Number",
        "line Item",
        "Billing Type",
        "Service Type",
        "Total Price W.O VAT",
        "VAT %",
        "Airline Can Fee",
        "Total Invoice Amount",
        "commision amount",
        "net payable amount",
    )
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(headers)
    sheet.append(
        (
            "A260819100005500_1",
            "2500000001",
            "000001",
            "Invoice",
            "A",
            1000,
            0,
            0,
            1000,
            34.5,
            965.5,
        )
    )
    sheet.append(
        (None, "2500000002", "000001", "Invoice", "A", 200, 0, 0, 200, 6.9, 193.1)
    )
    path = tmp_path / "claim.xlsx"
    book.save(path)

    with open_workbook(path) as workbook:
        sheets = detect_sheets(workbook, travel.VENDOR_SCHEMA)
        rows = list(
            travel.read_vendor(workbook, sheets, source="claim.xlsx", max_rows=10)
        )
    line, skipped = rows
    assert isinstance(line, TravelInvoiceLine)
    assert line.booking_id == "A260819100005500"
    # No booking id: excluded from the totals, but every figure kept to trace.
    assert isinstance(skipped, TravelSkippedRow)
    assert skipped.reason == "invoiced row carries no booking id"
    assert skipped.invoice_no == "2500000002"
    assert skipped.total_invoice == Decimal(200)
    assert skipped.commission == Decimal("6.9")
    assert skipped.net_payable == Decimal("193.1")


def test_the_claims_own_ledger_is_read() -> None:
    path = TRAVEL_CLAIMS["13"]
    with open_workbook(path) as workbook:
        sheet = detect_sheet(workbook, travel.CLAIM_SCHEMA)
        assert sheet is not None
        periods = list(
            travel.read_claim_periods(
                workbook, sheet, source=path.name, max_rows=MAX_ROWS
            )
        )
    assert [p.claim_period for p in periods] == [
        "Claim From 21 till 27 Jun 2026",
        "claim From 09 till 15 AUG 2026",
        "claim From 16 till 22 AUG 2026",
    ]
    assert periods[-1].total_claim == Decimal("6985.160000000001")


def test_the_portal_reader_keeps_the_first_of_three_customer_columns() -> None:
    with open_workbook(TRAVEL_PORTAL) as workbook:
        rows = list(
            travel.read_portal(
                workbook, "Hotel", source="portal.xlsx", max_rows=MAX_ROWS
            )
        )
    assert len(rows) == 75
    first = rows[0]
    assert first.customer_ref == "45841"
    assert first.sg_order_id == "124"
    assert first.vertical is Vertical.HOTEL
    assert first.status is BookingStatus.EXPIRED
    assert first.payment_status is PaymentStatus.PENDING
    assert first.booking_id is None


def test_the_portal_reader_reads_a_booked_row_whole() -> None:
    with open_workbook(TRAVEL_PORTAL) as workbook:
        rows = list(
            travel.read_portal(
                workbook, "Hotel", source="portal.xlsx", max_rows=MAX_ROWS
            )
        )
    booked = next(r for r in rows if r.booking_id == "H260819107345500")
    assert booked.order_date == date(2026, 8, 19)
    assert booked.order_total == Decimal("1161.7")
    assert booked.commission_charged == Decimal("58.09")
    assert booked.net_payable == Decimal("1103.61")
    assert booked.payment_method == "Apple Pay"
    # Hotels leave Commission Amount at zero and fill Commission Charged.
    assert booked.commission_amount == Decimal(0)


def test_the_wallet_reader_keeps_only_travel_movements() -> None:
    with open_workbook(TRAVEL_WALLET) as workbook:
        sheet = detect_sheet(workbook, travel.WALLET_SCHEMA)
        assert sheet is not None
        events = list(
            travel.read_wallet(workbook, sheet, source="wallet.xlsx", max_rows=MAX_ROWS)
        )
    assert {e.vertical for e in events} == {Vertical.HOTEL, Vertical.FLIGHT}
    assert len(events) == 46


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Invoice", BillingType.INVOICE),
        ("Credit for Returns", BillingType.CREDIT_FOR_RETURNS),
        ("credit note", BillingType.CREDIT_FOR_RETURNS),
        ("-", BillingType.UNKNOWN),
        (None, BillingType.UNKNOWN),
    ],
)
def test_billing_types(text: str | None, expected: BillingType) -> None:
    assert travel.parse_billing_type(text) is expected
