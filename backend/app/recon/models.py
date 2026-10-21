"""Canonical reconciliation models (plan.md section 4.4).

Every monetary field is a ``Decimal``. ``float`` must not appear anywhere in
this module or anything that feeds it.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.recon.cleaning import money
from app.recon.exceptions import ReconException
from app.recon.wallet import WalletReconciliation


class OrderStatus(StrEnum):
    """The complete status list, per plan.md Appendix A."""

    DELIVERED = "Delivered"
    READY_TO_PICKUP = "Ready to Pickup"
    RETURN = "Return"
    RTO = "RTO"


class LedgerDocType(StrEnum):
    SALES_INVOICE = "sales_invoice"
    SALES_RETURN = "sales_return"
    JOURNAL_ENTRY = "journal_entry"
    UNKNOWN = "unknown"


class VarianceClass(StrEnum):
    CLEAN = "clean"
    OVERCHARGE = "overcharge"
    WRONG_RATE = "wrong_rate"
    PROMO_ABSORBED = "promo_absorbed"


class MatchState(StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    PRIOR_PERIOD = "prior_period"
    LATER_PERIOD = "later_period"
    AMBIGUOUS = "ambiguous"


class PortalLine(BaseModel):
    """One order line from the portal export."""

    model_config = ConfigDict(frozen=True)

    order_no: str
    sku: str | None = None
    merchant_sku: str | None = None
    merchant_id: str | None = None
    order_date: date
    gmv: Decimal
    units: int = 0
    actual_commission: Decimal = Decimal(0)
    coupon_name: str | None = None
    coupon_value: Decimal | None = None
    status: OrderStatus
    brand: str | None = None
    item_title: str | None = None
    # Prior-cycle settlement, when the export carries a "Paid" column. This is
    # what makes clawbacks computable without a separate history upload.
    amount_paid: Decimal | None = None
    # Display only. The payment columns are order-level and repeat on every
    # line of the order; shipping is settled outside this reconciliation.
    payment_type: str | None = None
    wallet_amount: Decimal | None = None
    non_wallet_amount: Decimal | None = None
    shipping_owner: str | None = None
    shipping_cost: Decimal | None = None

    # Resolved by the rules engine, not by the parser.
    rate: Decimal = Decimal(0)
    expected_commission: Decimal = Decimal(0)
    commission_variance: Decimal = Decimal(0)
    variance_class: VarianceClass = VarianceClass.CLEAN


class LedgerEntry(BaseModel):
    """One transaction from the vendor's account statement."""

    doc_no: str | None = None
    doc_type: LedgerDocType = LedgerDocType.UNKNOWN
    doc_date: date | None = None
    debit: Decimal | None = None
    credit: Decimal | None = None
    balance: Decimal | None = None
    raw_ref: str | None = None
    raw_desc: str | None = None
    # From the template's order-number column when present, else extracted from
    # raw_desc; never from raw_ref, which truncates to 10 chars.
    order_no: str | None = None
    match_state: MatchState = MatchState.UNMATCHED
    sheet: str | None = None

    def dedup_key(self) -> tuple[str, str, str, str, str, str]:
        """The identity used to collapse the converter's duplicate sheets.

        Amounts are quantized before they enter the key: the same transaction is
        written as ``"82.00"`` text on one sheet and as the number ``82`` on
        another, and an unquantized key would treat those as two transactions.
        The order number separates same-day, same-amount invoices whose document
        number was left blank; every converter copy of a row yields the same one.
        """
        return (
            self.doc_no or "",
            self.order_no or "",
            str(self.doc_type),
            self.doc_date.isoformat() if self.doc_date else "",
            str(money(self.debit)) if self.debit is not None else "",
            str(money(self.credit)) if self.credit is not None else "",
        )


