"""eShop business rules (plan.md section 3).

Two invariants govern this module:

* the commission base is always portal GMV — never the promo catalogue price;
* shipping is never deducted, because it is settled outside reconciliation.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.recon.cleaning import money
from app.recon.models import (
    LineVariance,
    OrderRecon,
    OrderStatus,
    PortalLine,
    RateConfig,
    Settlement,
    VarianceClass,
)

# Statuses whose revenue is not payable in this cycle at all. An RTO's invoice is
# expected to be reversed by a credit note in the vendor's ledger.
EXCLUDED_STATUSES = frozenset({OrderStatus.RTO})


def resolve_rate(
    sku: str | None,
    order_date: date,
    promo_skus: frozenset[str],
    rates: RateConfig,
) -> Decimal:
    """Pick the commission rate for one line.

    The promo rate applies only when the SKU is in the catalogue *and* the order
    was placed on or before the cutoff. The date half is what the legacy
    workbook's ``IF(F2 = AI2, 10%, 15%)`` omits, which is why it still awards 10%
    to orders placed after the campaign ended.
    """
    if sku and sku in promo_skus and order_date <= rates.promo_cutoff:
        return rates.promo_rate
    return rates.standard_rate


def classify_variance(
    base: Decimal,
    actual: Decimal,
    expected: Decimal,
    applied_rate: Decimal,
    rates: RateConfig,
) -> VarianceClass:
    """Classify the gap between contractual and charged commission.

    Within tolerance is clean: the portal rounds ``SG Commission`` to 2dp, so
    effective rates read 0.14996 rather than 0.15 and are not breaks.
    """
    variance = actual - expected
    if abs(variance) <= rates.line_tolerance:
        return VarianceClass.CLEAN
    if variance > rates.line_tolerance:
        # We charged more than the contract allows; owed back to the vendor.
        return VarianceClass.OVERCHARGE
    # Undercharged. If what we actually took matches one of the *other*
    # configured rates, this is a system error rather than a promotion.
    for candidate in (rates.standard_rate, rates.promo_rate):
        if candidate == applied_rate:
            continue
        if abs(money(base * candidate) - actual) <= rates.line_tolerance:
            return VarianceClass.WRONG_RATE
    return VarianceClass.PROMO_ABSORBED


def evaluate_line(
    line: PortalLine, promo_skus: frozenset[str], rates: RateConfig
) -> PortalLine:
    """Resolve rate, expected commission and variance class for one line.

    Expected commission is deliberately left unrounded. Quantizing per line
    biases the total upward — 31 of the sample's lines land on an exact half
    cent, which is worth 0.12 on the cycle — so rounding happens once, at the
    point a figure is reported. Tolerance comparisons are unaffected, since a
    half cent is well inside the 0.01 line tolerance.
    """
    rate = resolve_rate(line.sku, line.order_date, promo_skus, rates)
    expected = line.gmv * rate
    variance = line.actual_commission - expected
    return line.model_copy(
        update={
            "rate": rate,
            "expected_commission": expected,
            "commission_variance": variance,
            "variance_class": classify_variance(
                line.gmv, line.actual_commission, expected, rate, rates
            ),
        }
    )


@dataclass
class OrderAccumulator:
    """Order-level totals built up line by line.

    The engine streams the portal export through this rather than collecting
    ``PortalLine`` objects, so peak memory scales with the number of *orders*,
    not the number of rows.
    """

    order_no: str
    order_date: date
    merchant_id: str | None = None
    line_count: int = 0
    gmv: Decimal = Decimal(0)
    expected_commission: Decimal = Decimal(0)
    actual_commission: Decimal = Decimal(0)
    standard_base: Decimal = Decimal(0)
    promo_base: Decimal = Decimal(0)
    absorbed: Decimal = Decimal(0)
    overcharged: Decimal = Decimal(0)
    statuses: set[OrderStatus] = field(default_factory=set)
    amount_paid: Decimal | None = None

    def add(self, line: PortalLine, rates: RateConfig) -> None:
        self.line_count += 1
        # plan.md 3.5: the order date is the earliest of its lines'.
        self.order_date = min(self.order_date, line.order_date)
        self.gmv += line.gmv
        self.expected_commission += line.expected_commission
        self.actual_commission += line.actual_commission
        self.statuses.add(line.status)
        if line.merchant_id and self.merchant_id is None:
            self.merchant_id = line.merchant_id
        if line.rate == rates.promo_rate:
            self.promo_base += line.gmv
        else:
            self.standard_base += line.gmv
        if line.variance_class is VarianceClass.PROMO_ABSORBED:
            # We collected less than the contract; we bear the shortfall.
            self.absorbed += -line.commission_variance
        elif line.variance_class is VarianceClass.OVERCHARGE:
            self.overcharged += line.commission_variance
        if line.amount_paid:
            self.amount_paid = (self.amount_paid or Decimal(0)) + line.amount_paid


def build_order(
    acc: OrderAccumulator,
    rates: RateConfig,
    settlements: dict[str, Settlement],
) -> OrderRecon:
    """Turn accumulated lines into a reconciled order, applying section 3.3.

    Status precedence, for an order whose lines disagree: RTO wins over Return,
    which wins over the normal path. The most reversing treatment governs, so a
    partially returned order is never over-paid.
    """
    # Every reported figure is quantized once, from the unrounded running total.
    gmv = money(acc.gmv)
    expected = money(acc.expected_commission)
    actual = money(acc.actual_commission)
    statuses = sorted(acc.statuses, key=lambda s: s.value)

    payable = money(acc.gmv - acc.expected_commission)
    adjustments = money(Decimal(0))
    included = True
    flags: list[str] = []

    settled = acc.amount_paid or (
        settlements[acc.order_no].amount_paid if acc.order_no in settlements else None
    )

    if OrderStatus.RTO in acc.statuses:
        # Excluded from payable entirely; a credit note should reverse the
        # invoice. If an earlier cycle already paid it, claw that back.
        included = False
        payable = money(Decimal(0))
        if settled:
            adjustments = money(-settled)
            flags.append("CLAWBACK")
        else:
            flags.append("RTO_EXCLUDED")
    elif OrderStatus.RETURN in acc.statuses:
        # Payable as normal, then the full base comes back off, leaving
        # net = -expected. The commission stays with us.
        adjustments = money(-gmv)
        flags.append("RETURN_ADJUSTED")
    elif OrderStatus.READY_TO_PICKUP in acc.statuses:
        flags.append("PENDING_DELIVERY")

    variance = money(acc.actual_commission - acc.expected_commission)
    variance_class = classify_variance(
        acc.gmv,
        acc.actual_commission,
        acc.expected_commission,
        _dominant_rate(acc, rates),
        rates,
    )

    return OrderRecon(
        order_no=acc.order_no,
        order_date=acc.order_date,
        line_count=acc.line_count,
        statuses=statuses,
        merchant_id=acc.merchant_id,
        portal_gmv=gmv,
        expected_commission=expected,
        actual_commission=actual,
        commission_variance=variance,
        variance_class=variance_class,
        promo_base=money(acc.promo_base if included else Decimal(0)),
        standard_base=money(acc.standard_base if included else Decimal(0)),
        absorbed_commission=money(acc.absorbed),
        payable=payable,
        adjustments=adjustments,
        net_payable=money(payable + adjustments),
        included_in_payable=included,
        flags=flags,
    )


def _dominant_rate(acc: OrderAccumulator, rates: RateConfig) -> Decimal:
    """The rate that governs most of the order's value.

    Used only to classify an order-level variance; per-line classification never
    goes through here.
    """
    return (
        rates.promo_rate if acc.promo_base > acc.standard_base else rates.standard_rate
    )


def line_variance(line: PortalLine) -> LineVariance:
    """Record a line whose commission breached tolerance."""
    return LineVariance(
        order_no=line.order_no,
        sku=line.sku,
        item_title=line.item_title,
        order_date=line.order_date,
        status=line.status,
        gmv=money(line.gmv),
        rate=line.rate,
        expected_commission=money(line.expected_commission),
        actual_commission=money(line.actual_commission),
        variance=money(line.commission_variance),
        variance_class=line.variance_class,
        coupon_name=line.coupon_name,
        coupon_value=(
            money(line.coupon_value) if line.coupon_value is not None else None
        ),
    )
