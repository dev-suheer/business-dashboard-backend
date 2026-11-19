"""What each formula of a setup can see when it runs, so that a name it does not
have, or a value of the wrong kind, is found when the setup is saved rather than
when the next run reaches it.

The scopes here mirror the runner's (``app/runner/run.py``, ``inputs.py``,
``wallet.py``) and the report's (``app/render/generic.py``): a row of an upload
sees its own columns and fields, an order its sides or chosen rows and its
fields, the run its parameters, context and figures, each only once it
has been worked out. An aggregate looks into the set it names.

Each name also knows where it comes from (its origin: ``model.fields[3]``), so a
rename can find every formula that reads a field, and only those
(``rename.py``).

Anything this cannot be sure of is let through: a name under something it
cannot see into, an aggregate whose set is not written out, a value whose kind
it cannot tell, a scope made from a part of the setup that is itself broken
(that part is reported once, where it is). A run on real files finds the
rest. What it does report fails on any files.
"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import get_close_matches

from app.config_model.models import (
    Breakdown,
    CategoryConfig,
    Group,
    InputSheet,
    Model,
    Named,
    OutputTab,
    Raise,
)
from app.expr import FormulaError, parse
from app.expr.functions import read_criterion
from app.expr.parser import Binary, Call, Literal, Node, Ref, Unary

# --- What a value can be -----------------------------------------------------------

Kind = frozenset[str]
NUMBER: Kind = frozenset({"number"})
TEXT: Kind = frozenset({"text"})
DATE: Kind = frozenset({"date"})
BOOL: Kind = frozenset({"bool"})
BLANK: Kind = frozenset({"blank"})

# The wallet check's rows and the history's movements no row had, as a table
# lists them (app/recon/wallet.py WalletCheck, WalletEvent): by name, each
# field's kind.
WALLET_LISTS: dict[str, dict[str, Kind]] = {
    "wallet_checks": {
        "vertical": TEXT,
        "sg_order_id": TEXT,
        "booking_id": TEXT | BLANK,
        "order_date": DATE | BLANK,
        "portal_wallet_amount": NUMBER,
        "history_paid": NUMBER | BLANK,
        "history_refunded": NUMBER | BLANK,
        "events": NUMBER,
        "state": TEXT,
        "diff": NUMBER | BLANK,
    },
    "wallet_unmatched": {
        "source": TEXT,
        "row": NUMBER,
        "event_id": TEXT | BLANK,
        "vertical": TEXT,
        "sg_order_id": TEXT,
        "amount": NUMBER,
        "message": TEXT | BLANK,
        "event_date": DATE | BLANK,
        "payment_type": TEXT | BLANK,
    },
}
ANY: Kind = frozenset({"any"})

_WORDS = {
    "bool": "TRUE or FALSE",
    "date": "a date",
    "number": "a number",
    "text": "text",
}

# How an upload's column is read (app/runner/inputs.py READERS).
_COLUMN: dict[str, Kind] = {
    "text": TEXT | BLANK,
    "id": TEXT | BLANK,
    "date": DATE | BLANK,
    "money": NUMBER,
    "decimal": NUMBER | BLANK,
    "rate": NUMBER | BLANK,
}

# Where a name comes from: the paths of the parts of the setup that define it.
Origins = frozenset[str]
NOWHERE: Origins = frozenset()
# An order's key, made by the pairing or the gathering, as a place of its own.
KEY_ORIGIN: Origins = frozenset({"model.key"})


class Open:
    """A value this cannot see into (the labels, the wallet check): any name
    under it goes."""


OPEN = Open()


@dataclass(eq=False)
class Record:
    """A value with a known set of names under it: a row, the parameters."""

    fields: dict[str, "Entry"]
    origins: dict[str, Origins] = field(default_factory=dict)


@dataclass(eq=False)
class Known:
    """One scope: its names and where each comes from, the scope it falls back
    on, its own sets, and its names worked out after the formula being checked.

    An ``open`` scope is one whose names cannot be known, because the part of
    the setup it is made from is broken: any name in it goes. The run's own scope
    carries the ``stage`` it stands for.
    """

    names: dict[str, "Entry"]
    parent: "Known | None" = None
    sets: Callable[[str], "Known | None"] | None = None
    later: frozenset[str] = frozenset()
    origins: dict[str, Origins] = field(default_factory=dict)
    open: bool = False
    stage: str | None = None

    def _chain(self) -> Iterator["Known"]:
        scope: Known | None = self
        while scope is not None:
            yield scope
            scope = scope.parent

    def locate(self, name: str) -> "tuple[Entry, Origins] | None":
        """What a name is where it is first found, and where it comes from. Not
        found, it is anything when a scope on the way is open."""
        opened = False
        for scope in self._chain():
            if name in scope.names:
                return scope.names[name], scope.origins.get(name, NOWHERE)
            opened = opened or scope.open
        return (ANY, NOWHERE) if opened else None

    def find(self, name: str) -> "Entry | None":
        found = self.locate(name)
        return None if found is None else found[0]

    def is_later(self, name: str) -> bool:
        return any(name in s.later for s in self._chain())

    def collection(self, name: str) -> "Known | None":
        for scope in self._chain():
            found = scope.sets(name) if scope.sets is not None else None
            if found is not None:
                return found
        return None

    def stage_of(self) -> str | None:
        return next((s.stage for s in self._chain() if s.stage is not None), None)


Entry = Kind | Record | Known | Open


@dataclass
class Names:
    """Names being gathered, each with where it comes from."""

    names: dict[str, Entry] = field(default_factory=dict)
    origins: dict[str, Origins] = field(default_factory=dict)
    open: bool = False

    def add(self, name: str, entry: Entry, origin: Origins = NOWHERE) -> None:
        self.names[name] = entry
        self.origins[name] = origin

    def merge(self, name: str, entry: Entry, origin: Origins) -> None:
        """A name read from two places (a row set made of two sheets)."""
        self.names[name] = _union(self.names.get(name), entry)
        self.origins[name] = self.origins.get(name, NOWHERE) | origin

    def merge_all(self, other: "Names") -> None:
        for name, entry in other.names.items():
            self.merge(name, entry, other.origins.get(name, NOWHERE))
        self.open = self.open or other.open

    def copy(self) -> "Names":
        return Names(dict(self.names), dict(self.origins), self.open)

    def scope(
        self,
        parent: "Known | None",
        later: frozenset[str] = frozenset(),
        stage: str | None = None,
    ) -> Known:
        return Known(
            dict(self.names),
            parent=parent,
            later=later,
            origins=dict(self.origins),
            open=self.open,
            stage=stage,
        )

    def record(self) -> Entry:
        if self.open:
            return OPEN
        return Record(dict(self.names), dict(self.origins))


def _union(a: "Entry | None", b: Entry) -> Entry:
    if a is None:
        return b
    if isinstance(a, frozenset) and isinstance(b, frozenset):
        return a | b
    return a if a is b else OPEN


def _step(entry: Entry | None, part: str) -> tuple[Entry | None, Origins]:
    """The name under a value, and where it comes from."""
    if isinstance(entry, Record):
        return entry.fields.get(part), entry.origins.get(part, NOWHERE)
    if isinstance(entry, Known):
        found = entry.locate(part)
        return (None, NOWHERE) if found is None else found
    return (None if entry is None else ANY), NOWHERE


def _resolve(path: Sequence[str], scope: Known) -> Entry | None:
    found = scope.locate(path[0])
    entry = None if found is None else found[0]
    for part in path[1:]:
        if entry is None or isinstance(entry, Open | frozenset):
            return None if entry is None else ANY
        entry = _step(entry, part)[0]
    return entry


def origins_of(ref: Ref, scope: Known) -> list[Origins]:
    """Where each part of a name comes from, as far as it can be told."""
    path = ref.name.split(".")
    found = scope.locate(path[0])
    if found is None:
        return [NOWHERE]
    entry: Entry | None = found[0]
    origins = [found[1]]
    for part in path[1:]:
        if entry is None or isinstance(entry, Open | frozenset):
            break
        entry, origin = _step(entry, part)
        origins.append(origin)
    return origins


# --- The functions, as the check sees them ------------------------------------------


def _per_member(name: str, count: int) -> frozenset[int] | None:
    """For an aggregate, the arguments worked out on each member of the set it
    names; the others are worked out where the formula is. None otherwise."""
    if name in _ONE_VALUE:
        return frozenset({1})
    if name == "SUMIF":
        return frozenset({1, 3})
    if name == "SUMIFS":
        return frozenset({1, *range(2, count, 2)})
    if name == "COUNTIFS":
        return frozenset(range(1, count, 2))
    return None


_ONE_VALUE = frozenset(
    {
        "SUMX",
        "COUNTX",
        "MINX",
        "MAXX",
        "ONLYX",
        "FIRSTX",
        "LASTX",
        "COALESCEX",
        "DISTINCTX",
        "JOINX",
        "COUNTIF",
    }
)


def _summed(name: str, count: int) -> int | None:
    """The argument an aggregate adds up, which has to be a number."""
    if name in ("SUMX", "SUMIFS"):
        return 1
    if name == "SUMIF":
        return 3 if count > 3 else 1
    return None


def _criteria(name: str, count: int) -> list[tuple[int, int]]:
    """For the SUMIF family, each criteria argument and the value it is met by."""
    if name in ("SUMIF", "COUNTIF"):
        return [(2, 1)] if count > 2 else []
    if name == "SUMIFS":
        return [(i, i - 1) for i in range(3, count, 2)]
    if name == "COUNTIFS":
        return [(i, i - 1) for i in range(2, count, 2)]
    return []


# Functions whose first argument names a lookup table (Model.lookups).
_TABLES = frozenset({"LOOKUP", "PREFIXCOUNT", "PREFIXKEYS", "MATCHPREFIX"})

# The arguments that have to be numbers; None for every one.
_NUMBERS: dict[str, frozenset[int] | None] = {
    "ABS": frozenset({0}),
    "ROUND": frozenset({0, 1}),
    "MONEY": frozenset({0}),
    "PLAIN": frozenset({0}),
    "PERCENT": frozenset({0}),
    "SUM": None,
    "MIN": None,
    "MAX": None,
}

# Functions taking pairs after their first few arguments.
_PAIRS: dict[str, tuple[int, str]] = {
    "IFS": (0, "IFS takes condition, value pairs"),
    "SUMIFS": (2, "SUMIFS takes value, criteria pairs"),
    "COUNTIFS": (1, "COUNTIFS takes value, criteria pairs"),
}

_RESULT: dict[str, Kind] = {
    **dict.fromkeys(("AND", "OR", "NOT", "ISBLANK", "EXACT", "IN", "CONTAINS"), BOOL),
    **dict.fromkeys(("ISNUMBER", "ISTEXT", "ISDATE"), BOOL),
    "BLANK": BLANK,
    **dict.fromkeys(("ABS", "ROUND", "MONEY", "MIN", "MAX", "INTEGER"), NUMBER | BLANK),
    **dict.fromkeys(
        ("SUM", "SUMX", "COUNTX", "DISTINCTX", "LEN", "PREFIXCOUNT"), NUMBER
    ),
    **dict.fromkeys(("SUMIF", "SUMIFS", "COUNTIF", "COUNTIFS"), NUMBER),
    **dict.fromkeys(
        (
            "LOWER",
            "UPPER",
            "TRIM",
            "PLAIN",
            "PERCENT",
            "REGEX",
            "TEXTBEFORE",
            "TEXTAFTER",
            "SUBSTITUTE",
            "JOINX",
            "LABEL",
            "MATCHPREFIX",
            "CLEAN",
        ),
        TEXT | BLANK,
    ),
    **dict.fromkeys(("TEXT", "QUOTE", "PREFIXKEYS", "IDTEXT"), TEXT),
    "DATEVALUE": DATE | BLANK,
}

# The aggregates whose result is one of the values they look at.
_PICKS = frozenset({"MINX", "MAXX", "ONLYX", "FIRSTX", "LASTX", "COALESCEX"})

_ARITHMETIC = frozenset({"+", "-", "*", "/"})
_ORDER = frozenset({"<", "<=", ">", ">="})


def _words(kind: Kind) -> str:
    return " or ".join(_WORDS[k] for k in sorted(kind) if k in _WORDS)


def _not_a_number(kind: Kind) -> Kind | None:
    """What a value can only be, when that is never a number (blank aside)."""
    if "any" in kind or "number" in kind:
        return None
    return (kind - BLANK) or None


def _comparable(a: Kind, b: Kind) -> bool:
    left, right = a - BLANK, b - BLANK
    if not left or not right or "any" in left or "any" in right:
        return True
    numbers = {"number", "bool"}
    return any((x in numbers and y in numbers) or x == y for x in left for y in right)


def _describe(node: Node) -> str:
    if isinstance(node, Ref):
        return node.name
    if isinstance(node, Literal):
        if isinstance(node.value, bool):
            return "TRUE" if node.value else "FALSE"
        if isinstance(node.value, str):
            return f'"{node.value}"'
        return "a blank" if node.value is None else str(node.value)
    if isinstance(node, Call):
        return f"{node.name}(…)"
    if isinstance(node, Unary):
        return f"{node.op}{_describe(node.operand)}"
    return f"{_describe(node.left)} {node.op} {_describe(node.right)}"


def _unknown(name: str, scope: Known) -> str:
    """A name nothing here is called, with the closest one that is, if any."""
    seen = {known for s in scope._chain() for known in s.names}
    close = get_close_matches(name, sorted(seen), n=1, cutoff=0.8)
    if close:
        return f"Nothing is called {name!r}. Did you mean {close[0]!r}?"
    return (
        f"Nothing is called {name!r} yet. Check the spelling, or add a column or "
        "field with that name."
    )


def _literal(node: Node) -> str | None:
    if isinstance(node, Literal) and isinstance(node.value, str):
        return node.value
    return None


class _Walk:
    """Checks a formula against the scope it runs in, tells what kind of value a
    part of it gives, and lists the names it reads."""

    def __init__(
        self,
        lookups: dict[str, str],
        exists: Callable[[str], bool],
        ready: Callable[[str, str], bool],
    ) -> None:
        # Each lookup table, and the set it is made from.
        self.lookups = lookups
        # Whether a set exists at all, to tell "not made yet" from "no such set".
        self.exists = exists
        # Whether a set is made by a stage of the run.
        self.ready = ready

    # -- kinds --

    def kind_of(self, formula: str, scope: Known) -> Kind:
        try:
            return self.kind(parse(formula), scope)
        except FormulaError:
            return ANY

    def kind(self, node: Node, scope: Known) -> Kind:
        if isinstance(node, Literal):
            value = node.value
            if value is None:
                return BLANK
            if isinstance(value, bool):
                return BOOL
            return NUMBER if isinstance(value, Decimal) else TEXT
        if isinstance(node, Ref):
            entry = _resolve(node.name.split("."), scope)
            return entry if isinstance(entry, frozenset) else ANY
        if isinstance(node, Unary):
            return NUMBER | BLANK
        if isinstance(node, Binary):
            if node.op == "&":
                return TEXT
            if node.op in ("=", "<>"):
                return BOOL
            return BOOL | BLANK if node.op in _ORDER else NUMBER | BLANK
        return self._call_kind(node, scope)

    def _call_kind(self, node: Call, scope: Known) -> Kind:
        name, args = node.name, node.args
        if name == "IF":
            otherwise = self.kind(args[2], scope) if len(args) > 2 else BLANK
            return self.kind(args[1], scope) | otherwise
        if name == "IFS":
            return frozenset().union(*(self.kind(a, scope) for a in args[1::2])) | BLANK
        if name == "COALESCE":
            return frozenset().union(*(self.kind(a, scope) for a in args)) | BLANK
        if name == "N":
            inner = self.kind(args[0], scope)
            return (inner - BLANK) | NUMBER if "blank" in inner else inner
        if name in _PICKS:
            set_name = _literal(args[0])
            members = scope.collection(set_name) if set_name is not None else None
            return self.kind(args[1], members) | BLANK if members is not None else ANY
        return _RESULT.get(name, ANY)

    # -- the names a formula reads --

    def refs(
        self, node: Node, scope: Known | None
    ) -> Iterator[tuple[Ref, Known | None]]:
        """Every name the formula reads, in the order it is written, with the
        scope it is read in (None where that cannot be told)."""
        if isinstance(node, Ref):
            yield node, scope
        elif isinstance(node, Unary):
            yield from self.refs(node.operand, scope)
        elif isinstance(node, Binary):
            yield from self.refs(node.left, scope)
            yield from self.refs(node.right, scope)
        elif isinstance(node, Call):
            per_member = _per_member(node.name, len(node.args))
            members: Known | None = None
            if per_member is not None:
                set_name = _literal(node.args[0])
                if set_name is not None and scope is not None:
                    members = scope.collection(set_name)
            for i, arg in enumerate(node.args):
                inner = members if per_member is not None and i in per_member else scope
                yield from self.refs(arg, inner)

    # -- problems --

    def check(self, node: Node, scope: Known) -> list[str]:
        found: list[str] = []
        self._check(node, scope, found)
        return found

    def _check(self, node: Node, scope: Known, found: list[str]) -> None:
        if isinstance(node, Ref):
            self._ref(node, scope, found)
        elif isinstance(node, Unary):
            self._check(node.operand, scope, found)
            self._number(f"'{node.op}'", node.operand, scope, found)
        elif isinstance(node, Binary):
            self._check(node.left, scope, found)
            self._check(node.right, scope, found)
            if node.op in _ARITHMETIC:
                self._number(f"'{node.op}'", node.left, scope, found)
                self._number(f"'{node.op}'", node.right, scope, found)
            elif node.op in _ORDER:
                left, right = self.kind(node.left, scope), self.kind(node.right, scope)
                if not _comparable(left, right):
                    found.append(
                        f"{_describe(node)} compares {_words(left - BLANK)} with "
                        f"{_words(right - BLANK)}, which can't be done. Make both "
                        "sides the same kind of value."
                    )
        elif isinstance(node, Call):
            self._call(node, scope, found)

    def _ref(self, node: Ref, scope: Known, found: list[str]) -> None:
        path = node.name.split(".")
        entry = scope.find(path[0])
        if entry is None:
            found.append(
                f"{path[0]!r} isn't ready here: it's worked out later. Use it in "
                "something that comes after it."
                if scope.is_later(path[0])
                else _unknown(path[0], scope)
            )
            return
        for i, part in enumerate(path[1:], start=1):
            if isinstance(entry, Open):
                return
            if isinstance(entry, frozenset):
                definite = entry - BLANK
                if definite and "any" not in definite:
                    found.append(
                        f"{'.'.join(path[:i])} is {_words(definite)}, with no {part!r} in it"
                    )
                return
            inner = _step(entry, part)[0]
            if inner is None:
                found.append(f"{'.'.join(path[:i])} has no {part!r}")
                return
            entry = inner

    def _number(self, where: str, node: Node, scope: Known, found: list[str]) -> None:
        wrong = _not_a_number(self.kind(node, scope))
        if wrong is not None:
            found.append(
                f"{where} needs a number, but {_describe(node)} is {_words(wrong)}"
            )

    def _call(self, node: Call, scope: Known, found: list[str]) -> None:
        name, args = node.name, node.args
        if name in _PAIRS:
            start, message = _PAIRS[name]
            if (len(args) - start) % 2:
                found.append(message)
        per_member = _per_member(name, len(args))
        if per_member is not None:
            set_name = _literal(args[0])
            members: Known | None = None
            if set_name is None:
                self._check(args[0], scope, found)
            else:
                members = scope.collection(set_name)
                if members is None:
                    found.append(
                        f"{set_name!r} is not made yet when this is worked out"
                        if self.exists(set_name)
                        else f"No set called {set_name!r}"
                    )
            for i, arg in enumerate(args[1:], start=1):
                if i not in per_member:
                    self._check(arg, scope, found)
                elif members is not None:
                    self._check(arg, members, found)
            if members is not None:
                summed = _summed(name, len(args))
                if summed is not None and summed < len(args):
                    self._number(name, args[summed], members, found)
                self._criteria(node, members, found)
            return
        if name in _TABLES:
            table = _literal(args[0])
            if table is None:
                self._check(args[0], scope, found)
            elif table not in self.lookups:
                found.append(f"No lookup called {table!r}")
            else:
                self._lookup_ready(table, scope, found)
            for arg in args[1:]:
                self._check(arg, scope, found)
            return
        for arg in args:
            self._check(arg, scope, found)
        if name in _NUMBERS:
            positions = _NUMBERS[name]
            for i, arg in enumerate(args):
                if positions is None or i in positions:
                    self._number(name, arg, scope, found)

    def _lookup_ready(self, table: str, scope: Known, found: list[str]) -> None:
        """A lookup table is made from its set on first use, so the set must be
        made by then."""
        made_from = self.lookups[table]
        stage = scope.stage_of()
        if stage is None or not self.exists(made_from) or self.ready(made_from, stage):
            return
        when = (
            "while the uploads are read: use a row field"
            if stage == "read"
            else "when this is worked out"
        )
        found.append(
            f"Lookup {table!r} is made from {made_from!r}, which is not ready {when}"
        )

    def _criteria(self, node: Call, members: Known, found: list[str]) -> None:
        """A written-out criterion that compares as a number or a date but is
        neither, against values that are one: it would match nothing."""
        for at, value_at in _criteria(node.name, len(node.args)):
            criteria = _literal(node.args[at])
            if criteria is None:
                continue
            op, target = read_criterion(criteria)
            if op not in _ORDER or not isinstance(target, str) or target == "":
                continue
            kind = self.kind(node.args[value_at], members) - BLANK
            if kind and kind <= NUMBER | DATE:
                found.append(
                    f"{node.name}: {criteria!r} is not a number or a date "
                    "(write dates as YYYY-MM-DD)"
                )


# --- The scopes of a setup --------------------------------------------------------

# When a formula is worked out in a run, which decides the names and sets it has.
_STAGES = (
    "read",
    "rows",
    "row_rules",
    "fields",
    "context",
    "decide",
    "metrics",
    "full",
)


def _ordered(
    named: list[Named],
    names: Names,
    prefix: str,
    make: Callable[[Names, frozenset[str]], Known],
    kinds: list[Kind],
    walk: _Walk,
) -> list[Known]:
    """The scope each of an ordered list of formulas is worked out in, every one
    before it known and every one after it later; ``kinds`` fills as it goes, so a
    formula that reaches back into this list sees the ones done so far. Each
    adds its name to ``names``, from ``prefix[k]``."""
    keys = [n.key for n in named]
    scopes: list[Known] = []
    for k, item in enumerate(named):
        scope = make(names.copy(), frozenset(keys[k:]) - names.names.keys())
        scopes.append(scope)
        kind = walk.kind_of(item.formula, scope)
        kinds.append(kind)
        names.add(item.key, kind, frozenset({f"{prefix}[{k}]"}))
    return scopes


@dataclass
class _Cache:
    sheets: dict[int, tuple[list[Known], Names]] = field(default_factory=dict)
    rows: dict[str, Names] = field(default_factory=dict)
    row_fields: dict[tuple[int, int], Known] = field(default_factory=dict)
    fields: list[Kind] | None = None
    context: list[Kind] | None = None
    metrics: list[Kind] | None = None
    groups: dict[str, list[Kind]] = field(default_factory=dict)


class _Scopes:
    def __init__(self, config: CategoryConfig) -> None:
        self.config = config
        self.model: Model | None = config.model
        self.row_sets: dict[str, list[InputSheet]] = {}
        # Each sheet's path in the setup, for the origins of its names.
        self.sheet_paths: dict[int, str] = {}
        for i, upload in enumerate(config.inputs):
            for j, sheet in enumerate(upload.sheets):
                self.row_sets.setdefault(sheet.set or upload.role, []).append(sheet)
                self.sheet_paths[id(sheet)] = f"inputs[{i}].sheets[{j}]"
        model = self.model
        self.set_defs = {s.key: s for s in model.sets} if model else {}
        self.group_index = (
            {g.name: i for i, g in enumerate(model.groups)} if model else {}
        )
        self.walk = _Walk(
            {lk.key: lk.set for lk in model.lookups} if model else {},
            self.exists,
            self._ready,
        )
        self.cache = _Cache()

    # -- sets --

    def base_of(self, name: str) -> str | None:
        """The set a set is made from, following configured sets to the end;
        None for sets made from each other."""
        seen: set[str] = set()
        while name in self.set_defs:
            if name in seen:
                return None
            seen.add(name)
            name = self.set_defs[name].over
        return name

    def exists(self, name: str) -> bool:
        base = self.base_of(name)
        pair = self.model is not None and self.model.builder == "pair"
        return base is not None and (
            base in self.row_sets
            or base in ("entities", "exceptions", *WALLET_LISTS)
            or (pair and base in ("inactive", "duplicates"))
        )

    def broken(self, name: str) -> bool:
        """A configured set made from one that does not exist, or from itself:
        reported where it is configured, so taken as unknown, not as missing."""
        return name in self.set_defs and not self.exists(name)

    def _ready(self, name: str, stage: str) -> bool:
        if not self.exists(name):
            return False
        base = self.base_of(name)
        if base in self.row_sets:
            first = "rows"
        elif base in ("inactive", "duplicates"):
            first = "row_rules"
        elif base == "entities":
            first = "fields"
        else:
            first = "metrics"
        return _STAGES.index(stage) >= _STAGES.index(first)

    def member(self, name: str, root: Known) -> Known | None:
        """One member of a set, as a formula worked out on it sees it."""
        if self.broken(name):
            return Known({}, parent=root, open=True)
        base = self.base_of(name)
        if base in self.row_sets:
            return self.rows(base).scope(root)
        if base == "entities":
            return self.entity(root)
        if base == "exceptions":
            return self.exception(root)
        if base == "inactive":
            return Known(
                {"role": TEXT, "key": TEXT, "row": self._inactive()}, parent=root
            )
        if base == "duplicates":
            return Known({"role": TEXT, "key": TEXT, "copies": NUMBER}, parent=root)
        if base in WALLET_LISTS:
            return Known(dict(WALLET_LISTS[base]), parent=root)
        return None

    # -- the run --

    def root(self, stage: str, upto: int | None = None) -> Known:
        """The run's own scope at a stage; ``upto`` is the context value or the
        figure being worked out."""
        params = {
            p.key: DATE if p.kind == "date" else NUMBER for p in self.config.parameters
        }
        names = Names()
        names.add("param", Record(dict(params)))
        for opaque in ("label", "wallet_check", "lookup"):
            names.add(opaque, OPEN)
        later: set[str] = set()
        model = self.model
        if model is not None:
            context = [n.key for n in model.context]
            metrics = [n.key for n in model.metrics]
            if stage == "context":
                self._add(names, context[:upto], self._context_kinds(), "model.context")
                later |= set(context[upto:])
            elif _STAGES.index(stage) > _STAGES.index("context"):
                self._add(names, context, self._context_kinds(), "model.context")
            if stage == "metrics":
                self._add(names, metrics[:upto], self._metric_kinds(), "model.metrics")
                later |= set(metrics[upto:])
            elif stage == "full":
                self._add(names, metrics, self._metric_kinds(), "model.metrics")
        root = names.scope(
            None, later=frozenset(later - names.names.keys()), stage=stage
        )

        def sets(name: str) -> Known | None:
            if self.broken(name):
                return Known({}, parent=root, open=True)
            return self.member(name, root) if self._ready(name, stage) else None

        root.sets = sets
        return root

    @staticmethod
    def _add(names: Names, keys: list[str], kinds: list[Kind], prefix: str) -> None:
        for k, (key, kind) in enumerate(zip(keys, kinds, strict=False)):
            names.add(key, kind, frozenset({f"{prefix}[{k}]"}))

    def _context_kinds(self) -> list[Kind]:
        if self.cache.context is None:
            assert self.model is not None
            kinds = self.cache.context = []
            for k, named in enumerate(self.model.context):
                kinds.append(self.walk.kind_of(named.formula, self.root("context", k)))
        return self.cache.context

    def _metric_kinds(self) -> list[Kind]:
        if self.cache.metrics is None:
            assert self.model is not None
            kinds = self.cache.metrics = []
            for k, named in enumerate(self.model.metrics):
                kinds.append(self.walk.kind_of(named.formula, self.root("metrics", k)))
        return self.cache.metrics

    # -- rows --

    def sheet_scopes(self, sheet: InputSheet) -> tuple[list[Known], Names]:
        """The scope each of a sheet's fields is worked out in, and the complete
        row they make."""
        cached = self.cache.sheets.get(id(sheet))
        if cached is not None:
            return cached
        here = self.sheet_paths.get(id(sheet), "")
        names = Names()
        for c, column in enumerate(sheet.columns):
            names.add(
                column.field, _COLUMN[column.type], frozenset({f"{here}.columns[{c}]"})
            )
        names.add("sheet", TEXT)
        names.add("row", NUMBER)
        read = self.root("read")
        scopes = _ordered(
            sheet.fields,
            names,
            f"{here}.fields",
            lambda known, later: known.scope(read, later=later),
            [],
            self.walk,
        )
        self.cache.sheets[id(sheet)] = (scopes, names)
        return scopes, names

    def rows(self, set_name: str) -> Names:
        """A row of the set once its row fields are worked out: its sheets'
        columns and fields, and the run's own row fields for the set."""
        cached = self.cache.rows.get(set_name)
        if cached is not None:
            return cached
        names = Names()
        # Cached as it fills, so a row field reaching back into its own set sees
        # the fields done so far.
        self.cache.rows[set_name] = names
        for sheet in self.row_sets.get(set_name, []):
            names.merge_all(self.sheet_scopes(sheet)[1])
        if self.model is not None:
            parent = self.root("rows")
            specs = [
                (i, s) for i, s in enumerate(self.model.row_fields) if s.set == set_name
            ]
            keys = [n.key for _, spec in specs for n in spec.fields]
            done = 0
            for i, spec in specs:
                for k, named in enumerate(spec.fields):
                    scope = names.scope(
                        parent, later=frozenset(keys[done:]) - names.names.keys()
                    )
                    self.cache.row_fields[(i, k)] = scope
                    names.add(
                        named.key,
                        self.walk.kind_of(named.formula, scope),
                        frozenset({f"model.row_fields[{i}].fields[{k}]"}),
                    )
                    done += 1
        return names

    def row_field_scope(self, i: int, k: int) -> Known:
        assert self.model is not None
        set_name = self.model.row_fields[i].set
        if set_name not in self.row_sets:
            # Reported where the row fields name their set.
            return Known({}, parent=self.root("rows"), open=True)
        self.rows(set_name)
        return self.cache.row_fields[(i, k)]

    def _record(self, set_name: str) -> Entry:
        base = self.base_of(set_name)
        return self.rows(base).record() if base in self.row_sets else OPEN

    def _inactive(self) -> Entry:
        assert self.model is not None
        return self._union_rows([side.role for side in self.model.sides]).record()

    def _union_rows(self, sets: list[str]) -> Names:
        """A row of any of the sets. One that cannot be known leaves it open."""
        names = Names()
        for set_name in sets:
            base = self.base_of(set_name)
            if base in self.row_sets:
                names.merge_all(self.rows(base))
            else:
                names.open = True
        return names

    # -- orders --

    def entity(self, root: Known, upto: int | None = None) -> Known:
        """An order: its sides or chosen rows, its fields (those before ``upto``),
        and, made by the group builder, its own rows and groups."""
        model = self.model
        assert model is not None
        names = Names()
        names.add("key", TEXT, KEY_ORIGIN)
        names.add("verdict", TEXT | BLANK)
        names.add("note", ANY)
        if model.builder == "pair":
            for side in model.sides:
                names.add(side.role, self._record(side.role))
                names.add(f"{side.role}_active", BOOL)
                names.add(f"{side.role}_copies", NUMBER)
        else:
            for member in model.members:
                if member.primary:
                    names.add(member.name, self._record(member.set))
        keys = [n.key for n in model.fields]
        count = len(keys) if upto is None else upto
        if count:
            self._add(names, keys[:count], self._field_kinds(), "model.fields")
        entity = names.scope(root, later=frozenset(keys[count:]) - names.names.keys())
        if model.builder == "group":
            entity.sets = self._own(entity)
        return entity

    def _field_kinds(self) -> list[Kind]:
        if self.cache.fields is None:
            assert self.model is not None
            kinds = self.cache.fields = []
            root = self.root("fields")
            for k, named in enumerate(self.model.fields):
                kinds.append(
                    self.walk.kind_of(named.formula, self.entity(root, upto=k))
                )
        return self.cache.fields

    def _collections(self) -> dict[str, list[str]]:
        """An order's own collections (group builder), and the sets each gathers."""
        assert self.model is not None
        found: dict[str, list[str]] = {}
        for member in self.model.members:
            name = member.repeats if member.primary else member.name
            if name is not None:
                found.setdefault(name, []).append(member.set)
        return found

    def _own(self, entity: Known) -> Callable[[str], "Known | None"]:
        assert self.model is not None
        collections = self._collections()
        groups = {g.name: g for g in self.model.groups}

        def own(name: str) -> Known | None:
            if name in collections:
                return self._union_rows(collections[name]).scope(entity)
            if name in groups:
                return self.group(groups[name], entity)
            return None

        return own

    def group(self, spec: Group, entity: Known, upto: int | None = None) -> Known:
        """One group of an order's rows: its key, its fields (those before
        ``upto``) and its own rows of ``of``."""
        names = Names()
        names.add("key", TEXT)
        keys = [n.key for n in spec.fields]
        count = len(keys) if upto is None else upto
        if count:
            prefix = f"model.groups[{self.group_index.get(spec.name, 0)}].fields"
            self._add(names, keys[:count], self._group_kinds(spec), prefix)
        scope = names.scope(entity, later=frozenset(keys[count:]) - names.names.keys())
        collections = self._collections()
        # A group of a collection the order does not have is reported where it is
        # configured; its rows are left open.
        gathered = (
            self._union_rows(collections[spec.of])
            if spec.of in collections
            else Names(open=True)
        )
        scope.sets = lambda name: gathered.scope(scope) if name == spec.of else None
        return scope

    def _group_kinds(self, spec: Group) -> list[Kind]:
        cached = self.cache.groups.get(spec.name)
        if cached is None:
            cached = self.cache.groups[spec.name] = []
            entity = self.entity(self.root("full"))
            for k, named in enumerate(spec.fields):
                scope = self.group(spec, entity, upto=k)
                cached.append(self.walk.kind_of(named.formula, scope))
        return cached

    def exception(self, root: Known) -> Known:
        order: Entry = self.entity(root) if self.model is not None else OPEN
        return Known(
            {
                "code": TEXT,
                "severity": TEXT,
                "order_no": TEXT | BLANK,
                "amount": NUMBER | BLANK,
                "detail": TEXT,
                "order": order,
            },
            parent=root,
        )


