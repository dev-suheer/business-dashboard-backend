"""Checking a configuration before it is saved, and tracing what each formula reads.

A configuration that is saved goes live: the next run reconciles under it. So
everything that can be checked without running it is checked here, and named
by where it is (``model.rules[3].when``), for the editor to point at:

* every formula parses, and calls only functions that exist;
* every name a formula reads is there when it runs, and every value is of a
  kind its operator or function takes (``scopes.py``);
* every set, row set, collection and figure a part of it names exists;
* every exception it raises has a severity;
* names that must be unique are.

A run on real files is what finds the rest.
"""

import re
import types
import typing
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from app.config_model.models import (
    FORMULA,
    GROUPS,
    CategoryConfig,
    Model,
    OutputTab,
    Raise,
    ReportFormat,
    cell_links,
)
from app.config_model.runtime import schema_of
from app.config_model.scopes import (
    WALLET_LISTS,
    formula_places,
    name_problems,
    non_numbers,
    origins_of,
)
from app.expr import FormulaError, parse, references
from app.expr.parser import Call, Node
from app.expr.parser import Literal as ExprLiteral
from app.recon import wallet
from app.recon.cleaning import normalize_header
from app.recon.exceptions import ExceptionCode


@dataclass(frozen=True)
class Problem:
    # Where it is, as a path into the configuration: inputs[0].sheets[0].columns[2].
    path: str
    message: str


@dataclass(frozen=True)
class Use:
    """One formula, where it is, the names it reads, and the parts of the setup
    those come from (``model.fields[3]``), as a run reads them."""

    path: str
    formula: str
    uses: list[str]
    reads: list[str]


def formulas(model: BaseModel, path: str = "") -> Iterator[tuple[str, str]]:
    """Every formula in a configuration (or any part of one), with its path."""
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        here = f"{path}.{name}" if path else name
        shape = _formula_shape(field.annotation, field.metadata)
        if shape == "one" and isinstance(value, str):
            yield here, value
        elif shape == "list":
            for i, item in enumerate(value):
                yield f"{here}[{i}]", item
        elif shape == "dict":
            for key, item in value.items():
                yield f"{here}.{key}", item
        elif isinstance(value, BaseModel):
            yield from formulas(value, here)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, BaseModel):
                    yield from formulas(item, f"{here}[{i}]")


def _formula_shape(annotation: Any, metadata: list[Any]) -> str | None:
    """Whether a field holds one formula, a list of them, or a mapping to them."""
    if FORMULA in metadata:
        return "one"
    for option in _options(annotation):
        if _is_formula(option):
            return "one"
        origin = get_origin(option)
        args = get_args(option)
        if origin is list and args and _is_formula(args[0]):
            return "list"
        if origin is dict and len(args) == 2 and _is_formula(args[1]):
            return "dict"
    return None


def _options(annotation: Any) -> list[Any]:
    if get_origin(annotation) in (Union, types.UnionType):
        return [a for a in get_args(annotation) if a is not type(None)]
    return [annotation]


def _is_formula(annotation: Any) -> bool:
    return (
        get_origin(annotation) is typing.Annotated
        and FORMULA in annotation.__metadata__
    )


def lineage(config: CategoryConfig) -> list[Use]:
    """Every formula that parses, the names it reads, and where each of those
    comes from: through the scopes it runs in, so a name inside an aggregate
    is the set's, and a name a row has of its own is the row's. A column with
    no formula reads the name its key is, and is listed at its key."""
    walk, places = formula_places(config)
    reads: dict[str, set[str]] = {}
    at: dict[str, str] = dict(formulas(config))
    for path, formula, scopes in places:
        at.setdefault(path, formula)
        try:
            node = parse(formula)
        except FormulaError:
            continue
        found = reads.setdefault(path, set())
        for scope in scopes:
            if scope is None:
                continue
            for ref, inner in walk.refs(node, scope):
                if inner is not None:
                    # The innermost part that comes from somewhere: portal.total
                    # is the portal's column, not the portal.
                    origins = origins_of(ref, inner)
                    found |= next((o for o in reversed(origins) if o), frozenset())
    uses = []
    for path, formula in at.items():
        try:
            names = references(parse(formula))
        except FormulaError:
            continue
        uses.append(
            Use(
                path=path,
                formula=formula,
                uses=sorted(names),
                reads=sorted(reads.get(path, ())),
            )
        )
    return uses


