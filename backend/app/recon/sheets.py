"""The sheets every category's report shares (plan-food.md section 8).

The client signed off on the food workbook, and travel and eShop follow it:
Dashboard, Orders, Exceptions, Wallet when a history was uploaded, Cashback,
and copies of the Portal and Vendor uploads. What is the same in all three
lives here: the palette, the dashboard blocks, the exceptions table, the
cashback import, the wallet table and the source copies. Each category builds
its own Orders sheet and decides what goes into the others.

Every report is written with ``constant_memory``, so rows go out strictly top
to bottom and are never revisited.
"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter

from app.recon.cleaning import money, normalize_header
from app.recon.excel import SheetSchema, find_header, open_workbook
from app.recon.exceptions import ReconException, Severity
from app.recon.models import SheetManifest
from app.recon.wallet import WalletReconciliation, WalletState
from app.recon.xlsx import formats, num, table

# key, header, width, group (portal / check / vendor / meta), kind
Column = tuple[str, str, int, str, str]

PORTAL_TITLE = "Portal"
VENDOR_TITLE = "Vendor"


def report_formats(book: xlsxwriter.Workbook) -> dict[str, Any]:
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
    fmt["source_date"] = book.add_format({"num_format": "yyyy-mm-dd"})
    fmt["source_datetime"] = book.add_format({"num_format": "yyyy-mm-dd hh:mm"})
    fmt["dash_header"] = book.add_format(
        {
            "bold": True,
            "bg_color": "#1F4E79",
            "font_color": "white",
            "border": 1,
            "text_wrap": True,
            "align": "center",
            "valign": "vcenter",
        }
    )
    fmt["dash_int"] = book.add_format({"num_format": "#,##0", "border": 1})
    fmt["dash_money"] = book.add_format({"num_format": "#,##0.00", "border": 1})
    fmt["dash_pct"] = book.add_format(
        {"num_format": "0%", "border": 1, "align": "center"}
    )
    fmt["dash_label"] = book.add_format({"bold": True, "font_color": "#1F4E79"})
    fmt["dash_total"] = book.add_format(
        {
            "bold": True,
            "bg_color": "#1F4E79",
            "font_color": "white",
            "border": 1,
            "num_format": "#,##0.00",
        }
    )
    fmt["dash_total_int"] = book.add_format(
        {
            "bold": True,
            "bg_color": "#1F4E79",
            "font_color": "white",
            "border": 1,
            "num_format": "#,##0",
        }
    )
    fmt["dash_total_text"] = book.add_format(
        {
            "bold": True,
            "bg_color": "#1F4E79",
            "font_color": "white",
            "border": 1,
        }
    )
    fmt["dash_text"] = book.add_format({"border": 1})
    return fmt


def letter(index: int) -> str:
    """The Excel column letters of a 0-based column index."""
    number = index + 1
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def write_text(sheet: Any, row: int, col: int, value: Any, style: Any = None) -> None:
    """Write a cell, keeping text as text.

    ``write`` turns a string that starts with ``=`` into a formula and one that
    looks like a link into a hyperlink. Text that came from an upload is data,
    so it is always written as a string.
    """
    if value == "":
        # What ``write`` does with an empty string: a blank, styled if asked.
        sheet.write_blank(row, col, None, style)
    elif isinstance(value, str):
        sheet.write_string(row, col, value, style)
    else:
        sheet.write(row, col, value, style)


# --- Orders -----------------------------------------------------------------


def write_column_headers(
    sheet: Any, fmt: dict[str, Any], columns: Sequence[Column]
) -> None:
    """The Orders header row, coloured by group, frozen."""
    sheet.set_row(0, 32)
    sheet.freeze_panes(1, 0)
    for index, spec in enumerate(columns):
        _key, header, width, group, _kind = spec
        sheet.set_column(index, index, width)
        sheet.write(0, index, header, fmt[f"{group}_header"])


def write_cell(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    col: int,
    value: Any,
    group: str,
    kind: str,
    *,
    tolerance: Decimal = Decimal("0.01"),
) -> None:
    """One Orders cell. A ``diff`` beyond ``tolerance`` is shaded as a break."""
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
            style = "money_bad" if abs(value) > tolerance else "money"
            sheet.write(row, col, num(value), fmt[style])
            return
        style = "money_vendor" if vendor else "money"
        sheet.write(row, col, num(value), fmt[style])
        return
    write_text(sheet, row, col, value, fmt["text_vendor"] if vendor else None)


def write_order_row(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    columns: Sequence[Column],
    values: dict[str, Any],
    *,
    tolerances: dict[str, Decimal] | None = None,
) -> None:
    """Write one row by column key; a key that is absent or None stays blank."""
    for col, (key, _header, _width, group, kind) in enumerate(columns):
        tolerance = (tolerances or {}).get(key, Decimal("0.01"))
        write_cell(
            sheet, fmt, row, col, values.get(key), group, kind, tolerance=tolerance
        )


def write_sum_row(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    first: int,
    last: int,
    columns: Sequence[Column],
) -> None:
    """A SUM under every money and difference column, Excel rows first..last."""
    for col, (_key, _header, _width, _group, kind) in enumerate(columns):
        if kind in {"money", "diff"}:
            ref = letter(col)
            sheet.write_formula(
                row, col, f"=SUM({ref}{first}:{ref}{last})", fmt["total"]
            )


# --- Dashboard --------------------------------------------------------------

DASH_ORIGIN = 2


@dataclass(frozen=True)
class DashBlock:
    """One labelled block of the dashboard: a header row and a value row."""

    label: str | None
    headers: tuple[str, ...]
    # Each value with its kind: ``int``, ``money`` or ``pct``.
    values: tuple[tuple[Decimal | int, str], ...]
    # A second label, in column A of the value row.
    value_label: str | None = None
    # A tall header row, for headers that wrap.
    tall: bool = True


@dataclass
class BreakdownEntry:
    """One row of the "Breakdown By Most Used" table.

    ``extra`` is the last column, which each category names: food's Total
    Delivery, travel's Net Payable, eShop's Total Shipping.
    """

    name: str
    orders: int = 0
    discounts: int = 0
    revenue: Decimal = Decimal(0)
    apple: Decimal = Decimal(0)
    tamara: Decimal = Decimal(0)
    card: Decimal = Decimal(0)
    wallet: Decimal = Decimal(0)
    commission: Decimal = Decimal(0)
    extra: Decimal = Decimal(0)

    def add_payment(self, method: str | None, card: Decimal, wallet: Decimal) -> None:
        """The card share goes to the bucket its payment method names."""
        self.wallet += wallet
        bucket = payment_bucket(method)
        if bucket == "apple":
            self.apple += card
        elif bucket == "tamara":
            self.tamara += card
        elif bucket == "card":
            self.card += card

    def quantize(self) -> None:
        for name in _BREAKDOWN_MONEY:
            setattr(self, name, money(getattr(self, name)))


_BREAKDOWN_MONEY = (
    "revenue",
    "apple",
    "tamara",
    "card",
    "wallet",
    "commission",
    "extra",
)


def breakdown_total(entries: Sequence[BreakdownEntry]) -> BreakdownEntry:
    """The TOTAL row: the sum of the rows."""
    total = BreakdownEntry(name="TOTAL")
    for entry in entries:
        total.orders += entry.orders
        total.discounts += entry.discounts
        for name in _BREAKDOWN_MONEY:
            setattr(total, name, getattr(total, name) + getattr(entry, name))
    return total


def ranked(entries: Sequence[BreakdownEntry]) -> list[BreakdownEntry]:
    """Most used first, then by name."""
    return sorted(entries, key=lambda item: (-item.orders, item.name))


def payment_bucket(method: str | None) -> str:
    text = (method or "").casefold()
    if "tamara" in text:
        return "tamara"
    if "apple" in text:
        return "apple"
    if "card" in text:
        return "card"
    return "other"


def share(part: Decimal, whole: Decimal) -> Decimal:
    return Decimal(0) if whole == 0 else part / whole


def write_dashboard(
    book: Any,
    fmt: dict[str, Any],
    blocks: Sequence[DashBlock],
    breakdown_headers: tuple[str, ...],
    total: BreakdownEntry,
    entries: Sequence[BreakdownEntry],
) -> None:
    """The client dashboard: labelled blocks, then the breakdown table."""
    sheet = book.add_worksheet("Dashboard")
    sheet.set_column(0, 0, 42)
    sheet.set_column(1, 1, 16)
    sheet.set_column(2, DASH_ORIGIN + max(len(b.headers) for b in blocks), 15)
    sheet.hide_gridlines(2)

    row = 2
    for block in blocks:
        if block.tall:
            sheet.set_row(row, 32)
        if block.label:
            sheet.write(row, 0, block.label, fmt["dash_label"])
        _write_dash_headers(sheet, fmt, row, DASH_ORIGIN, block.headers)
        row += 1
        if block.value_label:
            sheet.write(row, 0, block.value_label, fmt["dash_label"])
        _write_dash_numbers(sheet, fmt, row, DASH_ORIGIN, block.values)
        row += 3

    sheet.set_row(row, 32)
    _write_dash_headers(sheet, fmt, row, 0, breakdown_headers)
    row += 1
    _write_breakdown_row(sheet, fmt, row, total, total=True)
    for entry in entries:
        row += 1
        _write_breakdown_row(sheet, fmt, row, entry, total=False)
    if entries:
        sheet.autofilter(row - len(entries) - 1, 0, row, len(breakdown_headers) - 1)


def _write_dash_headers(
    sheet: Any, fmt: dict[str, Any], row: int, col: int, headers: tuple[str, ...]
) -> None:
    for offset, header in enumerate(headers):
        sheet.write(row, col + offset, header, fmt["dash_header"])


def _write_dash_numbers(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    col: int,
    values: tuple[tuple[Decimal | int, str], ...],
) -> None:
    styles = {"int": "dash_int", "money": "dash_money", "pct": "dash_pct"}
    for offset, (value, kind) in enumerate(values):
        style = fmt[styles[kind]]
        if kind == "int":
            sheet.write_number(row, col + offset, int(value), style)
        else:
            sheet.write_number(row, col + offset, num(Decimal(value)), style)


def _write_breakdown_row(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    entry: BreakdownEntry,
    *,
    total: bool,
) -> None:
    text = fmt["dash_total_text"] if total else fmt["dash_text"]
    whole = fmt["dash_total_int"] if total else fmt["dash_int"]
    amount = fmt["dash_total"] if total else fmt["dash_money"]
    write_text(sheet, row, 0, entry.name, text)
    sheet.write_number(row, 1, entry.orders, whole)
    sheet.write_number(row, 2, entry.discounts, whole)
    for col, value in enumerate(
        (
            entry.revenue,
            entry.apple,
            entry.tamara,
            entry.card,
            entry.wallet,
            entry.commission,
            entry.extra,
        ),
        start=3,
    ):
        sheet.write_number(row, col, num(value), amount)


# --- Exceptions -------------------------------------------------------------

_EXCEPTION_HEADERS = ("Severity", "Code", "Order", "Status", "Amount", "Detail")
_EXCEPTION_WIDTHS = (12, 32, 18, 36, 16, 88)


def exceptions_sheet(
    book: Any,
    fmt: dict[str, Any],
    exceptions: Sequence[ReconException],
    status_of: Callable[[ReconException], str],
) -> None:
    """Severity, code, order, the order's status, amount, detail."""
    sheet = book.add_worksheet("Exceptions")
    table(sheet, fmt, _EXCEPTION_HEADERS, _EXCEPTION_WIDTHS)
    for row, exc in enumerate(exceptions, start=1):
        style = fmt["high"] if exc.severity is Severity.HIGH else fmt["info"]
        sheet.write(row, 0, str(exc.severity), style)
        sheet.write(row, 1, str(exc.code), style)
        write_text(sheet, row, 2, exc.order_no or "")
        write_text(sheet, row, 3, status_of(exc), style)
        sheet.write(row, 4, num(exc.amount), fmt["money"])
        write_text(sheet, row, 5, exc.detail)


