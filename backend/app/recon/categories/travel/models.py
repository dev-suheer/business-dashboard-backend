"""Canonical travel models (plan-travel.md section 2 and 4).

Every monetary field is a ``Decimal``. ``float`` must not appear anywhere in
this module or anything that feeds it.

Travel reconciles at booking grain: one portal row against one to ten vendor
invoice lines. That is why these models are travel's own rather than the
order-line/ledger pair the eshop pipeline uses — see plan-travel.md section 4.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.recon.exceptions import ReconException
from app.recon.models import SheetManifest

# The wallet check is shared by every category; travel's names stay importable.
from app.recon.wallet import WalletCheck as WalletCheck
from app.recon.wallet import WalletEvent as WalletEvent
from app.recon.wallet import WalletReconciliation as WalletReconciliation
from app.recon.wallet import WalletState as WalletState


class Vertical(StrEnum):
    HOTEL = "hotel"
    FLIGHT = "flight"


class BookingStatus(StrEnum):
    """The portal's ``Order Status``. Only the first two ever reach the vendor."""

    PROCESSED = "Processed"
    CANCELLED = "Cancelled"
    EXPIRED = "Expired"
    PENDING = "Pending"
    UNKNOWN = "unknown"


class PaymentStatus(StrEnum):
    PAID = "Paid"
    COMPLETED = "Completed"
    PENDING = "Pending"
    FAILED = "Failed"
    REFUNDED = "Refunded"
    UNKNOWN = "unknown"


class BillingType(StrEnum):
    INVOICE = "Invoice"
    CREDIT_FOR_RETURNS = "Credit for Returns"
    UNKNOWN = "unknown"


class Locality(StrEnum):
    """Derived from VAT, never from the claim's ``DOM/INT`` label."""

    LOCAL = "local"
    INTERNATIONAL = "international"


class BookingMatchState(StrEnum):
    MATCHED = "matched"
    PORTAL_ONLY = "portal_only"
    VENDOR_ONLY = "vendor_only"


class RefundKind(StrEnum):
    NONE = "none"
    FULL = "full"
    PARTIAL = "partial"


class UnmatchedReason(StrEnum):
    """Why a booking is kept out of the matched output.

    A booking matches only when both sides carry it and neither has reversed
    it: a refund or a cancellation is meant to leave the two sides apart.
    """

    REFUNDED = "refunded"
    CANCELLED = "cancelled"
    MISSING_IN_VENDOR = "missing_in_vendor"
    MISSING_IN_PORTAL = "missing_in_portal"
    OTHER = "other"


class TravelPortalRow(BaseModel):
    """One row of the portal export, coerced but not yet judged.

    ``source``/``sheet``/``row`` are carried because ``SG Order ID`` is a
    per-export sequence rather than an identity — it changes between exports of
    the same bookings — so it cannot be used to point at a row in a report.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    sheet: str
    row: int

    sg_order_id: str | None = None
    booking_id: str | None = None
    # The trailing ``-N`` of a booking id that arrives split across rows.
    booking_suffix: int | None = None

    vertical: Vertical
    order_date: date
    order_time: str | None = None
    customer_ref: str | None = None
    provider: str | None = None
    transaction_id: str | None = None

    status: BookingStatus
    payment_status: PaymentStatus = PaymentStatus.UNKNOWN
    payment_method: str | None = None

    sub_total: Decimal = Decimal(0)
    # Display only: the promo code, the SG discount and the pre-discount total
    # feed the report's dashboard, never a calculation.
    promocode: str | None = None
    discount: Decimal = Decimal(0)
    vat: Decimal = Decimal(0)
    order_total: Decimal = Decimal(0)
    total_amount: Decimal = Decimal(0)
    # Hotels leave ``Commission Amount`` at 0 and fill ``Commission Charged``;
    # flights fill both. ``commission_charged`` is the figure under test.
    commission_amount: Decimal = Decimal(0)
    partner_payout: Decimal = Decimal(0)
    commission_charged: Decimal = Decimal(0)
    net_payable: Decimal = Decimal(0)
    wallet_amount: Decimal = Decimal(0)
    card_amount: Decimal = Decimal(0)
    refund_amount: Decimal = Decimal(0)
    compensation_amount: Decimal = Decimal(0)
    # ``SG Comission %``: "3.00 %" on flights, absent on hotels. Diagnostic
    # only — the rate that governs is resolved from the vertical.
    stated_rate: Decimal | None = None


class TravelInvoiceLine(BaseModel):
    """One line item of one vendor invoice."""

    model_config = ConfigDict(frozen=True)

    source: str
    sheet: str
    row: int

    booking_id: str
    booking_ref: str
    # The reference ends ``_R0`` when the line reverses an earlier booking.
    is_reversal_ref: bool = False

    invoice_no: str | None = None
    line_item: str | None = None
    billing_type: BillingType = BillingType.UNKNOWN
    service_type: str | None = None
    issue_date: date | None = None
    vendor_booking_date: date | None = None

    supplier: str | None = None
    service_provider: str | None = None
    description: str | None = None
    pnr: str | None = None
    to_number: str | None = None
    client_name: str | None = None
    route: str | None = None
    destination_city: str | None = None
    class_type: str | None = None
    # Kept as text: the source writes MM/DD/YYYY, which the shared date parser
    # deliberately does not accept — adding it would make DD/MM/YYYY ambiguous
    # for every other category.
    check_in_text: str | None = None
    check_out_text: str | None = None
    # The claim's own locality label. Carried for display; verified wrong on a
    # sample row, so it is never used to resolve a rate.
    dom_int_label: str | None = None

    base_ex_vat: Decimal = Decimal(0)
    vat_percent: Decimal = Decimal(0)
    vat: Decimal = Decimal(0)
    airline_cancel_fee: Decimal = Decimal(0)
    total_invoice: Decimal = Decimal(0)
    commission: Decimal = Decimal(0)
    net_payable: Decimal = Decimal(0)
    currency: str | None = None


class TravelAdjustment(BaseModel):
    """A free-text correction row sitting inside the claim's data sheet."""

    model_config = ConfigDict(frozen=True)

    source: str
    sheet: str
    row: int
    text: str
    booking_id: str | None = None
    commission: Decimal = Decimal(0)
    net_payable: Decimal = Decimal(0)


