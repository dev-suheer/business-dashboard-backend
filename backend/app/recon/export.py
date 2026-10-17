"""Excel report builder (plan.md section 8).

Written with ``constant_memory``, which flushes each row to disk as it is added
instead of buffering the workbook. That caps memory regardless of how many
orders the run produced, at the cost of one rule: rows must be written strictly
in order, top to bottom, and never revisited.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter

from app.recon.exceptions import Severity
from app.recon.models import ReconResult, VarianceClass
from app.recon.xlsx import formats, num, table


def build_report(result: ReconResult, path: Path) -> Path:
    book = xlsxwriter.Workbook(
        str(path),
        {
            "constant_memory": True,
            "in_memory": False,
            "default_date_format": "yyyy-mm-dd",
        },
    )
    try:
        fmt = formats(book)
        _summary_sheet(book, fmt, result)
        _matched_sheet(book, fmt, result)
        _exceptions_sheet(book, fmt, result)
        _variance_sheet(book, fmt, result)
        _bridge_sheet(book, fmt, result)
        _unmatched_sheet(book, fmt, result)
    finally:
        book.close()
    return path


def _summary_sheet(book: Any, fmt: dict[str, Any], result: ReconResult) -> None:
    sheet = book.add_worksheet("Summary")
    sheet.set_column(0, 0, 38)
    sheet.set_column(1, 1, 18)
    s = result.summary
    row = 0
    sheet.write(row, 0, "Reconciliation summary", fmt["title"])
    row += 2

    for label, text in (
        ("Category", result.category),
        ("Merchant", result.merchant_id or "—"),
        ("Period start", str(result.period_start)),
        ("Period end", str(result.period_end)),
    ):
        sheet.write(row, 0, label, fmt["label"])
        sheet.write(row, 1, text)
        row += 1

    row += 1
    sheet.write(row, 0, "Volumes", fmt["title"])
    row += 1
    volumes: tuple[tuple[str, int], ...] = (
        ("Portal lines", s.portal_lines),
        ("Orders", s.orders),
        ("Ledger transactions (deduplicated)", s.ledger_entries),
        ("Orders matched to the statement", s.orders_matched),
        ("Orders matching exactly", s.orders_exact),
    )
    for label, count in volumes:
        sheet.write(row, 0, label, fmt["label"])
        sheet.write_number(row, 1, count)
        row += 1

    row += 1
    sheet.write(row, 0, "Settlement", fmt["title"])
    row += 1
    settlement: tuple[tuple[str, Decimal | None, bool], ...] = (
        ("Gross sales", s.gross_sales, False),
        ("Standard-rate base", s.standard_base, False),
        ("Promo-rate base", s.promo_base, False),
        ("Commission earned", s.commission_earned, False),
        ("Payable before adjustments", s.payable_before_adjustments, False),
        ("Adjustments", s.adjustments, False),
        ("Net payable", s.net_payable, True),
        ("Vendor closing balance", s.vendor_closing_balance, False),
        ("Unexplained difference", s.unexplained_difference, False),
        ("Promotional absorption (we bear this)", s.promotional_absorption, False),
        ("Overcharged commission (owed back)", s.overcharged_commission, False),
    )
    for label, amount, bold in settlement:
        sheet.write(row, 0, label, fmt["label"])
        sheet.write(row, 1, num(amount), fmt["money_bold" if bold else "money"])
        row += 1

    row += 1
    sheet.write(row, 0, "Exceptions by code", fmt["title"])
    row += 1
    for code, count in sorted(s.exception_counts.items()):
        sheet.write(row, 0, code)
        sheet.write_number(row, 1, count)
        row += 1

    # The legend: which rates produced these numbers. Without it a report cannot
    # be re-derived months later.
    row += 1
    sheet.write(row, 0, "Rates applied to this run", fmt["title"])
    row += 1
    rates = result.rates
    legend: tuple[tuple[str, float | str, str | None], ...] = (
        ("Standard rate", num(rates.standard_rate), "rate"),
        ("Promo rate", num(rates.promo_rate), "rate"),
        ("Promo cutoff (inclusive, on order date)", str(rates.promo_cutoff), None),
        ("Line tolerance", num(rates.line_tolerance), "money"),
        ("Order tolerance", num(rates.order_tolerance), "money"),
    )
    for label, shown, style in legend:
        sheet.write(row, 0, label, fmt["label"])
        sheet.write(row, 1, shown, fmt[style] if style else None)
        row += 1
    row += 1
    sheet.write(
        row,
        0,
        "Commission base is always portal GMV. Shipping is settled outside this "
        "reconciliation and is never deducted.",
        fmt["info"],
    )


def _matched_sheet(book: Any, fmt: dict[str, Any], result: ReconResult) -> None:
    sheet = book.add_worksheet("Matched Detail")
    table(
        sheet,
        fmt,
        (
            "Order",
            "Order date",
            "Lines",
            "Statuses",
            "Portal GMV",
            "Vendor amount",
            "Difference",
            "Standard base",
            "Promo base",
            "Expected commission",
            "Actual commission",
            "Variance",
            "Variance class",
            "Payable",
            "Adjustments",
            "Net payable",
            "In payable",
            "Match state",
            "Flags",
        ),
        (16, 12, 7, 20, 13, 14, 11, 14, 12, 18, 17, 11, 16, 12, 12, 13, 10, 14, 24),
    )
    for row, order in enumerate(result.orders, start=1):
        diff_style = (
            fmt["money_bad"]
            if order.amount_diff not in (None, Decimal(0))
            else fmt["money"]
        )
        var_style = (
            fmt["money_bad"]
            if order.variance_class is VarianceClass.OVERCHARGE
            else fmt["money_warn"]
            if order.variance_class is not VarianceClass.CLEAN
            else fmt["money"]
        )
        sheet.write(row, 0, order.order_no)
        sheet.write(row, 1, str(order.order_date))
        sheet.write_number(row, 2, order.line_count)
        sheet.write(row, 3, ", ".join(s.value for s in order.statuses))
        sheet.write(row, 4, num(order.portal_gmv), fmt["money"])
        sheet.write(row, 5, num(order.vendor_amount), fmt["money"])
        sheet.write(row, 6, num(order.amount_diff), diff_style)
        sheet.write(row, 7, num(order.standard_base), fmt["money"])
        sheet.write(row, 8, num(order.promo_base), fmt["money"])
        sheet.write(row, 9, num(order.expected_commission), fmt["money"])
        sheet.write(row, 10, num(order.actual_commission), fmt["money"])
        sheet.write(row, 11, num(order.commission_variance), var_style)
        sheet.write(row, 12, order.variance_class.value)
        sheet.write(row, 13, num(order.payable), fmt["money"])
        sheet.write(row, 14, num(order.adjustments), fmt["money"])
        sheet.write(row, 15, num(order.net_payable), fmt["money"])
        sheet.write(row, 16, "yes" if order.included_in_payable else "no")
        sheet.write(row, 17, order.match_state.value)
        sheet.write(row, 18, ", ".join(order.flags))


def _exceptions_sheet(book: Any, fmt: dict[str, Any], result: ReconResult) -> None:
    sheet = book.add_worksheet("Exceptions")
    table(
        sheet,
        fmt,
        ("Severity", "Code", "Order", "Amount", "Detail"),
        (10, 28, 16, 13, 100),
    )
    for row, exc in enumerate(result.exceptions, start=1):
        style = (
            fmt["high"]
            if exc.severity is Severity.HIGH
            else (fmt["info"] if exc.severity is Severity.INFO else None)
        )
        sheet.write(row, 0, exc.severity.value, style)
        sheet.write(row, 1, exc.code.value, style)
        sheet.write(row, 2, exc.order_no or "")
        sheet.write(row, 3, num(exc.amount), fmt["money"])
        sheet.write(row, 4, exc.detail)


def _variance_sheet(book: Any, fmt: dict[str, Any], result: ReconResult) -> None:
    sheet = book.add_worksheet("Commission Variance")
    table(
        sheet,
        fmt,
        (
            "Order",
            "Order date",
            "SKU",
            "Item",
            "Status",
            "GMV",
            "Rate",
            "Expected",
            "Actual",
            "Variance",
            "Class",
            "Coupon",
            "Coupon value",
        ),
        (16, 12, 20, 42, 14, 11, 8, 11, 11, 11, 16, 14, 12),
    )
    for row, item in enumerate(result.line_variances, start=1):
        style = (
            fmt["money_bad"]
            if item.variance_class is VarianceClass.OVERCHARGE
            else fmt["money_warn"]
        )
        sheet.write(row, 0, item.order_no)
        sheet.write(row, 1, str(item.order_date))
        sheet.write(row, 2, item.sku or "")
        sheet.write(row, 3, item.item_title or "")
        sheet.write(row, 4, item.status.value)
        sheet.write(row, 5, num(item.gmv), fmt["money"])
        sheet.write(row, 6, num(item.rate), fmt["rate"])
        sheet.write(row, 7, num(item.expected_commission), fmt["money"])
        sheet.write(row, 8, num(item.actual_commission), fmt["money"])
        sheet.write(row, 9, num(item.variance), style)
        sheet.write(row, 10, item.variance_class.value)
        sheet.write(row, 11, item.coupon_name or "")
        sheet.write(row, 12, num(item.coupon_value), fmt["money"])


def _bridge_sheet(book: Any, fmt: dict[str, Any], result: ReconResult) -> None:
    """Computed net against the vendor's closing balance, and what explains the gap."""
    sheet = book.add_worksheet("Settlement Bridge")
    sheet.set_column(0, 0, 46)
    sheet.set_column(1, 1, 16)
    s = result.summary
    row = 0
    sheet.write(row, 0, "Settlement bridge", fmt["title"])
    row += 2

    lines: list[tuple[str, Decimal | None, bool]] = [
        ("Payable before adjustments", s.payable_before_adjustments, False),
    ]
    for order in result.orders:
        if order.adjustments:
            label = "Clawback" if "CLAWBACK" in order.flags else "Return adjustment"
            lines.append((f"{label} — {order.order_no}", order.adjustments, False))
    lines.append(("Net payable", s.net_payable, True))
    lines.append(("Vendor closing balance", s.vendor_closing_balance, False))
    lines.append(("Unexplained difference", s.unexplained_difference, True))

    for label, amount, bold in lines:
        sheet.write(row, 0, label, fmt["label"] if bold else None)
        sheet.write(row, 1, num(amount), fmt["money_bold" if bold else "money"])
        row += 1

    row += 1
    sheet.write(row, 0, "Bridged by items outside the period", fmt["title"])
    row += 1
    sheet.write(row, 0, "Code", fmt["header"])
    sheet.write(row, 1, "Orders", fmt["header"])
    sheet.write(row, 2, "Amount", fmt["header"])
    sheet.set_column(2, 2, 16)
    row += 1
    for code in ("PRIOR_PERIOD", "LATER_PERIOD", "MISSING_FROM_PORTAL"):
        matching = [e for e in result.exceptions if e.code.value == code]
        sheet.write(row, 0, code)
        sheet.write_number(row, 1, len(matching))
        sheet.write(
            row,
            2,
            num(sum((e.amount or Decimal(0) for e in matching), Decimal(0))),
            fmt["money"],
        )
        row += 1


