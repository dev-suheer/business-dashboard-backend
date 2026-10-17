"""Travel business rules (plan-travel.md section 3).

Two expectations run side by side here, and keeping them apart is the whole
point of the category: what we charged the customer (the local rate, always),
and what the vendor is entitled to bill (the local rate plus VAT when the
booking is international). Their difference is money we never collected.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.recon.categories.travel.models import (
    BillingType,
    BookingMatchState,
    BookingRecon,
    BookingStatus,
    Locality,
    PaymentMethodSummary,
    PaymentMethodTotals,
    PaymentStatus,
    RefundKind,
    TravelAdjustment,
    TravelInvoiceLine,
    TravelPortalRow,
    TravelRateConfig,
    UnmatchedReason,
    Vertical,
)
from app.recon.categories.travel.parser import (
    booking_date,
    vertical_from_id,
    vertical_from_text,
)
from app.recon.cleaning import money
from app.recon.exceptions import ExceptionCode, ReconException, make_exception

BLANK_METHOD = "(blank)"
WALLET_INSTRUMENT = "Wallet"
_CHANGE_FEE_MARKER = "change fee"
# Payment states in which the customer's money reached us.
_MONEY_MOVED = frozenset(
    {PaymentStatus.PAID, PaymentStatus.COMPLETED, PaymentStatus.REFUNDED}
)


@dataclass(frozen=True)
class Window:
    """A span of booking dates, used to place a booking in time.

    Two of these matter and they are not the same span: the portal export
    covers whatever was exported, while one weekly claim covers one week. A
    booking the claim does not bill is only a break if it falls inside the
    claim's own window — otherwise it is billed on another week's claim, which
    is the normal state of affairs and must not read as an exception.
    """

    start: date
    end: date

    def covers(self, when: date | None) -> bool:
        return when is not None and self.start <= when <= self.end

    def place(self, when: date | None) -> ExceptionCode | None:
        """PRIOR_PERIOD, LATER_PERIOD, or None when the date falls inside."""
        if when is None:
            return None
        if when < self.start:
            return ExceptionCode.PRIOR_PERIOD
        if when > self.end:
            return ExceptionCode.LATER_PERIOD
        return None


def locality_of_line(line: TravelInvoiceLine) -> Locality:
    """VAT decides, never the claim's own ``DOM/INT`` label.

    The label is wrong on at least one sample row — marked international while
    carrying 15% VAT, and billed at the local rate, which is what the VAT said.
    """
    if line.vat != 0 or line.vat_percent != 0:
        return Locality.LOCAL
    return Locality.INTERNATIONAL


def locality_of_row(row: TravelPortalRow) -> Locality:
    return Locality.LOCAL if row.vat > 0 else Locality.INTERNATIONAL


@dataclass
class BookingAccumulator:
    """Both sides of one booking, accumulated unrounded.

    The engine streams into these rather than collecting rows, so peak memory
    scales with the number of *bookings*, not the number of rows.
    """

    booking_id: str

    primary: TravelPortalRow | None = None
    repeats: list[TravelPortalRow] = field(default_factory=list)

    lines: list[TravelInvoiceLine] = field(default_factory=list)
    adjustments: list[TravelAdjustment] = field(default_factory=list)
    vendor_base: Decimal = Decimal(0)
    vendor_commission: Decimal = Decimal(0)
    vendor_expected_commission: Decimal = Decimal(0)
    vendor_net: Decimal = Decimal(0)
    cancel_fee: Decimal = Decimal(0)
    change_fee: Decimal = Decimal(0)
    adjustment: Decimal = Decimal(0)
    local_base: Decimal = Decimal(0)
    international_base: Decimal = Decimal(0)

    has_invoice: bool = False
    has_credit: bool = False
    verticals: set[Vertical] = field(default_factory=set)
    localities: set[Locality] = field(default_factory=set)
    first_issue_date: date | None = None

    def add_portal(self, row: TravelPortalRow) -> None:
        """Keep the earliest row as the booking; later ones are repeats.

        A second portal row for one booking is never added to the totals: the
        vendor bills the booking once, so counting it twice would invent money.
        """
        if self.primary is None:
            self.primary = row
            return
        if _row_order(row) < _row_order(self.primary):
            self.repeats.append(self.primary)
            self.primary = row
        else:
            self.repeats.append(row)

    def add_line(self, line: TravelInvoiceLine, rates: TravelRateConfig) -> None:
        self.lines.append(line)
        locality = locality_of_line(line)
        vertical = (
            vertical_from_id(line.booking_id)
            or vertical_from_text(line.service_type)
            or Vertical.FLIGHT
        )
        self.verticals.add(vertical)
        self.localities.add(locality)

        self.vendor_base += line.total_invoice
        self.vendor_commission += line.commission
        # Per line, so a booking whose lines disagree about VAT needs no
        # special case — and a credit note reverses its own expectation.
        self.vendor_expected_commission += line.total_invoice * rates.contract_rate(
            vertical, locality
        )
        self.vendor_net += line.net_payable
        self.cancel_fee += line.airline_cancel_fee
        if line.description and _CHANGE_FEE_MARKER in line.description.casefold():
            self.change_fee += line.total_invoice
        if locality is Locality.LOCAL:
            self.local_base += line.total_invoice
        else:
            self.international_base += line.total_invoice

        if line.billing_type is BillingType.CREDIT_FOR_RETURNS or line.is_reversal_ref:
            self.has_credit = True
        else:
            self.has_invoice = True
        if line.issue_date is not None:
            self.first_issue_date = (
                line.issue_date
                if self.first_issue_date is None
                else min(self.first_issue_date, line.issue_date)
            )

    def add_adjustment(self, adjustment: TravelAdjustment) -> None:
        self.adjustments.append(adjustment)
        self.adjustment += adjustment.commission


def _row_order(row: TravelPortalRow) -> tuple[date, int, int]:
    """Sort key for competing portal rows: earliest order, then lowest id."""
    try:
        sg_order = int(row.sg_order_id) if row.sg_order_id else 0
    except ValueError:
        sg_order = 0
    return row.order_date, sg_order, row.row


def refund_kind(acc: BookingAccumulator, rates: TravelRateConfig) -> RefundKind:
    """Full, partial, or no refund — decided entirely on the vendor's side.

    The portal never shows a refund: its ``Refund Amount`` column is zero on
    every row of both sample exports. A retained airline or change fee is what
    makes a refund partial; otherwise it is full once the credit has cancelled
    the invoice out.
    """
    if not acc.has_credit:
        return RefundKind.NONE
    if acc.cancel_fee + acc.change_fee > rates.base_tolerance:
        return RefundKind.PARTIAL
    if acc.has_invoice and acc.vendor_base > rates.base_tolerance:
        return RefundKind.PARTIAL
    return RefundKind.FULL


def dominant_locality(acc: BookingAccumulator) -> Locality | None:
    """The locality that governs most of the booking's value."""
    if not acc.localities:
        return None
    if len(acc.localities) == 1:
        return next(iter(acc.localities))
    return (
        Locality.LOCAL
        if abs(acc.local_base) >= abs(acc.international_base)
        else Locality.INTERNATIONAL
    )


