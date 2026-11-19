"""What a run produces: rows read, orders built, exceptions, figures.

Category-blind. The report, the history entry and the stored result are all
read from it through the category's configuration.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.config_model.models import CategoryConfig
from app.expr import Values
from app.recon.exceptions import ReconException
from app.recon.models import SheetManifest
from app.recon.wallet import WalletReconciliation


class Row(dict[str, Any]):
    """One row of an upload: its fields, read and typed, by name.

    Every row also has the fields ``sheet``, the sheet it came from, and
    ``row``, its place among that sheet's data rows, from 1.
    """

    __slots__ = ("index", "role", "sheet")

    def __init__(
        self, role: str, sheet: str, index: int, fields: dict[str, Any]
    ) -> None:
        super().__init__(fields)
        self.setdefault("sheet", sheet)
        self.setdefault("row", Decimal(index))
        self.role = role
        self.sheet = sheet
        self.index = index

    def as_json(self) -> dict[str, Any]:
        return dict(self)


@dataclass
class Entity:
    """One order (or booking): the rows under its key, its fields, its verdict."""

    key: str
    values: dict[str, Any]
    scope: Values
    verdict: str | None = None
    # The exceptions raised for it, by code.
    codes: list[str] = field(default_factory=list)
    # Its collections of rows, by name: its claim lines, say.
    rows: dict[str, list[Row]] = field(default_factory=dict)


@dataclass
class RunResult:
    config: CategoryConfig
    rows: dict[str, list[Row]]
    entities: list[Entity]
    exceptions: list[ReconException]
    wallet: WalletReconciliation | None
    detected: list[SheetManifest]
    metrics: dict[str, Any]
    history: dict[str, Any]
    # The run's own scope: parameters, figures and the named sets.
    scope: Values

    @property
    def category(self) -> str:
        return self.config.category

    @property
    def parameters(self) -> dict[str, Any]:
        """The parameters the run used: the configured ones, overrides applied."""
        params: dict[str, Any] = self.scope.values["param"]
        return params

    def model_dump_json(self) -> str:
        """The stored result: everything the run decided, as JSON."""
        model = self.config.model
        # The names an order holds one row under: a pair's sides, a group's
        # chosen rows.
        held = (
            {side.role for side in model.sides}
            | {m.name for m in model.members if m.primary}
            if model
            else set()
        )
        return json.dumps(
            {
                "category": self.category,
                "parameters": self.parameters,
                "entities": [
                    {
                        "key": entity.key,
                        "verdict": entity.verdict,
                        "codes": entity.codes,
                        "fields": {
                            name: value
                            for name, value in entity.values.items()
                            if name not in held and not isinstance(value, Row)
                        },
                        "rows": {
                            **{
                                name: row.as_json() if isinstance(row, Row) else None
                                for name in sorted(held)
                                for row in [entity.values.get(name)]
                            },
                            **{
                                name: [row.as_json() for row in rows]
                                for name, rows in entity.rows.items()
                            },
                        },
                    }
                    for entity in self.entities
                ],
                "exceptions": [e.model_dump(mode="json") for e in self.exceptions],
                "wallet": self.wallet.model_dump(mode="json") if self.wallet else None,
                "detected": [d.model_dump(mode="json") for d in self.detected],
                "metrics": self.metrics,
                "history": self.history,
            },
            default=_json_value,
        )


def exception_scope(
    exception: ReconException, orders: Mapping[str, Values], parent: Values
) -> Values:
    """An exception as a formula sees it: its fields, and ``order``, the order
    it names (blank when it names none)."""
    values = exception.model_dump()
    values["order"] = orders.get(exception.order_no or "")
    return Values(values, parent=parent)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        # As the v1 results stored a decimal, so a stored result reads the same.
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Row):
        return value.as_json()
    raise TypeError(f"{type(value).__name__} is not JSON")
