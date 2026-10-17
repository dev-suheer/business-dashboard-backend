"""Golden-file tests for travel, against the sample workbooks.

Every figure here is checked against the client's own worked reconciliation in
claim 13 (its ``Analysis`` and ``Sheet1`` tabs), or against the claim's own
summary block. Where ours differs from theirs on purpose, the test says so.
"""

import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from app.recon.categories.travel.export import build_report
from app.recon.categories.travel.models import (
    BookingMatchState,
    BookingRecon,
    RefundKind,
    TravelRateConfig,
    TravelResult,
    UnmatchedReason,
    Vertical,
)
from app.recon.categories.travel.pipeline import TravelRequest, run
from app.recon.exceptions import ExceptionCode, Severity
from app.recon.inputs import Upload
from tests.conftest import TRAVEL_CLAIMS, TRAVEL_PORTAL, TRAVEL_WALLET

RATES = TravelRateConfig(
    hotel_rate=Decimal("0.05"),
    flight_rate=Decimal("0.03"),
    vat_rate=Decimal("0.15"),
    base_tolerance=Decimal("0.05"),
    commission_tolerance=Decimal("0.01"),
    rate_tolerance=Decimal("0.0005"),
)


def reconcile(claim: str, *, wallet: bool = True) -> TravelResult:
    return run(
        TravelRequest(
            portal=Upload(role="portal", filename="portal.xlsx", path=TRAVEL_PORTAL),
            vendor=Upload(
                role="vendor",
                filename=TRAVEL_CLAIMS[claim].name,
                path=TRAVEL_CLAIMS[claim],
            ),
            wallet=(
                Upload(role="wallet", filename="wallet.xlsx", path=TRAVEL_WALLET)
                if wallet
                else None
            ),
            rates=RATES,
            period_start=None,
            period_end=None,
            max_rows=1_000_000,
        )
    )


@pytest.fixture(scope="module")
def result() -> TravelResult:
    """Claim 13 — the one week the client also reconciled by hand."""
    return reconcile("13")


def booking(result: TravelResult, booking_id: str) -> BookingRecon:
    return next(b for b in result.bookings if b.booking_id == booking_id)


def test_the_whole_export_is_read(result: TravelResult) -> None:
    assert result.summary.portal_rows == 206
    assert result.summary.portal_bookings == 45
    assert result.period_start == date(2026, 7, 26)
    assert result.period_end == date(2026, 8, 22)
    assert result.vendor_name == "Almosafer"


def test_the_claim_is_read_whole(result: TravelResult) -> None:
    assert result.summary.vendor_lines == 8
    assert result.summary.vendor_bookings == 7
    assert result.summary.bookings_matched == 7
    # Every booking this claim bills has a portal row.
    assert result.summary.bookings_vendor_only == 0


def test_the_claim_totals_agree_with_the_claims_own_summary(
    result: TravelResult,
) -> None:
    """Claim 13's Sheet2 reads 6985.16 / 255.19765 / 6729.96235."""
    assert result.summary.claim_base == Decimal("6985.16")
    assert result.summary.claim_commission == Decimal("255.20")
    assert result.summary.claim_net_payable == Decimal("6729.96")
    # Every booking this claim bills matched, so the matched side is all of it.
    assert result.summary.vendor_base == result.summary.claim_base


def test_the_hotel_base_difference_matches_the_clients_sheet(
    result: TravelResult,
) -> None:
    """Their Sheet1: 1161.70 against 1162.76, a difference of -1.06."""
    hotel = booking(result, "H260819107345500")
    assert hotel.portal_base == Decimal("1161.70")
    assert hotel.vendor_base == Decimal("1162.76")
    assert hotel.base_diff == Decimal("-1.06")
    assert result.summary.base_difference == Decimal("-1.06")


def test_the_settled_figures_match_the_clients_sheet(result: TravelResult) -> None:
    """Their 'Actual As Vendor' 66.79775 and 'Net Payable' 1094.90225, which
    this reports quantized once, as every money figure is."""
    hotel = booking(result, "H260819107345500")
    assert hotel.locality.value == "international"
    assert hotel.contract_rate == Decimal("0.0575")
    assert hotel.commission_as_vendor == Decimal("66.80")
    assert hotel.net_payable_as_vendor == Decimal("1094.90")
    # Their 'Difference': -0.06095, at the contract rate on both sides.
    assert hotel.commission_diff_as_vendor == Decimal("-0.06")
    assert result.summary.commission_difference_at_contract == Decimal("-0.06")
    assert result.summary.net_payable_difference_at_contract == Decimal("-1.00")


