"""Version 1 now carries its whole output workbook (Phase 3).

The renderer writes every report from the configuration, so version 1 of each
category is brought up to date with app/seed/v1: the same approved workbook,
now described tab by tab instead of being built into the code. The rules a run
reconciles under are unchanged, so this rewrites version 1 rather than adding
a version 2 the client never made.

Revision ID: abf0a2032fcc
Revises: a398d591de7b
"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "abf0a2032fcc"
down_revision: str | Sequence[str] | None = "a398d591de7b"
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
    # The earlier shape of version 1 described the output but did not drive it;
    # nothing reads it any more, so there is nothing to restore.
    pass
