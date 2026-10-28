"""The food Excel report (plan-food.md section 8).

The Orders sheet follows the client's ANALYSIS & RECON layout: portal columns,
the six checks, then the vendor columns, with their totals and the summary
block from the bottom of that sheet. Only matched orders that are not
cancelled are listed, so the totals leave out vendor rows with no portal
order. Those rows, and cancelled orders, stay on the Exceptions sheet.

The Portal and Vendor sheets are the uploads themselves, copied row for row,
including a title row and columns the recon does not use. They are a record of
what was read, not a second reconciliation.

When a finance report is uploaded, it is copied as its own sheet and the
Orders sheet looks each row's SG Order ID up in that sheet's Order ID. The
five cashback columns are formulas, so opening the cell shows the lookup.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter

from app.recon.categories.food import parser
from app.recon.categories.food.models import FoodOrder, FoodResult, FoodVerdict
from app.recon.cleaning import money
from app.recon.excel import SheetFormatError, detect_sheets, find_header, open_workbook
from app.recon.exceptions import ReconException
from app.recon.models import SheetManifest
from app.recon.sheets import (
    PORTAL_TITLE,
    VENDOR_TITLE,
    BreakdownEntry,
    CashbackLine,
    DashBlock,
    breakdown_total,
    cashback_sheet,
    exceptions_sheet,
    letter,
    order_id_text,
    portal_records,
    report_formats,
    source_sheets,
    wallet_sheet,
    write_cell,
    write_column_headers,
    write_dashboard,
)

# key, header, width, group (portal / check / vendor / meta), kind
_COLUMNS: tuple[tuple[str, str, int, str, str], ...] = (
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

# After the vendor columns. The last two names are not on the client's draft;
# Reward Type and Outcome are the two facts that sit beside the reward.
_CASHBACK_COLUMNS: tuple[tuple[str, str, int, str, str], ...] = (
    ("cashback_reward", "Cash Back Reward", 18, "cashback", "money"),
    ("vertical_name", "Vertical Name", 18, "cashback", "text"),
    ("reward_code", "Reward Code", 16, "cashback", "text"),
    ("reward_type", "Reward Type", 14, "cashback", "text"),
    ("outcome", "Outcome", 12, "cashback", "text"),
)
_CASHBACK_FIELDS = (
    "reward_amount",
    "vertical_name",
    "reward_code",
    "reward_type",
    "outcome",
)
FINANCE_TITLE = "Finance Report"

_COL = {spec[0]: index for index, spec in enumerate(_COLUMNS)}


def build_report(
    result: FoodResult,
    path: Path,
    sources: dict[str, Path] | None = None,
) -> Path:
    """Write the report. ``sources`` maps ``portal`` and ``vendor`` to the uploads.

    Portal, Vendor, and B2C Invoices ERP are appended only when the paths are
    supplied. The download route always supplies them; a caller that only has
    ``FoodResult`` still gets Dashboard, Orders, and Exceptions. Wallet follows
    Exceptions when a wallet history was uploaded. B2C invoices are the matched
    orders that are not cancelled, and their unit price reads the Orders sheet.
    A ``finance`` path adds the cashback columns and a copy of that report.
    """
    finance = finance_layout(sources.get("finance")) if sources else None
    book = xlsxwriter.Workbook(
        str(path),
        {
            "constant_memory": True,
            "in_memory": False,
            "default_date_format": "yyyy-mm-dd",
        },
    )
    try:
        fmt = report_formats(book)
        fmt["cashback_header"] = book.add_format(
            {
                "bold": True,
                "bg_color": "#1F4E79",
                "font_color": "white",
                "border": 1,
                "text_wrap": True,
                "valign": "vcenter",
            }
        )
        _dashboard_sheet(book, fmt, result)
        _orders_sheet(book, fmt, result, finance)
        _exceptions_sheet(book, fmt, result)
        if result.wallet is not None:
            wallet_sheet(book, fmt, result.wallet)
        if sources:
            _b2c_invoices_sheet(book, fmt, result, sources)
            source_sheets(
                book,
                fmt,
                result.detected,
                sources,
                (
                    ("portal", PORTAL_TITLE, parser.PORTAL_SCHEMA, "portal_header"),
                    ("vendor", VENDOR_TITLE, parser.VENDOR_SCHEMA, "vendor_header"),
                ),
            )
            if finance is not None:
                source_sheets(
                    book,
                    fmt,
                    [
                        SheetManifest(
                            role="finance",
                            filename="finance.xlsx",
                            sheets=finance.sheets,
                            rows=0,
                        )
                    ],
                    sources,
                    (
                        (
                            "finance",
                            FINANCE_TITLE,
                            parser.FINANCE_SCHEMA,
                            "cashback_header",
                        ),
                    ),
                )
    finally:
        book.close()
    return path


# The client dashboard reads every paired order and every vendor-only order.
# Portal-only orders stay off it, the same way the Orders totals leave them out.
_PRIMARY_HEADERS = (
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
_COLLECTION_HEADERS = (
    "Total Order Amount",
    "Wallets",
    "Other Method",
    "Collection",
    "Order-Collection",
    "Promo Discount",
    "Net Difference",
)
_DELIVERY_HEADERS = (
    "Customer Share",
    "Shoppinggate Share",
    "Merchant Share",
    "TOTAL",
    "Average Per Order",
)
_SHARE_HEADERS = ("By Customer", "By shoppinggate", "By Vendor")
_BREAKDOWN_HEADERS = (
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


def _dashboard_sheet(book: Any, fmt: dict[str, Any], result: FoodResult) -> None:
    view = _dashboard_view(result)
    blocks = (
        DashBlock(
            label="SG FOOD ORDER Amount",
            value_label="Vendor FOOD ORDER Amount",
            headers=_PRIMARY_HEADERS,
            values=(
                (view.orders, "int"),
                (view.food, "money"),
                (view.customer_delivery, "money"),
                (view.sg_delivery, "money"),
                (view.sg_commission, "money"),
                (view.vendor_fees, "money"),
                (view.road_off, "money"),
                (view.net_payable, "money"),
                (view.wallet, "money"),
                (view.card, "money"),
                (view.collection, "money"),
                (view.portal_total, "money"),
                (view.collection_gap, "money"),
                (view.promo, "money"),
                (view.remaining, "money"),
            ),
        ),
        DashBlock(
            label="Cash",
            headers=_COLLECTION_HEADERS,
            values=(
                (view.portal_total, "money"),
                (view.wallet, "money"),
                (view.card, "money"),
                (view.collection, "money"),
                (view.order_collection, "money"),
                (view.promo, "money"),
                (view.net_difference, "money"),
            ),
        ),
        DashBlock(
            label="Delivery",
            headers=_DELIVERY_HEADERS,
            values=(
                (view.customer_delivery, "money"),
                (view.sg_delivery, "money"),
                (view.merchant_delivery, "money"),
                (view.delivery_total, "money"),
                (view.average_delivery, "money"),
            ),
        ),
        DashBlock(
            label="Average Delivery share",
            headers=_SHARE_HEADERS,
            values=(
                (view.customer_share, "pct"),
                (view.sg_share, "pct"),
                (view.merchant_share, "pct"),
            ),
            tall=False,
        ),
    )
    write_dashboard(
        book,
        fmt,
        blocks,
        _BREAKDOWN_HEADERS,
        breakdown_total(view.restaurants),
        view.restaurants,
    )


@dataclass
class _DashboardView:
    orders: int = 0
    food: Decimal = Decimal(0)
    customer_delivery: Decimal = Decimal(0)
    sg_delivery: Decimal = Decimal(0)
    merchant_delivery: Decimal = Decimal(0)
    sg_commission: Decimal = Decimal(0)
    vendor_fees: Decimal = Decimal(0)
    wallet: Decimal = Decimal(0)
    card: Decimal = Decimal(0)
    portal_total: Decimal = Decimal(0)
    promo: Decimal = Decimal(0)
    restaurants: list[BreakdownEntry] = field(default_factory=list)

    @property
    def road_off(self) -> Decimal:
        return self.vendor_fees - self.sg_commission

    @property
    def net_payable(self) -> Decimal:
        return self.food + self.customer_delivery + self.sg_delivery - self.vendor_fees

    @property
    def collection(self) -> Decimal:
        return self.wallet + self.card

    @property
    def collection_gap(self) -> Decimal:
        return self.collection - self.portal_total

    @property
    def remaining(self) -> Decimal:
        return self.promo + self.collection - self.portal_total

    @property
    def order_collection(self) -> Decimal:
        return self.portal_total - self.collection

    @property
    def net_difference(self) -> Decimal:
        return self.order_collection - self.promo

    @property
    def delivery_total(self) -> Decimal:
        return self.customer_delivery + self.sg_delivery + self.merchant_delivery

    @property
    def average_delivery(self) -> Decimal:
        if self.orders == 0:
            return Decimal(0)
        return self.delivery_total / self.orders

    def _share(self, part: Decimal) -> Decimal:
        if self.delivery_total == 0:
            return Decimal(0)
        return part / self.delivery_total

    @property
    def customer_share(self) -> Decimal:
        return self._share(self.customer_delivery)

    @property
    def sg_share(self) -> Decimal:
        return self._share(self.sg_delivery)

    @property
    def merchant_share(self) -> Decimal:
        return self._share(self.merchant_delivery)


def _client_orders(result: FoodResult) -> list[FoodOrder]:
    return [
        order
        for order in result.orders
        if _paired(order) or order.verdict is FoodVerdict.VENDOR_ONLY
    ]


def _dashboard_view(result: FoodResult) -> _DashboardView:
    """The client dashboard, over paired orders plus vendor-only orders."""
    view = _DashboardView()
    restaurants: dict[str, BreakdownEntry] = {}
    for order in _client_orders(result):
        portal = order.portal
        vendor = order.vendor
        if portal is not None and vendor is not None:
            customer = portal.sg_delivery
            sg_share = order.checks.sg_delivery_share or Decimal(0)
            merchant = vendor.chef_delivery_charge
            food = portal.sub_total
            commission = portal.commission_charged
            fees = vendor.shoppinggate_fees
            name = portal.restaurant_name or vendor.brand_name or "Unknown"
        else:
            assert vendor is not None
            customer = _vendor_customer(order)
            sg_share = vendor.sg_delivery_charge
            merchant = vendor.chef_delivery_charge
            food = vendor.order_chef_total
            commission = vendor.shoppinggate_fees
            fees = vendor.shoppinggate_fees
            name = vendor.brand_name or "Unknown"
            portal = None

        view.orders += 1
        view.food += food
        view.customer_delivery += customer
        view.sg_delivery += sg_share
        view.merchant_delivery += merchant
        view.sg_commission += commission
        view.vendor_fees += fees

        entry = restaurants.get(name)
        if entry is None:
            entry = BreakdownEntry(name=name)
            restaurants[name] = entry
        entry.orders += 1
        entry.commission += fees
        entry.extra += customer + sg_share + merchant
        if portal is not None:
            view.wallet += portal.wallet_amount
            view.card += portal.card_amount
            view.portal_total += portal.total_amount
            view.promo += portal.discount
            entry.revenue += portal.discount
            if portal.discount != 0 or portal.promocode:
                entry.discounts += 1
            entry.add_payment(
                portal.payment_method, portal.card_amount, portal.wallet_amount
            )

    view.food = money(view.food)
    view.customer_delivery = money(view.customer_delivery)
    view.sg_delivery = money(view.sg_delivery)
    view.merchant_delivery = money(view.merchant_delivery)
    view.sg_commission = money(view.sg_commission)
    view.vendor_fees = money(view.vendor_fees)
    view.wallet = money(view.wallet)
    view.card = money(view.card)
    view.portal_total = money(view.portal_total)
    view.promo = money(view.promo)
    for entry in restaurants.values():
        entry.quantize()
    view.restaurants = sorted(
        restaurants.values(), key=lambda item: (-item.orders, item.name)
    )
    return view


@dataclass(frozen=True)
class _FinanceLayout:
    """The copied report's sheets, and which column holds each lookup field."""

    sheets: list[str]
    columns: dict[str, int]


