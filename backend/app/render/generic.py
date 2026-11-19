"""A generic run's result, as the figures, rows and texts its report names.

Every value comes from a formula in the output configuration, evaluated on the
run's orders: a table column on each member of its row set (or each row of an
upload), a breakdown column on each member of its set (or each of its parts).
"""

from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config_model.models import (
    Breakdown,
    Contacts,
    OutputTab,
    Section,
    cell_links,
)
from app.config_model.runtime import schema_of
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
from app.recon.cleaning import money, normalize_header
from app.recon.excel import SheetSchema
from app.recon.sheets import (
    BreakdownEntry,
    PortalRecord,
    breakdown_total,
    order_id_text,
    portal_records,
    ranked,
)
from app.render.data import Breakdowns, CopyRows, ReportData, TableRows
from app.render.lookups import copy_layout
from app.runner import Row, RunResult


def report_data(
    result: RunResult, sources: dict[str, Path] | None = None
) -> ReportData:
    """``sources`` maps upload roles to the uploaded files: a table listing an
    upload's own rows, or naming contacts, reads them."""
    output = result.config.output
    scope = result.scope
    tables: dict[str, dict[int, TableRows]] = {}
    listed: set[str] = set()
    copies: dict[str, CopyRows] = {}
    tolerances: dict[str, Decimal] = {}
    for tab in output.tabs:
        if tab.copies:
            if sources:
                copies[tab.key] = _copy_rows(result, tab, sources)
            continue
        if tab.kind != "table":
            continue
        parts: dict[int, TableRows] = {}
        for s, section in enumerate(tab.sections):
            if section.kind != "rows":
                continue
            if section.set is not None:
                parts[s] = _set_rows(result, tab, section, sources or {})
                if parts[s].values:
                    listed.add(section.set)
            elif section.upload is not None:
                parts[s] = _upload_rows(result, tab, section, sources or {})
        tables[tab.key] = parts
        tolerances.update(
            {key: evaluate(f, scope) for key, f in tab.tolerances.items()}
        )

    breakdowns = {
        tab.key: _breakdowns(tab.breakdown, scope.collection(tab.breakdown.set))
        for tab in output.tabs
        if tab.kind == "dashboard" and tab.breakdown is not None
    }
    return ReportData(
        metrics=result.metrics,
        texts={
            named.key: text(evaluate(named.formula, scope)) for named in output.texts
        },
        tables=tables,
        listed=listed,
        copies=copies,
        tolerances=tolerances,
        breakdowns=breakdowns,
        wallet=result.wallet,
        detected=result.detected,
        run_value=lambda formula: evaluate(formula, scope),
    )


def _set_rows(
    result: RunResult, tab: OutputTab, section: Section, sources: dict[str, Path]
) -> TableRows:
    """The members of the section's set that ``where`` holds for, each seeing
    its ``contact`` when the section names contacts."""
    assert section.set is not None
    spec = section.contacts
    contacts = _contacts(result, spec, sources) if spec is not None else {}
    chosen: list[int] = []
    rows: list[Scope] = []
    for index, member in enumerate(result.scope.collection(section.set)):
        item = _with_contact(spec, contacts, member) if spec is not None else member
        if not _holds(section.where, item):
            continue
        chosen.append(index)
        rows.append(item)
    return TableRows(
        values=_rows(tab, section, rows),
        styles=_styles(tab, section, rows),
        members=chosen,
        links=[],
    )


def _upload_rows(
    result: RunResult, tab: OutputTab, section: Section, sources: dict[str, Path]
) -> TableRows:
    """The upload's own rows, as its copy places them, that ``where`` holds
    for. A row whose {cell:} the upload's copy does not have is left out, before
    anything is worked out on it."""
    assert section.upload is not None
    copies = {
        t.key: (t.title, frozenset(normalize_header(h) for h in t.hidden))
        for t in result.config.output.tabs
        if t.copies and t.source == section.upload
    }
    wanted = [
        (placeholder, tab_key, header)
        for column in tab.columns
        if column.excel is not None
        for placeholder, tab_key, header in cell_links(column.excel)
    ]
    rows: list[Scope] = []
    links: list[dict[str, str]] = []
    path = sources.get(section.upload)
    names = {item.role: item.sheets for item in result.detected}.get(section.upload)
    upload = result.config.input(section.upload)
    if path is not None and names and upload is not None:
        named = _names(tab, section)
        for record in portal_records(path, names, schema_of(upload.sheets[0])):
            found: dict[str, str] = {}
            for placeholder, tab_key, header in wanted:
                copy = copies.get(tab_key)
                cell = record.cell(header, *copy) if copy is not None else None
                if cell is None:
                    break
                found[placeholder] = cell
            else:
                item = _record_scope(record, named, result.scope)
                if not _holds(section.where, item):
                    continue
                rows.append(item)
                links.append(found)
    return TableRows(
        values=_rows(tab, section, rows),
        styles=_styles(tab, section, rows),
        members=[],
        links=links,
    )


