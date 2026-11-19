"""The sheet writers the approved reports were written with, kept unchanged.

Each special sheet the report once wrote in Python is now a table in the
setup. The writer it replaced is kept here, word for word, so a test can write
the same data both ways and prove the table writes exactly what it did.
"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config_model.runtime import schema_of, sheet_schema
from app.config_model.seed import FINANCE_SCHEMA
from app.expr import (
    FormulaError,
    Scope,
    Values,
    evaluate,
    parse,
    references,
    text,
    truthy,
)
from app.recon.cleaning import normalize_header
from app.recon.excel import (
    SheetFormatError,
    SheetSchema,
    detect_sheets,
    find_header,
    open_workbook,
)
from app.recon.exceptions import ReconException, Severity
from app.recon.models import SheetManifest
from app.recon.sheets import (
    Column,
    PortalRecord,
    letter,
    order_id_text,
    portal_records,
    sheet_ref,
    write_text,
)
from app.recon.wallet import WalletReconciliation, WalletState
from app.recon.xlsx import num, table
from app.render.data import TableLayout
from app.runner import Row, RunResult

# --- Finance lookups (app/recon/finance.py, until the finance report's columns
# became look-up columns and the finance report a reference file) -------------


def finance_schema() -> SheetSchema:
    """The finance report as a run reads it: configured, or as defined above."""
    return sheet_schema("finance", "report", FINANCE_SCHEMA)


FINANCE_TITLE = "Finance Report"

# After the Orders sheet's own columns. The last two names are not on the
# client's draft; Reward Type and Outcome are the two facts that sit beside
# the reward.
CASHBACK_COLUMNS: tuple[Column, ...] = (
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
# Every column of the report its lookups read: a copy cannot leave them out.
LOOKED_UP = frozenset({"order_id", *_CASHBACK_FIELDS})

_NOT_FOUND = (
    "No finance report table found in 'finance'. Expected columns "
    "Spin Event ID, Order ID, Reward Amount (SAR), Reward Code, "
    "Reward Type, Outcome and Vertical Name."
)


@dataclass(frozen=True)
class FinanceLayout:
    """The copied report's sheets, and which column holds each lookup field."""

    sheets: list[str]
    columns: dict[str, int]
    # The tab the report is copied to, which every lookup reads.
    title: str = FINANCE_TITLE


def finance_layout(
    path: Path | None, hidden: frozenset[str] = frozenset()
) -> FinanceLayout | None:
    """The spin table inside a finance upload, or None when none was supplied.

    Order ID is unique on the report, so the first exact match is the reward.
    A worked recon file that already has the reward columns bolted on does not
    match: it has no Spin Event ID. Its columns are where its copy, which
    leaves out the ``hidden`` headers' columns, puts them.
    """
    if path is None:
        return None
    with open_workbook(path) as workbook:
        schema = finance_schema()
        names = detect_sheets(workbook, schema)
        if not names:
            raise SheetFormatError(_NOT_FOUND)
        match = find_header(workbook[names[0]], schema)
        if match is None:
            raise SheetFormatError(_NOT_FOUND)
        missing = match.missing(schema)
        if missing:
            raise SheetFormatError(
                "'finance' is missing column(s): " + ", ".join(missing)
            )
        columns = dict(match.columns)
        if hidden:
            header = next(
                workbook[names[0]].iter_rows(
                    min_row=match.row, max_row=match.row, values_only=True
                )
            )
            dropped = [
                index
                for index, value in enumerate(header)
                if normalize_header(value) in hidden
            ]
            columns = {
                name: index - sum(1 for d in dropped if d < index)
                for name, index in columns.items()
            }
        return FinanceLayout(sheets=list(names), columns=columns)


def write_cashback(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    first_col: int,
    key: str | None,
    layout: FinanceLayout,
) -> None:
    """Five lookups of ``key`` from ``first_col`` on. No key, or no reward, is NA.

    ``key`` is an Excel expression: a cell such as ``C2``, or a quoted
    literal such as ``"8986"``.
    """
    key_col = letter(layout.columns["order_id"])
    paired = zip(CASHBACK_COLUMNS, _CASHBACK_FIELDS, strict=True)
    for offset, (spec, field_name) in enumerate(paired):
        col = first_col + offset
        if key is None:
            sheet.write_string(row, col, "NA")
            continue
        formula = cashback_formula(
            key, key_col, letter(layout.columns[field_name]), layout.title
        )
        style = fmt["money"] if spec[4] == "money" else None
        sheet.write_formula(row, col, formula, style)