class PromoSku(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    offer_price: Decimal | None = None
    merchant_sku: str | None = None


class Settlement(BaseModel):
    """What an earlier cycle already paid for one order (plan.md section 2.4)."""

    model_config = ConfigDict(frozen=True)

    order_no: str
    amount_paid: Decimal
    settled_on: date | None = None
    cycle_ref: str | None = None


class OrderBrand(BaseModel):
    """One brand's lines within an order, for the report's breakdown."""

    brand: str
    lines: int = 0
    gmv: Decimal = Decimal(0)
    coupon_value: Decimal = Decimal(0)
    # Unrounded, like the line figures it sums, so a breakdown total is
    # quantized once rather than built from rounded parts.
    expected_commission: Decimal = Decimal(0)


class OrderRecon(BaseModel):
    """One order, reconciled."""

    order_no: str
    order_date: date
    line_count: int
    statuses: list[OrderStatus]
    merchant_id: str | None = None

    portal_gmv: Decimal
    vendor_amount: Decimal | None = None
    amount_diff: Decimal | None = None
    # The rest of the statement's side: credit notes and the first document date.
    vendor_credit: Decimal | None = None
    vendor_date: date | None = None

    expected_commission: Decimal
    actual_commission: Decimal
    commission_variance: Decimal
    variance_class: VarianceClass

    promo_base: Decimal = Decimal(0)
    standard_base: Decimal = Decimal(0)
    absorbed_commission: Decimal = Decimal(0)

    payable: Decimal
    adjustments: Decimal = Decimal(0)
    net_payable: Decimal
    included_in_payable: bool = True

    match_state: MatchState = MatchState.UNMATCHED
    flags: list[str] = Field(default_factory=list)

    # Display only: the report's dashboard and Orders sheet read these, and no
    # figure above depends on them. Shipping is never deducted.
    payment_type: str | None = None
    wallet_amount: Decimal = Decimal(0)
    non_wallet_amount: Decimal = Decimal(0)
    coupon_value: Decimal = Decimal(0)
    shipping_cost: Decimal = Decimal(0)
    sg_shipping: Decimal = Decimal(0)
    brands: list[OrderBrand] = Field(default_factory=list)


class LineVariance(BaseModel):
    """A single line whose commission breached tolerance.

    Only breaching lines are retained, which is what keeps memory proportional
    to the number of problems rather than the number of rows.
    """

    order_no: str
    sku: str | None = None
    item_title: str | None = None
    order_date: date
    status: OrderStatus
    gmv: Decimal
    rate: Decimal
    expected_commission: Decimal
    actual_commission: Decimal
    variance: Decimal
    variance_class: VarianceClass
    coupon_name: str | None = None
    coupon_value: Decimal | None = None


class RateConfig(BaseModel):
    """The rates in force for one run, echoed back for auditability."""

    model_config = ConfigDict(frozen=True)

    standard_rate: Decimal
    promo_rate: Decimal
    promo_cutoff: date
    line_tolerance: Decimal
    order_tolerance: Decimal


class Summary(BaseModel):
    """The KPI block (plan.md section 7)."""

    gross_sales: Decimal
    standard_base: Decimal
    promo_base: Decimal
    commission_earned: Decimal
    payable_before_adjustments: Decimal
    adjustments: Decimal
    net_payable: Decimal

    vendor_closing_balance: Decimal | None = None
    unexplained_difference: Decimal | None = None

    promotional_absorption: Decimal = Decimal(0)
    overcharged_commission: Decimal = Decimal(0)

    portal_lines: int = 0
    orders: int = 0
    ledger_entries: int = 0
    orders_matched: int = 0
    orders_exact: int = 0
    exception_counts: dict[str, int] = Field(default_factory=dict)


class SheetManifest(BaseModel):
    """Which sheet was chosen for which role, so misdetection is visible."""

    role: str
    filename: str
    sheets: list[str]
    rows: int


class ReconResult(BaseModel):
    category: str
    merchant_id: str | None = None
    period_start: date
    period_end: date
    rates: RateConfig

    orders: list[OrderRecon] = Field(default_factory=list)
    unmatched_ledger: list[LedgerEntry] = Field(default_factory=list)
    line_variances: list[LineVariance] = Field(default_factory=list)
    exceptions: list[ReconException] = Field(default_factory=list)
    detected: list[SheetManifest] = Field(default_factory=list)
    summary: Summary
    # Only when a wallet history was uploaded.
    wallet: WalletReconciliation | None = None
