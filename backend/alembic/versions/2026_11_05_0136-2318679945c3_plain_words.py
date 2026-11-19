"""Each rule, stop, list and exception carries its plain words.

A setup can say what each of its rules finds or decides, in words, for whoever
reads it in the editor (``about``; no run reads it). Version 1 of each category
is brought up to date with app/seed/v1, which now has the words for the approved
rules (``app/config_model/seed_words.py``), as the earlier migrations did. Later
versions have no words yet, which a setup may leave out.

Revision ID: 2318679945c3
Revises: 099107642693
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "2318679945c3"
down_revision: str | Sequence[str] | None = "099107642693"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_DIR = Path(__file__).resolve().parents[2] / "app" / "seed" / "v1"


def upgrade() -> None:
    connection = op.get_bind()
    for path in sorted(SEED_DIR.glob("*.json")):
        config = json.loads(path.read_text())
        connection.execute(
            sa.text(
                "update category_version set config = cast(:config as jsonb) "
                "where number = 1 and category_id = "
                "(select id from category where key = :key)"
            ),
            {"config": json.dumps(config), "key": config["category"]},
        )


def downgrade() -> None:
    # The words are text only: every stored version is the same without them.
    connection = op.get_bind()
    rows = connection.execute(sa.text("select id, config from category_version"))
    for version_id, config in rows.fetchall():
        connection.execute(
            sa.text(
                "update category_version set config = cast(:config as jsonb) "
                "where id = :id"
            ),
            {"config": json.dumps(_unworded(config)), "id": version_id},
        )


def _unworded(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _unworded(v) for k, v in value.items() if k != "about"}
    if isinstance(value, list):
        return [_unworded(v) for v in value]
    return value
