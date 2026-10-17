"""Food rules, without a workbook: the checks the sample turned up."""

from datetime import date
from decimal import Decimal

from app.recon.categories.food.models import FoodPortalRow, FoodVendorRow, FoodVerdict
from app.recon.categories.food.rules import reconcile
from app.recon.exceptions import ExceptionCode


def portal(order_id: str, **overrides: object) -> FoodPortalRow:
    values: dict[str, object] = {
        "source": "portal.xlsx",
        "sheet": "PORTAL REPORT",
        "row": 2,
        "order_id": order_id,
        "order_date": date(2026, 9, 2),
        "order_status": "Delivered",
        "vertical": "Food",
        "sub_total": Decimal(39),
        "sg_delivery": Decimal(2),
        "total_amount": Decimal(41),
        "commission_charged": Decimal("1.95"),
        "refund_amount": Decimal(0),
    }
    values.update(overrides)
    return FoodPortalRow(**values)  # type: ignore[arg-type]


def vendor(order_id: str, **overrides: object) -> FoodVendorRow:
    values: dict[str, object] = {
        "source": "vendor.xlsx",
        "sheet": "VENDOR REPORT",
        "row": 3,
        "order_id": order_id,
        "requested_delivery_date": date(2026, 9, 3),
        "order_status": "Delivered",
        "total": Decimal(41),
        "order_chef_total": Decimal(39),
        "shoppinggate_fees": Decimal("1.95"),
        "original_delivery_fee": Decimal(17),
        "sg_delivery_charge": Decimal("6.90"),
        "chef_delivery_charge": Decimal("8.10"),
    }
    values.update(overrides)
    return FoodVendorRow(**values)  # type: ignore[arg-type]


def test_a_date_difference_does_not_block_a_match() -> None:
    orders, exceptions = reconcile(
        [portal("1", order_date=date(2026, 9, 2))],
        [vendor("1", requested_delivery_date=date(2026, 9, 3))],
    )
    assert exceptions == []
    assert orders[0].verdict is FoodVerdict.SETTLED
    assert orders[0].checks.ids_match is True


def test_half_a_cent_on_the_fee_stays_settled() -> None:
    orders, exceptions = reconcile(
        [portal("1", commission_charged=Decimal("1.84"))],
        [vendor("1", shoppinggate_fees=Decimal("1.835"))],
    )
    assert exceptions == []
    assert orders[0].settled is True
    assert orders[0].due == Decimal(41) + Decimal("6.90") - Decimal("1.835")


def test_the_four_sar_break_is_held_and_keeps_the_fee_on_the_same_exception() -> None:
    orders, exceptions = reconcile(
        [portal("290238794")],
        [
            vendor(
                "290238794",
                total=Decimal(45),
                order_chef_total=Decimal(43),
                shoppinggate_fees=Decimal("2.15"),
            )
        ],
    )
    assert orders[0].verdict is FoodVerdict.HELD
    assert orders[0].settled is False
    assert [exc.code for exc in exceptions] == [ExceptionCode.FOOD_AMOUNT_MISMATCH]
    assert "Fee difference" in exceptions[0].detail
    assert orders[0].due == Decimal(45) + Decimal("6.90") - Decimal("2.15")


def test_a_fee_break_on_matching_amounts_is_its_own_exception() -> None:
    _orders, exceptions = reconcile(
        [portal("1")],
        [vendor("1", shoppinggate_fees=Decimal("3.00"))],
    )
    assert [exc.code for exc in exceptions] == [ExceptionCode.FEE_MISMATCH]


def test_a_delivery_split_that_does_not_meet_the_vendor_charge_is_held() -> None:
    _orders, exceptions = reconcile(
        [portal("1")],
        [vendor("1", sg_delivery_charge=Decimal(1))],
    )
    assert [exc.code for exc in exceptions] == [ExceptionCode.DELIVERY_SPLIT_MISMATCH]


def test_a_refund_on_a_delivered_order_is_held() -> None:
    _orders, exceptions = reconcile(
        [portal("1", refund_amount=Decimal(10))],
        [vendor("1")],
    )
    assert [exc.code for exc in exceptions] == [ExceptionCode.REFUND_ON_DELIVERED]


def test_one_sided_orders_are_not_settled() -> None:
    orders, exceptions = reconcile([portal("p")], [vendor("v")])
    by_id = {order.order_id: order for order in orders}
    assert by_id["p"].verdict is FoodVerdict.PORTAL_ONLY
    assert by_id["v"].verdict is FoodVerdict.VENDOR_ONLY
    assert {exc.code for exc in exceptions} == {
        ExceptionCode.ORDER_MISSING_IN_VENDOR,
        ExceptionCode.MISSING_FROM_PORTAL,
    }


def test_a_duplicate_id_is_not_settled() -> None:
    orders, exceptions = reconcile([portal("1"), portal("1", row=3)], [vendor("1")])
    assert orders[0].settled is False
    assert exceptions[0].code is ExceptionCode.DUPLICATE_ORDER_ID


def test_anything_other_than_delivered_is_listed_and_skipped() -> None:
    orders, exceptions = reconcile(
        [portal("1", order_status="Cancelled")],
        [vendor("1", order_status="Cancelled")],
    )
    assert orders == []
    assert {exc.code for exc in exceptions} == {ExceptionCode.STATUS_NOT_DELIVERED}


def test_delivered_against_another_status_is_a_mismatch() -> None:
    orders, exceptions = reconcile(
        [portal("1")],
        [vendor("1", order_status="Cancelled")],
    )
    assert orders[0].verdict is FoodVerdict.HELD
    assert exceptions[0].code is ExceptionCode.STATUS_MISMATCH