def finance_layout(path: Path | None) -> _FinanceLayout | None:
    """The spin table inside a finance upload, or None when none was supplied.

    Order ID is unique on the report, so the first exact match is the reward.
    A worked recon file that already has the reward columns bolted on does not
    match: it has no Spin Event ID.
    """
    if path is None:
        return None
    with open_workbook(path) as workbook:
        names = detect_sheets(workbook, parser.FINANCE_SCHEMA)
        if not names:
            raise SheetFormatError(
                "No finance report table found in 'finance'. Expected columns "
                "Spin Event ID, Order ID, Reward Amount (SAR), Reward Code, "
                "Reward Type, Outcome and Vertical Name."
            )
        match = find_header(workbook[names[0]], parser.FINANCE_SCHEMA)
        if match is None:
            raise SheetFormatError(
                "No finance report table found in 'finance'. Expected columns "
                "Spin Event ID, Order ID, Reward Amount (SAR), Reward Code, "
                "Reward Type, Outcome and Vertical Name."
            )
        missing = match.missing(parser.FINANCE_SCHEMA)
        if missing:
            raise SheetFormatError(
                "'finance' is missing column(s): " + ", ".join(missing)
            )
        return _FinanceLayout(sheets=list(names), columns=dict(match.columns))


def _orders_sheet(
    book: Any,
    fmt: dict[str, Any],
    result: FoodResult,
    finance: _FinanceLayout | None,
) -> None:
    columns = _COLUMNS + _CASHBACK_COLUMNS if finance is not None else _COLUMNS
    sheet = book.add_worksheet("Orders")
    write_column_headers(sheet, fmt, columns)

    included = _orders_on_sheet(result)
    for offset, order in enumerate(included):
        row = 1 + offset
        _write_order(sheet, fmt, row, order)
        if finance is not None:
            _write_cashback(sheet, fmt, row, finance)

    first = 2
    last = 1 + len(included)
    totals = last  # 0-based row index of the totals row; excel row is totals + 1
    if included:
        _write_totals(sheet, fmt, totals, first, last)
        _write_delivery_sum(sheet, fmt, totals + 1, totals)
        _write_paired_total(sheet, fmt, totals + 2, totals)
        _write_client_summary(sheet, fmt, totals + 4, totals)
    if included:
        sheet.autofilter(0, 0, last - 1, len(columns) - 1)


