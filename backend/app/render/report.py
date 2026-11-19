"""The report workbook, written from a category's output configuration.

Each tab of ``config.output`` is written in order by the writer for its kind.
A table tab is a column list and a stack of sections — row sets, a totals row,
fixed blocks of labels, figures and formulas — each placed below the one
before. Every figure and row comes from the run's ``ReportData``.

Written with ``constant_memory``: rows go out strictly top to bottom, which the
section stack guarantees.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, get_args

import xlsxwriter

from app.config_model.models import (
    CategoryConfig,
    Cell,
    OutputColumn,
    OutputTab,
    ReportFormat,
    Section,
    SheetLookup,
    cell_links,
)
from app.config_model.runtime import schema_of
from app.recon.cleaning import normalize_header
from app.recon.sheets import (
    Column,
    CopyEdits,
    DashBlock,
    DashBreakdown,
    header_style,
    letter,
    report_formats,
    sheet_ref,
    write_cell,
    write_column_headers,
    write_copy,
    write_dashboard,
    write_order_row,
    write_styled,
)
from app.recon.xlsx import num, table
from app.render.data import ReportData, TableLayout, TableRows
from app.render.lookups import CopyLayout, copy_layout, write_lookups

# Kinds a totals row sums, as write_sum_row always has.
_SUMMED = frozenset({"money", "diff"})
# The formats a column's style can name.
_FORMATS = frozenset(get_args(ReportFormat))
_PLACEHOLDER = re.compile(r"\{(col|row)(?::([A-Za-z0-9_]+))?\}")
# In an Excel formula on a copy: {col:header} for that header's column letter,
# {row} for this row.
_COPY_PLACEHOLDER = re.compile(r"\{col:([^{}]+)\}|\{row\}")


class ConfigurationError(ValueError):
    """The output configuration names something the run does not have."""


def render(
    config: CategoryConfig,
    data: ReportData,
    path: Path,
    sources: dict[str, Path] | None = None,
) -> Path:
    """Write the report. ``sources`` maps upload roles to the uploaded files.

    The copies of the uploads, and a table reading the uploads (an upload's own
    rows, or its contacts), need the uploads, so they are written only when
    ``sources`` is given, as the run service always does.
    """
    # The look-up columns read the copies they look in by their tab names, the
    # columns where each copy puts them: a workbook without a copy has none of
    # the look-up columns that look in it.
    copies = _copy_layouts(config, sources)

    book = xlsxwriter.Workbook(
        str(path),
        {
            "constant_memory": True,
            "in_memory": False,
            "default_date_format": "yyyy-mm-dd",
        },
    )
    # A run near the upload limit makes sheets of more than 4 GB before
    # compression, which the .xlsx's zip can hold only with ZIP64. Excel reads it;
    # a smaller report is written exactly as without it.
    book.use_zip64()
    try:
        fmt = report_formats(book)
        # Where each table written so far put its rows, by the tab's key.
        layouts: dict[str, TableLayout] = {}
        for tab in config.output.tabs:
            if not _included(tab, data, sources):
                continue
            if tab.kind == "dashboard":
                _dashboard(book, fmt, tab, data)
            elif tab.copies:
                assert sources is not None
                _copy(book, fmt, tab, data, sources)
            else:
                layouts[tab.key] = _table(book, fmt, tab, data, copies, layouts)
    finally:
        book.close()
    return path


def _copy_layouts(
    config: CategoryConfig, sources: dict[str, Path] | None
) -> dict[str, CopyLayout]:
    """Each copy a look-up column looks in, by its tab's key, when its upload
    came with the run."""
    layouts: dict[str, CopyLayout] = {}
    for tab in config.looked_in():
        assert tab.source is not None
        path = (sources or {}).get(tab.source)
        sheet = config.sheet(tab.source, tab.sheet) if tab.sheet else None
        if path is None or sheet is None:
            continue
        layouts[tab.key] = copy_layout(
            path, schema_of(sheet), tab.source, tab.title, _hidden(tab)
        )
    return layouts


def _included(
    tab: OutputTab,
    data: ReportData,
    sources: dict[str, Path] | None,
) -> bool:
    if tab.copies and not sources:
        return False
    if tab.kind == "table" and not sources and _reads_uploads(tab):
        return False
    if tab.requires == "wallet":
        return data.wallet is not None
    if tab.requires is not None:
        return bool(sources) and tab.requires in (sources or {})
    return True


def _reads_uploads(tab: OutputTab) -> bool:
    return any(
        s.kind == "rows" and (s.upload is not None or s.contacts is not None)
        for s in tab.sections
    )


# --- Dashboard ----------------------------------------------------------------


def _dashboard(
    book: Any, fmt: dict[str, Any], tab: OutputTab, data: ReportData
) -> None:
    blocks = tuple(
        DashBlock(
            label=block.label,
            value_label=block.value_label,
            headers=tuple(block.headers),
            values=tuple((_metric(data, v.metric), v.kind) for v in block.values),
            tall=block.tall,
        )
        for block in tab.blocks
    )
    breakdown = None
    spec, rows = tab.breakdown, data.breakdowns.get(tab.key)
    if spec is not None and rows is not None:
        breakdown = DashBreakdown(
            headers=(spec.name_header, *(c.header for c in spec.columns)),
            kinds=tuple(c.kind for c in spec.columns),
            total=rows.total,
            entries=rows.rows,
        )
    write_dashboard(book, fmt, blocks, breakdown, tab.title)


def _metric(data: ReportData, key: str) -> Any:
    if key not in data.metrics:
        raise ConfigurationError(f"The report has no figure named {key!r}")
    return data.metrics[key]


# --- Table --------------------------------------------------------------------


class _Cursor:
    """Where the next section goes, and what the sections before it left behind."""

    def __init__(self) -> None:
        self.row = 1  # below the header row
        self.totals: int | None = None


def _table(
    book: Any,
    fmt: dict[str, Any],
    tab: OutputTab,
    data: ReportData,
    copies: dict[str, CopyLayout],
    layouts: dict[str, TableLayout],
) -> TableLayout:
    columns: list[Column] = [
        (c.key, c.header, c.width, c.group, c.kind) for c in tab.columns
    ]
    # The look-up columns whose copy is in the report, after the table's own.
    looked: list[tuple[OutputColumn, CopyLayout]] = [
        (c, copies[c.lookup.sheet])
        for c in tab.lookup_columns
        if c.lookup is not None and c.lookup.sheet in copies
    ]
    specs = [*tab.columns, *(c for c, _layout in looked)]
    shown: list[Column] = [(c.key, c.header, c.width, c.group, c.kind) for c in specs]
    sheet = book.add_worksheet(tab.title)
    if tab.header == "plain":
        table(sheet, fmt, [c.header for c in specs], [c.width for c in specs])
    else:
        styles = [header_style(book, fmt, c.group, c.color) for c in specs]
        write_column_headers(sheet, fmt, shown, styles)

    layout = TableLayout(
        title=tab.title,
        letters={key: letter(i) for i, (key, *_rest) in enumerate(columns)},
    )
    cursor = _Cursor()
    for s, section in enumerate(tab.sections):
        if section.when is not None and section.when not in data.listed:
            continue
        start = cursor.row + section.gap
        if section.kind == "rows":
            part = data.tables.get(tab.key, {}).get(s)
            if part is not None:
                _rows(
                    sheet,
                    fmt,
                    tab,
                    section,
                    part,
                    start,
                    columns,
                    shown,
                    data,
                    looked,
                    layout,
                    layouts,
                    cursor,
                )
        elif section.kind == "totals":
            _totals(sheet, fmt, section, start, columns, layout, cursor)
        else:
            _block(sheet, fmt, section, start, data, layout, cursor)
    return layout


def _rows(
    sheet: Any,
    fmt: dict[str, Any],
    tab: OutputTab,
    section: Section,
    part: TableRows,
    start: int,
    columns: list[Column],
    shown: list[Column],
    data: ReportData,
    looked: list[tuple[OutputColumn, CopyLayout]],
    layout: TableLayout,
    layouts: dict[str, TableLayout],
    cursor: _Cursor,
) -> None:
    excel = {c.key: c.excel for c in tab.columns if c.excel is not None}
    # Each row with its Excel formulas; a row linking to a cell that is not
    # there is left out.
    written: list[tuple[int, dict[str, str]]] = []
    for index in range(len(part.values)):
        formulas = _formulas(excel, section, part, index, layouts) if excel else {}
        if formulas is not None:
            written.append((index, formulas))
    if not written:
        return
    row = start
    if section.title:
        sheet.write(row, 0, section.title, fmt["title"])
        row += 1
    placed: list[int] = []
    at: dict[int, int] = {}
    tolerances = data.tolerances if section.tolerances else None
    for index, formulas in written:
        values = part.values[index]
        styles = part.styles[index] if part.styles is not None else None
        if styles is None and not formulas:
            write_order_row(sheet, fmt, row, columns, values, tolerances=tolerances)
        else:
            _row(sheet, fmt, row, columns, values, styles or {}, formulas, tolerances)
        if looked and section.lookups:
            write_lookups(
                sheet,
                fmt,
                row,
                len(columns),
                [
                    (column, copy, _lookup_key(column.lookup, layout, row, values))
                    for column, copy in looked
                    if column.lookup is not None
                ],
            )
        placed.append(row)
        if part.members:
            at[part.members[index]] = row
        row += 1
    if section.set is not None:
        layout.rows[section.set] = placed
        layout.at[section.set] = at
    if section.autofilter:
        sheet.autofilter(0, 0, row - 1, len(shown) - 1)
    cursor.row = row


def _formulas(
    excel: dict[str, str],
    section: Section,
    part: TableRows,
    index: int,
    layouts: dict[str, TableLayout],
) -> dict[str, str] | None:
    """A row's Excel formulas, each {cell:tab.column} made the cell it names;
    None when one of them is not there."""
    found: dict[str, str] = {}
    for key, template in excel.items():
        formula = template
        for placeholder, tab_key, column in cell_links(template):
            if section.upload is not None:
                ref = part.links[index].get(placeholder)
            else:
                assert section.set is not None
                ref = _cell_on(
                    layouts.get(tab_key), section.set, part.members[index], column
                )
            if ref is None:
                return None
            formula = formula.replace(placeholder, ref)
        found[key] = formula
    return found


def _cell_on(
    layout: TableLayout | None, set_name: str, member: int, column: str
) -> str | None:
    """Where a table put a member of a set, in one of its columns."""
    if layout is None or column not in layout.letters:
        return None
    row = layout.at.get(set_name, {}).get(member)
    if row is None:
        return None
    return f"{sheet_ref(layout.title)}!{layout.letters[column]}{row + 1}"


def _row(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    columns: list[Column],
    values: dict[str, Any],
    styles: dict[str, str],
    formulas: dict[str, str],
    tolerances: dict[str, Decimal] | None,
) -> None:
    """A row with styled columns or Excel formulas: a formula, in its style if
    it has one; a styled cell in the format its style names; the others as
    their kind says."""
    for col, (key, _header, _width, group, kind) in enumerate(columns):
        style = _style(fmt, key, styles)
        if key in formulas:
            sheet.write_formula(row, col, formulas[key], style)
        elif style is not None:
            write_styled(sheet, row, col, values.get(key), style)
        else:
            tolerance = (tolerances or {}).get(key, Decimal("0.01"))
            write_cell(
                sheet, fmt, row, col, values.get(key), group, kind, tolerance=tolerance
            )


def _style(fmt: dict[str, Any], key: str, styles: dict[str, str]) -> Any:
    """The format a column's style gives a cell; None when it has no style, or
    its style gives blank on this row: the cell is then written as the
    column's kind says."""
    name = styles.get(key)
    return _format(fmt, key, name) if name else None


