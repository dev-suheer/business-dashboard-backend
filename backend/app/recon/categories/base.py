"""The seam between the generic pipeline and one category's specifics.

A category supplies four sheet schemas, four readers and two rule functions.
Everything else — matching, summarising, exporting — is shared.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from openpyxl.workbook import Workbook

from app.recon.excel import SheetSchema
from app.recon.models import (
    LedgerEntry,
    OrderRecon,
    PortalLine,
    PromoSku,
    RateConfig,
    Settlement,
)


class PortalReader(Protocol):
    def __call__(
        self, workbook: Workbook, sheet: str, *, max_rows: int
    ) -> Iterator[PortalLine]: ...


class LedgerReader(Protocol):
    def __call__(
        self, workbook: Workbook, sheets: list[str], *, max_rows: int
    ) -> Iterator[LedgerEntry]: ...


class PromoReader(Protocol):
    def __call__(
        self, workbook: Workbook, sheet: str, *, max_rows: int
    ) -> Iterator[PromoSku]: ...


class SettlementReader(Protocol):
    def __call__(
        self, workbook: Workbook, sheet: str, *, max_rows: int
    ) -> Iterator[Settlement]: ...


class LineEvaluator(Protocol):
    def __call__(
        self, line: PortalLine, promo_skus: frozenset[str], rates: RateConfig
    ) -> PortalLine: ...


class Accumulator(Protocol):
    """Order-level running totals, all unrounded.

    The engine reads these raw values to build the summary, so that each KPI is
    quantized exactly once rather than being a sum of already-rounded parts.
    """

    order_no: str
    gmv: Decimal
    expected_commission: Decimal
    standard_base: Decimal
    promo_base: Decimal
    absorbed: Decimal
    overcharged: Decimal

    def add(self, line: PortalLine, rates: RateConfig) -> None: ...


class AccumulatorFactory[AccT: Accumulator](Protocol):
    def __call__(self, line: PortalLine) -> AccT: ...


class OrderBuilder[AccT: Accumulator](Protocol):
    def __call__(
        self,
        acc: AccT,
        rates: RateConfig,
        settlements: dict[str, Settlement],
    ) -> OrderRecon: ...


@dataclass(frozen=True)
class CategoryAdapter[AccT: Accumulator]:
    """Everything the engine needs to reconcile one category.

    Generic in the accumulator so a category can give its builder the concrete
    accumulator type it actually needs, instead of being narrowed to whatever the
    shared protocol happens to declare.
    """

    name: str

    portal_schema: SheetSchema
    ledger_schema: SheetSchema
    promo_schema: SheetSchema
    settlement_schema: SheetSchema

    read_portal: PortalReader
    read_ledger: LedgerReader
    read_promo: PromoReader
    read_settlements: SettlementReader

    new_accumulator: AccumulatorFactory[AccT]
    evaluate_line: LineEvaluator
    build_order: OrderBuilder[AccT]
