"""Renaming a field, a figure, an upload's column or a report column, and every
use of it with it.

A formula names a field by its name, so a rename that left the formulas alone
would leave them naming nothing: some in parts of the setup the editor does not
show (a configured set, the sort, the wallet check, the history), which the
setup check would then refuse to save. So a rename finds every formula that
reads the thing renamed, through the same scopes the check uses
(``scopes.py``), and rewrites just that name in it. A formula that reads another
thing of the same name, a row's own column, say, is left as it is.

An upload's column is renamed by the name its rows give it (its ``field``), on
every sheet of its row set. A report column is renamed with the totals, blocks
and lookups that name it by key.

Nothing is saved: the editor takes the renamed setup into its draft.
"""

import re
from dataclasses import dataclass
from typing import Any

from app.config_model.models import CategoryConfig
from app.config_model.scopes import Origins, formula_places, origins_of
from app.expr import FormulaError, parse
from app.expr.parser import reference_spans
from app.recon import wallet

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# The run's own names, which no field or figure may take.
_RUN_NAMES = frozenset({"param", "label", "wallet_check", "lookup"})
# What a row of an upload holds besides its columns.
_ROW_NAMES = frozenset({"sheet", "row"})

_ELEMENTS = (
    ("order field", re.compile(r"model\.fields\[(\d+)\]")),
    ("figure", re.compile(r"model\.metrics\[(\d+)\]")),
    ("context value", re.compile(r"model\.context\[(\d+)\]")),
    ("sheet field", re.compile(r"inputs\[(\d+)\]\.sheets\[(\d+)\]\.fields\[(\d+)\]")),
    (
        "upload column",
        re.compile(r"inputs\[(\d+)\]\.sheets\[(\d+)\]\.columns\[(\d+)\]"),
    ),
    ("row field", re.compile(r"model\.row_fields\[(\d+)\]\.fields\[(\d+)\]")),
    ("group field", re.compile(r"model\.groups\[(\d+)\]\.fields\[(\d+)\]")),
    ("column", re.compile(r"output\.tabs\[(\d+)\]\.columns\[(\d+)\]")),
)

# What reads a wallet history's own columns, by their names, and those columns.
# A column added to it is its list's alone, and is renamed like any other.
_FIXED_READERS = {
    "wallet": (
        "The wallet check reads",
        frozenset(c.field for c in wallet.WALLET_SCHEMA.columns),
    ),
}

_STEP = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


