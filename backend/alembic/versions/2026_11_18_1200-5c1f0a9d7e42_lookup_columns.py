"""Look-up columns, and the finance report a reference file.

The finance report had its own kind of upload, and a table its own five
finance columns, filled by lookups of a key the tab named
(``cashback_key_column`` or ``cashback_key_value``) on the parts that ticked
``cashback``. Each of those columns is now a look-up column
(``lookup_columns``) saying what it finds: in the first copy of the finance
report, the row whose ``order_id`` holds the row's key, and that row's
reward_amount, vertical_name, reward_code, reward_type or outcome, as the
writer paired them. A part ticks ``lookups`` when it filled them, which only
a table with them did; ``cashback`` on any other part wrote nothing. The
finance upload is a ``reference`` file. Every report is written exactly as
before.

Going back makes the finance columns again from the look-up columns, and every
reference file a finance report; a part ticks ``cashback`` when it filled the
look-up columns, and any part listing no rows keeps the old default.

Revision ID: 5c1f0a9d7e42
Revises: 371dec14558c
"""

import json
from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "5c1f0a9d7e42"
down_revision: str | Sequence[str] | None = "371dec14558c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# What each finance column was filled with, by its place, and what found it.
_FINANCE_FIELDS = (
    "reward_amount",
    "vertical_name",
    "reward_code",
    "reward_type",
    "outcome",
)
_MATCH = "order_id"


def upgrade() -> None:
    _each(as_lookups)


def downgrade() -> None:
    _each(as_finance)


def _each(change: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
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


def as_lookups(config: dict[str, Any]) -> dict[str, Any]:
    """A setup with look-up columns; one that has them already is unchanged."""
    config = dict(config)
    inputs = config.get("inputs")
    finance: set[Any] = set()
    if isinstance(inputs, list):
        finance = {f.get("role") for f in inputs if f.get("kind") == "finance"}
        config["inputs"] = [
            {**f, "kind": "reference"} if f.get("kind") == "finance" else f
            for f in inputs
        ]
    output = config.get("output")
    if not isinstance(output, dict):
        return config
    tabs = output.get("tabs", [])
    # The copy the finance lookups read: the first copy of the finance report.
    copy = next(
        (
            t["key"]
            for t in tabs
            if t.get("source") is not None and t["source"] in finance
        ),
        "",
    )
    changed = []
    for tab in tabs:
        tab = dict(tab)
        if "columns" in tab:
            tab["columns"] = [
                {**column, "lookup": column.get("lookup")} for column in tab["columns"]
            ]
        old = "finance_columns" in tab or "cashback_key_column" in tab
        if old:
            finance_columns = tab.pop("finance_columns", [])
            key_column = tab.pop("cashback_key_column", None)
            key_value = tab.pop("cashback_key_value", None)
            tab["lookup_columns"] = [
                {
                    **column,
                    "lookup": {
                        "sheet": copy,
                        "match": _MATCH,
                        "value": field,
                        "key_column": key_column,
                        "key_value": key_value,
                    },
                }
                for column, field in zip(finance_columns, _FINANCE_FIELDS, strict=False)
            ]
        else:
            tab.setdefault("lookup_columns", [])
        if "sections" in tab:
            sections = []
            for section in tab["sections"]:
                section = dict(section)
                if "cashback" in section:
                    cashback = section.pop("cashback")
                    section["lookups"] = (
                        bool(cashback)
                        and section.get("kind") == "rows"
                        and bool(tab["lookup_columns"])
                    )
                else:
                    section.setdefault("lookups", False)
                sections.append(section)
            tab["sections"] = sections
        changed.append(tab)
    config["output"] = {**output, "tabs": changed}
    return config


def as_finance(config: dict[str, Any]) -> dict[str, Any]:
    """A setup with finance columns; one that has them already is unchanged."""
    config = dict(config)
    inputs = config.get("inputs")
    if isinstance(inputs, list):
        config["inputs"] = [
            {**f, "kind": "finance"} if f.get("kind") == "reference" else f
            for f in inputs
        ]
    output = config.get("output")
    if not isinstance(output, dict):
        return config
    changed = []
    for tab in output.get("tabs", []):
        tab = dict(tab)
        if "columns" in tab:
            tab["columns"] = [
                {k: v for k, v in column.items() if k != "lookup"}
                for column in tab["columns"]
            ]
        if "lookup_columns" in tab:
            lookup_columns = tab.pop("lookup_columns")
            first = (lookup_columns[0].get("lookup") or {}) if lookup_columns else {}
            tab["finance_columns"] = [
                {k: v for k, v in column.items() if k != "lookup"}
                for column in lookup_columns
            ]
            tab["cashback_key_column"] = first.get("key_column")
            tab["cashback_key_value"] = first.get("key_value")
        if "sections" in tab:
            sections = []
            for section in tab["sections"]:
                section = dict(section)
                if "lookups" in section:
                    # A part listing no rows never looked anything up; it
                    # carried the old default.
                    lookups = bool(section.pop("lookups"))
                    section["cashback"] = (
                        lookups if section.get("kind") == "rows" else True
                    )
                sections.append(section)
            tab["sections"] = sections
        changed.append(tab)
    config["output"] = {**output, "tabs": changed}
    return config
