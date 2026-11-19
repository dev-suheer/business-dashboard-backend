"""The output workbook of version 1, as configuration.

Translated once from the v1 exporters, which it replaces: the column lists and
header rows below were dumped from their constants, and the sections and
blocks transcribe what their writers did, row by row. ``tests/test_parity.py``
proves the result is the approved workbook, cell for cell.
"""

from typing import Literal

from app.config_model.models import (
    BlockRow,
    Breakdown,
    BreakdownColumn,
    Cell,
    Contacts,
    DashBlockDef,
    DashValue,
    Named,
    OutputColumn,
    OutputTab,
    OutputWorkbook,
    Section,
    SheetLookup,
)
from app.config_model.standard_tabs import (
    copy_tab,
    erp_invoices_tab,
    exceptions_tab,
    wallet_tab,
)

# key, header, width, group, kind
ColumnSpec = tuple[str, str, int, str, str]

# The approved setups' look-up columns of the finance report, after the Orders
# sheet's own, and the finance report's column each brings back; each finds the order by the report's
# Order ID. The last two names are not on the client's draft; Reward Type and
# Outcome are the two facts that sit beside the reward.
FINANCE_LOOKUPS: tuple[tuple[ColumnSpec, str], ...] = (
    (("cashback_reward", "Cash Back Reward", 18, "cashback", "money"), "reward_amount"),
    (("vertical_name", "Vertical Name", 18, "cashback", "text"), "vertical_name"),
    (("reward_code", "Reward Code", 16, "cashback", "text"), "reward_code"),
    (("reward_type", "Reward Type", 14, "cashback", "text"), "reward_type"),
    (("outcome", "Outcome", 12, "cashback", "text"), "outcome"),
)
FINANCE_MATCH = "order_id"

ESHOP_COLUMNS = (
    ("order_date", "Date", 14, "portal", "date"),
    ("merchant_id", "Merchant Id", 12, "portal", "text"),
    ("order_no", "Order Number", 16, "portal", "text"),
    ("lines", "Lines", 8, "portal", "text"),
    ("statuses", "Order Status", 18, "portal", "text"),
    ("gmv", "GMV", 12, "portal", "money"),
    ("coupon_value", "Coupon Value", 12, "portal", "money"),
    ("actual", "SG Commission", 14, "portal", "money"),
    ("payment_type", "Payment Type", 20, "portal", "text"),
    ("wallet", "Wallet Amount", 14, "portal", "money"),
    ("non_wallet", "Non-Wallet Amount", 16, "portal", "money"),
    ("shipping", "Shipping Cost", 12, "portal", "money"),
    ("match", "MATCH ID'S", 12, "check", "text"),
    ("amount_diff", "ORDER AMOUNT DIFFERENCE", 18, "check", "diff"),
    ("standard_base", "Standard Base", 14, "check", "money"),
    ("promo_base", "Promo Base", 14, "check", "money"),
    ("expected", "Contract Commission", 16, "check", "money"),
    ("variance", "COMMISSION VARIANCE", 16, "check", "diff"),
    ("variance_class", "Variance Class", 16, "check", "text"),
    ("payable", "Payable", 14, "check", "money"),
    ("adjustments", "Adjustments", 14, "check", "money"),
    ("net_payable", "Net Payable", 14, "check", "money"),
    ("vendor_date", "Invoice Date", 14, "vendor", "date"),
    ("vendor_amount", "Invoice Amount", 14, "vendor", "money"),
    ("vendor_credit", "Credit Note Amount", 16, "vendor", "money"),
    ("verdict", "Verdict", 20, "meta", "text"),
)

TRAVEL_COLUMNS = (
    ("order_date", "Order Date", 14, "portal", "date"),
    ("order_time", "Order Time", 12, "portal", "text"),
    ("sg_order_id", "SG Order ID", 14, "portal", "text"),
    ("booking_id", "Partner Order ID", 20, "portal", "text"),
    ("vertical", "Vertical", 10, "portal", "text"),
    ("provider", "Provider Name", 16, "portal", "text"),
    ("transaction_id", "Transaction ID", 28, "portal", "text"),
    ("order_status", "Order Status", 14, "portal", "text"),
    ("payment_status", "Payment Status", 14, "portal", "text"),
    ("payment_method", "Payment Method", 20, "portal", "text"),
    ("sub_total", "Sub Total", 12, "portal", "money"),
    ("promocode", "Promocode", 12, "portal", "text"),
    ("discount", "SG Discount/Promo Amount", 16, "portal", "money"),
    ("vat", "VAT", 12, "portal", "money"),
    ("order_total", "Order Total", 12, "portal", "money"),
    ("total_amount", "Total Amount", 14, "portal", "money"),
    ("stated_rate", "SG Comission %", 14, "portal", "rate"),
    ("commission_amount", "Commission Amount", 16, "portal", "money"),
    ("partner_payout", "Partner Payout", 14, "portal", "money"),
    ("commission_charged", "Commission Charged", 16, "portal", "money"),
    ("wallet_amount", "Wallet Amount", 14, "portal", "money"),
    ("card_amount", "Applepay/Card Amount", 18, "portal", "money"),
    ("refund_amount", "Refund Amount", 14, "portal", "money"),
    ("compensation_amount", "Compensation Amount", 16, "portal", "money"),
    ("net_payable", "Net Payable Amount to Partner", 18, "portal", "money"),
    ("match", "MATCH ID'S", 12, "check", "text"),
    ("sg_rate", "Com Rate Check", 12, "check", "rate"),
    ("vendor_rate", "Vendor Rate Check", 12, "check", "rate"),
    ("rate_diff", "Difference", 12, "check", "rate"),
    ("contract_rate", "Contract Rate", 12, "check", "rate"),
    ("as_vendor", "Actual As Vendor", 14, "check", "money"),
    ("net_as_vendor", "Net Payable", 14, "check", "money"),
    ("base_diff", "BASE DIFFERENCE", 14, "check", "diff"),
    ("net_diff", "NET PAYABLE DIFFERENCE", 16, "check", "diff"),
    ("uplift", "VAT UPLIFT NOT CHARGED", 16, "check", "money"),
    ("dom_int", "DOM/INT", 10, "vendor", "text"),
    ("invoice", "Invoice Number", 16, "vendor", "text"),
    ("lines", "Claim Lines", 10, "vendor", "text"),
    ("billing", "Billing Type", 14, "vendor", "text"),
    ("issued", "Issue Date", 12, "vendor", "date"),
    ("service", "Service Type", 10, "vendor", "text"),
    ("service_provider", "Service Provider", 18, "vendor", "text"),
    ("description", "Description of Service", 22, "vendor", "text"),
    ("supplier", "Supplier Name", 16, "vendor", "text"),
    ("pnr", "PNR No", 12, "vendor", "text"),
    ("booked", "Booking Date", 12, "vendor", "date"),
    ("check_in", "Check In Date", 12, "vendor", "text"),
    ("check_out", "Check Out Date", 12, "vendor", "text"),
    ("to_number", "T/O Number", 14, "vendor", "text"),
    ("client", "Client Name", 24, "vendor", "text"),
    ("route", "Route", 18, "vendor", "text"),
    ("city", "Destination City", 16, "vendor", "text"),
    ("class_type", "Class/Type", 20, "vendor", "text"),
    ("base_ex_vat", "Total Price W.O VAT", 16, "vendor", "money"),
    ("vat_percent", "VAT %", 8, "vendor", "text"),
    ("line_vat", "VAT", 12, "vendor", "money"),
    ("cancel_fee", "Airline Can Fee", 14, "vendor", "money"),
    ("total_invoice", "Total Invoice Amount", 16, "vendor", "money"),
    ("v_commission", "commision amount", 16, "vendor", "money"),
    ("v_net", "net payable amount", 16, "vendor", "money"),
    ("currency", "Currency", 10, "vendor", "text"),
    ("booking_ref", "Booking ID", 26, "vendor", "text"),
    ("verdict", "Verdict", 24, "meta", "text"),
)