def build_booking(acc: BookingAccumulator, rates: TravelRateConfig) -> BookingRecon:
    """Reconcile one booking. Every reported figure is quantized once, here."""
    row = acc.primary
    vertical = (
        (row.vertical if row else None)
        or vertical_from_id(acc.booking_id)
        or (next(iter(acc.verticals)) if acc.verticals else Vertical.FLIGHT)
    )
    locality = dominant_locality(acc) or (
        locality_of_row(row) if row else Locality.INTERNATIONAL
    )

    if row is not None and acc.lines:
        match_state = BookingMatchState.MATCHED
    elif row is not None:
        match_state = BookingMatchState.PORTAL_ONLY
    else:
        match_state = BookingMatchState.VENDOR_ONLY

    portal_base = row.order_total if row else Decimal(0)
    portal_commission = row.commission_charged if row else Decimal(0)
    portal_net = row.net_payable if row else Decimal(0)

    contract_rate = rates.contract_rate(vertical, locality)
    local_rate = rates.base_rate(vertical)
    sg_expected = portal_base * local_rate
    commission_as_vendor = portal_base * contract_rate

    flags: list[str] = []
    if len(acc.lines) > 1:
        flags.append("MULTI_LEG")
    if len(acc.localities) > 1:
        flags.append("MIXED_VAT_BASIS")
    if acc.repeats:
        flags.append("REPEATS")
    if acc.adjustments:
        flags.append("ADJUSTED")

    return BookingRecon(
        booking_id=acc.booking_id,
        vertical=vertical,
        locality=locality,
        booking_date=_booking_date(acc),
        order_date=row.order_date if row else None,
        match_state=match_state,
        refund=refund_kind(acc, rates),
        portal_rows=(1 if row else 0) + len(acc.repeats),
        portal_base=money(portal_base),
        portal_commission=money(portal_commission),
        portal_net_payable=money(portal_net),
        portal_status=row.status if row else None,
        portal_payment_status=row.payment_status if row else None,
        payment_method=row.payment_method if row else None,
        wallet_amount=money(row.wallet_amount if row else Decimal(0)),
        card_amount=money(row.card_amount if row else Decimal(0)),
        vendor_lines=len(acc.lines),
        vendor_invoice_nos=sorted(
            {line.invoice_no for line in acc.lines if line.invoice_no}
        ),
        vendor_base=money(acc.vendor_base),
        vendor_commission=money(acc.vendor_commission),
        vendor_net_payable=money(acc.vendor_net),
        vendor_cancel_fee=money(acc.cancel_fee),
        vendor_change_fee=money(acc.change_fee),
        vendor_adjustment=money(acc.adjustment),
        # Rates are ratios, not money: they are compared against the rate
        # tolerance, so quantizing them to 2dp would erase the comparison.
        sg_rate=(portal_commission / portal_base) if portal_base else None,
        vendor_rate=(
            (acc.vendor_commission / acc.vendor_base) if acc.vendor_base else None
        ),
        contract_rate=contract_rate,
        sg_expected_commission=money(sg_expected),
        vendor_expected_commission=money(acc.vendor_expected_commission),
        base_diff=money(portal_base - acc.vendor_base),
        commission_diff=money(portal_commission - acc.vendor_commission),
        net_payable_diff=money(portal_net - acc.vendor_net),
        commission_as_vendor=money(commission_as_vendor),
        net_payable_as_vendor=money(portal_base - commission_as_vendor),
        # Only meaningful where both sides exist: against a claim that never
        # billed the booking, "the difference at the contract rate" is just the
        # whole commission again.
        commission_diff_as_vendor=money(
            commission_as_vendor - acc.vendor_commission if acc.lines else Decimal(0)
        ),
        net_payable_diff_as_vendor=money(
            (portal_base - commission_as_vendor) - acc.vendor_net
            if acc.lines
            else Decimal(0)
        ),
        vat_uplift_shortfall=money(commission_as_vendor - sg_expected),
        flags=flags,
    )