def _format(fmt: dict[str, Any], key: str, name: str) -> Any:
    if name not in _FORMATS:
        raise ConfigurationError(f"The column {key!r} names no format {name!r}")
    return fmt[name]


def _lookup_key(
    lookup: SheetLookup, layout: TableLayout, row: int, values: dict[str, Any]
) -> str | None:
    """A look-up's key as an Excel expression: a cell on this row, or a literal."""
    if lookup.key_column is not None:
        return f"{layout.letters[lookup.key_column]}{row + 1}"
    if lookup.key_value is not None:
        value = values.get(lookup.key_value)
        return f'"{value}"' if value is not None else None
    return None


def _totals(
    sheet: Any,
    fmt: dict[str, Any],
    section: Section,
    row: int,
    columns: list[Column],
    layout: TableLayout,
    cursor: _Cursor,
) -> None:
    assert section.set is not None
    placed = layout.rows.get(section.set, [])
    if not placed:
        return
    first, last = placed[0] + 1, placed[-1] + 1
    if section.label:
        sheet.write(row, 0, section.label.format(count=len(placed)), fmt["label"])
    for col, (key, _header, _width, _group, kind) in enumerate(columns):
        if kind in _SUMMED:
            ref = letter(col)
            style = fmt[section.formats.get(key, "total")]
            sheet.write_formula(row, col, f"=SUM({ref}{first}:{ref}{last})", style)
    cursor.totals = row
    cursor.row = row + 1


