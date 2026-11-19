"""The generic runner: read, build the orders, decide, check, sum — as the
configuration says.

1. Read each upload's rows (``inputs.read_upload``) into its row sets, then
   work out the rows' further fields, which may look into other uploads
   through the lookups (is this SKU on promotion? which order does this
   credit note's reference name?).
2. Build the orders. The pair builder pairs two sides' rows by key: only
   active rows (Delivered, say) pair, and an order whose other side has the
   key only on an inactive row carries that row, so a status mismatch can be
   seen. The group builder gathers rows of any number of sets under their
   key: an order's claim lines, its portal row and that row's repeats.
3. Raise the row rules' exceptions, in order.
4. Compute each order's fields, then the run-wide values (the export's
   dates, a claim's week); a stop that holds ends the run with its message. For each
   order, take the first status rule that holds. It sets the verdict and may raise an exception; or it runs the
   checks, and the verdict says whether any failed. Then raise its findings.
5. Sort the orders, check the wallet history, raise the run's own rules, sort
   the exceptions, and work out the figures and the history entry.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.config_model.models import CategoryConfig, Group, Model, Raise
from app.expr import FormulaError, Scope, Values, evaluate, text, truthy
from app.recon.exceptions import (
    ReconException,
    make_exception,
    sort_exceptions,
)
from app.recon.inputs import ReconInputError, Upload
from app.recon.models import SheetManifest
from app.recon.wallet import WalletReconciliation
from app.runner.inputs import read_upload
from app.runner.result import Entity, Row, RunResult, exception_scope
from app.runner.wallet import check_wallet


def run(
    config: CategoryConfig,
    uploads: dict[str, Upload],
    *,
    overrides: dict[str, Any] | None = None,
    max_rows: int,
) -> RunResult:
    context = _Context(config, overrides)
    detected: list[SheetManifest] = []
    rows: dict[str, list[Row]] = {}
    exceptions: list[ReconException] = []
    for spec in config.inputs:
        if spec.role in uploads:
            # The wallet history and a reference file are lists too. Their
            # sheets are not listed here: the wallet check notes the history
            # it reads, and a reference file is no part of the reconciliation.
            read = read_upload(
                spec,
                uploads[spec.role],
                max_rows=max_rows,
                scope=context.scope,
                detected=detected if spec.kind == "rows" else [],
                exceptions=exceptions,
            )
            for name, members in read.items():
                rows.setdefault(name, []).extend(members)
    wallet = next(
        (
            uploads[f.role]
            for f in config.inputs
            if f.kind == "wallet" and f.role in uploads
        ),
        None,
    )
    return _reconcile(context, rows, detected, exceptions, wallet, max_rows)


def reconcile(
    config: CategoryConfig,
    rows: dict[str, list[Row]],
    *,
    overrides: dict[str, Any] | None = None,
) -> RunResult:
    """What ``run`` does once the uploads are read into row sets, with no
    wallet history."""
    context = _Context(config, overrides)
    return _reconcile(context, rows, [], [], None, max_rows=0)


class _Context:
    """The run's own scope: its parameters and labels, then its run-wide
    values and figures, and its named sets."""

    def __init__(
        self,
        config: CategoryConfig,
        overrides: dict[str, Any] | None,
    ) -> None:
        if config.model is None:
            raise ReconInputError(f"{config.category} has no reconciliation model")
        self.config = config
        self.model: Model = config.model
        params: dict[str, Any] = {p.key: p.typed() for p in config.parameters}
        params.update({k: v for k, v in (overrides or {}).items() if v is not None})
        self.values: dict[str, Any] = {
            "param": params,
            "label": config.labels,
            # The wallet check's result, once there is one.
            "wallet_check": None,
        }
        self.sets = _Sets(self.model)
        self.scope = Values(self.values, collections=self.sets.get)
        self.values["lookup"] = _Lookups(self.model, self.sets, self.scope)


def _reconcile(
    context: _Context,
    rows: dict[str, list[Row]],
    detected: list[SheetManifest],
    exceptions: list[ReconException],
    wallet_upload: Upload | None,
    max_rows: int,
) -> RunResult:
    model, scope, sets, values = (
        context.model,
        context.scope,
        context.sets,
        context.values,
    )
    # An upload left out is a set with no rows.
    for upload in context.config.inputs:
        for sheet in upload.sheets:
            rows.setdefault(sheet.set or upload.role, [])
    for name, members in rows.items():
        sets.bind(name, _members(members, scope))
    for spec in model.row_fields:
        for item in sets.get(spec.set) or []:
            row = _row_of(item)
            for named in spec.fields:
                row[named.key] = evaluate(named.formula, item)
    # Sets made from those rows are made again, now that the rows are complete.
    sets.cache.clear()

    build = _Pairing(model, rows, scope) if model.builder == "pair" else None
    if build is not None:
        sets.bind("inactive", lambda: build.inactive)
        sets.bind("duplicates", lambda: build.duplicates)
    for rule in model.row_rules:
        for item in sets.get(rule.over) or []:
            if truthy(evaluate(rule.when, item)):
                exceptions.append(_exception(rule, item, None))

    entities = (
        build.entities(scope) if build is not None else _group(model, sets, scope)
    )
    sets.bind("entities", lambda: [e.scope for e in entities])
    for entity in entities:
        for named in model.fields:
            entity.values[named.key] = evaluate(named.formula, entity.scope)
    sets.cache.clear()
    for named in model.context:
        values[named.key] = evaluate(named.formula, scope)
    for stop in model.stops:
        if truthy(evaluate(stop.when, scope)):
            raise ReconInputError(text(evaluate(stop.message, scope)))
    for entity in entities:
        _decide(model, entity, exceptions)
        _findings(model, entity, exceptions)

    # Checked before the orders are sorted: the checks list in the order the
    # orders were first met, as the wallet sheet shows them.
    wallet: WalletReconciliation | None = None
    if model.wallet is not None and wallet_upload is not None:
        excluded = model.wallet.excluded
        wallet, found = check_wallet(
            model.wallet,
            wallet_upload,
            sets.get(model.wallet.rows) or [],
            (sets.get(excluded) or []) if excluded is not None else [],
            scope=scope,
            max_rows=max_rows,
            detected=detected,
        )
        exceptions += found
        values["wallet_check"] = wallet.model_dump()
    # The wallet check's rows and the history's movements no row had, for a
    # table to list: none without a wallet history.
    checks = wallet.checks if wallet is not None else []
    unmatched = wallet.unmatched_history if wallet is not None else []
    sets.bind(
        "wallet_checks", lambda: [Values(c.model_dump(), parent=scope) for c in checks]
    )
    sets.bind(
        "wallet_unmatched",
        lambda: [Values(e.model_dump(), parent=scope) for e in unmatched],
    )
    entities.sort(key=lambda e: _sort_key(model.sort, e.scope))
    sets.cache.clear()
    for run_rule in model.run_rules:
        if truthy(evaluate(run_rule.when, scope)):
            exceptions.append(_exception(run_rule, scope, None))
    exceptions = sort_exceptions(exceptions)
    by_key = {e.key: e.scope for e in entities}
    sets.bind(
        "exceptions", lambda: [exception_scope(e, by_key, scope) for e in exceptions]
    )

    metrics: dict[str, Any] = {}
    for metric in model.metrics:
        metrics[metric.key] = values[metric.key] = evaluate(metric.formula, scope)
    history = {
        name: evaluate(formula, scope)
        for name, formula in model.history.model_dump().items()
    }
    return RunResult(
        config=context.config,
        rows=rows,
        entities=entities,
        exceptions=exceptions,
        wallet=wallet,
        detected=detected,
        metrics=metrics,
        history=history,
        scope=scope,
    )


# --- The pair builder -------------------------------------------------------------


class _Pairing:
    """Two sides' rows by key. Rows left out are the set ``inactive`` (each a
    ``role``, a ``key`` and its ``row``), keys active more than once the set
    ``duplicates`` (a ``role``, a ``key`` and its ``copies``)."""

    def __init__(self, model: Model, rows: dict[str, list[Row]], scope: Scope) -> None:
        self.sides = sides = model.sides
        self.active: dict[str, dict[str, list[Row]]] = {}
        unpaired: dict[str, list[tuple[str, Row]]] = {}
        for side in sides:
            self.active[side.role] = {}
            unpaired[side.role] = []
            for row in rows.get(side.role, []):
                key = text(row[side.key])
                if truthy(evaluate(side.active, Values(row, parent=scope))):
                    self.active[side.role].setdefault(key, []).append(row)
                else:
                    unpaired[side.role].append((key, row))

        # Inactive rows nobody pairs with: the key is not active on the other side.
        self.inactive: list[Scope] = [
            Values({"role": side.role, "key": key, "row": row}, parent=scope)
            for side, other in ((sides[0], sides[1]), (sides[1], sides[0]))
            for key, row in unpaired[side.role]
            if key not in self.active[other.role]
        ]
        self.duplicates: list[Scope] = [
            Values(
                {"role": side.role, "key": key, "copies": Decimal(len(copies))},
                parent=scope,
            )
            for side in sides
            for key, copies in self.active[side.role].items()
            if len(copies) > 1
        ]
        # The first inactive row under each key stands in for a side with no
        # active one.
        self.first_inactive: dict[str, dict[str, Row]] = {}
        for side in sides:
            firsts = self.first_inactive[side.role] = {}
            for key, row in unpaired[side.role]:
                firsts.setdefault(key, row)

    def entities(self, scope: Scope) -> list[Entity]:
        entities: list[Entity] = []
        for key in sorted(set().union(*(self.active[s.role] for s in self.sides))):
            # A note is blank until a status rule writes one, as for a group.
            values: dict[str, Any] = {"key": key, "verdict": None, "note": None}
            for side in self.sides:
                copies = self.active[side.role].get(key, [])
                values[side.role] = (
                    copies[0] if copies else self.first_inactive[side.role].get(key)
                )
                values[f"{side.role}_active"] = bool(copies)
                values[f"{side.role}_copies"] = Decimal(len(copies))
            entities.append(Entity(key=key, values=values, scope=Values(values, scope)))
        return entities


# --- The group builder ------------------------------------------------------------


@dataclass
class _Gathered:
    primary: dict[str, tuple[tuple[Any, ...], Row]] = field(default_factory=dict)
    rows: dict[str, list[Row]] = field(default_factory=dict)


def _group(model: Model, sets: "_Sets", scope: Scope) -> list[Entity]:
    """Orders in the order their keys are first met, member by member."""
    collections = [m.repeats if m.primary else m.name for m in model.members]
    names = [c for c in collections if c is not None]
    gathered: dict[str, _Gathered] = {}
    for member in model.members:
        for item in sets.get(member.set) or []:
            key = text(evaluate(member.key, item))
            if not key:
                continue
            row = _row_of(item)
            into = gathered.setdefault(key, _Gathered(rows={n: [] for n in names}))
            if not member.primary:
                into.rows[member.name].append(row)
                continue
            order = _sort_key(member.primary, item)
            current = into.primary.get(member.name)
            if current is None:
                into.primary[member.name] = (order, row)
                continue
            # The earlier row is the order's own; a tie keeps the one met first.
            if order < current[0]:
                into.primary[member.name] = (order, row)
                set_aside = current[1]
            else:
                set_aside = row
            if member.repeats is not None:
                into.rows[member.repeats].append(set_aside)

    entities: list[Entity] = []
    for key, into in gathered.items():
        values: dict[str, Any] = {"key": key, "verdict": None, "note": None}
        for member in model.members:
            if member.primary:
                chosen = into.primary.get(member.name)
                values[member.name] = chosen[1] if chosen else None
        own = _Own(into.rows, model.groups)
        entity = Entity(
            key=key,
            values=values,
            scope=Values(values, parent=scope, collections=own),
            rows=into.rows,
        )
        own.scope = entity.scope
        entities.append(entity)
    return entities


class _Own:
    """An order's own collections: its rows of each, and its groups of them,
    as scopes inside the order. Each is built on first use."""

    __slots__ = ("cache", "groups", "rows", "scope")

    def __init__(self, rows: dict[str, list[Row]], groups: list[Group]) -> None:
        self.rows = rows
        self.groups = {g.name: g for g in groups}
        self.cache: dict[str, list[Scope]] = {}
        self.scope: Scope | None = None

    def __call__(self, name: str) -> Sequence[Scope] | None:
        members = self.cache.get(name)
        if members is not None:
            return members
        if name in self.rows:
            members = self.cache[name] = [
                Values(row, parent=self.scope) for row in self.rows[name]
            ]
        elif name in self.groups:
            members = self.cache[name] = self._gather(self.groups[name])
        return members

    def _gather(self, spec: Group) -> list[Scope]:
        """The order's rows of ``spec.of`` by key, in the order first met."""
        by_key: dict[str, list[Row]] = {}
        for item in self(spec.of) or []:
            key = text(evaluate(spec.key, item))
            by_key.setdefault(key, []).append(_row_of(item))
        groups: list[Scope] = []
        for key, rows in by_key.items():
            own = _Own({spec.of: rows}, [])
            values: dict[str, Any] = {"key": key}
            group = Values(values, parent=self.scope, collections=own)
            own.scope = group
            for named in spec.fields:
                values[named.key] = evaluate(named.formula, group)
            groups.append(group)
        return groups