def cashback_formula(
    key: str, key_col: str, return_col: str, title: str = FINANCE_TITLE
) -> str:
    """Exact lookup. The key is text; the report's Order ID may be a number.

    Both functions carry their ``_xlfn.`` prefix: Excel files name every
    function added after Excel 2007 that way, and a bare ``IFNA`` reads as
    ``#NAME?``. xlsxwriter adds no prefix to a formula that already has one,
    so both are written here.
    """
    sheet = "'" + title.replace("'", "''") + "'"
    lookup = f"{sheet}!{key_col}:{key_col}"
    found = f"{sheet}!{return_col}:{return_col}"
    number = f"IFERROR(VALUE({key}),{key})"
    return (
        f'=IF({key}="","NA",_xlfn.IFNA('
        f"_xlfn.XLOOKUP({number},{lookup},{found},NA(),0),"
        f'_xlfn.XLOOKUP({key}&"",{lookup},{found},"NA",0)))'
    )


# --- Exceptions (app/recon/sheets.py, until the Exceptions sheet became a table) --

_EXCEPTION_HEADERS = ("Severity", "Code", "Order", "Status", "Amount", "Detail")
_EXCEPTION_WIDTHS = (12, 32, 18, 36, 16, 88)


def exceptions_sheet(
    book: Any,
    fmt: dict[str, Any],
    exceptions: Sequence[ReconException],
    status_of: Callable[[ReconException], str],
    title: str = "Exceptions",
) -> None:
    """Severity, code, order, the order's status, amount, detail."""
    sheet = book.add_worksheet(title)
    table(sheet, fmt, _EXCEPTION_HEADERS, _EXCEPTION_WIDTHS)
    for row, exc in enumerate(exceptions, start=1):
        style = fmt["high"] if exc.severity is Severity.HIGH else fmt["info"]
        sheet.write(row, 0, str(exc.severity), style)
        sheet.write(row, 1, str(exc.code), style)
        write_text(sheet, row, 2, exc.order_no or "")
        write_text(sheet, row, 3, status_of(exc), style)
        sheet.write(row, 4, num(exc.amount), fmt["money"])
        write_text(sheet, row, 5, exc.detail)


# --- B2C Invoices ERP (app/recon/sheets.py and app/render/generic.py, until the
# sheet became a table) ---------------------------------------------------------

B2C_INVOICES_TITLE = "B2C Invoices ERP"


@dataclass(frozen=True)
class Invoices:
    """The settings the sheet was written from (app/config_model/models.py)."""

    partner: str
    order_id: str
    bill_date: str
    product: str
    set: str | None = None
    where: str = "TRUE"
    contact: str = "BLANK()"
    contact_source: str | None = None
    contact_key: str | None = None
    contact_row: str | None = None
    upload: str | None = None
    unit_header: str | None = None
    unit_column: str | None = None


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
    # The cell the unit price is taken from, before VAT: on the Orders sheet,
    # or on the Portal copy.
    unit_cell: str


def cashback_sheet(
    book: Any,
    fmt: dict[str, Any],
    lines: Sequence[CashbackLine],
    title: str = B2C_INVOICES_TITLE,
) -> None:
    """One invoice line per order the category bills, ready to import."""
    sheet = book.add_worksheet(title)
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


def _contacts(
    result: RunResult, spec: Invoices, sources: dict[str, Path]
) -> dict[Any, str]:
    """The name in the upload's second Customer column: by the order id beside
    it, or by the row's place in the upload (its sheet and data row)."""
    if spec.contact_source is None:
        return {}
    path = sources.get(spec.contact_source)
    names = {item.role: item.sheets for item in result.detected}.get(
        spec.contact_source
    )
    upload = result.config.input(spec.contact_source)
    if path is None or not names or upload is None:
        return {}
    contacts: dict[Any, str] = {}
    for record in portal_records(path, names, schema_of(upload.sheets[0])):
        contact = record.contact()
        if not contact:
            continue
        if spec.contact_key is not None:
            order_id = order_id_text(record.get(spec.contact_key))
            if order_id:
                contacts[order_id] = contact
        else:
            contacts[(record.sheet, record.ordinal)] = contact
    return contacts


def _contact(
    spec: Invoices, contacts: dict[Any, str], order_id: str, member: Scope
) -> str:
    found: str | None = None
    if spec.contact_key is not None:
        found = contacts.get(order_id)
    elif spec.contact_row is not None:
        row = evaluate(spec.contact_row, member)
        if isinstance(row, Row):
            found = contacts.get((row.sheet, row.index))
    return found or text(evaluate(spec.contact, member))


