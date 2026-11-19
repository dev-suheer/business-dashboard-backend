"""Reading a template file: an upload's sheets and headers, for the editor.

A file given to the editor (to start an upload's columns, or to add a sheet)
is read once and not kept: its sheets, where each one's header is, its
headers, which of the configuration's columns each header feeds, and a
suggested name and type for each, the type guessed from the first rows.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet

from app.config_model.models import InputFile
from app.config_model.runtime import schema_of
from app.recon.cleaning import clean_text
from app.recon.excel import HEADER_SCAN_LIMIT, find_header, open_workbook

# The first rows a column's type is guessed from.
SAMPLE_ROWS = 12


def read_sheets(path: Path, upload: InputFile | None) -> list[dict[str, Any]]:
    """Each sheet of the workbook: the configured sheet it is (if any), its
    header row, its headers, and the configured field each header feeds."""
    sheets = []
    with open_workbook(path) as workbook:
        for name in workbook.sheetnames:
            sheets.append(_read_sheet(workbook[name], str(name), upload))
    return sheets


def _read_sheet(
    worksheet: Worksheet, name: str, upload: InputFile | None
) -> dict[str, Any]:
    kind: str | None = None
    header_row: int | None = None
    fields: dict[int, str] = {}
    for sheet in upload.sheets if upload else []:
        match = find_header(worksheet, schema_of(sheet))
        if match is not None:
            kind, header_row = sheet.key, match.row
            fields = {col: field for field, col in match.columns.items()}
            break
    rows = list(
        worksheet.iter_rows(max_row=HEADER_SCAN_LIMIT + SAMPLE_ROWS, values_only=True)
    )
    if header_row is None:
        header_row = next(
            (
                n
                for n, row in enumerate(rows, start=1)
                if sum(v is not None for v in row) >= 2
            ),
            None,
        )
    if header_row is None:
        return {"name": name, "kind": None, "header_row": None, "columns": []}
    header = rows[header_row - 1]
    data = rows[header_row : header_row + SAMPLE_ROWS]
    columns: list[dict[str, Any]] = []
    for col, raw in enumerate(header):
        title = clean_text(raw)
        if title is None:
            continue
        values = [row[col] if col < len(row) else None for row in data]
        columns.append(
            {
                "header": title,
                "field": fields.get(col),
                "suggested_field": _field_name(title),
                "suggested_type": _guess_type(values),
            }
        )
    return {"name": name, "kind": kind, "header_row": header_row, "columns": columns}


def _field_name(header: str) -> str:
    """A configuration field name for a header: "Order Total" is order_total."""
    name = re.sub(r"[^0-9a-z]+", "_", header.casefold()).strip("_")
    if not name or not name[0].isalpha():
        name = f"col_{name}" if name else "column"
    return name


def _guess_type(values: list[Any]) -> str:
    present = [v for v in values if v is not None and v != ""]
    if not present:
        return "text"
    if all(isinstance(v, datetime | date) for v in present):
        return "date"
    if all(
        isinstance(v, int | float | Decimal) and not isinstance(v, bool)
        for v in present
    ):
        return "decimal"
    return "text"
