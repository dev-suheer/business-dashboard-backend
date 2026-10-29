"""Food readers: the portal export and the Chefz order report.

Columns are located by header text. A title row above the headers is skipped.
Rows that are not ``Vertical = Food`` are not food and are not yielded.
"""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from openpyxl.workbook import Workbook

from app.recon.categories.food.models import FoodPortalRow, FoodVendorRow
from app.recon.cleaning import clean_text, normalize_header, to_date, to_decimal
from app.recon.excel import ColumnSpec, SheetSchema, iter_records

PORTAL_SCHEMA = SheetSchema(
    name="food portal export",
    fingerprint=(
        "SG Order ID",
        "Partner Order ID",
        "Vertical",
        "Order Total",
        "SG Delivery Expense",
        "Commission Charged",
        "Net Payable Amount to Partner",
    ),
    # The worked ANALYSIS sheet bolts the vendor columns onto the portal ones,
    # and it satisfies this fingerprint. Those headers are what disqualify it.
    exclude=(
        "order_id",
        "shoppinggate_fees",
        "shoppinggate_percentage",
        "MATCH ID'S",
        "Match ID's",
    ),
    columns=(
        ColumnSpec("sg_order_id", ("SG Order ID",)),
        ColumnSpec("order_id", ("Partner Order ID",), required=True),
        ColumnSpec("vertical", ("Vertical",), required=True),
        ColumnSpec("order_date", ("Order Date",)),
        ColumnSpec("order_time", ("Order Time",)),
        ColumnSpec("provider", ("Provider Name",)),
        ColumnSpec("food_order_type", ("Food Order Type",)),
        ColumnSpec("restaurant_id", ("Restaurant ID/Seller ID",)),
        ColumnSpec("restaurant_name", ("Restaurant Name",)),
        ColumnSpec("transaction_id", ("Transaction ID",)),
        ColumnSpec("order_status", ("Order Status",), required=True),
        ColumnSpec("payment_status", ("Payment Status",)),
        ColumnSpec("payment_method", ("Payment Method",)),
        ColumnSpec("sub_total", ("Sub Total",)),
        ColumnSpec("promocode", ("Promocode",)),
        ColumnSpec("discount", ("SG Discount/Promo Amount",)),
        ColumnSpec("sg_delivery", ("SG Delivery Expense",), required=True),
        ColumnSpec("vat", ("VAT",)),
        ColumnSpec("order_total", ("Order Total",)),
        ColumnSpec("total_amount", ("Total Amount",), required=True),
        ColumnSpec("stated_rate", ("SG Comission %", "SG Commission %")),
        ColumnSpec("commission_amount", ("Commission Amount",)),
        ColumnSpec("partner_payout", ("Partner Payout",)),
        ColumnSpec("commission_charged", ("Commission Charged",), required=True),
        ColumnSpec("wallet_amount", ("Wallet Amount",)),
        ColumnSpec("card_amount", ("Applepay/Card Amount",)),
        ColumnSpec("refund_amount", ("Refund Amount",)),
        ColumnSpec("compensation_amount", ("Compensation Amount",)),
        ColumnSpec("net_payable", ("Net Payable Amount to Partner",)),
    ),
)

VENDOR_SCHEMA = SheetSchema(
    name="food vendor report",
    fingerprint=(
        "order_id",
        "requestedDeliveryDate",
        "shoppinggate_percentage",
        "shoppinggate_fees",
        "original_deliveryFee",
        "promotion_shoppinggate_charge_amount",
        "promotion_chef_charge_amount",
    ),
    # The same ANALYSIS sheet also satisfies this fingerprint.
    exclude=("Partner Order ID", "MATCH ID'S", "Match ID's"),
    columns=(
        ColumnSpec("requested_delivery_date", ("requestedDeliveryDate",)),
        ColumnSpec("brand_name", ("brand_name",)),
        ColumnSpec("order_id", ("order_id",), required=True),
        ColumnSpec("order_no", ("order_no",)),
        ColumnSpec("customer_name", ("customer_name",)),
        ColumnSpec("total", ("total",), required=True),
        ColumnSpec("order_chef_total", ("order_chef_total",)),
        ColumnSpec("shoppinggate_percentage", ("shoppinggate_percentage",)),
        ColumnSpec("shoppinggate_fees", ("shoppinggate_fees",), required=True),
        ColumnSpec("original_delivery_fee", ("original_deliveryFee",), required=True),
        ColumnSpec(
            "sg_delivery_charge",
            ("promotion_shoppinggate_charge_amount",),
            required=True,
        ),
        ColumnSpec("delivery_fee", ("deliveryFee",)),
        ColumnSpec("order_status", ("order_status",), required=True),
        ColumnSpec("order_items", ("order_items",)),
        ColumnSpec("promotion_discount", ("promotion_discount",)),
        ColumnSpec(
            "chef_delivery_charge",
            ("promotion_chef_charge_amount",),
            required=True,
        ),
    ),
)

