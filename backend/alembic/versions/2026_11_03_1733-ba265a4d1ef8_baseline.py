"""Baseline: an empty schema, so the database is under Alembic from the start.

Revision ID: ba265a4d1ef8
Revises:
Create Date: 2026-11-03 17:33:39.285495
"""

from collections.abc import Sequence

revision: str = "ba265a4d1ef8"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
