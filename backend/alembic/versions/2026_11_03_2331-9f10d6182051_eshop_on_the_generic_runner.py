"""eShop reconciles on the generic runner (Phase 4).

Version 1 of eShop now carries its rules as formulas (``app/config_model/
seed_eshop.py``): the same approved rules, moved out of the eShop pipeline,
which is gone. Every category now runs on the generic runner. Version 1 of each
category is brought up to date with app/seed/v1, in place, as the earlier
Phase 3 and 4 migrations did: the rules a run reconciles under are unchanged.

Revision ID: 9f10d6182051
Revises: 3e733c917812
"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "9f10d6182051"
down_revision: str | Sequence[str] | None = "3e733c917812"
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
    # The eShop pipeline the earlier shape ran on no longer exists.
    pass
