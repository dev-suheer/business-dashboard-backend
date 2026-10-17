"""Golden-file tests against the sample workbooks.

Every figure here comes from plan.md section 9. They are deliberately *not* the
legacy workbook's 4,473.60 / 14,223.61 / 13,876.76 — that workbook used the
catalogue price as the base on three rows, and the 0.50 difference is its error.
"""

import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.recon.engine import ReconRequest, Upload, reconcile
from app.recon.exceptions import ExceptionCode
from app.recon.export import build_report
from app.recon.models import OrderStatus, RateConfig, ReconResult
from tests.conftest import VENDOR_STATEMENT, WORKINGS

D = Decimal


@pytest.fixture(scope="module")
def result() -> ReconResult:
    """Reconcile the sample cycle once and share it across the module."""
    from app.core.config import settings

    rates = RateConfig(
        standard_rate=settings.STANDARD_RATE,
        promo_rate=settings.PROMO_RATE,
        promo_cutoff=settings.PROMO_CUTOFF,
        line_tolerance=settings.LINE_TOLERANCE,
        order_tolerance=settings.ORDER_TOLERANCE,
    )
    return reconcile(
        ReconRequest(
            category="eshop",
            # One workbook holds both the portal export and the promo catalogue,
            # so the same file goes into both roles and each detects its sheet.
            portal=Upload("portal", WORKINGS.name, WORKINGS),
            promo=Upload("promo", WORKINGS.name, WORKINGS),
            vendor=Upload("vendor", VENDOR_STATEMENT.name, VENDOR_STATEMENT),
            settlements=None,
            rates=rates,
            period_start=None,
            period_end=None,
            max_rows=1_000_000,
        )
    )


# --- volumes ----------------------------------------------------------------


def test_volumes(result: ReconResult) -> None:
    s = result.summary
    assert s.portal_lines == 75
    assert s.orders == 37
    assert s.ledger_entries == 64
    assert s.orders_matched == 37
    assert s.orders_exact == 33


def test_the_four_orders_differing_by_one_cent(result: ReconResult) -> None:
    differing = {
        o.order_no: o.amount_diff
        for o in result.orders
        if o.amount_diff not in (None, D(0))
    }
    assert set(differing) == {
        "SG-SH-05785",
        "SG-SH-06874",
        "SG-SH-07568",
        "SG-SH-08458",
    }
    assert all(abs(v) == D("0.01") for v in differing.values())


# --- money ------------------------------------------------------------------


def test_commission_bases(result: ReconResult) -> None:
    """The 15% base is what pins the 9 August cutoff; an 8 August cutoff misses it."""
    assert result.summary.standard_base == D("11996.91")
    assert result.summary.promo_base == D("4473.10")


def test_payable_and_net(result: ReconResult) -> None:
    s = result.summary
    assert s.gross_sales == D("17141.01")
    assert s.payable_before_adjustments == D("14223.16")
    assert s.adjustments == D("-346.85")
    assert s.net_payable == D("13876.31")


def test_payable_reconciles_to_its_own_parts(result: ReconResult) -> None:
    s = result.summary
    assert (
        s.standard_base + s.promo_base - s.commission_earned
        == s.payable_before_adjustments
    )
    assert s.payable_before_adjustments + s.adjustments == s.net_payable


def test_the_two_adjustments(result: ReconResult) -> None:
    adjustments = {
        o.order_no: (o.adjustments, o.flags) for o in result.orders if o.adjustments
    }
    assert adjustments == {
        "SG-SH-03344": (D("-238.85"), ["CLAWBACK"]),
        "SG-SH-07435": (D("-108.00"), ["RETURN_ADJUSTED"]),
    }


