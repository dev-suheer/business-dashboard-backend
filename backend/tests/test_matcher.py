from datetime import date
from decimal import Decimal

from app.recon.exceptions import ExceptionCode, Severity
from app.recon.matcher import closing_balance, match, resolve_credit_notes
from app.recon.models import (
    LedgerDocType,
    LedgerEntry,
    MatchState,
    OrderRecon,
    OrderStatus,
    VarianceClass,
)

TOLERANCE = Decimal("0.05")
PERIOD_START = date(2026, 7, 22)
PERIOD_END = date(2026, 8, 25)


def order(
    order_no: str,
    gmv: str,
    *,
    order_date: date = date(2026, 8, 1),
    status: OrderStatus = OrderStatus.DELIVERED,
) -> OrderRecon:
    return OrderRecon(
        order_no=order_no,
        order_date=order_date,
        line_count=1,
        statuses=[status],
        portal_gmv=Decimal(gmv),
        expected_commission=Decimal("0"),
        actual_commission=Decimal("0"),
        commission_variance=Decimal("0"),
        variance_class=VarianceClass.CLEAN,
        payable=Decimal(gmv),
        net_payable=Decimal(gmv),
    )


def invoice(order_no: str | None, debit: str, when: date, *, ref: str | None = None):
    return LedgerEntry(
        doc_no=f"doc-{order_no or ref}",
        doc_type=LedgerDocType.SALES_INVOICE,
        doc_date=when,
        debit=Decimal(debit),
        order_no=order_no,
        raw_ref=ref,
    )


def credit_note(ref: str, credit: str, when: date = date(2026, 8, 16)):
    return LedgerEntry(
        doc_no=f"cn-{ref}",
        doc_type=LedgerDocType.SALES_RETURN,
        doc_date=when,
        credit=Decimal(credit),
        raw_ref=ref,
    )


# --- credit-note resolution (plan.md 3.7) -----------------------------------


def test_prefix_plus_amount_resolves_the_ambiguous_reference() -> None:
    """SG-SH-0611 prefixes two orders; only the amount separates them."""
    gmv = {"SG-SH-06115": Decimal("108.00"), "SG-SH-06118": Decimal("168.00")}
    note = credit_note("SG-SH-0611", "168.00")
    assert resolve_credit_notes([note], gmv, TOLERANCE) == []
    assert note.order_no == "SG-SH-06118"


def test_the_other_amount_resolves_to_the_other_order() -> None:
    gmv = {"SG-SH-06115": Decimal("108.00"), "SG-SH-06118": Decimal("168.00")}
    note = credit_note("SG-SH-0611", "108.00")
    resolve_credit_notes([note], gmv, TOLERANCE)
    assert note.order_no == "SG-SH-06115"


def test_an_unresolvable_reference_is_reported_never_guessed() -> None:
    # Both candidates carry the same amount, so the amount cannot separate them.
    gmv = {"SG-SH-06115": Decimal("168.00"), "SG-SH-06118": Decimal("168.00")}
    note = credit_note("SG-SH-0611", "168.00")
    exceptions = resolve_credit_notes([note], gmv, TOLERANCE)
    assert [e.code for e in exceptions] == [ExceptionCode.AMBIGUOUS_CREDIT_NOTE]
    assert note.order_no is None
    assert note.match_state is MatchState.AMBIGUOUS


def test_a_unique_prefix_needs_no_amount() -> None:
    gmv = {"SG-SH-05422": Decimal("222.00")}
    note = credit_note("SG-SH-0542", "222.00")
    resolve_credit_notes([note], gmv, TOLERANCE)
    assert note.order_no == "SG-SH-05422"


def test_a_prefix_matching_nothing_is_left_alone() -> None:
    note = credit_note("SG-SH-0139", "82.00")
    assert resolve_credit_notes([note], {"SG-SH-99999": Decimal("1")}, TOLERANCE) == []
    assert note.order_no is None


# --- tolerance --------------------------------------------------------------


