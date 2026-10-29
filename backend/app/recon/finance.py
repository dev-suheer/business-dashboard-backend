"""The finance report: cashback lookups on the Orders sheet, for every category.

The finance report is the spin (cashback) report, one export per category.
When it is uploaded, the report is copied as its own sheet and each Orders row
looks its order up in that sheet's Order ID. The five cashback columns are
formulas, so opening a cell shows the lookup. The report is never read into a
calculation and never moves a payable.

What the key is belongs to the category: food and travel look up the row's
SG Order ID cell; eShop looks up the digits of the order number.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.recon.excel import (
    ColumnSpec,
    SheetFormatError,
    SheetSchema,
    detect_sheets,
    find_header,
    open_workbook,
)
from app.recon.models import SheetManifest
from app.recon.sheets import Column, letter, source_sheets

# The cashback file is a spin report. A finished recon workbook also carries
# the reward columns, bolted onto its Orders sheet; Spin Event ID is what the
# source table has and that sheet does not.
FINANCE_SCHEMA = SheetSchema(
    name="finance report",
    fingerprint=(
        "Spin Event ID",
        "Order ID",
        "Reward Amount (SAR)",
        "Reward Code",
        "Reward Type",
        "Outcome",
        "Vertical Name",
    ),
    exclude=("MATCH ID'S", "Match ID's", "Partner Order ID"),
    columns=(
        ColumnSpec("order_id", ("Order ID",), required=True),
        ColumnSpec("reward_amount", ("Reward Amount (SAR)",), required=True),
        ColumnSpec("vertical_name", ("Vertical Name",), required=True),
        ColumnSpec("reward_code", ("Reward Code",), required=True),
        ColumnSpec("reward_type", ("Reward Type",), required=True),
        ColumnSpec("outcome", ("Outcome",), required=True),
    ),
)

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


def finance_layout(path: Path | None) -> FinanceLayout | None:
    """The spin table inside a finance upload, or None when none was supplied.

    Order ID is unique on the report, so the first exact match is the reward.
    A worked recon file that already has the reward columns bolted on does not
    match: it has no Spin Event ID.
    """
    if path is None:
        return None
    with open_workbook(path) as workbook:
        names = detect_sheets(workbook, FINANCE_SCHEMA)
        if not names:
            raise SheetFormatError(_NOT_FOUND)
        match = find_header(workbook[names[0]], FINANCE_SCHEMA)
        if match is None:
            raise SheetFormatError(_NOT_FOUND)
        missing = match.missing(FINANCE_SCHEMA)
        if missing:
            raise SheetFormatError(
                "'finance' is missing column(s): " + ", ".join(missing)
            )
        return FinanceLayout(sheets=list(names), columns=dict(match.columns))


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
        formula = cashback_formula(key, key_col, letter(layout.columns[field_name]))
        style = fmt["money"] if spec[4] == "money" else None
        sheet.write_formula(row, col, formula, style)


def cashback_formula(key: str, key_col: str, return_col: str) -> str:
    """Exact lookup. The key is text; the report's Order ID may be a number.

    Both functions carry their ``_xlfn.`` prefix: Excel files name every
    function added after Excel 2007 that way, and a bare ``IFNA`` reads as
    ``#NAME?``. xlsxwriter adds no prefix to a formula that already has one,
    so both are written here.
    """
    lookup = f"'{FINANCE_TITLE}'!{key_col}:{key_col}"
    found = f"'{FINANCE_TITLE}'!{return_col}:{return_col}"
    number = f"IFERROR(VALUE({key}),{key})"
    return (
        f'=IF({key}="","NA",_xlfn.IFNA('
        f"_xlfn.XLOOKUP({number},{lookup},{found},NA(),0),"
        f'_xlfn.XLOOKUP({key}&"",{lookup},{found},"NA",0)))'
    )


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
        (("finance", FINANCE_TITLE, FINANCE_SCHEMA, "cashback_header"),),
    )
