"""Evaluating formulas, and the functions they may call.

Values are ``Decimal``, ``str``, ``bool``, ``date`` or blank (``None``).

* Arithmetic with a blank is blank: ``portal.total_amount - vendor.total`` is
  blank when there is no vendor row. ``N(x)`` turns a blank into 0.
* ``=`` and ``<>`` compare text without regard to case, as Excel does;
  ``EXACT`` compares it exactly.
* ``&`` joins text; a blank joins as nothing.
* ``IF``, ``IFS``, ``AND``, ``OR`` and the aggregates evaluate only what they
  need, so ``IF(n = 0, 0, total / n)`` never divides by zero.

The aggregates (``SUMX``, ``COUNTX``, ``MINX``, ``MAXX``, ``ONLYX``,
``FIRSTX``, ``COALESCEX``, ``DISTINCTX``, ``JOINX``, and Excel's ``SUMIF``,
``SUMIFS``, ``COUNTIF``, ``COUNTIFS``) run a formula over each member of a named
set — the settled orders, the portal rows, an order's own claim lines — which
the scope supplies.

A formula is compiled once into Python closures and the compiled form is
cached, so a column formula costs a few calls per row, however many rows.
"""

import operator
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any, Protocol

from app.expr.parser import Binary, FormulaError, Literal, Node, Ref, Unary, parse
from app.recon.cleaning import clean_text


class Scope(Protocol):
    def lookup(self, path: tuple[str, ...]) -> Any:
        """The value of a dotted name, given as its parts."""
        ...

    def collection(self, name: str) -> Sequence["Scope"]: ...


# A scope's own sets: the members of a set by name, or None for a set it does
# not have, which is then looked for in its parent.
Collections = Callable[[str], "Sequence[Scope] | None"]


class Values:
    """A scope over plain values, with a parent to fall back on.

    A dotted name walks into mappings and scopes: ``portal.total_amount`` reads
    ``total_amount`` of whatever ``portal`` is, and is blank when ``portal`` is.
    """

    __slots__ = ("collections", "parent", "values")

    def __init__(
        self,
        values: Mapping[str, Any],
        parent: Scope | None = None,
        collections: Collections | None = None,
    ) -> None:
        self.values = values
        self.parent = parent
        self.collections = collections

    def get(self, name: str) -> Any:
        return self.lookup(tuple(name.split(".")))

    def lookup(self, path: tuple[str, ...]) -> Any:
        head = path[0]
        if head in self.values:
            value = self.values[head]
        elif self.parent is not None:
            return self.parent.lookup(path)
        else:
            raise FormulaError(f"Unknown name {'.'.join(path)!r}")
        for part in path[1:]:
            if value is None:
                return None
            value = _member(value, part, path)
        return value

    def collection(self, name: str) -> Sequence[Scope]:
        if self.collections is not None:
            found = self.collections(name)
            if found is not None:
                return found
        if self.parent is not None:
            return self.parent.collection(name)
        raise FormulaError(f"Unknown set {name!r}")


def _member(value: Any, part: str, path: tuple[str, ...]) -> Any:
    # dict first: rows and exceptions are dicts, and the check is cheap.
    if isinstance(value, dict | Mapping):
        if part not in value:
            raise FormulaError(f"Unknown name {'.'.join(path)!r}")
        return value[part]
    if isinstance(value, Values):
        return value.lookup((part,))
    raise FormulaError(
        f"{'.'.join(path)!r}: a {type(value).__name__} has no {part!r} in it"
    )


Compiled = Callable[["Scope"], Any]


def evaluate(formula: str | Node, scope: Scope) -> Any:
    run = compiled(formula) if isinstance(formula, str) else _compile(formula)
    return run(scope)


@lru_cache(maxsize=4096)
def compiled(formula: str) -> Compiled:
    """A formula as a function of its scope."""
    return _compile(parse(formula))


def truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    return bool(value)