FOOD_COLUMNS = (
    ("order_date", "Order Date", 14, "portal", "date"),
    ("order_time", "Order Time", 12, "portal", "text"),
    ("sg_order_id", "SG Order ID", 14, "portal", "text"),
    ("order_id", "Partner Order ID", 18, "portal", "text"),
    ("vertical", "Vertical", 12, "portal", "text"),
    ("provider", "Provider Name", 16, "portal", "text"),
    ("food_order_type", "Food Order Type", 16, "portal", "text"),
    ("restaurant_id", "Restaurant ID/Seller ID", 18, "portal", "text"),
    ("restaurant_name", "Restaurant Name", 36, "portal", "text"),
    ("transaction_id", "Transaction ID", 28, "portal", "text"),
    ("order_status", "Order Status", 14, "portal", "text"),
    ("payment_status", "Payment Status", 14, "portal", "text"),
    ("payment_method", "Payment Method", 20, "portal", "text"),
    ("sub_total", "Sub Total", 12, "portal", "money"),
    ("promocode", "Promocode", 12, "portal", "text"),
    ("discount", "SG Discount/Promo Amount", 16, "portal", "money"),
    ("sg_delivery", "SG Delivery Expense", 16, "portal", "money"),
    ("vat", "VAT", 12, "portal", "money"),
    ("order_total", "Order Total", 12, "portal", "money"),
    ("total_amount", "Total Amount", 14, "portal", "money"),
    ("stated_rate", "SG Comission %", 14, "portal", "rate"),
    ("commission_amount", "Commission Amount", 16, "portal", "money"),
    ("partner_payout", "Partner Payout", 14, "portal", "money"),
    ("commission_charged", "Commission Charged", 16, "portal", "money"),
    ("wallet_amount", "Wallet Amount", 14, "portal", "money"),
    ("card_amount", "Applepay/Card Amount", 18, "portal", "money"),
    ("refund_amount", "Refund Amount", 14, "portal", "money"),
    ("compensation_amount", "Compensation Amount", 16, "portal", "money"),
    ("net_payable", "Net Payable Amount to Partner", 18, "portal", "money"),
    ("match", "MATCH ID'S", 12, "check", "text"),
    ("amount_diff", "ORDER AMOUNT DIFFERENCE", 18, "check", "diff"),
    ("fee_diff", "RATE% CHECK SG-VNDR SOA", 16, "check", "diff"),
    ("sg_bear", "Additional Del Charges SG BEAR", 16, "check", "money"),
    ("chefz_bear", "Additional Delivery CHarge CHEFZ BEAR", 18, "check", "money"),
    ("cust_del", "Customer Delivery Charges", 16, "check", "money"),
    ("v_date", "requestedDeliveryDate", 20, "vendor", "date"),
    ("brand_name", "brand_name", 28, "vendor", "text"),
    ("v_order_id", "order_id", 16, "vendor", "text"),
    ("order_no", "order_no", 14, "vendor", "text"),
    ("customer_name", "customer_name", 18, "vendor", "text"),
    ("v_total", "total", 12, "vendor", "money"),
    ("chef_total", "order_chef_total", 16, "vendor", "money"),
    ("v_rate", "shoppinggate_percentage", 16, "vendor", "rate"),
    ("v_fees", "shoppinggate_fees", 16, "vendor", "money"),
    ("orig_delivery", "original_deliveryFee", 18, "vendor", "money"),
    ("sg_charge", "promotion_shoppinggate_charge_amount", 22, "vendor", "money"),
    ("delivery_fee", "deliveryFee", 12, "vendor", "money"),
    ("v_status", "order_status", 14, "vendor", "text"),
    ("order_items", "order_items", 40, "vendor", "text"),
    ("promo_discount", "promotion_discount", 16, "vendor", "money"),
    ("chef_charge", "promotion_chef_charge_amount", 20, "vendor", "money"),
    ("due", "Due from shoppinggate", 20, "vendor", "money"),
    ("net_invoice", "Net Invoice from shoppinggate", 18, "vendor", "money"),
    ("net_delivery", "Net Delivery Invoice to shoppinggate", 22, "vendor", "money"),
    ("verdict", "Verdict", 14, "meta", "text"),
)

