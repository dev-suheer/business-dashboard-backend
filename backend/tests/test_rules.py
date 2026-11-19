"""eShop business rules: dated rates, the commission base, variance classes and
status treatments.

eShop's rules are version 1 of its configuration (``seed_eshop.py``), run by
the generic runner on rows built by hand.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.recon.cleaning import money
from app.runner import Row, RunResult
from tests.eshop import (
    order,
    portal_line,
    promo_row,
    reconcile_eshop,
    settlement_row,
)

PROMO = [promo_row("PROMO-SKU")]


def _line(**values: Any) -> Row:
    """One line, reconciled on its own; its rate and variance read back."""
    line = portal_line(**values)
    reconcile_eshop([line], promo=PROMO)
    return line


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
    sku: str | None, order_date: date, expected: str
) -> None:
    assert _line(sku=sku, order_date=order_date)["rate"] == Decimal(expected)


def test_a_run_level_rate_override_is_honoured() -> None:
    line = portal_line(sku="PROMO-SKU", order_date=date(2026, 8, 9))
    reconcile_eshop([line], promo=PROMO, overrides={"promo_cutoff": date(2026, 8, 8)})
    assert line["rate"] == Decimal("0.15")


# --- base and precision (plan.md 3.2) ---------------------------------------


def test_commission_base_is_always_gmv() -> None:
    # An offer price of 110 sits in the catalogue; it must not be the base.
    line = _line(
        sku="PROMO-SKU", gmv=Decimal("199"), actual_commission=Decimal("19.90")
    )
    assert line["expected"] == Decimal("19.90")
    assert line["variance_class"] == "clean"


def test_expected_commission_keeps_full_precision() -> None:
    """Rounding per line biases the cycle total upward by 0.12."""
    line = _line(gmv=Decimal("66.9"), actual_commission=Decimal("10.04"))
    assert line["expected"] == Decimal("10.035")
    # The portal's 2dp rounding is inside tolerance, so this is not a break.
    assert line["variance_class"] == "clean"


# --- variance classification (plan.md 3.4) ----------------------------------


@pytest.mark.parametrize(
    ("actual", "expected_class"),
    [
        ("15.00", "clean"),
        ("15.01", "clean"),  # exactly on the tolerance
        ("14.99", "clean"),
        ("15.02", "overcharge"),
        ("12.00", "promo_absorbed"),
    ],
)
def test_variance_classes_on_the_tolerance_boundary(
    actual: str, expected_class: str
) -> None:
    line = _line(gmv=Decimal("100"), actual_commission=Decimal(actual))
    assert line["variance_class"] == expected_class


def test_undercharge_at_another_configured_rate_is_a_system_error() -> None:
    # Charged 10 on a 100 base where the contract says 15: that is the promo
    # rate applied to a non-promo order, not a coupon.
    line = _line(gmv=Decimal("100"), actual_commission=Decimal("10"))
    assert line["variance_class"] == "wrong_rate"


def test_plan_worked_example_is_a_wrong_rate_under_these_configured_rates() -> None:
    """plan.md 3.4's illustration — contractual 12, charged 8 — is ambiguous.

    On a base of 80, charging 8 is exactly the 10% promo rate, so the
    wrong-rate test in 3.4 fires before the absorption fallback. That ordering is
    deliberate: a charge that lands precisely on another configured rate is far
    more likely to be a misapplied rate than a coupon that happened to round to
    the same figure.
    """
    line = _line(gmv=Decimal("80"), actual_commission=Decimal("8"))
    assert money(line["expected"]) == Decimal("12.00")
    assert line["variance_class"] == "wrong_rate"


def test_promotional_absorption_still_pays_the_vendor_the_contractual_rate() -> None:
    """A coupon reduced what we collected; the vendor is unaffected.

    Contractual commission 12 on a base of 80, of which we actually collected
    7.50 — a figure that matches no configured rate, so this is absorption.
    """
    result = reconcile_eshop(
        [portal_line(gmv=Decimal("80"), actual_commission=Decimal("7.50"))]
    )
    values = order(result, "SG-SH-00001").values
    assert values["expected_commission"] == Decimal("12.00")
    assert values["variance_class"] == "promo_absorbed"
    # The vendor is paid base - contractual commission, not base - 7.50.
    assert values["payable"] == Decimal("68.00")
    # We absorb the 4.50 and report it as margin leakage.
    assert values["absorbed_commission"] == Decimal("4.50")
    assert result.metrics["promotional_absorption"] == Decimal("4.50")


# --- status treatment (plan.md 3.3) -----------------------------------------


def _order(
    status: str,
    *,
    gmv: str = "100",
    actual: str = "15",
    amount_paid: str | None = None,
    settlements: list[Row] | None = None,
) -> RunResult:
    return reconcile_eshop(
        [
            portal_line(
                status=status,
                gmv=Decimal(gmv),
                actual_commission=Decimal(actual),
                amount_paid=Decimal(amount_paid) if amount_paid else None,
            )
        ],
        settlements=settlements or [],
    )


def _values(result: RunResult) -> dict[str, Any]:
    return order(result, "SG-SH-00001").values


def test_delivered_pays_base_less_commission() -> None:
    values = _values(_order("Delivered"))
    assert values["payable"] == Decimal("85.00")
    assert values["adjustments"] == Decimal("0")
    assert values["net_payable"] == Decimal("85.00")
    assert values["included"]


def test_ready_to_pickup_pays_but_is_flagged() -> None:
    result = _order("Ready to Pickup")
    values = _values(result)
    assert values["payable"] == Decimal("85.00")
    assert values["net_payable"] == Decimal("85.00")
    assert values["treatment"] == "PENDING_DELIVERY"
    (pending,) = [e for e in result.exceptions if str(e.code) == "PENDING_DELIVERY"]
    assert pending.amount == Decimal("85.00")


def test_return_nets_to_minus_the_commission() -> None:
    """The commission stays with us, so net = -expected."""
    values = _values(_order("Return"))
    assert values["payable"] == Decimal("85.00")
    assert values["adjustments"] == Decimal("-100.00")
    assert values["net_payable"] == Decimal("-15.00")
    assert values["included"], "the base still counts toward the bases"


def test_unsettled_rto_is_excluded_entirely() -> None:
    result = _order("RTO")
    values = _values(result)
    assert not values["included"]
    assert values["payable"] == Decimal("0")
    assert values["adjustments"] == Decimal("0")
    assert values["standard_base"] == Decimal("0")
    assert values["treatment"] == "RTO_EXCLUDED"
    assert "CLAWBACK_UNRESOLVED" in {str(e.code) for e in result.exceptions}


def test_previously_settled_rto_becomes_a_clawback() -> None:
    values = _values(_order("RTO", gmv="281", actual="42.15", amount_paid="238.85"))
    assert not values["included"]
    assert values["adjustments"] == Decimal("-238.85")
    assert values["net_payable"] == Decimal("-238.85")
    assert values["treatment"] == "CLAWBACK"


def test_clawback_falls_back_to_uploaded_settlement_history() -> None:
    result = _order(
        "RTO", settlements=[settlement_row("SG-SH-00001", Decimal("238.85"))]
    )
    values = _values(result)
    assert values["adjustments"] == Decimal("-238.85")
    assert values["treatment"] == "CLAWBACK"
    assert "CLAWBACK_UNRESOLVED" not in {str(e.code) for e in result.exceptions}


def test_rto_wins_over_return_on_a_mixed_order() -> None:
    """The most reversing treatment governs, so the order is never over-paid."""
    result = reconcile_eshop(
        [portal_line(status=status) for status in ("Delivered", "Return", "RTO")]
    )
    values = _values(result)
    assert not values["included"]
    assert values["n_lines"] == 3
    assert values["statuses"] == "Delivered, RTO, Return"


# --- shipping ---------------------------------------------------------------


def test_shipping_is_never_deducted() -> None:
    """Shipping is settled outside this reconciliation (plan.md Appendix A)."""
    values = _values(
        reconcile_eshop([portal_line(shipping_cost=Decimal("15"), shipping_owner="SG")])
    )
    # A 15 shipping cost sits on the line; payable is base - commission only.
    assert values["payable"] == money(Decimal("100") - Decimal("15"))
    assert values["shipping_cost"] == Decimal("15.00")
    assert values["sg_shipping"] == Decimal("15.00")