def _booking_date(acc: BookingAccumulator) -> date | None:
    """The booking's own date, from the id, falling back to the portal row.

    The id is authoritative: the claim's ``Booking Date`` column holds the date
    of the *credit* on every credit note in the sample.
    """
    return booking_date(acc.booking_id) or (
        acc.primary.order_date if acc.primary else acc.first_issue_date
    )


def explain(
    booking: BookingRecon, *, export: Window, claim: Window | None
) -> tuple[UnmatchedReason | None, str]:
    """Why a booking is kept out of the matched output; ``None`` when it matched.

    A booking matches when both sides carry it, nothing reversed it, and the
    portal says Processed. Reasons are taken in order of what decides them: a
    refund or a cancellation leaves the two sides apart on purpose, so it
    explains a booking before a missing side does.
    """
    status = booking.portal_status
    shown = status.value if status else "unknown"
    when = booking.booking_date

    if booking.refund is not RefundKind.NONE:
        parts = [f"{booking.refund.value} refund credited on the claim"]
        fees = booking.vendor_cancel_fee + booking.vendor_change_fee
        if fees:
            parts.append(f"{fees} in fees retained")
        if booking.vendor_commission:
            parts.append(f"vendor commission {booking.vendor_commission} still billed")
        if booking.portal_rows:
            parts.append(f"the portal shows {shown}")
        else:
            parts.append("no portal row" + _placed(export, when, "the portal export"))
        return UnmatchedReason.REFUNDED, "; ".join(parts)

    if status is BookingStatus.CANCELLED:
        if booking.match_state is BookingMatchState.MATCHED:
            return (
                UnmatchedReason.CANCELLED,
                "cancelled on the portal, but the claim bills it with no credit note",
            )
        return UnmatchedReason.CANCELLED, "cancelled on the portal; not on the claim"

    if booking.match_state is BookingMatchState.PORTAL_ONLY:
        if status is not BookingStatus.PROCESSED:
            return UnmatchedReason.OTHER, f"{shown} on the portal; not on the claim"
        if claim is None:
            return UnmatchedReason.MISSING_IN_VENDOR, "not billed on the claim"
        return (
            UnmatchedReason.MISSING_IN_VENDOR,
            "not billed on the claim"
            + _placed(
                claim,
                when,
                "this claim's week",
                outside=", so expected on another claim",
            ),
        )

    if booking.match_state is BookingMatchState.VENDOR_ONLY:
        lead = (
            "billed on the claim"
            if booking.vendor_lines
            else f"adjusted by {booking.vendor_adjustment} on the claim"
        )
        return (
            UnmatchedReason.MISSING_IN_PORTAL,
            f"{lead}, no portal row" + _placed(export, when, "the portal export"),
        )

    if status is not BookingStatus.PROCESSED:
        return (
            UnmatchedReason.OTHER,
            f"billed on the claim while the portal shows {shown}",
        )
    return None, ""