def problems(config: CategoryConfig) -> list[Problem]:
    """Everything wrong with a configuration that can be told without running it."""
    found: list[Problem] = []
    for path, formula in formulas(config):
        try:
            parse(formula)
        except FormulaError as exc:
            found.append(Problem(path, str(exc)))
    found += [Problem(path, message) for path, message in name_problems(config)]
    found += _unique(config)
    found += _standard_uploads(config)
    found += _exceptions(config)
    if config.model is not None:
        found += _model(config, config.model)
    found += _output(config)
    found += _settings(config)
    return found


def _duplicates(path: str, names: list[str], what: str) -> list[Problem]:
    seen: set[str] = set()
    found = []
    for name in names:
        if name in seen:
            found.append(Problem(path, f"Two {what} are called {name!r}"))
        seen.add(name)
    return found


def _unique(config: CategoryConfig) -> list[Problem]:
    found = _duplicates("inputs", [f.role for f in config.inputs], "uploads")
    found += _duplicates("parameters", [p.key for p in config.parameters], "parameters")
    for i, upload in enumerate(config.inputs):
        found += _duplicates(
            f"inputs[{i}].sheets", [s.key for s in upload.sheets], "sheets"
        )
        for j, sheet in enumerate(upload.sheets):
            # A sheet's own field may replace a column on purpose, so only the
            # columns are compared.
            found += _duplicates(
                f"inputs[{i}].sheets[{j}].columns",
                [c.field for c in sheet.columns],
                "columns",
            )
    for t, tab in enumerate(config.output.tabs):
        found += _duplicates(
            f"output.tabs[{t}].columns",
            [c.key for c in [*tab.columns, *tab.lookup_columns]],
            "columns",
        )
    found += _duplicates("output.tabs", [t.key for t in config.output.tabs], "tabs")
    # Excel tells sheets apart without regard to case.
    titles: set[str] = set()
    for t, tab in enumerate(config.output.tabs):
        if tab.title.casefold() in titles:
            found.append(
                Problem(f"output.tabs[{t}].title", f"Two tabs are called {tab.title!r}")
            )
        titles.add(tab.title.casefold())
    return found


# The upload every category takes as it is, which its reader finds and reads by
# name: what the upload is called, its sheet's key, the columns it reads.
_STANDARD = {
    "wallet": ("wallet", "history", wallet.WALLET_SCHEMA, "The wallet check reads"),
}


def _standard_uploads(config: CategoryConfig) -> list[Problem]:
    """A wallet history keeps the names the wallet check reads it by: a run
    would fail without them."""
    found: list[Problem] = []
    for i, upload in enumerate(config.inputs):
        if upload.kind not in _STANDARD:
            continue
        role, key, schema, reader = _STANDARD[upload.kind]
        here = f"inputs[{i}]"
        if upload.role != role:
            found.append(
                Problem(
                    f"{here}.role",
                    f"{reader} this upload by the name {role!r}: call it that",
                )
            )
        j = next((j for j, s in enumerate(upload.sheets) if s.key == key), None)
        if j is None:
            found.append(
                Problem(
                    f"{here}.sheets",
                    f"{reader} its sheet by the key {key!r}: give it that key",
                )
            )
            continue
        fields = {c.field for c in upload.sheets[j].columns}
        found += [
            Problem(
                f"{here}.sheets[{j}].columns",
                f"{reader} the column {column.field!r}: put it back",
            )
            for column in schema.columns
            if column.field not in fields
        ]
    return found


