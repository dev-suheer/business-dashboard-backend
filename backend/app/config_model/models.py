"""A category's configuration: what the database stores, one version per save.

Version 1 of each category is generated from the code the client approved
(``scripts/build_seed.py``), so it reproduces today's results exactly. What a
run reads from it: the input files and how each sheet is recognised and read,
the rates and tolerances, exception severities, the verdict labels, and the
whole output workbook: its tabs, columns, totals and fixed blocks.

Money and rates are strings, never floats, so a value survives JSON unchanged.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal[1] = 1

Severity = Literal["high", "medium", "info"]

# The cell formats a report can write with (app/recon/sheets.py report_formats).
ReportFormat = Literal[
    "cashback_header",
    "check_header",
    "dash_header",
    "dash_int",
    "dash_label",
    "dash_money",
    "dash_pct",
    "dash_text",
    "dash_total",
    "dash_total_int",
    "dash_total_text",
    "date",
    "date_vendor",
    "header",
    "high",
    "info",
    "label",
    "meta_header",
    "money",
    "money_bad",
    "money_bold",
    "money_vendor",
    "money_warn",
    "portal_header",
    "rate",
    "rate_vendor",
    "source_date",
    "source_datetime",
    "text_vendor",
    "title",
    "total",
    "vendor_header",
    "yellow",
    "yellow_text",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FormulaMark:
    """Marks a configuration text as a formula of ``app/expr``, so it is
    checked when the configuration is saved and its lineage can be drawn."""

    def __repr__(self) -> str:
        return "formula"


FORMULA = FormulaMark()
# A formula: ``portal.total_amount - vendor.total``.
Formula = Annotated[str, FORMULA]


class Named(_Model):
    """A named formula: a figure, a text, a field."""

    key: str
    formula: Formula


class Raise(_Model):
    """An exception a rule raises."""

    code: str
    detail: Formula
    amount: Formula | None = None
    # Whose exception it is: by default the order's key, or none for a row's.
    order_no: Formula | None = None


# Every row read carries these two, for formulas and messages: the sheet it
# came from and its place among that sheet's data rows, from 1.
ROW_FIELDS = frozenset({"sheet", "row"})


class InputColumn(_Model):
    """One logical field and the header texts that may introduce it."""

    field: str
    # Every header text accepted for this column; a renamed column is a new entry.
    headers: list[str] = Field(min_length=1)
    required: bool = False
    # How the cell is read: text, id (text without a trailing ".0"), date,
    # money (blank reads as 0), decimal (blank stays blank), rate (a fraction:
    # "5.00 %" and 5 both read as 0.05).
    type: Literal["text", "id", "date", "money", "decimal", "rate"] = "text"
    # A row with no value here is not a row (a totals line, a spacer).
    skip_if_blank: bool = False


class InputSheet(_Model):
    """One kind of sheet read from an input file, and how it is recognised."""

    key: str
    # How the sheet is named in messages: "No vendor claim report sheet found…".
    name: str
    columns: list[InputColumn]
    # Every one of these headers must be present for a sheet to be this kind.
    fingerprint: list[str] = []
    # Headers whose presence disqualifies a sheet (a worked recon tab that carries
    # the input's columns bolted onto others).
    exclude: list[str] = []
    # Fields worked out on each row once its columns are read, in order; one
    # may replace a column, e.g. the booking id without its "-1" leg.
    fields: list[Named] = []
    # Only rows for which this formula holds are read, e.g. LOWER(vertical) = "food".
    filter: Formula | None = None
    # A row that repeats an earlier one is dropped.
    unique: "Unique | None" = None
    # A row the upload cannot be reconciled with.
    rejects: list["Reject"] = []
    # The row set its rows join; by default the file's role.
    set: str | None = None
    # An optional sheet may be missing from the file. Otherwise every matching
    # sheet is read, or only the first with ``first_only``. A missing sheet's
    # error ends with ``missing_hint``.
    optional: bool = False
    first_only: bool = False
    missing_hint: str = ""
    # How the sheet is listed among the sheets a run read: under this role
    # (by default the file's), counting the rows ``counted`` holds for.
    manifest_role: str | None = None
    counted: Formula = "TRUE"

    @model_validator(mode="after")
    def _columns_leave_the_row_fields_free(self) -> "InputSheet":
        taken = ROW_FIELDS & {c.field for c in self.columns}
        if taken:
            raise ValueError(f"{', '.join(sorted(taken))} is a field every row has")
        return self


class Unique(_Model):
    """Rows are the same row when these formulas all agree; only the first is
    kept. Only rows ``when`` holds for are compared. With ``exception``, each
    row dropped is reported."""

    keys: list[Formula] = Field(min_length=1)
    when: Formula = "TRUE"
    exception: Raise | None = None


class Reject(_Model):
    """The upload is refused, with ``message`` (a formula on the row), when a
    row ``when`` holds for is met."""

    when: Formula
    message: Formula


class InputFile(_Model):
    """One upload a run takes. One file per role."""

    role: str
    title: str
    caption: str
    required: bool
    # Accepted, but not offered on the upload screen.
    shown: bool = True
    sheets: list[InputSheet]
    # Rows to reconcile; the wallet history, which the wallet check reads; or
    # a reference file (a finance report, say): read and copied into the report
    # for look-up columns to find things in, but no part of the reconciliation,
    # so a run does not list its sheets.
    kind: Literal["rows", "wallet", "reference"] = "rows"
    # The error when a file has the right sheet but no rows to reconcile;
    # {filename} names the file.
    empty_message: str | None = None


class Parameter(_Model):
    """A rate, tolerance or date the reconciliation uses."""

    key: str
    label: str
    kind: Literal["rate", "tolerance", "date"]
    value: str
    # Whether a single run may override it.
    overridable: bool = False

    def typed(self) -> Decimal | date:
        return (
            date.fromisoformat(self.value)
            if self.kind == "date"
            else Decimal(self.value)
        )


class ExceptionDef(_Model):
    code: str
    severity: Severity
    # What it means, in plain words.
    about: str | None = None


# The header colour bands a column can be in (app/recon/sheets.py report_formats).
GROUPS = ("portal", "check", "vendor", "meta", "cashback")

# {cell:tab.column} in a column's Excel formula: this row's cell on another tab.
CELL_LINK = re.compile(r"\{cell:([^{}]*)\}")


def cell_links(excel: str) -> list[tuple[str, str, str]]:
    """Each {cell:tab.column} of an Excel formula: the placeholder as written,
    the tab's key and the column, either empty when it does not say."""
    links = []
    for match in CELL_LINK.finditer(excel):
        tab, _, column = match.group(1).partition(".")
        links.append((match.group(0), tab, column))
    return links