def test_the_vat_uplift_is_measured(result: TravelResult) -> None:
    """We charged 5%; the claim bills 5.75%. The difference is uncollected.

    Totalled over the matched bookings only — the four international ones,
    4.31 + 5.96 + 8.71 + 3.39 — so each booking is counted on the claim that
    bills it rather than on every week's run.
    """
    hotel = booking(result, "H260819107345500")
    assert hotel.portal_commission == Decimal("58.09")
    assert hotel.sg_expected_commission == Decimal("58.09")
    assert hotel.vat_uplift_shortfall == Decimal("8.71")
    assert result.summary.vat_uplift_shortfall == Decimal("22.37")
    shortfall = next(
        e
        for e in result.exceptions
        if e.code is ExceptionCode.SG_COMMISSION_SHORT_OF_VENDOR_RATE
    )
    assert shortfall.amount == Decimal("22.37")


def test_a_local_booking_is_billed_at_our_own_rate(result: TravelResult) -> None:
    local = booking(result, "A26082185665500")
    assert local.locality.value == "local"
    assert local.contract_rate == Decimal("0.03")
    assert local.portal_commission == local.vendor_commission == Decimal("28.05")
    assert local.vat_uplift_shortfall == Decimal(0)


def test_a_multi_leg_booking_is_compared_once(result: TravelResult) -> None:
    """Two invoice lines, 481.38 + 477.41, against one portal row of 958.79."""
    multi = booking(result, "A260813120395500")
    assert multi.vendor_lines == 2
    assert multi.vendor_base == Decimal("958.79")
    assert multi.portal_base == Decimal("958.79")
    assert multi.base_diff == Decimal(0)
    assert "MULTI_LEG" in multi.flags


def test_bookings_billed_on_another_week_are_not_breaks(result: TravelResult) -> None:
    """The export spans a month; this claim covers one week of it."""
    codes = result.summary.exception_counts
    assert codes["PRIOR_PERIOD"] == 34
    assert codes["BOOKING_MISSING_IN_VENDOR"] == 4


def test_the_payment_capture_defect_is_found(result: TravelResult) -> None:
    """SG order 198 collected 13.00 of a 276.25 total."""
    split = next(
        e for e in result.exceptions if e.code is ExceptionCode.PAYMENT_SPLIT_MISMATCH
    )
    assert split.amount == Decimal("263.25")
    assert len(result.portal_rows_flagged) == 3


def test_every_payment_method_is_counted(result: TravelResult) -> None:
    """Every booked row is counted; rows with no booking ID are left out."""
    by_label = {m.label: m for m in result.payment_methods.by_label}
    assert set(by_label) == {
        "Card",
        "Tamara",
        "Apple Pay",
        "Wallet",
        "Apple Pay + Wallet",
        "Card + Wallet",
        "Tamara + Wallet",
    }
    assert sum(m.rows for m in result.payment_methods.by_label) == 45
    by_instrument = {m.label: m for m in result.payment_methods.by_instrument}
    # The wallet column totals what actually moved on wallets, combined
    # payments included.
    assert by_instrument["Wallet"].order_total == Decimal("7832.93")


def test_the_wallet_history_accounts_for_every_wallet_payment(
    result: TravelResult,
) -> None:
    wallet = result.wallet
    assert wallet is not None
    assert (wallet.window_start, wallet.window_end) == (
        date(2026, 8, 1),
        date(2026, 8, 15),
    )
    # Booked rows only: the ten wallet-paid rows with no booking ID are left out.
    assert wallet.rows_checked == 24
    assert wallet.matched == 17
    assert wallet.amount_mismatch == 0
    assert wallet.missing == 0
    # The rest fall outside the history's own two-week window.
    assert wallet.out_of_window == 7
    # Leaving a row out of the check does not make its movement portal-less.
    assert len(wallet.unmatched_history) == 1


def test_no_wallet_file_means_no_wallet_block() -> None:
    assert reconcile("13", wallet=False).wallet is None


def test_the_overcharge_and_its_credit_are_both_found() -> None:
    """Claim 10 bills A260629226265500 at 4.4724%; claim 12 credits it back."""
    overcharge = next(
        e
        for e in reconcile("10", wallet=False).exceptions
        if e.code is ExceptionCode.VENDOR_COMMISSION_OVERCHARGE
    )
    assert overcharge.order_no == "A260629226265500"
    assert overcharge.amount == Decimal("66.82")

    credited = reconcile("12", wallet=False)
    assert credited.summary.vendor_adjustments == Decimal("-66.82")
    applied = next(
        e for e in credited.exceptions if e.code is ExceptionCode.ADJUSTMENT_APPLIED
    )
    assert applied.order_no == "A260629226265500"