def text(value: Any) -> str:
    """A value as it joins into text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _number(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise FormulaError(f"{where} needs a number, not {value!r}")
    return Decimal(value)


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    if isinstance(a, (Decimal, int)) and isinstance(b, (Decimal, int)):
        if isinstance(a, bool) or isinstance(b, bool):
            return a is b
        return Decimal(a) == Decimal(b)
    return bool(a == b)


def _compile(node: Node) -> Compiled:
    if isinstance(node, Literal):
        value = node.value
        return lambda scope: value
    if isinstance(node, Ref):
        path = tuple(node.name.split("."))
        return lambda scope: scope.lookup(path)
    if isinstance(node, Unary):
        return _unary(node.op, _compile(node.operand))
    if isinstance(node, Binary):
        return _binary(node.op, _compile(node.left), _compile(node.right))
    args = tuple(_compile(arg) for arg in node.args)
    lazy = LAZY.get(node.name)
    if lazy is not None:
        return lambda scope: lazy(args, scope)
    function = EAGER[node.name]
    return lambda scope: function(*(arg(scope) for arg in args))


def _unary(op: str, operand: Compiled) -> Compiled:
    def run(scope: Scope) -> Any:
        value = operand(scope)
        if value is None:
            return None
        number = _number(value, "A sign")
        return -number if op == "-" else number

    return run


_ORDER: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
_ARITHMETIC: dict[str, Callable[[Decimal, Decimal], Decimal]] = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
}


def _binary(op: str, left: Compiled, right: Compiled) -> Compiled:
    if op == "&":
        return lambda scope: text(left(scope)) + text(right(scope))
    if op == "=":
        return lambda scope: _equal(left(scope), right(scope))
    if op == "<>":
        return lambda scope: not _equal(left(scope), right(scope))
    if op in _ORDER:
        compare = _ORDER[op]

        def comparison(scope: Scope) -> bool | None:
            a, b = left(scope), right(scope)
            if a is None or b is None:
                return None
            if isinstance(a, (Decimal, int)) and isinstance(b, (Decimal, int)):
                a, b = Decimal(a), Decimal(b)
            elif isinstance(a, str) and isinstance(b, str):
                a, b = a.casefold(), b.casefold()
            try:
                return compare(a, b)
            except TypeError:
                raise FormulaError(f"Cannot compare {a!r} with {b!r}") from None

        return comparison
    where = f"'{op}'"
    combine = _ARITHMETIC.get(op)

    def arithmetic(scope: Scope) -> Decimal | None:
        a, b = left(scope), right(scope)
        if a is None or b is None:
            return None
        x, y = _number(a, where), _number(b, where)
        if combine is not None:
            return combine(x, y)
        if y == 0:
            raise FormulaError("Division by zero")
        return x / y

    return arithmetic


# --- Lazy functions: they decide what to evaluate -------------------------------


def _if(args: Sequence[Compiled], scope: Scope) -> Any:
    if truthy(args[0](scope)):
        return args[1](scope)
    return args[2](scope) if len(args) > 2 else None


def _ifs(args: Sequence[Compiled], scope: Scope) -> Any:
    if len(args) % 2:
        raise FormulaError("IFS takes condition, value pairs")
    for condition, value in zip(args[::2], args[1::2], strict=True):
        if truthy(condition(scope)):
            return value(scope)
    return None


def _and(args: Sequence[Compiled], scope: Scope) -> bool:
    return all(truthy(arg(scope)) for arg in args)


def _or(args: Sequence[Compiled], scope: Scope) -> bool:
    return any(truthy(arg(scope)) for arg in args)


def _members(args: Sequence[Compiled], scope: Scope) -> Sequence[Scope]:
    name = args[0](scope)
    if not isinstance(name, str):
        raise FormulaError("An aggregate's first argument names a set")
    return scope.collection(name)


def _values(args: Sequence[Compiled], scope: Scope) -> list[Any]:
    formula = args[1]
    return [
        value
        for member in _members(args, scope)
        if (value := formula(member)) is not None
    ]


def _sumx(args: Sequence[Compiled], scope: Scope) -> Decimal:
    return sum((_number(v, "SUMX") for v in _values(args, scope)), Decimal(0))


def _countx(args: Sequence[Compiled], scope: Scope) -> Decimal:
    members = _members(args, scope)
    if len(args) == 1:
        return Decimal(len(members))
    condition = args[1]
    return Decimal(sum(1 for member in members if truthy(condition(member))))


def _minx(args: Sequence[Compiled], scope: Scope) -> Any:
    values = _values(args, scope)
    return min(values) if values else None


def _maxx(args: Sequence[Compiled], scope: Scope) -> Any:
    values = _values(args, scope)
    return max(values) if values else None


def _onlyx(args: Sequence[Compiled], scope: Scope) -> Any:
    """The one distinct value across the set, or blank when there are several."""
    distinct = set(_values(args, scope))
    return distinct.pop() if len(distinct) == 1 else None


def _firstx(args: Sequence[Compiled], scope: Scope) -> Any:
    """The value on the set's first member, blank or not."""
    members = _members(args, scope)
    return args[1](members[0]) if members else None


