"""Wallet reconciliation (plan-travel.md section 3.8).

Does the wallet share of a booking appear in the wallet history, and for the
same amount? The join is on the portal's ``SG Order ID``, which is a per-export
row sequence rather than an identity — the same booking is numbered differently
in two exports of the same data — so this module treats a collapse in the match
rate as evidence that the two files come from different generations, rather
than reporting a page of bookings as missing.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.recon.categories.travel.models import (
    TravelPortalRow,
    Vertical,
    WalletCheck,
    WalletEvent,
    WalletReconciliation,
    WalletState,
)
from app.recon.cleaning import money
from app.recon.exceptions import ExceptionCode, ReconException, make_exception

# Below this share of in-window rows finding their key at all, the two exports
# are not of the same generation and every "missing" row is an artefact of the
# key rather than a real gap. Amount disagreements say nothing about this — a
# key that was found is a key that lines up.
STABLE_KEY_THRESHOLD = Decimal("0.5")
# Too few rows to tell a numbering mismatch from a handful of real gaps.
STABLE_KEY_MIN_ROWS = 5


@dataclass
class _Totals:
    paid: Decimal = Decimal(0)
    refunded: Decimal = Decimal(0)
    events: int = 0
    matched: bool = False
    first: WalletEvent | None = None
    payments: list[Decimal] = field(default_factory=list)

    def bases(self) -> list[Decimal]:
        """The amounts a portal row could legitimately equal.

        One booking's wallet share can appear three ways, all seen in the
        sample: as a single payment, as several payments that sum to it, or —
        where the customer was charged and refunded repeatedly before the
        booking stuck — as what is left once the refunds are taken off.
        """
        return [*self.payments, self.paid, self.paid - self.refunded]


@dataclass
class WalletHistory:
    """The history, indexed by the key the portal can be joined on.

    Aggregated rather than paired one-to-one: a single booking in the sample
    carries seven payment/refund pairs from a retry loop, and a refunded
    booking carries its payment and its refund under the same key.
    """

    window_start: date | None = None
    window_end: date | None = None
    events: int = 0
    by_key: dict[tuple[Vertical, str], _Totals] = field(default_factory=dict)

    def add(self, event: WalletEvent) -> None:
        self.events += 1
        totals = self.by_key.setdefault((event.vertical, event.sg_order_id), _Totals())
        totals.events += 1
        if totals.first is None:
            totals.first = event
        if event.amount < 0:
            totals.paid += -event.amount
            totals.payments.append(-event.amount)
        else:
            totals.refunded += event.amount
        if event.event_date is not None:
            self.window_start = (
                event.event_date
                if self.window_start is None
                else min(self.window_start, event.event_date)
            )
            self.window_end = (
                event.event_date
                if self.window_end is None
                else max(self.window_end, event.event_date)
            )

    def covers(self, when: date) -> bool:
        if self.window_start is None or self.window_end is None:
            return False
        return self.window_start <= when <= self.window_end


def reconcile_wallet(
    rows: list[TravelPortalRow],
    history: WalletHistory,
    *,
    tolerance: Decimal,
    excluded: list[TravelPortalRow] | None = None,
) -> tuple[WalletReconciliation, list[ReconException]]:
    """Check every wallet-paid row against the history.

    ``excluded`` are portal rows left out of the check (they carry no booking
    id). Their movements still have a portal row, so they are not reported as
    movements without one.
    """
    checks: list[WalletCheck] = []
    found: list[ReconException] = []
    in_window = 0
    in_window_found = 0

    for row in rows:
        if row.wallet_amount <= 0 or row.sg_order_id is None:
            continue
        key = (row.vertical, row.sg_order_id)
        totals = history.by_key.get(key)
        covered = history.covers(row.order_date)
        reference = row.booking_id or f"{row.vertical.value} order {row.sg_order_id}"

        if totals is None:
            state = WalletState.MISSING if covered else WalletState.OUT_OF_WINDOW
            checks.append(
                WalletCheck(
                    vertical=row.vertical,
                    sg_order_id=row.sg_order_id,
                    booking_id=row.booking_id,
                    order_date=row.order_date,
                    portal_wallet_amount=money(row.wallet_amount),
                    state=state,
                )
            )
            if covered:
                in_window += 1
                found.append(
                    make_exception(
                        ExceptionCode.WALLET_ENTRY_MISSING,
                        order_no=reference,
                        amount=money(row.wallet_amount),
                        detail=(
                            f"{money(row.wallet_amount)} taken from the wallet on "
                            f"{row.order_date}, with no movement in the history"
                        ),
                    )
                )
            else:
                found.append(
                    make_exception(
                        ExceptionCode.WALLET_OUT_OF_WINDOW,
                        order_no=reference,
                        amount=money(row.wallet_amount),
                        detail=(
                            f"ordered {row.order_date}; the history covers "
                            f"{history.window_start} to {history.window_end}"
                        ),
                    )
                )
            continue

        totals.matched = True
        in_window += 1
        in_window_found += 1
        diff = min(
            (row.wallet_amount - basis for basis in totals.bases()),
            key=abs,
        )
        matched = abs(diff) <= tolerance
        if not matched:
            found.append(
                make_exception(
                    ExceptionCode.WALLET_AMOUNT_MISMATCH,
                    order_no=reference,
                    amount=money(diff),
                    detail=(
                        f"the portal records {money(row.wallet_amount)} from the "
                        f"wallet; the history holds {money(totals.paid)} paid and "
                        f"{money(totals.refunded)} refunded over {totals.events} "
                        "movement(s)"
                    ),
                )
            )
        checks.append(
            WalletCheck(
                vertical=row.vertical,
                sg_order_id=row.sg_order_id,
                booking_id=row.booking_id,
                order_date=row.order_date,
                portal_wallet_amount=money(row.wallet_amount),
                history_paid=money(totals.paid),
                history_refunded=money(totals.refunded),
                events=totals.events,
                state=WalletState.MATCHED if matched else WalletState.AMOUNT_MISMATCH,
                diff=money(diff),
            )
        )

    set_aside = {(row.vertical, row.sg_order_id) for row in excluded or ()}
    unmatched = [
        totals.first
        for key, totals in history.by_key.items()
        if not totals.matched and totals.first is not None and key not in set_aside
    ]
    for event in unmatched:
        found.append(
            make_exception(
                ExceptionCode.WALLET_HISTORY_UNMATCHED,
                order_no=f"{event.vertical.value} order {event.sg_order_id}",
                amount=money(event.amount),
                detail="wallet movement with no portal row in this export",
            )
        )

    if (
        in_window >= STABLE_KEY_MIN_ROWS
        and Decimal(in_window_found) / Decimal(in_window) < STABLE_KEY_THRESHOLD
    ):
        # Said once for the run: the alternative is every row of the export
        # reported as missing because the two files number their orders apart.
        found.append(
            make_exception(
                ExceptionCode.WALLET_KEY_UNSTABLE,
                amount=None,
                detail=(
                    f"only {in_window_found} of {in_window} in-window wallet rows "
                    "were found at all. SG Order ID is a per-export sequence; "
                    "re-export the wallet history and the portal together"
                ),
            )
        )

    summary = WalletReconciliation(
        window_start=history.window_start,
        window_end=history.window_end,
        rows_checked=len(checks),
        matched=sum(1 for c in checks if c.state is WalletState.MATCHED),
        amount_mismatch=sum(
            1 for c in checks if c.state is WalletState.AMOUNT_MISMATCH
        ),
        out_of_window=sum(1 for c in checks if c.state is WalletState.OUT_OF_WINDOW),
        missing=sum(1 for c in checks if c.state is WalletState.MISSING),
        checks=checks,
        unmatched_history=unmatched,
    )
    return summary, found