# --- Verdicts, checks and findings ----------------------------------------------


def _decide(model: Model, entity: Entity, exceptions: list[ReconException]) -> None:
    for rule in model.rules:
        if not truthy(evaluate(rule.when, entity.scope)):
            continue
        if rule.exception is not None:
            exceptions.append(_exception(rule.exception, entity.scope, entity.key))
            entity.codes.append(rule.exception.code)
        if rule.checks:
            failed = [c for c in model.checks if truthy(evaluate(c.when, entity.scope))]
            exceptions.extend(_exception(c, entity.scope, entity.key) for c in failed)
            # A checked order carries its failed checks' codes, nothing else.
            entity.codes = [c.code for c in failed]
            entity.verdict = rule.broken if failed else rule.clean
        else:
            entity.verdict = rule.verdict
        entity.values["verdict"] = entity.verdict
        if rule.note is not None:
            entity.values["note"] = evaluate(rule.note, entity.scope)
        return


def _findings(model: Model, entity: Entity, exceptions: list[ReconException]) -> None:
    for finding in model.findings:
        items = (
            entity.scope.collection(finding.each)
            if finding.each is not None
            else [entity.scope]
        )
        for item in items:
            if truthy(evaluate(finding.when, item)):
                exceptions.append(_exception(finding, item, entity.key))
                entity.codes.append(finding.code)


