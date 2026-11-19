"""Template files, for the configuration editor (Phase 5).

A sample of each of a category's uploads, kept with its sheets, headers and
first rows, for the editor to show and to preview a configuration on. Version 1
of travel is also brought up to date with app/seed/v1: one of its stop messages
was not a formula, which the configuration check (Phase 5) found.

Revision ID: 565e1ebb7b25
Revises: 9f10d6182051
"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "565e1ebb7b25"
down_revision: str | Sequence[str] | None = "9f10d6182051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_DIR = Path(__file__).resolve().parents[2] / "app" / "seed" / "v1"


def upgrade() -> None:
    op.create_table(
        "template_file",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sheets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["category.id"],
            name=op.f("fk_template_file_category_id_category"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_file")),
        sa.UniqueConstraint(
            "category_id", "role", name=op.f("uq_template_file_category_id")
        ),
    )
    op.create_index(
        op.f("ix_template_file_category_id"),
        "template_file",
        ["category_id"],
        unique=False,
    )
    connection = op.get_bind()
    config = json.loads((SEED_DIR / "travel.json").read_text())
    connection.execute(
        sa.text(
            "update category_version set config = cast(:config as jsonb) "
            "where number = 1 and category_id = "
            "(select id from category where key = :key)"
        ),
        {"config": json.dumps(config), "key": "travel"},
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_template_file_category_id"), table_name="template_file")
    op.drop_table("template_file")
