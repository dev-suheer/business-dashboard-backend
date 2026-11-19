"""The Wallet sheet is a table of the setup.

It was written by its own writer from the run's wallet check. It is now a
plain table (``app/config_model/standard_tabs.py``), written exactly as before:
the check's rows (``wallet_checks``), then the history's movements no row had
(``wallet_unmatched``), then what the history held, each count a block cell
worked out on the run. Tables gain a part's own formats (``styles``, beside
its own ``values``) and a block cell worked out on the run (``value``), stored
here at their defaults. Nothing else in a setup is touched, so a category
rebuilt by hand keeps its own version 1.

Revision ID: 9160c93acf02
Revises: 03877eb0cfe4
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "9160c93acf02"
down_revision: str | Sequence[str] | None = "03877eb0cfe4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = '"money"'
_COUNTS = (
    ("Wallet-paid rows checked", "wallet_check.rows_checked"),
    ("Found, same amount", "wallet_check.matched"),
    ("Found, amount disagreed", "wallet_check.amount_mismatch"),
    ("Outside the history's window", "wallet_check.out_of_window"),
    ("Missing from the history", "wallet_check.missing"),
    ("Wallet-paid rows with no SG Order ID, not checked", "wallet_check.unkeyed"),
    ("History movements with no portal row", 'COUNTX("wallet_unmatched")'),
)
_COVERS = (
    "IF(AND(NOT(ISBLANK(wallet_check.window_start)), "
    "NOT(ISBLANK(wallet_check.window_end))), "
    'TEXT(wallet_check.window_start) & " to " & TEXT(wallet_check.window_end), '
    '"no dated movements")'
)


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
        "excel": None,
        "color": None,
        "style": style,
    }


def _cell(
    col: int, *, text: str | None = None, value: str | None = None
) -> dict[str, Any]:
    return {
        "col": col,
        "to_col": None,
        "text": text,
        "metric": None,
        "formula": None,
        "value": value,
        "format": None,
    }


def _label(text: str, value: str | None = None) -> dict[str, Any]:
    cells = [{**_cell(0, text=text), "format": "label"}]
    if value is not None:
        cells.append(_cell(2, value=value))
    return {"cells": cells, "height": None}


def _heading(text: str) -> dict[str, Any]:
    return {"cells": [{**_cell(0, text=text), "format": "title"}], "height": None}


def _section(kind: str, **fields: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "gap": 0,
        "when": None,
        "set": None,
        "upload": None,
        "where": "TRUE",
        "contacts": None,
        "title": None,
        "tolerances": True,
        "cashback": True,
        "autofilter": False,
        "label": None,
        "formats": {},
        "rows": [],
        "values": {},
        "styles": {},
        **fields,
    }


def wallet_table(key: str, title: str, requires: str | None) -> dict[str, Any]:
    """The Wallet table as the setup stores it, frozen as of this change."""
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
            _column("vertical", "Vertical", 10, "vertical", kind="string"),
            _column("sg_order_id", "SG Order ID", 12, "sg_order_id", kind="string"),
            _column("order", "Order", 22, "booking_id"),
            _column("order_date", "Order Date", 12, "order_date", kind="date"),
            _column(
                "portal_wallet",
                "Portal Wallet",
                14,
                "portal_wallet_amount",
                kind="money",
                style=_MONEY,
            ),
            _column(
                "history_paid",
                "History Paid",
                14,
                "history_paid",
                kind="money",
                style=_MONEY,
            ),
            _column(
                "history_refunded",
                "History Refunded",
                16,
                "history_refunded",
                kind="money",
                style=_MONEY,
            ),
            _column("movements", "Movements", 11, "events"),
            _column("state", "State", 16, "TEXT(state)", kind="string"),
            _column(
                "difference",
                "Difference",
                13,
                "diff",
                kind="money",
                style='IF(state = "amount_mismatch", "money_bad", "money")',
            ),
        ],
        "finance_columns": [],
        "sections": [
            _section("rows", set="wallet_checks", tolerances=False, cashback=False),
            _section(
                "block",
                gap=1,
                rows=[_heading("Wallet movements with no portal row")],
            ),
            _section(
                "rows",
                set="wallet_unmatched",
                tolerances=False,
                cashback=False,
                values={
                    "vertical": "vertical",
                    "sg_order_id": "sg_order_id",
                    "order_date": "event_date",
                    "portal_wallet": "amount",
                    "state": "IF(ISBLANK(message), BLANK(), message)",
                },
                styles={"portal_wallet": _MONEY},
            ),
            _section(
                "block",
                gap=1,
                rows=[
                    _heading("Wallet history"),
                    _label("History covers", _COVERS),
                    *(_label(text, value) for text, value in _COUNTS),
                ],
            ),
        ],
        "cashback_key_column": None,
        "cashback_key_value": None,
        "blocks": [],
        "breakdown": None,
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
    tabs = []
    for tab in output.get("tabs", []):
        if tab.get("kind") == "wallet":
            tabs.append(wallet_table(tab["key"], tab["title"], tab.get("requires")))
            continue
        if "sections" in tab:
            tab = {
                **tab,
                "sections": [
                    {
                        **section,
                        "styles": section.get("styles", {}),
                        "rows": [
                            {
                                **row,
                                "cells": [
                                    {**cell, "value": cell.get("value")}
                                    for cell in row.get("cells", [])
                                ],
                            }
                            for row in section.get("rows", [])
                        ],
                    }
                    for section in tab["sections"]
                ],
            }
        tabs.append(tab)
    return {**config, "output": {**output, "tabs": tabs}}


def as_sheet(config: dict[str, Any]) -> dict[str, Any]:
    """Back to the Wallet writer: a table that is the Wallet table as this
    change stored it. The new settings go."""
    output = config.get("output")
    if not isinstance(output, dict):
        return config
    tabs = []
    for tab in output.get("tabs", []):
        if tab == wallet_table(tab.get("key"), tab.get("title"), tab.get("requires")):
            tabs.append(_sheet(tab))
            continue
        if "sections" in tab:
            tab = {
                **tab,
                "sections": [
                    {
                        **{k: v for k, v in section.items() if k != "styles"},
                        "rows": [
                            {
                                **row,
                                "cells": [
                                    {k: v for k, v in cell.items() if k != "value"}
                                    for cell in row.get("cells", [])
                                ],
                            }
                            for row in section.get("rows", [])
                        ],
                    }
                    for section in tab["sections"]
                ],
            }
        tabs.append(tab)
    return {**config, "output": {**output, "tabs": tabs}}


def _sheet(tab: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": tab["key"],
        "title": tab["title"],
        "kind": "wallet",
        "requires": tab.get("requires"),
        "source": None,
        "sheet": None,
        "header_format": None,
        "header": "bands",
        "columns": [],
        "finance_columns": [],
        "sections": [],
        "cashback_key_column": None,
        "cashback_key_value": None,
        "blocks": [],
        "breakdown": None,
        "tolerances": {},
    }
