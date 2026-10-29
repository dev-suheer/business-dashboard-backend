"""The eShop Excel report (plan.md section 8).

The client signed off on the food workbook, and eShop follows it: Dashboard,
Orders, Exceptions, Wallet when a wallet history was uploaded, B2C Invoices
ERP, copies of the Portal and Vendor uploads, and Finance Report when that
file was uploaded.

The Orders sheet has one row per order: the portal side, the checks and the
payable, then the statement's side, coloured like food's. The orders in the
payable are totalled, and the settlement bridge sits beneath: payable before
adjustments, adjustments, net payable, the vendor's closing balance and what
is left unexplained. Orders out of the payable, and statement orders with no
portal row, are listed under that and stay out of the totals.

When a finance report is uploaded, each portal order's row looks the digits of
its order number up in that report's Order ID, in five cashback columns after
Verdict: SG-SH-08986 is 8986, the same key the wallet history uses.

Written with ``constant_memory``, which flushes each row to disk as it is added
instead of buffering the workbook. That caps memory regardless of how many
orders the run produced, at the cost of one rule: rows must be written strictly
in order, top to bottom, and never revisited.
"""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter

from app.recon.categories.eshop import parser
from app.recon.cleaning import clean_text
from app.recon.exceptions import ReconException
from app.recon.finance import (
    CASHBACK_COLUMNS,
    FinanceLayout,
    finance_layout,
    finance_sheet,
    write_cashback,
)
from app.recon.models import LedgerEntry, MatchState, OrderRecon, ReconResult
from app.recon.sheets import (
    PORTAL_TITLE,
    VENDOR_TITLE,
    BreakdownEntry,
    CashbackLine,
    Column,
    DashBlock,
    PortalRecord,
    cashback_sheet,
    exceptions_sheet,
    order_id_text,
    portal_records,
    ranked,
    report_formats,
    share,
    source_sheets,
    wallet_sheet,
    write_column_headers,
    write_dashboard,
    write_order_row,
    write_sum_row,
)
from app.recon.xlsx import num