class SheetLookup(_Model):
    """What a look-up column finds: in the report tab ``sheet``, a copy of an
    upload, the row whose ``match`` column holds this row's key, and that
    row's ``value`` column; both named by the copied upload's column names.
    The key is this row's cell in ``key_column``, a column of the table, or
    ``key_value``, a formula worked out on the row. It is written as an Excel
    lookup, so the cell shows where its value comes from, and no formula of
    the setup reads it."""

    sheet: str
    match: str
    value: str
    key_column: str | None = None
    key_value: Formula | None = None


class OutputColumn(_Model):
    key: str
    header: str
    width: int
    # Header colour band: portal, check, vendor, meta, cashback. A vendor
    # column's cells are shaded too.
    group: str
    # How the value is written: text, money, diff, rate, date; or id (a
    # number when it is all digits, text otherwise), string (always text, even
    # empty) and datetime (a date, with its time when it has one).
    kind: str
    # The formula for each row's cell, e.g. portal.order_date or
    # IF(ids_match, "TRUE", "FALSE"). Without one, the row's field named ``key``.
    value: Formula | None = None
    # Instead of a value, an Excel formula written in each row's cell, where
    # {cell:tab.column} is this row's cell on another tab: on the table before
    # this one that lists the same rows, or on the copy of the upload whose
    # rows these are (column is then the upload's header). A row whose cell
    # cannot be found is left out.
    excel: str | None = None
    # The header's own fill, #RRGGBB, in place of its band's; its text is white
    # or black, whichever reads on it.
    color: str | None = None
    # A format's name for each row's cell, e.g. IF(severity = "high", "high",
    # "info"), in place of the one its kind gives; an empty cell is written in
    # it too. Without one, the cell is written as its kind says.
    style: Formula | None = None
    # A look-up column's look-up: only on a table's ``lookup_columns``.
    lookup: SheetLookup | None = None


