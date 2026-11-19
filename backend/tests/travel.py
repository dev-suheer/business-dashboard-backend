"""Travel on the generic runner, for the tests: version 1 of its configuration,
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
from app.runner.inputs import complete, read_upload
from tests.conftest import TRAVEL_CLAIMS, TRAVEL_PORTAL, TRAVEL_WALLET


@cache
def travel_config() -> CategoryConfig:
    return load_seed("travel")


def sheet(role: str, key: str) -> InputSheet:
    found = travel_config().sheet(role, key)
    assert found is not None
    return found


def schema(role: str, key: str) -> SheetSchema:
    return schema_of(sheet(role, key))


def run_travel(claim: str, *, wallet: bool = True, **files: Path) -> RunResult:
    """Reconcile the sample portal against one sample claim, as a run does."""
    paths = {"portal": TRAVEL_PORTAL, "vendor": TRAVEL_CLAIMS[claim], **files}
    if wallet:
        paths["wallet"] = TRAVEL_WALLET
    config = travel_config()
    uploads = {role: Upload(role, path.name, path) for role, path in paths.items()}
    with use_config(config):
        return run(config, uploads, max_rows=1_000_000)


def report(
    result: RunResult, path: Path, sources: dict[str, Path] | None = None
) -> Path:
    with use_config(result.config):
        return generic_report(result, path, sources)


def booking(result: RunResult, booking_id: str) -> Entity:
    return next(b for b in result.entities if b.key == booking_id)


def read(role: str, path: Path) -> dict[str, list[Row]]:
    """One upload's rows by row set, as a run reads them."""
    spec = travel_config().input(role)
    assert spec is not None
    return read_upload(
        spec,
        Upload(role, path.name, path),
        max_rows=1_000_000,
        scope=_scope(),
        detected=[],
        exceptions=[],
    )


def _scope() -> Values:
    config = travel_config()
    return Values({"param": {p.key: p.typed() for p in config.parameters}})


def _row(role: str, key: str, values: dict[str, Any]) -> Row:
    """A row as the reader leaves it: its columns as given (unset money is 0,
    anything else blank), then the sheet's own fields."""
    columns = sheet(role, key).columns
    fields: dict[str, Any] = {
        c.field: Decimal(0) if c.type == "money" else None for c in columns
    }
    unknown = set(values) - set(fields)
    assert not unknown, f"no such {role} column: {unknown}"
    fields.update(values)
    fields["sheet"] = values.get("sheet", key)
    fields["row"] = Decimal(1)
    complete(sheet(role, key), fields, _scope())
    return Row(role, str(fields["sheet"]), 1, fields)


def portal_row(**values: Any) -> Row:
    return _row(
        "portal",
        "portal",
        {
            "sg_order_id": "100",
            "booking_id": "A260819100005500",
            "vertical": "Flights",
            "order_date": date(2026, 8, 19),
            "status": "Processed",
            "payment_status": "Paid",
            "payment_method": "Card",
            "order_total": Decimal("1000"),
            "total_amount": Decimal("1000"),
            "commission_charged": Decimal("30"),
            "net_payable": Decimal("970"),
            "card_amount": Decimal("1000"),
            **values,
        },
    )


def export_span(
    start: date = date(2026, 7, 26), end: date = date(2026, 8, 22)
) -> list[Row]:
    """Two unfinished portal rows with no booking id, unpaid and for nothing,
    which give the export its span and add no booking and no finding."""
    unbooked = {
        "booking_id": None,
        "status": "Pending",
        "payment_status": "Pending",
        "order_total": Decimal(0),
        "total_amount": Decimal(0),
        "commission_charged": Decimal(0),
        "net_payable": Decimal(0),
        "card_amount": Decimal(0),
    }
    return [
        portal_row(sg_order_id="1", order_date=start, **unbooked),
        portal_row(sg_order_id="2", order_date=end, **unbooked),
    ]


def claim_line(**values: Any) -> Row:
    return _row(
        "vendor",
        "claim",
        {
            "booking_ref": "A260819100005500_1",
            "invoice_no": "2500000001",
            "line_item": "000001",
            "billing_type": "Invoice",
            "service_type": "A",
            "issue_date": date(2026, 8, 19),
            "total_invoice": Decimal("1000"),
            "commission": Decimal("34.50"),
            "net_payable": Decimal("965.50"),
            **values,
        },
    )


def credit_line(**values: Any) -> Row:
    return claim_line(billing_type="Credit for Returns", line_item="000002", **values)


def reconcile_travel(
    portal: Sequence[Row] = (),
    claim: Sequence[Row] = (),
) -> RunResult:
    """Reconcile rows already read. The export's span is its rows' dates."""
    config = travel_config()
    with use_config(config):
        return reconcile(
            config, {"portal": list(portal), "claim": list(claim), "claim_periods": []}
        )
