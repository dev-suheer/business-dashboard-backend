"""Matching the portal's orders against the vendor statement: credit notes,
tolerance, the three-way period split, and the closing balance.

These are version 1 of eShop's configuration (``seed_eshop.py``), run by the
generic runner on rows built by hand.
"""

from datetime import date
from decimal import Decimal

from app.recon.exceptions import ExceptionCode, ReconException, Severity
from app.runner import Row, RunResult
from tests.eshop import order, portal_line, reconcile_eshop, statement_row


def line(order_no: str, gmv: str, *, status: str = "Delivered") -> Row:
    return portal_line(
        order_no,
        gmv=Decimal(gmv),
        actual_commission=Decimal(gmv) * Decimal("0.15"),
        status=status,
    )


def invoice(
    order_no: str | None, debit: str, when: date, *, ref: str | None = None
) -> Row:
    return statement_row(
        doc_no=f"doc-{order_no or ref}",
        doc_date=when,
        raw_desc=f"طلب {order_no}" if order_no else "فاتورة",
        raw_ref=ref,
        debit=Decimal(debit),
    )


def credit_note(ref: str, credit: str, when: date = date(2026, 8, 16)) -> Row:
    return statement_row(
        doc_no=f"cn-{ref}",
        doc_type="فاتورة مردود بيع أجل",
        doc_date=when,
        raw_desc="مردود",
        raw_ref=ref,
        debit=None,
        credit=Decimal(credit),
    )


def run(portal: list[Row], statement: list[Row]) -> RunResult:
    return reconcile_eshop(portal, statement)


def found(result: RunResult, code: ExceptionCode) -> list[ReconException]:
    return [e for e in result.exceptions if e.code is code]


# --- credit-note resolution (plan.md 3.7) -----------------------------------


def test_prefix_plus_amount_resolves_the_ambiguous_reference() -> None:
    """SG-SH-0611 prefixes two orders; only the amount separates them."""
    note = credit_note("SG-SH-0611", "168.00")
    result = run([line("SG-SH-06115", "108.00"), line("SG-SH-06118", "168.00")], [note])
    assert not found(result, ExceptionCode.AMBIGUOUS_CREDIT_NOTE)
    assert note["order_no"] == "SG-SH-06118"


def test_the_other_amount_resolves_to_the_other_order() -> None:
    note = credit_note("SG-SH-0611", "108.00")
    run([line("SG-SH-06115", "108.00"), line("SG-SH-06118", "168.00")], [note])
    assert note["order_no"] == "SG-SH-06115"


def test_an_unresolvable_reference_is_reported_never_guessed() -> None:
    # Both candidates carry the same amount, so the amount cannot separate them.
    note = credit_note("SG-SH-0611", "168.00")
    result = run([line("SG-SH-06115", "168.00"), line("SG-SH-06118", "168.00")], [note])
    (ambiguous,) = found(result, ExceptionCode.AMBIGUOUS_CREDIT_NOTE)
    assert ambiguous.detail == (
        "Reference 'SG-SH-0611' prefix-matches 2 orders (SG-SH-06115, SG-SH-06118) "
        "and the amount 168.00 did not separate them"
    )
    assert note["order_no"] is None
    assert note["ambiguous"] is True


def test_a_unique_prefix_needs_no_amount() -> None:
    note = credit_note("SG-SH-0542", "1.00")
    run([line("SG-SH-05422", "222.00")], [note])
    assert note["order_no"] == "SG-SH-05422"


def test_a_prefix_matching_nothing_is_left_alone() -> None:
    note = credit_note("SG-SH-0139", "82.00")
    result = run([line("SG-SH-99999", "1")], [note])
    assert not found(result, ExceptionCode.AMBIGUOUS_CREDIT_NOTE)
    assert note["order_no"] is None


# --- tolerance --------------------------------------------------------------


def test_a_one_cent_difference_is_within_tolerance() -> None:
    """The vendor rounds on their own unit-price base."""
    result = run(
        [line("SG-SH-05785", "300.00")],
        [invoice("SG-SH-05785", "300.01", date(2026, 8, 2))],
    )
    values = order(result, "SG-SH-05785").values
    assert values["amount_diff"] == Decimal("-0.01")
    assert values["matched"]
    assert not found(result, ExceptionCode.AMOUNT_MISMATCH)


