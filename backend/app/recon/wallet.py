"""Wallet reconciliation, shared by every category (plan-travel.md section 3.8).

Does the wallet share of an order appear in the wallet history, and for the
same amount? The join is on the portal's ``SG Order ID`` within the category
the history names in ``Wallet Type``. ``SG Order ID`` is a per-export row
sequence rather than an identity — the same booking is numbered differently in
two exports of the same data — so this module treats a collapse in the match
rate as evidence that the two files come from different generations, rather
than reporting a page of orders as missing.

A category supplies the rows to check, as ``WalletPayment``, and the function
that recognises its own ``Wallet Type``. Nothing here knows which category it
is checking.
"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from openpyxl.workbook import Workbook
from pydantic import BaseModel, ConfigDict, Field

from app.recon.cleaning import clean_text, money, to_date, to_decimal
from app.recon.excel import (
    ColumnSpec,
    SheetSchema,
    detect_sheet,
    iter_records,
    open_workbook,
)
from app.recon.exceptions import ExceptionCode, ReconException, make_exception
from app.recon.inputs import ReconInputError, Upload

# Below this share of in-window rows finding their key at all, the two exports
# are not of the same generation and every "missing" row is an artefact of the
# key rather than a real gap. Amount disagreements say nothing about this — a
# key that was found is a key that lines up.
STABLE_KEY_THRESHOLD = Decimal("0.5")
# Too few rows to tell a numbering mismatch from a handful of real gaps.
STABLE_KEY_MIN_ROWS = 5

WALLET_SCHEMA = SheetSchema(
    name="wallet history",
    fingerprint=("Wallet Type", "SG Order ID", "Amount", "Message"),
    exclude=("Partner Order ID", "Order Total"),
    columns=(
        ColumnSpec("event_id", ("ID",)),
        ColumnSpec("wallet_type", ("Wallet Type",), required=True),
        ColumnSpec("sg_order_id", ("SG Order ID",), required=True),
        ColumnSpec("amount", ("Amount",), required=True),
        ColumnSpec("message", ("Message",)),
        ColumnSpec("event_date", ("Date",), required=True),
        ColumnSpec("payment_type", ("Payment Type",)),
    ),
)

# Reads a ``Wallet Type`` cell and names the category key it belongs to, or
# None for a movement of another category.
WalletType = Callable[[Any], str | None]


class WalletState(StrEnum):
    MATCHED = "matched"
    AMOUNT_MISMATCH = "amount_mismatch"
    OUT_OF_WINDOW = "out_of_window"
    MISSING = "missing"


class WalletEvent(BaseModel):
    """One wallet-history movement, already narrowed to one category."""

    model_config = ConfigDict(frozen=True)

    source: str
    row: int
    event_id: str | None = None
    # The category key: a travel vertical, ``food`` or ``eshop``.
    vertical: str
    sg_order_id: str
    amount: Decimal
    message: str | None = None
    event_date: date | None = None
    payment_type: str | None = None


class WalletCheck(BaseModel):
    vertical: str
    sg_order_id: str
    # The order the portal row belongs to: a travel booking, a food Partner
    # Order ID or an eShop order number. Named for travel, where it began.
    booking_id: str | None = None
    order_date: date | None = None
    portal_wallet_amount: Decimal
    history_paid: Decimal | None = None
    history_refunded: Decimal | None = None
    events: int = 0
    state: WalletState
    diff: Decimal | None = None


class WalletReconciliation(BaseModel):
    window_start: date | None = None
    window_end: date | None = None
    rows_checked: int = 0
    matched: int = 0
    amount_mismatch: int = 0
    out_of_window: int = 0
    missing: int = 0
    # Wallet-paid rows with no SG Order ID: there is nothing to look them up by.
    unkeyed: int = 0
    checks: list[WalletCheck] = Field(default_factory=list)
    unmatched_history: list[WalletEvent] = Field(default_factory=list)


@dataclass(frozen=True)
class WalletPayment:
    """One portal row's wallet share, as the check needs it."""

    vertical: str
    sg_order_id: str | None
    # How the row is named in an exception: its booking or order id.
    reference: str | None
    order_date: date | None
    wallet_amount: Decimal


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

    The window is the file's own coverage, taken from every dated movement it
    holds, whatever its category. A category with few movements would
    otherwise shrink the window to its own handful of days.
    """

    window_start: date | None = None
    window_end: date | None = None
    events: int = 0
    by_key: dict[tuple[str, str], _Totals] = field(default_factory=dict)

    def note(self, when: date | None) -> None:
        if when is None:
            return
        self.window_start = (
            when if self.window_start is None else min(self.window_start, when)
        )
        self.window_end = (
            when if self.window_end is None else max(self.window_end, when)
        )

    def add(self, event: WalletEvent) -> None:
        self.events += 1
        # str(): a StrEnum vertical and its value must be one key.
        key = (str(event.vertical), event.sg_order_id)
        totals = self.by_key.setdefault(key, _Totals())
        totals.events += 1
        if totals.first is None:
            totals.first = event
        if event.amount < 0:
            totals.paid += -event.amount
            totals.payments.append(-event.amount)
        else:
            totals.refunded += event.amount
        self.note(event.event_date)

    def covers(self, when: date | None) -> bool:
        if when is None or self.window_start is None or self.window_end is None:
            return False
        return self.window_start <= when <= self.window_end


def _read(
    workbook: Workbook,
    sheet: str,
    *,
    source: str,
    max_rows: int,
    wallet_type: WalletType,
) -> Iterator[tuple[date | None, WalletEvent | None]]:
    """Every movement's date, with the event when it belongs to the category."""
    for index, record in enumerate(
        iter_records(workbook[sheet], WALLET_SCHEMA, max_rows=max_rows), start=1
    ):
        when = to_date(record["event_date"])
        vertical = wallet_type(record["wallet_type"])
        sg_order_id = clean_text(record["sg_order_id"])
        amount = to_decimal(record["amount"])
        if vertical is None or sg_order_id is None or amount is None:
            yield when, None
            continue
        yield (
            when,
            WalletEvent(
                source=source,
                row=index,
                event_id=clean_text(record.get("event_id")),
                vertical=vertical,
                sg_order_id=sg_order_id,
                amount=amount,
                message=clean_text(record.get("message")),
                event_date=when,
                payment_type=clean_text(record.get("payment_type")),
            ),
        )


