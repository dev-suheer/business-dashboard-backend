from datetime import date
from decimal import Decimal

import pytest

from app.recon.categories.eshop.rules import (
    OrderAccumulator,
    build_order,
    classify_variance,
    evaluate_line,
    resolve_rate,
)
from app.recon.cleaning import money
from app.recon.models import (
    OrderStatus,
    PortalLine,
    RateConfig,
    Settlement,
    VarianceClass,
)

PROMO_SKUS = frozenset({"PROMO-SKU"})


def line(
    *,
    sku: str | None = "PROMO-SKU",
    order_date: date = date(2026, 8, 1),
    gmv: str = "100",
    actual: str = "15",
    status: OrderStatus = OrderStatus.DELIVERED,
    amount_paid: str | None = None,
    order_no: str = "SG-SH-00001",
) -> PortalLine:
    return PortalLine(
        order_no=order_no,
        sku=sku,
        order_date=order_date,
        gmv=Decimal(gmv),
        actual_commission=Decimal(actual),
        status=status,
        amount_paid=Decimal(amount_paid) if amount_paid else None,
    )


# --- dated rate resolution (plan.md 3.1) ------------------------------------


@pytest.mark.parametrize(
    ("sku", "order_date", "expected"),
    [
        ("PROMO-SKU", date(2026, 8, 8), "0.10"),
        # The cutoff is 9 August *inclusive*.
        ("PROMO-SKU", date(2026, 8, 9), "0.10"),
        # The legacy workbook still gives 10% here; that is the bug being fixed.
        ("PROMO-SKU", date(2026, 8, 10), "0.15"),
        ("OTHER-SKU", date(2026, 8, 1), "0.15"),
        (None, date(2026, 8, 1), "0.15"),
    ],
)
def test_rate_depends_on_both_sku_and_order_date(
    rates: RateConfig, sku: str | None, order_date: date, expected: str
) -> None:
    assert resolve_rate(sku, order_date, PROMO_SKUS, rates) == Decimal(expected)


def test_a_run_level_rate_override_is_honoured(rates: RateConfig) -> None:
    shifted = rates.model_copy(update={"promo_cutoff": date(2026, 8, 8)})
    assert resolve_rate("PROMO-SKU", date(2026, 8, 9), PROMO_SKUS, shifted) == Decimal(
        "0.15"
    )


# --- base and precision (plan.md 3.2) ---------------------------------------


def test_commission_base_is_always_gmv(rates: RateConfig) -> None:
    # An offer price of 110 sits in the catalogue; it must not be the base.
    evaluated = evaluate_line(line(gmv="199", actual="19.90"), PROMO_SKUS, rates)
    assert evaluated.expected_commission == Decimal("19.90")
    assert evaluated.variance_class is VarianceClass.CLEAN


def test_expected_commission_keeps_full_precision(rates: RateConfig) -> None:
    """Rounding per line biases the cycle total upward by 0.12."""
    evaluated = evaluate_line(
        line(sku="OTHER", gmv="66.9", actual="10.04"), PROMO_SKUS, rates
    )
    assert evaluated.expected_commission == Decimal("10.035")
    # The portal's 2dp rounding is inside tolerance, so this is not a break.
    assert evaluated.variance_class is VarianceClass.CLEAN


# --- variance classification (plan.md 3.4) ----------------------------------


@pytest.mark.parametrize(
    ("actual", "expected_class"),
    [
        ("15.00", VarianceClass.CLEAN),
        ("15.01", VarianceClass.CLEAN),  # exactly on the tolerance
        ("14.99", VarianceClass.CLEAN),
        ("15.02", VarianceClass.OVERCHARGE),
        ("12.00", VarianceClass.PROMO_ABSORBED),
    ],
)
def test_variance_classes_on_the_tolerance_boundary(
    rates: RateConfig, actual: str, expected_class: VarianceClass
) -> None:
    base, expected = Decimal("100"), Decimal("15")
    assert (
        classify_variance(base, Decimal(actual), expected, rates.standard_rate, rates)
        is expected_class
    )


def test_undercharge_at_another_configured_rate_is_a_system_error(
    rates: RateConfig,
) -> None:
    # Charged 10 on a 100 base where the contract says 15: that is the promo
    # rate applied to a non-promo order, not a coupon.
    assert (
        classify_variance(
            Decimal("100"), Decimal("10"), Decimal("15"), rates.standard_rate, rates
        )
        is VarianceClass.WRONG_RATE
    )


def test_plan_worked_example_is_a_wrong_rate_under_these_configured_rates(
    rates: RateConfig,
) -> None:
    """plan.md 3.4's illustration — contractual 12, charged 8 — is ambiguous.

    On a base of 80, charging 8 is exactly the 10% promo rate, so the
    wrong-rate test in 3.4 fires before the absorption fallback. That ordering is
    deliberate: a charge that lands precisely on another configured rate is far
    more likely to be a misapplied rate than a coupon that happened to round to
    the same figure.
    """
    evaluated = evaluate_line(
        line(sku="OTHER", gmv="80", actual="8"), PROMO_SKUS, rates
    )
    assert evaluated.expected_commission == Decimal("12.00")
    assert evaluated.variance_class is VarianceClass.WRONG_RATE