def _exception(spec: Raise, scope: Scope, key: str | None) -> ReconException:
    """By default an exception is the order's, and a row's or the run's is no
    order's."""
    return make_exception(
        spec.code,
        order_no=text(evaluate(spec.order_no, scope)) if spec.order_no else key,
        amount=evaluate(spec.amount, scope) if spec.amount else None,
        detail=text(evaluate(spec.detail, scope)),
    )


def _sort_key(formulas: Sequence[str], scope: Scope) -> tuple[Any, ...]:
    """Blank sorts first, as an order with no date always has."""
    parts: list[Any] = []
    for formula in formulas:
        value = evaluate(formula, scope)
        parts.append((0,) if value is None else (1, value))
    return tuple(parts)


def _members(rows: list[Row], parent: Scope) -> Callable[[], Sequence[Scope]]:
    return lambda: [Values(row, parent=parent) for row in rows]


def _row_of(item: Scope) -> Row:
    """The row a member of a row set stands for."""
    if not isinstance(item, Values) or not isinstance(item.values, Row):
        raise FormulaError("An order is made of rows, not of orders or exceptions")
    return item.values


# --- Lookups ----------------------------------------------------------------------


class _Lookups:
    """The run's lookup tables, each built on first use: by then the set it is
    made from is complete (the promo catalogue once read, the billed orders
    once decided)."""

    def __init__(self, model: Model, sets: "_Sets", scope: Scope) -> None:
        self.defs = {spec.key: spec for spec in model.lookups}
        self.sets = sets
        self.scope = scope
        self.built: dict[str, dict[str, Any]] = {}

    def index(self, name: str) -> dict[str, Any]:
        if name in self.built:
            return self.built[name]
        spec = self.defs.get(name)
        if spec is None:
            raise FormulaError(f"Unknown lookup {name!r}")
        members = self.sets.get(spec.set)
        if members is None:
            raise FormulaError(
                f"Lookup {name!r} is made from {spec.set!r}, which is not a set"
            )
        table: dict[str, Any] = {}
        for member in members:
            by = evaluate(spec.by, member)
            if by is None or by == "":
                continue
            key = text(by)
            value = evaluate(spec.value, member)
            if spec.combine == "sum":
                table[key] = table.get(key, Decimal(0)) + (value or Decimal(0))
            elif spec.combine == "last" or key not in table:
                table[key] = value
        if spec.then is not None:
            for key, value in table.items():
                table[key] = evaluate(spec.then, Values({"value": value}, self.scope))
        self.built[name] = table
        return table


