"""Reading travel's uploads, and the keys version 1 of its configuration derives."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from app.expr import Values, evaluate
from app.recon.cleaning import normalize_header, to_decimal
from app.recon.excel import open_workbook
from app.recon.exceptions import ExceptionCode, ReconException
from app.recon.inputs import Upload
from app.recon.models import SheetManifest
from app.recon.wallet import read_wallet
from app.runner import Row
from app.runner.inputs import READERS, read_upload
from tests.conftest import TRAVEL_CLAIMS, TRAVEL_PORTAL, TRAVEL_WALLET
from tests.travel import claim_line, portal_row, read, travel_config

SERVICE_VERTICAL = (
    'IFS(IN(service_type, "hotel", "hotels", "h"), "hotel", '
    'IN(service_type, "flight", "flights", "a"), "flight")'
)


def claim_rows(name: str) -> list[Row]:
    return read("vendor", TRAVEL_CLAIMS[name])["claim"]


def lines(name: str) -> list[Row]:
    return [row for row in claim_rows(name) if row["kind"] == "line"]


def test_a_vendor_booking_id_splits_into_its_group_key() -> None:
    line = claim_line(booking_ref="a260726150195500_7889547")
    assert line["booking_id"] == "A260726150195500"
    assert line["booking_ref"] == "A260726150195500_7889547"
    assert line["reversal"] is False


def test_a_refund_groups_under_the_booking_it_reverses() -> None:
    """Netting a credit against its invoice depends on this."""
    line = claim_line(booking_ref="A26070648655500_257964_R0")
    assert line["booking_id"] == "A26070648655500"
    assert line["reversal"] is True
    assert line["credit"] is True


def test_a_portal_booking_id_sheds_its_leg_suffix() -> None:
    assert (
        portal_row(booking_id="A260813120395500-1")["booking_id"] == "A260813120395500"
    )
    assert portal_row(booking_id="a260813120395500")["booking_id"] == "A260813120395500"
    # Unbooked rows carry "-", which reads as no value.
    assert READERS["text"]("-") is None
    assert portal_row(booking_id=None)["booking_id"] is None


def test_a_booking_id_carries_its_own_date() -> None:
    assert claim_line(booking_ref="A260726150195500_1")["id_date"] == date(2026, 7, 26)
    assert claim_line(booking_ref="H260819107345500_1")["id_date"] == date(2026, 8, 19)
    assert claim_line(booking_ref="nonsense_1")["id_date"] is None


def test_the_derived_date_agrees_with_every_invoice_in_every_claim() -> None:
    """The id is the authority, and this is why it can be trusted."""
    checked = 0
    for name in TRAVEL_CLAIMS:
        for line in lines(name):
            if line["billing_type"] == "Credit for Returns":
                continue
            assert line["id_date"] == line["vendor_booking_date"]
            checked += 1
    assert checked == 59


def test_a_credit_notes_own_booking_date_is_the_credits_date() -> None:
    """Which is exactly why the id, not the column, places a refund in time."""
    credits = [r for r in lines("10") if r["billing_type"] == "Credit for Returns"]
    assert credits
    for row in credits:
        assert row["vendor_booking_date"] != row["id_date"]


def test_the_vertical_is_read_from_both_spellings() -> None:
    assert claim_line(booking_ref="H260819107345500_1")["line_vertical"] == "hotel"
    assert claim_line(booking_ref="A260726150195500_1")["line_vertical"] == "flight"
    assert portal_row(vertical="Flights")["vertical"] == "flight"
    assert portal_row(vertical="Hotel")["vertical"] == "hotel"
    # The claim writes the same thing as a single letter.
    assert portal_row(vertical="A")["vertical"] == "flight"
    assert portal_row(vertical="H")["vertical"] == "hotel"
    assert portal_row(vertical="Food")["vertical"] is None


def test_the_service_type_never_disagrees_with_the_booking_id() -> None:
    for name in TRAVEL_CLAIMS:
        for line in lines(name):
            assert line["line_vertical"] == evaluate(SERVICE_VERTICAL, Values(line))


def test_a_stated_rate_is_read_as_a_rate() -> None:
    """The portal writes the flight rate as the text "3.00 %"."""
    assert to_decimal("3.00 %") == Decimal("0.03")


def test_a_claims_vat_percent_is_a_percentage_not_a_rate() -> None:
    """The cell holds the number 15, so it must not become 0.15."""
    assert {line["vat_percent"] for line in lines("13")} == {Decimal(0), Decimal(15)}
    assert {line["vat_percent_text"] for line in lines("13")} == {"0", "15"}


def test_check_in_dates_stay_text() -> None:
    """They are MM/DD/YYYY, which the shared date parser deliberately rejects —
    teaching it that format would make DD/MM/YYYY ambiguous for every category."""
    assert "08/23/2026" in {line["check_in"] for line in lines("13")}


def test_a_totals_row_is_dropped_and_an_adjustment_is_kept() -> None:
    rows = claim_rows("12")
    adjustments = [r for r in rows if r["kind"] == "adjustment"]
    assert len(adjustments) == 1
    assert adjustments[0]["adjusts"] == "A260629226265500"
    assert adjustments[0]["commission"] == Decimal("-66.82")
    # The trailing totals row is a spacer, not an exception-worthy skip.
    assert {r["kind"] for r in rows} == {"line", "adjustment", "spacer"}


CLAIM_HEADERS = (
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
LINE = (
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


def _claim(path: Path, *rows: tuple[object, ...]) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(CLAIM_HEADERS)
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


def test_a_claim_headed_booking_number_is_read_and_an_unbooked_row_kept(
    tmp_path: Path,
) -> None:
    path = _claim(
        tmp_path / "claim.xlsx",
        LINE,
        (None, "2500000002", "000001", "Invoice", "A", 200, 0, 0, 200, 6.9, 193.1),
    )
    line, skipped = read("vendor", path)["claim"]
    assert line["kind"] == "line"
    assert line["booking_id"] == "A260819100005500"
    # No booking id: excluded from the totals, but every figure kept to trace.
    assert skipped["kind"] == "skipped"
    assert skipped["invoice_no"] == "2500000002"
    assert skipped["total_invoice"] == Decimal(200)
    assert skipped["commission"] == Decimal("6.9")
    assert skipped["net_payable"] == Decimal("193.1")


def test_a_line_uploaded_twice_is_read_once_and_reported(tmp_path: Path) -> None:
    """Uploading the same claim twice must not double what we owe. The same
    line written as text on one sheet and as a number on another is the same."""
    path = _claim(tmp_path / "claim.xlsx", LINE, (*LINE[:9], "34.50", LINE[10]))
    spec = travel_config().input("vendor")
    assert spec is not None
    detected: list[SheetManifest] = []
    exceptions: list[ReconException] = []
    rows = read_upload(
        spec,
        Upload("vendor", "claim.xlsx", path),
        max_rows=100,
        scope=Values({"param": {p.key: p.typed() for p in travel_config().parameters}}),
        detected=detected,
        exceptions=exceptions,
    )["claim"]
    assert len(rows) == 1
    assert detected[0].rows == 1
    (duplicate,) = exceptions
    assert duplicate.code is ExceptionCode.DUPLICATE_VENDOR_LINE
    assert duplicate.order_no == "A260819100005500"
    assert (
        duplicate.detail == "invoice 2500000001 line 000001 appears twice in the upload"
    )


def test_the_claims_own_ledger_is_read() -> None:
    periods = read("vendor", TRAVEL_CLAIMS["13"])["claim_periods"]
    assert [p["claim_period"] for p in periods] == [
        "Claim From 21 till 27 Jun 2026",
        "claim From 09 till 15 AUG 2026",
        "claim From 16 till 22 AUG 2026",
    ]
    assert periods[-1]["total_claim"] == Decimal("6985.160000000001")


def test_the_portal_reader_keeps_the_first_of_three_customer_columns() -> None:
    rows = [r for r in read("portal", TRAVEL_PORTAL)["portal"] if r.sheet == "Hotel"]
    assert len(rows) == 75
    first = rows[0]
    assert first["customer_ref"] == "45841"
    assert first["sg_order_id"] == "124"
    assert first["vertical"] == "hotel"
    assert first["status"] == "Expired"
    assert first["payment_status"] == "Pending"
    assert first["booking_id"] is None


def test_the_portal_reader_reads_a_booked_row_whole() -> None:
    rows = read("portal", TRAVEL_PORTAL)["portal"]
    booked = next(r for r in rows if r["booking_id"] == "H260819107345500")
    assert booked["order_date"] == date(2026, 8, 19)
    assert booked["order_total"] == Decimal("1161.7")
    assert booked["commission_charged"] == Decimal("58.09")
    assert booked["net_payable"] == Decimal("1103.61")
    assert booked["payment_method"] == "Apple Pay"
    # Hotels leave Commission Amount at zero and fill Commission Charged.
    assert booked["commission_amount"] == Decimal(0)


def test_the_wallet_reader_keeps_only_travel_movements() -> None:
    model = travel_config().model
    assert model is not None and model.wallet is not None
    verticals = {normalize_header(k): v for k, v in model.wallet.wallet_types.items()}
    with open_workbook(TRAVEL_WALLET) as workbook:
        events = list(
            read_wallet(
                workbook,
                "Sheet1",
                source="wallet.xlsx",
                max_rows=100_000,
                wallet_type=lambda raw: verticals.get(normalize_header(raw)),
            )
        )
    assert {e.vertical for e in events} == {"hotel", "flight"}
    assert len(events) == 46


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Invoice", "Invoice"),
        ("Credit for Returns", "Credit for Returns"),
        ("credit note", "Credit for Returns"),
        (None, "unknown"),
    ],
)
def test_billing_types(text: str | None, expected: str) -> None:
    assert claim_line(billing_type=text)["billing_type"] == expected