# --- Wallet -----------------------------------------------------------------

_WALLET_HEADERS = (
    "Vertical",
    "SG Order ID",
    "Order",
    "Order Date",
    "Portal Wallet",
    "History Paid",
    "History Refunded",
    "Movements",
    "State",
    "Difference",
)
_WALLET_WIDTHS = (10, 12, 22, 12, 14, 14, 16, 11, 16, 13)


def wallet_sheet(book: Any, fmt: dict[str, Any], wallet: WalletReconciliation) -> None:
    """Each wallet-paid row against the history, then what the history held alone."""
    sheet = book.add_worksheet("Wallet")
    table(sheet, fmt, _WALLET_HEADERS, _WALLET_WIDTHS)
    for row, check in enumerate(wallet.checks, start=1):
        style = "money_bad" if check.state is WalletState.AMOUNT_MISMATCH else "money"
        sheet.write_string(row, 0, check.vertical)
        sheet.write_string(row, 1, check.sg_order_id)
        write_text(sheet, row, 2, check.booking_id or "")
        if check.order_date is not None:
            sheet.write_datetime(
                row,
                3,
                datetime(
                    check.order_date.year, check.order_date.month, check.order_date.day
                ),
                fmt["date"],
            )
        sheet.write(row, 4, num(check.portal_wallet_amount), fmt["money"])
        sheet.write(row, 5, num(check.history_paid), fmt["money"])
        sheet.write(row, 6, num(check.history_refunded), fmt["money"])
        sheet.write_number(row, 7, check.events)
        sheet.write_string(row, 8, str(check.state))
        sheet.write(row, 9, num(check.diff), fmt[style])

    row = len(wallet.checks) + 2
    sheet.write(row, 0, "Wallet movements with no portal row", fmt["title"])
    row += 1
    for event in wallet.unmatched_history:
        sheet.write_string(row, 0, event.vertical)
        sheet.write_string(row, 1, event.sg_order_id)
        if event.event_date is not None:
            sheet.write_datetime(
                row,
                3,
                datetime(
                    event.event_date.year, event.event_date.month, event.event_date.day
                ),
                fmt["date"],
            )
        sheet.write(row, 4, num(event.amount), fmt["money"])
        write_text(sheet, row, 8, event.message or "")
        row += 1

    row += 1
    sheet.write(row, 0, "Wallet history", fmt["title"])
    row += 1
    covers = (
        f"{wallet.window_start} to {wallet.window_end}"
        if wallet.window_start and wallet.window_end
        else "no dated movements"
    )
    sheet.write(row, 0, "History covers", fmt["label"])
    sheet.write_string(row, 2, covers)
    row += 1
    for label, count in (
        ("Wallet-paid rows checked", wallet.rows_checked),
        ("Found, same amount", wallet.matched),
        ("Found, amount disagreed", wallet.amount_mismatch),
        ("Outside the history's window", wallet.out_of_window),
        ("Missing from the history", wallet.missing),
        ("Wallet-paid rows with no SG Order ID, not checked", wallet.unkeyed),
        ("History movements with no portal row", len(wallet.unmatched_history)),
    ):
        sheet.write(row, 0, label, fmt["label"])
        sheet.write_number(row, 2, count)
        row += 1


