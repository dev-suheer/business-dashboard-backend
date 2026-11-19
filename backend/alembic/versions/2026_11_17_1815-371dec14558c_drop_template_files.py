"""The editor keeps no template files.

A sample file was kept for each of a category's uploads, to show its first
rows in the editor and to preview a setup on. The editor no longer previews:
a file it is given is read once, for its sheets and headers, and not kept.
The files this database's rows point at are deleted from the run store (never
anything outside it), then the table goes. Going back makes the table again,
empty: the files are not restored.

Revision ID: 371dec14558c
Revises: e2b3cbea8f50
"""

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

revision: str = "371dec14558c"
down_revision: str | Sequence[str] | None = "e2b3cbea8f50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    stored = [
        row[0] for row in connection.execute(sa.text("select path from template_file"))
    ]
    op.drop_index(op.f("ix_template_file_category_id"), table_name="template_file")
    op.drop_table("template_file")
    _delete(stored)


def _delete(stored: list[str]) -> None:
    """The stored files, and their folders once empty: inside the run store only."""
    root = settings.run_storage_dir.resolve()
    folders: set[Path] = set()
    for relative in stored:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or path == root:
            continue
        path.unlink(missing_ok=True)
        folders.add(path.parent)
    templates = root / "templates"
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        for empty in (folder, templates):
            if empty.is_relative_to(root) and empty != root and empty.is_dir():
                try:
                    empty.rmdir()
                except OSError:
                    pass  # not empty: something else is kept there


def downgrade() -> None:
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
