"""Food rules, without a workbook: the checks the sample turned up.

Food's rules are version 1 of its configuration (``seed_food.py``), run by the
generic runner.
"""

from datetime import date
from decimal import Decimal

from app.recon.exceptions import ExceptionCode
from tests.food import portal_row, reconcile_food, vendor_row


def test_a_date_difference_does_not_block_a_match() -> None:
    result = reconcile_food(
        [portal_row("1", order_date=date(2026, 9, 2))],
        [vendor_row("1", requested_delivery_date=date(2026, 9, 3))],
    )
    assert result.exceptions == []
    order = result.entities[0]
    assert order.verdict == "settled"
    assert order.values["ids_match"] is True


def test_half_a_cent_on_the_fee_stays_settled() -> None:
    result = reconcile_food(
        [portal_row("1", commission_charged=Decimal("1.84"))],
        [vendor_row("1", shoppinggate_fees=Decimal("1.835"))],
    )
    assert result.exceptions == []
    order = result.entities[0]
    assert order.verdict == "settled"
    assert order.values["due"] == Decimal(41) + Decimal("6.90") - Decimal("1.835")


def test_the_four_sar_break_is_held_and_keeps_the_fee_on_the_same_exception() -> None:
    result = reconcile_food(
        [portal_row("290238794")],
        [
            vendor_row(
                "290238794",
                total=Decimal(45),
                order_chef_total=Decimal(43),
                shoppinggate_fees=Decimal("2.15"),
            )
        ],
    )
    order = result.entities[0]
    assert order.verdict == "held"
    assert [e.code for e in result.exceptions] == [ExceptionCode.FOOD_AMOUNT_MISMATCH]
    assert result.exceptions[0].detail == (
        "Portal total 41 against vendor total 45 (difference -4). Fee difference "
        "-0.20 follows the same gap (portal 1.95, vendor 2.15)."
    )
    assert order.values["due"] == Decimal(45) + Decimal("6.90") - Decimal("2.15")
    assert order.codes == ["FOOD_AMOUNT_MISMATCH"]


def test_a_fee_break_on_matching_amounts_is_its_own_exception() -> None:
    result = reconcile_food(
        [portal_row("1")],
        [vendor_row("1", shoppinggate_fees=Decimal("3.00"))],
    )
    assert [e.code for e in result.exceptions] == [ExceptionCode.FEE_MISMATCH]
    assert result.exceptions[0].amount == Decimal("-1.05")


def test_a_delivery_split_that_does_not_meet_the_vendor_charge_is_held() -> None:
    result = reconcile_food(
        [portal_row("1")],
        [vendor_row("1", sg_delivery_charge=Decimal(1))],
    )
    assert [e.code for e in result.exceptions] == [
        ExceptionCode.DELIVERY_SPLIT_MISMATCH
    ]
    assert result.exceptions[0].detail == (
        "SG delivery share 6.90 against vendor charge 1."
    )
    assert result.entities[0].verdict == "held"


def test_a_zero_delivery_share_is_written_as_zero() -> None:
    """The share is 17 - 2 - 15.00 = 0.00, written as a plain 0, as v1 did."""
    result = reconcile_food(
        [portal_row("1")],
        [vendor_row("1", chef_delivery_charge=Decimal("15.00"))],
    )
    exception = result.exceptions[0]
    assert exception.code is ExceptionCode.DELIVERY_SPLIT_MISMATCH
    assert exception.detail == "SG delivery share 0 against vendor charge 6.90."
    assert str(exception.amount) == "-6.90"


def test_a_refund_on_a_delivered_order_is_held() -> None:
    result = reconcile_food(
        [portal_row("1", refund_amount=Decimal(10))],
        [vendor_row("1")],
    )
    assert [e.code for e in result.exceptions] == [ExceptionCode.REFUND_ON_DELIVERED]
    assert result.exceptions[0].detail == ("Delivered order 1 carries a refund of 10.")


def test_one_sided_orders_are_not_settled() -> None:
    result = reconcile_food([portal_row("p")], [vendor_row("v")])
    by_id = {order.key: order for order in result.entities}
    assert by_id["p"].verdict == "portal_only"
    assert by_id["v"].verdict == "vendor_only"
    assert {(e.code, e.amount) for e in result.exceptions} == {
        (ExceptionCode.ORDER_MISSING_IN_VENDOR, Decimal(41)),
        (ExceptionCode.MISSING_FROM_PORTAL, Decimal("45.95")),
    }


def test_a_duplicate_id_is_not_settled() -> None:
    result = reconcile_food([portal_row("1"), portal_row("1")], [vendor_row("1")])
    order = result.entities[0]
    assert order.verdict == "held"
    # Listed under the duplicated order.
    assert [e.order_no for e in result.exceptions] == [order.key]
    assert [e.detail for e in result.exceptions] == [
        "Partner Order ID 1 appears 2 times on the portal."
    ]


def test_anything_other_than_delivered_is_listed_and_skipped() -> None:
    result = reconcile_food(
        [portal_row("1", order_status="Cancelled")],
        [vendor_row("1", order_status="Cancelled")],
    )
    assert result.entities == []
    assert [e.detail for e in result.exceptions] == [
        "Portal status Cancelled is not Delivered.",
        "Vendor status Cancelled is not Delivered.",
    ]
    assert {e.code for e in result.exceptions} == {ExceptionCode.STATUS_NOT_DELIVERED}


def test_delivered_against_another_status_is_a_mismatch() -> None:
    result = reconcile_food(
        [portal_row("1")],
        [vendor_row("1", order_status="Cancelled")],
    )
    order = result.entities[0]
    assert order.verdict == "held"
    assert order.values["vendor"]["order_status"] == "Cancelled"
    assert [e.code for e in result.exceptions] == [ExceptionCode.STATUS_MISMATCH]
    assert result.exceptions[0].detail == (
        "Portal status Delivered, vendor status Cancelled."
    )


def test_orders_are_listed_by_date_and_then_by_id() -> None:
    result = reconcile_food(
        [
            portal_row("b", order_date=date(2026, 9, 1)),
            portal_row("a", order_date=date(2026, 9, 2)),
            portal_row("c", order_date=None),
        ],
        [
            vendor_row("a"),
            vendor_row("b"),
            vendor_row("c", requested_delivery_date=None),
        ],
    )
    # No date on either side sorts first.
    assert [order.key for order in result.entities] == ["c", "b", "a"]
