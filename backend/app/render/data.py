"""What the renderer needs from a run: named figures, each table's rows, a few texts.

The output configuration says where each of these goes, and what each is, as
formulas on the run's orders (``app/render/generic.py``). Nothing in here
knows which category it is.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.recon.excel import SheetSchema
from app.recon.models import SheetManifest
from app.recon.sheets import BreakdownEntry
from app.recon.wallet import WalletReconciliation


@dataclass
class TableLayout:
    """Where a table sheet put things, for the sheets that point back at it."""

    title: str = "Orders"
    # Column key → its letter.
    letters: dict[str, str] = field(default_factory=dict)
    # Row set → the 0-based row index of each of its rows, in order.
    rows: dict[str, list[int]] = field(default_factory=dict)
    # Row set → each written member's place in the set → its 0-based row.
    at: dict[str, dict[int, int]] = field(default_factory=dict)


@dataclass
class TableRows:
    """The rows one part of a table lists, as it writes them."""

    # Each row's values by column key, plus the keys its look-ups work out.
    values: list[dict[str, Any]]
    # The same rows' format names by column key, for the columns with a
    # style; None when the table has none.
    styles: list[dict[str, str]] | None
    # A set's rows: each one's place in the set, by which another table's
    # {cell:} finds it. Empty for an upload's rows.
    members: list[int]
    # An upload's rows: each one's {cell:} references on the upload's copy,
    # already found, by their placeholder. Empty for a set's rows.
    links: list[dict[str, str]]


@dataclass
class CopyRows:
    """What a table copying an upload needs from the run: the sheets it
    copies, how their header is found (None when the setup names no such
    sheet), and each data row's added cells, its values and its formats by
    column key, by the row's place on the copy."""

    sheets: list[str]
    schema: SheetSchema | None
    cells: dict[int, tuple[dict[str, Any], dict[str, str]]]


@dataclass
class Breakdowns:
    """A breakdown table's rows, ranked, and its TOTAL row."""

    total: BreakdownEntry
    rows: list[BreakdownEntry]


@dataclass
class ReportData:
    # Figures a dashboard value or a block cell can name.
    metrics: dict[str, Decimal | int | None]
    # Texts a block cell can name in braces: {vendor_upper}.
    texts: dict[str, str]
    # Each table's rows, by the tab's key, then by its part's place among its
    # parts; parts that list no rows are not there.
    tables: dict[str, dict[int, TableRows]]
    # The sets some table lists rows of, which a part ``when`` asks about.
    listed: set[str]
    # Each copy of an upload, by the tab's key: only with the uploads.
    copies: dict[str, CopyRows]
    # Column key → the tolerance its difference shading uses.
    tolerances: dict[str, Decimal]
    # Each dashboard's breakdown table, by the tab's key.
    breakdowns: dict[str, Breakdowns]
    wallet: WalletReconciliation | None
    detected: list[SheetManifest]
    # A formula worked out on the run, for a block's worked-out cell.
    run_value: Callable[[str], Any]