class Cell(_Model):
    """One cell of a fixed block: a label, a figure, an Excel formula, or a
    value worked out on the run.

    ``col`` is a column number or an Orders column key; ``to_col`` merges across
    to it. Exactly one of ``text``, ``metric``, ``formula`` and ``value`` is
    given.

    ``text`` may name report values in braces: ``{vendor_upper}``.
    ``metric`` is one of the run's figures, written as a number.
    ``formula`` is Excel, with ``{col:key}`` for an Orders column's letter,
    ``{row:totals}`` for the totals row and ``{row}`` for this row.
    ``value`` is worked out on the run, as a figure is, and written as a number
    when it is one, as text otherwise: ``wallet_check.rows_checked``.
    """

    col: int | str
    to_col: int | str | None = None
    text: str | None = None
    metric: str | None = None
    formula: str | None = None
    value: Formula | None = None
    format: ReportFormat | None = None


class BlockRow(_Model):
    cells: list[Cell] = []
    height: int | None = None


class Contacts(_Model):
    """Names from an upload's second Customer column, which a table's rows see
    as ``contact``: the name on this row's own upload row (``row``, a formula
    giving that row), or on the upload row whose ``key`` header holds this
    row's ``match``. A row with no name has a blank ``contact``."""

    upload: str
    key: str | None = None
    match: Formula | None = None
    row: Formula | None = None


class Section(_Model):
    """One part of a table sheet, written below the part before it.

    ``gap`` blank rows come first. A section with ``when`` is written only when
    that row set has rows; otherwise it is skipped and takes no space.
    """

    kind: Literal["rows", "totals", "block"]
    gap: int = 0
    when: str | None = None
    # rows: the row set, or instead an upload's own rows, as its copy places
    # them, each formula reading a row's cells by their header (shippingcost
    # for Shipping Cost); only those ``where`` holds for; the names its
    # formulas see as ``contact``; an optional title row above it; and
    # whether its diff columns use the run's tolerances and its rows fill the
    # table's look-up columns.
    set: str | None = None
    upload: str | None = None
    where: Formula = "TRUE"
    contacts: Contacts | None = None
    title: str | None = None
    tolerances: bool = True
    lookups: bool = False
    autofilter: bool = False
    # totals: the label in column A ({count} is the rows totalled) and any
    # column whose total has its own format.
    label: str | None = None
    formats: dict[str, ReportFormat] = {}
    # block: rows written one after another; an empty row is a blank line.
    rows: list[BlockRow] = []
    # rows: the cells of this set's rows, by column key, when they are not the
    # table's own; a column not listed stays blank. With values of its own, the
    # part has its own formats too: a column not listed in ``styles`` has none.
    values: dict[str, Formula] = {}
    styles: dict[str, Formula] = {}


class DashValue(_Model):
    metric: str
    # How it is written: a whole number, money or a percentage.
    kind: Literal["int", "money", "pct"]


class DashBlockDef(_Model):
    label: str | None = None
    value_label: str | None = None
    headers: list[str]
    values: list[DashValue]
    tall: bool = True


class OutputTab(_Model):
    key: str
    title: str
    kind: Literal["dashboard", "table"]
    # Only written when this input was uploaded.
    requires: str | None = None
    # A table with a source is a copy of that upload, as it arrived: which of
    # its sheet kinds finds the header, and the header's format. It has no
    # parts; it leaves out the columns of the ``hidden`` headers (compared as
    # headers are), and its own columns are added after the upload's last,
    # each worked out on the row, reading its cells by header, or an Excel
    # formula where {col:header} is this row's cell under that header and
    # {row} this row.
    source: str | None = None
    sheet: str | None = None
    header_format: ReportFormat | None = None
    hidden: list[str] = []
    # table: its header row, its columns, the look-up columns added after them
    # (each written when the sheet it looks in is in the report), and how they
    # are laid out.
    # ``bands`` colours each header by its band and makes the row tall;
    # ``plain`` is the standard header, filtered.
    header: Literal["bands", "plain"] = "bands"
    columns: list[OutputColumn] = []
    lookup_columns: list[OutputColumn] = []
    sections: list[Section] = []
    # dashboard: the labelled blocks, then the breakdown table.
    blocks: list[DashBlockDef] = []
    breakdown: "Breakdown | None" = None
    # table: column key → the tolerance (a formula) its difference shading uses.
    tolerances: dict[str, Formula] = {}

    @property
    def copies(self) -> bool:
        """Whether this is a table that copies an upload, as it arrived."""
        return self.kind == "table" and self.source is not None