def _copy_rows(result: RunResult, tab: OutputTab, sources: dict[str, Path]) -> CopyRows:
    """The sheets a copy copies, how their header is found, and the values and
    formats of the columns it adds, worked out on each row after the header:
    its cells by their header, as an upload's own rows are read."""
    assert tab.source is not None
    config = result.config
    path = sources.get(tab.source)
    sheets: list[str] = []
    sheet = config.sheet(tab.source, tab.sheet) if tab.sheet else None
    schema: SheetSchema | None = schema_of(sheet) if sheet is not None else None
    upload = config.input(tab.source)
    if upload is not None and upload.kind == "reference":
        # A run does not list a reference file's sheets: its copy finds them.
        if path is not None and schema is not None:
            sheets = copy_layout(path, schema, tab.source).sheets
    else:
        detected = {item.role: item.sheets for item in result.detected}
        sheets = detected.get(tab.source) or []
    cells: dict[int, tuple[dict[str, Any], dict[str, str]]] = {}
    worked = [c for c in tab.columns if c.excel is None or c.style is not None]
    if worked and path is not None and sheets and schema is not None:
        formulas = [c.value or c.key for c in worked if c.excel is None]
        formulas += [c.style for c in worked if c.style is not None]
        named = set().union(*(references(parse(f)) for f in formulas))
        for record in portal_records(path, sheets, schema):
            item = _record_scope(record, named, result.scope)
            values = {
                c.key: _as_text(value) if c.kind == "text" else value
                for c in worked
                if c.excel is None
                for value in [evaluate(c.value or c.key, item)]
            }
            styles = {
                c.key: text(evaluate(c.style, item))
                for c in worked
                if c.style is not None
            }
            cells[record.excel_row - 1] = (values, styles)
    return CopyRows(sheets=sheets, schema=schema, cells=cells)


def _names(tab: OutputTab, section: Section) -> set[str]:
    """Every name the section's formulas read on a row."""
    formulas = [section.where, *section.values.values()]
    for column in tab.columns:
        if column.excel is None:
            formulas.append(column.value or column.key)
        if column.style is not None:
            formulas.append(column.style)
    return set().union(*(references(parse(f)) for f in formulas))


def _holds(where: str, item: Scope) -> bool:
    return where == "TRUE" or truthy(evaluate(where, item))


def _rows(
    tab: OutputTab, section: Section, members: Sequence[Scope]
) -> list[dict[str, Any]]:
    """Each member's cells by column key: the section's own formulas when it
    has them, the table's otherwise; and the keys its look-up columns find
    by a formula, when the part fills them."""
    formulas: list[tuple[str, str | None, bool]] = [
        (
            c.key,
            None
            if c.excel is not None
            else section.values.get(c.key)
            if section.values
            else c.value or c.key,
            c.kind == "text",
        )
        for c in tab.columns
    ]
    if section.lookups:
        keys = dict.fromkeys(
            c.lookup.key_value
            for c in tab.lookup_columns
            if c.lookup is not None and c.lookup.key_value is not None
        )
        formulas += [(key, key, False) for key in keys]
    return [
        {
            key: _as_text(value) if is_text else value
            for key, formula, is_text in formulas
            if formula is not None
            for value in [evaluate(formula, member)]
        }
        for member in members
    ]