# --- Cashback ---------------------------------------------------------------

_CASHBACK_HEADERS = (
    "Partner",
    "Invoice Partner Display Name",
    "Contact Name",
    "Invoice/Bill Date",
    "Invoice lines/Product",
    "Invoice lines/Quantity",
    "Invoice lines/Unit Price",
    "Invoice lines/Taxes",
)
_CASHBACK_WIDTHS = (16, 28, 24, 18, 18, 22, 24, 26)
_CASHBACK_TAX = "Domestic Sales (15%)"
VAT_DIVISOR = Decimal("1.15")


@dataclass(frozen=True)
class CashbackLine:
    partner: str
    order_id: str
    contact: str
    bill_date: date | datetime | None
    product: str
    # The Portal-sheet cell the unit price is taken from, ex VAT.
    unit_cell: str


def cashback_sheet(
    book: Any, fmt: dict[str, Any], lines: Sequence[CashbackLine]
) -> None:
    """One invoice line per portal row the category bills, ready to import."""
    sheet = book.add_worksheet("Cashback")
    table(sheet, fmt, _CASHBACK_HEADERS, _CASHBACK_WIDTHS)
    for offset, line in enumerate(lines, start=1):
        write_text(sheet, offset, 0, line.partner)
        _write_order_id(sheet, offset, 1, line.order_id)
        write_text(sheet, offset, 2, line.contact)
        _write_bill_date(sheet, fmt, offset, line.bill_date)
        sheet.write_string(offset, 4, line.product)
        sheet.write_number(offset, 5, 1)
        sheet.write_formula(offset, 6, f"={line.unit_cell}/{VAT_DIVISOR}", fmt["money"])
        sheet.write_string(offset, 7, _CASHBACK_TAX)
    if lines:
        sheet.autofilter(0, 0, len(lines), len(_CASHBACK_HEADERS) - 1)


