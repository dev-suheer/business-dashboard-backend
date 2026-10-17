"""Canonical food models (plan-food.md).

Every monetary field is a ``Decimal``. ``float`` must not appear anywhere in
this module or anything that feeds it.

Food reconciles one portal order against one Chefz order. The rate arrives on
the vendor row, and the result is a delivery split plus a fee check, which is
why these models are food's own rather than the eshop adapter's.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.recon.exceptions import ReconException
from app.recon.models import SheetManifest


class FoodVerdict(StrEnum):
    SETTLED = "settled"
    HELD = "held"
    PORTAL_ONLY = "portal_only"
    VENDOR_ONLY = "vendor_only"


class FoodPortalRow(BaseModel):
    """One food row of the portal export, coerced but not yet judged."""

    model_config = ConfigDict(frozen=True)

    source: str
    sheet: str
    row: int

    order_id: str
    order_date: date | None = None
    order_time: str | None = None
    sg_order_id: str | None = None
    vertical: str
    provider: str | None = None
    food_order_type: str | None = None
    restaurant_id: str | None = None
    restaurant_name: str | None = None
    transaction_id: str | None = None
    order_status: str
    payment_status: str | None = None
    payment_method: str | None = None
    sub_total: Decimal = Decimal(0)
    promocode: str | None = None
    discount: Decimal = Decimal(0)
    sg_delivery: Decimal = Decimal(0)
    vat: Decimal = Decimal(0)
    order_total: Decimal = Decimal(0)
    total_amount: Decimal = Decimal(0)
    # ``SG Comission %`` as a fraction (``5.00 %`` -> 0.05). Display only.
    stated_rate: Decimal | None = None
    commission_amount: Decimal = Decimal(0)
    partner_payout: Decimal = Decimal(0)
    commission_charged: Decimal = Decimal(0)
    wallet_amount: Decimal = Decimal(0)
    card_amount: Decimal = Decimal(0)
    refund_amount: Decimal = Decimal(0)
    compensation_amount: Decimal = Decimal(0)
    net_payable: Decimal = Decimal(0)


class FoodVendorRow(BaseModel):
    """One row of the Chefz order export."""

    model_config = ConfigDict(frozen=True)

    source: str
    sheet: str
    row: int

    order_id: str
    requested_delivery_date: date | None = None
    brand_name: str | None = None
    order_no: str | None = None
    customer_name: str | None = None
    total: Decimal = Decimal(0)
    order_chef_total: Decimal = Decimal(0)
    # Fraction, same scale as the portal's stated rate. ``5`` and ``5.00 %``
    # both become 0.05.
    shoppinggate_percentage: Decimal | None = None
    shoppinggate_fees: Decimal = Decimal(0)
    original_delivery_fee: Decimal = Decimal(0)
    sg_delivery_charge: Decimal = Decimal(0)
    delivery_fee: Decimal = Decimal(0)
    order_status: str
    order_items: str | None = None
    promotion_discount: Decimal = Decimal(0)
    chef_delivery_charge: Decimal = Decimal(0)

    @property
    def due_from_shoppinggate(self) -> Decimal:
        """Vendor total + SG's delivery share - vendor fees."""
        return self.total + self.sg_delivery_charge - self.shoppinggate_fees

    @property
    def net_invoice(self) -> Decimal:
        return self.shoppinggate_fees

    @property
    def net_delivery_invoice(self) -> Decimal:
        return self.original_delivery_fee - self.chef_delivery_charge


class FoodChecks(BaseModel):
    """The six middle columns of the hand-built recon sheet."""

    ids_match: bool
    order_amount_difference: Decimal | None = None
    fee_difference: Decimal | None = None
    sg_delivery_share: Decimal | None = None
    chef_delivery_share: Decimal | None = None
    customer_delivery: Decimal | None = None


class FoodOrder(BaseModel):
    """One order on the recon sheet, with whichever side we have."""

    order_id: str
    verdict: FoodVerdict
    settled: bool
    # Vendor due when a vendor row exists. Counted in ``settled_due`` only when
    # ``settled`` is true.
    due: Decimal | None = None
    portal: FoodPortalRow | None = None
    vendor: FoodVendorRow | None = None
    checks: FoodChecks
    flags: list[str] = Field(default_factory=list)


class FoodSummary(BaseModel):
    """Totals for the run. Money fields are already quantized to 2dp."""

    portal_rows: int = 0
    vendor_rows: int = 0
    paired: int = 0
    settled_orders: int = 0
    settled_due: Decimal = Decimal(0)
    held_orders: int = 0
    held_due: Decimal = Decimal(0)
    portal_only: int = 0
    vendor_only: int = 0
    vendor_only_due: Decimal = Decimal(0)
    amount_breaks: int = 0
    fee_breaks: int = 0
    delivery_breaks: int = 0
    not_delivered: int = 0
    compensation_orders: int = 0
    compensation_total: Decimal = Decimal(0)
    settled_subtotal: Decimal = Decimal(0)
    settled_customer_delivery: Decimal = Decimal(0)
    settled_sg_delivery: Decimal = Decimal(0)
    settled_vendor_fees: Decimal = Decimal(0)
    # Vendor chef total minus portal subtotal, settled rows only.
    settled_food_gap: Decimal = Decimal(0)
    portal_date_start: date | None = None
    portal_date_end: date | None = None
    rates_seen: list[Decimal] = Field(default_factory=list)


class FoodResult(BaseModel):
    category: str = "food"
    vendor_name: str | None = None
    orders: list[FoodOrder] = Field(default_factory=list)
    exceptions: list[ReconException] = Field(default_factory=list)
    detected: list[SheetManifest] = Field(default_factory=list)
    summary: FoodSummary