# What each eShop Orders cell shows, on an order the generic runner built. A
# column not listed here shows the order's field of the same name.
ESHOP_VALUES = {
    "order_no": "key",
    "lines": "n_lines",
    "gmv": "portal_gmv",
    "actual": "actual_commission",
    "wallet": "wallet_amount",
    "non_wallet": "non_wallet_amount",
    "shipping": "shipping_cost",
    "match": 'IF(matched, "TRUE", "FALSE")',
    "expected": "expected_commission",
    "variance": "commission_variance",
}
# A statement row with no portal order fills only the statement's columns.
ESHOP_STATEMENT_VALUES = {
    "order_no": "order_no",
    "match": '"FALSE"',
    "vendor_date": "doc_date",
    "vendor_amount": "debit",
    "vendor_credit": "credit",
    "verdict": (
        'LABEL("match_state", IFS(ISBLANK(doc_date), "unmatched", '
        'doc_date < period_start, "prior_period", doc_date > period_end, '
        '"later_period", TRUE, "unmatched"))'
    ),
}

# What each Travel Orders cell shows, on a booking the generic runner built.
# The check columns are written only where both sides carry the booking, and
# the claim columns only where the claim does; the joined ones list each
# distinct value once, in the order met.
_LINES = "n_lines > 0"
TRAVEL_VALUES = {
    **{
        key: f"portal.{key}"
        for key in (
            "order_date",
            "order_time",
            "sg_order_id",
            "provider",
            "transaction_id",
            "payment_method",
            "sub_total",
            "promocode",
            "discount",
            "vat",
            "order_total",
            "total_amount",
            "stated_rate",
            "commission_amount",
            "partner_payout",
            "commission_charged",
            "wallet_amount",
            "card_amount",
            "refund_amount",
            "compensation_amount",
            "net_payable",
        )
    },
    "booking_id": "key",
    "vertical": (
        'IF(ISBLANK(portal), BLANK(), IF(portal.vertical = "hotel", "Hotel", "Flights"))'
    ),
    "order_status": "portal.status",
    "payment_status": "portal.payment_status",
    "match": 'IF(both, "TRUE", "FALSE")',
    "sg_rate": "IF(both, sg_rate, BLANK())",
    "vendor_rate": "IF(both, vendor_rate, BLANK())",
    "rate_diff": "IF(both, sg_rate - vendor_rate, BLANK())",
    "contract_rate": "IF(both, contract_rate, BLANK())",
    "as_vendor": "IF(both, commission_as_vendor, BLANK())",
    "net_as_vendor": "IF(both, net_payable_as_vendor, BLANK())",
    "base_diff": "IF(both, base_diff, BLANK())",
    "net_diff": "IF(both, net_payable_diff_as_vendor, BLANK())",
    "uplift": "IF(both, vat_uplift_shortfall, BLANK())",
    **{
        key: f'JOINX("lines", {field}, ", ")'
        for key, field in (
            ("dom_int", "dom_int_label"),
            ("invoice", "invoice_no"),
            ("billing", "billing_type"),
            ("service", "service_type"),
            ("service_provider", "service_provider"),
            ("description", "description"),
            ("supplier", "supplier"),
            ("pnr", "pnr"),
            ("check_in", "check_in"),
            ("check_out", "check_out"),
            ("to_number", "to_number"),
            ("client", "client_name"),
            ("route", "route"),
            ("city", "destination_city"),
            ("class_type", "class_type"),
            ("vat_percent", "vat_percent_text"),
            ("currency", "currency"),
            ("booking_ref", "booking_ref"),
        )
    },
    "lines": f"IF({_LINES}, n_lines, BLANK())",
    "issued": 'FIRSTX("lines", issue_date)',
    "booked": 'FIRSTX("lines", vendor_booking_date)',
    "base_ex_vat": f'IF({_LINES}, MONEY(SUMX("lines", base_ex_vat)), BLANK())',
    "line_vat": f'IF({_LINES}, MONEY(SUMX("lines", vat)), BLANK())',
    "cancel_fee": f"IF({_LINES}, vendor_cancel_fee, BLANK())",
    "total_invoice": f"IF({_LINES}, vendor_base, BLANK())",
    "v_commission": f"IF({_LINES}, vendor_commission, BLANK())",
    "v_net": f"IF({_LINES}, vendor_net_payable, BLANK())",
    # Why an unmatched booking is out; for a matched one, what disagrees. A
    # commission gap that only follows from the base difference or the VAT
    # uplift is not one.
    "verdict": (
        'IF(verdict <> "matched", LABEL("unmatched_reason", verdict), IFS('
        "AND(ABS(base_diff) > param.base_tolerance, "
        "ABS(vendor_commission - vendor_expected_commission) > param.commission_tolerance), "
        '"Base & commission differ", '
        'ABS(base_diff) > param.base_tolerance, "Base differs", '
        "ABS(vendor_commission - vendor_expected_commission) > param.commission_tolerance, "
        '"Commission differs", TRUE, "Matched"))'
    ),
}

# What each Food Orders cell shows, on an order the generic runner built. A
# column not listed here shows the order's field of the same name.
FOOD_VALUES = {
    **{
        key: f"portal.{key}"
        for key, _header, _width, group, _kind in FOOD_COLUMNS
        if group == "portal" and key != "order_id"
    },
    "order_id": "key",
    "v_date": "vendor.requested_delivery_date",
    "brand_name": "vendor.brand_name",
    "v_order_id": "vendor.order_id",
    "order_no": "vendor.order_no",
    "customer_name": "vendor.customer_name",
    "v_total": "vendor.total",
    "chef_total": "vendor.order_chef_total",
    "v_rate": "vendor.shoppinggate_percentage",
    "v_fees": "vendor.shoppinggate_fees",
    "orig_delivery": "vendor.original_delivery_fee",
    "sg_charge": "vendor.sg_delivery_charge",
    "delivery_fee": "vendor.delivery_fee",
    "v_status": "vendor.order_status",
    "order_items": "vendor.order_items",
    "promo_discount": "vendor.promotion_discount",
    "chef_charge": "vendor.chef_delivery_charge",
    "net_invoice": "vendor.shoppinggate_fees",
    "net_delivery": "vendor.original_delivery_fee - vendor.chef_delivery_charge",
}

