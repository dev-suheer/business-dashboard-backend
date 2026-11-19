"""The B2C Invoices ERP sheet is a table of the setup.

It was written by its own writer from the tab's ``invoices`` settings. It is
now a plain table (``app/config_model/standard_tabs.py``), written exactly as
before: its settings are its rows (a set, or an upload's own rows), its filter,
its contacts and its columns' formulas, and its unit price an Excel formula
pointing at the row's cell on the table listing the set, or on the upload's
copy. Tables gain an upload's rows, a filter and contacts on each rows part
(``upload``, ``where``, ``contacts``) and an Excel formula on each column
(``excel``), stored here at their defaults. Nothing else in a setup is
touched, so a category rebuilt by hand keeps its own version 1.

Revision ID: 03877eb0cfe4
Revises: 7e5383595ede
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "03877eb0cfe4"
down_revision: str | Sequence[str] | None = "7e5383595ede"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEYS = (
    "partner",
    "order_id",
    "contact",
    "bill_date",
    "product",
    "quantity",
    "unit_price",
    "taxes",
)


def _column(
    key: str,
    header: str,
    width: int,
    value: str | None,
    kind: str = "text",
    style: str | None = None,
    excel: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "header": header,
        "width": width,
        "group": "meta",
        "kind": kind,
        "value": value,
        "excel": excel,
        "color": None,
        "style": style,
    }


def erp_table(
    key: str,
    title: str,
    requires: str | None,
    *,
    unit_cell: str,
    partner: str,
    order_id: str,
    bill_date: str,
    product: str,
    rows: str | None = None,
    upload: str | None = None,
    where: str = "TRUE",
    contact: str = "BLANK()",
    contacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The B2C Invoices ERP table as the setup stores it, frozen as of this
    change."""
    named = f"COALESCE(contact, {contact})" if contacts is not None else contact
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
            _column("partner", "Partner", 16, f"TEXT({partner})"),
            _column(
                "order_id",
                "Invoice Partner Display Name",
                28,
                f"TEXT({order_id})",
                kind="id",
            ),
            _column("contact", "Contact Name", 24, f"TEXT({named})"),
            _column("bill_date", "Invoice/Bill Date", 18, bill_date, kind="datetime"),
            _column(
                "product",
                "Invoice lines/Product",
                18,
                f"TEXT({product})",
                kind="string",
            ),
            _column("quantity", "Invoice lines/Quantity", 22, "1"),
            _column(
                "unit_price",
                "Invoice lines/Unit Price",
                24,
                None,
                kind="money",
                style='"money"',
                excel=f"={{cell:{unit_cell}}}/1.15",
            ),
            _column(
                "taxes",
                "Invoice lines/Taxes",
                26,
                '"Domestic Sales (15%)"',
                kind="string",
            ),
        ],
        "finance_columns": [],
        "sections": [
            {
                "kind": "rows",
                "gap": 0,
                "when": None,
                "set": rows,
                "upload": upload,
                "where": where,
                "contacts": contacts,
                "title": None,
                "tolerances": False,
                "cashback": False,
                "autofilter": True,
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


# --- Up ---------------------------------------------------------------------------


def as_table(config: dict[str, Any]) -> dict[str, Any]:
    output = config.get("output")
    if not isinstance(output, dict):
        return config
    tabs = output.get("tabs", [])
    changed = []
    for tab in tabs:
        if tab.get("kind") == "erp_invoices":
            changed.append(_erp_from(tab, tabs))
            continue
        tab = {k: v for k, v in tab.items() if k != "invoices"}
        for name in ("columns", "finance_columns"):
            if name in tab:
                tab[name] = [{**c, "excel": c.get("excel")} for c in tab[name]]
        if "sections" in tab:
            tab["sections"] = [
                {
                    **s,
                    "upload": s.get("upload"),
                    "where": s.get("where", "TRUE"),
                    "contacts": s.get("contacts"),
                }
                for s in tab["sections"]
            ]
        changed.append(tab)
    return {**config, "output": {**output, "tabs": changed}}


def _erp_from(tab: dict[str, Any], tabs: list[dict[str, Any]]) -> dict[str, Any]:
    """The table for an ERP invoices tab: its lines, now its rows."""
    spec = tab.get("invoices") or {}
    upload = spec.get("upload")
    rows = None if upload is not None else spec.get("set")
    if upload is not None:
        copy = next(
            (
                t["key"]
                for t in tabs
                if t.get("kind") == "source_copy" and t.get("source") == upload
            ),
            "",
        )
        unit_cell = f"{copy}.{spec.get('unit_header') or ''}"
    else:
        lister = next(
            (
                t["key"]
                for t in tabs
                if t.get("kind") == "table"
                and any(
                    s.get("kind") == "rows" and s.get("set") == rows
                    for s in t.get("sections", [])
                )
            ),
            "",
        )
        unit_cell = f"{lister}.{spec.get('unit_column') or ''}"
    order_id = spec.get("order_id") or '""'
    contacts: dict[str, Any] | None = None
    source = spec.get("contact_source")
    # An upload's own lines never had contacts.
    if source is not None and upload is None:
        if spec.get("contact_key") is not None:
            contacts = {
                "upload": source,
                "key": spec["contact_key"],
                "match": f"TEXT({order_id})",
                "row": None,
            }
        elif spec.get("contact_row") is not None:
            contacts = {
                "upload": source,
                "key": None,
                "match": None,
                "row": spec["contact_row"],
            }
    return erp_table(
        tab["key"],
        tab["title"],
        tab.get("requires"),
        unit_cell=unit_cell,
        partner=spec.get("partner") or '""',
        order_id=order_id,
        bill_date=spec.get("bill_date") or "BLANK()",
        product=spec.get("product") or '""',
        rows=rows,
        upload=upload,
        where=spec.get("where") or "TRUE",
        contact=spec.get("contact") or "BLANK()",
        contacts=contacts,
    )


# --- Down -------------------------------------------------------------------------


def as_sheet(config: dict[str, Any]) -> dict[str, Any]:
    """Back to the ERP writer: a table that is the ERP table as this change
    stored it, and its settings. The new settings go."""
    output = config.get("output")
    if not isinstance(output, dict):
        return config
    changed = []
    for tab in output.get("tabs", []):
        spec = _spec_of(tab)
        if spec is not None:
            changed.append(_sheet(tab, spec))
            continue
        tab = {**tab, "invoices": None}
        for name in ("columns", "finance_columns"):
            if name in tab:
                tab[name] = [
                    {k: v for k, v in c.items() if k != "excel"} for c in tab[name]
                ]
        if "sections" in tab:
            tab["sections"] = [
                {k: v for k, v in s.items() if k not in ("upload", "where", "contacts")}
                for s in tab["sections"]
            ]
        changed.append(tab)
    return {**config, "output": {**output, "tabs": changed}}


def _unwrap(value: str | None) -> str | None:
    if value is None or not (value.startswith("TEXT(") and value.endswith(")")):
        return None
    return value[len("TEXT(") : -1]


def _spec_of(tab: dict[str, Any]) -> dict[str, Any] | None:
    """The ERP settings of a tab that is the ERP table, else None."""
    columns = tab.get("columns") or []
    sections = tab.get("sections") or []
    if (
        tab.get("kind") != "table"
        or tuple(c.get("key") for c in columns) != _KEYS
        or len(sections) != 1
    ):
        return None
    part = sections[0]
    values = {c["key"]: c.get("value") for c in columns}
    excel = columns[6].get("excel") or ""
    if not (excel.startswith("={cell:") and excel.endswith("}/1.15")):
        return None
    unit_tab, _, unit_name = excel[len("={cell:") : -len("}/1.15")].partition(".")
    partner, order_id, product = (
        _unwrap(values["partner"]),
        _unwrap(values["order_id"]),
        _unwrap(values["product"]),
    )
    named = _unwrap(values["contact"])
    contacts = part.get("contacts")
    contact = named
    if contacts is not None and named is not None:
        if not (named.startswith("COALESCE(contact, ") and named.endswith(")")):
            return None
        contact = named[len("COALESCE(contact, ") : -1]
    if partner is None or order_id is None or product is None or contact is None:
        return None
    if contacts is not None and contacts.get("key") is not None:
        # The old lines matched a contact's key by their own order id.
        if contacts.get("match") != f"TEXT({order_id})":
            return None
    upload = part.get("upload")
    spec = {
        "set": part.get("set"),
        "where": part.get("where", "TRUE"),
        "partner": partner,
        "order_id": order_id,
        "contact": contact,
        "contact_source": contacts["upload"] if contacts else None,
        "contact_key": contacts["key"] if contacts else None,
        "contact_row": contacts["row"] if contacts else None,
        "upload": upload,
        "unit_header": unit_name if upload is not None else None,
        "bill_date": values["bill_date"],
        "product": product,
        "unit_column": None if upload is not None else unit_name,
    }
    rebuilt = erp_table(
        tab["key"],
        tab["title"],
        tab.get("requires"),
        unit_cell=f"{unit_tab}.{unit_name}",
        partner=partner,
        order_id=order_id,
        bill_date=values["bill_date"],
        product=product,
        rows=part.get("set"),
        upload=upload,
        where=part.get("where", "TRUE"),
        contact=contact,
        contacts=contacts,
    )
    return spec if rebuilt == tab else None


def _sheet(tab: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": tab["key"],
        "title": tab["title"],
        "kind": "erp_invoices",
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
        "invoices": spec,
        "tolerances": {},
    }
