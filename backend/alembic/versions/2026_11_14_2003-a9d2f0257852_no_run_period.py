"""A run no longer takes a period: its dates come from its files.

The upload step stopped asking for a period, so no run has had one since.
Formulas could still read it, as ``period.start`` and ``period.end``, which
were always blank. In every stored version:

- the run values version 1 had for the period, ``COALESCE(period.start, x)``
  and the like, become ``x``;
- the stop for a period that starts after it ends, which can no longer hold,
  is removed;
- any other ``period.start`` or ``period.end`` becomes ``BLANK()``, which is
  what it always was.

Version 1 of eShop and Travel is then app/seed/v1. Only these exact edits are
made, never a whole setup replaced: a category rebuilt by hand keeps its own
version 1. Each setup still means what it did and passes the check.

Revision ID: a9d2f0257852
Revises: 2318679945c3
"""

import json
import re
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "a9d2f0257852"
down_revision: str | Sequence[str] | None = "2318679945c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ESHOP_SPAN = (
    'IF(COUNTX("current") > 0, MIN{x}("current", order_date), '
    'MIN{x}("orders", order_date))'
)
_ESHOP_START = _ESHOP_SPAN.replace("MIN{x}", "MINX")
_ESHOP_END = _ESHOP_SPAN.replace("MIN{x}", "MAXX")

# Version 1's run values that read the period, and what they are without it.
FORMULAS = {
    'COALESCE(period.start, MINX("portal", order_date))': 'MINX("portal", order_date)',
    'COALESCE(period.end, MAXX("portal", order_date))': 'MAXX("portal", order_date)',
    f"COALESCE(period.start, {_ESHOP_START})": _ESHOP_START,
    f"COALESCE(period.end, {_ESHOP_END})": _ESHOP_END,
}

# Version 1's stops for a period that starts after it ends.
STOPS = {
    ("export_start > export_end", '"period_start is after period_end"'),
    ("period_start > period_end", '"period_start is after period_end"'),
}

PERIOD = re.compile(r"\bperiod\.(start|end)\b")


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("select id, config from category_version"))
    for version_id, config in rows.fetchall():
        changed = without_period(config)
        if changed != config:
            connection.execute(
                sa.text(
                    "update category_version set config = cast(:config as jsonb) "
                    "where id = :id"
                ),
                {"config": json.dumps(changed), "id": version_id},
            )


def downgrade() -> None:
    # A run had no period either way, so every stored version means the same
    # with or without it.
    pass


def without_period(config: dict[str, Any]) -> dict[str, Any]:
    config = _blanked(config)
    model = config.get("model")
    if isinstance(model, dict) and isinstance(model.get("stops"), list):
        model["stops"] = [
            stop
            for stop in model["stops"]
            if (stop.get("when"), stop.get("message")) not in STOPS
        ]
    return config


def _blanked(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: _blanked(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_blanked(v, key) for v in value]
    if isinstance(value, str) and key != "about":
        return PERIOD.sub("BLANK()", FORMULAS.get(value, value))
    return value