def _write_cashback(
    sheet: Any, fmt: dict[str, Any], row: int, finance: _FinanceLayout
) -> None:
    """Five lookups of this row's SG Order ID. A blank id, or no reward, is NA."""
    excel_row = row + 1
    key = letter(finance.columns["order_id"])
    paired = zip(_CASHBACK_COLUMNS, _CASHBACK_FIELDS, strict=True)
    for offset, (spec, field_name) in enumerate(paired):
        formula = _cashback_formula(excel_row, key, letter(finance.columns[field_name]))
        style = fmt["money"] if spec[4] == "money" else None
        sheet.write_formula(row, len(_COLUMNS) + offset, formula, style)


def _cashback_formula(excel_row: int, key_col: str, return_col: str) -> str:
    """Exact lookup. SG Order ID is text; the report's Order ID may be a number."""
    cell = f"{letter(_COL['sg_order_id'])}{excel_row}"
    lookup = f"'{FINANCE_TITLE}'!{key_col}:{key_col}"
    found = f"'{FINANCE_TITLE}'!{return_col}:{return_col}"
    number = f"IFERROR(VALUE({cell}),{cell})"
    return (
        f'=IF({cell}="","NA",IFNA('
        f"XLOOKUP({number},{lookup},{found},NA(),0),"
        f'XLOOKUP({cell}&"",{lookup},{found},"NA",0)))'
    )