def _block(
    sheet: Any,
    fmt: dict[str, Any],
    section: Section,
    start: int,
    data: ReportData,
    layout: TableLayout,
    cursor: _Cursor,
) -> None:
    row = start
    for block_row in section.rows:
        if block_row.height is not None:
            sheet.set_row(row, block_row.height)
        for cell in block_row.cells:
            _cell(sheet, fmt, row, cell, data, layout, cursor)
        row += 1
    cursor.row = row


def _column(ref: int | str, layout: TableLayout) -> int:
    if isinstance(ref, int):
        return ref
    if ref not in layout.letters:
        raise ConfigurationError(f"The table has no column {ref!r}")
    return list(layout.letters).index(ref)


def _cell(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    cell: Cell,
    data: ReportData,
    layout: TableLayout,
    cursor: _Cursor,
) -> None:
    col = _column(cell.col, layout)
    style = fmt[cell.format] if cell.format else None
    value: Any
    if cell.value is not None:
        value = _cell_value(data.run_value(cell.value))
        if cell.to_col is not None:
            to = _column(cell.to_col, layout)
            sheet.merge_range(row, col, row, to, value, style)
        else:
            _write_value(sheet, row, col, value, style)
        return
    if cell.formula is not None:
        value = _formula(cell.formula, row, layout, cursor)
    elif cell.metric is not None:
        value = num(_metric(data, cell.metric))
    else:
        value = (cell.text or "").format_map(data.texts)

    if cell.to_col is not None:
        sheet.merge_range(row, col, row, _column(cell.to_col, layout), value, style)
    elif cell.formula is not None:
        sheet.write_formula(row, col, value, style)
    else:
        sheet.write(row, col, value, style)