def _lastx(args: Sequence[Compiled], scope: Scope) -> Any:
    """The value on the set's last member, blank or not."""
    members = _members(args, scope)
    return args[1](members[-1]) if members else None


def _coalescex(args: Sequence[Compiled], scope: Scope) -> Any:
    """The first value across the set that is not blank."""
    formula = args[1]
    for member in _members(args, scope):
        value = formula(member)
        if value is not None and value != "":
            return value
    return None


def _distinctx(args: Sequence[Compiled], scope: Scope) -> Decimal:
    """How many different values the set holds, blanks aside."""
    return Decimal(len({v for v in _values(args, scope) if v != ""}))


def _joinx(args: Sequence[Compiled], scope: Scope) -> str | None:
    """The set's different values in the order met (sorted, when the fourth
    argument holds), empty ones aside, joined by the third argument; blank
    when there are none."""
    seen = [v for v in dict.fromkeys(_values(args, scope)) if truthy(v)]
    if len(args) > 3 and truthy(args[3](scope)):
        seen.sort()
    return text(args[2](scope)).join(text(v) for v in seen) if seen else None


# --- Excel's conditional sums and counts --------------------------------------
#
# SUMIF("settled", status, "Delivered", due) adds up due over the settled rows
# whose status is Delivered. Each value is worked out on the member; a
# criterion once, where the formula is.

_CRITERION = re.compile(r"(<>|<=|>=|=|<|>)?(.*)", re.DOTALL)
# A number in a criterion as people write one: 5, -0.5, .5, 1,000.25, 1E3, 50%.
_NUMBER = re.compile(
    r"[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?%?"
)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _operand(raw: str) -> Decimal | date | str:
    """What a criterion compares with: a number if it reads as one, else a date
    written YYYY-MM-DD, else the text."""
    stripped = raw.strip()
    if _NUMBER.fullmatch(stripped):
        number = Decimal(stripped.rstrip("%").replace(",", ""))
        return number / 100 if stripped.endswith("%") else number
    if _ISO_DATE.fullmatch(stripped):
        try:
            return date.fromisoformat(stripped)
        except ValueError:
            return raw
    return raw


def read_criterion(criteria: str) -> tuple[str, Decimal | date | str]:
    """A criterion's comparison and what it compares with: ``">=1,000"`` is
    (">=", 1000), ``"Food*"`` is ("=", "Food*"), ``""`` is ("=", "")."""
    found = _CRITERION.fullmatch(criteria)
    assert found is not None  # the pattern matches any text
    op, raw = found.group(1) or "=", found.group(2)
    return op, raw if raw == "" else _operand(raw)


def _wildcard(pattern: str) -> re.Pattern[str]:
    """Excel's wildcards: * any run of characters, ? any one, ~ before either
    (or before ~) for the character itself. Case does not matter."""
    parts: list[str] = []
    escaped = False
    for char in pattern:
        if escaped:
            parts.append(re.escape(char))
            escaped = False
        elif char == "~":
            escaped = True
        elif char == "*":
            parts.append(".*")
        elif char == "?":
            parts.append(".")
        else:
            parts.append(re.escape(char))
    if escaped:
        parts.append(re.escape("~"))
    return re.compile("".join(parts), re.IGNORECASE | re.DOTALL)


def _blank(value: Any) -> bool:
    return value is None or value == ""


def _criterion(criteria: Any, where: str) -> Callable[[Any], bool]:
    """Whether a value meets an Excel criterion: "Delivered" (equal, whatever
    the case), "<>Cancelled", ">100", ">=2026-08-01", "Food*", "" (blank) or
    "<>" (not blank). A criterion that is not text means equal to it, so a blank
    one matches blanks."""
    if not isinstance(criteria, str):
        return lambda value: _equal(value, criteria)
    op, target = read_criterion(criteria)
    if op in ("=", "<>"):
        if target == "":
            return _blank if op == "=" else (lambda value: not _blank(value))
        if isinstance(target, str):
            pattern = _wildcard(target)

            def matches(value: Any) -> bool:
                # As in Excel, text never matches a number or a date.
                if isinstance(value, bool):
                    return pattern.fullmatch(text(value)) is not None
                return isinstance(value, str) and pattern.fullmatch(value) is not None
        else:
            written = criteria.lstrip("=<>").strip().casefold()

            def matches(value: Any) -> bool:
                if isinstance(value, str):
                    return value.strip().casefold() == written
                return _equal(value, target)

        return matches if op == "=" else (lambda value: not matches(value))

    compare = _ORDER[op]

    def ordered(value: Any) -> bool:
        # As in Excel, only a value of the criterion's own kind can meet it.
        if isinstance(target, Decimal):
            if isinstance(value, bool) or not isinstance(value, Decimal | int):
                return False
            return compare(Decimal(value), target)
        if isinstance(target, date):
            return isinstance(value, date) and compare(value, target)
        if isinstance(value, str):
            return compare(value.casefold(), target.casefold())
        if value is None or isinstance(value, bool):
            return False
        # A number or a date against text: the criterion was surely meant as
        # one of those, so saying so beats quietly matching nothing.
        raise FormulaError(
            f"{where}: {criteria!r} is not a number or a date "
            "(write dates as YYYY-MM-DD)"
        )

    return ordered