# ``rules._not_delivered`` writes this sentence for a row that never becomes an order.
_NOT_DELIVERED_STATUS = re.compile(
    r"^(?:Portal|Vendor) status (?P<status>.+) is not Delivered\.$"
)


def _exceptions_sheet(book: Any, fmt: dict[str, Any], result: FoodResult) -> None:
    statuses = {order.order_id: _order_status(order) for order in result.orders}
    exceptions_sheet(
        book,
        fmt,
        result.exceptions,
        lambda exc: _exception_status(exc, statuses),
    )


def _order_status(order: FoodOrder) -> str:
    """The order status on each side we have. One value when the two agree."""
    portal = order.portal.order_status if order.portal is not None else None
    vendor = order.vendor.order_status if order.vendor is not None else None
    if portal and vendor and portal.casefold() != vendor.casefold():
        return f"Portal: {portal} / Vendor: {vendor}"
    return portal or vendor or ""


def _exception_status(exc: ReconException, statuses: dict[str, str]) -> str:
    if exc.order_no and exc.order_no in statuses:
        return statuses[exc.order_no]
    match = _NOT_DELIVERED_STATUS.match(exc.detail)
    return match.group("status") if match else ""


_INVOICE_SHEET = "B2C Invoices ERP"
_INVOICE_PRODUCT = "Food Delivery"
_CANCELLED = frozenset({"cancelled", "canceled"})


