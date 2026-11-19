"""The copies of the uploads are tables of the setup.

A copy of an upload (``source_copy``) and of the finance report
(``finance_copy``) was each written by its own writer. Each is now a table
copying its upload as it arrived (``source``, ``sheet``, ``header_format``),
written exactly as before; the finance report's copy names the finance
upload, its sheet and its header format, which the writer used to fix. A
copy can now leave columns out (``hidden``, stored empty here) and add its
own. Nothing else in a setup is touched, so a category rebuilt by hand keeps
its own version 1. After this, a report tab is a table or a dashboard.

Revision ID: e2b3cbea8f50
Revises: 9160c93acf02
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "e2b3cbea8f50"
down_revision: str | Sequence[str] | None = "9160c93acf02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The finance report's copy wrote its header in this format.
_FINANCE_HEADER = "cashback_header"


def upgrade() -> None:
    _each(as_table)


def downgrade() -> None:
    _each(as_sheet)


def _each(change: Any) -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("select id, config from category_version"))
    for version_id, config in rows.fetchall():
        changed = change(config)
        if changed != config:
            connection.execute(
                sa.text(
                    "update category_version set config = cast(:config as jsonb) "
                    "where id = :id"
                ),
                {"config": json.dumps(changed), "id": version_id},
            )


def _finance(config: dict[str, Any]) -> tuple[str, str]:
    """The finance upload's role and its sheet's key."""
    upload = next(
        (f for f in config.get("inputs", []) if f.get("kind") == "finance"), None
    )
    if upload is None:
        return "finance", "report"
    sheets = upload.get("sheets") or [{}]
    return upload["role"], sheets[0].get("key") or "report"


def as_table(config: dict[str, Any]) -> dict[str, Any]:
    output = config.get("output")
    if not isinstance(output, dict):
        return config
    tabs = []
    for tab in output.get("tabs", []):
        tab = {**tab, "hidden": tab.get("hidden", [])}
        if tab.get("kind") == "source_copy":
            tab["kind"] = "table"
        elif tab.get("kind") == "finance_copy":
            role, sheet = _finance(config)
            tab.update(
                kind="table", source=role, sheet=sheet, header_format=_FINANCE_HEADER
            )
        tabs.append(tab)
    return {**config, "output": {**output, "tabs": tabs}}


def as_sheet(config: dict[str, Any]) -> dict[str, Any]:
    """Back to the copy writers: a copy with nothing left out and nothing
    added. The new setting goes."""
    output = config.get("output")
    if not isinstance(output, dict):
        return config
    kinds = {f.get("role"): f.get("kind") for f in config.get("inputs", [])}
    role, sheet = _finance(config)
    tabs = []
    for tab in output.get("tabs", []):
        plain = (
            tab.get("kind") == "table"
            and tab.get("source") is not None
            and not tab.get("hidden")
            and not tab.get("columns")
            and not tab.get("sections")
        )
        tab = {k: v for k, v in tab.items() if k != "hidden"}
        if plain and kinds.get(tab["source"]) == "finance":
            if (tab["source"], tab.get("sheet"), tab.get("header_format")) == (
                role,
                sheet,
                _FINANCE_HEADER,
            ):
                tab.update(
                    kind="finance_copy", source=None, sheet=None, header_format=None
                )
        elif plain:
            tab["kind"] = "source_copy"
        tabs.append(tab)
    return {**config, "output": {**output, "tabs": tabs}}