def test_a_partial_refund_reports_what_was_retained() -> None:
    """Claim 10 credits A26070648655500 less an 824 fee and a 30 change fee."""
    result = reconcile("10", wallet=False)
    refund = booking(result, "A26070648655500")
    assert refund.refund is RefundKind.PARTIAL
    assert refund.match_state is BookingMatchState.VENDOR_ONLY
    assert refund.vendor_cancel_fee == Decimal("824.00")
    assert refund.vendor_change_fee == Decimal("30.00")
    assert result.summary.retained_fees == Decimal("854.00")


def test_a_refund_the_portal_never_shows_is_taken_from_the_claim() -> None:
    """Claim 12 credits H260810138915500 in full; the portal still shows 850."""
    result = reconcile("12", wallet=False)
    refunded = booking(result, "H260810138915500")
    assert refunded.refund is RefundKind.FULL
    assert refunded.portal_base == Decimal("850.00")
    assert refunded.vendor_base == Decimal(0)
    # The vendor reversed its commission; we did not reverse ours.
    assert refunded.vendor_commission == Decimal(0)
    assert refunded.portal_commission == Decimal("42.50")
    kept = [
        e
        for e in result.exceptions
        if e.code is ExceptionCode.COMMISSION_NOT_REVERSED_ON_REFUND
    ]
    assert [e.amount for e in kept] == [Decimal("42.50")]


def test_a_refunded_booking_is_not_counted_as_a_base_difference() -> None:
    """Its two sides are meant to disagree, so it belongs on the refunds sheet."""
    result = reconcile("12", wallet=False)
    assert result.summary.base_difference == Decimal("-3.86")


def test_the_claims_own_ledger_is_carried_through(result: TravelResult) -> None:
    assert [p.claim_period for p in result.claim_periods] == [
        "Claim From 21 till 27 Jun 2026",
        "claim From 09 till 15 AUG 2026",
        "claim From 16 till 22 AUG 2026",
    ]


def test_uploading_the_same_claim_twice_would_not_double_it() -> None:
    """Every line of every claim is distinct under the deduplication key."""
    for name in TRAVEL_CLAIMS:
        result = reconcile(name, wallet=False)
        assert ExceptionCode.DUPLICATE_VENDOR_LINE not in {
            e.code for e in result.exceptions
        }


def test_the_exception_list_leads_with_what_needs_action(
    result: TravelResult,
) -> None:
    severities = [e.severity for e in result.exceptions]
    assert severities == sorted(
        severities, key=lambda s: ["high", "medium", "info"].index(str(s))
    )
    assert severities[0] is Severity.HIGH


def test_the_report_carries_every_sheet(result: TravelResult, tmp_path: Path) -> None:
    path = build_report(result, tmp_path / "travel.xlsx")
    assert path.exists()
    with zipfile.ZipFile(path) as book:
        sheets = book.read("xl/workbook.xml").decode()
    for name in (
        "Summary",
        "Matched - Booking Analysis",
        "Matched - Base Differences",
        "Matched - Invoice Lines",
        "Unmatched - Explanation",
        "Exceptions",
        "Ref - Portal Rows",
        "Ref - Vendor Rows",
        "Ref - Payment Methods",
        "Ref - Wallet",
        "Ref - Claim Periods",
    ):
        assert name in sheets


def test_the_report_omits_the_wallet_sheet_when_no_wallet_was_uploaded(
    tmp_path: Path,
) -> None:
    path = build_report(reconcile("13", wallet=False), tmp_path / "travel.xlsx")
    with zipfile.ZipFile(path) as book:
        sheets = book.read("xl/workbook.xml").decode()
    assert "Ref - Wallet" not in sheets


def test_hotel_and_flight_commission_split_with_its_ex_vat_column(
    result: TravelResult,
) -> None:
    s = result.summary
    assert s.hotel_commission + s.flight_commission == s.commission_as_vendor
    # Matched bookings only: 66.80 / 1.15 and 188.33 / 1.15.
    assert s.hotel_commission_ex_vat == Decimal("58.09")
    assert s.flight_commission_ex_vat == Decimal("163.77")


def test_every_vertical_is_resolved(result: TravelResult) -> None:
    assert {b.vertical for b in result.bookings} == {Vertical.HOTEL, Vertical.FLIGHT}