def _b2c_invoices_sheet(
    book: Any,
    fmt: dict[str, Any],
    result: FoodResult,
    sources: dict[str, Path],
) -> None:
    """Matched orders that are not cancelled. Unit price reads the Orders sheet."""
    contacts = _invoice_contacts(result, sources)
    delivery = letter(_COL["sg_delivery"])
    lines: list[CashbackLine] = []
    for excel_row, order in enumerate(_orders_on_sheet(result), start=2):
        if not _paired(order) or _cancelled(order):
            continue
        portal = order.portal
        assert portal is not None
        lines.append(
            CashbackLine(
                partner=(portal.provider or "").strip(),
                order_id=order.order_id,
                contact=contacts.get(order.order_id, ""),
                bill_date=portal.order_date,
                product=_INVOICE_PRODUCT,
                unit_cell=f"Orders!{delivery}{excel_row}",
            )
        )
    cashback_sheet(book, fmt, lines, title=_INVOICE_SHEET)


def _orders_on_sheet(result: FoodResult) -> list[FoodOrder]:
    """Matched orders that are not cancelled, in workbook order.

    A vendor row with no portal order has MATCH ID'S false. Leaving it on
    this sheet puts its amounts into the totals. It is already an exception.
    """
    return [
        order for order in result.orders if _paired(order) and not _cancelled(order)
    ]


def _cancelled(order: FoodOrder) -> bool:
    statuses = []
    if order.portal is not None:
        statuses.append(order.portal.order_status)
    if order.vendor is not None:
        statuses.append(order.vendor.order_status)
    return any(status.casefold() in _CANCELLED for status in statuses)


def _invoice_contacts(result: FoodResult, sources: dict[str, Path]) -> dict[str, str]:
    contacts = {
        order.order_id: order.vendor.customer_name
        for order in result.orders
        if order.vendor is not None and order.vendor.customer_name
    }
    path = sources.get("portal")
    names = {item.role: item.sheets for item in result.detected}.get("portal") or []
    if path is None or not names:
        return contacts
    for record in portal_records(path, names, parser.PORTAL_SCHEMA):
        order_id = order_id_text(record.get("partnerorderid"))
        contact = record.contact()
        if order_id and contact:
            contacts[order_id] = contact
    return contacts


def _paired(order: FoodOrder) -> bool:
    return (
        order.portal is not None and order.vendor is not None and order.checks.ids_match
    )


def _write_order(sheet: Any, fmt: dict[str, Any], row: int, order: FoodOrder) -> None:
    for col, value in enumerate(_order_values(order)):
        write_cell(sheet, fmt, row, col, value, _COLUMNS[col][3], _COLUMNS[col][4])


def _write_totals(
    sheet: Any, fmt: dict[str, Any], row: int, first: int, last: int
) -> None:
    money_keys = {
        key for key, _header, _width, _group, kind in _COLUMNS if kind == "money"
    }
    diff_keys = {"amount_diff", "fee_diff"}
    for key in money_keys | diff_keys:
        col = _COL[key]
        ref = letter(col)
        formula = f"=SUM({ref}{first}:{ref}{last})"
        if key in {"sg_bear", "chefz_bear", "cust_del"}:
            style = "yellow"
        else:
            style = "total"
        sheet.write_formula(row, col, formula, fmt[style])


def _write_delivery_sum(sheet: Any, fmt: dict[str, Any], row: int, totals: int) -> None:
    """The yellow 5,089-style line: the three delivery shares add to the gross fee."""
    excel = totals + 1
    sg = letter(_COL["sg_bear"])
    chefz = letter(_COL["chefz_bear"])
    customer = letter(_COL["cust_del"])
    sheet.merge_range(
        row,
        _COL["sg_bear"],
        row,
        _COL["cust_del"],
        f"={sg}{excel}+{chefz}{excel}+{customer}{excel}",
        fmt["yellow"],
    )


def _write_paired_total(sheet: Any, fmt: dict[str, Any], row: int, totals: int) -> None:
    """Client row 306: net payable + food gap + SG delivery + fee difference."""
    excel = totals + 1
    net = letter(_COL["net_payable"])
    chef = letter(_COL["chef_total"])
    sub = letter(_COL["sub_total"])
    sg = letter(_COL["sg_bear"])
    fee = letter(_COL["fee_diff"])
    sheet.write_formula(
        row,
        _COL["net_payable"],
        f"={net}{excel}+({chef}{excel}-{sub}{excel})+{sg}{excel}+{fee}{excel}",
        fmt["total"],
    )