def read_wallet(
    workbook: Workbook,
    sheet: str,
    *,
    source: str,
    max_rows: int,
    wallet_type: WalletType,
) -> Iterator[WalletEvent]:
    """Stream the wallet history, keeping only the category's movements.

    The file holds every wallet type the platform has; the others are skipped
    here so nothing downstream has to know they exist.
    """
    for _when, event in _read(
        workbook, sheet, source=source, max_rows=max_rows, wallet_type=wallet_type
    ):
        if event is not None:
            yield event


def load_wallet(
    upload: Upload, *, wallet_type: WalletType, max_rows: int
) -> tuple[WalletHistory, str]:
    """Read the uploaded history into one category's index.

    Returns the history and the sheet it was read from, for the manifest.
    """
    history = WalletHistory()
    with open_workbook(upload.path) as workbook:
        sheet = detect_sheet(workbook, WALLET_SCHEMA)
        if sheet is None:
            raise ReconInputError(
                f"No {WALLET_SCHEMA.name} sheet found in {upload.filename!r} "
                f"uploaded as {upload.role!r}."
            )
        for when, event in _read(
            workbook,
            sheet,
            source=upload.filename,
            max_rows=max_rows,
            wallet_type=wallet_type,
        ):
            history.note(when)
            if event is not None:
                history.add(event)
    return history, sheet