Tests = list[tuple[Compiled, Callable[[Any], bool]]]


def _pairs(args: Sequence[Compiled], scope: Scope, start: int, name: str) -> Tests:
    """The value, criteria pairs from ``start`` on, each criterion read once."""
    rest = args[start:]
    if len(rest) % 2:
        raise FormulaError(f"{name} takes value, criteria pairs")
    return [
        (value, _criterion(criteria(scope), name))
        for value, criteria in zip(rest[::2], rest[1::2], strict=True)
    ]


def _meeting(args: Sequence[Compiled], scope: Scope, tests: Tests) -> list[Scope]:
    return [
        member
        for member in _members(args, scope)
        if all(test(value(member)) for value, test in tests)
    ]


def _total(members: Sequence[Scope], amount: Compiled, where: str) -> Decimal:
    values = (amount(member) for member in members)
    return sum((_number(v, where) for v in values if v is not None), Decimal(0))


def _sumif(args: Sequence[Compiled], scope: Scope) -> Decimal:
    """SUMIF("set", value, criteria, amount): the amount added up over the
    members whose value meets the criteria; the value itself without one."""
    tests: Tests = [(args[1], _criterion(args[2](scope), "SUMIF"))]
    amount = args[3] if len(args) > 3 else args[1]
    return _total(_meeting(args, scope, tests), amount, "SUMIF")


def _sumifs(args: Sequence[Compiled], scope: Scope) -> Decimal:
    """SUMIFS("set", amount, value, criteria, …): the amount added up over the
    members that meet every criteria."""
    tests = _pairs(args, scope, 2, "SUMIFS")
    return _total(_meeting(args, scope, tests), args[1], "SUMIFS")


def _countif(args: Sequence[Compiled], scope: Scope) -> Decimal:
    """COUNTIF("set", value, criteria): how many members' value meets it."""
    tests: Tests = [(args[1], _criterion(args[2](scope), "COUNTIF"))]
    return Decimal(len(_meeting(args, scope, tests)))


def _countifs(args: Sequence[Compiled], scope: Scope) -> Decimal:
    """COUNTIFS("set", value, criteria, …): how many members meet every one."""
    return Decimal(len(_meeting(args, scope, _pairs(args, scope, 1, "COUNTIFS"))))


class Index(Protocol):
    def index(self, name: str) -> Mapping[str, Any]: ...


def _index(scope: Scope, name: Any) -> Mapping[str, Any]:
    """A run's lookup table by name (``Model.lookups``)."""
    tables: Index | None = scope.lookup(("lookup",))
    if tables is None:
        raise FormulaError("This scope has no lookups")
    return tables.index(text(name))


def _lookup(args: Sequence[Compiled], scope: Scope) -> Any:
    """LOOKUP("promo", sku): the value the table holds for the key, or blank."""
    key = args[1](scope)
    if key is None:
        return None
    return _index(scope, args[0](scope)).get(text(key))


def _prefixed(args: Sequence[Compiled], scope: Scope) -> list[str]:
    """The table's keys the text starts, in the table's order."""
    prefix = args[1](scope)
    if prefix is None:
        return []
    start = text(prefix)
    return [key for key in _index(scope, args[0](scope)) if key.startswith(start)]


def _prefixcount(args: Sequence[Compiled], scope: Scope) -> Decimal:
    """How many of the table's keys start with the text."""
    return Decimal(len(_prefixed(args, scope)))


def _prefixkeys(args: Sequence[Compiled], scope: Scope) -> str:
    """The table's keys that start with the text, sorted, joined by ", "."""
    return ", ".join(sorted(_prefixed(args, scope)))