def _write_client_summary(
    sheet: Any, fmt: dict[str, Any], row: int, totals: int
) -> None:
    """Client rows 311-313: two labels, then the measures across one row."""
    excel = totals + 1

    def ref(key: str) -> str:
        return f"{letter(_COL[key])}{excel}"

    sub = ref("sub_total")
    chef = ref("chef_total")
    delivery = ref("sg_delivery")
    add_dc = ref("sg_bear")
    fees = ref("v_fees")
    commission = ref("commission_charged")
    wallet = ref("wallet_amount")
    card = ref("card_amount")
    portal_total = ref("total_amount")
    discount = ref("discount")
    measures: tuple[tuple[str, str, bool], ...] = (
        ("Food Amount", f"={sub}", False),
        ("Delivery", f"={delivery}", False),
        ("ADD DC", f"={add_dc}", False),
        ("Round of differ", f"={fees}-{commission}", False),
        ("COM", f"=-{commission}", False),
        (
            "Total Order Amount",
            (
                f"={sub}+{delivery}+{add_dc}-{commission}-({fees}-{commission})"
                f"+({chef}-{sub})"
            ),
            True,
        ),
        ("Wallet Amount", f"={wallet}", False),
        ("Applepay/Card Amount", f"={card}", False),
        ("Total collection", f"={wallet}+{card}", False),
        ("Portal total", f"={portal_total}", False),
        ("T. Order - Collection Dif", f"={wallet}+{card}-{portal_total}", False),
        ("sg promo code disc", f"={discount}", False),
        ("T. Remaining", f"={discount}+{wallet}+{card}-{portal_total}", False),
    )

    sheet.merge_range(row, 0, row, 2, "SG FOOD ORDER Amount", fmt["label"])
    sheet.write_formula(row, 3, f"={sub}", fmt["money"])
    sheet.merge_range(row + 1, 0, row + 1, 2, "Vendor FOOD ORDER Amount", fmt["label"])
    sheet.write_formula(row + 1, 3, f"={chef}", fmt["money"])
    for offset, (title, _formula, highlight) in enumerate(measures):
        style = "yellow_text" if highlight else "label"
        sheet.write(row + 1, 4 + offset, title, fmt[style])
    sheet.write_formula(row + 2, 3, f"={chef}-{sub}", fmt["yellow"])
    for offset, (_title, formula, highlight) in enumerate(measures):
        style = "yellow" if highlight else "money"
        sheet.write_formula(row + 2, 4 + offset, formula, fmt[style])