def test_promotional_absorption_still_pays_the_vendor_the_contractual_rate(
    rates: RateConfig,
) -> None:
    """A coupon reduced what we collected; the vendor is unaffected.

    Contractual commission 12 on a base of 80, of which we actually collected
    7.50 — a figure that matches no configured rate, so this is absorption.
    """
    evaluated = evaluate_line(
        line(sku="OTHER", gmv="80", actual="7.50"), PROMO_SKUS, rates
    )
    assert evaluated.expected_commission == Decimal("12.00")
    assert evaluated.variance_class is VarianceClass.PROMO_ABSORBED

    acc = OrderAccumulator(order_no="SG-SH-00001", order_date=evaluated.order_date)
    acc.add(evaluated, rates)
    order = build_order(acc, rates, {})

    # The vendor is paid base - contractual commission, not base - 7.50.
    assert order.payable == Decimal("68.00")
    # We absorb the 4.50 and report it as margin leakage.
    assert order.absorbed_commission == Decimal("4.50")


# --- status treatment (plan.md 3.3) -----------------------------------------


def _order(
    rates: RateConfig,
    status: OrderStatus,
    *,
    gmv: str = "100",
    actual: str = "15",
    amount_paid: str | None = None,
    settlements: dict[str, Settlement] | None = None,
):
    evaluated = evaluate_line(
        line(
            sku="OTHER", gmv=gmv, actual=actual, status=status, amount_paid=amount_paid
        ),
        PROMO_SKUS,
        rates,
    )
    acc = OrderAccumulator(order_no=evaluated.order_no, order_date=evaluated.order_date)
    acc.add(evaluated, rates)
    return build_order(acc, rates, settlements or {})


def test_delivered_pays_base_less_commission(rates: RateConfig) -> None:
    order = _order(rates, OrderStatus.DELIVERED)
    assert order.payable == Decimal("85.00")
    assert order.adjustments == Decimal("0")
    assert order.net_payable == Decimal("85.00")
    assert order.included_in_payable


def test_ready_to_pickup_pays_but_is_flagged(rates: RateConfig) -> None:
    order = _order(rates, OrderStatus.READY_TO_PICKUP)
    assert order.payable == Decimal("85.00")
    assert order.net_payable == Decimal("85.00")
    assert "PENDING_DELIVERY" in order.flags


def test_return_nets_to_minus_the_commission(rates: RateConfig) -> None:
    """The commission stays with us, so net = -expected."""
    order = _order(rates, OrderStatus.RETURN)
    assert order.payable == Decimal("85.00")
    assert order.adjustments == Decimal("-100.00")
    assert order.net_payable == Decimal("-15.00")
    assert order.included_in_payable, "the base still counts toward the bases"


def test_unsettled_rto_is_excluded_entirely(rates: RateConfig) -> None:
    order = _order(rates, OrderStatus.RTO)
    assert not order.included_in_payable
    assert order.payable == Decimal("0")
    assert order.adjustments == Decimal("0")
    assert order.standard_base == Decimal("0")
    assert "RTO_EXCLUDED" in order.flags


def test_previously_settled_rto_becomes_a_clawback(rates: RateConfig) -> None:
    order = _order(
        rates, OrderStatus.RTO, gmv="281", actual="42.15", amount_paid="238.85"
    )
    assert not order.included_in_payable
    assert order.adjustments == Decimal("-238.85")
    assert order.net_payable == Decimal("-238.85")
    assert "CLAWBACK" in order.flags


def test_clawback_falls_back_to_uploaded_settlement_history(rates: RateConfig) -> None:
    settlements = {
        "SG-SH-00001": Settlement(order_no="SG-SH-00001", amount_paid=Decimal("238.85"))
    }
    order = _order(rates, OrderStatus.RTO, settlements=settlements)
    assert order.adjustments == Decimal("-238.85")
    assert "CLAWBACK" in order.flags


def test_rto_wins_over_return_on_a_mixed_order(rates: RateConfig) -> None:
    """The most reversing treatment governs, so the order is never over-paid."""
    acc = OrderAccumulator(order_no="SG-SH-00001", order_date=date(2026, 8, 1))
    for status in (OrderStatus.DELIVERED, OrderStatus.RETURN, OrderStatus.RTO):
        acc.add(
            evaluate_line(line(sku="OTHER", status=status), PROMO_SKUS, rates), rates
        )
    order = build_order(acc, rates, {})
    assert not order.included_in_payable
    assert order.line_count == 3


# --- shipping ---------------------------------------------------------------


def test_shipping_is_never_deducted(rates: RateConfig) -> None:
    """Shipping is settled outside this reconciliation (plan.md Appendix A)."""
    order = _order(rates, OrderStatus.DELIVERED, gmv="100", actual="15")
    # A 15 shipping cost sits on the source row; payable is base - commission only.
    assert order.payable == money(Decimal("100") - Decimal("15"))
