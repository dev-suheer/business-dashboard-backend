"""The travel reconciliation pipeline (plan-travel.md section 5).

Read the claim, read the portal, group both sides on the booking id, judge each
booking once, then summarise. Every stage streams: the portal export is the
largest input and is reduced to per-booking state as it is read. Rows with no
booking id are kept whole for the references but reach no calculation.

Only matched bookings feed the totals. Every other source row is carried in the
result with the reason it did not match, so nothing is lost.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.recon.categories.travel import parser, rules
from app.recon.categories.travel.models import (
    BillingType,
    BookingMatchState,
    BookingRecon,
    ClaimPeriodTotals,
    Locality,
    PaymentMethodSummary,
    RefundKind,
    TravelAdjustment,
    TravelInvoiceLine,
    TravelPortalRow,
    TravelRateConfig,
    TravelReferences,
    TravelResult,
    TravelSkippedRow,
    TravelSummary,
    UnmatchedReason,
    Vertical,
    WalletReconciliation,
)
from app.recon.categories.travel.rules import BookingAccumulator, Window
from app.recon.categories.travel.wallet import reconcile_wallet
from app.recon.cleaning import money
from app.recon.excel import detect_sheet, detect_sheets, open_workbook
from app.recon.exceptions import (
    ExceptionCode,
    ReconException,
    make_exception,
    sort_exceptions,
)
from app.recon.inputs import ReconInputError, Upload
from app.recon.matcher import group_exceptions
from app.recon.models import SheetManifest
from app.recon.wallet import load_wallet

logger = logging.getLogger(__name__)

VendorLineKey = tuple[str, str, str, str, str]


@dataclass(frozen=True)
class TravelRequest:
    portal: Upload
    vendor: Upload
    wallet: Upload | None
    rates: TravelRateConfig
    period_start: date | None
    period_end: date | None
    max_rows: int


@dataclass
class _Vendor:
    accumulators: dict[str, BookingAccumulator]
    lines: list[TravelInvoiceLine]
    adjustments: list[TravelAdjustment]
    claim_periods: list[ClaimPeriodTotals]
    exceptions: list[ReconException]
    # The week this claim covers, taken from the booking dates it bills.
    window: Window | None
    # Invoiced rows with no booking id: excluded, but kept to be traced.
    skipped: list[TravelSkippedRow]


@dataclass
class _Portal:
    rows_read: int
    flagged: list[TravelPortalRow]
    # Rows with no booking id: excluded, but kept to be traced.
    unbooked: list[TravelPortalRow]
    wallet_rows: list[TravelPortalRow]
    payment_methods: PaymentMethodSummary
    first_date: date | None
    last_date: date | None
    provider: str | None
    exceptions: list[ReconException]


def run(request: TravelRequest) -> TravelResult:
    manifest: list[SheetManifest] = []

    vendor = _load_vendor(request, manifest)
    portal = _load_portal(request, vendor.accumulators, manifest)

    period_start = request.period_start or portal.first_date
    period_end = request.period_end or portal.last_date
    if period_start is None or period_end is None:
        raise ReconInputError("The portal export carried no dated travel rows")
    if period_start > period_end:
        raise ReconInputError("period_start is after period_end")

    export = Window(start=period_start, end=period_end)
    exceptions = vendor.exceptions + portal.exceptions
    exceptions += _link_adjustments(vendor.adjustments, vendor.accumulators)

    bookings: list[BookingRecon] = []
    for acc in vendor.accumulators.values():
        booking = rules.build_booking(acc, request.rates)
        booking.unmatched_reason, booking.explanation = rules.explain(
            booking, export=export, claim=vendor.window
        )
        bookings.append(booking)
        exceptions += rules.booking_exceptions(
            booking,
            acc,
            request.rates,
            export=export,
            claim=vendor.window,
        )
    bookings.sort(key=lambda b: (b.booking_date or date.min, b.booking_id))

    wallet: WalletReconciliation | None = None
    if request.wallet is not None:
        wallet, wallet_exceptions = _load_wallet(
            request.wallet, request, portal, manifest
        )
        exceptions += wallet_exceptions

    exceptions += _shortfall_exception(
        [b for b in bookings if b.unmatched_reason is None]
    )
    summary = _build_summary(
        bookings=bookings,
        accumulators=vendor.accumulators,
        vendor_lines=len(vendor.lines),
        portal_rows=portal.rows_read,
        unbooked_portal_rows=len(portal.unbooked),
        unbooked_vendor_rows=len(vendor.skipped),
        rates=request.rates,
        wallet=wallet,
        exceptions=exceptions,
    )

    return TravelResult(
        vendor_name=portal.provider,
        period_start=period_start,
        period_end=period_end,
        rates=request.rates,
        bookings=bookings,
        invoice_lines=vendor.lines,
        portal_rows_flagged=portal.flagged,
        adjustments=vendor.adjustments,
        claim_periods=vendor.claim_periods,
        references=TravelReferences(
            portal_rows=_reference_rows(bookings, vendor.accumulators, portal.unbooked),
            vendor_rows_without_booking_id=vendor.skipped,
        ),
        payment_methods=portal.payment_methods,
        wallet=wallet,
        exceptions=sort_exceptions(exceptions),
        detected=manifest,
        summary=summary,
    )


def _load_vendor(request: TravelRequest, manifest: list[SheetManifest]) -> _Vendor:
    """Read the claim into per-booking accumulators.

    Every claim-shaped sheet in the workbook is read, so a file holding several
    weeks reconciles in one run. Lines are deduplicated on their invoice
    identity *and* their amounts: uploading the same claim twice must not double
    what we owe.
    """
    upload = request.vendor
    accumulators: dict[str, BookingAccumulator] = {}
    lines: list[TravelInvoiceLine] = []
    adjustments: list[TravelAdjustment] = []
    claim_periods: list[ClaimPeriodTotals] = []
    exceptions: list[ReconException] = []
    skipped: list[TravelSkippedRow] = []
    seen: set[VendorLineKey] = set()

    with open_workbook(upload.path) as workbook:
        sheets = detect_sheets(workbook, parser.VENDOR_SCHEMA)
        if not sheets:
            raise ReconInputError(
                f"No {parser.VENDOR_SCHEMA.name} sheet found in {upload.filename!r} "
                f"uploaded as {upload.role!r}."
            )
        for row in parser.read_vendor(
            workbook, sheets, source=upload.filename, max_rows=request.max_rows
        ):
            if isinstance(row, TravelInvoiceLine):
                key = _line_key(row)
                if key in seen:
                    exceptions.append(
                        make_exception(
                            ExceptionCode.DUPLICATE_VENDOR_LINE,
                            order_no=row.booking_id,
                            amount=row.total_invoice,
                            detail=(
                                f"invoice {row.invoice_no} line {row.line_item} "
                                "appears twice in the upload"
                            ),
                        )
                    )
                    continue
                seen.add(key)
                lines.append(row)
                acc = accumulators.setdefault(
                    row.booking_id, BookingAccumulator(booking_id=row.booking_id)
                )
                acc.add_line(row, request.rates)
            elif isinstance(row, TravelAdjustment):
                adjustments.append(row)
            elif row.reason != "totals or spacer row":
                exceptions.append(_skipped_exception(row))
                skipped.append(row)
        _note(manifest, upload, sheets, len(lines))

        claim_sheet = detect_sheet(workbook, parser.CLAIM_SCHEMA)
        if claim_sheet is not None:
            claim_periods = list(
                parser.read_claim_periods(
                    workbook,
                    claim_sheet,
                    source=upload.filename,
                    max_rows=request.max_rows,
                )
            )
            # Its own role, so the manifest cannot be read as the claim
            # having been detected twice.
            _note(
                manifest,
                upload,
                [claim_sheet],
                len(claim_periods),
                role="vendor-ledger",
            )

    return _Vendor(
        accumulators,
        lines,
        adjustments,
        claim_periods,
        exceptions,
        _claim_window(lines),
        skipped,
    )


def _claim_window(lines: list[TravelInvoiceLine]) -> Window | None:
    """The span of booking dates this claim bills.

    Credit notes are excluded: a claim reverses bookings of any age, so
    including them would stretch the week back over months and make every
    portal booking in between look unbilled. Dates come from the booking ids
    rather than the ``Booking Date`` column, which holds the date of the credit
    on every credit note in the sample.
    """
    dates = [
        when
        for line in lines
        if line.billing_type is not BillingType.CREDIT_FOR_RETURNS
        and not line.is_reversal_ref
        and (when := parser.booking_date(line.booking_id)) is not None
    ]
    return Window(start=min(dates), end=max(dates)) if dates else None


def _line_key(line: TravelInvoiceLine) -> VendorLineKey:
    """The identity that makes a re-uploaded claim file a no-op.

    Amounts are quantized into the key for the same reason the eshop ledger
    does it: one sheet writes 82.00 as text and another as the number 82.
    """
    return (
        line.invoice_no or "",
        line.line_item or "",
        line.booking_ref,
        str(money(line.total_invoice)),
        str(money(line.commission)),
    )


def _skipped_exception(row: TravelSkippedRow) -> ReconException:
    return make_exception(
        ExceptionCode.UNPARSEABLE_VENDOR_ROW,
        amount=None,
        detail=(
            f"{row.sheet!r} row {row.row}: {row.reason}"
            + (f" ({row.text})" if row.text else "")
        ),
    )


def _load_portal(
    request: TravelRequest,
    accumulators: dict[str, BookingAccumulator],
    manifest: list[SheetManifest],
) -> _Portal:
    """Stream the portal export into the same accumulators the claim filled."""
    upload = request.portal
    methods = rules.PaymentMethodAccumulator()
    flagged: list[TravelPortalRow] = []
    unbooked: list[TravelPortalRow] = []
    wallet_rows: list[TravelPortalRow] = []
    exceptions: list[ReconException] = []
    rows_read = 0
    first_date: date | None = None
    last_date: date | None = None
    provider: str | None = None

    with open_workbook(upload.path) as workbook:
        sheets = detect_sheets(workbook, parser.PORTAL_SCHEMA)
        if not sheets:
            raise ReconInputError(
                f"No {parser.PORTAL_SCHEMA.name} sheet found in "
                f"{upload.filename!r} uploaded as {upload.role!r}."
            )
        for sheet in sheets:
            for row in parser.read_portal(
                workbook, sheet, source=upload.filename, max_rows=request.max_rows
            ):
                rows_read += 1
                first_date = (
                    row.order_date
                    if first_date is None
                    else min(first_date, row.order_date)
                )
                last_date = (
                    row.order_date
                    if last_date is None
                    else max(last_date, row.order_date)
                )
                provider = provider or row.provider

                # Raised for every row, booked or not: a row with no booking
                # id still shows in the exceptions.
                raised = rules.row_exceptions(row, request.rates)
                if raised:
                    exceptions += raised
                    flagged.append(row)
                if row.booking_id is None:
                    # Kept to be traced, but no total is taken from it.
                    unbooked.append(row)
                    continue
                methods.add(row)
                if request.wallet is not None and row.wallet_amount > 0:
                    wallet_rows.append(row)
                acc = accumulators.setdefault(
                    row.booking_id, BookingAccumulator(booking_id=row.booking_id)
                )
                acc.add_portal(row)
        _note(manifest, upload, sheets, rows_read)

    if rows_read == 0:
        raise ReconInputError(
            f"{upload.filename!r} holds no Hotel or Flights rows. A portal export "
            "for another category cannot be reconciled as travel."
        )
    return _Portal(
        rows_read=rows_read,
        flagged=flagged,
        unbooked=unbooked,
        wallet_rows=wallet_rows,
        payment_methods=methods.build(),
        first_date=first_date,
        last_date=last_date,
        provider=provider,
        exceptions=exceptions,
    )


def _load_wallet(
    upload: Upload,
    request: TravelRequest,
    portal: _Portal,
    manifest: list[SheetManifest],
) -> tuple[WalletReconciliation, list[ReconException]]:
    history, sheet = load_wallet(
        upload, wallet_type=parser.vertical_from_text, max_rows=request.max_rows
    )
    _note(manifest, upload, [sheet], history.events)
    return reconcile_wallet(
        portal.wallet_rows,
        history,
        tolerance=request.rates.commission_tolerance,
        excluded=portal.unbooked,
    )


def _link_adjustments(
    adjustments: list[TravelAdjustment], accumulators: dict[str, BookingAccumulator]
) -> list[ReconException]:
    """Attach each free-text correction to the booking it names."""
    found: list[ReconException] = []
    for adjustment in adjustments:
        if adjustment.booking_id is None:
            found.append(
                make_exception(
                    ExceptionCode.ADJUSTMENT_UNLINKED,
                    amount=adjustment.commission,
                    detail=f"no booking id in {adjustment.text!r}",
                )
            )
            continue
        acc = accumulators.setdefault(
            adjustment.booking_id,
            BookingAccumulator(booking_id=adjustment.booking_id),
        )
        acc.add_adjustment(adjustment)
        found.append(
            make_exception(
                ExceptionCode.ADJUSTMENT_APPLIED,
                order_no=adjustment.booking_id,
                amount=adjustment.commission,
                detail=adjustment.text,
            )
        )
    return found


def _note(
    manifest: list[SheetManifest],
    upload: Upload,
    sheets: list[str],
    rows: int,
    *,
    role: str | None = None,
) -> None:
    manifest.append(
        SheetManifest(
            role=role or upload.role,
            filename=upload.filename,
            sheets=sheets,
            rows=rows,
        )
    )


def _build_summary(
    *,
    bookings: list[BookingRecon],
    accumulators: dict[str, BookingAccumulator],
    vendor_lines: int,
    portal_rows: int,
    unbooked_portal_rows: int,
    unbooked_vendor_rows: int,
    rates: TravelRateConfig,
    wallet: WalletReconciliation | None,
    exceptions: list[ReconException],
) -> TravelSummary:
    """Build the KPI block from the unrounded accumulators.

    Every comparison, difference and settlement figure is taken from the
    matched bookings alone; an unmatched booking is explained, not totalled.
    The ``claim_*`` figures and the claim-level findings beside them cover the
    whole claim, so the report still ties out to the vendor's own ledger.

    Each KPI is quantized once, here. The per-booking columns are each quantized
    once too, so a column can total a cent away from the headline — the headline
    is the figure to pay on.
    """
    matched = [b for b in bookings if b.unmatched_reason is None]
    unmatched = [b for b in bookings if b.unmatched_reason is not None]
    raw = [accumulators[b.booking_id] for b in matched]
    whole = accumulators.values()

    pairs = [(b, accumulators[b.booking_id]) for b in bookings]
    vendor_overcharge = sum(
        (
            acc.vendor_commission - acc.vendor_expected_commission
            for booking, acc in pairs
            if booking.refund is RefundKind.NONE
            and acc.vendor_commission - acc.vendor_expected_commission > 0
        ),
        Decimal(0),
    )
    # What the vendor keeps after reversing a booking it has also credited.
    commission_on_refunds = sum(
        (
            acc.vendor_commission
            for booking, acc in pairs
            if booking.refund is not RefundKind.NONE and acc.has_invoice
        ),
        Decimal(0),
    )

    contract = _contract_totals(matched, accumulators)

    by_vertical = {
        vertical: sum(
            (b.commission_as_vendor for b in matched if b.vertical is vertical),
            Decimal(0),
        )
        for vertical in Vertical
    }
    vat_divisor = Decimal(1) + rates.vat_rate
    reasons = [b.unmatched_reason for b in unmatched]

    return TravelSummary(
        portal_rows=portal_rows,
        portal_bookings=sum(1 for b in bookings if b.portal_rows),
        vendor_lines=vendor_lines,
        vendor_bookings=sum(1 for b in bookings if b.vendor_lines),
        bookings_matched=len(matched),
        bookings_unmatched=len(unmatched),
        unmatched_by_reason={
            str(reason): reasons.count(reason)
            for reason in UnmatchedReason
            if reason in reasons
        },
        bookings_portal_only=sum(
            1 for b in bookings if b.match_state is BookingMatchState.PORTAL_ONLY
        ),
        bookings_vendor_only=sum(
            1 for b in bookings if b.match_state is BookingMatchState.VENDOR_ONLY
        ),
        repeat_rows=sum(len(acc.repeats) for acc in whole),
        portal_rows_without_booking_id=unbooked_portal_rows,
        vendor_rows_without_booking_id=unbooked_vendor_rows,
        claim_base=money(sum((a.vendor_base for a in whole), Decimal(0))),
        claim_commission=money(sum((a.vendor_commission for a in whole), Decimal(0))),
        claim_net_payable=money(sum((a.vendor_net for a in whole), Decimal(0))),
        portal_base=money(
            sum((a.primary.order_total for a in raw if a.primary), Decimal(0))
        ),
        vendor_base=money(sum((a.vendor_base for a in raw), Decimal(0))),
        base_difference=money(sum((b.base_diff for b in matched), Decimal(0))),
        portal_commission=money(
            sum((a.primary.commission_charged for a in raw if a.primary), Decimal(0))
        ),
        vendor_commission=money(sum((a.vendor_commission for a in raw), Decimal(0))),
        commission_difference=money(
            sum((b.commission_diff for b in matched), Decimal(0))
        ),
        portal_net_payable=money(
            sum((a.primary.net_payable for a in raw if a.primary), Decimal(0))
        ),
        vendor_net_payable=money(sum((a.vendor_net for a in raw), Decimal(0))),
        net_payable_difference=money(
            sum((b.net_payable_diff for b in matched), Decimal(0))
        ),
        commission_as_vendor=money(
            sum((b.commission_as_vendor for b in matched), Decimal(0))
        ),
        net_payable_as_vendor=money(
            sum((b.net_payable_as_vendor for b in matched), Decimal(0))
        ),
        commission_difference_at_contract=money(contract.commission_difference),
        net_payable_difference_at_contract=money(contract.net_payable_difference),
        vat_uplift_shortfall=money(
            sum((b.vat_uplift_shortfall for b in matched), Decimal(0))
        ),
        vendor_overcharge=money(vendor_overcharge),
        vendor_adjustments=money(sum((a.adjustment for a in whole), Decimal(0))),
        retained_fees=money(
            sum((a.cancel_fee + a.change_fee for a in whole), Decimal(0))
        ),
        commission_retained_on_refunds=money(commission_on_refunds),
        hotel_commission=money(by_vertical[Vertical.HOTEL]),
        flight_commission=money(by_vertical[Vertical.FLIGHT]),
        hotel_commission_ex_vat=money(by_vertical[Vertical.HOTEL] / vat_divisor),
        flight_commission_ex_vat=money(by_vertical[Vertical.FLIGHT] / vat_divisor),
        wallet_rows_checked=wallet.rows_checked if wallet else 0,
        wallet_matched=wallet.matched if wallet else 0,
        exception_counts=group_exceptions(exceptions),
    )


def _reference_rows(
    bookings: list[BookingRecon],
    accumulators: dict[str, BookingAccumulator],
    unbooked: list[TravelPortalRow],
) -> list[TravelPortalRow]:
    """Every portal row no matched booking stands on, in the order reported.

    An unmatched booking's own row comes before its repeats, which is what
    lets the report tell the two apart.
    """
    unmatched: list[TravelPortalRow] = []
    repeats: list[TravelPortalRow] = []
    for booking in bookings:
        acc = accumulators[booking.booking_id]
        if booking.unmatched_reason is None:
            repeats.extend(acc.repeats)
            continue
        if acc.primary is not None:
            unmatched.append(acc.primary)
        unmatched.extend(acc.repeats)
    return unmatched + repeats + unbooked


@dataclass
class _ContractTotals:
    """Both sides of the matched bookings, priced at the contract rate.

    What is left once the rate is agreed is the difference in the base amount
    alone — the client's own headline comparison.
    """

    commission_difference: Decimal = Decimal(0)
    net_payable_difference: Decimal = Decimal(0)


def _contract_totals(
    bookings: list[BookingRecon], accumulators: dict[str, BookingAccumulator]
) -> _ContractTotals:
    """Recomputed from the unrounded order totals and the exact contract rate,
    so the headline is quantized once rather than summing rounded parts."""
    totals = _ContractTotals()
    for booking in bookings:
        acc = accumulators[booking.booking_id]
        row = acc.primary
        if row is None:
            continue
        commission = row.order_total * booking.contract_rate
        totals.commission_difference += commission - acc.vendor_commission
        totals.net_payable_difference += (row.order_total - commission) - acc.vendor_net
    return totals


def _shortfall_exception(bookings: list[BookingRecon]) -> list[ReconException]:
    """The VAT uplift, raised once for the run rather than once per booking.

    It is one finding about pricing, not a list of broken bookings: every
    international booking carries it by construction, so reporting each one
    would bury everything that genuinely needs looking at.
    """
    affected = [
        b
        for b in bookings
        if b.locality is Locality.INTERNATIONAL and b.vat_uplift_shortfall > 0
    ]
    if not affected:
        return []
    shortfall = money(sum((b.vat_uplift_shortfall for b in affected), Decimal(0)))
    return [
        make_exception(
            ExceptionCode.SG_COMMISSION_SHORT_OF_VENDOR_RATE,
            amount=shortfall,
            detail=(
                f"{len(affected)} international booking(s) were charged our local "
                "rate while the vendor bills that rate plus VAT; the difference is "
                "commission we never collected from the customer"
            ),
        )
    ]
