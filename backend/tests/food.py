"""Food on the generic runner, for the tests: version 1 of its configuration,
a run on the sample files, rows built by hand, and the report."""

from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from app.config_model.models import CategoryConfig, InputSheet
from app.config_model.runtime import schema_of, use_config
from app.config_model.seed import load_seed
from app.recon.excel import SheetSchema
from app.recon.inputs import Upload
from app.render import generic_report
from app.runner import Row, RunResult, reconcile, run


@cache
def food_config() -> CategoryConfig:
    return load_seed("food")


def _sheet(role: str, key: str) -> InputSheet:
    sheet = food_config().sheet(role, key)
    assert sheet is not None
    return sheet


def portal_schema() -> SheetSchema:
    return schema_of(_sheet("portal", "portal"))


def vendor_schema() -> SheetSchema:
    return schema_of(_sheet("vendor", "orders"))


def run_food(**files: Path) -> RunResult:
    """Reconcile the uploads, by role, as a run does."""
    config = food_config()
    uploads = {role: Upload(role, path.name, path) for role, path in files.items()}
    with use_config(config):
        return run(config, uploads, max_rows=10_000)


def reconcile_food(portal: list[Row], vendor: list[Row]) -> RunResult:
    """Reconcile rows already read."""
    config = food_config()
    with use_config(config):
        return reconcile(config, {"portal": portal, "vendor": vendor})


def report(
    result: RunResult, path: Path, sources: dict[str, Path] | None = None
) -> Path:
    with use_config(result.config):
        return generic_report(result, path, sources)


def _row(role: str, key: str, values: dict[str, Any]) -> Row:
    """A row as the reader types it: unset money is 0, anything else blank."""
    columns = _sheet(role, key).columns
    fields = {c.field: Decimal(0) if c.type == "money" else None for c in columns}
    unknown = set(values) - set(fields)
    assert not unknown, f"no such {role} field: {unknown}"
    fields.update(values)
    return Row(role, key, 1, fields)


def portal_row(order_id: str, **values: Any) -> Row:
    return _row(
        "portal",
        "portal",
        {
            "order_id": order_id,
            "order_date": date(2026, 9, 2),
            "order_status": "Delivered",
            "vertical": "Food",
            "sub_total": Decimal(39),
            "sg_delivery": Decimal(2),
            "total_amount": Decimal(41),
            "commission_charged": Decimal("1.95"),
            **values,
        },
    )


def vendor_row(order_id: str, **values: Any) -> Row:
    return _row(
        "vendor",
        "orders",
        {
            "order_id": order_id,
            "requested_delivery_date": date(2026, 9, 3),
            "order_status": "Delivered",
            "total": Decimal(41),
            "order_chef_total": Decimal(39),
            "shoppinggate_fees": Decimal("1.95"),
            "original_delivery_fee": Decimal(17),
            "sg_delivery_charge": Decimal("6.90"),
            "chef_delivery_charge": Decimal("8.10"),
            **values,
        },
    )