ESHOP_PRIMARY_HEADERS = (
    "Order",
    "GMV",
    "Vendor Amount",
    "Amount Difference",
    "SG Commission",
    "Contract Commission",
    "Round of differ",
    "Net Payable",
    "Wallet Amount",
    "Non-Wallet Amount",
    "Total collection",
    "Portal total",
    "T. Order - Collection Dif",
    "Coupon disc",
    "T. Remaining",
)

ESHOP_COLLECTION_HEADERS = (
    "Total Order Amount",
    "Wallets",
    "Other Method",
    "Collection",
    "Order-Collection",
    "Promo Discount",
    "Net Difference",
)

ESHOP_SHIPPING_HEADERS = (
    "SG Shipping",
    "Merchant Shipping",
    "TOTAL",
    "Average Per Order",
)

ESHOP_SHARE_HEADERS = (
    "By shoppinggate",
    "By Vendor",
)

ESHOP_BREAKDOWN_HEADERS = (
    "Breakdown By Most Used",
    "Order Count",
    "Discount Count",
    "Revenue By Brand",
    "Applepay",
    "Tamara",
    "Card",
    "Wallet",
    "Commission",
    "Total Shipping",
)

TRAVEL_PRIMARY_HEADERS = (
    "Order",
    "Booking Amount",
    "Vendor Amount",
    "Base Difference",
    "SG Commission",
    "Vendor Commission",
    "Round of differ",
    "Net Payable",
    "Wallet Amount",
    "Applepay/Card Amount",
    "Total collection",
    "Portal total",
    "T. Order - Collection Dif",
    "sg promo code disc",
    "T. Remaining",
)

TRAVEL_COLLECTION_HEADERS = (
    "Total Order Amount",
    "Wallets",
    "Other Method",
    "Collection",
    "Order-Collection",
    "Promo Discount",
    "Net Difference",
)

TRAVEL_COMMISSION_HEADERS = (
    "Hotel",
    "Flights",
    "TOTAL",
    "Excluding VAT",
    "Average Per Order",
)

TRAVEL_SHARE_HEADERS = (
    "By Hotel",
    "By Flights",
)

TRAVEL_BREAKDOWN_HEADERS = (
    "Breakdown By Most Used",
    "Order Count",
    "Discount Count",
    "Revenue By Provider",
    "Applepay",
    "Tamara",
    "Card",
    "Wallet",
    "Commission",
    "Net Payable",
)

FOOD_PRIMARY_HEADERS = (
    "Order",
    "Food Amount",
    "Delivery",
    "ADD DC",
    "SG Commission",
    "Round of differ",
    "Road Off Adjustment",
    "Net Payable",
    "Wallet Amount",
    "Applepay/Card Amount",
    "Total collection",
    "Portal total",
    "T. Order - Collection Dif",
    "sg promo code disc",
    "T. Remaining",
)

FOOD_COLLECTION_HEADERS = (
    "Total Order Amount",
    "Wallets",
    "Other Method",
    "Collection",
    "Order-Collection",
    "Promo Discount",
    "Net Difference",
)

FOOD_DELIVERY_HEADERS = (
    "Customer Share",
    "Shoppinggate Share",
    "Merchant Share",
    "TOTAL",
    "Average Per Order",
)

FOOD_SHARE_HEADERS = (
    "By Customer",
    "By shoppinggate",
    "By Vendor",
)

FOOD_BREAKDOWN_HEADERS = (
    "Breakdown By Most Used",
    "Order Count",
    "Discount Count",
    "Revenue By Restaurant",
    "Applepay",
    "Tamara",
    "Card",
    "Wallet",
    "Commission",
    "Total Delivery",
)


def _columns(
    specs: tuple[ColumnSpec, ...], values: dict[str, str] | None = None
) -> list[OutputColumn]:
    return [
        OutputColumn(
            key=key,
            header=header,
            width=width,
            group=group,
            kind=kind,
            value=(values or {}).get(key),
        )
        for key, header, width, group, kind in specs
    ]


def finance_lookups(
    sheet: str,
    *,
    key_column: str | None = None,
    key_value: str | None = None,
) -> list[OutputColumn]:
    """The approved setups' five look-up columns of the finance report, finding
    each row in the copy ``sheet`` of the finance report by its key: a column of
    the table, or a formula. Version 1 is built from them; the editor adds
    look-up columns one at a time."""
    return [
        OutputColumn(
            key=key,
            header=header,
            width=width,
            group=group,
            kind=kind,
            lookup=SheetLookup(
                sheet=sheet,
                match=FINANCE_MATCH,
                value=value,
                key_column=key_column,
                key_value=key_value,
            ),
        )
        for (key, header, width, group, kind), value in FINANCE_LOOKUPS
    ]


def _dash(
    label: str | None,
    headers: tuple[str, ...],
    metrics: list[str],
    *,
    kind: Literal["int", "money", "pct"] = "money",
    first: str | None = None,
    value_label: str | None = None,
    tall: bool = True,
) -> DashBlockDef:
    """A dashboard block. ``first`` is a leading count, written as a whole number."""
    values = [DashValue(metric=first, kind="int")] if first else []
    values += [DashValue(metric=m, kind=kind) for m in metrics]
    return DashBlockDef(
        label=label,
        value_label=value_label,
        headers=list(headers),
        values=values,
        tall=tall,
    )