# --- Every formula, in the scope it runs in -----------------------------------------

# A formula's path in the setup, the formula, and every scope it is worked out in
# (None for one that cannot be had: its set is reported where it is named).
Place = tuple[str, str, tuple[Known | None, ...]]
Places = Iterator[Place]


class _Places:
    def __init__(self, scopes: _Scopes) -> None:
        self.config = scopes.config
        self.scopes = scopes

    def at(self, path: str, formula: str | None, *where: Known | None) -> Places:
        if formula is not None:
            yield path, formula, where

    def raised(self, path: str, spec: Raise, *where: Known | None) -> Places:
        yield from self.at(f"{path}.detail", spec.detail, *where)
        yield from self.at(f"{path}.amount", spec.amount, *where)
        yield from self.at(f"{path}.order_no", spec.order_no, *where)

    def all(self) -> Places:
        yield from self.inputs()
        if self.config.model is not None:
            yield from self.model(self.config.model)
            yield from self.output()

    def inputs(self) -> Places:
        for i, upload in enumerate(self.config.inputs):
            for j, sheet in enumerate(upload.sheets):
                here = f"inputs[{i}].sheets[{j}]"
                field_scopes, names = self.scopes.sheet_scopes(sheet)
                for k, named in enumerate(sheet.fields):
                    yield from self.at(
                        f"{here}.fields[{k}].formula", named.formula, field_scopes[k]
                    )
                row = names.scope(self.scopes.root("read"))
                yield from self.at(f"{here}.filter", sheet.filter, row)
                yield from self.at(f"{here}.counted", sheet.counted, row)
                if sheet.unique is not None:
                    for n, key in enumerate(sheet.unique.keys):
                        yield from self.at(f"{here}.unique.keys[{n}]", key, row)
                    yield from self.at(f"{here}.unique.when", sheet.unique.when, row)
                    if sheet.unique.exception is not None:
                        yield from self.raised(
                            f"{here}.unique.exception", sheet.unique.exception, row
                        )
                for n, reject in enumerate(sheet.rejects):
                    yield from self.at(f"{here}.rejects[{n}].when", reject.when, row)
                    yield from self.at(
                        f"{here}.rejects[{n}].message", reject.message, row
                    )

    def model(self, model: Model) -> Places:
        scopes, at, raised = self.scopes, self.at, self.raised
        rows = scopes.root("rows")
        for i, spec in enumerate(model.row_fields):
            for k, named in enumerate(spec.fields):
                yield from at(
                    f"model.row_fields[{i}].fields[{k}].formula",
                    named.formula,
                    scopes.row_field_scope(i, k),
                )
        for i, side in enumerate(model.sides):
            yield from at(
                f"model.sides[{i}].active", side.active, scopes.member(side.role, rows)
            )
        for i, member in enumerate(model.members):
            item = scopes.member(member.set, rows)
            yield from at(f"model.members[{i}].key", member.key, item)
            for n, formula in enumerate(member.primary):
                yield from at(f"model.members[{i}].primary[{n}]", formula, item)

        full = scopes.root("full")
        whole = scopes.entity(full)
        for i, group in enumerate(model.groups):
            yield from at(
                f"model.groups[{i}].key", group.key, whole.collection(group.of)
            )
            for k, named in enumerate(group.fields):
                yield from at(
                    f"model.groups[{i}].fields[{k}].formula",
                    named.formula,
                    scopes.group(group, whole, upto=k),
                )
        for i, lookup in enumerate(model.lookups):
            item = scopes.member(lookup.set, full)
            yield from at(f"model.lookups[{i}].by", lookup.by, item)
            yield from at(f"model.lookups[{i}].value", lookup.value, item)
            yield from at(
                f"model.lookups[{i}].then",
                lookup.then,
                Known({"value": ANY}, parent=full),
            )
        row_rules = scopes.root("row_rules")
        for i, rule in enumerate(model.row_rules):
            item = scopes.member(rule.over, row_rules)
            yield from at(f"model.row_rules[{i}].when", rule.when, item)
            yield from raised(f"model.row_rules[{i}]", rule, item)

        fields = scopes.root("fields")
        for k, named in enumerate(model.fields):
            yield from at(
                f"model.fields[{k}].formula",
                named.formula,
                scopes.entity(fields, upto=k),
            )
        for k, named in enumerate(model.context):
            yield from at(
                f"model.context[{k}].formula", named.formula, scopes.root("context", k)
            )
        decide = scopes.root("decide")
        for i, stop in enumerate(model.stops):
            yield from at(f"model.stops[{i}].when", stop.when, decide)
            yield from at(f"model.stops[{i}].message", stop.message, decide)
        order = scopes.entity(decide)
        for i, status in enumerate(model.rules):
            yield from at(f"model.rules[{i}].when", status.when, order)
            yield from at(f"model.rules[{i}].note", status.note, order)
            if status.exception is not None:
                yield from raised(
                    f"model.rules[{i}].exception", status.exception, order
                )
        for i, each_check in enumerate(model.checks):
            yield from at(f"model.checks[{i}].when", each_check.when, order)
            yield from raised(f"model.checks[{i}]", each_check, order)
        for i, finding in enumerate(model.findings):
            item = order.collection(finding.each) if finding.each is not None else order
            yield from at(f"model.findings[{i}].when", finding.when, item)
            yield from raised(f"model.findings[{i}]", finding, item)
        if model.wallet is not None:
            wallet = model.wallet
            yield from at("model.wallet.tolerance", wallet.tolerance, decide)
            sides = [scopes.member(wallet.rows, decide)]
            if wallet.excluded is not None:
                sides.append(scopes.member(wallet.excluded, decide))
            for name in (
                "vertical",
                "sg_order_id",
                "reference",
                "order_date",
                "amount",
            ):
                yield from at(f"model.wallet.{name}", getattr(wallet, name), *sides)
        for n, formula in enumerate(model.sort):
            yield from at(f"model.sort[{n}]", formula, order)
        for i, run_rule in enumerate(model.run_rules):
            yield from at(f"model.run_rules[{i}].when", run_rule.when, decide)
            yield from raised(f"model.run_rules[{i}]", run_rule, decide)
        for i, set_def in enumerate(model.sets):
            yield from at(
                f"model.sets[{i}].where",
                set_def.where,
                scopes.member(set_def.over, full),
            )
        for k, named in enumerate(model.metrics):
            yield from at(
                f"model.metrics[{k}].formula", named.formula, scopes.root("metrics", k)
            )
        for name in type(model.history).model_fields:
            yield from at(f"model.history.{name}", getattr(model.history, name), full)

    def output(self) -> Places:
        output, scopes = self.config.output, self.scopes
        full = scopes.root("full")
        for k, named in enumerate(output.texts):
            yield from self.at(f"output.texts[{k}].formula", named.formula, full)
        for t, tab in enumerate(output.tabs):
            here = f"output.tabs[{t}]"
            if tab.kind == "table":
                for key, formula in tab.tolerances.items():
                    yield from self.at(f"{here}.tolerances.{key}", formula, full)
                yield from self.table(here, tab, full)
            if tab.kind == "dashboard" and tab.breakdown is not None:
                yield from self.breakdown(f"{here}.breakdown", tab.breakdown, full)

    def table(self, here: str, tab: OutputTab, full: Known) -> Places:
        """A table's columns, on each row of every rows section that uses them;
        a section with its own values uses those, and its own styles, instead.
        A block's worked-out cells are worked out on the run."""
        uses: list[list[Known | None]] = [[] for _ in tab.columns]
        if tab.copies:
            # A copy's rows are its upload's: any of its headers is a name, the
            # run's own names are still checked (and renamed), the rest let be.
            row = Known({}, parent=full, open=True)
            uses = [[row] for _ in tab.columns]
        for s, section in enumerate(tab.sections):
            at = f"{here}.sections[{s}]"
            for r, block_row in enumerate(section.rows):
                for k, cell in enumerate(block_row.cells):
                    yield from self.at(
                        f"{at}.rows[{r}].cells[{k}].value", cell.value, full
                    )
            if section.kind != "rows":
                continue
            item: Known | None
            if section.set is not None:
                item = full.collection(section.set)
            elif section.upload is not None:
                # Read from an upload, any of its headers is a name: the run's
                # own names are still checked (and renamed), the rest let be.
                item = Known({}, parent=full, open=True)
            else:
                continue
            if section.contacts is not None:
                yield from self.at(f"{at}.contacts.match", section.contacts.match, item)
                yield from self.at(f"{at}.contacts.row", section.contacts.row, item)
                if item is not None:
                    item = Known({"contact": TEXT | BLANK}, parent=item)
            yield from self.at(f"{at}.where", section.where, item)
            for key, style in section.styles.items():
                yield from self.at(f"{at}.styles.{key}", style, item)
            if section.values:
                for column in tab.columns:
                    yield from self.at(
                        f"{here}.sections[{s}].values.{column.key}",
                        section.values.get(column.key),
                        item,
                    )
            else:
                for column_uses in uses:
                    column_uses.append(item)
            if section.lookups:
                for c, column in enumerate(tab.lookup_columns):
                    if column.lookup is not None and column.lookup.key_value:
                        yield from self.at(
                            f"{here}.lookup_columns[{c}].lookup.key_value",
                            column.lookup.key_value,
                            item,
                        )
        for c, column in enumerate(tab.columns):
            # An Excel formula is no formula of ours: the check reads its links.
            if column.excel is None and column.value is not None:
                yield from self.at(f"{here}.columns[{c}].value", column.value, *uses[c])
            elif column.excel is None:
                # A column with no formula reads the name its key is.
                yield from self.at(f"{here}.columns[{c}].key", column.key, *uses[c])
            if column.style is not None:
                yield from self.at(f"{here}.columns[{c}].style", column.style, *uses[c])

    def breakdown(self, here: str, spec: Breakdown, full: Known) -> Places:
        member = full.collection(spec.set)
        if member is None:
            return
        part = member.collection(spec.parts) if spec.parts is not None else member
        yield from self.at(f"{here}.name", spec.name, part)
        yield from self.at(f"{here}.basis", spec.basis, part)
        for k, total in enumerate(spec.totals):
            yield from self.at(f"{here}.totals[{k}].formula", total.formula, part)
        item = Known(
            {"share": NUMBER, **{t.key: NUMBER for t in spec.totals}}, parent=member
        )
        for c, column in enumerate(spec.columns):
            yield from self.at(f"{here}.columns[{c}].when", column.when, item)
            yield from self.at(f"{here}.columns[{c}].value", column.value, item)


def formula_places(config: CategoryConfig) -> tuple[_Walk, list[Place]]:
    """Every formula of the setup that a run works out, with the scopes it is
    worked out in, and the walk that reads them."""
    scopes = _Scopes(config)
    return scopes.walk, list(_Places(scopes).all())


def name_problems(config: CategoryConfig) -> list[tuple[str, str]]:
    """Every name a formula reads that is not there when it runs, and every value
    its operator or function cannot take, as (path, message), each once."""
    walk, places = formula_places(config)
    found: list[tuple[str, str]] = []
    for path, formula, where in places:
        try:
            node = parse(formula)
        except FormulaError:
            continue  # reported as it is parsed
        for scope in where:
            if scope is not None:
                found += [(path, message) for message in walk.check(node, scope)]
    return list(dict.fromkeys(found))


def non_numbers(config: CategoryConfig) -> dict[str, str]:
    """Each figure that is never a number, and what it is instead, in words."""
    if config.model is None:
        return {}
    scopes = _Scopes(config)
    found: dict[str, str] = {}
    for named, kind in zip(config.model.metrics, scopes._metric_kinds(), strict=True):
        other = _not_a_number(kind)
        if other is not None:
            found[named.key] = _words(other)
    return found
