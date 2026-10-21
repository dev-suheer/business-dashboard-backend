"""The reconciliation pipeline (plan.md section 5).

Parse, extract, resolve, apply rules, match, check the wallet when a history
was uploaded, summarise. Every stage streams: nothing here holds a whole
sheet, and the portal export — by far the largest input — is reduced to
order-level state as it is read.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.recon.categories.base import Accumulator, CategoryAdapter
from app.recon.cleaning import money
from app.recon.excel import detect_sheet, detect_sheets, open_workbook
from app.recon.exceptions import (
    ExceptionCode,
    ReconException,
    make_exception,
    sort_exceptions,
)
from app.recon.inputs import ReconInputError as ReconInputError
from app.recon.inputs import Upload as Upload
from app.recon.matcher import group_exceptions, match
from app.recon.models import (
    LedgerEntry,
    LineVariance,
    OrderRecon,
    OrderStatus,
    RateConfig,
    ReconResult,
    Settlement,
    SheetManifest,
    Summary,
    VarianceClass,
)
from app.recon.registry import get_adapter
from app.recon.wallet import (
    WalletPayment,
    WalletReconciliation,
    load_wallet,
    reconcile_wallet,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconRequest:
    category: str
    portal: Upload
    vendor: Upload
    promo: Upload
    settlements: Upload | None
    rates: RateConfig
    period_start: date | None
    period_end: date | None
    max_rows: int
    wallet: Upload | None = None


def reconcile(request: ReconRequest) -> ReconResult:
    adapter = get_adapter(request.category)
    manifest: list[SheetManifest] = []

    promo_skus = _load_promo(adapter, request, manifest)
    settlements = _load_settlements(adapter, request, manifest)
    orders, accumulators, line_variances, lines_read = _load_portal(
        adapter, request, promo_skus, settlements, manifest
    )
    if not orders:
        raise ReconInputError("The portal export contained no usable order lines")
    entries = _load_ledger(adapter, request, manifest)

    derived_start, derived_end = _derive_period(orders)
    period_start = request.period_start or derived_start
    period_end = request.period_end or derived_end
    if period_start > period_end:
        raise ReconInputError("period_start is after period_end")

    outcome = match(
        orders,
        entries,
        period_start=period_start,
        period_end=period_end,
        order_tolerance=request.rates.order_tolerance,
    )

    exceptions = outcome.exceptions + _order_exceptions(orders, settlements)
    wallet: WalletReconciliation | None = None
    if request.wallet is not None:
        wallet, wallet_exceptions = _check_wallet(
            adapter, request, request.wallet, orders, manifest
        )
        exceptions += wallet_exceptions
    summary = _build_summary(
        orders=orders,
        accumulators=accumulators,
        entries=entries,
        lines_read=lines_read,
        closing_balance=outcome.closing_balance,
        exceptions=exceptions,
    )

    merchant_ids = {o.merchant_id for o in orders if o.merchant_id}
    ordered: list[OrderRecon] = sorted(orders, key=lambda order: order.order_no)
    return ReconResult(
        category=adapter.name,
        merchant_id=next(iter(merchant_ids), None),
        period_start=period_start,
        period_end=period_end,
        rates=request.rates,
        orders=ordered,
        unmatched_ledger=outcome.unmatched,
        line_variances=line_variances,
        exceptions=sort_exceptions(exceptions),
        detected=manifest,
        summary=summary,
        wallet=wallet,
    )


def _derive_period(orders: list[OrderRecon]) -> tuple[date, date]:
    """Infer the cycle from portal order dates (plan.md section 3.5).

    Clawback orders are left out: an RTO already settled in an earlier cycle is
    carried into this one deliberately, and its order date belongs to the earlier
    period. Including it would stretch the window backwards by weeks and then
    report every prior-period statement row as missing from the portal.
    """
    current = [o for o in orders if "CLAWBACK" not in o.flags] or orders
    return (
        min(o.order_date for o in current),
        max(o.order_date for o in current),
    )


def _require_sheet(upload: Upload, schema_name: str, sheet: str | None) -> str:
    if sheet is None:
        raise ReconInputError(
            f"No {schema_name} sheet found in {upload.filename!r} uploaded as "
            f"{upload.role!r}. Check that the right file went into that field."
        )
    return sheet


def _load_promo(
    adapter: CategoryAdapter[Any], request: ReconRequest, manifest: list[SheetManifest]
) -> frozenset[str]:
    upload = request.promo
    with open_workbook(upload.path) as workbook:
        sheet = _require_sheet(
            upload,
            adapter.promo_schema.name,
            detect_sheet(workbook, adapter.promo_schema),
        )
        skus: set[str] = set()
        rows = 0
        for promo in adapter.read_promo(workbook, sheet, max_rows=request.max_rows):
            rows += 1
            skus.add(promo.sku)
        _note(manifest, upload, [sheet], rows)
    return frozenset(skus)


def _load_settlements(
    adapter: CategoryAdapter[Any], request: ReconRequest, manifest: list[SheetManifest]
) -> dict[str, Settlement]:
    upload = request.settlements
    if upload is None:
        return {}
    with open_workbook(upload.path) as workbook:
        sheet = _require_sheet(
            upload,
            adapter.settlement_schema.name,
            detect_sheet(workbook, adapter.settlement_schema),
        )
        settlements = {
            s.order_no: s
            for s in adapter.read_settlements(
                workbook, sheet, max_rows=request.max_rows
            )
        }
        _note(manifest, upload, [sheet], len(settlements))
    return settlements


def _load_portal(
    adapter: CategoryAdapter[Any],
    request: ReconRequest,
    promo_skus: frozenset[str],
    settlements: dict[str, Settlement],
    manifest: list[SheetManifest],
) -> tuple[list[OrderRecon], dict[str, Accumulator], list[LineVariance], int]:
    """Stream the portal export straight into order-level accumulators.

    Per-line records are kept only for lines that breach the commission
    tolerance, so memory scales with the number of problems rather than with the
    number of rows.
    """
    from app.recon.categories.eshop.rules import line_variance

    upload = request.portal
    accumulators: dict[str, Accumulator] = {}
    variances = []
    lines_read = 0
    merchant_ids: set[str] = set()

    with open_workbook(upload.path) as workbook:
        sheet = _require_sheet(
            upload,
            adapter.portal_schema.name,
            detect_sheet(workbook, adapter.portal_schema),
        )
        for raw in adapter.read_portal(workbook, sheet, max_rows=request.max_rows):
            line = adapter.evaluate_line(raw, promo_skus, request.rates)
            lines_read += 1
            if line.merchant_id:
                merchant_ids.add(line.merchant_id)
            acc = accumulators.get(line.order_no)
            if acc is None:
                acc = adapter.new_accumulator(line)
                accumulators[line.order_no] = acc
            acc.add(line, request.rates)
            if line.variance_class is not VarianceClass.CLEAN:
                variances.append(line_variance(line))
        _note(manifest, upload, [sheet], lines_read)

    if len(merchant_ids) > 1:
        # The vendor statement is per-vendor, so a run must be too.
        raise ReconInputError(
            "The portal export covers more than one merchant "
            f"({', '.join(sorted(merchant_ids))}). Reconcile one vendor per run."
        )

    orders = [
        adapter.build_order(acc, request.rates, settlements)
        for acc in accumulators.values()
    ]
    return orders, accumulators, variances, lines_read


def _load_ledger(
    adapter: CategoryAdapter[Any], request: ReconRequest, manifest: list[SheetManifest]
) -> list[LedgerEntry]:
    upload = request.vendor
    with open_workbook(upload.path) as workbook:
        sheets = detect_sheets(workbook, adapter.ledger_schema)
        if not sheets:
            raise ReconInputError(
                f"No {adapter.ledger_schema.name} sheet found in {upload.filename!r} "
                f"uploaded as {upload.role!r}."
            )
        # Every ledger-shaped sheet is read and the union deduplicated: the PDF
        # converter emits overlapping copies of the same transactions.
        entries = list(adapter.read_ledger(workbook, sheets, max_rows=request.max_rows))
        _note(manifest, upload, sheets, len(entries))
    return entries


def _check_wallet(
    adapter: CategoryAdapter[Any],
    request: ReconRequest,
    upload: Upload,
    orders: list[OrderRecon],
    manifest: list[SheetManifest],
) -> tuple[WalletReconciliation, list[ReconException]]:
    """One check per order: the wallet share is an order-level figure."""
    history, sheet = load_wallet(
        upload, wallet_type=adapter.wallet_type, max_rows=request.max_rows
    )
    _note(manifest, upload, [sheet], history.events)
    payments = [
        WalletPayment(
            vertical=adapter.wallet_vertical,
            sg_order_id=adapter.wallet_order_id(order.order_no),
            reference=order.order_no,
            order_date=order.order_date,
            wallet_amount=order.wallet_amount,
        )
        for order in orders
    ]
    return reconcile_wallet(payments, history, tolerance=request.rates.line_tolerance)


def _note(
    manifest: list[SheetManifest], upload: Upload, sheets: list[str], rows: int
) -> None:
    manifest.append(
        SheetManifest(
            role=upload.role, filename=upload.filename, sheets=sheets, rows=rows
        )
    )


def _order_exceptions(
    orders: list[OrderRecon], settlements: dict[str, Settlement]
) -> list[ReconException]:
    """Raise the per-order exceptions the rules imply."""
    exceptions: list[ReconException] = []
    for order in orders:
        if order.variance_class is VarianceClass.OVERCHARGE:
            exceptions.append(
                make_exception(
                    ExceptionCode.COMMISSION_OVERCHARGE,
                    order_no=order.order_no,
                    amount=order.commission_variance,
                    detail=(
                        f"Charged {order.actual_commission} against a contractual "
                        f"{order.expected_commission}; the excess is owed back to the vendor"
                    ),
                )
            )
        elif order.variance_class is VarianceClass.WRONG_RATE:
            exceptions.append(
                make_exception(
                    ExceptionCode.COMMISSION_WRONG_RATE,
                    order_no=order.order_no,
                    amount=order.commission_variance,
                    detail=(
                        f"Charged {order.actual_commission}, which matches a different "
                        "configured rate — investigate as a system error"
                    ),
                )
            )
        elif order.variance_class is VarianceClass.PROMO_ABSORBED:
            exceptions.append(
                make_exception(
                    ExceptionCode.PROMO_ABSORBED,
                    order_no=order.order_no,
                    amount=order.absorbed_commission,
                    detail=(
                        "A coupon reduced what we collected; we bear the shortfall and "
                        "the vendor is still paid on the contractual rate"
                    ),
                )
            )
        if "PENDING_DELIVERY" in order.flags:
            exceptions.append(
                make_exception(
                    ExceptionCode.PENDING_DELIVERY,
                    order_no=order.order_no,
                    amount=order.net_payable,
                    detail="Ready to Pickup — payable but not yet delivered",
                )
            )
        if "RTO_EXCLUDED" in order.flags and OrderStatus.RTO in order.statuses:
            # An RTO that predates the period was probably paid in an earlier
            # cycle, but with no settlement history we cannot prove it either way.
            if order.order_no not in settlements:
                exceptions.append(
                    make_exception(
                        ExceptionCode.CLAWBACK_UNRESOLVED,
                        order_no=order.order_no,
                        amount=order.portal_gmv,
                        detail=(
                            "RTO excluded from payable. No prior settlement is recorded, "
                            "so any earlier overpayment cannot be clawed back automatically"
                        ),
                    )
                )
    return exceptions


def _build_summary(
    *,
    orders: list[OrderRecon],
    accumulators: dict[str, Accumulator],
    entries: list[LedgerEntry],
    lines_read: int,
    closing_balance: Decimal | None,
    exceptions: list[ReconException],
) -> Summary:
    """Build the KPI block from unrounded running totals.

    Each KPI is quantized once, here. Summing the already-rounded per-order
    figures instead would drift by a couple of cents on the sample cycle, so the
    Matched Detail column can total a cent or two away from the headline — the
    headline is the figure to pay on.
    """
    included = [o for o in orders if o.included_in_payable]
    raw = [accumulators[o.order_no] for o in included]
    raw_base = sum((a.gmv for a in raw), Decimal(0))
    raw_expected = sum((a.expected_commission for a in raw), Decimal(0))

    payable_before = money(raw_base - raw_expected)
    adjustments = money(sum((o.adjustments for o in orders), Decimal(0)))
    net_payable = money(payable_before + adjustments)
    matched = [o for o in orders if o.vendor_amount is not None]

    return Summary(
        gross_sales=money(
            sum((accumulators[o.order_no].gmv for o in orders), Decimal(0))
        ),
        standard_base=money(sum((a.standard_base for a in raw), Decimal(0))),
        promo_base=money(sum((a.promo_base for a in raw), Decimal(0))),
        commission_earned=money(raw_expected),
        payable_before_adjustments=payable_before,
        adjustments=adjustments,
        net_payable=net_payable,
        vendor_closing_balance=closing_balance,
        unexplained_difference=(
            money(closing_balance - net_payable)
            if closing_balance is not None
            else None
        ),
        promotional_absorption=money(
            sum((a.absorbed for a in accumulators.values()), Decimal(0))
        ),
        overcharged_commission=money(
            sum((a.overcharged for a in accumulators.values()), Decimal(0))
        ),
        portal_lines=lines_read,
        orders=len(orders),
        ledger_entries=len(entries),
        orders_matched=len(matched),
        orders_exact=sum(1 for o in matched if o.amount_diff == 0),
        exception_counts=group_exceptions(exceptions),
    )
