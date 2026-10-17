"""Generic matching: aggregate, join, tolerance, classify (plan.md section 3.5).

Category-independent by design. The Food category's vendor file is line-level
rather than invoice-level, but the shape of the problem — aggregate both sides,
join on an order key, compare within tolerance, classify what did not join — is
the same, so this module is meant to be reused as-is.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.recon.cleaning import money
from app.recon.exceptions import ExceptionCode, ReconException, make_exception
from app.recon.models import (
    LedgerDocType,
    LedgerEntry,
    MatchState,
    OrderRecon,
    OrderStatus,
)


@dataclass
class LedgerOrderTotals:
    """One order's side of the vendor statement."""

    order_no: str
    debit: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)
    first_date: date | None = None
    has_credit_note: bool = False

    def add(self, entry: LedgerEntry) -> None:
        if entry.debit:
            self.debit += entry.debit
        if entry.credit:
            self.credit += entry.credit
        if entry.doc_type is LedgerDocType.SALES_RETURN:
            self.has_credit_note = True
        if entry.doc_date and (
            self.first_date is None or entry.doc_date < self.first_date
        ):
            self.first_date = entry.doc_date


@dataclass
class MatchOutcome:
    ledger_totals: dict[str, LedgerOrderTotals] = field(default_factory=dict)
    unmatched: list[LedgerEntry] = field(default_factory=list)
    exceptions: list[ReconException] = field(default_factory=list)
    closing_balance: Decimal | None = None


def resolve_credit_notes(
    entries: list[LedgerEntry], order_gmv: dict[str, Decimal], tolerance: Decimal
) -> list[ReconException]:
    """Attach order numbers to credit notes (plan.md section 3.7).

    Return invoices carry no order number in the description — only the
    reference, which the vendor's system truncates to ten characters. Resolve by
    prefix-matching that reference against known order numbers, then
    disambiguating on amount: ``SG-SH-0611`` prefixes both ``SG-SH-06115``
    (108.00) and ``SG-SH-06118`` (168.00), and only the amount separates them.

    Never guesses. A reference that still leaves more than one candidate is
    reported as ambiguous.
    """
    exceptions: list[ReconException] = []
    for entry in entries:
        if entry.order_no or not entry.raw_ref:
            continue
        prefix = entry.raw_ref.upper()
        candidates = [no for no in order_gmv if no.startswith(prefix)]
        if not candidates:
            continue
        if len(candidates) > 1:
            amount = entry.credit or entry.debit or Decimal(0)
            narrowed = [
                no for no in candidates if abs(order_gmv[no] - amount) <= tolerance
            ]
            if len(narrowed) != 1:
                entry.match_state = MatchState.AMBIGUOUS
                exceptions.append(
                    make_exception(
                        ExceptionCode.AMBIGUOUS_CREDIT_NOTE,
                        amount=amount,
                        detail=(
                            f"Reference {entry.raw_ref!r} prefix-matches "
                            f"{len(candidates)} orders ({', '.join(sorted(candidates))}) "
                            f"and the amount {amount} did not separate them"
                        ),
                    )
                )
                continue
            candidates = narrowed
        entry.order_no = candidates[0]
    return exceptions


def aggregate_ledger(entries: list[LedgerEntry]) -> dict[str, LedgerOrderTotals]:
    """Sum the statement per order, ignoring rows that name no order.

    Journal entries — our commission postings and our payments — carry no order
    number by nature and are deliberately excluded here; they belong to the
    settlement bridge, not to any one order.
    """
    totals: dict[str, LedgerOrderTotals] = {}
    for entry in entries:
        if not entry.order_no:
            continue
        totals.setdefault(
            entry.order_no, LedgerOrderTotals(order_no=entry.order_no)
        ).add(entry)
    return totals


def closing_balance(entries: list[LedgerEntry]) -> Decimal | None:
    """The running balance after the statement's final transaction.

    Several rows usually share the closing date, so the latest date alone does
    not identify the closing row — the last of those rows in document order
    does. Deduplication preserves each sheet's own row order, so scanning for
    the last entry that reaches the maximum date is enough.
    """
    closing: Decimal | None = None
    latest: date | None = None
    for entry in entries:
        if entry.doc_date is None or entry.balance is None:
            continue
        if latest is None or entry.doc_date >= latest:
            latest = entry.doc_date
            closing = entry.balance
    return money(closing) if closing is not None else None