def _raises(config: CategoryConfig) -> Iterator[tuple[str, Raise]]:
    model = config.model
    if model is not None:
        for group in ("row_rules", "checks", "findings", "run_rules"):
            for i, rule in enumerate(getattr(model, group)):
                yield f"model.{group}[{i}]", rule
        for i, status in enumerate(model.rules):
            if status.exception is not None:
                yield f"model.rules[{i}].exception", status.exception
    for i, upload in enumerate(config.inputs):
        for j, sheet in enumerate(upload.sheets):
            if sheet.unique is not None and sheet.unique.exception is not None:
                yield (
                    f"inputs[{i}].sheets[{j}].unique.exception",
                    sheet.unique.exception,
                )


def _exceptions(config: CategoryConfig) -> list[Problem]:
    known = {e.code for e in config.exceptions}
    found = _duplicates("exceptions", [e.code for e in config.exceptions], "exceptions")
    for path, rule in _raises(config):
        if rule.code not in known:
            found.append(
                Problem(
                    f"{path}.code",
                    f"Exception {rule.code!r} has no severity: add it to the exceptions",
                )
            )
    return found


def _row_sets(config: CategoryConfig) -> set[str]:
    # The wallet history's and a reference file's rows are lists as well.
    return {
        sheet.set or upload.role for upload in config.inputs for sheet in upload.sheets
    }


# The lists every run has of its own, and the codes the wallet check raises.
_OWN_SETS = frozenset(
    {"entities", "exceptions", "inactive", "duplicates", *WALLET_LISTS}
)
_WALLET_CODES = frozenset(
    code.value for code in ExceptionCode if code.value.startswith("WALLET_")
)


def _sets(config: CategoryConfig, model: Model) -> set[str]:
    names = (
        _row_sets(config)
        | {s.key for s in model.sets}
        | {"entities", "exceptions", *WALLET_LISTS}
    )
    if model.builder == "pair":
        names |= {"inactive", "duplicates"}
    return names


def _collections(model: Model) -> set[str]:
    """The collections each order holds: its rows of each member, its groups."""
    names = {m.repeats if m.primary else m.name for m in model.members}
    return {n for n in names if n is not None} | {g.name for g in model.groups}