def _matchprefix(args: Sequence[Compiled], scope: Scope) -> str | None:
    """MATCHPREFIX(table, text, amount, tolerance): the one key the text
    starts; when it starts several, the one whose value is within the
    tolerance of the amount. Blank when that does not single one out: a
    reference is never guessed."""
    candidates = _prefixed(args, scope)
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    table = _index(scope, args[0](scope))
    amount = _number(args[2](scope), "MATCHPREFIX")
    tolerance = _number(args[3](scope), "MATCHPREFIX")
    narrowed = [
        key
        for key in candidates
        if abs(_number(table[key], "MATCHPREFIX") - amount) <= tolerance
    ]
    return narrowed[0] if len(narrowed) == 1 else None


def _label(args: Sequence[Compiled], scope: Scope) -> str | None:
    """The configured wording for a value, e.g. LABEL("unmatched_reason",
    verdict); the value itself when none is configured."""
    group, value = args[0](scope), args[1](scope)
    if value is None:
        return None
    labels = scope.lookup(("label",)) or {}
    return str(labels.get(text(group), {}).get(text(value), text(value)))


LAZY: dict[str, Callable[[Sequence[Compiled], Scope], Any]] = {
    "IF": _if,
    "IFS": _ifs,
    "AND": _and,
    "OR": _or,
    "SUMX": _sumx,
    "COUNTX": _countx,
    "MINX": _minx,
    "MAXX": _maxx,
    "ONLYX": _onlyx,
    "FIRSTX": _firstx,
    "LASTX": _lastx,
    "COALESCEX": _coalescex,
    "DISTINCTX": _distinctx,
    "JOINX": _joinx,
    "SUMIF": _sumif,
    "SUMIFS": _sumifs,
    "COUNTIF": _countif,
    "COUNTIFS": _countifs,
    "LABEL": _label,
    "LOOKUP": _lookup,
    "PREFIXCOUNT": _prefixcount,
    "PREFIXKEYS": _prefixkeys,
    "MATCHPREFIX": _matchprefix,
}


# --- Eager functions -------------------------------------------------------------


def _round(value: Any, digits: Any = Decimal(0)) -> Decimal | None:
    if value is None:
        return None
    places = Decimal(1).scaleb(-int(_number(digits, "ROUND")))
    return _number(value, "ROUND").quantize(places, rounding=ROUND_HALF_UP)


def _money(value: Any) -> Decimal | None:
    return _round(value, Decimal(2))


def _numbers(values: Sequence[Any], where: str) -> list[Decimal]:
    return [_number(v, where) for v in values if v is not None]


def _min(*values: Any) -> Decimal | None:
    numbers = _numbers(values, "MIN")
    return min(numbers) if numbers else None


def _max(*values: Any) -> Decimal | None:
    numbers = _numbers(values, "MAX")
    return max(numbers) if numbers else None


def _percent(value: Any, digits: Any = Decimal(2)) -> str | None:
    if value is None:
        return None
    return f"{_number(value, 'PERCENT'):.{int(digits)}%}"


def _regex(value: Any, pattern: Any, group: Any = Decimal(0)) -> str | None:
    if value is None:
        return None
    match = re.search(str(pattern), text(value))
    return match.group(int(group)) if match else None


def _before(value: Any, delimiter: Any) -> str | None:
    if value is None:
        return None
    head, found, _ = text(value).partition(text(delimiter))
    return head if found else None


def _after(value: Any, delimiter: Any) -> str | None:
    if value is None:
        return None
    _, found, tail = text(value).partition(text(delimiter))
    return tail if found else None


def _in(value: Any, *options: Any) -> bool:
    return any(_equal(value, option) for option in options)


def _coalesce(*values: Any) -> Any:
    return next((v for v in values if v is not None and v != ""), None)


def _substitute(value: Any, old: Any, new: Any) -> str | None:
    return None if value is None else text(value).replace(text(old), text(new))


def _datevalue(value: Any, pattern: Any) -> date | None:
    """Text read as a date in the given strptime pattern, e.g. "%y%m%d";
    blank when it is not one."""
    if value is None:
        return None
    try:
        return datetime.strptime(text(value), text(pattern)).date()
    except ValueError:
        return None


def _integer(value: Any) -> Decimal | None:
    """Text read as a whole number; blank when it is not one."""
    if value is None:
        return None
    try:
        return Decimal(int(text(value)))
    except ValueError:
        return None