class TravelSkippedRow(BaseModel):
    """A claim row that looked like data but could not be used.

    Its amounts are kept so the row can still be traced in the report, even
    though nothing it carries reaches a calculation.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    sheet: str
    row: int
    reason: str
    text: str | None = None
    invoice_no: str | None = None
    total_invoice: Decimal | None = None
    commission: Decimal | None = None
    net_payable: Decimal | None = None


class ClaimPeriodTotals(BaseModel):
    """One line of the claim workbook's own per-period ledger."""

    model_config = ConfigDict(frozen=True)

    source: str
    claim_period: str
    total_claim: Decimal | None = None
    commission_amount: Decimal | None = None
    total_payable: Decimal | None = None
    paid_amount: Decimal | None = None
    difference: Decimal | None = None
    unprovided_commission: Decimal | None = None


class BookingRecon(BaseModel):
    """One booking, both sides, reconciled. The single rounding point.

    The computed columns carry the names the client's manual workbook gives
    them: ``sg_rate`` is their *Com Rate Check*, ``vendor_rate`` their *Vendor
    Rate Check*, ``commission_as_vendor`` their *Actual As Vendor* and
    ``net_payable_as_vendor`` their *Net Payable*.
    """

    booking_id: str
    vertical: Vertical
    locality: Locality
    booking_date: date | None = None
    order_date: date | None = None
    match_state: BookingMatchState
    refund: RefundKind = RefundKind.NONE
    # None when the booking matched; otherwise why it did not, in words.
    unmatched_reason: UnmatchedReason | None = None
    explanation: str = ""

    portal_rows: int = 0
    portal_base: Decimal = Decimal(0)
    portal_commission: Decimal = Decimal(0)
    portal_net_payable: Decimal = Decimal(0)
    portal_status: BookingStatus | None = None
    portal_payment_status: PaymentStatus | None = None
    payment_method: str | None = None
    wallet_amount: Decimal = Decimal(0)
    card_amount: Decimal = Decimal(0)

    vendor_lines: int = 0
    vendor_invoice_nos: list[str] = Field(default_factory=list)
    vendor_base: Decimal = Decimal(0)
    vendor_commission: Decimal = Decimal(0)
    vendor_net_payable: Decimal = Decimal(0)
    vendor_cancel_fee: Decimal = Decimal(0)
    vendor_change_fee: Decimal = Decimal(0)
    vendor_adjustment: Decimal = Decimal(0)

    sg_rate: Decimal | None = None
    vendor_rate: Decimal | None = None
    contract_rate: Decimal
    sg_expected_commission: Decimal = Decimal(0)
    vendor_expected_commission: Decimal = Decimal(0)

    base_diff: Decimal = Decimal(0)
    commission_diff: Decimal = Decimal(0)
    net_payable_diff: Decimal = Decimal(0)
    # What we settle on: our base at the vendor's contract rate.
    commission_as_vendor: Decimal = Decimal(0)
    net_payable_as_vendor: Decimal = Decimal(0)
    # The same two figures against the claim's own: what is left to argue about
    # once both sides use the contract rate, which is the base difference alone.
    commission_diff_as_vendor: Decimal = Decimal(0)
    net_payable_diff_as_vendor: Decimal = Decimal(0)
    # The commission we never collected from the customer.
    vat_uplift_shortfall: Decimal = Decimal(0)

    repeat_of: str | None = None
    flags: list[str] = Field(default_factory=list)
    # The primary portal row, whole, for the report's portal columns.
    portal: TravelPortalRow | None = None


class PaymentMethodTotals(BaseModel):
    label: str
    rows: int = 0
    bookings: int = 0
    order_total: Decimal = Decimal(0)
    wallet_amount: Decimal = Decimal(0)
    card_amount: Decimal = Decimal(0)


