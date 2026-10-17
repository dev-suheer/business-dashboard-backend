"""Workbook formats and the few writing helpers every report shares.

Each category builds its own report, but they are one product and must look like
it: one palette, one money format, one way to open a table. That is what lives
here. The ``constant_memory`` rule the reports are written under — rows go out
strictly top to bottom and are never revisited — is why bad values are styled as
they are written rather than by a conditional format.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import xlsxwriter

MONEY = "#,##0.00"
RATE = "0.00%"


def formats(book: xlsxwriter.Workbook) -> dict[str, Any]:
    return {
        "title": book.add_format({"bold": True, "font_size": 14}),
        "header": book.add_format(
            {"bold": True, "bg_color": "#1F3864", "font_color": "white", "border": 1}
        ),
        "label": book.add_format({"bold": True}),
        "money": book.add_format({"num_format": MONEY}),
        "money_bold": book.add_format({"num_format": MONEY, "bold": True}),
        # Applied directly while writing rather than as a conditional format:
        # constant_memory mode cannot revisit a row once it is flushed.
        "money_bad": book.add_format(
            {"num_format": MONEY, "bg_color": "#FFC7CE", "font_color": "#9C0006"}
        ),
        "money_warn": book.add_format(
            {"num_format": MONEY, "bg_color": "#FFEB9C", "font_color": "#9C5700"}
        ),
        "rate": book.add_format({"num_format": RATE}),
        "date": book.add_format({"num_format": "yyyy-mm-dd"}),
        "high": book.add_format({"bold": True, "font_color": "#9C0006"}),
        "info": book.add_format({"font_color": "#595959"}),
    }


def num(value: Decimal | None) -> float | str:
    """xlsxwriter cannot write a Decimal, so numbers cross this boundary once."""
    return float(value) if value is not None else ""


def table(
    sheet: Any, fmt: dict[str, Any], headers: Sequence[str], widths: Sequence[int]
) -> None:
    for col, (title, width) in enumerate(zip(headers, widths, strict=True)):
        sheet.write(0, col, title, fmt["header"])
        sheet.set_column(col, col, width)
    sheet.freeze_panes(1, 0)
    sheet.autofilter(0, 0, 0, len(headers) - 1)


def heading(sheet: Any, fmt: dict[str, Any], row: int, text: str) -> int:
    """Write a section heading and return the row after it."""
    sheet.write(row, 0, text, fmt["title"])
    return row + 1


def kv(
    sheet: Any,
    fmt: dict[str, Any],
    row: int,
    label: str,
    value: Any,
    *,
    style: str | None = None,
) -> int:
    """Write one label/value line of a summary block, returning the next row."""
    sheet.write(row, 0, label, fmt["label"])
    sheet.write(row, 1, value, fmt[style] if style else None)
    return row + 1