def _model(config: CategoryConfig, model: Model) -> list[Problem]:
    found: list[Problem] = []
    rows = _row_sets(config)
    sets = _sets(config, model)
    roles = {f.role for f in config.inputs}

    def known(path: str, name: str | None, among: set[str], what: str) -> None:
        if name is not None and name not in among:
            found.append(Problem(path, f"No {what} is called {name!r}"))

    found += _duplicates("model.sets", [s.key for s in model.sets], "sets")
    found += _duplicates("model.lookups", [lk.key for lk in model.lookups], "lookups")
    found += _duplicates("model.metrics", [m.key for m in model.metrics], "figures")
    found += _duplicates("model.fields", [f.key for f in model.fields], "fields")
    found += _duplicates("model.context", [c.key for c in model.context], "run values")
    row_sets = _row_sets(config)
    for i, set_def in enumerate(model.sets):
        if not _NAME.fullmatch(set_def.key):
            found.append(
                Problem(f"model.sets[{i}].key", f"{set_def.key!r} is not a name")
            )
        elif set_def.key in row_sets or set_def.key in _OWN_SETS:
            found.append(
                Problem(
                    f"model.sets[{i}].key",
                    f"{set_def.key!r} is already the name of an upload's rows "
                    "or of the run's own list",
                )
            )
    if model.wallet is not None:
        codes = {e.code for e in config.exceptions}
        for code in _WALLET_CODES - codes:
            found.append(
                Problem(
                    "exceptions",
                    f"The wallet check raises {code}: keep it in the list",
                )
            )
    for i, side in enumerate(model.sides):
        known(f"model.sides[{i}].role", side.role, rows, "row set")
    for i, member in enumerate(model.members):
        known(f"model.members[{i}].set", member.set, sets, "set")
        if member.primary and member.repeats is None:
            found.append(
                Problem(f"model.members[{i}].repeats", "Name where the other rows go")
            )
    collections = _collections(model)
    for i, group in enumerate(model.groups):
        known(f"model.groups[{i}].of", group.of, collections, "collection")
    for i, spec in enumerate(model.row_fields):
        known(f"model.row_fields[{i}].set", spec.set, rows, "row set")
    for i, lookup in enumerate(model.lookups):
        known(f"model.lookups[{i}].set", lookup.set, sets, "set")
    for i, set_def in enumerate(model.sets):
        if set_def.over != set_def.key:  # a set made from itself is a cycle, below
            known(f"model.sets[{i}].over", set_def.over, sets - {set_def.key}, "set")
    found += _cycles(model)
    for i, rule in enumerate(model.row_rules):
        known(f"model.row_rules[{i}].over", rule.over, sets, "set")
    for i, finding in enumerate(model.findings):
        known(f"model.findings[{i}].each", finding.each, collections, "collection")
    if model.wallet is not None:
        known("model.wallet.rows", model.wallet.rows, sets, "set")
        known("model.wallet.excluded", model.wallet.excluded, sets, "set")
    if not model.rules:
        found.append(Problem("model.rules", "An order needs at least one status rule"))
    for i, status in enumerate(model.rules):
        if status.checks and (status.clean is None or status.broken is None):
            found.append(
                Problem(
                    f"model.rules[{i}]",
                    "A rule that runs the checks names a verdict for each outcome",
                )
            )
    for t, tab in enumerate(config.output.tabs):
        here = f"output.tabs[{t}]"
        for s, section in enumerate(tab.sections):
            at = f"{here}.sections[{s}]"
            known(f"{at}.set", section.set, sets, "set")
            known(f"{at}.when", section.when, sets, "set")
            known(f"{at}.upload", section.upload, roles, "upload")
            if section.contacts is not None:
                known(f"{at}.contacts.upload", section.contacts.upload, roles, "upload")
        if tab.breakdown is not None:
            known(f"{here}.breakdown.set", tab.breakdown.set, sets, "set")
            known(
                f"{here}.breakdown.parts",
                tab.breakdown.parts,
                collections,
                "collection",
            )
    metrics = {m.key for m in model.metrics}
    words = non_numbers(config)

    def figure(path: str, name: str | None) -> None:
        known(path, name, metrics, "figure")
        if name in words:
            found.append(
                Problem(path, f"{name} is {words[name]}, and this writes a number")
            )

    for t, tab in enumerate(config.output.tabs):
        for b, block in enumerate(tab.blocks):
            for v, value in enumerate(block.values):
                figure(f"output.tabs[{t}].blocks[{b}].values[{v}].metric", value.metric)
        for s, section in enumerate(tab.sections):
            for r, row in enumerate(section.rows):
                for c, cell in enumerate(row.cells):
                    figure(
                        f"output.tabs[{t}].sections[{s}].rows[{r}].cells[{c}].metric",
                        cell.metric,
                    )
    return found


def _cycles(model: Model) -> list[Problem]:
    """A configured set made, through others, from itself: one problem for each
    set of the loop."""
    over = {s.key: s.over for s in model.sets}
    found = []
    for i, set_def in enumerate(model.sets):
        chain = [set_def.key]
        step = set_def.over
        while step in over and step not in chain:
            chain.append(step)
            step = over[step]
        if step == set_def.key:
            message = (
                f"{set_def.key!r} is made from itself"
                if len(chain) == 1
                else f"{' and '.join(repr(n) for n in chain)} are made from each other"
            )
            found.append(Problem(f"model.sets[{i}].over", message))
    return found


# A column with no formula reads the name its key is, so the key must be one.
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# How a block's Excel formula names a column of its table: {col:key}.
_COLUMN_PLACEHOLDER = re.compile(r"\{col:([A-Za-z0-9_]+)\}")


# What Excel will not have in a sheet's name.
_TITLE_CHARACTERS = re.compile(r"[\[\]:*?/\\]")
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
# The header formats a copy of an upload can have (app/recon/sheets.py).
_COPY_HEADERS = frozenset({"header", *(f"{g}_header" for g in GROUPS)})
# The formats a column's style can name.
_FORMATS = frozenset(get_args(ReportFormat))


