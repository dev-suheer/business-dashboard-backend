"""The food Excel report (plan-food.md section 8).

The Orders sheet follows the client's ANALYSIS & RECON layout: portal columns,
the six checks, then the vendor columns, with their totals and the summary
block from the bottom of that sheet. One-sided orders stay on the sheet and
out of those totals.
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter

from app.recon.categories.food.models import FoodOrder, FoodResult, FoodVerdict
from app.recon.cleaning import money
from app.recon.exceptions import Severity
from app.recon.xlsx import formats, heading, kv, num, table

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

_COL = {spec[0]: index for index, spec in enumerate(_COLUMNS)}


def build_report(result: FoodResult, path: Path) -> Path:
    book = xlsxwriter.Workbook(
        str(path),
        {
            "constant_memory": True,
            "in_memory": False,
            "default_date_format": "yyyy-mm-dd",
        },
    )
    try:
        fmt = _formats(book)
        _summary_sheet(book, fmt, result)
        _orders_sheet(book, fmt, result)
        _exceptions_sheet(book, fmt, result)
    finally:
        book.close()
    return path


def _formats(book: xlsxwriter.Workbook) -> dict[str, Any]:
    fmt = formats(book)
    fmt["portal_header"] = book.add_format(
        {
            "bold": True,
            "bg_color": "#375623",
            "font_color": "white",
            "border": 1,
            "text_wrap": True,
            "valign": "vcenter",
        }
    )
    fmt["check_header"] = book.add_format(
        {
            "bold": True,
            "bg_color": "#C6EFCE",
            "border": 1,
            "text_wrap": True,
            "valign": "vcenter",
        }
    )
    fmt["vendor_header"] = book.add_format(
        {
            "bold": True,
            "bg_color": "#A9D08E",
            "border": 1,
            "text_wrap": True,
            "valign": "vcenter",
        }
    )
    fmt["meta_header"] = book.add_format(
        {
            "bold": True,
            "bg_color": "#595959",
            "font_color": "white",
            "border": 1,
            "text_wrap": True,
            "valign": "vcenter",
        }
    )
    fmt["money_vendor"] = book.add_format(
        {"num_format": "#,##0.00", "bg_color": "#E2EFDA"}
    )
    fmt["text_vendor"] = book.add_format({"bg_color": "#E2EFDA"})
    fmt["date_vendor"] = book.add_format(
        {"num_format": "yyyy-mm-dd", "bg_color": "#E2EFDA"}
    )
    fmt["rate_vendor"] = book.add_format({"num_format": "0.00%", "bg_color": "#E2EFDA"})
    fmt["yellow"] = book.add_format(
        {"num_format": "#,##0.00", "bg_color": "#FFFF00", "bold": True}
    )
    fmt["yellow_text"] = book.add_format({"bold": True, "bg_color": "#FFFF00"})
    fmt["total"] = book.add_format({"num_format": "#,##0.00", "bold": True})
    return fmt


def _summary_sheet(book: Any, fmt: dict[str, Any], result: FoodResult) -> None:
    sheet = book.add_worksheet("Summary")
    sheet.set_column(0, 0, 62)
    sheet.set_column(1, 1, 18)
    summary = result.summary
    figures = _figures(result)
    row = heading(sheet, fmt, 0, "Food reconciliation") + 1
    for label, text in (
        ("Vendor", result.vendor_name or ""),
        ("Portal from", str(summary.portal_date_start or "")),
        ("Portal to", str(summary.portal_date_end or "")),
    ):
        row = kv(sheet, fmt, row, label, text)

    row = heading(sheet, fmt, row + 1, "Volumes")
    for label, count in (
        ("Portal rows", summary.portal_rows),
        ("Vendor rows", summary.vendor_rows),
        ("Paired on order id", summary.paired),
        ("Settled", summary.settled_orders),
        ("Held for an amount or fee break", summary.held_orders),
        ("On the portal only", summary.portal_only),
        ("On the vendor report only", summary.vendor_only),
        ("Not delivered", summary.not_delivered),
    ):
        sheet.write(row, 0, label, fmt["label"])
        sheet.write_number(row, 1, count)
        row += 1

    row = heading(sheet, fmt, row + 1, "Client recon total")
    for label, amount in (
        ("SG FOOD ORDER Amount", figures.subtotal),
        ("Vendor FOOD ORDER Amount", figures.chef_total),
        ("Difference", figures.food_gap),
        ("Food Amount", figures.subtotal),
        ("Delivery", figures.customer_delivery),
        ("ADD DC", figures.sg_bear),
        ("Round of differ", figures.round_off),
        ("COM", figures.commission),
        ("Total order amount", figures.total_order_amount),
        ("Payable with the food difference added back", figures.headline),
        ("Compensation (reported, not deducted)", summary.compensation_total),
        ("Settled due, with the amount break held out", summary.settled_due),
    ):
        sheet.write(row, 0, label, fmt["label"])
        sheet.write(row, 1, num(amount), fmt["money"])
        row += 1

    row += 1
    sheet.write(
        row,
        0,
        "The total follows the client recon block. Vendor orders missing from this "
        "portal export stay in it, using the vendor food amount, fees, and delivery "
        "split. Portal orders with no vendor row stay out. Amounts show two decimal "
        "places.",
        fmt["info"],
    )


def _orders_sheet(book: Any, fmt: dict[str, Any], result: FoodResult) -> None:
    sheet = book.add_worksheet("Orders")
    sheet.set_row(0, 32)
    sheet.freeze_panes(1, 0)
    for index, spec in enumerate(_COLUMNS):
        _key, header, width, group, _kind = spec
        sheet.set_column(index, index, width)
        sheet.write(0, index, header, fmt[f"{group}_header"])

    included = [
        order
        for order in result.orders
        if _paired(order) or order.verdict is FoodVerdict.VENDOR_ONLY
    ]
    portal_only = [
        order for order in result.orders if order.verdict is FoodVerdict.PORTAL_ONLY
    ]
    for offset, order in enumerate(included):
        _write_order(sheet, fmt, 1 + offset, order)

    first = 2
    last = 1 + len(included)
    totals = last  # 0-based row index of the totals row; excel row is totals + 1
    if included:
        _write_totals(sheet, fmt, totals, first, last)
        _write_delivery_sum(sheet, fmt, totals + 1, totals)
        _write_paired_total(sheet, fmt, totals + 2, totals)
        _write_client_summary(sheet, fmt, totals + 4, totals)
    if portal_only:
        note_row = (totals + 10) if included else 2
        _write_one_sided(sheet, fmt, note_row, portal_only)
    if included:
        sheet.autofilter(0, 0, last - 1, len(_COLUMNS) - 1)


def _exceptions_sheet(book: Any, fmt: dict[str, Any], result: FoodResult) -> None:
    sheet = book.add_worksheet("Exceptions")
    table(
        sheet,
        fmt,
        ("Severity", "Code", "Order", "Amount", "Detail"),
        (12, 32, 18, 16, 88),
    )
    for index, exc in enumerate(result.exceptions, start=1):
        style = fmt["high"] if exc.severity is Severity.HIGH else fmt["info"]
        sheet.write(index, 0, str(exc.severity), style)
        sheet.write(index, 1, str(exc.code), style)
        sheet.write(index, 2, exc.order_no or "")
        sheet.write(index, 3, num(exc.amount), fmt["money"])
        sheet.write(index, 4, exc.detail)


def _paired(order: FoodOrder) -> bool:
    return (
        order.portal is not None and order.vendor is not None and order.checks.ids_match
    )


def _write_order(sheet: Any, fmt: dict[str, Any], row: int, order: FoodOrder) -> None:
    for col, value in enumerate(_order_values(order)):
        _write_value(sheet, fmt, row, col, value, _COLUMNS[col][3], _COLUMNS[col][4])


def _write_value(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    col: int,
    value: Any,
    group: str,
    kind: str,
) -> None:
    if value is None:
        return
    vendor = group == "vendor"
    if isinstance(value, date):
        style = "date_vendor" if vendor else "date"
        sheet.write_datetime(
            row, col, datetime(value.year, value.month, value.day), fmt[style]
        )
        return
    if isinstance(value, Decimal):
        if kind == "rate":
            sheet.write(row, col, num(value), fmt["rate_vendor" if vendor else "rate"])
            return
        if kind == "diff":
            style = "money_bad" if abs(value) > Decimal("0.01") else "money"
            sheet.write(row, col, num(value), fmt[style])
            return
        style = "money_vendor" if vendor else "money"
        sheet.write(row, col, num(value), fmt[style])
        return
    sheet.write(row, col, value, fmt["text_vendor"] if vendor else None)


def _write_totals(
    sheet: Any, fmt: dict[str, Any], row: int, first: int, last: int
) -> None:
    money_keys = {
        key for key, _header, _width, _group, kind in _COLUMNS if kind == "money"
    }
    diff_keys = {"amount_diff", "fee_diff"}
    for key in money_keys | diff_keys:
        col = _COL[key]
        letter = _letter(col)
        formula = f"=SUM({letter}{first}:{letter}{last})"
        if key in {"sg_bear", "chefz_bear", "cust_del"}:
            style = "yellow"
        else:
            style = "total"
        sheet.write_formula(row, col, formula, fmt[style])


def _write_delivery_sum(sheet: Any, fmt: dict[str, Any], row: int, totals: int) -> None:
    """The yellow 5,089-style line: the three delivery shares add to the gross fee."""
    excel = totals + 1
    sg = _letter(_COL["sg_bear"])
    chefz = _letter(_COL["chefz_bear"])
    customer = _letter(_COL["cust_del"])
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
    net = _letter(_COL["net_payable"])
    chef = _letter(_COL["chef_total"])
    sub = _letter(_COL["sub_total"])
    sg = _letter(_COL["sg_bear"])
    fee = _letter(_COL["fee_diff"])
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
        return f"{_letter(_COL[key])}{excel}"

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


def _write_one_sided(
    sheet: Any, fmt: dict[str, Any], row: int, orders: list[FoodOrder]
) -> None:
    sheet.write(
        row, 0, "Portal orders with no vendor row, not in the totals", fmt["title"]
    )
    for offset, order in enumerate(orders, start=1):
        _write_order(sheet, fmt, row + offset, order)


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


def _letter(index: int) -> str:
    number = index + 1
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