class RenameError(ValueError):
    """A rename that cannot be done, and where."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(message)
        self.path = path
        self.message = message


@dataclass(frozen=True)
class Renamed:
    config: CategoryConfig
    # Every part of the setup the rename changed.
    changed: list[str]


def rename(config: CategoryConfig, path: str, name: str) -> Renamed:
    """Rename what ``path`` names (``model.fields[3]``, ``model.metrics[2]``,
    ``inputs[0].sheets[1].fields[2]``, ``inputs[0].sheets[1].columns[4]``,
    ``model.row_fields[0].fields[1]``, ``output.tabs[1].columns[5]``) to
    ``name``, with every use of it."""
    kind, numbers = _element(path)
    if kind == "column":
        return _rename_column(config, path, numbers[0], numbers[1], name)
    if kind == "upload column":
        upload = config.inputs[numbers[0]]
        fixed = _FIXED_READERS.get(upload.kind)
        column = upload.sheets[numbers[1]].columns[numbers[2]]
        if fixed is not None and column.field in fixed[1]:
            raise RenameError(
                path,
                f"{fixed[0]} the {upload.title}'s columns by these names, so they "
                "cannot change",
            )
    # An upload's column is named by its field; everything else by its key.
    attribute = "field" if kind == "upload column" else "key"
    doc = config.model_dump(mode="json")
    old = _get(doc, path)[attribute]
    if name == old:
        return Renamed(config, [])
    _valid(config, kind, numbers, path, old, name)
    targets = _same_field(config, kind, numbers, path, old)

    walk, places = formula_places(config)
    # (formula path, k-th name it reads, part of that name) for every use.
    uses: set[tuple[str, int, int]] = set()
    rewritten: dict[str, str] = {}
    for where_path, formula, scopes in places:
        found = _uses(walk, formula, scopes, targets, where_path)
        if found:
            uses |= {(where_path, k, part) for k, part in found}
            rewritten[where_path] = _rewrite(formula, found, name)

    changed: list[str] = []
    for where_path, formula in rewritten.items():
        changed.append(_write_formula(doc, where_path, formula))
    for target in sorted(targets):
        _get(doc, target)[attribute] = name
        changed.append(f"{target}.{attribute}")
    changed += _rename_keys(config, doc, kind, targets, old, name)
    if kind == "upload column":
        changed += _rename_lookups(config, doc, targets, old, name)
    renamed = CategoryConfig.model_validate(doc)
    _same_meaning(renamed, targets, uses)
    return Renamed(renamed, sorted(set(changed)))


# --- What is renamed --------------------------------------------------------------


def _element(path: str) -> tuple[str, list[int]]:
    for kind, pattern in _ELEMENTS:
        found = pattern.fullmatch(path)
        if found:
            return kind, [int(n) for n in found.groups()]
    raise RenameError(
        path, "Only a field, a figure, an upload's column or a column can be renamed"
    )


def _valid(
    config: CategoryConfig,
    kind: str,
    numbers: list[int],
    path: str,
    old: str,
    name: str,
) -> None:
    if not NAME.fullmatch(name):
        raise RenameError(
            path,
            f"{name!r} is not a name: use letters, digits and _, not first a digit",
        )
    if name.upper() in ("TRUE", "FALSE"):
        raise RenameError(path, f"{name} is a value, not a name")
    model = config.model
    taken: set[str] = set()
    if kind == "order field" and model is not None:
        taken = {"key", "verdict", "note", *(f.key for f in model.fields)}
        for side in model.sides:
            taken |= {side.role, f"{side.role}_active", f"{side.role}_copies"}
        taken |= {m.name for m in model.members if m.primary}
    elif kind in ("figure", "context value") and model is not None:
        taken = {
            *_RUN_NAMES,
            *(m.key for m in model.metrics),
            *(c.key for c in model.context),
        }
    elif kind == "sheet field":
        # A field may take its own sheet's column name, replacing the column
        # on purpose; whether that changes what a formula reads is checked after.
        sheet = config.inputs[numbers[0]].sheets[numbers[1]]
        set_name = sheet.set or config.inputs[numbers[0]].role
        taken = {
            *_ROW_NAMES,
            *(f.key for f in sheet.fields),
            *_row_fields(config, set_name),
        }
    elif kind == "upload column":
        # A column may take the name of the field that cleans it, on its sheet
        # or the run's own for its rows, which then replaces it as before;
        # whether that changes what a formula reads is checked after.
        sheet = config.inputs[numbers[0]].sheets[numbers[1]]
        taken = {*_ROW_NAMES, *(c.field for c in sheet.columns)}
    elif kind == "row field" and model is not None:
        taken = {*_ROW_NAMES, *_row_fields(config, model.row_fields[numbers[0]].set)}
    elif kind == "group field" and model is not None:
        group = model.groups[numbers[0]]
        taken = {"key", *(f.key for f in group.fields)}
    if name in taken - {old}:
        raise RenameError(path, f"There is already something called {name!r} there")


def _row_fields(config: CategoryConfig, set_name: str) -> set[str]:
    """The run's own row fields for a set."""
    if config.model is None:
        return set()
    return {
        f.key
        for spec in config.model.row_fields
        if spec.set == set_name
        for f in spec.fields
    }


def _same_field(
    config: CategoryConfig, kind: str, numbers: list[int], path: str, old: str
) -> Origins:
    """What is renamed: the element, and for a sheet's field or column, the same
    field or column on the other sheets of its row set, since a formula on those
    rows cannot tell them apart."""
    if kind not in ("sheet field", "upload column"):
        return frozenset({path})
    upload = config.inputs[numbers[0]]
    set_name = upload.sheets[numbers[1]].set or upload.role
    found = set()
    for i, other in enumerate(config.inputs):
        for j, sheet in enumerate(other.sheets):
            if (sheet.set or other.role) != set_name:
                continue
            if kind == "sheet field":
                for k, named in enumerate(sheet.fields):
                    if named.key == old:
                        found.add(f"inputs[{i}].sheets[{j}].fields[{k}]")
            else:
                for c, column in enumerate(sheet.columns):
                    if column.field == old:
                        found.add(f"inputs[{i}].sheets[{j}].columns[{c}]")
    # A column of the same name on another sheet is a clash the uses show.
    return frozenset(found)


# --- Finding and rewriting the uses -----------------------------------------------


def _uses(
    walk: Any, formula: str, scopes: tuple[Any, ...], targets: Origins, where: str
) -> set[tuple[int, int]]:
    """The names, and the part of each, that read the renamed thing: in any of
    the scopes the formula runs in."""
    try:
        node = parse(formula)
    except FormulaError:
        return set()
    found: set[tuple[int, int]] = set()
    for scope in scopes:
        if scope is None:
            continue
        for k, (ref, inner) in enumerate(walk.refs(node, scope)):
            if inner is None:
                continue
            for part, origin in enumerate(origins_of(ref, inner)):
                if not origin & targets:
                    continue
                if not origin <= targets:
                    raise RenameError(
                        where,
                        f"{ref.name.split('.')[part]!r} here may also be another "
                        "field of the same name",
                    )
                found.add((k, part))
    return found


