"""The Exceptions sheet is a table of the setup.

It was written by its own writer, with its Status column worked out by the
workbook's ``exception_status``. It is now a plain table over the run's
exceptions (``app/config_model/standard_tabs.py``), written exactly as before,
and its Status column carries that formula. Tables gain two settings, a
header row (``header``) and a column's own format (``style``), stored here at
their defaults on every tab and column. Nothing else in a setup is touched,
so a category rebuilt by hand keeps its own version 1.

Revision ID: 7e5383595ede
Revises: b47d34f1ac0e
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "7e5383595ede"
down_revision: str | Sequence[str] | None = "b47d34f1ac0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEVERITY_STYLE = 'IF(severity = "high", "high", "info")'
_EMPTY_STATUS = '""'


def _column(
    key: str,
    header: str,
    width: int,
    value: str,
    kind: str = "text",
    style: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "header": header,
        "width": width,
        "group": "meta",
        "kind": kind,
        "value": value,
        "color": None,
        "style": style,
    }


def exceptions_table(
    key: str, title: str, requires: str | None, status: str
) -> dict[str, Any]:
    """The Exceptions table as the setup stores it, frozen as of this change."""
    return {
        "key": key,
        "title": title,
        "kind": "table",
        "requires": requires,
        "source": None,
        "sheet": None,
        "header_format": None,
        "header": "plain",
        "columns": [
            _column(
                "severity", "Severity", 12, "TEXT(severity)", style=_SEVERITY_STYLE
            ),
            _column("code", "Code", 32, "TEXT(code)", style=_SEVERITY_STYLE),
            _column("order_no", "Order", 18, "order_no"),
            _column("status", "Status", 36, f"TEXT({status})", style=_SEVERITY_STYLE),
            _column("amount", "Amount", 16, "amount", kind="money", style='"money"'),
            _column("detail", "Detail", 88, "detail"),
        ],
        "finance_columns": [],
        "sections": [
            {
                "kind": "rows",
                "gap": 0,
                "when": None,
                "set": "exceptions",
                "title": None,
                "tolerances": False,
                "cashback": False,
                "autofilter": False,
                "label": None,
                "formats": {},
                "rows": [],
                "values": {},
            }
        ],
        "cashback_key_column": None,
        "cashback_key_value": None,
        "blocks": [],
        "breakdown": None,
        "invoices": None,
        "tolerances": {},
    }


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


def as_table(config: dict[str, Any]) -> dict[str, Any]:
    output = config.get("output")
    if not isinstance(output, dict):
        return config
    output = dict(output)
    formula = output.pop("exception_status", None)
    status = formula if formula is not None else _EMPTY_STATUS
    tabs = []
    for tab in output.get("tabs", []):
        if tab.get("kind") == "exceptions":
            tabs.append(
                exceptions_table(tab["key"], tab["title"], tab.get("requires"), status)
            )
            continue
        tab = {**tab}
        tab.setdefault("header", "bands")
        for name in ("columns", "finance_columns"):
            if name in tab:
                tab[name] = [{**c, "style": c.get("style")} for c in tab[name]]
        tabs.append(tab)
    output["tabs"] = tabs
    return {**config, "output": output}


def as_sheet(config: dict[str, Any]) -> dict[str, Any]:
    """Back to the Exceptions writer: a tab that is the Exceptions table as
    this change stored it, and its Status formula. The new settings go."""
    output = config.get("output")
    if not isinstance(output, dict):
        return config
    output = dict(output)
    status: str | None = None
    tabs = []
    for tab in output.get("tabs", []):
        found = _status_of(tab)
        if found is not None:
            if status is None:
                status = found
            tabs.append(_sheet(tab))
            continue
        tab = {k: v for k, v in tab.items() if k != "header"}
        for name in ("columns", "finance_columns"):
            if name in tab:
                tab[name] = [
                    {k: v for k, v in c.items() if k != "style"} for c in tab[name]
                ]
        tabs.append(tab)
    output["tabs"] = tabs
    output["exception_status"] = status if status != _EMPTY_STATUS else None
    return {**config, "output": output}


def _status_of(tab: dict[str, Any]) -> str | None:
    """The Status formula of a tab that is the Exceptions table, else None."""
    columns = tab.get("columns") or []
    if len(columns) != 6 or tab.get("kind") != "table":
        return None
    value = columns[3].get("value") or ""
    if not (value.startswith("TEXT(") and value.endswith(")")):
        return None
    status = value[len("TEXT(") : -1]
    template = exceptions_table(tab["key"], tab["title"], tab.get("requires"), status)
    return status if tab == template else None


def _sheet(tab: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": tab["key"],
        "title": tab["title"],
        "kind": "exceptions",
        "requires": tab.get("requires"),
        "source": None,
        "sheet": None,
        "header_format": None,
        "columns": [],
        "finance_columns": [],
        "sections": [],
        "cashback_key_column": None,
        "cashback_key_value": None,
        "blocks": [],
        "breakdown": None,
        "invoices": None,
        "tolerances": {},
    }