def _unmatched_sheet(book: Any, fmt: dict[str, Any], result: ReconResult) -> None:
    sheet = book.add_worksheet("Unmatched Ledger Rows")
    table(
        sheet,
        fmt,
        (
            "Sheet",
            "Doc date",
            "Doc no",
            "Doc type",
            "Debit",
            "Credit",
            "Balance",
            "Reference",
            "Order",
            "State",
            "Description",
        ),
        (20, 12, 12, 16, 12, 12, 13, 16, 16, 14, 60),
    )
    for row, entry in enumerate(result.unmatched_ledger, start=1):
        sheet.write(row, 0, entry.sheet or "")
        sheet.write(row, 1, str(entry.doc_date) if entry.doc_date else "")
        sheet.write(row, 2, entry.doc_no or "")
        sheet.write(row, 3, entry.doc_type.value)
        sheet.write(row, 4, num(entry.debit), fmt["money"])
        sheet.write(row, 5, num(entry.credit), fmt["money"])
        sheet.write(row, 6, num(entry.balance), fmt["money"])
        sheet.write(row, 7, entry.raw_ref or "")
        sheet.write(row, 8, entry.order_no or "")
        sheet.write(row, 9, entry.match_state.value)
        sheet.write(row, 10, entry.raw_desc or "")
