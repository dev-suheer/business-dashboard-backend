"""The sheets every category's report shares (plan-food.md section 8).

The client signed off on the food workbook, and travel and eShop follow it:
Dashboard, Orders, Exceptions, Wallet when a history was uploaded, Cashback,
and copies of the Portal and Vendor uploads. What is the same in all three
lives here: the palette, the dashboard blocks and the source copies. Each
category builds its own Orders sheet and decides what goes into the others.
The Exceptions, B2C invoice and Wallet sheets are tables in the setup
(``app/config_model/standard_tabs.py``).

Every report is written with ``constant_memory``, so rows go out strictly top
to bottom and are never revisited.
"""

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter

from app.recon.cleaning import normalize_header
from app.recon.excel import SheetSchema, find_header, open_workbook
from app.recon.xlsx import formats, num

# key, header, width, group (portal / check / vendor / meta), kind
Column = tuple[str, str, int, str, str]


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
    # The finance report's columns on Orders, and its copied header.
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
    sheet: Any,
    fmt: dict[str, Any],
    columns: Sequence[Column],
    styles: Sequence[Any] | None = None,
) -> None:
    """The Orders header row, coloured by group (or each in ``styles``), frozen."""
    sheet.set_row(0, 32)
    sheet.freeze_panes(1, 0)
    for index, spec in enumerate(columns):
        _key, header, width, group, _kind = spec
        sheet.set_column(index, index, width)
        style = styles[index] if styles is not None else fmt[f"{group}_header"]
        sheet.write(0, index, header, style)


def header_style(
    book: xlsxwriter.Workbook, fmt: dict[str, Any], group: str, color: str | None
) -> Any:
    """A column header's format: its group's, or a header of its own colour,
    with white or black text on it, whichever reads. Made once per colour."""
    if color is None:
        return fmt[f"{group}_header"]
    key = f"header {color.upper()}"
    if key not in fmt:
        style = {
            "bold": True,
            "bg_color": color.upper(),
            "border": 1,
            "text_wrap": True,
            "valign": "vcenter",
        }
        if _dark(color):
            style["font_color"] = "white"
        fmt[key] = book.add_format(style)
    return fmt[key]


def _dark(color: str) -> bool:
    """Whether white text reads better than black on ``#RRGGBB`` (WCAG contrast)."""

    def linear(channel: str) -> float:
        value = int(channel, 16) / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(color[i : i + 2]) for i in (1, 3, 5))
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return 1.05 / (luminance + 0.05) > (luminance + 0.05) / 0.05


def sheet_ref(title: str) -> str:
    """A sheet's name as a formula on another sheet names it: as it is when it
    is a plain name, quoted otherwise."""
    if _PLAIN_SHEET.fullmatch(title) and not _CELL_LIKE.fullmatch(title):
        return title
    return "'" + title.replace("'", "''") + "'"


_PLAIN_SHEET = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A name Excel would read as a cell (AB12, R1C1): quoted.
_CELL_LIKE = re.compile(r"[A-Za-z]{1,3}[0-9]+|[RrCc][0-9]*|[Rr][0-9]*[Cc][0-9]*")


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
    if kind in _AS_IS:
        _write_as_is(sheet, fmt, row, col, value, kind)
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


# Kinds written as the value is, in any band: an id (a number when it is all
# digits), a string (text even when empty), a date with its time.
_AS_IS = frozenset({"id", "string", "datetime"})


def _write_as_is(
    sheet: Any, fmt: dict[str, Any], row: int, col: int, value: Any, kind: str
) -> None:
    if kind == "datetime":
        # A date or a moment; anything else is left blank.
        if isinstance(value, datetime):
            style = "source_date" if _midnight(value) else "source_datetime"
            sheet.write_datetime(row, col, value, fmt[style])
        elif isinstance(value, date):
            sheet.write_datetime(
                row,
                col,
                datetime(value.year, value.month, value.day),
                fmt["source_date"],
            )
        return
    text = value if isinstance(value, str) else str(value)
    if kind == "id" and text.isdigit():
        sheet.write_number(row, col, int(text))
    else:
        sheet.write_string(row, col, text)


def write_styled(sheet: Any, row: int, col: int, value: Any, style: Any) -> None:
    """A cell in a format of its own: an empty one is still written, in it."""
    if value is None or value == "":
        sheet.write_blank(row, col, None, style)
    elif isinstance(value, Decimal):
        sheet.write(row, col, num(value), style)
    elif isinstance(value, datetime):
        sheet.write_datetime(row, col, value, style)
    elif isinstance(value, date):
        sheet.write_datetime(
            row, col, datetime(value.year, value.month, value.day), style
        )
    else:
        write_text(sheet, row, col, value, style)


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
    """One row of a breakdown table: a name, and its value in each column."""

    name: str
    values: list[Decimal | int]