def _styles(
    tab: OutputTab, section: Section, members: Sequence[Scope]
) -> list[dict[str, str]] | None:
    """Each member's format names by column key, for the columns with a style:
    the part's own when it has values of its own, the table's otherwise; None
    when there are none."""
    styled = (
        list(section.styles.items())
        if section.values
        else [(c.key, c.style) for c in tab.columns if c.style is not None]
    )
    if not styled:
        return None
    return [
        {key: text(evaluate(style, member)) for key, style in styled}
        for member in members
    ]


def _as_text(value: Any) -> Any:
    """A number in a text column is written as a plain number: a count of
    claim lines, say, not money."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _number(value: Any) -> Decimal:
    return Decimal(0) if value is None else value


def _breakdowns(spec: Breakdown, members: Sequence[Scope]) -> Breakdowns:
    entries = ranked(_breakdown(spec, members), spec.rank_by)
    if spec.total == "whole":
        empty = BreakdownEntry(name="TOTAL", values=[0] * len(spec.columns))
        total = next(iter(_breakdown(spec, members, every="TOTAL")), empty)
    else:
        total = breakdown_total(entries, len(spec.columns))
    return Breakdowns(total=total, rows=entries)


def _breakdown(
    spec: Breakdown, members: Sequence[Scope], every: str | None = None
) -> list[BreakdownEntry]:
    """One row per name, in the order met: what each of its members adds to
    each column, where the column's ``when`` holds.

    A member split into parts counts once under each of its names, its figures
    worked out with that name's ``share`` of its basis and the name's own
    totals. ``every`` puts every part under that one name.
    """
    entries: dict[str, BreakdownEntry] = {}
    for member in members:
        parts = member.collection(spec.parts) if spec.parts is not None else [member]
        basis: dict[str, Decimal] = {}
        totals: dict[str, dict[str, Decimal]] = {}
        for part in parts:
            name = every or text(evaluate(spec.name, part))
            basis[name] = basis.get(name, Decimal(0)) + _number(
                evaluate(spec.basis, part)
            )
            sums = totals.setdefault(name, {t.key: Decimal(0) for t in spec.totals})
            for total in spec.totals:
                sums[total.key] += _number(evaluate(total.formula, part))
        whole = sum(basis.values(), Decimal(0))
        for name, own in basis.items():
            share = Decimal(1)
            if spec.parts is not None:
                share = own / whole if whole else Decimal(1) / len(basis)
            item = Values({"share": share, **totals[name]}, parent=member)

            entry = entries.setdefault(
                name, BreakdownEntry(name=name, values=[0] * len(spec.columns))
            )
            for c, column in enumerate(spec.columns):
                if not truthy(evaluate(column.when, item)):
                    continue
                if column.add == "count":
                    entry.values[c] += 1
                else:
                    entry.values[c] += _number(evaluate(column.value or "0", item))
    for entry in entries.values():
        entry.values = [
            money(Decimal(value)) if column.kind == "money" else value
            for value, column in zip(entry.values, spec.columns, strict=True)
        ]
    return list(entries.values())


def _contacts(
    result: RunResult, spec: Contacts, sources: dict[str, Path]
) -> dict[Any, str]:
    """The name in the upload's second Customer column: by the key beside it,
    or by the row's place in the upload (its sheet and data row)."""
    path = sources.get(spec.upload)
    names = {item.role: item.sheets for item in result.detected}.get(spec.upload)
    upload = result.config.input(spec.upload)
    if path is None or not names or upload is None:
        return {}
    contacts: dict[Any, str] = {}
    for record in portal_records(path, names, schema_of(upload.sheets[0])):
        contact = record.contact()
        if not contact:
            continue
        if spec.key is not None:
            key = order_id_text(record.get(spec.key))
            if key:
                contacts[key] = contact
        else:
            contacts[(record.sheet, record.ordinal)] = contact
    return contacts


def _with_contact(spec: Contacts, contacts: dict[Any, str], member: Scope) -> Values:
    """The member, seeing ``contact``: the name its key or its upload row has."""
    found: str | None = None
    if spec.key is not None:
        if spec.match is not None:
            found = contacts.get(text(evaluate(spec.match, member)))
    elif spec.row is not None:
        row = evaluate(spec.row, member)
        if isinstance(row, Row):
            found = contacts.get((row.sheet, row.index))
    return Values({"contact": found}, parent=member)


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
