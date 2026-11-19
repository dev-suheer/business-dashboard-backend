"""Parsing formulas: Excel-like text into a small syntax tree. Never ``eval``.

    portal.total_amount - vendor.total
    IF(ABS(N(amount_difference)) > param.tolerance, "Amount differs", "")
    "Portal total " & PLAIN(portal.total_amount) & "."
    SUMX("settled", N(due))

Names are dotted paths (``portal.total_amount``); what they mean is up to the
scope a formula is evaluated in. Functions are written in capitals and must be
known: an unknown function, or the wrong number of arguments, is a parse
error, so a mistake surfaces when the configuration is saved rather than in
the middle of a run.

Precedence, loosest first: comparison (= <> < <= > >=), & (join text),
+ and -, * and /, then a leading minus.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache


class FormulaError(ValueError):
    """A formula that cannot be parsed, or that refers to something unknown."""


@dataclass(frozen=True)
class Literal:
    value: Decimal | str | bool | None


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class Unary:
    op: str
    operand: "Node"


@dataclass(frozen=True)
class Binary:
    op: str
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple["Node", ...]


Node = Literal | Ref | Unary | Binary | Call

_TOKEN = re.compile(
    r"""
    \s*(?:
      (?P<number>\d+(?:\.\d+)?)
    | (?P<string>"(?:[^"]|"")*")
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
    | (?P<op><>|<=|>=|[-+*/&=<>(),])
    )
    """,
    re.VERBOSE,
)

_COMPARISON = ("=", "<>", "<", "<=", ">", ">=")


def _tokens(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None or match.end() == pos:
            raise FormulaError(f"Cannot read the formula at {text[pos : pos + 12]!r}")
        kind = match.lastgroup
        assert kind is not None
        tokens.append((kind, match.group(kind)))
        pos = match.end()
        # Trailing whitespace is consumed by the next match's \s*.
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return tokens


class _Parser:
    def __init__(self, text: str, arity: dict[str, tuple[int, int | None]]) -> None:
        self.text = text
        self.tokens = _tokens(text)
        self.pos = 0
        self.arity = arity

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise FormulaError(f"The formula ends too early: {self.text!r}")
        self.pos += 1
        return token

    def expect(self, value: str) -> None:
        kind, got = self.take()
        if kind != "op" or got != value:
            raise FormulaError(f"Expected {value!r} but found {got!r} in {self.text!r}")

    def at(self, *values: str) -> bool:
        token = self.peek()
        return token is not None and token[0] == "op" and token[1] in values

    def parse(self) -> Node:
        node = self.comparison()
        extra = self.peek()
        if extra is not None:
            raise FormulaError(f"Unexpected {extra[1]!r} in {self.text!r}")
        return node

    def comparison(self) -> Node:
        node = self.concat()
        while self.at(*_COMPARISON):
            op = self.take()[1]
            node = Binary(op, node, self.concat())
        return node

    def concat(self) -> Node:
        node = self.additive()
        while self.at("&"):
            self.take()
            node = Binary("&", node, self.additive())
        return node

    def additive(self) -> Node:
        node = self.multiplicative()
        while self.at("+", "-"):
            op = self.take()[1]
            node = Binary(op, node, self.multiplicative())
        return node

    def multiplicative(self) -> Node:
        node = self.unary()
        while self.at("*", "/"):
            op = self.take()[1]
            node = Binary(op, node, self.unary())
        return node

    def unary(self) -> Node:
        if self.at("-", "+"):
            op = self.take()[1]
            return Unary(op, self.unary())
        return self.primary()

    def primary(self) -> Node:
        kind, value = self.take()
        if kind == "number":
            return Literal(Decimal(value))
        if kind == "string":
            return Literal(value[1:-1].replace('""', '"'))
        if kind == "op" and value == "(":
            node = self.comparison()
            self.expect(")")
            return node
        if kind == "name":
            upper = value.upper()
            if self.at("("):
                return self.call(upper)
            if upper == "TRUE":
                return Literal(True)
            if upper == "FALSE":
                return Literal(False)
            return Ref(value)
        raise FormulaError(f"Unexpected {value!r} in {self.text!r}")

    def call(self, name: str) -> Call:
        if name not in self.arity:
            raise FormulaError(f"Unknown function {name}")
        self.expect("(")
        args: list[Node] = []
        if not self.at(")"):
            args.append(self.comparison())
            while self.at(","):
                self.take()
                args.append(self.comparison())
        self.expect(")")
        low, high = self.arity[name]
        if len(args) < low or (high is not None and len(args) > high):
            expected = str(low) if high == low else f"{low} to {high or 'any'}"
            raise FormulaError(f"{name} takes {expected} argument(s), not {len(args)}")
        return Call(name, tuple(args))


@lru_cache(maxsize=4096)
def parse(text: str) -> Node:
    """A formula's syntax tree. Cached: a formula is parsed once, however many
    rows it is evaluated for."""
    from app.expr.functions import ARITY

    return _Parser(text, ARITY).parse()


def references(node: Node) -> set[str]:
    """Every name a formula reads, for the editor's lineage lines."""
    if isinstance(node, Ref):
        return {node.name}
    if isinstance(node, Unary):
        return references(node.operand)
    if isinstance(node, Binary):
        return references(node.left) | references(node.right)
    if isinstance(node, Call):
        return (
            set().union(*(references(arg) for arg in node.args)) if node.args else set()
        )
    return set()


def reference_spans(text: str) -> list[tuple[int, int]]:
    """Where each name the formula reads is written, in the order written: the
    order its parsed form holds them in, so the k-th span is the k-th ``Ref``.
    A name before ``(`` is a function, and TRUE and FALSE are values."""
    tokens: list[tuple[str, str, int, int]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None or match.end() == pos:
            break  # only trailing spaces are left: the formula parsed
        kind = match.lastgroup
        assert kind is not None
        tokens.append((kind, match.group(kind), match.start(kind), match.end(kind)))
        pos = match.end()
    spans: list[tuple[int, int]] = []
    for i, (kind, value, start, end) in enumerate(tokens):
        if kind != "name":
            continue
        called = i + 1 < len(tokens) and tokens[i + 1][1] == "("
        if not called and value.upper() not in ("TRUE", "FALSE"):
            spans.append((start, end))
    return spans