def reconcile_wallet(
    payments: Sequence[WalletPayment],
    history: WalletHistory,
    *,
    tolerance: Decimal,
    excluded: Sequence[WalletPayment] = (),
) -> tuple[WalletReconciliation, list[ReconException]]:
    """Check every wallet-paid row against the history.

    ``excluded`` are portal rows left out of the check. Their movements still
    have a portal row, so they are not reported as movements without one.
    """
    checks: list[WalletCheck] = []
    found: list[ReconException] = []
    in_window = 0
    in_window_found = 0
    unkeyed = 0

    for payment in payments:
        if payment.wallet_amount <= 0:
            continue
        if payment.sg_order_id is None:
            unkeyed += 1
            continue
        totals = history.by_key.get((str(payment.vertical), payment.sg_order_id))
        covered = history.covers(payment.order_date)
        reference = payment.reference or (
            f"{payment.vertical} order {payment.sg_order_id}"
        )
        amount = money(payment.wallet_amount)

        if totals is None:
            state = WalletState.MISSING if covered else WalletState.OUT_OF_WINDOW
            checks.append(
                WalletCheck(
                    vertical=payment.vertical,
                    sg_order_id=payment.sg_order_id,
                    booking_id=payment.reference,
                    order_date=payment.order_date,
                    portal_wallet_amount=amount,
                    state=state,
                )
            )
            if covered:
                in_window += 1
                found.append(
                    make_exception(
                        ExceptionCode.WALLET_ENTRY_MISSING,
                        order_no=reference,
                        amount=amount,
                        detail=(
                            f"{amount} taken from the wallet on "
                            f"{payment.order_date}, with no movement in the history"
                        ),
                    )
                )
            else:
                ordered = (
                    f"ordered {payment.order_date}"
                    if payment.order_date is not None
                    else "no order date"
                )
                found.append(
                    make_exception(
                        ExceptionCode.WALLET_OUT_OF_WINDOW,
                        order_no=reference,
                        amount=amount,
                        detail=(
                            f"{ordered}; the history covers "
                            f"{history.window_start} to {history.window_end}"
                        ),
                    )
                )
            continue

        totals.matched = True
        in_window += 1
        in_window_found += 1
        diff = min(
            (payment.wallet_amount - basis for basis in totals.bases()),
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
                        f"the portal records {amount} from the "
                        f"wallet; the history holds {money(totals.paid)} paid and "
                        f"{money(totals.refunded)} refunded over {totals.events} "
                        "movement(s)"
                    ),
                )
            )
        checks.append(
            WalletCheck(
                vertical=payment.vertical,
                sg_order_id=payment.sg_order_id,
                booking_id=payment.reference,
                order_date=payment.order_date,
                portal_wallet_amount=amount,
                history_paid=money(totals.paid),
                history_refunded=money(totals.refunded),
                events=totals.events,
                state=WalletState.MATCHED if matched else WalletState.AMOUNT_MISMATCH,
                diff=money(diff),
            )
        )

    set_aside = {
        (str(payment.vertical), payment.sg_order_id)
        for payment in excluded
        if payment.sg_order_id is not None
    }
    unmatched = [
        totals.first
        for key, totals in history.by_key.items()
        if not totals.matched and totals.first is not None and key not in set_aside
    ]
    for event in unmatched:
        found.append(
            make_exception(
                ExceptionCode.WALLET_HISTORY_UNMATCHED,
                order_no=f"{event.vertical} order {event.sg_order_id}",
                amount=money(event.amount),
                detail="wallet movement with no portal row in this export",
            )
        )

    if unkeyed:
        # Said once for the run: each of these rows has nothing to look up.
        found.append(
            make_exception(
                ExceptionCode.WALLET_ORDER_ID_MISSING,
                amount=None,
                detail=(
                    f"{unkeyed} wallet-paid row(s) carry no SG Order ID, so the "
                    "wallet history cannot be checked for them; re-export the "
                    "portal with SG Order ID filled in"
                ),
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
        unkeyed=unkeyed,
        checks=checks,
        unmatched_history=unmatched,
    )
    return summary, found