def test_the_clawback_comes_from_the_paid_column_not_an_upload(
    result: ReconResult,
) -> None:
    """No settlement file was supplied, yet the clawback still resolved."""
    clawback = next(o for o in result.orders if o.order_no == "SG-SH-03344")
    assert clawback.adjustments == D("-238.85")
    assert not clawback.included_in_payable
    assert ExceptionCode.CLAWBACK_UNRESOLVED not in [
        e.code for e in result.exceptions if e.order_no == "SG-SH-03344"
    ]


def test_vendor_closing_balance(result: ReconResult) -> None:
    assert result.summary.vendor_closing_balance == D("17424.75")


# --- period and matching ----------------------------------------------------


def test_period_excludes_the_prior_cycle_clawback(result: ReconResult) -> None:
    """SG-SH-03344 is a 3 July order carried in for its clawback alone.

    Letting it set the period start would stretch the window back three weeks
    and turn ten prior-period statement rows into false breaks.
    """
    assert result.period_start == date(2026, 7, 22)
    assert result.period_end == date(2026, 8, 25)


def test_statement_orders_outside_the_period_are_all_informational(
    result: ReconResult,
) -> None:
    counts = result.summary.exception_counts
    assert counts.get("PRIOR_PERIOD", 0) + counts.get("LATER_PERIOD", 0) == 21
    assert counts.get("MISSING_FROM_PORTAL", 0) == 0


def test_the_ambiguous_credit_note_resolved_to_the_rto(result: ReconResult) -> None:
    """SG-SH-0611 + 168.00 must land on SG-SH-06118, not SG-SH-06115."""
    assert not [
        e for e in result.exceptions if e.code is ExceptionCode.AMBIGUOUS_CREDIT_NOTE
    ]
    rto = next(o for o in result.orders if o.order_no == "SG-SH-06118")
    assert OrderStatus.RTO in rto.statuses
    assert not rto.included_in_payable
    # Its credit note was matched, so no missing-credit-note exception.
    assert ExceptionCode.RTO_WITHOUT_CREDIT_NOTE not in [
        e.code for e in result.exceptions if e.order_no == "SG-SH-06118"
    ]
    # The look-alike order is untouched and still payable.
    other = next(o for o in result.orders if o.order_no == "SG-SH-06115")
    assert other.included_in_payable


def test_the_three_rtos_are_excluded_from_the_bases(result: ReconResult) -> None:
    excluded = {
        o.order_no: o.portal_gmv for o in result.orders if not o.included_in_payable
    }
    assert excluded == {
        "SG-SH-03344": D("281.00"),
        "SG-SH-05422": D("222.00"),
        "SG-SH-06118": D("168.00"),
    }
    s = result.summary
    assert s.gross_sales - sum(excluded.values()) == s.standard_base + s.promo_base


def test_a_single_merchant(result: ReconResult) -> None:
    assert result.merchant_id == "34"


def test_the_detected_manifest_names_the_right_sheets(result: ReconResult) -> None:
    detected = {m.role: m for m in result.detected}
    assert detected["portal"].sheets == ["E-SHop 1 Vendor "]
    assert detected["promo"].sheets == ["Discount Base Sheet"]
    assert len(detected["vendor"].sheets) == 4
    assert detected["portal"].rows == 75
    assert detected["vendor"].rows == 64


def test_rates_are_echoed_back_for_audit(result: ReconResult) -> None:
    assert result.rates.promo_cutoff == date(2026, 8, 9)
    assert result.rates.standard_rate == D("0.15")
    assert result.rates.promo_rate == D("0.10")


# --- export -----------------------------------------------------------------


def test_the_report_has_all_six_sheets(result: ReconResult, tmp_path: Path) -> None:
    path = build_report(result, tmp_path / "report.xlsx")
    assert path.stat().st_size > 0
    with zipfile.ZipFile(path) as book:
        names = book.read("xl/workbook.xml").decode()
    for sheet in (
        "Summary",
        "Matched Detail",
        "Exceptions",
        "Commission Variance",
        "Settlement Bridge",
        "Unmatched Ledger Rows",
    ):
        assert sheet in names