def match(
    orders: list[OrderRecon],
    entries: list[LedgerEntry],
    *,
    period_start: date,
    period_end: date,
    order_tolerance: Decimal,
) -> MatchOutcome:
    """Join the two sides and classify everything that does not agree.

    The three-way split on statement orders with no portal counterpart is what
    keeps the exception list usable: the statement's invoice date lags the portal
    order date, so it always straddles the period boundary. Without the split the
    sample data opens with 21 rows of noise.
    """
    outcome = MatchOutcome(closing_balance=closing_balance(entries))
    order_gmv = {o.order_no: o.portal_gmv for o in orders}

    outcome.exceptions.extend(resolve_credit_notes(entries, order_gmv, order_tolerance))
    totals = aggregate_ledger(entries)
    outcome.ledger_totals = totals

    by_order = {o.order_no: o for o in orders}

    for order in orders:
        entry_totals = totals.get(order.order_no)
        if entry_totals is None:
            order.match_state = MatchState.UNMATCHED
            outcome.exceptions.append(
                make_exception(
                    ExceptionCode.ORDER_MISSING_IN_VENDOR,
                    order_no=order.order_no,
                    amount=order.portal_gmv,
                    detail="Present in the portal export, absent from the vendor statement",
                )
            )
            continue

        order.match_state = MatchState.MATCHED
        order.vendor_amount = money(entry_totals.debit)
        order.amount_diff = money(order.portal_gmv - entry_totals.debit)
        if abs(order.amount_diff) > order_tolerance:
            outcome.exceptions.append(
                make_exception(
                    ExceptionCode.AMOUNT_MISMATCH,
                    order_no=order.order_no,
                    amount=order.amount_diff,
                    detail=(
                        f"Portal {order.portal_gmv} vs statement "
                        f"{money(entry_totals.debit)}"
                    ),
                )
            )

        reversing = {OrderStatus.RTO, OrderStatus.RETURN} & set(order.statuses)
        if reversing and not entry_totals.has_credit_note:
            outcome.exceptions.append(
                make_exception(
                    ExceptionCode.RTO_WITHOUT_CREDIT_NOTE,
                    order_no=order.order_no,
                    amount=order.portal_gmv,
                    detail=(
                        f"Status {'/'.join(sorted(s.value for s in reversing))} with no "
                        "reversing credit note in the statement"
                    ),
                )
            )
        if entry_totals.has_credit_note and not reversing:
            outcome.exceptions.append(
                make_exception(
                    ExceptionCode.CREDIT_NOTE_WITHOUT_RTO,
                    order_no=order.order_no,
                    amount=money(entry_totals.credit),
                    detail="Credit note in the statement with no RTO or Return in the portal",
                )
            )

    # Statement orders with no portal counterpart.
    for order_no, entry_totals in totals.items():
        if order_no in by_order:
            continue
        invoice_date = entry_totals.first_date
        amount = money(entry_totals.debit or entry_totals.credit)
        if invoice_date is None:
            code = ExceptionCode.MISSING_FROM_PORTAL
            detail = (
                "In the statement with no usable invoice date and no portal counterpart"
            )
        elif invoice_date < period_start:
            code = ExceptionCode.PRIOR_PERIOD
            detail = (
                f"Invoiced {invoice_date}, before the period opened on {period_start}"
            )
        elif invoice_date > period_end:
            code = ExceptionCode.LATER_PERIOD
            detail = f"Invoiced {invoice_date}, after the period closed on {period_end}"
        else:
            code = ExceptionCode.MISSING_FROM_PORTAL
            detail = f"Invoiced {invoice_date}, inside the period, but absent from the portal"
        outcome.exceptions.append(
            make_exception(code, order_no=order_no, amount=amount, detail=detail)
        )

    outcome.unmatched = [
        e
        for e in entries
        if not e.order_no or (e.order_no not in by_order and e.doc_no)
    ]
    for entry in outcome.unmatched:
        if entry.match_state is MatchState.UNMATCHED and entry.order_no:
            entry.match_state = _period_state(entry.doc_date, period_start, period_end)
    return outcome


def _period_state(
    doc_date: date | None, period_start: date, period_end: date
) -> MatchState:
    if doc_date is None:
        return MatchState.UNMATCHED
    if doc_date < period_start:
        return MatchState.PRIOR_PERIOD
    if doc_date > period_end:
        return MatchState.LATER_PERIOD
    return MatchState.UNMATCHED


def group_exceptions(exceptions: list[ReconException]) -> dict[str, int]:
    """Count exceptions by code, for the summary KPI block."""
    counts: dict[str, int] = defaultdict(int)
    for exc in exceptions:
        counts[str(exc.code)] += 1
    return dict(counts)
