"""Logins are kept in the database, seeded from app/seed/users.json.

The one login used to be a file beside the backend (``credentials.json``, made
by ``app/create_credentials.py``). It is now a row of ``account``: a username
and the hash of its password, never the password. The accounts in
``app/seed/users.json`` are added here, and one already there is left as it is.
The JSON is read as it is: this migration imports nothing from the app.

Going back drops the table; the credential file is not made again.

Revision ID: 7a2d4c9b1e30
Revises: 5c1f0a9d7e42
"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "7a2d4c9b1e30"
down_revision: str | Sequence[str] | None = "5c1f0a9d7e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = Path(__file__).resolve().parents[2] / "app" / "seed" / "users.json"


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account")),
        sa.UniqueConstraint("username", name=op.f("uq_account_username")),
    )
    connection = op.get_bind()
    for user in json.loads(USERS.read_text(encoding="utf-8")):
        connection.execute(
            sa.text(
                "insert into account (username, password_hash) "
                "values (:username, :password_hash) on conflict (username) do nothing"
            ),
            {"username": user["username"], "password_hash": user["password_hash"]},
        )


def downgrade() -> None:
    op.drop_table("account")