def _rewrite(formula: str, uses: set[tuple[int, int]], name: str) -> str:
    spans = reference_spans(formula)
    text = formula
    for k in sorted({k for k, _ in uses}, reverse=True):
        start, end = spans[k]
        parts = text[start:end].split(".")
        for use_k, part in uses:
            if use_k == k:
                parts[part] = name
        text = text[:start] + ".".join(parts) + text[end:]
    return text


def _write_formula(doc: dict[str, Any], path: str, formula: str) -> str:
    """Put a rewritten formula back. A column with no formula reads the name its
    key is: it gets the new name as its formula, and keeps its key."""
    holder, last = _parent(doc, path)
    if last == "key" and ".columns[" in path:
        holder["value"] = formula
        return path[: -len("key")] + "value"
    holder[last] = formula
    return path


def _rename_keys(
    config: CategoryConfig,
    doc: dict[str, Any],
    kind: str,
    targets: Origins,
    old: str,
    name: str,
) -> list[str]:
    """Names used outside formulas: a side's key field, a figure on the
    dashboard or in a block."""
    changed: list[str] = []
    model = config.model
    if model is None:
        return changed
    if kind in ("sheet field", "upload column", "row field"):
        for i, side in enumerate(model.sides):
            if side.key == old and _row_origin(config, side.role, old) & targets:
                doc["model"]["sides"][i]["key"] = name
                changed.append(f"model.sides[{i}].key")
    if kind == "figure":
        for t, tab in enumerate(doc["output"]["tabs"]):
            for b, block in enumerate(tab.get("blocks", [])):
                for v, value in enumerate(block.get("values", [])):
                    if value.get("metric") == old:
                        value["metric"] = name
                        changed.append(
                            f"output.tabs[{t}].blocks[{b}].values[{v}].metric"
                        )
            for s, section in enumerate(tab.get("sections", [])):
                for r, row in enumerate(section.get("rows", [])):
                    for c, cell in enumerate(row.get("cells", [])):
                        if cell.get("metric") == old:
                            cell["metric"] = name
                            changed.append(
                                f"output.tabs[{t}].sections[{s}].rows[{r}].cells[{c}].metric"
                            )
    return changed


_UPLOAD_COLUMN = re.compile(r"inputs\[(\d+)\]\.sheets\[(\d+)\]\.columns\[\d+\]")


def _rename_lookups(
    config: CategoryConfig, doc: dict[str, Any], targets: Origins, old: str, name: str
) -> list[str]:
    """A look-up column names the columns of the upload its copy copies: they
    follow the upload's column."""
    renamed: set[tuple[str, str]] = set()
    for target in targets:
        found = _UPLOAD_COLUMN.fullmatch(target)
        if found:
            upload = config.inputs[int(found.group(1))]
            renamed.add((upload.role, upload.sheets[int(found.group(2))].key))
    changed: list[str] = []
    for t, tab in enumerate(doc["output"]["tabs"]):
        for c, column in enumerate(tab.get("lookup_columns", [])):
            lookup = column.get("lookup")
            copy = config.tab(lookup["sheet"]) if lookup else None
            if (
                lookup is None
                or copy is None
                or (copy.source, copy.sheet) not in renamed
            ):
                continue
            for part in ("match", "value"):
                if lookup.get(part) == old:
                    lookup[part] = name
                    changed.append(
                        f"output.tabs[{t}].lookup_columns[{c}].lookup.{part}"
                    )
    return changed


def _row_origin(config: CategoryConfig, set_name: str, name: str) -> Origins:
    """Where a row of the set gets a name from: the run's row field of that
    name, or else each sheet's field of that name, or else its column."""
    if config.model is not None:
        own = {
            f"model.row_fields[{i}].fields[{k}]"
            for i, spec in enumerate(config.model.row_fields)
            if spec.set == set_name
            for k, named in enumerate(spec.fields)
            if named.key == name
        }
        if own:
            return frozenset(own)
    found = set()
    for i, upload in enumerate(config.inputs):
        for j, sheet in enumerate(upload.sheets):
            if (sheet.set or upload.role) != set_name:
                continue
            here = f"inputs[{i}].sheets[{j}]"
            fields = [k for k, named in enumerate(sheet.fields) if named.key == name]
            if fields:
                found.add(f"{here}.fields[{fields[-1]}]")
                continue
            for c, column in enumerate(sheet.columns):
                if column.field == name:
                    found.add(f"{here}.columns[{c}]")
    return frozenset(found)


