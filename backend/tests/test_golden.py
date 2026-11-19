"""Golden-file tests against the sample workbooks.

Every figure here comes from plan.md section 9. They are deliberately *not* the
legacy workbook's 4,473.60 / 14,223.61 / 13,876.76 — that workbook used the
catalogue price as the base on three rows, and the 0.50 difference is its error.
"""

import zipfile
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.recon.exceptions import ExceptionCode
from app.runner import RunResult
from tests.eshop import order, orders, report, run_eshop

D = Decimal


@pytest.fixture(scope="module")
def result() -> RunResult:
    """Reconcile the sample cycle once and share it across the module. One
    workbook holds both the portal export and the promo catalogue, so the same
    file goes into both roles and each detects its sheet."""
    return run_eshop()


def summary(result: RunResult) -> dict[str, Any]:
    return result.metrics


# --- volumes ----------------------------------------------------------------


def test_volumes(result: RunResult) -> None:
    s = summary(result)
    assert s["portal_lines"] == 75
    assert s["orders"] == 37
    assert s["ledger_entries"] == 64
    assert s["orders_matched"] == 37
    assert s["orders_exact"] == 33


def test_the_four_orders_differing_by_one_cent(result: RunResult) -> None:
    differing = {
        o.key: o.values["amount_diff"]
        for o in orders(result)
        if o.values["amount_diff"] not in (None, D(0))
    }
    assert set(differing) == {
        "SG-SH-05785",
        "SG-SH-06874",
        "SG-SH-07568",
        "SG-SH-08458",
    }
    assert all(abs(v) == D("0.01") for v in differing.values())


# --- money ------------------------------------------------------------------


def test_commission_bases(result: RunResult) -> None:
    """The 15% base is what pins the 9 August cutoff; an 8 August cutoff misses it."""
    assert summary(result)["standard_base"] == D("11996.91")
    assert summary(result)["promo_base"] == D("4473.10")


def test_payable_and_net(result: RunResult) -> None:
    s = summary(result)
    assert s["gross_sales"] == D("17141.01")
    assert s["payable_before_adjustments"] == D("14223.16")
    assert s["adjustments"] == D("-346.85")
    assert s["net_payable"] == D("13876.31")


def test_payable_reconciles_to_its_own_parts(result: RunResult) -> None:
    s = summary(result)
    assert (
        s["standard_base"] + s["promo_base"] - s["commission_earned"]
        == s["payable_before_adjustments"]
    )
    assert s["payable_before_adjustments"] + s["adjustments"] == s["net_payable"]


def test_the_two_adjustments(result: RunResult) -> None:
    adjustments = {
        o.key: (o.values["adjustments"], o.values["treatment"])
        for o in orders(result)
        if o.values["adjustments"]
    }
    assert adjustments == {
        "SG-SH-03344": (D("-238.85"), "CLAWBACK"),
        "SG-SH-07435": (D("-108.00"), "RETURN_ADJUSTED"),
    }


def test_the_clawback_comes_from_the_paid_column_not_an_upload(
    result: RunResult,
) -> None:
    """No settlement file was supplied, yet the clawback still resolved."""
    clawback = order(result, "SG-SH-03344")
    assert clawback.values["adjustments"] == D("-238.85")
    assert not clawback.values["included"]
    assert clawback.verdict == "Clawback"
    assert ExceptionCode.CLAWBACK_UNRESOLVED not in [
        e.code for e in result.exceptions if e.order_no == "SG-SH-03344"
    ]


def test_vendor_closing_balance(result: RunResult) -> None:
    assert summary(result)["vendor_closing_balance"] == D("17424.75")


# --- period and matching ----------------------------------------------------


def test_period_excludes_the_prior_cycle_clawback(result: RunResult) -> None:
    """SG-SH-03344 is a 3 July order carried in for its clawback alone.

    Letting it set the period start would stretch the window back three weeks
    and turn ten prior-period statement rows into false breaks.
    """
    assert result.history["period_start"] == date(2026, 7, 22)
    assert result.history["period_end"] == date(2026, 8, 25)


def test_statement_orders_outside_the_period_are_all_informational(
    result: RunResult,
) -> None:
    counts = Counter(str(e.code) for e in result.exceptions)
    assert counts["PRIOR_PERIOD"] + counts["LATER_PERIOD"] == 21
    assert counts["MISSING_FROM_PORTAL"] == 0


def test_the_ambiguous_credit_note_resolved_to_the_rto(result: RunResult) -> None:
    """SG-SH-0611 + 168.00 must land on SG-SH-06118, not SG-SH-06115."""
    assert not [
        e for e in result.exceptions if e.code is ExceptionCode.AMBIGUOUS_CREDIT_NOTE
    ]
    rto = order(result, "SG-SH-06118")
    assert "RTO" in rto.values["statuses"]
    assert not rto.values["included"]
    # Its credit note was matched, so no missing-credit-note exception.
    assert rto.values["has_credit_note"]
    assert ExceptionCode.RTO_WITHOUT_CREDIT_NOTE not in [
        e.code for e in result.exceptions if e.order_no == "SG-SH-06118"
    ]
    # The look-alike order is untouched and still payable.
    assert order(result, "SG-SH-06115").values["included"]


def test_the_three_rtos_are_excluded_from_the_bases(result: RunResult) -> None:
    excluded = {
        o.key: o.values["portal_gmv"]
        for o in orders(result)
        if not o.values["included"]
    }
    assert excluded == {
        "SG-SH-03344": D("281.00"),
        "SG-SH-05422": D("222.00"),
        "SG-SH-06118": D("168.00"),
    }
    s = summary(result)
    assert (
        s["gross_sales"] - sum(excluded.values())
        == s["standard_base"] + s["promo_base"]
    )


def test_a_single_merchant(result: RunResult) -> None:
    assert summary(result)["merchant_id"] == "34"
    assert result.history["vendor"] == "34"


def test_the_detected_manifest_names_the_right_sheets(result: RunResult) -> None:
    detected = {m.role: m for m in result.detected}
    assert detected["portal"].sheets == ["E-SHop 1 Vendor "]
    assert detected["promo"].sheets == ["Discount Base Sheet"]
    assert len(detected["vendor"].sheets) == 4
    assert detected["portal"].rows == 75
    assert detected["vendor"].rows == 64


def test_rates_are_echoed_back_for_audit(result: RunResult) -> None:
    assert result.parameters["promo_cutoff"] == date(2026, 8, 9)
    assert result.parameters["standard_rate"] == D("0.15")
    assert result.parameters["promo_rate"] == D("0.10")


def test_the_history_entry_names_the_cycle(result: RunResult) -> None:
    assert result.history["report_name"] == (
        "reconciliation-eshop-2026-07-22-to-2026-08-25.xlsx"
    )
    assert result.history["counted"] == 34
    assert result.history["total"] == 37


# --- export -----------------------------------------------------------------


def test_the_report_follows_the_food_workbook(
    result: RunResult, tmp_path: Path
) -> None:
    """Without the uploads there is nothing to copy or to point a cashback at."""
    path = report(result, tmp_path / "report.xlsx")
    assert path.stat().st_size > 0
    with zipfile.ZipFile(path) as book:
        names = book.read("xl/workbook.xml").decode()
    for sheet in ("Dashboard", "Orders", "Exceptions"):
        assert sheet in names
    for sheet in ("Wallet", "B2C Invoices ERP", "Portal", "Vendor", "Finance Report"):
        assert f'name="{sheet}"' not in names