def _cell_value(value: Any) -> Any:
    """A worked-out value as Excel takes it: a number, a moment, text, blank."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, Decimal):
        return num(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day)
    return value


def _write_value(sheet: Any, row: int, col: int, value: Any, style: Any) -> None:
    if value is None:
        sheet.write_blank(row, col, None, style)
    elif isinstance(value, bool):
        sheet.write_boolean(row, col, value, style)
    elif isinstance(value, int | float):
        sheet.write_number(row, col, value, style)
    elif isinstance(value, datetime):
        sheet.write_datetime(row, col, value, style)
    else:
        sheet.write_string(row, col, str(value), style)


def _formula(template: str, row: int, layout: TableLayout, cursor: _Cursor) -> str:
    def resolve(match: re.Match[str]) -> str:
        what, name = match.group(1), match.group(2)
        if what == "col":
            if name not in layout.letters:
                raise ConfigurationError(f"The table has no column {name!r}")
            return layout.letters[name]
        if name == "totals":
            if cursor.totals is None:
                raise ConfigurationError(
                    "A block names the totals row before one exists"
                )
            return str(cursor.totals + 1)
        return str(row + 1)

    return _PLACEHOLDER.sub(resolve, template)


# --- Copies of the uploads ------------------------------------------------------


def _copy(
    book: Any,
    fmt: dict[str, Any],
    tab: OutputTab,
    data: ReportData,
    sources: dict[str, Path],
) -> None:
    """An upload, as it arrived, but for the columns the tab leaves out and
    the ones it adds."""
    assert tab.source is not None
    copy = data.copies.get(tab.key)
    if copy is None or copy.schema is None:
        raise ConfigurationError(f"No {tab.source!r} sheet {tab.sheet!r} to copy")
    path = sources.get(tab.source)
    if path is None or not copy.sheets:
        return
    write_copy(
        book,
        fmt,
        tab.title,
        path,
        copy.sheets,
        copy.schema,
        tab.header_format or "header",
        CopyEdits(
            hidden=_hidden(tab),
            added=tuple((c.header, c.width) for c in tab.columns),
            write=_added(fmt, tab, data) if tab.columns else None,
        ),
    )


def _hidden(tab: OutputTab) -> frozenset[str]:
    return frozenset(normalize_header(header) for header in tab.hidden)


def _added(fmt: dict[str, Any], tab: OutputTab, data: ReportData) -> Any:
    """What writes a copy's added cells on one of its data rows."""
    copy = data.copies.get(tab.key)
    cells = copy.cells if copy is not None else {}

    def write(sheet: Any, row: int, first: int, landed: dict[str, int]) -> None:
        values, styles = cells.get(row, ({}, {}))
        for offset, column in enumerate(tab.columns):
            col = first + offset
            style = _style(fmt, column.key, styles)
            if column.excel is not None:
                formula = _copy_formula(column.excel, landed, row)
                if formula is not None:
                    sheet.write_formula(row, col, formula, style)
            elif style is not None:
                write_styled(sheet, row, col, values.get(column.key), style)
            else:
                write_cell(
                    sheet,
                    fmt,
                    row,
                    col,
                    values.get(column.key),
                    column.group,
                    column.kind,
                )

    return write


def _copy_formula(template: str, landed: dict[str, int], row: int) -> str | None:
    """An added column's Excel formula on one row of a copy; None when it names
    a header the row's sheet does not have."""
    missing = False

    def resolve(match: re.Match[str]) -> str:
        nonlocal missing
        if match.group(1) is None:
            return str(row + 1)
        col = landed.get(normalize_header(match.group(1)))
        if col is None:
            missing = True
            return ""
        return letter(col)

    formula = _COPY_PLACEHOLDER.sub(resolve, template)
    return None if missing else formula