class BreakdownColumn(_Model):
    """One column of a breakdown table, after the names: what each member adds
    to its name's row, where ``when`` holds. ``count`` adds one; ``sum`` adds
    ``value``. Both are worked out on the member, as the breakdown says."""

    header: str
    add: Literal["count", "sum"] = "sum"
    value: Formula | None = None
    when: Formula = "TRUE"
    # A whole number, or money, rounded to the cent on each row.
    kind: Literal["int", "money"] = "money"


class Breakdown(_Model):
    """A dashboard's breakdown table ("Breakdown By Most Used"): one row per
    name, each column added up over the name's members.

    Each member of ``set`` is one part, or is split into its ``parts`` (its
    claim lines, its brands): each part has a ``name`` and a ``basis``, and
    each of ``totals`` is added up over a name's parts. The columns are worked
    out on the member, once for each of its names, where ``share`` is that
    name's part of the member's basis and each of ``totals`` is the name's own.

    The rows are ranked by the column ``rank_by`` (0 is the first after the
    names), most first, then by name; with none, by name. The TOTAL row adds
    the rows up, or, with ``total = "whole"``, is worked out the same way with
    every part under one name.
    """

    set: str
    parts: str | None = None
    name: Formula
    basis: Formula = "1"
    totals: list[Named] = []
    name_header: str = "Breakdown By Most Used"
    columns: list[BreakdownColumn] = []
    rank_by: int | None = 0
    total: Literal["sum", "whole"] = "sum"


class OutputWorkbook(_Model):
    tabs: list[OutputTab]
    # Texts a block can name in braces, by key.
    texts: list[Named] = []


# --- The generic runner (Phase 4) ---------------------------------------------


class Side(_Model):
    """The pair builder: one upload's rows, as one side of the pairing."""

    role: str
    # The field both sides are paired on.
    key: str
    # Rows that take part (Delivered, say). Others are reported, not paired.
    active: Formula = "TRUE"


class Member(_Model):
    """The group builder: every row of ``set`` with a ``key`` joins the order
    of that key, in its collection ``name`` (an order's claim lines, say).

    With ``primary``, one row is the order's own instead, under ``name``: the
    earliest by those formulas, the one met first on a tie. The others go to
    the collection ``repeats``, in the order they were set aside.
    """

    set: str
    key: Formula
    name: str
    primary: list[Formula] = []
    repeats: str | None = None


class RowRule(Raise):
    """An exception for each member of ``over`` that ``when`` holds for.

    ``over`` is a row set or, for the pair builder, ``inactive`` (rows left out
    of the pairing) or ``duplicates`` (keys active more than once).
    """

    over: str
    when: Formula = "TRUE"
    # What it finds, in plain words.
    about: str | None = None


class StatusRule(_Model):
    """The first rule that holds for an order decides its verdict."""

    when: Formula
    verdict: str | None = None
    exception: Raise | None = None
    # Run the checks: the verdict is ``clean`` if none fails, ``broken`` if any does.
    checks: bool = False
    clean: str | None = None
    broken: str | None = None
    # Why, in words: the order's ``note``.
    note: Formula | None = None
    # What it decides, in plain words.
    about: str | None = None


class Check(Raise):
    """Raised when ``when`` holds, i.e. when the check fails."""

    when: Formula
    # What it finds, in plain words.
    about: str | None = None


class Finding(Check):
    """Raised for every order ``when`` holds for, whatever its verdict; with
    ``each``, once for every member of that collection of the order's."""

    each: str | None = None


class RunRule(Check):
    """Raised once for the run when ``when`` holds."""


class Stop(_Model):
    """The run stops with ``message`` (a formula) when ``when`` holds."""

    when: Formula
    message: Formula
    # Why it stops, in plain words.
    about: str | None = None


class Lookup(_Model):
    """A table from ``by`` to ``value`` over the members of ``set``, read with
    LOOKUP("key", ...). On a repeated ``by`` the first value stands, the last,
    or their sum; ``then`` (on ``value``) is worked out on each when given."""

    key: str
    set: str
    by: Formula
    value: Formula = "TRUE"
    combine: Literal["first", "last", "sum"] = "first"
    then: Formula | None = None


class RowFields(_Model):
    """Fields worked out on each row of ``set`` once every upload is read, so
    they can look into the others."""

    set: str
    fields: list[Named]