def test_a_one_cent_difference_is_within_tolerance() -> None:
    """The vendor rounds on their own unit-price base."""
    orders = [order("SG-SH-05785", "300.00")]
    outcome = match(
        orders,
        [invoice("SG-SH-05785", "300.01", date(2026, 8, 2))],
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        order_tolerance=TOLERANCE,
    )
    assert orders[0].amount_diff == Decimal("-0.01")
    assert orders[0].match_state is MatchState.MATCHED
    assert not [
        e for e in outcome.exceptions if e.code is ExceptionCode.AMOUNT_MISMATCH
    ]


def test_a_difference_beyond_tolerance_is_an_exception() -> None:
    outcome = match(
        [order("SG-SH-05785", "300.00")],
        [invoice("SG-SH-05785", "310.00", date(2026, 8, 2))],
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        order_tolerance=TOLERANCE,
    )
    codes = [e.code for e in outcome.exceptions]
    assert ExceptionCode.AMOUNT_MISMATCH in codes


def test_an_order_absent_from_the_statement_is_reported() -> None:
    outcome = match(
        [order("SG-SH-05785", "300.00")],
        [],
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        order_tolerance=TOLERANCE,
    )
    assert [e.code for e in outcome.exceptions] == [
        ExceptionCode.ORDER_MISSING_IN_VENDOR
    ]


# --- the three-way period split (plan.md 3.5) -------------------------------


def test_statement_orders_outside_the_period_are_informational_not_breaks() -> None:
    entries = [
        invoice("SG-SH-01397", "82.00", date(2026, 5, 18)),  # before
        invoice("SG-SH-12154", "198.00", date(2026, 9, 3)),  # after
        invoice("SG-SH-09999", "100.00", date(2026, 8, 1)),  # inside: a real break
    ]
    outcome = match(
        [],
        entries,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        order_tolerance=TOLERANCE,
    )
    by_code = {e.code: e for e in outcome.exceptions}
    assert by_code[ExceptionCode.PRIOR_PERIOD].order_no == "SG-SH-01397"
    assert by_code[ExceptionCode.LATER_PERIOD].order_no == "SG-SH-12154"
    assert by_code[ExceptionCode.MISSING_FROM_PORTAL].order_no == "SG-SH-09999"
    # Only the in-period one is a real exception.
    assert by_code[ExceptionCode.PRIOR_PERIOD].severity is Severity.INFO
    assert by_code[ExceptionCode.LATER_PERIOD].severity is Severity.INFO
    assert by_code[ExceptionCode.MISSING_FROM_PORTAL].severity is Severity.HIGH


# --- credit notes against portal status -------------------------------------


def test_an_rto_with_no_credit_note_is_flagged() -> None:
    outcome = match(
        [order("SG-SH-05422", "222.00", status=OrderStatus.RTO)],
        [invoice("SG-SH-05422", "222.00", date(2026, 8, 2))],
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        order_tolerance=TOLERANCE,
    )
    assert ExceptionCode.RTO_WITHOUT_CREDIT_NOTE in [e.code for e in outcome.exceptions]


def test_a_credit_note_with_no_reversing_status_is_flagged() -> None:
    entries = [
        invoice("SG-SH-06115", "108.00", date(2026, 8, 2)),
        credit_note("SG-SH-0611", "108.00"),
    ]
    outcome = match(
        [order("SG-SH-06115", "108.00")],
        entries,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        order_tolerance=TOLERANCE,
    )
    assert ExceptionCode.CREDIT_NOTE_WITHOUT_RTO in [e.code for e in outcome.exceptions]


# --- closing balance --------------------------------------------------------


def test_closing_balance_is_the_last_row_not_merely_the_latest_date() -> None:
    """Several rows share the closing date; the last of them is the balance."""
    when = date(2026, 9, 3)
    entries = []
    for n, balance in enumerate(("15543.80", "16011.79", "16446.78", "17424.75")):
        entry = invoice(f"SG-SH-1215{n}", "100.00", when)
        entry.balance = Decimal(balance)
        entries.append(entry)
    assert closing_balance(entries) == Decimal("17424.75")


def test_closing_balance_ignores_undated_rows() -> None:
    assert closing_balance([]) is None