@dataclass(frozen=True)
class DashBreakdown:
    """A breakdown table as the dashboard writes it, below the blocks."""

    # The names' header, then each column's.
    headers: tuple[str, ...]
    # Each column's kind: ``int`` or ``money``.
    kinds: tuple[str, ...]
    total: BreakdownEntry
    entries: Sequence[BreakdownEntry]


def breakdown_total(entries: Sequence[BreakdownEntry], width: int) -> BreakdownEntry:
    """The TOTAL row: the sum of the rows."""
    total = BreakdownEntry(name="TOTAL", values=[0] * width)
    for entry in entries:
        total.values = [a + b for a, b in zip(total.values, entry.values, strict=True)]
    return total


def ranked(entries: Sequence[BreakdownEntry], by: int | None) -> list[BreakdownEntry]:
    """Most first in column ``by``, then by name; by name alone without one."""
    if by is None:
        return sorted(entries, key=lambda item: item.name)
    return sorted(entries, key=lambda item: (-item.values[by], item.name))


def write_dashboard(
    book: Any,
    fmt: dict[str, Any],
    blocks: Sequence[DashBlock],
    breakdown: DashBreakdown | None,
    title: str = "Dashboard",
) -> None:
    """The client dashboard: labelled blocks, then the breakdown table."""
    sheet = book.add_worksheet(title)
    sheet.set_column(0, 0, 42)
    sheet.set_column(1, 1, 16)
    widest = max(
        [DASH_ORIGIN + len(b.headers) for b in blocks]
        + [len(breakdown.headers) - 1 if breakdown else 0, DASH_ORIGIN]
    )
    sheet.set_column(2, widest, 15)
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

    if breakdown is None:
        return
    sheet.set_row(row, 32)
    _write_dash_headers(sheet, fmt, row, 0, breakdown.headers)
    row += 1
    _write_breakdown_row(sheet, fmt, row, breakdown.total, breakdown.kinds, total=True)
    for entry in breakdown.entries:
        row += 1
        _write_breakdown_row(sheet, fmt, row, entry, breakdown.kinds, total=False)
    if breakdown.entries:
        first = row - len(breakdown.entries) - 1
        sheet.autofilter(first, 0, row, len(breakdown.headers) - 1)


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
    kinds: tuple[str, ...],
    *,
    total: bool,
) -> None:
    text = fmt["dash_total_text"] if total else fmt["dash_text"]
    whole = fmt["dash_total_int"] if total else fmt["dash_int"]
    amount = fmt["dash_total"] if total else fmt["dash_money"]
    write_text(sheet, row, 0, entry.name, text)
    for col, (value, kind) in enumerate(zip(entry.values, kinds, strict=True), start=1):
        if kind == "int" and isinstance(value, int):
            sheet.write_number(row, col, value, whole)
        else:
            style = whole if kind == "int" else amount
            sheet.write_number(row, col, num(Decimal(value)), style)


