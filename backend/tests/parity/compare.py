"""Cell-by-cell comparison of two report workbooks.

Byte comparison is useless for .xlsx: the file carries its creation time. What
the client sees is compared instead: sheet order, every cell's value or formula,
how it is formatted, and the sheet's layout (merges, widths, heights, frozen
panes, filters).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.worksheet.formula import ArrayFormula
from openpyxl.worksheet.worksheet import Worksheet

MAX_DIFFERENCES = 50


def _colour(colour: Any) -> str | None:
    if colour is None:
        return None
    if colour.type == "rgb":
        return str(colour.rgb)
    if colour.type == "theme":
        return f"theme:{colour.theme}:{colour.tint}"
    return f"{colour.type}:{colour.value}"


def _value(value: Any) -> Any:
    # An array formula (the cashback lookups) is an object with no equality of
    # its own; what it means is its range and its text.
    if isinstance(value, ArrayFormula):
        return ("array", value.ref, value.text)
    return value


def _cell_state(cell: Cell | MergedCell) -> tuple[Any, ...]:
    if isinstance(cell, MergedCell):
        return ("<merged>",)
    fill = cell.fill.fgColor if cell.fill.patternType else None
    border = cell.border
    return (
        _value(cell.value),
        cell.number_format,
        bool(cell.font.b),
        _colour(cell.font.color),
        cell.fill.patternType,
        _colour(fill),
        bool(cell.alignment.wrap_text),
        cell.alignment.horizontal,
        cell.alignment.vertical,
        cell.font.sz,
        cell.font.name,
        tuple(
            side.style if side is not None else None
            for side in (border.left, border.right, border.top, border.bottom)
        ),
    )


_STATE_NAMES = (
    "value",
    "number_format",
    "bold",
    "font_colour",
    "fill",
    "fill_colour",
    "wrap",
    "align",
    "valign",
    "font_size",
    "font_name",
    "borders",
)


def _layout(sheet: Worksheet) -> dict[str, Any]:
    return {
        "dimensions": (sheet.max_row, sheet.max_column),
        "freeze_panes": sheet.freeze_panes,
        "merged": sorted(str(r) for r in sheet.merged_cells.ranges),
        "widths": {
            key: dim.width
            for key, dim in sorted(sheet.column_dimensions.items())
            if dim.customWidth
        },
        "heights": {
            key: dim.height
            for key, dim in sorted(sheet.row_dimensions.items())
            if dim.height is not None
        },
        "autofilter": sheet.auto_filter.ref,
        "gridlines": sheet.sheet_view.showGridLines,
    }


def _sheet_differences(name: str, ours: Worksheet, theirs: Worksheet) -> Iterator[str]:
    ours_layout, theirs_layout = _layout(ours), _layout(theirs)
    for key, value in ours_layout.items():
        if value != theirs_layout[key]:
            yield f"{name}: {key} differs: {value!r} != {theirs_layout[key]!r}"

    rows = max(ours.max_row, theirs.max_row)
    cols = max(ours.max_column, theirs.max_column)
    for row in range(1, rows + 1):
        for col in range(1, cols + 1):
            a = _cell_state(ours.cell(row, col))
            b = _cell_state(theirs.cell(row, col))
            if a == b:
                continue
            ref = ours.cell(row, col).coordinate
            if len(a) != len(b):
                yield f"{name}!{ref}: {a!r} != {b!r}"
                continue
            for label, x, y in zip(_STATE_NAMES, a, b, strict=False):
                if x != y:
                    yield f"{name}!{ref} {label}: {x!r} != {y!r}"


def workbook_differences(ours: Path, theirs: Path) -> list[str]:
    """Every visible difference between two workbooks, at most MAX_DIFFERENCES."""
    a = load_workbook(ours)
    b = load_workbook(theirs)
    found: list[str] = []
    if a.sheetnames != b.sheetnames:
        found.append(f"sheets differ: {a.sheetnames} != {b.sheetnames}")
    for name in a.sheetnames:
        if name not in b.sheetnames:
            continue
        for difference in _sheet_differences(name, a[name], b[name]):
            found.append(difference)
            if len(found) >= MAX_DIFFERENCES:
                return found
    return found


def json_differences(ours: Any, theirs: Any, path: str = "$") -> list[str]:
    """Paths at which two JSON documents differ, at most MAX_DIFFERENCES."""
    found: list[str] = []

    def walk(a: Any, b: Any, at: str) -> None:
        if len(found) >= MAX_DIFFERENCES:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a or key not in b:
                    found.append(
                        f"{at}.{key}: only in {'ours' if key in a else 'baseline'}"
                    )
                else:
                    walk(a[key], b[key], f"{at}.{key}")
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                found.append(f"{at}: length {len(a)} != {len(b)}")
            for index, (x, y) in enumerate(zip(a, b, strict=False)):
                walk(x, y, f"{at}[{index}]")
        elif a != b:
            found.append(f"{at}: {a!r} != {b!r}")

    walk(ours, theirs, path)
    return found


def sheet_differences(ours: Path, theirs: Path, name: str) -> list[str]:
    """Every visible difference between one sheet of two workbooks."""
    a = load_workbook(ours)
    b = load_workbook(theirs)
    if name not in a.sheetnames or name not in b.sheetnames:
        return [f"no sheet {name!r}: {a.sheetnames} / {b.sheetnames}"]
    return list(_sheet_differences(name, a[name], b[name]))[:MAX_DIFFERENCES]
