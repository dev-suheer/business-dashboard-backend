"""eShop on the generic runner, for the tests: version 1 of its configuration,
a run on the sample files, rows built by hand, and the report."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from app.config_model.models import CategoryConfig, InputSheet
from app.config_model.runtime import schema_of, use_config
from app.config_model.seed import load_seed
from app.expr import Values
from app.recon.excel import SheetSchema
from app.recon.inputs import Upload
from app.render import generic_report
from app.runner import Entity, Row, RunResult, reconcile, run
from app.runner.inputs import READERS, complete, read_upload
from tests.conftest import VENDOR_STATEMENT, WORKINGS


@cache
def eshop_config() -> CategoryConfig:
    return load_seed("eshop")


def sheet(role: str, key: str) -> InputSheet:
    found = eshop_config().sheet(role, key)
    assert found is not None
    return found


def schema(role: str, key: str) -> SheetSchema:
    return schema_of(sheet(role, key))


SAMPLE = {"portal": WORKINGS, "promo": WORKINGS, "vendor": VENDOR_STATEMENT}


def run_eshop(
    *,
    overrides: dict[str, Any] | None = None,
    **files: Path,
) -> RunResult:
    """Reconcile the uploads, by role, as a run does: by default the sample
    cycle, whose one workbook holds both the portal export and the promo
    catalogue."""
    config = eshop_config()
    paths = {**SAMPLE, **files}
    uploads = {role: Upload(role, path.name, path) for role, path in paths.items()}
    with use_config(config):
        return run(config, uploads, overrides=overrides, max_rows=1_000_000)


def report(
    result: RunResult, path: Path, sources: dict[str, Path] | None = None
) -> Path:
    with use_config(result.config):
        return generic_report(result, path, sources)


def order(result: RunResult, order_no: str) -> Entity:
    return next(o for o in result.entities if o.key == order_no)


def orders(result: RunResult) -> list[Entity]:
    """The portal's orders, as the report lists them."""
    return [o for o in result.entities if o.values["n_lines"] > 0]


def read(role: str, path: Path, max_rows: int = 1_000_000) -> dict[str, list[Row]]:
    """One upload's rows by row set, as a run reads them."""
    spec = eshop_config().input(role)
    assert spec is not None
    return read_upload(
        spec,
        Upload(role, path.name, path),
        max_rows=max_rows,
        scope=_scope(),
        detected=[],
        exceptions=[],
    )


def _scope() -> Values:
    config = eshop_config()
    return Values({"param": {p.key: p.typed() for p in config.parameters}})


def _row(role: str, key: str, values: dict[str, Any]) -> Row:
    """A row as the reader leaves it: its cells as given, read as their
    columns are (an unset one is blank), then the sheet's own fields."""
    columns = sheet(role, key).columns
    unknown = set(values) - {c.field for c in columns}
    assert not unknown, f"no such {role} column: {unknown}"
    fields: dict[str, Any] = {
        c.field: READERS[c.type](values.get(c.field)) for c in columns
    }
    fields["sheet"] = key
    fields["row"] = Decimal(1)
    complete(sheet(role, key), fields, _scope())
    return Row(role, key, 1, fields)


def portal_line(order_ref: str = "SG-SH-00001", **values: Any) -> Row:
    return _row(
        "portal",
        "portal",
        {
            "order_ref": order_ref,
            "order_date": date(2026, 8, 1),
            "merchant_id": "34",
            "sku": "SKU-1",
            "gmv": Decimal("100"),
            "actual_commission": Decimal("15"),
            "status": "Delivered",
            **values,
        },
    )


def statement_row(**values: Any) -> Row:
    return _row(
        "vendor",
        "ledger",
        {
            "doc_type": "فاتورة بيع أجل",
            "doc_date": date(2026, 8, 2),
            "raw_desc": "طلب SG-SH-00001",
            "doc_no": "INV-1",
            "debit": Decimal("100"),
            **values,
        },
    )


def promo_row(sku: str) -> Row:
    return _row("promo", "promo", {"sku": sku})


def settlement_row(order_no: str, amount_paid: Decimal) -> Row:
    return _row(
        "settlements",
        "settlements",
        {"order_no": order_no, "amount_paid": amount_paid},
    )


def reconcile_eshop(
    portal: Sequence[Row] = (),
    statement: Sequence[Row] = (),
    *,
    promo: Sequence[Row] = (),
    settlements: Sequence[Row] = (),
    overrides: dict[str, Any] | None = None,
) -> RunResult:
    """Reconcile rows already read."""
    config = eshop_config()
    with use_config(config):
        return reconcile(
            config,
            {
                "portal": list(portal),
                "statement": list(statement),
                "promo": list(promo),
                "settlements": list(settlements),
            },
            overrides=overrides,
        )