_COLUMNS: tuple[Column, ...] = (
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

_PERIOD_LABELS = {
    MatchState.PRIOR_PERIOD: "Prior period",
    MatchState.LATER_PERIOD: "Later period",
    MatchState.UNMATCHED: "Missing from portal",
    MatchState.AMBIGUOUS: "Ambiguous credit note",
    MatchState.MATCHED: "Matched",
}


def build_report(
    result: ReconResult,
    path: Path,
    sources: dict[str, Path] | None = None,
) -> Path:
    """Write the report. ``sources`` maps ``portal`` and ``vendor`` to the uploads.

    B2C Invoices ERP and the Portal and Vendor copies are appended only when
    the paths are supplied, which the download route always does. B2C invoices
    are the orders in the payable that are on the statement. A ``finance``
    path adds the cashback columns and a copy of that report.
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
                    ("vendor", VENDOR_TITLE, parser.LEDGER_SCHEMA, "vendor_header"),
                ),
            )
            if finance is not None:
                finance_sheet(book, fmt, sources, finance)
    finally:
        book.close()
    return path


def _in_payable(result: ReconResult) -> list[OrderRecon]:
    return [order for order in result.orders if order.included_in_payable]


# --- Dashboard --------------------------------------------------------------

_PRIMARY_HEADERS = (
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
_COLLECTION_HEADERS = (
    "Total Order Amount",
    "Wallets",
    "Other Method",
    "Collection",
    "Order-Collection",
    "Promo Discount",
    "Net Difference",
)
_SHIPPING_HEADERS = (
    "SG Shipping",
    "Merchant Shipping",
    "TOTAL",
    "Average Per Order",
)
_SHARE_HEADERS = ("By shoppinggate", "By Vendor")
_BREAKDOWN_HEADERS = (
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


def _total(values: Any) -> Decimal:
    return sum(values, Decimal(0))


def _dashboard_sheet(book: Any, fmt: dict[str, Any], result: ReconResult) -> None:
    """The client dashboard, over the orders in the payable.

    Commission earned and net payable are the run's headline figures, which
    also carry clawbacks and return adjustments.
    """
    s = result.summary
    orders = _in_payable(result)
    gmv = _total(order.portal_gmv for order in orders)
    vendor = _total(order.vendor_amount or Decimal(0) for order in orders)
    diff = _total(order.amount_diff or Decimal(0) for order in orders)
    actual = _total(order.actual_commission for order in orders)
    wallet = _total(order.wallet_amount for order in orders)
    non_wallet = _total(order.non_wallet_amount for order in orders)
    coupon = _total(order.coupon_value for order in orders)
    sg_shipping = _total(order.sg_shipping for order in orders)
    shipping = _total(order.shipping_cost for order in orders)
    collection = wallet + non_wallet

    blocks = (
        DashBlock(
            label="SG ESHOP ORDER Amount",
            value_label="Vendor ESHOP ORDER Amount",
            headers=_PRIMARY_HEADERS,
            values=(
                (len(orders), "int"),
                (gmv, "money"),
                (vendor, "money"),
                (diff, "money"),
                (actual, "money"),
                (s.commission_earned, "money"),
                (actual - s.commission_earned, "money"),
                (s.net_payable, "money"),
                (wallet, "money"),
                (non_wallet, "money"),
                (collection, "money"),
                (gmv, "money"),
                (collection - gmv, "money"),
                (coupon, "money"),
                (coupon + collection - gmv, "money"),
            ),
        ),
        DashBlock(
            label="Cash",
            headers=_COLLECTION_HEADERS,
            values=(
                (gmv, "money"),
                (wallet, "money"),
                (non_wallet, "money"),
                (collection, "money"),
                (gmv - collection, "money"),
                (coupon, "money"),
                (gmv - collection - coupon, "money"),
            ),
        ),
        DashBlock(
            label="Shipping",
            headers=_SHIPPING_HEADERS,
            values=(
                (sg_shipping, "money"),
                (shipping - sg_shipping, "money"),
                (shipping, "money"),
                (shipping / len(orders) if orders else Decimal(0), "money"),
            ),
        ),
        DashBlock(
            label="Average Shipping share",
            headers=_SHARE_HEADERS,
            values=(
                (share(sg_shipping, shipping), "pct"),
                (share(shipping - sg_shipping, shipping), "pct"),
            ),
            tall=False,
        ),
    )
    # The TOTAL row is every order under one name: each counts once, and its
    # money is not split.
    total = next(
        iter(_breakdown(orders, lambda _brand: "TOTAL")), BreakdownEntry(name="TOTAL")
    )
    entries = ranked(_breakdown(orders, lambda brand: brand))
    write_dashboard(book, fmt, blocks, _BREAKDOWN_HEADERS, total, entries)


def _breakdown(
    orders: list[OrderRecon], name_of: Callable[[str], str]
) -> list[BreakdownEntry]:
    """One row per brand.

    An order carrying several brands is counted under each. Its order-level
    money — the payment split and the shipping — is shared between them by
    their part of the order's GMV; coupon and commission are the brand's own
    lines. Each row is quantized once.
    """
    entries: dict[str, BreakdownEntry] = {}
    for order in orders:
        whole = _total(brand.gmv for brand in order.brands)
        parts: dict[str, list[Decimal]] = {}
        for brand in order.brands:
            weight = brand.gmv / whole if whole else Decimal(1) / len(order.brands)
            part = parts.setdefault(name_of(brand.brand), [Decimal(0)] * 3)
            part[0] += weight
            part[1] += brand.coupon_value
            part[2] += brand.expected_commission
        for name, (weight, coupon, commission) in parts.items():
            entry = entries.setdefault(name, BreakdownEntry(name=name))
            entry.orders += 1
            if coupon != 0:
                entry.discounts += 1
            entry.revenue += coupon
            entry.add_payment(
                order.payment_type,
                order.non_wallet_amount * weight,
                order.wallet_amount * weight,
            )
            entry.commission += commission
            entry.extra += order.shipping_cost * weight
    for entry in entries.values():
        entry.quantize()
    return list(entries.values())


# --- Orders -----------------------------------------------------------------


def _orders_sheet(
    book: Any,
    fmt: dict[str, Any],
    result: ReconResult,
    finance: FinanceLayout | None,
) -> None:
    columns = _COLUMNS + CASHBACK_COLUMNS if finance is not None else _COLUMNS
    sheet = book.add_worksheet("Orders")
    write_column_headers(sheet, fmt, columns)
    rates = result.rates
    tolerances = {
        "amount_diff": rates.order_tolerance,
        "variance": rates.line_tolerance,
    }

    included = _in_payable(result)
    excluded = [order for order in result.orders if not order.included_in_payable]
    portal_orders = {order.order_no for order in result.orders}
    statement_only = [
        entry
        for entry in result.unmatched_ledger
        if entry.order_no and entry.order_no not in portal_orders
    ]
    for offset, order in enumerate(included):
        write_order_row(
            sheet,
            fmt,
            1 + offset,
            _COLUMNS,
            _order_values(order, result),
            tolerances=tolerances,
        )
        _write_cashback(sheet, fmt, 1 + offset, order, finance)

    first = 2
    last = 1 + len(included)
    totals = last  # 0-based row of the totals; its Excel row is totals + 1
    row = 2
    if included:
        sheet.write(totals, 0, f"Total, {len(included)} in the payable", fmt["label"])
        write_sum_row(sheet, fmt, totals, first, last, _COLUMNS)
        row = _write_bridge(sheet, fmt, totals + 2, result) + 2
        sheet.autofilter(0, 0, last - 1, len(columns) - 1)
    if excluded:
        sheet.write(
            row, 0, "Orders not in the payable, not in the totals", fmt["title"]
        )
        for offset, order in enumerate(excluded, start=1):
            write_order_row(
                sheet,
                fmt,
                row + offset,
                _COLUMNS,
                _order_values(order, result),
                tolerances=tolerances,
            )
            _write_cashback(sheet, fmt, row + offset, order, finance)
        row += len(excluded) + 2
    if statement_only:
        sheet.write(
            row,
            0,
            "Statement rows with no portal order, not in the totals",
            fmt["title"],
        )
        for offset, entry in enumerate(statement_only, start=1):
            write_order_row(
                sheet, fmt, row + offset, _COLUMNS, _statement_values(entry)
            )


def _write_cashback(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    order: OrderRecon,
    finance: FinanceLayout | None,
) -> None:
    """The finance lookups after Verdict, keyed on the order number's digits.

    The Orders sheet has no SG Order ID column, so the key is written into the
    formula as text: SG-SH-08986 looks up "8986".
    """
    if finance is None:
        return
    digits = parser.wallet_order_id(order.order_no)
    key = f'"{digits}"' if digits is not None else None
    write_cashback(sheet, fmt, row, len(_COLUMNS), key, finance)


def _write_bridge(
    sheet: Any, fmt: dict[str, Any], row: int, result: ReconResult
) -> int:
    """The settlement bridge and the rate legend, across. Returns the last row."""
    s = result.summary
    measures: tuple[tuple[str, Decimal | None, bool], ...] = (
        ("Payable before adjustments", s.payable_before_adjustments, False),
        ("Adjustments", s.adjustments, False),
        ("Net payable", s.net_payable, True),
        ("Vendor closing balance", s.vendor_closing_balance, False),
        ("Unexplained difference", s.unexplained_difference, False),
        ("Standard-rate base", s.standard_base, False),
        ("Promo-rate base", s.promo_base, False),
        ("Commission earned", s.commission_earned, False),
        ("Promotional absorption (we bear this)", s.promotional_absorption, False),
        ("Overcharged commission (owed back)", s.overcharged_commission, False),
    )
    sheet.set_row(row, 32)
    for col, (title, _amount, _highlight) in enumerate(measures):
        sheet.write(row, col, title, fmt["dash_header"])
    for col, (_title, amount, highlight) in enumerate(measures):
        sheet.write(row + 1, col, num(amount), fmt["yellow" if highlight else "money"])
    rates = result.rates
    sheet.write(
        row + 3,
        0,
        f"Rates applied: standard {rates.standard_rate:.2%}, promo "
        f"{rates.promo_rate:.2%} on promotional SKUs ordered up to "
        f"{rates.promo_cutoff} inclusive. Commission base is always portal GMV. "
        "Shipping is settled outside this reconciliation and is never deducted.",
        fmt["info"],
    )
    return row + 3


def _order_values(order: OrderRecon, result: ReconResult) -> dict[str, Any]:
    return {
        "order_date": order.order_date,
        "merchant_id": order.merchant_id,
        "order_no": order.order_no,
        "lines": order.line_count,
        "statuses": ", ".join(status.value for status in order.statuses),
        "gmv": order.portal_gmv,
        "coupon_value": order.coupon_value,
        "actual": order.actual_commission,
        "payment_type": order.payment_type,
        "wallet": order.wallet_amount,
        "non_wallet": order.non_wallet_amount,
        "shipping": order.shipping_cost,
        "match": "TRUE" if order.vendor_amount is not None else "FALSE",
        "amount_diff": order.amount_diff,
        "standard_base": order.standard_base,
        "promo_base": order.promo_base,
        "expected": order.expected_commission,
        "variance": order.commission_variance,
        "variance_class": order.variance_class.value,
        "payable": order.payable,
        "adjustments": order.adjustments,
        "net_payable": order.net_payable,
        "vendor_date": order.vendor_date,
        "vendor_amount": order.vendor_amount,
        "vendor_credit": order.vendor_credit,
        "verdict": _verdict(order, result),
    }


def _verdict(order: OrderRecon, result: ReconResult) -> str:
    """The treatment that governs the order, most reversing first."""
    if "CLAWBACK" in order.flags:
        return "Clawback"
    if "RTO_EXCLUDED" in order.flags:
        return "RTO excluded"
    if order.vendor_amount is None:
        return "Missing from vendor"
    if (
        order.amount_diff is not None
        and abs(order.amount_diff) > result.rates.order_tolerance
    ):
        return "Amount mismatch"
    if "RETURN_ADJUSTED" in order.flags:
        return "Return adjusted"
    if "PENDING_DELIVERY" in order.flags:
        return "Pending delivery"
    return "Matched"


def _statement_values(entry: LedgerEntry) -> dict[str, Any]:
    return {
        "order_no": entry.order_no,
        "match": "FALSE",
        "vendor_date": entry.doc_date,
        "vendor_amount": entry.debit,
        "vendor_credit": entry.credit,
        "verdict": _PERIOD_LABELS[entry.match_state],
    }


# --- Exceptions -------------------------------------------------------------


def _exceptions_sheet(book: Any, fmt: dict[str, Any], result: ReconResult) -> None:
    statuses = {
        order.order_no: ", ".join(status.value for status in order.statuses)
        for order in result.orders
    }

    def status_of(exc: ReconException) -> str:
        return statuses.get(exc.order_no or "", "")

    exceptions_sheet(book, fmt, result.exceptions, status_of)


# --- B2C Invoices ERP -------------------------------------------------------

_INVOICE_PRODUCT = "eShop Delivery"


def _b2c_invoices_sheet(
    book: Any,
    fmt: dict[str, Any],
    result: ReconResult,
    sources: dict[str, Path],
) -> None:
    """One invoice line per billed order that carries SG-owned shipping.

    Billed orders are those in the payable and on the vendor statement: an RTO
    or a clawback is not billed, a return is, because it was delivered. Unit
    price is the shipping cost ex VAT. It reads the Portal copy rather than the
    Orders sheet: only a portal line says who owns its shipping, and the
    export puts an order's shipping on its first line, so this is one line per
    shipped order.
    """
    detected = {item.role: item.sheets for item in result.detected}
    path = sources.get("portal")
    names = detected.get("portal") or []
    if path is None or not names:
        return
    billed = {
        order.order_no
        for order in result.orders
        if order.included_in_payable and order.vendor_amount is not None
    }
    lines = [
        line
        for record in portal_records(path, names, parser.PORTAL_SCHEMA)
        if (line := _invoice_line(record, billed)) is not None
    ]
    cashback_sheet(book, fmt, lines)


def _invoice_line(record: PortalRecord, billed: set[str]) -> CashbackLine | None:
    order_id = order_id_text(record.get("ordernumber"))
    owner = record.get("shippingownership")
    cost = record.get("shippingcost")
    unit_cell = record.cell("shippingcost")
    if not order_id or unit_cell is None:
        return None
    # Normalised as the portal reader does, so it compares with the order number.
    if (clean_text(order_id) or "").upper() not in billed:
        return None
    if not isinstance(owner, str) or owner.strip().casefold() != "sg":
        return None
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost <= 0:
        return None
    partner = record.get("merchantname")
    bill_date = record.get("date")
    return CashbackLine(
        partner="" if partner is None else str(partner).strip(),
        order_id=order_id,
        # The eShop export carries no customer name.
        contact="",
        bill_date=bill_date if isinstance(bill_date, (date, datetime)) else None,
        product=_INVOICE_PRODUCT,
        unit_cell=unit_cell,
    )