def test_a_difference_beyond_tolerance_is_an_exception() -> None:
    result = run(
        [line("SG-SH-05785", "300.00")],
        [invoice("SG-SH-05785", "310.00", date(2026, 8, 2))],
    )
    (mismatch,) = found(result, ExceptionCode.AMOUNT_MISMATCH)
    assert mismatch.detail == "Portal 300.00 vs statement 310.00"
    assert order(result, "SG-SH-05785").verdict == "Amount mismatch"


def test_an_order_absent_from_the_statement_is_reported() -> None:
    result = run([line("SG-SH-05785", "300.00")], [])
    assert [e.code for e in result.exceptions] == [
        ExceptionCode.ORDER_MISSING_IN_VENDOR
    ]
    assert order(result, "SG-SH-05785").verdict == "Missing from vendor"


# --- the three-way period split (plan.md 3.5) -------------------------------


def test_statement_orders_outside_the_period_are_informational_not_breaks() -> None:
    entries = [
        invoice("SG-SH-01397", "82.00", date(2026, 5, 18)),  # before
        invoice("SG-SH-12154", "198.00", date(2026, 9, 3)),  # after
        invoice("SG-SH-09999", "100.00", date(2026, 8, 1)),  # inside: a real break
    ]
    # The period is the portal's own order dates: 2026-08-01, its one line's.
    result = run([line("SG-SH-05785", "300.00")], entries)
    by_code = {e.code: e for e in result.exceptions}
    assert by_code[ExceptionCode.PRIOR_PERIOD].order_no == "SG-SH-01397"
    assert by_code[ExceptionCode.PRIOR_PERIOD].detail == (
        "Invoiced 2026-05-18, before the period opened on 2026-08-01"
    )
    assert by_code[ExceptionCode.LATER_PERIOD].order_no == "SG-SH-12154"
    assert by_code[ExceptionCode.MISSING_FROM_PORTAL].order_no == "SG-SH-09999"
    # Only the in-period one is a real exception.
    assert by_code[ExceptionCode.PRIOR_PERIOD].severity is Severity.INFO
    assert by_code[ExceptionCode.LATER_PERIOD].severity is Severity.INFO
    assert by_code[ExceptionCode.MISSING_FROM_PORTAL].severity is Severity.HIGH
    # They are not orders of the portal's.
    assert result.metrics["orders"] == 1


# --- credit notes against portal status -------------------------------------


def test_an_rto_with_no_credit_note_is_flagged() -> None:
    result = run(
        [line("SG-SH-05422", "222.00", status="RTO")],
        [invoice("SG-SH-05422", "222.00", date(2026, 8, 2))],
    )
    (flagged,) = found(result, ExceptionCode.RTO_WITHOUT_CREDIT_NOTE)
    assert flagged.detail == "Status RTO with no reversing credit note in the statement"


def test_a_credit_note_with_no_reversing_status_is_flagged() -> None:
    entries = [
        invoice("SG-SH-06115", "108.00", date(2026, 8, 2)),
        credit_note("SG-SH-0611", "108.00"),
    ]
    result = run([line("SG-SH-06115", "108.00")], entries)
    assert found(result, ExceptionCode.CREDIT_NOTE_WITHOUT_RTO)


# --- closing balance --------------------------------------------------------


def test_closing_balance_is_the_last_row_not_merely_the_latest_date() -> None:
    """Several rows share the closing date; the last of them is the balance."""
    when = date(2026, 9, 3)
    entries = [
        statement_row(
            doc_no=f"doc-{n}",
            doc_date=when,
            raw_desc=f"طلب SG-SH-1215{n}",
            balance=Decimal(balance),
        )
        for n, balance in enumerate(("15543.80", "16011.79", "16446.78", "17424.75"))
    ]
    entries.insert(
        0,
        statement_row(doc_no="later-first", doc_date=when, balance=Decimal("1")),
    )
    result = run([line("SG-SH-00001", "100")], entries)
    assert result.metrics["vendor_closing_balance"] == Decimal("17424.75")


def test_closing_balance_ignores_undated_rows() -> None:
    result = run([line("SG-SH-00001", "100")], [])
    assert result.metrics["vendor_closing_balance"] is None
    assert result.metrics["unexplained_difference"] is None