# --- Named sets -----------------------------------------------------------------


class _Sets:
    """The sets aggregates run over: the rows of each upload, every order, every
    exception, and the configured subsets of those. Each is built on first use,
    and built again once what it is made from has changed."""

    def __init__(self, model: Model) -> None:
        self.defs = {s.key: s for s in model.sets}
        self.base: dict[str, Callable[[], Sequence[Scope]]] = {}
        self.cache: dict[str, Sequence[Scope]] = {}
        # The configured sets being made, so one made from itself is a reason
        # the run gives, not a stack overflow.
        self.making: set[str] = set()

    def bind(self, name: str, members: Callable[[], Sequence[Scope]]) -> None:
        self.base[name] = members
        self.cache.clear()

    def get(self, name: str) -> Sequence[Scope] | None:
        """The set's members; None for a set the run does not have (yet)."""
        if name in self.cache:
            return self.cache[name]
        if name in self.base:
            members = self.base[name]()
        elif name in self.defs:
            if name in self.making:
                raise FormulaError(f"Set {name!r} is made from itself")
            spec = self.defs[name]
            self.making.add(name)
            try:
                over = self.get(spec.over)
            finally:
                self.making.discard(name)
            if over is None:
                raise FormulaError(
                    f"Set {name!r} is made from {spec.over!r}, which is not one"
                )
            members = [m for m in over if truthy(evaluate(spec.where, m))]
        else:
            return None
        self.cache[name] = members
        return members