def _breakdown(
    headers: tuple[str, ...],
    *,
    over: str,
    name: str,
    commission: str,
    extra: str,
    parts: str | None = None,
    basis: str = "1",
    totals: list[Named] | None = None,
    paid: str = "TRUE",
    revenue: str = "0",
    discounted: str = "FALSE",
    payment_method: str = "BLANK()",
    card: str = "0",
    wallet: str = "0",
    total: Literal["sum", "whole"] = "sum",
) -> Breakdown:
    """The approved "Breakdown By Most Used": for each name, its orders; of the
    paid ones, those with a discount, the revenue, the card share in the column
    its payment method names (Tamara, else Apple Pay, else card) and the wallet
    share; then the commission and the category's last column, on every order.
    Ranked by the orders."""
    tamara = f'CONTAINS({payment_method}, "tamara")'
    apple = f'CONTAINS({payment_method}, "apple")'
    named_card = f'CONTAINS({payment_method}, "card")'
    columns = [
        BreakdownColumn(header=headers[1], add="count", kind="int"),
        BreakdownColumn(
            header=headers[2], add="count", when=_all(paid, discounted), kind="int"
        ),
        BreakdownColumn(header=headers[3], value=revenue, when=_all(paid)),
        BreakdownColumn(
            header=headers[4], value=card, when=_all(paid, apple, f"NOT({tamara})")
        ),
        BreakdownColumn(header=headers[5], value=card, when=_all(paid, tamara)),
        BreakdownColumn(
            header=headers[6],
            value=card,
            when=_all(paid, named_card, f"NOT({tamara})", f"NOT({apple})"),
        ),
        BreakdownColumn(header=headers[7], value=wallet, when=_all(paid)),
        BreakdownColumn(header=headers[8], value=commission),
        BreakdownColumn(header=headers[9], value=extra),
    ]
    return Breakdown(
        set=over,
        parts=parts,
        name=name,
        basis=basis,
        totals=totals or [],
        name_header=headers[0],
        columns=columns,
        rank_by=0,
        total=total,
    )


def _all(*conditions: str) -> str:
    """Where every condition holds; TRUE says nothing."""
    held = [c for c in conditions if c != "TRUE"]
    if not held:
        return "TRUE"
    return held[0] if len(held) == 1 else f"AND({', '.join(held)})"


def _label(text: str) -> Cell:
    """A label across columns A to C."""
    return Cell(col=0, to_col=2, text=text, format="label")


def _total(key: str) -> str:
    """A reference to a column's figure on the totals row."""
    return f"{{col:{key}}}{{row:totals}}"


# The finance report's copy, which the Orders sheet's look-up columns look in.
_FINANCE_COPY = "finance_copy"


def _tabs(
    dashboard: OutputTab,
    orders: OutputTab,
    vendor_sheet: str,
    *,
    invoices: OutputTab,
    exception_status: str,
    texts: list[Named] | None = None,
) -> OutputWorkbook:
    """Every category's report: the same tabs, in the same order (plan-food.md 8).
    ``exception_status`` is the Exceptions sheet's Status, on each exception."""
    return OutputWorkbook(
        texts=texts or [],
        tabs=[
            dashboard,
            orders,
            exceptions_tab("exceptions", "Exceptions", exception_status),
            wallet_tab("wallet", "Wallet"),
            invoices,
            copy_tab("portal_copy", "Portal", "portal", "portal", "portal_header"),
            copy_tab("vendor_copy", "Vendor", "vendor", vendor_sheet, "vendor_header"),
            copy_tab(
                _FINANCE_COPY,
                "Finance Report",
                "finance",
                "report",
                "cashback_header",
                requires="finance",
            ),
        ],
    )


# --- eShop ------------------------------------------------------------------

_ESHOP_BRIDGE = (
    ("Payable before adjustments", "payable_before_adjustments", False),
    ("Adjustments", "adjustments", False),
    ("Net payable", "net_payable", True),
    ("Vendor closing balance", "vendor_closing_balance", False),
    ("Unexplained difference", "unexplained_difference", False),
    ("Standard-rate base", "standard_base", False),
    ("Promo-rate base", "promo_base", False),
    ("Commission earned", "commission_earned", False),
    ("Promotional absorption (we bear this)", "promotional_absorption", False),
    ("Overcharged commission (owed back)", "overcharged_commission", False),
)