def _order_values(order: FoodOrder) -> list[Any]:
    portal = order.portal
    vendor = order.vendor
    checks = order.checks
    values: dict[str, Any] = {
        "order_id": order.order_id,
        "verdict": order.verdict.value,
    }
    if portal is not None:
        values.update(
            {
                "order_date": portal.order_date,
                "order_time": portal.order_time,
                "sg_order_id": portal.sg_order_id,
                "vertical": portal.vertical,
                "provider": portal.provider,
                "food_order_type": portal.food_order_type,
                "restaurant_id": portal.restaurant_id,
                "restaurant_name": portal.restaurant_name,
                "transaction_id": portal.transaction_id,
                "order_status": portal.order_status,
                "payment_status": portal.payment_status,
                "payment_method": portal.payment_method,
                "sub_total": portal.sub_total,
                "promocode": portal.promocode,
                "discount": portal.discount,
                "sg_delivery": portal.sg_delivery,
                "vat": portal.vat,
                "order_total": portal.order_total,
                "total_amount": portal.total_amount,
                "stated_rate": portal.stated_rate,
                "commission_amount": portal.commission_amount,
                "partner_payout": portal.partner_payout,
                "commission_charged": portal.commission_charged,
                "wallet_amount": portal.wallet_amount,
                "card_amount": portal.card_amount,
                "refund_amount": portal.refund_amount,
                "compensation_amount": portal.compensation_amount,
                "net_payable": portal.net_payable,
            }
        )
    if vendor is not None:
        values.update(
            {
                "v_date": vendor.requested_delivery_date,
                "brand_name": vendor.brand_name,
                "v_order_id": vendor.order_id,
                "order_no": vendor.order_no,
                "customer_name": vendor.customer_name,
                "v_total": vendor.total,
                "chef_total": vendor.order_chef_total,
                "v_rate": vendor.shoppinggate_percentage,
                "v_fees": vendor.shoppinggate_fees,
                "orig_delivery": vendor.original_delivery_fee,
                "sg_charge": vendor.sg_delivery_charge,
                "delivery_fee": vendor.delivery_fee,
                "v_status": vendor.order_status,
                "order_items": vendor.order_items,
                "promo_discount": vendor.promotion_discount,
                "chef_charge": vendor.chef_delivery_charge,
                "due": vendor.due_from_shoppinggate,
                "net_invoice": vendor.net_invoice,
                "net_delivery": vendor.net_delivery_invoice,
            }
        )
    if portal is not None and vendor is not None:
        values.update(
            {
                "match": "TRUE" if checks.ids_match else "FALSE",
                "amount_diff": checks.order_amount_difference,
                "fee_diff": checks.fee_difference,
                "sg_bear": checks.sg_delivery_share,
                "chefz_bear": checks.chef_delivery_share,
                "cust_del": checks.customer_delivery,
            }
        )
    elif vendor is not None:
        # No portal row. The client sheet still totals these orders, and every
        # sample row of this kind matches on food and fees, so the vendor's own
        # food, fees, and delivery split fill the columns the totals read.
        customer = (
            vendor.original_delivery_fee
            - vendor.sg_delivery_charge
            - vendor.chef_delivery_charge
        )
        values.update(
            {
                "sub_total": vendor.order_chef_total,
                "sg_delivery": customer,
                "commission_charged": vendor.shoppinggate_fees,
                "net_payable": vendor.total - vendor.shoppinggate_fees,
                "match": "FALSE",
                "amount_diff": Decimal(0),
                "fee_diff": Decimal(0),
                "sg_bear": vendor.sg_delivery_charge,
                "chefz_bear": vendor.chef_delivery_charge,
                "cust_del": customer,
            }
        )
    else:
        values["match"] = "FALSE"
    return [values.get(spec[0]) for spec in _COLUMNS]


def _vendor_customer(order: FoodOrder) -> Decimal:
    vendor = order.vendor
    assert vendor is not None
    return (
        vendor.original_delivery_fee
        - vendor.sg_delivery_charge
        - vendor.chef_delivery_charge
    )


class _Figures:
    def __init__(self, result: FoodResult) -> None:
        paired = [order for order in result.orders if _paired(order)]
        vendor_only = [
            order for order in result.orders if order.verdict is FoodVerdict.VENDOR_ONLY
        ]

        def total(orders: list[FoodOrder], pick: Any) -> Decimal:
            return sum((pick(order) for order in orders), Decimal(0))

        subtotal = total(paired, lambda order: order.portal.sub_total) + total(
            vendor_only, lambda order: order.vendor.order_chef_total
        )
        chef_total = total(paired, lambda order: order.vendor.order_chef_total) + total(
            vendor_only, lambda order: order.vendor.order_chef_total
        )
        customer = total(paired, lambda order: order.portal.sg_delivery) + total(
            vendor_only, _vendor_customer
        )
        sg_bear = total(
            paired, lambda order: order.checks.sg_delivery_share or Decimal(0)
        ) + total(vendor_only, lambda order: order.vendor.sg_delivery_charge)
        commission = total(
            paired, lambda order: order.portal.commission_charged
        ) + total(vendor_only, lambda order: order.vendor.shoppinggate_fees)
        fees = total(paired, lambda order: order.vendor.shoppinggate_fees) + total(
            vendor_only, lambda order: order.vendor.shoppinggate_fees
        )
        net_payable = total(paired, lambda order: order.portal.net_payable) + total(
            vendor_only,
            lambda order: order.vendor.total - order.vendor.shoppinggate_fees,
        )
        fee_diff = total(
            paired, lambda order: order.checks.fee_difference or Decimal(0)
        )
        food_gap = chef_total - subtotal

        self.subtotal = money(subtotal)
        self.chef_total = money(chef_total)
        self.food_gap = money(food_gap)
        self.customer_delivery = money(customer)
        self.sg_bear = money(sg_bear)
        self.commission = money(-commission)
        self.round_off = money(fees - commission)
        self.total_order_amount = money(chef_total + customer + sg_bear - fees)
        self.headline = money(net_payable + food_gap + sg_bear + fee_diff)


def _figures(result: FoodResult) -> _Figures:
    return _Figures(result)