def _write_order_id(sheet: Any, row: int, col: int, order_id: str) -> None:
    if order_id.isdigit():
        sheet.write_number(row, col, int(order_id))
    else:
        sheet.write_string(row, col, order_id)


def _write_bill_date(
    sheet: Any, fmt: dict[str, Any], row: int, value: date | datetime | None
) -> None:
    if isinstance(value, datetime):
        style = "source_date" if _midnight(value) else "source_datetime"
        sheet.write_datetime(row, 3, value, fmt[style])
    elif isinstance(value, date):
        sheet.write_datetime(
            row, 3, datetime(value.year, value.month, value.day), fmt["source_date"]
        )


def order_id_text(value: Any) -> str:
    """A cell as an order id, without the ``.0`` a numeric cell can carry."""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


@dataclass(frozen=True)
class PortalRecord:
    """One data row of the portal upload, placed where the Portal copy puts it."""

    sheet: str
    # Data rows after the header, from 1, the way ``iter_records`` counts them.
    ordinal: int
    # The row's 1-based number on the Portal sheet of the report.
    excel_row: int
    values: tuple[Any, ...]
    # Normalised header -> its first column; ``Customer`` repeats, so its
    # columns are listed apart.
    columns: dict[str, int]
    customers: list[int] = field(default_factory=list)

    def get(self, header: str) -> Any:
        return _at(self.values, self.columns.get(header))

    def cell(self, header: str) -> str | None:
        """The Portal-sheet reference of this row's cell under ``header``."""
        col = self.columns.get(header)
        if col is None:
            return None
        return f"{PORTAL_TITLE}!{letter(col)}{self.excel_row}"

    def contact(self) -> str:
        """The second Customer column is the name. The first is a numeric id."""
        chosen = self.customers[1] if len(self.customers) > 1 else None
        value = _at(self.values, chosen)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return ""