def eshop_output() -> OutputWorkbook:
    dashboard = OutputTab(
        key="dashboard",
        title="Dashboard",
        kind="dashboard",
        blocks=[
            _dash(
                "SG ESHOP ORDER Amount",
                ESHOP_PRIMARY_HEADERS,
                [
                    "gmv",
                    "vendor",
                    "amount_difference",
                    "actual_commission",
                    "commission_earned",
                    "round_off",
                    "net_payable",
                    "wallet",
                    "non_wallet",
                    "collection",
                    "gmv",
                    "collection_gap",
                    "coupon",
                    "remaining",
                ],
                first="payable_orders",
                value_label="Vendor ESHOP ORDER Amount",
            ),
            _dash(
                "Cash",
                ESHOP_COLLECTION_HEADERS,
                [
                    "gmv",
                    "wallet",
                    "non_wallet",
                    "collection",
                    "order_collection",
                    "coupon",
                    "net_difference",
                ],
            ),
            _dash(
                "Shipping",
                ESHOP_SHIPPING_HEADERS,
                ["sg_shipping", "merchant_shipping", "shipping", "average_shipping"],
            ),
            _dash(
                "Average Shipping share",
                ESHOP_SHARE_HEADERS,
                ["sg_shipping_share", "merchant_shipping_share"],
                kind="pct",
                tall=False,
            ),
        ],
        # One row per brand, over the orders in the payable. An order carrying
        # several brands is counted under each: its payment split and shipping
        # are shared by their part of its GMV, and coupon and commission are
        # the brand's own lines. The TOTAL row counts every order once, unsplit.
        breakdown=_breakdown(
            ESHOP_BREAKDOWN_HEADERS,
            over="included",
            parts="brands",
            name="key",
            basis="gmv",
            totals=[
                Named(key="coupon", formula="coupon"),
                Named(key="expected", formula="expected"),
            ],
            commission="expected",
            extra="shipping_cost * share",
            revenue="coupon",
            discounted="coupon <> 0",
            payment_method="payment_type",
            card="non_wallet_amount * share",
            wallet="wallet_amount * share",
            total="whole",
        ),
    )
    orders = OutputTab(
        key="orders",
        title="Orders",
        kind="table",
        columns=_columns(ESHOP_COLUMNS, ESHOP_VALUES),
        # The Orders sheet has no SG Order ID: the order number's digits are the key.
        lookup_columns=finance_lookups(_FINANCE_COPY, key_value="order_digits"),
        tolerances={
            "amount_diff": "param.order_tolerance",
            "variance": "param.line_tolerance",
        },
        sections=[
            Section(kind="rows", set="included", autofilter=True, lookups=True),
            Section(
                kind="totals",
                set="included",
                when="included",
                label="Total, {count} in the payable",
            ),
            # The settlement bridge and the rate legend, across.
            Section(
                kind="block",
                when="included",
                gap=1,
                rows=[
                    BlockRow(
                        height=32,
                        cells=[
                            Cell(col=col, text=title, format="dash_header")
                            for col, (title, _metric, _yellow) in enumerate(
                                _ESHOP_BRIDGE
                            )
                        ],
                    ),
                    BlockRow(
                        cells=[
                            Cell(
                                col=col,
                                metric=metric,
                                format="yellow" if yellow else "money",
                            )
                            for col, (_title, metric, yellow) in enumerate(
                                _ESHOP_BRIDGE
                            )
                        ]
                    ),
                    BlockRow(),
                    BlockRow(
                        cells=[
                            Cell(
                                col=0,
                                text=(
                                    "Rates applied: standard {standard_rate}, promo "
                                    "{promo_rate} on promotional SKUs ordered up to "
                                    "{promo_cutoff} inclusive. Commission base is always "
                                    "portal GMV. Shipping is settled outside this "
                                    "reconciliation and is never deducted."
                                ),
                                format="info",
                            )
                        ]
                    ),
                ],
            ),
            Section(
                kind="rows",
                set="excluded",
                gap=1,
                title="Orders not in the payable, not in the totals",
                lookups=True,
            ),
            Section(
                kind="rows",
                set="statement_only",
                gap=1,
                title="Statement rows with no portal order, not in the totals",
                tolerances=False,
                values=ESHOP_STATEMENT_VALUES,
            ),
        ],
    )
    return _tabs(
        dashboard,
        orders,
        vendor_sheet="ledger",
        # One line per billed order carrying SG-owned shipping, read off the
        # Portal copy: only a portal line says who owns its shipping, and the
        # export puts an order's shipping on its first line. Billed is in the
        # payable and on the statement: an RTO or a clawback is not, a return
        # is, because it was delivered. The unit price is that line's
        # Shipping Cost, ex VAT.
        invoices=erp_invoices_tab(
            "b2c_invoices",
            "B2C Invoices ERP",
            upload="portal",
            where=(
                'AND(IDTEXT(ordernumber) <> "", '
                'NOT(ISBLANK(LOOKUP("billed", UPPER(CLEAN(IDTEXT(ordernumber)))))), '
                'ISTEXT(shippingownership), TRIM(shippingownership) = "sg", '
                "ISNUMBER(shippingcost), shippingcost > 0)"
            ),
            partner="TRIM(TEXT(merchantname))",
            order_id="IDTEXT(ordernumber)",
            bill_date="date",
            product='"eShop Delivery"',
            unit_cell="portal_copy.shippingcost",
        ),
        exception_status="order.statuses",
        texts=[
            Named(key="standard_rate", formula="PERCENT(param.standard_rate, 2)"),
            Named(key="promo_rate", formula="PERCENT(param.promo_rate, 2)"),
            Named(key="promo_cutoff", formula="TEXT(param.promo_cutoff)"),
        ],
    )


# --- Travel -----------------------------------------------------------------


def _vs_vendor(label: str, ours: str, theirs: str) -> BlockRow:
    """Our figure, theirs, and the gap between them."""
    return BlockRow(
        cells=[
            _label(label),
            Cell(col=3, formula=f"={_total(ours)}", format="money"),
            Cell(col=4, formula=f"={_total(theirs)}", format="money"),
            Cell(col=5, formula="=D{row}-E{row}", format="yellow"),
        ]
    )


def _by_vertical(label: str, gross: str, net: str) -> BlockRow:
    return BlockRow(
        cells=[
            _label(label),
            Cell(col=3, metric=gross, format="money"),
            Cell(col=4, metric=net, format="money"),
        ]
    )