class Group(_Model):
    """Each order's rows of ``of``, gathered by ``key`` into its collection
    ``name`` (an order's lines by brand, say). Each group has its ``key`` and
    its ``fields``, worked out over its own rows, as ``of``."""

    name: str
    of: str
    key: Formula
    fields: list[Named] = []


class SetDef(_Model):
    """A named set of orders, rows of an upload, or exceptions."""

    key: str
    over: str
    where: Formula = "TRUE"
    # What is in it, in plain words.
    about: str | None = None


class WalletCheck(_Model):
    """Every row of ``rows`` with a wallet share, proved against the wallet
    history upload. Rows of ``excluded`` are not checked, but their movements
    are not reported as having no row."""

    rows: str
    excluded: str | None = None
    # The vertical each row is filed under, and the history's Wallet Type texts
    # that name each vertical, read without these characters.
    vertical: Formula
    wallet_types: dict[str, str]
    strip: str = ""
    sg_order_id: Formula
    reference: Formula
    order_date: Formula
    amount: Formula
    tolerance: Formula


class History(_Model):
    """What the History screen shows for a run, and its report's file name."""

    counted: Formula
    total: Formula
    unit: Formula
    net_payable: Formula
    vendor: Formula = "BLANK()"
    period_start: Formula = "BLANK()"
    period_end: Formula = "BLANK()"
    report_name: Formula


class Model(_Model):
    """How a category reconciles.

    Once the uploads are read, the rows' own further fields are worked out.
    The rows make the orders: paired by key from two sides (``pair``) or
    gathered by key from any number of row sets (``group``). Each order's
    fields are computed, its verdict is decided by the status rules and
    checks, its findings are raised, and the figures are summed.
    """

    builder: Literal["pair", "group"] = "pair"
    lookups: list[Lookup] = []
    row_fields: list[RowFields] = []
    sides: list[Side] = []
    members: list[Member] = []
    groups: list[Group] = []
    fields: list[Named] = []
    # Run-wide values worked out once the orders are: a period, a claim's
    # week. Then the run stops if a stop holds.
    context: list[Named] = []
    stops: list[Stop] = []
    row_rules: list[RowRule] = []
    rules: list[StatusRule]
    checks: list[Check] = []
    findings: list[Finding] = []
    run_rules: list[RunRule] = []
    # Orders are listed in this order: formulas, compared in turn, blank first.
    sort: list[Formula] = ["key"]
    wallet: WalletCheck | None = None
    sets: list[SetDef] = []
    metrics: list[Named] = []
    history: History

    @model_validator(mode="after")
    def _builder_has_its_inputs(self) -> "Model":
        if self.builder == "pair" and len(self.sides) != 2:
            raise ValueError("the pair builder takes two sides")
        if self.builder == "group" and not self.members:
            raise ValueError("the group builder takes at least one member")
        return self


class CategoryConfig(_Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    category: str
    # The runner, driven by ``model``.
    engine: Literal["generic"]
    inputs: list[InputFile]
    parameters: list[Parameter] = []
    exceptions: list[ExceptionDef] = []
    # Texts the report writes for a value: {"unmatched_reason": {"cancelled": "Cancelled"}}.
    labels: dict[str, dict[str, str]] = {}
    output: OutputWorkbook
    model: Model | None = None

    def input(self, role: str) -> InputFile | None:
        return next((f for f in self.inputs if f.role == role), None)

    def sheet(self, role: str, key: str) -> InputSheet | None:
        file = self.input(role)
        return next((s for s in file.sheets if s.key == key), None) if file else None

    def parameter(self, key: str) -> Parameter | None:
        return next((p for p in self.parameters if p.key == key), None)

    def severity(self, code: str) -> Severity | None:
        return next((e.severity for e in self.exceptions if e.code == code), None)

    def tab(self, key: str) -> "OutputTab | None":
        return next((t for t in self.output.tabs if t.key == key), None)

    def looked_in(self) -> list["OutputTab"]:
        """The copies some look-up column looks in, in the report's order."""
        keys = {
            column.lookup.sheet
            for tab in self.output.tabs
            for column in tab.lookup_columns
            if column.lookup is not None
        }
        return [t for t in self.output.tabs if t.copies and t.key in keys]


# OutputTab and InputSheet name models defined after them.
OutputTab.model_rebuild()
InputSheet.model_rebuild()