class PaymentMethodSummary(BaseModel):
    """Two cuts of the same rows.

    ``by_label`` is the client's pivot filter — the raw ``Payment Method`` text,
    blanks included. ``by_instrument`` splits a combined label on ``+`` so that
    "how much moved on wallet at all" has an answer.
    """

    by_label: list[PaymentMethodTotals] = Field(default_factory=list)
    by_instrument: list[PaymentMethodTotals] = Field(default_factory=list)


class TravelRateConfig(BaseModel):
    """The rates in force for one run, echoed back for auditability."""

    model_config = ConfigDict(frozen=True)

    hotel_rate: Decimal
    flight_rate: Decimal
    vat_rate: Decimal
    base_tolerance: Decimal
    commission_tolerance: Decimal
    rate_tolerance: Decimal

    def base_rate(self, vertical: Vertical) -> Decimal:
        return self.hotel_rate if vertical is Vertical.HOTEL else self.flight_rate

    def contract_rate(self, vertical: Vertical, locality: Locality) -> Decimal:
        """What the vendor is entitled to bill (plan-travel.md 3.1).

        An international booking carries no VAT of its own, so the vendor adds
        VAT to its commission instead. Left unrounded: it is a rate, not money.
        """
        rate = self.base_rate(vertical)
        if locality is Locality.INTERNATIONAL:
            rate *= Decimal(1) + self.vat_rate
        return rate


class TravelReferences(BaseModel):
    """Source rows kept out of the matched output, so nothing is lost.

    ``portal_rows`` holds every portal row no matched booking stands on: the
    rows of unmatched bookings, repeated rows, and rows with no booking id.
    Unmatched claim lines and adjustments are already on the result whole.
    """

    portal_rows: list[TravelPortalRow] = Field(default_factory=list)
    vendor_rows_without_booking_id: list[TravelSkippedRow] = Field(default_factory=list)


class TravelSummary(BaseModel):
    """The KPI block (plan-travel.md section 8, sheet 1).

    Money figures cover the matched bookings only; the ``claim_*`` figures are
    the whole claim as billed, to tie out against the vendor's own ledger.
    """

    portal_rows: int = 0
    portal_bookings: int = 0
    vendor_lines: int = 0
    vendor_bookings: int = 0
    bookings_matched: int = 0
    bookings_unmatched: int = 0
    unmatched_by_reason: dict[str, int] = Field(default_factory=dict)
    bookings_portal_only: int = 0
    bookings_vendor_only: int = 0
    repeat_rows: int = 0
    # Excluded from every calculation; listed in the references.
    portal_rows_without_booking_id: int = 0
    vendor_rows_without_booking_id: int = 0

    claim_base: Decimal = Decimal(0)
    claim_commission: Decimal = Decimal(0)
    claim_net_payable: Decimal = Decimal(0)

    portal_base: Decimal = Decimal(0)
    vendor_base: Decimal = Decimal(0)
    base_difference: Decimal = Decimal(0)
    portal_commission: Decimal = Decimal(0)
    vendor_commission: Decimal = Decimal(0)
    commission_difference: Decimal = Decimal(0)
    portal_net_payable: Decimal = Decimal(0)
    vendor_net_payable: Decimal = Decimal(0)
    net_payable_difference: Decimal = Decimal(0)

    commission_as_vendor: Decimal = Decimal(0)
    net_payable_as_vendor: Decimal = Decimal(0)
    # Both sides at the contract rate — the client's own headline comparison.
    commission_difference_at_contract: Decimal = Decimal(0)
    net_payable_difference_at_contract: Decimal = Decimal(0)
    vat_uplift_shortfall: Decimal = Decimal(0)
    vendor_overcharge: Decimal = Decimal(0)
    vendor_adjustments: Decimal = Decimal(0)
    retained_fees: Decimal = Decimal(0)
    commission_retained_on_refunds: Decimal = Decimal(0)

    hotel_commission: Decimal = Decimal(0)
    flight_commission: Decimal = Decimal(0)
    hotel_commission_ex_vat: Decimal = Decimal(0)
    flight_commission_ex_vat: Decimal = Decimal(0)

    wallet_rows_checked: int = 0
    wallet_matched: int = 0

    exception_counts: dict[str, int] = Field(default_factory=dict)


class TravelResult(BaseModel):
    category: Literal["travel"] = "travel"
    vendor_name: str | None = None
    period_start: date
    period_end: date
    rates: TravelRateConfig

    bookings: list[BookingRecon] = Field(default_factory=list)
    invoice_lines: list[TravelInvoiceLine] = Field(default_factory=list)
    # Only rows that raised something: the export's 135 expired rows are noise.
    portal_rows_flagged: list[TravelPortalRow] = Field(default_factory=list)
    adjustments: list[TravelAdjustment] = Field(default_factory=list)
    claim_periods: list[ClaimPeriodTotals] = Field(default_factory=list)
    references: TravelReferences = Field(default_factory=TravelReferences)

    payment_methods: PaymentMethodSummary = Field(default_factory=PaymentMethodSummary)
    wallet: WalletReconciliation | None = None

    exceptions: list[ReconException] = Field(default_factory=list)
    detected: list[SheetManifest] = Field(default_factory=list)
    summary: TravelSummary