def travel_output() -> OutputWorkbook:
    dashboard = OutputTab(
        key="dashboard",
        title="Dashboard",
        kind="dashboard",
        blocks=[
            _dash(
                "SG TRAVEL ORDER Amount",
                TRAVEL_PRIMARY_HEADERS,
                [
                    "portal_base",
                    "vendor_base",
                    "base_difference",
                    "portal_commission",
                    "vendor_commission",
                    "round_off",
                    "net_payable",
                    "wallet",
                    "card",
                    "collection",
                    "portal_total",
                    "collection_gap",
                    "promo",
                    "remaining",
                ],
                first="bookings",
                value_label="Vendor TRAVEL ORDER Amount",
            ),
            _dash(
                "Cash",
                TRAVEL_COLLECTION_HEADERS,
                [
                    "portal_total",
                    "wallet",
                    "card",
                    "collection",
                    "order_collection",
                    "promo",
                    "net_difference",
                ],
            ),
            _dash(
                "Commission at the contract rate",
                TRAVEL_COMMISSION_HEADERS,
                [
                    "hotel_commission",
                    "flight_commission",
                    "commission",
                    "commission_ex_vat",
                    "average_commission",
                ],
            ),
            _dash(
                "Average Commission share",
                TRAVEL_SHARE_HEADERS,
                ["hotel_share", "flight_share"],
                kind="pct",
                tall=False,
            ),
        ],
        # One row per claim Service Provider, over the matched bookings. A
        # booking whose lines name several is counted under each: its own
        # money is shared by their Total Invoice Amount, and its commission is
        # theirs. The TOTAL row counts every booking once, unsplit.
        breakdown=_breakdown(
            TRAVEL_BREAKDOWN_HEADERS,
            over="matched",
            parts="lines",
            name='COALESCE(service_provider, "Unknown")',
            basis="total_invoice",
            totals=[Named(key="line_commission", formula="commission")],
            commission="line_commission",
            extra="net_payable_as_vendor * share",
            revenue="portal.discount * share",
            discounted="OR(portal.discount <> 0, NOT(ISBLANK(portal.promocode)))",
            payment_method="portal.payment_method",
            card="portal.card_amount * share",
            wallet="portal.wallet_amount * share",
            total="whole",
        ),
    )
    orders = OutputTab(
        key="orders",
        title="Orders",
        kind="table",
        columns=_columns(TRAVEL_COLUMNS, TRAVEL_VALUES),
        lookup_columns=finance_lookups(_FINANCE_COPY, key_column="sg_order_id"),
        tolerances={
            "base_diff": "param.base_tolerance",
            "net_diff": "param.base_tolerance",
        },
        sections=[
            Section(kind="rows", set="matched", autofilter=True, lookups=True),
            Section(
                kind="totals",
                set="matched",
                when="matched",
                label="Total, {count} matched",
            ),
            # The client's Sheet1: our figures, theirs, and the gap, then by vertical.
            Section(
                kind="block",
                when="matched",
                gap=1,
                rows=[
                    BlockRow(
                        cells=[
                            _label("Matched bookings"),
                            Cell(col=3, text="SHOPPINGGATE", format="header"),
                            Cell(col=4, text="{vendor_upper}", format="header"),
                            Cell(col=5, text="DIFFERENCE", format="header"),
                        ]
                    ),
                    _vs_vendor("Base Amount", "order_total", "total_invoice"),
                    _vs_vendor(
                        "Commission at the contract rate", "as_vendor", "v_commission"
                    ),
                    _vs_vendor(
                        "Net Payable at the contract rate", "net_as_vendor", "v_net"
                    ),
                    BlockRow(
                        cells=[
                            _label("VAT uplift never charged"),
                            Cell(col=3, formula=f"={_total('uplift')}", format="money"),
                        ]
                    ),
                    BlockRow(),
                    BlockRow(
                        cells=[
                            _label("Commission by vertical"),
                            Cell(col=3, text="Including VAT", format="header"),
                            Cell(col=4, text="Excluding VAT", format="header"),
                        ]
                    ),
                    _by_vertical(
                        "Hotel", "hotel_commission", "hotel_commission_ex_vat"
                    ),
                    _by_vertical(
                        "Flights", "flight_commission", "flight_commission_ex_vat"
                    ),
                    BlockRow(),
                    BlockRow(
                        cells=[
                            Cell(
                                col=0,
                                text=(
                                    "Rates applied: hotel {hotel_rate}, flights "
                                    "{flight_rate}, plus {vat_rate} VAT on the commission "
                                    "of an international booking. Local or international "
                                    "is decided by the VAT on the claim, never by its "
                                    "DOM/INT label."
                                ),
                                format="info",
                            )
                        ]
                    ),
                ],
            ),
            Section(
                kind="rows",
                set="unmatched",
                gap=1,
                title="Bookings not in the totals, with the reason they did not match",
                lookups=True,
            ),
        ],
    )
    return _tabs(
        dashboard,
        orders,
        vendor_sheet="claim",
        # Every matched booking, as the Orders sheet lists them; its unit price
        # is its Commission Charged cell.
        invoices=erp_invoices_tab(
            "b2c_invoices",
            "B2C Invoices ERP",
            rows="matched",
            partner="TRIM(TEXT(portal.provider))",
            order_id="key",
            contacts=Contacts(upload="portal", row="portal"),
            bill_date="portal.order_date",
            product='IF(portal.vertical = "hotel", "Hotel Booking", "Flight Booking")',
            unit_cell="orders.commission_charged",
        ),
        exception_status="order.portal_status",
        texts=[
            Named(key="vendor_upper", formula='UPPER(COALESCE(vendor_name, "Vendor"))'),
            *(
                Named(key=f"{rate}_rate", formula=f"PERCENT(param.{rate}_rate, 2)")
                for rate in ("hotel", "flight", "vat")
            ),
        ],
    )


# --- Food -------------------------------------------------------------------

# The client's summary measures: title, formula over the totals row, highlighted.
_FOOD_MEASURES = (
    ("Food Amount", "={sub}", False),
    ("Delivery", "={delivery}", False),
    ("ADD DC", "={add_dc}", False),
    ("Round of differ", "={fees}-{commission}", False),
    ("COM", "=-{commission}", False),
    (
        "Total Order Amount",
        "={sub}+{delivery}+{add_dc}-{commission}-({fees}-{commission})+({chef}-{sub})",
        True,
    ),
    ("Wallet Amount", "={wallet}", False),
    ("Applepay/Card Amount", "={card}", False),
    ("Total collection", "={wallet}+{card}", False),
    ("Portal total", "={portal_total}", False),
    ("T. Order - Collection Dif", "={wallet}+{card}-{portal_total}", False),
    ("sg promo code disc", "={discount}", False),
    ("T. Remaining", "={discount}+{wallet}+{card}-{portal_total}", False),
)
_FOOD_REFS = {
    "sub": _total("sub_total"),
    "chef": _total("chef_total"),
    "delivery": _total("sg_delivery"),
    "add_dc": _total("sg_bear"),
    "fees": _total("v_fees"),
    "commission": _total("commission_charged"),
    "wallet": _total("wallet_amount"),
    "card": _total("card_amount"),
    "portal_total": _total("total_amount"),
    "discount": _total("discount"),
}