def _same_meaning(
    renamed: CategoryConfig, targets: Origins, uses: set[tuple[str, int, int]]
) -> None:
    """After the rename, exactly the renamed names read the renamed thing: none
    of them now reads something else of the new name, and nothing that read
    something else of the new name now reads it."""
    walk, places = formula_places(renamed)
    after: set[tuple[str, int, int]] = set()
    for where_path, formula, scopes in places:
        for k, part in _uses(walk, formula, scopes, targets, where_path):
            after.add((where_path, k, part))
    expected = {(_moved(path), k, part) for path, k, part in uses}
    differs = sorted(after ^ expected)
    if differs:
        raise RenameError(
            differs[0][0],
            "That name is already used there, so the rename would change what a "
            "formula reads",
        )


def _moved(path: str) -> str:
    """Where a rewritten formula sits once written back."""
    if path.endswith(".key") and ".columns[" in path:
        return path[: -len("key")] + "value"
    return path


# --- Columns ----------------------------------------------------------------------

_PLACEHOLDER = r"\{{col:{}\}}"


def _rename_column(
    config: CategoryConfig, path: str, t: int, c: int, name: str
) -> Renamed:
    """A column's key, and every total, block and lookup of its table that names
    it. A column with no formula reads the name its key is: it keeps reading it."""
    tab = config.output.tabs[t]
    old = tab.columns[c].key
    if name == old:
        return Renamed(config, [])
    if not NAME.fullmatch(name):
        raise RenameError(
            path,
            f"{name!r} is not a name: use letters, digits and _, not first a digit",
        )
    if any(column.key == name for column in tab.columns):
        raise RenameError(path, f"The {tab.title} sheet already has a column {name!r}")
    doc = config.model_dump(mode="json")
    tab_doc = doc["output"]["tabs"][t]
    here = f"output.tabs[{t}]"
    column = tab_doc["columns"][c]
    changed = [f"{path}.key"]
    if column.get("value") is None and column.get("excel") is None:
        column["value"] = old
        changed.append(f"{path}.value")
    column["key"] = name

    def renamed_keys(mapping: dict[str, Any], where: str) -> dict[str, Any]:
        if old not in mapping:
            return mapping
        changed.append(where)
        return {name if key == old else key: value for key, value in mapping.items()}

    tab_doc["tolerances"] = renamed_keys(
        tab_doc.get("tolerances", {}), f"{here}.tolerances"
    )
    placeholder = re.compile(_PLACEHOLDER.format(re.escape(old)))
    for s, section in enumerate(tab_doc.get("sections", [])):
        at = f"{here}.sections[{s}]"
        section["values"] = renamed_keys(section.get("values", {}), f"{at}.values")
        section["styles"] = renamed_keys(section.get("styles", {}), f"{at}.styles")
        section["formats"] = renamed_keys(section.get("formats", {}), f"{at}.formats")
        for r, row in enumerate(section.get("rows", [])):
            for k, cell in enumerate(row.get("cells", [])):
                cell_path = f"{at}.rows[{r}].cells[{k}]"
                for which in ("col", "to_col"):
                    if cell.get(which) == old:
                        cell[which] = name
                        changed.append(f"{cell_path}.{which}")
                formula = cell.get("formula")
                if formula and placeholder.search(formula):
                    cell["formula"] = placeholder.sub("{col:" + name + "}", formula)
                    changed.append(f"{cell_path}.formula")
    for c, lookup_column in enumerate(tab_doc.get("lookup_columns", [])):
        lookup = lookup_column.get("lookup")
        if lookup and lookup.get("key_column") == old:
            lookup["key_column"] = name
            changed.append(f"{here}.lookup_columns[{c}].lookup.key_column")
    # Another table's Excel formulas read this table's cells as {cell:tab.column}.
    link = "{cell:" + tab.key + "." + old + "}"
    for u, other in enumerate(doc["output"]["tabs"]):
        for k, column in enumerate(other.get("columns", [])):
            excel = column.get("excel")
            if excel and link in excel:
                column["excel"] = excel.replace(
                    link, "{cell:" + tab.key + "." + name + "}"
                )
                changed.append(f"output.tabs[{u}].columns[{k}].excel")
    return Renamed(CategoryConfig.model_validate(doc), changed)


# --- Paths ------------------------------------------------------------------------


def _steps(path: str) -> list[str | int]:
    return [int(n) if n else word for word, n in _STEP.findall(path)]


def _get(doc: Any, path: str) -> Any:
    for step in _steps(path):
        doc = doc[step]
    return doc


def _parent(doc: Any, path: str) -> tuple[Any, Any]:
    steps = _steps(path)
    for step in steps[:-1]:
        doc = doc[step]
    return doc, steps[-1]