def portal_records(
    path: Path, sheet_names: list[str], schema: SheetSchema
) -> Iterator[PortalRecord]:
    """Walk the portal upload in the order the Portal sheet is written.

    A cashback formula points at that sheet, so the row numbers have to match
    the copy, including a title row above the headers and the name row that
    introduces each of several stacked sheets.
    """
    columns: dict[str, int] = {}
    customers: list[int] = []
    for source in iter_source_rows(path, sheet_names, schema):
        if source.values is None or source.header_at is None:
            continue
        if source.cursor == source.header_at:
            columns, customers = _header_columns(source.values)
        elif source.cursor > source.header_at:
            yield PortalRecord(
                sheet=source.sheet,
                ordinal=source.cursor - source.header_at,
                excel_row=source.cursor + 1,
                values=source.values,
                columns=columns,
                customers=customers,
            )


def _header_columns(header: tuple[Any, ...]) -> tuple[dict[str, int], list[int]]:
    columns: dict[str, int] = {}
    customers: list[int] = []
    for index, value in enumerate(header):
        key = normalize_header(value)
        if key == "customer":
            customers.append(index)
        elif key and key not in columns:
            columns[key] = index
    return columns, customers


def _at(row: tuple[Any, ...], index: int | None) -> Any:
    if index is None or index >= len(row):
        return None
    return row[index]


# --- Source copies ----------------------------------------------------------


@dataclass(frozen=True)
class SourceRow:
    """One row of a source copy. ``values`` is None on a sheet's name row."""

    sheet: str
    # 0-based row on the copy.
    cursor: int
    values: tuple[Any, ...] | None
    # 0-based row of this sheet's header on the copy, when it has one.
    header_at: int | None
    single: bool


def iter_source_rows(
    path: Path, sheet_names: list[str], schema: SheetSchema
) -> Iterator[SourceRow]:
    """Every row of the detected sheets, numbered as the copy writes them.

    One source sheet is the normal case and is copied in place, title row
    included. Several are stacked, each introduced by its own name and
    followed by a blank row, because the pipeline reads all of them.
    """
    single = len(sheet_names) == 1
    cursor = 0
    with open_workbook(path) as workbook:
        for name in sheet_names:
            match = find_header(workbook[name], schema)
            if not single:
                yield SourceRow(name, cursor, None, None, single)
                cursor += 1
            header_at = cursor + (match.row - 1) if match is not None else None
            for row in workbook[name].iter_rows(values_only=True):
                yield SourceRow(name, cursor, row, header_at, single)
                cursor += 1
            if not single:
                cursor += 1


