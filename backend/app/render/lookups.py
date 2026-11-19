"""Look-up columns: a table's cells that find their value in a copied sheet.

A look-up column (``OutputColumn.lookup``) is written after a table's own
columns when the sheet it looks in, a copy of an upload, is in the report.
Each of its cells is an Excel lookup of the row's key in that copy, so opening
a cell shows where its value comes from; no formula of the setup reads it, so
it never moves a figure. A finance report's cashback is one: the finance
report is a reference file, copied into the report, and five look-up columns
of the Orders sheet find each order in it.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config_model.models import OutputColumn
from app.recon.cleaning import normalize_header
from app.recon.excel import (
    SheetFormatError,
    SheetSchema,
    detect_sheets,
    find_header,
    open_workbook,
)
from app.recon.sheets import letter


@dataclass(frozen=True)
class CopyLayout:
    """A copied upload's sheets, the column each of its columns lands in on
    the copy, and the copy's tab, which every look-up into it names."""

    sheets: list[str]
    columns: dict[str, int]
    title: str


def copy_layout(
    path: Path,
    schema: SheetSchema,
    role: str,
    title: str = "",
    hidden: frozenset[str] = frozenset(),
) -> CopyLayout:
    """Where an upload's sheet is, and where its copy puts each column.

    The first sheet found has the header; a copy leaving out the ``hidden``
    headers moves the columns after them left. A file without the sheet, or
    without a column the sheet requires, is refused with the reason.
    """
    with open_workbook(path) as workbook:
        names = detect_sheets(workbook, schema)
        match = find_header(workbook[names[0]], schema) if names else None
        if match is None:
            raise SheetFormatError(_not_found(schema, role))
        missing = match.missing(schema)
        if missing:
            raise SheetFormatError(
                f"'{role}' is missing column(s): " + ", ".join(missing)
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
        return CopyLayout(sheets=list(names), columns=columns, title=title)


def _not_found(schema: SheetSchema, role: str) -> str:
    expected = list(schema.fingerprint)
    listed = (
        ", ".join(expected[:-1]) + " and " + expected[-1]
        if len(expected) > 1
        else "".join(expected)
    )
    return (
        f"No {schema.name} table found in '{role}'. Expected columns {listed}."
        if listed
        else f"No {schema.name} table found in '{role}'."
    )


def write_lookups(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    first_col: int,
    columns: list[tuple[OutputColumn, CopyLayout, str | None]],
) -> None:
    """Each look-up column's cell on ``row``, from ``first_col`` on: the
    lookup of its key (an Excel expression: a cell such as ``C2``, or a quoted
    literal such as ``"8986"``) in its copy. No key is NA."""
    for offset, (column, layout, key) in enumerate(columns):
        assert column.lookup is not None
        col = first_col + offset
        if key is None:
            sheet.write_string(row, col, "NA")
            continue
        formula = lookup_formula(
            key,
            letter(layout.columns[column.lookup.match]),
            letter(layout.columns[column.lookup.value]),
            layout.title,
        )
        sheet.write_formula(row, col, formula, _number_format(fmt, column.kind))


def _number_format(fmt: dict[str, Any], kind: str) -> Any:
    """A looked-up number takes its kind's format; anything else is plain."""
    return fmt.get(kind) if kind in ("money", "rate", "date") else None


def lookup_formula(key: str, key_col: str, return_col: str, title: str) -> str:
    """Exact lookup. The key is text; the copy's column may hold a number.

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
