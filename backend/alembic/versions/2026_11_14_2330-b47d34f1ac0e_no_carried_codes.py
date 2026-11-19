"""An upload-row rule no longer marks the order it is listed under.

A row rule could copy its code onto that order (``carried``), but nothing read
the copy: no formula, no report, only the stored result. The setting is gone,
so it is removed from each row rule of every stored version. Only that key is
removed, never a whole setup replaced: a category rebuilt by hand keeps its
own version 1, and version 1 of each seeded category is then app/seed/v1.

Revision ID: b47d34f1ac0e
Revises: a9d2f0257852
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "b47d34f1ac0e"
down_revision: str | Sequence[str] | None = "a9d2f0257852"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("select id, config from category_version"))
    for version_id, config in rows.fetchall():
        changed = without_carried(config)
        if changed != config:
            connection.execute(
                sa.text(
                    "update category_version set config = cast(:config as jsonb) "
                    "where id = :id"
                ),
                {"config": json.dumps(changed), "id": version_id},
            )


def downgrade() -> None:
    # Without the key a row rule marks no order, which no one could see anyway.
    pass


def without_carried(config: dict[str, Any]) -> dict[str, Any]:
    model = config.get("model")
    if not isinstance(model, dict) or not isinstance(model.get("row_rules"), list):
        return config
    rules = [
        {k: v for k, v in rule.items() if k != "carried"}
        if isinstance(rule, dict)
        else rule
        for rule in model["row_rules"]
    ]
    return {**config, "model": {**model, "row_rules": rules}}