def test_only_bookings_both_sides_agree_on_are_matched(result: TravelResult) -> None:
    """Claim 13 bills seven of the export's 45 bookings; the rest are explained."""
    s = result.summary
    assert s.bookings_matched == 7
    assert s.bookings_unmatched == 38
    assert s.unmatched_by_reason == {"cancelled": 3, "missing_in_vendor": 35}
    unmatched = [b for b in result.bookings if b.unmatched_reason is not None]
    assert len(unmatched) == 38
    assert all(b.explanation for b in unmatched)
    # The totals are taken from the matched bookings alone.
    matched = [b for b in result.bookings if b.unmatched_reason is None]
    assert s.portal_base == sum((b.portal_base for b in matched), Decimal(0))


def test_a_booking_billed_on_another_week_says_so(result: TravelResult) -> None:
    early = booking(result, "A260726150195500")
    assert early.unmatched_reason is UnmatchedReason.MISSING_IN_VENDOR
    assert "before this claim's week" in early.explanation
    assert "expected on another claim" in early.explanation


def test_a_refund_matched_by_id_is_explained_not_matched() -> None:
    """Both sides carry H260810138915500, but the claim credits it in full."""
    result = reconcile("12", wallet=False)
    refunded = booking(result, "H260810138915500")
    assert refunded.match_state is BookingMatchState.MATCHED
    assert refunded.unmatched_reason is UnmatchedReason.REFUNDED
    assert "full refund" in refunded.explanation
    cancelled = booking(result, "H260809145945500")
    assert cancelled.unmatched_reason is UnmatchedReason.CANCELLED
    assert "no credit note" in cancelled.explanation
    assert result.summary.unmatched_by_reason == {
        "refunded": 1,
        "cancelled": 2,
        "missing_in_vendor": 33,
        "missing_in_portal": 1,
    }


def test_a_vendor_only_booking_is_missing_from_the_portal() -> None:
    result = reconcile("10", wallet=False)
    prior = booking(result, "A260629226265500")
    assert prior.unmatched_reason is UnmatchedReason.MISSING_IN_PORTAL
    assert "before the portal export" in prior.explanation
    credit = booking(result, "A26070648655500")
    assert credit.unmatched_reason is UnmatchedReason.REFUNDED
    assert "854.00 in fees retained" in credit.explanation


def test_rows_without_a_booking_id_are_kept_but_not_calculated(
    result: TravelResult,
) -> None:
    assert result.summary.portal_rows_without_booking_id == 161
    unbooked = [r for r in result.references.portal_rows if r.booking_id is None]
    assert len(unbooked) == 161
    assert result.wallet is not None
    assert all(check.booking_id for check in result.wallet.checks)
    # Their exceptions are still raised: two expired orders kept commission.
    assert result.summary.exception_counts["COMMISSION_ON_UNCOMPLETED_ORDER"] == 2


@pytest.mark.parametrize("claim", ["10", "11", "12", "13"])
def test_every_portal_row_is_reported_exactly_once(claim: str) -> None:
    """A matched booking's own row, or a reference: never both, never neither."""
    result = reconcile(claim, wallet=False)
    references = {(r.sheet, r.row) for r in result.references.portal_rows}
    assert len(references) == len(result.references.portal_rows)
    assert result.summary.bookings_matched + len(references) == (
        result.summary.portal_rows
    )


@pytest.mark.parametrize("claim", ["12", "13"])
def test_the_report_splits_matched_from_unmatched(claim: str, tmp_path: Path) -> None:
    result = reconcile(claim)
    path = build_report(result, tmp_path / "travel.xlsx")
    book = openpyxl.load_workbook(path, read_only=True)

    def data_rows(sheet: str, header_rows: int = 1) -> list[tuple[object, ...]]:
        rows = list(book[sheet].iter_rows(min_row=header_rows + 1, values_only=True))
        # Up to the computed total row.
        end = next(i for i, r in enumerate(rows) if str(r[0] or "").startswith("Total"))
        return rows[:end]

    s = result.summary
    analysis = data_rows("Matched - Booking Analysis", header_rows=2)
    assert len(analysis) == s.bookings_matched
    assert len(data_rows("Unmatched - Explanation")) == s.bookings_unmatched
    assert len(data_rows("Ref - Portal Rows")) == s.portal_rows - s.bookings_matched
    vendor_refs = data_rows("Ref - Vendor Rows")
    unmatched_lines = [r for r in vendor_refs if str(r[0]).startswith("Unmatched")]
    assert len(data_rows("Matched - Invoice Lines")) + len(unmatched_lines) == (
        s.vendor_lines
    )
    summary = [r[0] for r in book["Summary"].iter_rows(values_only=True)]
    assert f"{s.bookings_unmatched} booking IDs could not be matched" in summary