def _placed(window: Window, when: date | None, name: str, *, outside: str = "") -> str:
    """Where a booking date falls against a window, as a trailing clause."""
    if when is None:
        return "; booking date unknown"
    span = f"{name} ({window.start} to {window.end})"
    placed = window.place(when)
    if placed is ExceptionCode.PRIOR_PERIOD:
        return f"; booked {when}, before {span}{outside}"
    if placed is ExceptionCode.LATER_PERIOD:
        return f"; booked {when}, after {span}{outside}"
    return f"; booked {when}, inside {span}"


def row_exceptions(
    row: TravelPortalRow, rates: TravelRateConfig
) -> list[ReconException]:
    """Checks that belong to a portal row whether or not it reached the vendor."""
    found: list[ReconException] = []
    reference = row.booking_id or f"{row.vertical.value} order {row.sg_order_id}"

    split = row.wallet_amount + row.card_amount
    if abs(split - row.order_total) > rates.base_tolerance:
        found.append(
            make_exception(
                ExceptionCode.PAYMENT_SPLIT_MISMATCH,
                order_no=reference,
                amount=money(row.order_total - split),
                detail=(
                    f"{row.payment_method or 'no method'}: wallet "
                    f"{money(row.wallet_amount)} + card {money(row.card_amount)} "
                    f"does not make the order total {money(row.order_total)}"
                ),
            )
        )

    # The portal computes a notional commission on every row, so the status
    # alone says nothing. It is only a finding where money actually moved.
    if (
        row.status in {BookingStatus.EXPIRED, BookingStatus.PENDING}
        and row.payment_status in _MONEY_MOVED
        and row.commission_charged > rates.commission_tolerance
    ):
        found.append(
            make_exception(
                ExceptionCode.COMMISSION_ON_UNCOMPLETED_ORDER,
                order_no=reference,
                amount=money(row.commission_charged),
                detail=(
                    f"paid for ({row.payment_status.value}) but {row.status.value}, "
                    "and commission was still taken"
                ),
            )
        )
    return found


