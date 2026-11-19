"""Food reconciles on the generic runner (Phase 4).

Version 1 of food now carries its rules as formulas (``app/config_model/
seed_food.py``): the same approved rules, moved out of the food pipeline, which
is gone. Every category's uploads also say what kind of file each is, so the
runner knows the wallet history and the finance report from rows to reconcile.
Version 1 of each category is brought up to date with app/seed/v1, in place,
as the Phase 3 migration did: the rules a run reconciles under are unchanged.

Revision ID: 27759aa964d8
Revises: abf0a2032fcc
"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "27759aa964d8"
down_revision: str | Sequence[str] | None = "abf0a2032fcc"
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
    # The food pipeline the earlier shape ran on no longer exists.
    pass
