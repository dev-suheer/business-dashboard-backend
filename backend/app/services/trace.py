"""How a report column is worked out, step by step.

The editor's mini-map. A table column, a dashboard figure or a breakdown column
is followed through every formula it reads, down to the uploaded columns, the
status rules and the rates: from the setup alone, without a run or a file.
Where each name comes from is told by the setup check's own scopes
(``app/config_model/scopes.py``).
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from app.config_model.models import CategoryConfig, OutputTab, Section, cell_links
from app.config_model.scopes import (
    NOWHERE,
    Known,
    _literal,
    _per_member,
    formula_places,
    origins_of,
)
from app.expr import FormulaError, parse
from app.expr.parser import Binary, Call, Node, Ref, Unary

# How far a trace goes.
DEPTH = 12
STEPS = 400
TEXT = 60
# Between a tab and the column of it a title names.
_ARROW = chr(0x203A)


@dataclass
class Step:
    """One step of a trace: a name, what it is and where it comes from, its
    formula, and the steps it reads."""

    name: str
    kind: str
    where: str | None = None
    formula: str | None = None
    # The editor element it is, to go to: out:tab:column, order:field, …
    id: str | None = None
    # Already traced above.
    repeat: bool = False
    children: list["Step"] = field(default_factory=list)


@dataclass
class Trace:
    title: str
    root: Step


@dataclass(frozen=True)
class Target:
    tab: str
    column: str | None = None
    block: int | None = None
    value: int | None = None
    # A breakdown column by its place, or "name" for its names.
    breakdown: int | str | None = None


class TraceTargetError(LookupError):
    """The target names nothing the setup has."""


def trace(config: CategoryConfig, target: Target) -> Trace:
    """How ``target`` is worked out in ``config``."""
    return _Tracer(config).trace(target)


def _short(text: str) -> str:
    return text if len(text) <= TEXT else text[: TEXT - 1] + "…"


# --- Reading a formula ------------------------------------------------------------


@dataclass(frozen=True)
class _Read:
    """A name a formula reads, and the scope it is read in."""

    ref: Ref
    static: Known | None


def _parts(node: Node, static: Known | None) -> Iterator[_Read | Call]:
    """What a formula's steps are, in the order written: each name read where
    the formula is, and each aggregate (its rows are a step of their own)."""
    if isinstance(node, Ref):
        yield _Read(node, static)
    elif isinstance(node, Unary):
        yield from _parts(node.operand, static)
    elif isinstance(node, Binary):
        yield from _parts(node.left, static)
        yield from _parts(node.right, static)
    elif isinstance(node, Call):
        per_member = _per_member(node.name, len(node.args))
        if per_member is not None:
            yield node
        for i, arg in enumerate(node.args):
            if per_member is None or i not in per_member:
                yield from _parts(arg, static)


_FIELD = re.compile(r"model\.fields\[(\d+)\]$")
_COLUMN = re.compile(r"inputs\[(\d+)\]\.sheets\[(\d+)\]\.columns\[(\d+)\]$")
_SHEET_FIELD = re.compile(r"inputs\[(\d+)\]\.sheets\[(\d+)\]\.fields\[(\d+)\]$")
_ROW_FIELD = re.compile(r"model\.row_fields\[(\d+)\]\.fields\[(\d+)\]$")
_GROUP_FIELD = re.compile(r"model\.groups\[(\d+)\]\.fields\[(\d+)\]$")
_CONTEXT = re.compile(r"model\.context\[(\d+)\]$")
_METRIC = re.compile(r"model\.metrics\[(\d+)\]$")


class _Tracer:
    def __init__(self, config: CategoryConfig) -> None:
        self.config = config
        self.model = config.model
        _walk, places = formula_places(config)
        self.places: dict[str, tuple[Known | None, ...]] = {
            path: tuple(scopes) for path, _formula, scopes in places
        }
        self.steps = 0
        self.seen: set[str] = set()
        # What a name with nowhere it comes from is, on the traced rows.
        self.leaf = "value"
        self.leaf_where: str | None = None
        # A breakdown's totals of a name's parts, by key, and its parts' list.
        self.totals: dict[str, str] = {}
        self.parts: str | None = None

    # -- the target --

    def trace(self, target: Target) -> Trace:
        t, tab = self._tab(target.tab)
        if tab.kind == "dashboard":
            if target.breakdown is not None:
                return self._breakdown(t, tab, target.breakdown)
            return self._figure(tab, target)
        if target.column is None:
            raise TraceTargetError("Name the column to trace")
        c = next(
            (i for i, col in enumerate(tab.columns) if col.key == target.column), None
        )
        if c is None:
            raise TraceTargetError(
                f"The {tab.title} tab has no column {target.column!r}"
            )
        title = f"{tab.title} {_ARROW} {tab.columns[c].header}"
        if tab.copies:
            return Trace(title, self._copy(tab, c))
        s = next(
            (
                i
                for i, section in enumerate(tab.sections)
                if section.kind == "rows"
                and (section.set is not None or section.upload is not None)
            ),
            None,
        )
        if s is None:
            column = tab.columns[c]
            return Trace(
                title,
                Step(
                    column.header,
                    "column",
                    tab.title,
                    column.value,
                    _out(tab, column.key),
                ),
            )
        self._leaf_for(tab.sections[s])
        return Trace(title, self._column(t, tab, c, s, 0))

    def _tab(self, key: str) -> tuple[int, OutputTab]:
        for t, tab in enumerate(self.config.output.tabs):
            if tab.key == key:
                return t, tab
        raise TraceTargetError(f"No tab {key!r}")

    def _leaf_for(self, section: Section) -> None:
        if section.upload is not None:
            upload = self.config.input(section.upload)
            self.leaf = "upload column"
            self.leaf_where = upload.title if upload else section.upload
        elif section.set == "exceptions":
            self.leaf, self.leaf_where = "exception", "the exception"
        elif section.set in ("wallet_checks", "wallet_unmatched"):
            self.leaf, self.leaf_where = "wallet check", "the wallet check"

    def _column(self, t: int, tab: OutputTab, c: int, s: int, depth: int) -> Step:
        """A table column, as one of its rows parts works it out."""
        column = tab.columns[c]
        section = tab.sections[s]
        here = f"output.tabs[{t}]"
        if column.excel is not None:
            return self._excel(tab, c, section, depth)
        if section.values:
            formula = section.values.get(column.key)
            place = f"{here}.sections[{s}].values.{column.key}"
            index = 0
        else:
            formula = column.value or column.key
            place = f"{here}.columns[{c}].{'value' if column.value else 'key'}"
            parts = [
                i
                for i, sec in enumerate(tab.sections)
                if sec.kind == "rows"
                and (sec.set is not None or sec.upload is not None)
                and not sec.values
            ]
            index = parts.index(s) if s in parts else 0
        step = Step(column.header, "column", tab.title, formula, _out(tab, column.key))
        if formula is None:
            return step
        scopes = self.places.get(place, ())
        static = scopes[index] if index < len(scopes) else None
        self._count()
        step.children = self._formula(formula, static, depth + 1)
        return step

    def _excel(self, tab: OutputTab, c: int, section: Section, depth: int) -> Step:
        """An Excel formula: each {cell:} it points at, on the same row. A link
        followed already in this trace (a ring of links) is shown once."""
        column = tab.columns[c]
        assert column.excel is not None
        step = Step(
            column.header,
            "Excel formula",
            tab.title,
            column.excel,
            _out(tab, column.key),
        )
        place = f"excel:{tab.key}:{column.key}"
        if place in self.seen:
            step.repeat = True
            return step
        if depth > DEPTH or self.steps > STEPS:
            return step
        self.seen.add(place)
        self._count()
        for _placeholder, tab_key, name in cell_links(column.excel):
            linked = next(
                (x for x in self.config.output.tabs if x.key == tab_key), None
            )
            if linked is None:
                continue
            if section.upload is not None:
                step.children.append(
                    Step(name, "upload column", f"{linked.title} (as uploaded)")
                )
                continue
            u, _ = self._tab(tab_key)
            lc = next((i for i, x in enumerate(linked.columns) if x.key == name), None)
            ls = next(
                (
                    i
                    for i, x in enumerate(linked.sections)
                    if x.kind == "rows" and x.set == section.set
                ),
                None,
            )
            if lc is not None and ls is not None:
                step.children.append(self._column(u, linked, lc, ls, depth + 1))
        return step

    def _copy(self, tab: OutputTab, c: int) -> Step:
        """A column added to a copy: read on each of the upload's rows."""
        assert tab.source is not None
        column = tab.columns[c]
        upload = self.config.input(tab.source)
        self.leaf = "upload column"
        self.leaf_where = upload.title if upload else tab.source
        if column.excel is not None:
            step = Step(
                column.header,
                "Excel formula",
                tab.title,
                column.excel,
                _out(tab, column.key),
            )
            for header in re.findall(r"\{col:([^{}]*)\}", column.excel):
                step.children.append(Step(header, "upload column", self.leaf_where))
            return step
        formula = column.value or column.key
        step = Step(column.header, "column", tab.title, formula, _out(tab, column.key))
        step.children = self._formula(formula, Known({}, open=True), 1)
        return step

    def _figure(self, tab: OutputTab, target: Target) -> Trace:
        """A dashboard figure: worked out once for the run."""
        if target.block is None or target.value is None:
            raise TraceTargetError("Name the dashboard cell to trace")
        if target.block < 0 or target.value < 0:
            raise TraceTargetError("No such dashboard cell")
        try:
            block = tab.blocks[target.block]
            metric = block.values[target.value].metric
            header = block.headers[target.value]
        except IndexError as exc:
            raise TraceTargetError("No such dashboard cell") from exc
        root = self._metric_step(metric, 0)
        root.name = header
        root.where = metric or None
        return Trace(f"{tab.title} {_ARROW} {header}", root)

    def _metric_step(self, key: str, depth: int) -> Step:
        # Before How orders are made is set up, there are no figures yet.
        if self.model is None:
            return Step(key, "figure")
        k = next((i for i, m in enumerate(self.model.metrics) if m.key == key), None)
        if k is None:
            return Step(key, "figure")
        step = Step(key, "figure", None, self.model.metrics[k].formula, f"metric:{key}")
        return self._follow(step, f"model.metrics[{k}].formula", depth)

    # -- the breakdown table --

    def _breakdown(self, t: int, tab: OutputTab, which: int | str) -> Trace:
        spec = tab.breakdown
        if spec is None:
            raise TraceTargetError(f"The {tab.title} tab has no breakdown table")
        here = f"output.tabs[{t}].breakdown"
        self.totals = {total.key: total.formula for total in spec.totals}
        self.parts = spec.parts
        name = Step(
            spec.name_header,
            "breakdown name",
            f"one row per name of {spec.set}"
            + (f", each split into its {spec.parts}" if spec.parts else ""),
            spec.name,
            f"bdname:{tab.key}",
        )
        scopes = self.places.get(f"{here}.name", ())
        name.children = self._formula(spec.name, scopes[0] if scopes else None, 1)
        if which == "name":
            return Trace(f"{tab.title} {_ARROW} {spec.name_header}", name)
        if not isinstance(which, int) or not 0 <= which < len(spec.columns):
            raise TraceTargetError("No such breakdown column")
        column = spec.columns[which]
        adds = "one per member" if column.add == "count" else "a sum over the members"
        root = Step(
            column.header,
            "breakdown",
            f"{tab.title}: {adds}",
            None,
            f"bd:{tab.key}:{which}",
        )
        root.children.append(name)
        for part, formula in (("when", column.when), ("value", column.value)):
            if formula is None or (part == "when" and formula == "TRUE"):
                continue
            scopes = self.places.get(f"{here}.columns[{which}].{part}", ())
            child = Step(part, "breakdown", "on each member", formula)
            child.children = self._formula(formula, scopes[0] if scopes else None, 1)
            root.children.append(child)
        return Trace(f"{tab.title} {_ARROW} {column.header}", root)

    # -- a formula and the names it reads --

    def _formula(self, formula: str, static: Known | None, depth: int) -> list[Step]:
        """A step for each name the formula reads, and for each list its
        aggregates go over, in the order written."""
        try:
            node = parse(formula)
        except FormulaError:
            return []
        children: list[Step] = []
        named: set[str] = set()
        for part in _parts(node, static):
            if isinstance(part, Call):
                children.append(self._over(part, static, depth))
            elif part.ref.name not in named:
                named.add(part.ref.name)
                children.append(self._name(part.ref, part.static, depth))
        return children

    def _over(self, call: Call, static: Known | None, depth: int) -> Step:
        """An aggregate's list: what it is, its condition, and the names its
        per-row arguments read."""
        name = _literal(call.args[0]) if call.args else None
        if name is None:
            return Step(call.name, "rows")
        where = self._list_where(name)
        step = Step(
            name,
            "rows",
            f"{call.name} over {self._list_words(name)}",
            where[0] if where else None,
        )
        self._count()
        if depth > DEPTH or self.steps > STEPS:
            return step
        inner = static.collection(name) if static is not None else None
        seen: set[str] = set()
        for i in sorted(_per_member(call.name, len(call.args)) or ()):
            if i >= len(call.args):
                continue
            for part in _parts(call.args[i], inner):
                if isinstance(part, Call):
                    step.children.append(self._over(part, inner, depth + 1))
                elif part.ref.name not in seen:
                    seen.add(part.ref.name)
                    step.children.append(self._name(part.ref, part.static, depth + 1))
        if where is not None:
            formula, place = where
            if place not in self.seen:
                self.seen.add(place)
                scopes = self.places.get(place, ())
                step.children += [
                    child
                    for child in self._formula(
                        formula, scopes[0] if scopes else None, depth + 1
                    )
                    if child.name not in seen
                ]
        return step

    def _list_words(self, name: str) -> str:
        """What a list is, in a few words."""
        model = self.model
        if name == "entities":
            return "every order"
        if model is not None:
            for s in model.sets:
                if s.key == name:
                    return _short(s.about) if s.about else f"from {s.over}"
            for member in model.members:
                held = member.repeats if member.primary else member.name
                if held == name:
                    return f"the order's {member.set} rows, joined by {member.key}"
        for upload in self.config.inputs:
            for sheet in upload.sheets:
                if (sheet.set or upload.role) == name:
                    return f"rows of {upload.title}"
        return name

    def _list_where(self, name: str) -> tuple[str, str] | None:
        if self.model is None:
            return None
        for i, s in enumerate(self.model.sets):
            if s.key == name and s.where != "TRUE":
                return s.where, f"model.sets[{i}].where"
        return None

    def _name(self, ref: Ref, static: Known | None, depth: int) -> Step:
        """A name: what it is, where it comes from, and its own steps."""
        self._count()
        name = ref.name
        head, _, rest = name.partition(".")
        if head == "param" and rest:
            label = next(
                (p.label for p in self.config.parameters if p.key == rest), None
            )
            return Step(name, "rate", label)
        origin = self._origin(ref, static)
        if origin is not None:
            return self._defined(name, origin, depth)
        return self._undefined(name, depth)

    def _origin(self, ref: Ref, static: Known | None) -> str | None:
        if static is None:
            return None
        origins = origins_of(ref, static)
        deepest = next((o for o in reversed(origins) if o and o != NOWHERE), None)
        return min(deepest) if deepest else None

    def _defined(self, name: str, origin: str, depth: int) -> Step:
        config, model = self.config, self.model
        if m := _FIELD.match(origin):
            assert model is not None
            named = model.fields[int(m.group(1))]
            step = Step(name, "order field", None, named.formula, f"order:{named.key}")
            return self._follow(step, f"{origin}.formula", depth)
        if m := _COLUMN.match(origin):
            upload = config.inputs[int(m.group(1))]
            sheet = upload.sheets[int(m.group(2))]
            column = sheet.columns[int(m.group(3))]
            header = column.headers[0] if column.headers else column.field
            return Step(
                name,
                "upload column",
                f"{upload.title} · {header}",
                id=f"in:{upload.role}:{sheet.key}:{column.field}",
            )
        if m := _SHEET_FIELD.match(origin):
            upload = config.inputs[int(m.group(1))]
            sheet = upload.sheets[int(m.group(2))]
            named = sheet.fields[int(m.group(3))]
            step = Step(
                name,
                "upload field",
                f"{upload.title}, worked out",
                named.formula,
                f"in:{upload.role}:{sheet.key}:{named.key}",
            )
            return self._follow(step, f"{origin}.formula", depth)
        if m := _ROW_FIELD.match(origin):
            assert model is not None
            spec = model.row_fields[int(m.group(1))]
            named = spec.fields[int(m.group(2))]
            step = Step(name, "row field", f"rows of {spec.set}", named.formula)
            return self._follow(step, f"{origin}.formula", depth)
        if m := _GROUP_FIELD.match(origin):
            assert model is not None
            group = model.groups[int(m.group(1))]
            named = group.fields[int(m.group(2))]
            step = Step(name, "group field", f"group {group.name}", named.formula)
            return self._follow(step, f"{origin}.formula", depth)
        if m := _CONTEXT.match(origin):
            assert model is not None
            named = model.context[int(m.group(1))]
            step = Step(name, "run value", None, named.formula)
            return self._follow(step, f"{origin}.formula", depth)
        if m := _METRIC.match(origin):
            assert model is not None
            return self._metric_step(model.metrics[int(m.group(1))].key, depth)
        if origin == "model.key":
            return Step(name, "key", self._key_words())
        return Step(name, self.leaf, self.leaf_where)

    def _follow(self, step: Step, place: str, depth: int) -> Step:
        """Go on into a step's own formula, unless it is shown above or the
        trace has gone far enough."""
        if step.formula is None:
            return step
        if place in self.seen:
            step.repeat = True
            return step
        if depth > DEPTH or self.steps > STEPS:
            return step
        self.seen.add(place)
        scopes = self.places.get(place, ())
        step.children = self._formula(
            step.formula, scopes[0] if scopes else None, depth + 1
        )
        return step

    def _undefined(self, name: str, depth: int) -> Step:
        """A name the setup does not define in a formula: the verdict, an
        order's own row, the contact, a value of the row itself."""
        model = self.model
        if name == "verdict" and model is not None and model.rules:
            return self._verdict(depth)
        if model is not None:
            for member in model.members:
                if member.primary and member.name == name:
                    return Step(
                        name,
                        "order's row",
                        f"its {member.set} row, joined by {member.key}",
                    )
            for side in model.sides:
                if side.role == name:
                    return Step(name, "order's row", f"its {side.role} row")
        if name in self.totals:
            return Step(
                name,
                "name's total",
                f"{self.totals[name]} added up over its {self.parts or 'parts'}",
            )
        if name == "share":
            return Step(name, "share", "the name's part of the member")
        if name == "contact":
            return Step(name, "contact", "the upload's second Customer column")
        if name == "note":
            return Step(name, "note", "the deciding status rule's note")
        if name.startswith("wallet_check."):
            return Step(name, "wallet check")
        return Step(name, self.leaf, self.leaf_where)

    def _verdict(self, depth: int) -> Step:
        """The verdict: the status rules, tried in order; the first that holds
        decides."""
        model = self.model
        assert model is not None
        step = Step(
            "verdict",
            "status rules",
            f"{len(model.rules)} rules, the first that holds decides",
        )
        if "model.rules" in self.seen:
            step.repeat = True
            return step
        self.seen.add("model.rules")
        for n, rule in enumerate(model.rules):
            outcome = (
                f"{rule.clean} or {rule.broken} by the checks"
                if rule.checks
                else rule.verdict
            )
            about = f": {_short(rule.about)}" if rule.about else ""
            child = Step(f"rule {n + 1}", "status rule", f"{outcome}{about}", rule.when)
            if depth <= DEPTH and self.steps <= STEPS:
                scopes = self.places.get(f"model.rules[{n}].when", ())
                child.children = self._formula(
                    rule.when, scopes[0] if scopes else None, depth + 1
                )
            step.children.append(child)
        return step

    def _key_words(self) -> str:
        model = self.model
        if model is not None and model.members:
            keys = sorted({m.key for m in model.members})
            return "the order's key, joined by " + ", ".join(keys)
        return "the order's key"

    def _count(self) -> None:
        self.steps += 1


def _out(tab: OutputTab, key: str) -> str:
    return f"out:{tab.key}:{key}"