def booking_exceptions(
    booking: BookingRecon,
    acc: BookingAccumulator,
    rates: TravelRateConfig,
    *,
    export: Window,
    claim: Window | None,
) -> list[ReconException]:
    """Everything that can be wrong with one booking."""
    found: list[ReconException] = []
    ref = booking.booking_id
    on_this_claim = claim is None or claim.covers(booking.booking_date)

    if booking.match_state is BookingMatchState.PORTAL_ONLY:
        found.append(_portal_only_exception(booking, claim))
    elif booking.match_state is BookingMatchState.VENDOR_ONLY:
        found.append(_vendor_only_exception(booking, export))
    elif booking.refund is RefundKind.NONE and (
        abs(booking.base_diff) > rates.base_tolerance
    ):
        found.append(
            make_exception(
                ExceptionCode.BOOKING_BASE_MISMATCH,
                order_no=ref,
                amount=booking.base_diff,
                detail=(
                    f"we booked {booking.portal_base}, they billed "
                    f"{booking.vendor_base}"
                ),
            )
        )

    found.extend(_refund_exceptions(booking, acc, rates, on_this_claim=on_this_claim))
    if booking.refund is RefundKind.NONE:
        found.extend(_commission_exceptions(booking, acc, rates))
    found.extend(_consistency_exceptions(booking, acc))
    return found


def _portal_only_exception(
    booking: BookingRecon, claim: Window | None
) -> ReconException:
    """A booking this claim does not bill, placed against the claim's week.

    Outside that week it is billed on another claim, which is expected and
    informational; inside it, the vendor has not billed us for something we
    sold, which is not.
    """
    status = booking.portal_status.value if booking.portal_status else "booked"
    detail = f"{status} on the portal, not billed on this claim"
    placed = claim.place(booking.booking_date) if claim else None
    if placed is not None:
        return make_exception(
            placed,
            order_no=booking.booking_id,
            amount=booking.portal_base,
            detail=f"{detail} (booked {booking.booking_date}, outside its week)",
        )
    return make_exception(
        ExceptionCode.BOOKING_MISSING_IN_VENDOR,
        order_no=booking.booking_id,
        amount=booking.portal_base,
        detail=detail,
    )


def _vendor_only_exception(booking: BookingRecon, export: Window) -> ReconException:
    """A billed booking we have no portal row for, placed in time.

    A credit note for a booking older than the export is the common case and is
    not a break; only a booking *inside* the window is.
    """
    when = booking.booking_date
    detail = f"billed on the claim, no portal row (booked {when or 'unknown'})"
    placed = export.place(when)
    return make_exception(
        placed or ExceptionCode.BOOKING_MISSING_IN_PORTAL,
        order_no=booking.booking_id,
        amount=booking.vendor_base,
        detail=detail,
    )