def _set_lines(
    result: RunResult,
    spec: Invoices,
    layout: TableLayout | None,
    sources: dict[str, Path],
) -> list[CashbackLine]:
    """One line per row of the set as its table placed it; the unit price
    reads that row's cell. A set with no rows was placed nowhere: no lines."""
    assert spec.set is not None and spec.unit_column is not None
    if layout is None:
        return []
    contacts = _contacts(result, spec, sources)
    column = layout.letters[spec.unit_column]
    placed = layout.rows.get(spec.set, [])
    sheet = sheet_ref(layout.title)
    lines: list[CashbackLine] = []
    for row_index, member in zip(
        placed, result.scope.collection(spec.set), strict=False
    ):
        if not truthy(evaluate(spec.where, member)):
            continue
        order_id = text(evaluate(spec.order_id, member))
        lines.append(
            CashbackLine(
                partner=text(evaluate(spec.partner, member)),
                order_id=order_id,
                contact=_contact(spec, contacts, order_id, member),
                bill_date=evaluate(spec.bill_date, member),
                product=text(evaluate(spec.product, member)),
                unit_cell=f"{sheet}!{column}{row_index + 1}",
            )
        )
    return lines


def _upload_lines(
    result: RunResult, spec: Invoices, title: str, sources: dict[str, Path]
) -> list[CashbackLine] | None:
    """One line per row of the upload, read as its copy (the sheet ``title``)
    places it; the unit price reads that row's cell on the copy. With no such
    upload there is no sheet at all."""
    assert spec.upload is not None and spec.unit_header is not None
    path = sources.get(spec.upload)
    names = {item.role: item.sheets for item in result.detected}.get(spec.upload)
    upload = result.config.input(spec.upload)
    if path is None or not names or upload is None:
        return None
    formulas = [
        spec.where,
        spec.partner,
        spec.order_id,
        spec.contact,
        spec.bill_date,
        spec.product,
    ]
    named = set().union(*(references(parse(f)) for f in formulas))
    lines: list[CashbackLine] = []
    for record in portal_records(path, names, schema_of(upload.sheets[0])):
        item = _record_scope(record, named, result.scope)
        unit_cell = record.cell(spec.unit_header, title)
        if unit_cell is None or not truthy(evaluate(spec.where, item)):
            continue
        bill_date = evaluate(spec.bill_date, item)
        lines.append(
            CashbackLine(
                partner=text(evaluate(spec.partner, item)),
                order_id=text(evaluate(spec.order_id, item)),
                contact=text(evaluate(spec.contact, item)),
                bill_date=bill_date if isinstance(bill_date, date) else None,
                product=text(evaluate(spec.product, item)),
                unit_cell=unit_cell,
            )
        )
    return lines


def _record_scope(record: PortalRecord, named: set[str], parent: Scope) -> Values:
    """An upload row's cells by their header, as formulas read them: a number
    as a decimal, anything else as it was written. A header the upload lacks
    is a blank cell."""
    values: dict[str, Any] = {}
    for header in record.columns:
        values[header] = _cell_value(record.get(header))
    for name in named:
        head = name.split(".", 1)[0]
        if head not in values:
            try:
                parent.lookup((head,))
            except FormulaError:
                values[head] = None
    return Values(values, parent=parent)


def _cell_value(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)


# --- Wallet (app/recon/sheets.py, until the Wallet sheet became a table) ---------

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


def wallet_sheet(
    book: Any,
    fmt: dict[str, Any],
    wallet: WalletReconciliation,
    title: str = "Wallet",
) -> None:
    """Each wallet-paid row against the history, then what the history held alone."""
    sheet = book.add_worksheet(title)
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


# --- Copies of the uploads (app/recon/sheets.py and app/recon/finance.py, until
# each copy became a table) ------------------------------------------------------


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


def finance_sheet(
    book: Any,
    fmt: dict[str, Any],
    sources: dict[str, Path],
    layout: FinanceLayout,
) -> None:
    """Copy the uploaded report, as received, as the Finance Report sheet."""
    detected: Sequence[SheetManifest] = [
        SheetManifest(
            role="finance",
            filename="finance.xlsx",
            sheets=layout.sheets,
            rows=0,
        )
    ]
    source_sheets(
        book,
        fmt,
        detected,
        sources,
        (("finance", layout.title, finance_schema(), "cashback_header"),),
    )