def _output(config: CategoryConfig) -> list[Problem]:
    found: list[Problem] = []
    roles = {f.role for f in config.inputs}
    tabs = config.output.tabs
    if not tabs:
        found.append(Problem("output.tabs", "The report needs at least one tab"))
    found += _row_tables(config)
    for t, tab in enumerate(tabs):
        here = f"output.tabs[{t}]"
        keys = {c.key for c in tab.columns}
        found += _title(here, tab.title)
        found += _shape(here, tab)
        for c, column in enumerate([*tab.columns, *tab.lookup_columns]):
            where = (
                f"{here}.columns[{c}]"
                if c < len(tab.columns)
                else f"{here}.lookup_columns[{c - len(tab.columns)}]"
            )
            if column.group not in GROUPS:
                found.append(
                    Problem(
                        f"{where}.group",
                        f"No header colour band is called {column.group!r}",
                    )
                )
            if column.color is not None and not _COLOR.fullmatch(column.color):
                found.append(
                    Problem(
                        f"{where}.color",
                        f"{column.color!r} is not a colour: give it as #RRGGBB",
                    )
                )
        for key in tab.tolerances:
            if key not in keys:
                found.append(Problem(f"{here}.tolerances.{key}", f"No column {key!r}"))
        found += _lookups(config, here, tab)
        if tab.kind == "table":
            for c, column in enumerate(tab.columns):
                if (
                    column.value is None
                    and column.excel is None
                    and not _NAME.fullmatch(column.key)
                ):
                    found.append(
                        Problem(
                            f"{here}.columns[{c}].key",
                            "A column with no formula reads the name its key is, "
                            f"and {column.key!r} is not one",
                        )
                    )
                found += _style_problems(f"{here}.columns[{c}].style", column.style)
        for s, section in enumerate(tab.sections):
            for key in section.values:
                if key not in keys:
                    found.append(
                        Problem(
                            f"{here}.sections[{s}].values.{key}", f"No column {key!r}"
                        )
                    )
            for key, style in section.styles.items():
                where = f"{here}.sections[{s}].styles.{key}"
                if key not in keys:
                    found.append(Problem(where, f"No column {key!r}"))
                found += _style_problems(where, style)
            for r, row in enumerate(section.rows):
                for c, cell in enumerate(row.cells):
                    for name in _COLUMN_PLACEHOLDER.findall(cell.formula or ""):
                        if name not in keys:
                            found.append(
                                Problem(
                                    f"{here}.sections[{s}].rows[{r}].cells[{c}].formula",
                                    f"No column {name!r}",
                                )
                            )
                    for which in ("col", "to_col"):
                        ref = getattr(cell, which)
                        if isinstance(ref, str) and ref not in keys:
                            found.append(
                                Problem(
                                    f"{here}.sections[{s}].rows[{r}].cells[{c}].{which}",
                                    f"No column {ref!r}",
                                )
                            )
        if tab.copies:
            found += _source(config, here, tab, roles)
            found += _copy(config, here, tab)
        if tab.kind == "table":
            found += _rows_and_links(config, t, tab)
        if tab.kind == "table" and not tab.lookup_columns:
            found += [
                Problem(
                    f"{here}.sections[{s}].lookups",
                    "This part fills the look-up columns, but the "
                    f"{tab.title} sheet has none: add them in its tab settings, "
                    "or untick Fills the look-up columns",
                )
                for s, section in enumerate(tab.sections)
                if section.kind == "rows" and section.lookups
            ]
        if (
            tab.kind == "table"
            and tab.lookup_columns
            and not any(s.kind == "rows" and s.lookups for s in tab.sections)
        ):
            found.append(
                Problem(
                    f"{here}.lookup_columns",
                    f"The {tab.title} sheet has look-up columns, but none of its "
                    "parts fills them: tick Fills the look-up columns in its Sheet "
                    "layout, or remove the columns",
                )
            )
    return found