def _refund_exceptions(
    booking: BookingRecon,
    acc: BookingAccumulator,
    rates: TravelRateConfig,
    *,
    on_this_claim: bool,
) -> list[ReconException]:
    found: list[ReconException] = []
    ref = booking.booking_id

    if booking.refund is RefundKind.PARTIAL:
        found.append(
            make_exception(
                ExceptionCode.PARTIAL_REFUND,
                order_no=ref,
                amount=money(acc.cancel_fee + acc.change_fee),
                detail=(
                    f"refunded with {money(acc.cancel_fee)} cancellation fee and "
                    f"{money(acc.change_fee)} change fee retained"
                ),
            )
        )

    if booking.refund is not RefundKind.NONE:
        if booking.portal_status is BookingStatus.PROCESSED:
            found.append(
                make_exception(
                    ExceptionCode.REFUND_WITHOUT_PORTAL_CANCELLATION,
                    order_no=ref,
                    amount=booking.vendor_base,
                    detail="credited on the claim while the portal still says Processed",
                )
            )
        # The vendor's own netting: only checkable when both sides of the
        # reversal are on this claim.
        if (
            acc.has_invoice
            and acc.has_credit
            and abs(acc.vendor_commission) > rates.commission_tolerance
        ):
            found.append(
                make_exception(
                    ExceptionCode.COMMISSION_NOT_REVERSED_ON_REFUND,
                    order_no=ref,
                    amount=booking.vendor_commission,
                    detail="the credit note leaves the vendor's commission standing",
                )
            )
        if booking.portal_commission > rates.commission_tolerance:
            found.append(
                make_exception(
                    ExceptionCode.COMMISSION_NOT_REVERSED_ON_REFUND,
                    order_no=ref,
                    amount=booking.portal_commission,
                    detail="the portal still retains its commission on a refunded booking",
                )
            )
    elif booking.portal_status is BookingStatus.CANCELLED and on_this_claim:
        found.append(
            make_exception(
                ExceptionCode.CANCELLATION_WITHOUT_VENDOR_CREDIT,
                order_no=ref,
                amount=booking.portal_base,
                detail="cancelled on the portal with no credit note on the claim",
            )
        )
    return found


def _commission_exceptions(
    booking: BookingRecon, acc: BookingAccumulator, rates: TravelRateConfig
) -> list[ReconException]:
    found: list[ReconException] = []
    ref = booking.booking_id

    if acc.lines:
        vendor_variance = acc.vendor_commission - acc.vendor_expected_commission
        if vendor_variance > rates.commission_tolerance:
            found.append(
                make_exception(
                    ExceptionCode.VENDOR_COMMISSION_OVERCHARGE,
                    order_no=ref,
                    amount=money(vendor_variance),
                    detail=(
                        f"billed {booking.vendor_commission} where "
                        f"{booking.vendor_expected_commission} is contractual "
                        f"({_pct(booking.vendor_rate)} against "
                        f"{_pct(booking.contract_rate)})"
                    ),
                )
            )
        elif vendor_variance < -rates.commission_tolerance:
            found.append(
                make_exception(
                    ExceptionCode.VENDOR_COMMISSION_UNDERCHARGE,
                    order_no=ref,
                    amount=money(vendor_variance),
                    detail=(
                        f"billed {booking.vendor_commission} where "
                        f"{booking.vendor_expected_commission} is contractual"
                    ),
                )
            )

    if booking.portal_base:
        sg_variance = booking.portal_commission - booking.sg_expected_commission
        if abs(sg_variance) > rates.commission_tolerance:
            found.append(
                make_exception(
                    ExceptionCode.SG_COMMISSION_RATE_UNEXPECTED,
                    order_no=ref,
                    amount=money(sg_variance),
                    detail=(
                        f"we charged {booking.portal_commission} where our own "
                        f"{_pct(rates.base_rate(booking.vertical))} rate makes "
                        f"{booking.sg_expected_commission}"
                    ),
                )
            )
    return found


