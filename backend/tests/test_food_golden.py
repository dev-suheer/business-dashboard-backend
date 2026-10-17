"""The sample cycle: portal tab against vendor tab, 1-6 Sep 2026."""

from datetime import date
from decimal import Decimal

from app.recon.categories.food.models import FoodResult
from app.recon.categories.food.pipeline import FoodRequest, run
from app.recon.exceptions import ExceptionCode
from app.recon.inputs import Upload
from tests.conftest import FOOD_PORTAL, FOOD_VENDOR


def _run() -> FoodResult:
    return run(
        FoodRequest(
            portal=Upload("portal", FOOD_PORTAL.name, FOOD_PORTAL),
            vendor=Upload("vendor", FOOD_VENDOR.name, FOOD_VENDOR),
            max_rows=10_000,
        )
    )


def test_the_sample_settles_the_orders_whose_ids_match() -> None:
    result = _run()
    summary = result.summary

    assert result.category == "food"
    assert result.vendor_name == "The Chefz"
    assert summary.portal_rows == 302
    assert summary.vendor_rows == 302
    assert summary.paired == 298
    assert summary.portal_only == 4
    assert summary.vendor_only == 4
    assert summary.settled_orders == 297
    assert summary.held_orders == 1
    assert summary.settled_due == Decimal("20748.07")
    assert summary.held_due == Decimal("49.75")
    assert summary.amount_breaks == 1
    assert summary.fee_breaks == 0
    assert summary.delivery_breaks == 0
    assert summary.not_delivered == 0
    assert summary.compensation_orders == 17
    assert summary.compensation_total == Decimal(776)
    assert summary.portal_date_start == date(2026, 9, 1)
    assert summary.portal_date_end == date(2026, 9, 6)

    held = next(
        order
        for order in result.orders
        if not order.settled and order.portal and order.vendor
    )
    assert held.order_id == "290238794"
    assert held.checks.order_amount_difference == Decimal(-4)
    assert [exc.code for exc in result.exceptions if exc.order_no == "290238794"] == [
        ExceptionCode.FOOD_AMOUNT_MISMATCH
    ]

    portal_only = {
        order.order_id
        for order in result.orders
        if order.verdict.value == "portal_only"
    }
    vendor_only = {
        order.order_id
        for order in result.orders
        if order.verdict.value == "vendor_only"
    }
    assert portal_only == {"291072289", "291072796", "291073381", "291073584"}
    assert vendor_only == {"289926686", "289927106", "289927845", "289928292"}

    # Thirteen pairs fall on different calendar days and still settle.
    crossed = [
        order
        for order in result.orders
        if order.settled
        and order.portal
        and order.vendor
        and order.portal.order_date != order.vendor.requested_delivery_date
    ]
    assert len(crossed) == 13
    assert max(
        abs(order.checks.fee_difference or Decimal(0))
        for order in result.orders
        if order.settled
    ) <= Decimal("0.01")