def _lookups(config: CategoryConfig, here: str, tab: OutputTab) -> list[Problem]:
    """A look-up column finds its value in a copy of an upload, by columns that
    upload has, with one key: a column of the table, or a formula."""
    found: list[Problem] = []
    keys = {c.key for c in tab.columns}
    for c, column in enumerate(tab.columns):
        if column.lookup is not None:
            found.append(
                Problem(
                    f"{here}.columns[{c}].lookup",
                    "Only a look-up column looks things up: add it as one",
                )
            )
    if tab.lookup_columns and (tab.kind != "table" or tab.copies):
        found.append(
            Problem(
                f"{here}.lookup_columns",
                "Only a table with parts of its own takes look-up columns",
            )
        )
    for c, column in enumerate(tab.lookup_columns):
        where = f"{here}.lookup_columns[{c}]"
        lookup = column.lookup
        if lookup is None:
            found.append(
                Problem(f"{where}.lookup", "Say what the look-up column finds")
            )
            continue
        for part, given in (
            ("value", column.value),
            ("excel", column.excel),
            ("style", column.style),
        ):
            if given is not None:
                found.append(
                    Problem(
                        f"{where}.{part}",
                        "A look-up column is filled by its look-up: it takes no "
                        "formula, Excel formula or style",
                    )
                )
        if (lookup.key_column is None) == (lookup.key_value is None):
            found.append(
                Problem(
                    f"{where}.lookup",
                    "Find each row by a column of the table or by a formula: one",
                )
            )
        elif lookup.key_column is not None and lookup.key_column not in keys:
            found.append(
                Problem(
                    f"{where}.lookup.key_column", f"No column {lookup.key_column!r}"
                )
            )
        copy = config.tab(lookup.sheet)
        if copy is None or not copy.copies:
            found.append(
                Problem(
                    f"{where}.lookup.sheet",
                    f"No tab copying an upload is called {lookup.sheet!r}",
                )
            )
            continue
        assert copy.source is not None
        sheet = config.sheet(copy.source, copy.sheet) if copy.sheet else None
        if sheet is None:
            continue  # the copy's own problem
        fields = {col.field for col in sheet.columns}
        for part in ("match", "value"):
            name = getattr(lookup, part)
            if name not in fields:
                found.append(
                    Problem(
                        f"{where}.lookup.{part}",
                        f"{copy.source} has no column {name!r} on its "
                        f"{sheet.key} sheet",
                    )
                )
    return found


def _title(here: str, title: str) -> list[Problem]:
    """A name Excel takes for a sheet."""
    problem = None
    if not title.strip():
        problem = "A tab needs a title"
    elif len(title) > 31:
        problem = "A tab's title is at most 31 characters"
    elif _TITLE_CHARACTERS.search(title):
        problem = "A tab's title cannot have any of [ ] : * ? / \\"
    elif title.startswith("'") or title.endswith("'"):
        problem = "A tab's title cannot start or end with '"
    elif title.casefold() == "history":
        problem = "Excel keeps the name History for itself"
    return [Problem(f"{here}.title", problem)] if problem else []


def _style_problems(where: str, style: str | None) -> list[Problem]:
    """A style gives a format by its name, in quotes, or BLANK() (or "") to
    keep the column's own: as itself, or as what IF, IFS or COALESCE gives.
    Each name must be a format; a style giving anything else is refused, since
    what it would give is only known on the run."""
    if style is None:
        return []
    try:
        node = parse(style)
    except FormulaError:
        return []  # reported with the formula's other problems
    names: list[str] = []
    loose = False

    def given(node: Node) -> None:
        nonlocal loose
        if isinstance(node, ExprLiteral) and isinstance(node.value, str):
            if node.value:  # "" is blank: the column's own format
                names.append(node.value)
        elif isinstance(node, Call) and node.name == "BLANK" and not node.args:
            return
        elif isinstance(node, Call) and node.name in ("IF", "IFS", "COALESCE"):
            if node.name == "IF":
                outcomes = node.args[1:]
            elif node.name == "IFS":
                outcomes = node.args[1::2]
            else:
                outcomes = node.args
            for outcome in outcomes:
                given(outcome)
        else:
            loose = True

    given(node)
    found = [
        Problem(where, f"No format is called {name!r}")
        for name in names
        if name not in _FORMATS
    ]
    if loose:
        found.append(
            Problem(
                where,
                "Write each format's name in quotes, or BLANK() to keep the "
                "column's own format, so the check can see what this style gives",
            )
        )
    return found


