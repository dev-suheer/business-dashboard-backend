"""Category versions, with version 1 of each category loaded from app/seed/v1.

Version 1 is the configuration generated from the code the client approved
(``scripts/build_seed.py``). The JSON files are read as they are: this
migration imports nothing from the app, so it means the same thing when the
code it was generated from is long gone.

Revision ID: a398d591de7b
Revises: 20280b84c838
Create Date: 2026-11-03 18:14:51.876209
"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a398d591de7b"
down_revision: str | Sequence[str] | None = "20280b84c838"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_DIR = Path(__file__).resolve().parents[2] / "app" / "seed" / "v1"
NOTE = "Version 1: the approved rules, as built into the code."


def upgrade() -> None:
    op.create_table(
        "category_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["category.id"],
            name=op.f("fk_category_version_category_id_category"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category_version")),
        sa.UniqueConstraint(
            "category_id", "number", name=op.f("uq_category_version_category_id")
        ),
    )
    op.create_index(
        op.f("ix_category_version_category_id"),
        "category_version",
        ["category_id"],
        unique=False,
    )
    op.add_column(
        "category", sa.Column("current_version_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_category_current_version_id_category_version"),
        "category",
        "category_version",
        ["current_version_id"],
        ["id"],
        use_alter=True,
    )
    op.add_column("run", sa.Column("version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_run_version_id_category_version"),
        "run",
        "category_version",
        ["version_id"],
        ["id"],
    )

    connection = op.get_bind()
    for path in sorted(SEED_DIR.glob("*.json")):
        config = json.loads(path.read_text())
        version_id = connection.execute(
            sa.text(
                "insert into category_version (category_id, number, config, note) "
                "select id, 1, cast(:config as jsonb), :note from category where key = :key "
                "returning id"
            ),
            {"config": json.dumps(config), "note": NOTE, "key": config["category"]},
        ).scalar_one()
        connection.execute(
            sa.text(
                "update category set current_version_id = :version where key = :key"
            ),
            {"version": version_id, "key": config["category"]},
        )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_run_version_id_category_version"), "run", type_="foreignkey"
    )
    op.drop_column("run", "version_id")
    op.drop_constraint(
        op.f("fk_category_current_version_id_category_version"),
        "category",
        type_="foreignkey",
    )
    op.drop_column("category", "current_version_id")
    op.drop_index(
        op.f("ix_category_version_category_id"), table_name="category_version"
    )
    op.drop_table("category_version")