# --- An upload's rows, where its copy puts them ----------------------------


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
    # Each column's normalised header, in order.
    header_keys: tuple[str, ...] = ()

    def get(self, header: str) -> Any:
        return _at(self.values, self.columns.get(header))

    def cell(
        self, header: str, title: str, hidden: frozenset[str] = frozenset()
    ) -> str | None:
        """The reference of this row's cell under ``header`` on its copy, the
        sheet called ``title``, which leaves out the ``hidden`` headers' columns."""
        col = self.columns.get(header)
        if col is None:
            return None
        if hidden:
            if col < len(self.header_keys) and self.header_keys[col] in hidden:
                return None
            col -= sum(1 for key in self.header_keys[:col] if key in hidden)
        return f"{sheet_ref(title)}!{letter(col)}{self.excel_row}"

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
    keys: tuple[str, ...] = ()
    for source in iter_source_rows(path, sheet_names, schema):
        if source.values is None or source.header_at is None:
            continue
        if source.cursor == source.header_at:
            columns, customers = _header_columns(source.values)
            keys = tuple(normalize_header(value) for value in source.values)
        elif source.cursor > source.header_at:
            yield PortalRecord(
                sheet=source.sheet,
                ordinal=source.cursor - source.header_at,
                excel_row=source.cursor + 1,
                values=source.values,
                columns=columns,
                customers=customers,
                header_keys=keys,
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


# --- Copies of the uploads --------------------------------------------------


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


@dataclass(frozen=True)
class CopyEdits:
    """How a copy differs from its upload: the columns it leaves out, by their
    normalised header, and the columns it adds after the last one."""

    hidden: frozenset[str] = frozenset()
    # Each added column's header and width.
    added: tuple[tuple[str, int], ...] = ()
    # Writes a data row's added cells: the sheet, the row, its first added
    # column, and the copy's column of each of the sheet's headers.
    write: Callable[[Any, int, int, dict[str, int]], None] | None = None


def write_copy(
    book: Any,
    fmt: dict[str, Any],
    title: str,
    path: Path,
    sheet_names: list[str],
    schema: SheetSchema,
    header_style: str,
    edits: CopyEdits | None = None,
) -> None:
    """Copy every detected sheet of one upload into a single worksheet, as it
    arrived, but for its ``edits``.

    A single source sheet gets its header frozen and filtered. A column left
    out is left out of every row of its sheet, the rows above the header too,
    and the columns after it move left; those rows wait for the header. The
    added columns follow the header's last column, on each row after it that
    has a value, and the filter takes them in.
    """
    edits = edits or CopyEdits()
    sheet = book.add_worksheet(title)
    widths: list[int] = []
    filter_at: tuple[int, int] | None = None
    end = 0
    frozen = False
    # The sheet being copied: the rows waiting for its header, the columns it
    # leaves out, where each header landed, and where the added columns go.
    current: str | None = None
    waiting: list[SourceRow] = []
    drop: frozenset[int] = frozenset()
    landed: dict[str, int] = {}
    first_added: int | None = None

    for source in iter_source_rows(path, sheet_names, schema):
        end = source.cursor + 1
        if source.values is None:
            write_text(sheet, source.cursor, 0, source.sheet, fmt["title"])
            continue
        if source.sheet != current:
            current, waiting, drop, landed, first_added = (
                source.sheet,
                [],
                frozenset(),
                {},
                None,
            )
        if source.single and source.header_at is not None and not frozen:
            sheet.freeze_panes(source.header_at + 1, 0)
            frozen = True
        is_header = source.header_at is not None and source.cursor == source.header_at
        if (
            edits.hidden
            and source.header_at is not None
            and source.cursor < source.header_at
        ):
            waiting.append(source)
            continue
        if is_header:
            drop = _dropped(source.values, edits.hidden)
            for above in waiting:
                assert above.values is not None
                _write_source_row(
                    sheet,
                    fmt,
                    above.cursor,
                    above.values,
                    widths,
                    False,
                    header_style,
                    drop,
                )
            waiting = []
        last_col, wrote = _write_source_row(
            sheet,
            fmt,
            source.cursor,
            source.values,
            widths,
            is_header,
            header_style,
            drop,
        )
        if is_header:
            landed = _landed(source.values, drop)
            first_added = last_col + 1
            for offset, (header, width) in enumerate(edits.added):
                col = first_added + offset
                sheet.write_string(source.cursor, col, header, fmt[header_style])
                if len(widths) <= col:
                    widths.extend([12] * (col + 1 - len(widths)))
                widths[col] = width
            if source.single:
                filter_at = (source.cursor, last_col + len(edits.added))
        elif (
            wrote
            and edits.write is not None
            and first_added is not None
            and source.header_at is not None
            and source.cursor > source.header_at
        ):
            edits.write(sheet, source.cursor, first_added, landed)

    for index, width in enumerate(widths):
        sheet.set_column(index, index, width)
    if filter_at is not None and end > filter_at[0] + 1:
        sheet.autofilter(filter_at[0], 0, end - 1, filter_at[1])


def _dropped(header: tuple[Any, ...], hidden: frozenset[str]) -> frozenset[int]:
    """The columns of a sheet whose header is one the copy leaves out."""
    if not hidden:
        return frozenset()
    return frozenset(
        index for index, value in enumerate(header) if normalize_header(value) in hidden
    )


def _landed(header: tuple[Any, ...], drop: frozenset[int]) -> dict[str, int]:
    """Where each of a sheet's headers landed on the copy: its first column."""
    landed: dict[str, int] = {}
    shift = 0
    for index, value in enumerate(header):
        if index in drop:
            shift += 1
            continue
        key = normalize_header(value)
        if key and key not in landed:
            landed[key] = index - shift
    return landed


def _write_source_row(
    sheet: Any,
    fmt: dict[str, Any],
    row_index: int,
    row: tuple[Any, ...],
    widths: list[int],
    is_header: bool,
    header_style: str,
    drop: frozenset[int] = frozenset(),
) -> tuple[int, bool]:
    """Write one source row, but for the ``drop`` columns, and return its last
    occupied column on the copy, 0-based, and whether it had a value."""
    last = 0
    wrote = False
    shift = 0
    for col, value in enumerate(row):
        if col in drop:
            shift += 1
            continue
        if value is None or value == "":
            continue
        last = col - shift
        wrote = True
        _write_source_cell(sheet, fmt, row_index, last, value, is_header, header_style)
        _note_width(widths, last, value, is_header)
    return last, wrote


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