def _shape(here: str, tab: OutputTab) -> list[Problem]:
    """What a tab of its kind needs to be written."""
    found: list[Problem] = []
    if tab.copies:
        if tab.sections:
            found.append(
                Problem(
                    f"{here}.sections",
                    "A copy writes its upload's rows as they arrived: it has no parts",
                )
            )
    elif tab.kind == "table":
        if not tab.columns:
            found.append(Problem(f"{here}.columns", "A table needs a column"))
        if not any(s.kind == "rows" for s in tab.sections):
            found.append(Problem(f"{here}.sections", "A table needs rows to list"))
    if tab.kind == "dashboard" and not tab.blocks and tab.breakdown is None:
        found.append(
            Problem(f"{here}.blocks", "A dashboard needs a block or a breakdown")
        )
    if tab.breakdown is not None and tab.kind != "dashboard":
        found.append(Problem(f"{here}.breakdown", "Only a dashboard has a breakdown"))
    wallet_rows = any(s.kind == "rows" and s.set in WALLET_LISTS for s in tab.sections)
    if wallet_rows and tab.requires != "wallet":
        found.append(
            Problem(
                f"{here}.requires",
                "A table of the wallet check's rows is written only when a wallet "
                "history was uploaded",
            )
        )
    if tab.copies and tab.header_format not in (None, *_COPY_HEADERS):
        found.append(
            Problem(
                f"{here}.header_format",
                f"No header format is called {tab.header_format!r}",
            )
        )
    for b, block in enumerate(tab.blocks):
        at = f"{here}.blocks[{b}]"
        if not block.values:
            found.append(Problem(f"{at}.values", "A block needs a figure"))
        if len(block.headers) != len(block.values):
            found.append(
                Problem(f"{at}.headers", "A block has one header for each figure")
            )
    if tab.breakdown is not None:
        spec = tab.breakdown
        at = f"{here}.breakdown"
        for c, column in enumerate(spec.columns):
            if column.add == "sum" and column.value is None:
                found.append(
                    Problem(f"{at}.columns[{c}].value", "Say what the column adds up")
                )
        if spec.rank_by is not None and not 0 <= spec.rank_by < len(spec.columns):
            found.append(Problem(f"{at}.rank_by", "No such column to rank by"))
    return found


def _row_tables(config: CategoryConfig) -> list[Problem]:
    """A table lists a set once: the report keeps a table's rows of a set
    together. Two tables may list the same set."""
    found: list[Problem] = []
    for t, tab in enumerate(config.output.tabs):
        if tab.kind != "table":
            continue
        listed: set[str] = set()
        for s, section in enumerate(tab.sections):
            if section.kind != "rows" or section.set is None:
                continue
            if section.set in listed:
                found.append(
                    Problem(
                        f"output.tabs[{t}].sections[{s}].set",
                        f"{section.set!r} is listed already: a table lists a set once",
                    )
                )
            listed.add(section.set)
    return found


