"""Run history: categories, runs and their uploaded files.

The three categories that exist today are seeded here, so a run can be recorded
the moment this migration has been applied.

Revision ID: 20280b84c838
Revises: ba265a4d1ef8
Create Date: 2026-11-03 17:47:35.425315
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20280b84c838"
down_revision: str | Sequence[str] | None = "ba265a4d1ef8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIES = [
    {
        "key": "eshop",
        "name": "eShop",
        "description": "Order lines against the vendor account ledger.",
    },
    {
        "key": "travel",
        "name": "Travel",
        "description": "Hotel and flight bookings against the vendor's weekly claim.",
    },
    {
        "key": "food",
        "name": "Food",
        "description": "Portal orders against the vendor statement.",
    },
]


def upgrade() -> None:
    category = op.create_table(
        "category",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category")),
        sa.UniqueConstraint("key", name=op.f("uq_category_key")),
    )
    op.create_table(
        "run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "exception_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("report_filename", sa.String(length=255), nullable=True),
        sa.Column("report_path", sa.Text(), nullable=True),
        sa.Column("report_sha256", sa.String(length=64), nullable=True),
        sa.Column("report_size", sa.BigInteger(), nullable=True),
        sa.Column("result_path", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status in ('running', 'completed', 'failed')",
            name=op.f("ck_run_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["category.id"], name=op.f("fk_run_category_id_category")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run")),
    )
    op.create_index(op.f("ix_run_category_id"), "run", ["category_id"], unique=False)
    op.create_index(op.f("ix_run_started_at"), "run", ["started_at"], unique=False)
    op.create_table(
        "run_file",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["run.id"],
            name=op.f("fk_run_file_run_id_run"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_file")),
        sa.UniqueConstraint("run_id", "role", name=op.f("uq_run_file_run_id")),
    )
    op.create_index(op.f("ix_run_file_run_id"), "run_file", ["run_id"], unique=False)
    op.bulk_insert(category, CATEGORIES)


def downgrade() -> None:
    op.drop_index(op.f("ix_run_file_run_id"), table_name="run_file")
    op.drop_table("run_file")
    op.drop_index(op.f("ix_run_started_at"), table_name="run")
    op.drop_index(op.f("ix_run_category_id"), table_name="run")
    op.drop_table("run")
    op.drop_table("category")