def food_output() -> OutputWorkbook:
    dashboard = OutputTab(
        key="dashboard",
        title="Dashboard",
        kind="dashboard",
        blocks=[
            _dash(
                "SG FOOD ORDER Amount",
                FOOD_PRIMARY_HEADERS,
                [
                    "food",
                    "customer_delivery",
                    "sg_delivery",
                    "sg_commission",
                    "vendor_fees",
                    "road_off",
                    "net_payable",
                    "wallet",
                    "card",
                    "collection",
                    "portal_total",
                    "collection_gap",
                    "promo",
                    "remaining",
                ],
                first="orders",
                value_label="Vendor FOOD ORDER Amount",
            ),
            _dash(
                "Cash",
                FOOD_COLLECTION_HEADERS,
                [
                    "portal_total",
                    "wallet",
                    "card",
                    "collection",
                    "order_collection",
                    "promo",
                    "net_difference",
                ],
            ),
            _dash(
                "Delivery",
                FOOD_DELIVERY_HEADERS,
                [
                    "customer_delivery",
                    "sg_delivery",
                    "merchant_delivery",
                    "delivery_total",
                    "average_delivery",
                ],
            ),
            _dash(
                "Average Delivery share",
                FOOD_SHARE_HEADERS,
                ["customer_share", "sg_share", "merchant_share"],
                kind="pct",
                tall=False,
            ),
        ],
        # One row per restaurant, over the orders the client dashboard counts.
        breakdown=_breakdown(
            FOOD_BREAKDOWN_HEADERS,
            over="client",
            name='COALESCE(portal.restaurant_name, vendor.brand_name, "Unknown")',
            commission="vendor.shoppinggate_fees",
            extra="client_customer + client_sg_share + vendor.chef_delivery_charge",
            paid="NOT(ISBLANK(portal))",
            revenue="portal.discount",
            discounted="OR(portal.discount <> 0, NOT(ISBLANK(portal.promocode)))",
            payment_method="portal.payment_method",
            card="portal.card_amount",
            wallet="portal.wallet_amount",
        ),
    )
    measures = [
        (title, formula.format_map(_FOOD_REFS), yellow)
        for title, formula, yellow in _FOOD_MEASURES
    ]
    orders = OutputTab(
        key="orders",
        title="Orders",
        kind="table",
        columns=_columns(FOOD_COLUMNS, FOOD_VALUES),
        lookup_columns=finance_lookups(_FINANCE_COPY, key_column="sg_order_id"),
        sections=[
            Section(
                kind="rows",
                set="orders",
                autofilter=True,
                tolerances=False,
                lookups=True,
            ),
            Section(
                kind="totals",
                set="orders",
                when="orders",
                formats={
                    "sg_bear": "yellow",
                    "chefz_bear": "yellow",
                    "cust_del": "yellow",
                },
            ),
            Section(
                kind="block",
                when="orders",
                rows=[
                    # The three delivery shares add to the gross fee.
                    BlockRow(
                        cells=[
                            Cell(
                                col="sg_bear",
                                to_col="cust_del",
                                formula=(
                                    f"={_total('sg_bear')}+{_total('chefz_bear')}"
                                    f"+{_total('cust_del')}"
                                ),
                                format="yellow",
                            )
                        ]
                    ),
                    # Net payable + food gap + SG delivery + fee difference.
                    BlockRow(
                        cells=[
                            Cell(
                                col="net_payable",
                                formula=(
                                    f"={_total('net_payable')}+({_total('chef_total')}"
                                    f"-{_total('sub_total')})+{_total('sg_bear')}"
                                    f"+{_total('fee_diff')}"
                                ),
                                format="total",
                            )
                        ]
                    ),
                    BlockRow(),
                    BlockRow(
                        cells=[
                            _label("SG FOOD ORDER Amount"),
                            Cell(
                                col=3, formula=f"={_total('sub_total')}", format="money"
                            ),
                        ]
                    ),
                    BlockRow(
                        cells=[
                            _label("Vendor FOOD ORDER Amount"),
                            Cell(
                                col=3,
                                formula=f"={_total('chef_total')}",
                                format="money",
                            ),
                            *(
                                Cell(
                                    col=4 + offset,
                                    text=title,
                                    format="yellow_text" if yellow else "label",
                                )
                                for offset, (title, _formula, yellow) in enumerate(
                                    measures
                                )
                            ),
                        ]
                    ),
                    BlockRow(
                        cells=[
                            Cell(
                                col=3,
                                formula=f"={_total('chef_total')}-{_total('sub_total')}",
                                format="yellow",
                            ),
                            *(
                                Cell(
                                    col=4 + offset,
                                    formula=formula,
                                    format="yellow" if yellow else "money",
                                )
                                for offset, (_title, formula, yellow) in enumerate(
                                    measures
                                )
                            ),
                        ]
                    ),
                ],
            ),
        ],
    )
    return _tabs(
        dashboard,
        orders,
        vendor_sheet="orders",
        # Every order on the Orders sheet; its unit price is its SG Delivery cell.
        invoices=erp_invoices_tab(
            "b2c_invoices",
            "B2C Invoices ERP",
            rows="orders",
            partner="TRIM(TEXT(portal.provider))",
            order_id="key",
            contact="vendor.customer_name",
            contacts=Contacts(upload="portal", key="partnerorderid", match="TEXT(key)"),
            bill_date="portal.order_date",
            product='"Food Delivery"',
            unit_cell="orders.sg_delivery",
        ),
        # The order's status on each side; a row never paired names its own.
        exception_status=(
            "IF(ISBLANK(order), "
            'REGEX(detail, "^(?:Portal|Vendor) status (.+) is not Delivered\\.$", 1), '
            "order.status_text)"
        ),
    )


OUTPUTS = {"eshop": eshop_output, "travel": travel_output, "food": food_output}