def _consistency_exceptions(
    booking: BookingRecon, acc: BookingAccumulator
) -> list[ReconException]:
    found: list[ReconException] = []
    ref = booking.booking_id

    if len(acc.lines) > 1:
        found.append(
            make_exception(
                ExceptionCode.MULTI_LEG_BOOKING,
                order_no=ref,
                amount=booking.vendor_base,
                detail=f"{len(acc.lines)} invoice lines compared as one booking",
            )
        )

    for row in acc.repeats:
        found.append(
            make_exception(
                ExceptionCode.REPEAT_BOOKING_ROW,
                order_no=ref,
                amount=money(row.order_total),
                detail=(
                    f"portal row {row.row} of {row.sheet!r} repeats this booking; "
                    "excluded from the totals"
                ),
            )
        )

    verticals = set(acc.verticals)
    if acc.primary is not None:
        verticals.add(acc.primary.vertical)
    id_vertical = vertical_from_id(acc.booking_id)
    if id_vertical is not None:
        verticals.add(id_vertical)
    if len(verticals) > 1:
        found.append(
            make_exception(
                ExceptionCode.VERTICAL_CONFLICT,
                order_no=ref,
                detail=(
                    "the booking id, the portal and the claim disagree: "
                    + ", ".join(sorted(v.value for v in verticals))
                ),
            )
        )

    if acc.primary is not None and acc.lines:
        portal_locality = locality_of_row(acc.primary)
        if len(acc.localities) > 1:
            found.append(
                make_exception(
                    ExceptionCode.VAT_BASIS_CONFLICT,
                    order_no=ref,
                    detail="the claim's own lines disagree about whether VAT applies",
                )
            )
        elif portal_locality is not booking.locality:
            found.append(
                make_exception(
                    ExceptionCode.VAT_BASIS_CONFLICT,
                    order_no=ref,
                    detail=(
                        f"the portal reads {portal_locality.value} and the claim "
                        f"{booking.locality.value}"
                    ),
                )
            )
    return found


def _pct(rate: Decimal | None) -> str:
    return "—" if rate is None else f"{rate * 100:.4f}%"


@dataclass
class _MethodTotals:
    rows: int = 0
    bookings: int = 0
    order_total: Decimal = Decimal(0)
    wallet_amount: Decimal = Decimal(0)
    card_amount: Decimal = Decimal(0)


@dataclass
class PaymentMethodAccumulator:
    """Two cuts of the same rows (plan-travel.md 3.7)."""

    by_label: dict[str, _MethodTotals] = field(default_factory=dict)
    by_instrument: dict[str, _MethodTotals] = field(default_factory=dict)

    def add(self, row: TravelPortalRow) -> None:
        label = row.payment_method or BLANK_METHOD
        booked = 1 if row.booking_id else 0

        totals = self.by_label.setdefault(label, _MethodTotals())
        totals.rows += 1
        totals.bookings += booked
        totals.order_total += row.order_total
        totals.wallet_amount += row.wallet_amount
        totals.card_amount += row.card_amount

        for part in (p.strip() for p in label.split("+")):
            if not part:
                continue
            instrument = self.by_instrument.setdefault(part, _MethodTotals())
            # A combined payment is split at the amount that actually moved on
            # each instrument, so the column still totals the order value.
            moved = row.wallet_amount if part == WALLET_INSTRUMENT else row.card_amount
            instrument.rows += 1
            instrument.bookings += booked
            instrument.order_total += moved
            if part == WALLET_INSTRUMENT:
                instrument.wallet_amount += row.wallet_amount
            else:
                instrument.card_amount += row.card_amount

    def build(self) -> PaymentMethodSummary:
        return PaymentMethodSummary(
            by_label=_method_rows(self.by_label),
            by_instrument=_method_rows(self.by_instrument),
        )


def _method_rows(source: dict[str, _MethodTotals]) -> list[PaymentMethodTotals]:
    return [
        PaymentMethodTotals(
            label=label,
            rows=totals.rows,
            bookings=totals.bookings,
            order_total=money(totals.order_total),
            wallet_amount=money(totals.wallet_amount),
            card_amount=money(totals.card_amount),
        )
        for label, totals in sorted(
            source.items(), key=lambda item: (-item[1].order_total, item[0])
        )
    ]
