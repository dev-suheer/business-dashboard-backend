"""A dashboard's breakdown table is designed column by column.

The breakdown had ten fixed columns (orders, discounts, revenue, the Apple Pay,
Tamara and card shares, wallet, commission and one more), with their headers
on the tab. Each column is now configured: what it adds up, where, and how it
is written (``app/config_model/models.py`` BreakdownColumn). Every stored
version is rewritten to the same ten columns, which give the same table: the
card share goes to the column its payment method names, Tamara first, then
Apple Pay, then card, as before, and the rows are still ranked by the orders.
Version 1 of each category is brought up to date with app/seed/v1, which says
exactly that, as the earlier migrations did.

Revision ID: 099107642693
Revises: 565e1ebb7b25
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "099107642693"
down_revision: str | Sequence[str] | None = "565e1ebb7b25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_DIR = Path(__file__).resolve().parents[2] / "app" / "seed" / "v1"

# The fixed fields, with what each was when it was not given.
_FIXED: dict[str, Any] = {
    "commission": None,
    "extra": None,
    "paid": "TRUE",
    "revenue": "0",
    "discounted": "FALSE",
    "payment_method": "BLANK()",
    "card": "0",
    "wallet": "0",
}


def upgrade() -> None:
    connection = op.get_bind()
    for path in sorted(SEED_DIR.glob("*.json")):
        config = json.loads(path.read_text())
        connection.execute(
            sa.text(
                "update category_version set config = cast(:config as jsonb) "
                "where number = 1 and category_id = "
                "(select id from category where key = :key)"
            ),
            {"config": json.dumps(config), "key": config["category"]},
        )
    rows = connection.execute(
        sa.text("select id, config from category_version where number > 1")
    )
    for version_id, config in rows.fetchall():
        changed = designed(config)
        if changed is not None:
            connection.execute(
                sa.text(
                    "update category_version set config = cast(:config as jsonb) "
                    "where id = :id"
                ),
                {"config": json.dumps(changed), "id": version_id},
            )


def downgrade() -> None:
    # The fixed columns cannot hold a breakdown designed since.
    pass


def designed(config: dict[str, Any]) -> dict[str, Any] | None:
    """The configuration with each breakdown in columns; None when it is so already."""
    changed = False
    for tab in config.get("output", {}).get("tabs", []):
        headers = tab.pop("breakdown_headers", None)
        changed = changed or headers is not None
        spec = tab.get("breakdown")
        if spec is None or "commission" not in spec:
            continue
        tab["breakdown"] = _columns(spec, list(headers or []))
        changed = True
    return config if changed else None


def _columns(spec: dict[str, Any], headers: list[str]) -> dict[str, Any]:
    fixed: dict[str, Any] = {n: spec.get(n, default) for n, default in _FIXED.items()}
    headers += [""] * (10 - len(headers))
    paid, method = fixed["paid"], fixed["payment_method"]
    tamara = f'CONTAINS({method}, "tamara")'
    apple = f'CONTAINS({method}, "apple")'
    named_card = f'CONTAINS({method}, "card")'

    def column(
        header: str, add: str, value: str | None, when: str, kind: str
    ) -> dict[str, Any]:
        return {
            "header": header,
            "add": add,
            "value": value,
            "when": when,
            "kind": kind,
        }

    columns = [
        column(headers[1], "count", None, "TRUE", "int"),
        column(headers[2], "count", None, _all(paid, fixed["discounted"]), "int"),
        column(headers[3], "sum", fixed["revenue"], _all(paid), "money"),
        column(
            headers[4],
            "sum",
            fixed["card"],
            _all(paid, apple, f"NOT({tamara})"),
            "money",
        ),
        column(headers[5], "sum", fixed["card"], _all(paid, tamara), "money"),
        column(
            headers[6],
            "sum",
            fixed["card"],
            _all(paid, named_card, f"NOT({tamara})", f"NOT({apple})"),
            "money",
        ),
        column(headers[7], "sum", fixed["wallet"], _all(paid), "money"),
        column(headers[8], "sum", fixed["commission"], "TRUE", "money"),
        column(headers[9], "sum", fixed["extra"], "TRUE", "money"),
    ]
    kept = {k: v for k, v in spec.items() if k not in _FIXED and k != "total"}
    return {
        **kept,
        "name_header": headers[0],
        "columns": columns,
        "rank_by": 0,
        "total": spec.get("total", "sum"),
    }


def _all(*conditions: str) -> str:
    held = [c for c in conditions if c != "TRUE"]
    if not held:
        return "TRUE"
    return held[0] if len(held) == 1 else f"AND({', '.join(held)})"