def _idtext(value: Any) -> str:
    """A cell as an order id: a whole number without its ".0", text trimmed
    and without a trailing ".0"; blank cells and TRUE/FALSE as no id."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return str(int(value))
    result = str(value).strip()
    if result.endswith(".0") and result[:-2].isdigit():
        return result[:-2]
    return result


def _contains(value: Any, part: Any) -> bool:
    """Whether the text holds the part, without regard to case."""
    return value is not None and text(part).casefold() in text(value).casefold()


EAGER: dict[str, Callable[..., Any]] = {
    "NOT": lambda value: not truthy(value),
    "N": lambda value: Decimal(0) if value is None else value,
    "ISBLANK": lambda value: value is None or value == "",
    "BLANK": lambda: None,
    "COALESCE": _coalesce,
    "ABS": lambda value: None if value is None else abs(_number(value, "ABS")),
    "ROUND": _round,
    "MONEY": _money,
    "MIN": _min,
    "MAX": _max,
    "SUM": lambda *values: sum(_numbers(values, "SUM"), Decimal(0)),
    "LOWER": lambda value: None if value is None else text(value).lower(),
    "UPPER": lambda value: None if value is None else text(value).upper(),
    "TRIM": lambda value: None if value is None else text(value).strip(),
    "TEXT": text,
    "PLAIN": lambda value: (
        None if value is None else format(_number(value, "PLAIN"), "f")
    ),
    "PERCENT": _percent,
    "LEN": lambda value: Decimal(len(text(value))),
    "EXACT": lambda a, b: a == b,
    "IN": _in,
    "REGEX": _regex,
    "TEXTBEFORE": _before,
    "TEXTAFTER": _after,
    "SUBSTITUTE": _substitute,
    "DATEVALUE": _datevalue,
    "INTEGER": _integer,
    "CONTAINS": _contains,
    # The text in quotes, as a message shows a sheet name or a free-text cell.
    "QUOTE": lambda value: repr(text(value)),
    "ISNUMBER": lambda value: (
        isinstance(value, Decimal | int) and not isinstance(value, bool)
    ),
    "ISTEXT": lambda value: isinstance(value, str),
    "ISDATE": lambda value: isinstance(value, date),
    # Text as the readers clean it: trimmed, its spacing evened out, "-" and
    # "n/a" read as blank.
    "CLEAN": clean_text,
    "IDTEXT": _idtext,
}

# How many arguments each function takes: (fewest, most), None for no limit.
ARITY: dict[str, tuple[int, int | None]] = {
    "IF": (2, 3),
    "IFS": (2, None),
    "AND": (1, None),
    "OR": (1, None),
    "SUMX": (2, 2),
    "COUNTX": (1, 2),
    "MINX": (2, 2),
    "MAXX": (2, 2),
    "ONLYX": (2, 2),
    "FIRSTX": (2, 2),
    "LASTX": (2, 2),
    "COALESCEX": (2, 2),
    "DISTINCTX": (2, 2),
    "JOINX": (3, 4),
    "SUMIF": (3, 4),
    "SUMIFS": (4, None),
    "COUNTIF": (3, 3),
    "COUNTIFS": (3, None),
    "LABEL": (2, 2),
    "LOOKUP": (2, 2),
    "PREFIXCOUNT": (2, 2),
    "PREFIXKEYS": (2, 2),
    "MATCHPREFIX": (4, 4),
    "NOT": (1, 1),
    "N": (1, 1),
    "ISBLANK": (1, 1),
    "BLANK": (0, 0),
    "COALESCE": (1, None),
    "ABS": (1, 1),
    "ROUND": (1, 2),
    "MONEY": (1, 1),
    "MIN": (1, None),
    "MAX": (1, None),
    "SUM": (1, None),
    "LOWER": (1, 1),
    "UPPER": (1, 1),
    "TRIM": (1, 1),
    "TEXT": (1, 1),
    "PLAIN": (1, 1),
    "PERCENT": (1, 2),
    "LEN": (1, 1),
    "EXACT": (2, 2),
    "IN": (2, None),
    "REGEX": (2, 3),
    "TEXTBEFORE": (2, 2),
    "TEXTAFTER": (2, 2),
    "SUBSTITUTE": (3, 3),
    "DATEVALUE": (2, 2),
    "INTEGER": (1, 1),
    "CONTAINS": (2, 2),
    "QUOTE": (1, 1),
    "ISNUMBER": (1, 1),
    "ISTEXT": (1, 1),
    "ISDATE": (1, 1),
    "CLEAN": (1, 1),
    "IDTEXT": (1, 1),
}

assert set(ARITY) == set(LAZY) | set(EAGER)