def source_sheets(
    book: Any,
    fmt: dict[str, Any],
    detected: Sequence[SheetManifest],
    sources: dict[str, Path],
    copies: Sequence[tuple[str, str, SheetSchema, str]],
) -> None:
    """Append the uploaded sheets, one tab per (role, title, schema, header style)."""
    by_role = {item.role: item.sheets for item in detected}
    for role, title, schema, header_style in copies:
        path = sources.get(role)
        names = by_role.get(role) or []
        if path is None or not names:
            continue
        _write_source_sheet(book, fmt, title, path, names, schema, header_style)


def _write_source_sheet(
    book: Any,
    fmt: dict[str, Any],
    title: str,
    path: Path,
    sheet_names: list[str],
    schema: SheetSchema,
    header_style: str,
) -> None:
    """Copy every detected sheet of one upload into a single worksheet.

    A single source sheet gets its header frozen and filtered.
    """
    sheet = book.add_worksheet(title)
    widths: list[int] = []
    filter_at: tuple[int, int] | None = None
    end = 0
    frozen = False

    for source in iter_source_rows(path, sheet_names, schema):
        end = source.cursor + 1
        if source.values is None:
            write_text(sheet, source.cursor, 0, source.sheet, fmt["title"])
            continue
        if source.single and source.header_at is not None and not frozen:
            sheet.freeze_panes(source.header_at + 1, 0)
            frozen = True
        is_header = source.header_at is not None and source.cursor == source.header_at
        last_col = _write_source_row(
            sheet, fmt, source.cursor, source.values, widths, is_header, header_style
        )
        if is_header and source.single:
            filter_at = (source.cursor, last_col)

    for index, width in enumerate(widths):
        sheet.set_column(index, index, width)
    if filter_at is not None and end > filter_at[0] + 1:
        sheet.autofilter(filter_at[0], 0, end - 1, filter_at[1])


def _write_source_row(
    sheet: Any,
    fmt: dict[str, Any],
    row_index: int,
    row: tuple[Any, ...],
    widths: list[int],
    is_header: bool,
    header_style: str,
) -> int:
    """Write one source row and return the last occupied column, 0-based."""
    last = 0
    for col, value in enumerate(row):
        if value is None or value == "":
            continue
        last = col
        _write_source_cell(sheet, fmt, row_index, col, value, is_header, header_style)
        _note_width(widths, col, value, is_header)
    return last


def _write_source_cell(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    col: int,
    value: Any,
    is_header: bool,
    header_style: str,
) -> None:
    if is_header:
        text = value if isinstance(value, str) else str(value)
        sheet.write_string(row, col, text, fmt[header_style])
        return
    # ``datetime`` is a ``date``, and ``bool`` is an ``int``. Order matters.
    if isinstance(value, datetime):
        style = "source_date" if _midnight(value) else "source_datetime"
        sheet.write_datetime(row, col, value, fmt[style])
        return
    if isinstance(value, date):
        sheet.write_datetime(
            row, col, datetime(value.year, value.month, value.day), fmt["source_date"]
        )
        return
    if isinstance(value, time):
        sheet.write_string(row, col, value.strftime("%H:%M:%S"))
        return
    if isinstance(value, bool):
        sheet.write_boolean(row, col, value)
        return
    if isinstance(value, (int, float)):
        sheet.write_number(row, col, value)
        return
    sheet.write_string(row, col, str(value))


def _midnight(value: datetime) -> bool:
    return (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0)


def _note_width(widths: list[int], col: int, value: Any, is_header: bool) -> None:
    if isinstance(value, datetime):
        length = 16 if _midnight(value) else 18
    else:
        length = len(str(value))
    needed = min(42, max(12, length + (2 if is_header else 1)))
    if len(widths) <= col:
        widths.extend([12] * (col + 1 - len(widths)))
    if needed > widths[col]:
        widths[col] = needed
