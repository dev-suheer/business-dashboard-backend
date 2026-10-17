"""The food reconciliation pipeline (plan-food.md section 5).

Read both workbooks, pair on order id, judge each pair, then summarise. Dates
are not a match condition. Nothing is persisted.
"""

import logging
from dataclasses import dataclass

from app.recon.categories.food import parser, rules
from app.recon.categories.food.models import (
    FoodPortalRow,
    FoodResult,
    FoodVendorRow,
)
from app.recon.excel import detect_sheets, open_workbook
from app.recon.exceptions import sort_exceptions
from app.recon.inputs import ReconInputError, Upload
from app.recon.models import SheetManifest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FoodRequest:
    portal: Upload
    vendor: Upload
    max_rows: int


def run(request: FoodRequest) -> FoodResult:
    manifest: list[SheetManifest] = []
    portal_rows = _load_portal(request, manifest)
    vendor_rows = _load_vendor(request, manifest)
    orders, exceptions = rules.reconcile(portal_rows, vendor_rows)
    exceptions = sort_exceptions(exceptions)
    summary = rules.summarise(portal_rows, vendor_rows, orders, exceptions)
    providers = {
        row.provider
        for row in portal_rows
        if row.provider and rules.is_delivered(row.order_status)
    }
    vendor_name = providers.pop() if len(providers) == 1 else None
    logger.info(
        "food recon settled %s of %s orders, due %s",
        summary.settled_orders,
        summary.paired,
        summary.settled_due,
    )
    return FoodResult(
        vendor_name=vendor_name,
        orders=orders,
        exceptions=exceptions,
        detected=manifest,
        summary=summary,
    )


def _load_portal(
    request: FoodRequest, manifest: list[SheetManifest]
) -> list[FoodPortalRow]:
    upload = request.portal
    rows: list[FoodPortalRow] = []
    with open_workbook(upload.path) as workbook:
        sheets = detect_sheets(workbook, parser.PORTAL_SCHEMA)
        if not sheets:
            raise ReconInputError(
                f"No {parser.PORTAL_SCHEMA.name} sheet found in {upload.filename!r} "
                f"uploaded as {upload.role!r}."
            )
        for row in parser.read_portal(
            workbook, sheets, source=upload.filename, max_rows=request.max_rows
        ):
            rows.append(row)
    if not rows:
        raise ReconInputError("The portal export contained no food orders")
    manifest.append(
        SheetManifest(
            role="portal",
            filename=upload.filename,
            sheets=sheets,
            rows=len(rows),
        )
    )
    return rows


def _load_vendor(
    request: FoodRequest, manifest: list[SheetManifest]
) -> list[FoodVendorRow]:
    upload = request.vendor
    rows: list[FoodVendorRow] = []
    with open_workbook(upload.path) as workbook:
        sheets = detect_sheets(workbook, parser.VENDOR_SCHEMA)
        if not sheets:
            raise ReconInputError(
                f"No {parser.VENDOR_SCHEMA.name} sheet found in {upload.filename!r} "
                f"uploaded as {upload.role!r}."
            )
        for row in parser.read_vendor(
            workbook, sheets, source=upload.filename, max_rows=request.max_rows
        ):
            rows.append(row)
    if not rows:
        raise ReconInputError("The vendor report contained no orders")
    manifest.append(
        SheetManifest(
            role="vendor",
            filename=upload.filename,
            sheets=sheets,
            rows=len(rows),
        )
    )
    return rows