# The wallet history's name for food movements, and the key they are filed under.
WALLET_VERTICAL = "food"


def wallet_type(raw: Any) -> str | None:
    """Read a wallet history ``Wallet Type``: ``Food`` is ours, the rest are not."""
    return WALLET_VERTICAL if normalize_header(raw) == "food" else None


def order_key(raw: Any) -> str | None:
    """The join key, as text, without a trailing ``.0`` from a numeric cell."""
    text = clean_text(raw)
    if text is None:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def rate_fraction(raw: Any) -> Decimal | None:
    """A commission rate as a fraction.

    The portal writes ``5.00 %`` and the vendor writes ``5``. Both mean five
    percent. ``to_decimal`` already turns a percent sign into a fraction, so
    only a bare number above 1 still needs scaling.
    """
    value = to_decimal(raw)
    if value is None:
        return None
    if value > 1:
        return value / Decimal(100)
    return value


def _amount(raw: Any) -> Decimal:
    return to_decimal(raw) or Decimal(0)


def _text_id(raw: Any) -> str | None:
    return order_key(raw)


def read_portal(
    workbook: Workbook, sheets: list[str], *, source: str, max_rows: int
) -> Iterator[FoodPortalRow]:
    """Stream every food-shaped sheet. Other verticals are skipped."""
    for sheet in sheets:
        for index, record in enumerate(
            iter_records(workbook[sheet], PORTAL_SCHEMA, max_rows=max_rows), start=1
        ):
            vertical = clean_text(record.get("vertical"))
            if vertical is None or vertical.casefold() != "food":
                continue
            order_id = order_key(record.get("order_id"))
            status = clean_text(record.get("order_status"))
            if order_id is None or status is None:
                continue
            yield FoodPortalRow(
                source=source,
                sheet=sheet,
                row=index,
                order_id=order_id,
                order_date=to_date(record.get("order_date")),
                order_time=clean_text(record.get("order_time")),
                sg_order_id=_text_id(record.get("sg_order_id")),
                vertical=vertical,
                provider=clean_text(record.get("provider")),
                food_order_type=clean_text(record.get("food_order_type")),
                restaurant_id=_text_id(record.get("restaurant_id")),
                restaurant_name=clean_text(record.get("restaurant_name")),
                transaction_id=clean_text(record.get("transaction_id")),
                order_status=status,
                payment_status=clean_text(record.get("payment_status")),
                payment_method=clean_text(record.get("payment_method")),
                sub_total=_amount(record.get("sub_total")),
                promocode=clean_text(record.get("promocode")),
                discount=_amount(record.get("discount")),
                sg_delivery=_amount(record.get("sg_delivery")),
                vat=_amount(record.get("vat")),
                order_total=_amount(record.get("order_total")),
                total_amount=_amount(record.get("total_amount")),
                stated_rate=rate_fraction(record.get("stated_rate")),
                commission_amount=_amount(record.get("commission_amount")),
                partner_payout=_amount(record.get("partner_payout")),
                commission_charged=_amount(record.get("commission_charged")),
                wallet_amount=_amount(record.get("wallet_amount")),
                card_amount=_amount(record.get("card_amount")),
                refund_amount=_amount(record.get("refund_amount")),
                compensation_amount=_amount(record.get("compensation_amount")),
                net_payable=_amount(record.get("net_payable")),
            )


def read_vendor(
    workbook: Workbook, sheets: list[str], *, source: str, max_rows: int
) -> Iterator[FoodVendorRow]:
    """Stream every Chefz-shaped sheet. A totals row has no order id and is skipped."""
    for sheet in sheets:
        for index, record in enumerate(
            iter_records(workbook[sheet], VENDOR_SCHEMA, max_rows=max_rows), start=1
        ):
            order_id = order_key(record.get("order_id"))
            status = clean_text(record.get("order_status"))
            if order_id is None or status is None:
                continue
            yield FoodVendorRow(
                source=source,
                sheet=sheet,
                row=index,
                order_id=order_id,
                requested_delivery_date=to_date(record.get("requested_delivery_date")),
                brand_name=clean_text(record.get("brand_name")),
                order_no=_text_id(record.get("order_no")),
                customer_name=clean_text(record.get("customer_name")),
                total=_amount(record.get("total")),
                order_chef_total=_amount(record.get("order_chef_total")),
                shoppinggate_percentage=rate_fraction(
                    record.get("shoppinggate_percentage")
                ),
                shoppinggate_fees=_amount(record.get("shoppinggate_fees")),
                original_delivery_fee=_amount(record.get("original_delivery_fee")),
                sg_delivery_charge=_amount(record.get("sg_delivery_charge")),
                delivery_fee=_amount(record.get("delivery_fee")),
                order_status=status,
                order_items=clean_text(record.get("order_items")),
                promotion_discount=_amount(record.get("promotion_discount")),
                chef_delivery_charge=_amount(record.get("chef_delivery_charge")),
            )