def _rows_and_links(config: CategoryConfig, t: int, tab: OutputTab) -> list[Problem]:
    """What each part lists, and what each Excel formula's {cell:tab.column}
    points at: a cell the report writes before this row is written, on the
    table before this one that lists the same set, or on the copy of the
    upload whose rows these are."""
    found: list[Problem] = []
    here = f"output.tabs[{t}]"
    tabs = config.output.tabs
    for c, column in enumerate(tab.columns):
        if column.excel is not None and column.value is not None:
            found.append(
                Problem(
                    f"{here}.columns[{c}].excel",
                    "A column has a formula or an Excel formula, not both",
                )
            )
    for s, section in enumerate(tab.sections):
        if section.kind != "rows":
            continue
        at = f"{here}.sections[{s}]"
        if (section.set is None) == (section.upload is None):
            found.append(
                Problem(f"{at}.set", "Name the set the rows are, or the upload")
            )
            continue
        contacts = section.contacts
        if contacts is not None:
            by_key = contacts.key is not None and contacts.match is not None
            by_row = contacts.row is not None and contacts.key is None
            if by_key == by_row:
                found.append(
                    Problem(
                        f"{at}.contacts",
                        "Find a row's contact by its upload row, or by a key "
                        "and the value it matches",
                    )
                )
        for c, column in enumerate(tab.columns):
            if column.excel is None:
                continue
            for placeholder, key, name in cell_links(column.excel):
                where = f"{here}.columns[{c}].excel"
                if not key or not name:
                    found.append(
                        Problem(
                            where,
                            f"{placeholder} names a tab and a column: "
                            "{cell:tab.column}",
                        )
                    )
                    continue
                u, other = next(
                    ((u, o) for u, o in enumerate(tabs) if o.key == key), (None, None)
                )
                if section.upload is not None:
                    if other is None or not (
                        other.copies and other.source == section.upload
                    ):
                        found.append(
                            Problem(
                                where,
                                f"{{cell:{key}.{name}}} points at the copy of "
                                f"{section.upload}: name a tab that copies it",
                            )
                        )
                    continue
                if (
                    other is None
                    or u is None
                    or u >= t
                    or other.kind != "table"
                    or not any(
                        x.kind == "rows" and x.set == section.set
                        for x in other.sections
                    )
                ):
                    found.append(
                        Problem(
                            where,
                            f"{{cell:{key}.{name}}} points at the {section.set} "
                            "rows: name a table before this tab that lists them",
                        )
                    )
                elif name not in {x.key for x in other.columns}:
                    found.append(
                        Problem(
                            where, f"The {other.title} sheet has no column {name!r}"
                        )
                    )
    return found


def _copy(config: CategoryConfig, here: str, tab: OutputTab) -> list[Problem]:
    """A copy may leave out no column another tab reads: a look-up column's,
    or a cell a {cell:} names."""
    read: dict[str, str] = {}
    sheet = config.sheet(tab.source, tab.sheet) if tab.source and tab.sheet else None
    looked = {
        name
        for other in config.output.tabs
        for column in other.lookup_columns
        if column.lookup is not None and column.lookup.sheet == tab.key
        for name in (column.lookup.match, column.lookup.value)
    }
    if sheet is not None:
        for spec in schema_of(sheet).columns:
            if spec.field in looked:
                for key in spec.keys:
                    read[key] = "a look-up column reads it"
    for other in config.output.tabs:
        for column in other.columns:
            for placeholder, key, header in cell_links(column.excel or ""):
                if key == tab.key:
                    read[normalize_header(header)] = f"{placeholder} reads it"
    found: list[Problem] = []
    for h, header in enumerate(tab.hidden):
        why = read.get(normalize_header(header))
        if why is not None:
            found.append(
                Problem(f"{here}.hidden[{h}]", f"{header!r} cannot be left out: {why}")
            )
    return found


def _source(
    config: CategoryConfig, here: str, tab: OutputTab, roles: set[str]
) -> list[Problem]:
    if tab.source not in roles:
        return [Problem(f"{here}.source", f"No upload is called {tab.source!r}")]
    if tab.sheet is None or config.sheet(tab.source, tab.sheet) is None:
        return [Problem(f"{here}.sheet", f"{tab.source} has no sheet {tab.sheet!r}")]
    return []


def _settings(config: CategoryConfig) -> list[Problem]:
    """Rates, tolerances and dates that read as what they are, and the name an
    upload's sheet gives its rows."""
    found: list[Problem] = []
    for i, parameter in enumerate(config.parameters):
        if not _NAME.fullmatch(parameter.key):
            found.append(
                Problem(f"parameters[{i}].key", f"{parameter.key!r} is not a name")
            )
        try:
            parameter.typed()
        except (ValueError, ArithmeticError):
            wanted = "a date, YYYY-MM-DD" if parameter.kind == "date" else "a number"
            found.append(
                Problem(
                    f"parameters[{i}].value",
                    f"{parameter.value!r} is not {wanted}",
                )
            )
    for i, upload in enumerate(config.inputs):
        for j, sheet in enumerate(upload.sheets):
            if sheet.set is not None and not _NAME.fullmatch(sheet.set):
                found.append(
                    Problem(
                        f"inputs[{i}].sheets[{j}].set", f"{sheet.set!r} is not a name"
                    )
                )
    return found
