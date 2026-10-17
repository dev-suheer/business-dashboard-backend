"""Food checks and the payable (plan-food.md section 3).

An order is settled only when both sides are Delivered, the id is unique, the
amount and the fee agree within 0.01, the delivery split agrees, and the portal
refund is zero. Dates are not compared. A break stays on the sheet and out of
the settled payable.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.recon.categories.food.models import (
    FoodChecks,
    FoodOrder,
    FoodPortalRow,
    FoodSummary,
    FoodVendorRow,
    FoodVerdict,
)
from app.recon.cleaning import money
from app.recon.exceptions import ExceptionCode, ReconException, make_exception

TOLERANCE = Decimal("0.01")
_DELIVERED = "delivered"


def is_delivered(status: str) -> bool:
    return status.casefold() == _DELIVERED


def _plain(value: Decimal) -> str:
    return format(value, "f")


def checks_for(
    portal: FoodPortalRow | None, vendor: FoodVendorRow | None
) -> FoodChecks:
    if portal is None or vendor is None:
        return FoodChecks(
            ids_match=False,
            chef_delivery_share=vendor.chef_delivery_charge if vendor else None,
            customer_delivery=portal.sg_delivery if portal else None,
        )
    return FoodChecks(
        ids_match=portal.order_id == vendor.order_id,
        order_amount_difference=portal.total_amount - vendor.total,
        fee_difference=portal.commission_charged - vendor.shoppinggate_fees,
        sg_delivery_share=(
            vendor.original_delivery_fee
            - portal.sg_delivery
            - vendor.chef_delivery_charge
        ),
        chef_delivery_share=vendor.chef_delivery_charge,
        customer_delivery=portal.sg_delivery,
    )


def pair_exceptions(
    portal: FoodPortalRow, vendor: FoodVendorRow
) -> list[ReconException]:
    """The breaks on one paired order. Empty means the order can settle."""
    found: list[ReconException] = []
    inspected = checks_for(portal, vendor)
    amount_diff = inspected.order_amount_difference or Decimal(0)
    fee_diff = inspected.fee_difference or Decimal(0)
    sg_share = inspected.sg_delivery_share or Decimal(0)
    amount_ok = abs(amount_diff) <= TOLERANCE
    fee_ok = abs(fee_diff) <= TOLERANCE
    split_ok = abs(sg_share - vendor.sg_delivery_charge) <= TOLERANCE

    if not amount_ok:
        detail = (
            f"Portal total {_plain(portal.total_amount)} against vendor total "
            f"{_plain(vendor.total)} (difference {_plain(amount_diff)})."
        )
        if not fee_ok:
            detail += (
                f" Fee difference {_plain(fee_diff)} follows the same gap "
                f"(portal {_plain(portal.commission_charged)}, vendor "
                f"{_plain(vendor.shoppinggate_fees)})."
            )
        found.append(
            make_exception(
                ExceptionCode.FOOD_AMOUNT_MISMATCH,
                order_no=portal.order_id,
                amount=amount_diff,
                detail=detail,
            )
        )
    elif not fee_ok:
        found.append(
            make_exception(
                ExceptionCode.FEE_MISMATCH,
                order_no=portal.order_id,
                amount=fee_diff,
                detail=(
                    f"Portal commission {_plain(portal.commission_charged)} against "
                    f"vendor fees {_plain(vendor.shoppinggate_fees)} "
                    f"(difference {_plain(fee_diff)})."
                ),
            )
        )
    if not split_ok:
        found.append(
            make_exception(
                ExceptionCode.DELIVERY_SPLIT_MISMATCH,
                order_no=portal.order_id,
                amount=sg_share - vendor.sg_delivery_charge,
                detail=(
                    f"SG delivery share {_plain(sg_share)} against vendor charge "
                    f"{_plain(vendor.sg_delivery_charge)}."
                ),
            )
        )
    if portal.refund_amount != 0:
        found.append(
            make_exception(
                ExceptionCode.REFUND_ON_DELIVERED,
                order_no=portal.order_id,
                amount=portal.refund_amount,
                detail=(
                    f"Delivered order {portal.order_id} carries a refund of "
                    f"{_plain(portal.refund_amount)}."
                ),
            )
        )
    return found


def _not_delivered(row_status: str, order_id: str, side: str) -> ReconException:
    return make_exception(
        ExceptionCode.STATUS_NOT_DELIVERED,
        order_no=order_id,
        detail=f"{side} status {row_status} is not Delivered.",
    )


def _order(
    order_id: str,
    verdict: FoodVerdict,
    *,
    portal: FoodPortalRow | None,
    vendor: FoodVendorRow | None,
    flags: list[str],
) -> FoodOrder:
    due = vendor.due_from_shoppinggate if vendor is not None else None
    return FoodOrder(
        order_id=order_id,
        verdict=verdict,
        settled=verdict is FoodVerdict.SETTLED,
        due=due,
        portal=portal,
        vendor=vendor,
        checks=checks_for(portal, vendor),
        flags=flags,
    )


def reconcile(
    portal_rows: list[FoodPortalRow], vendor_rows: list[FoodVendorRow]
) -> tuple[list[FoodOrder], list[ReconException]]:
    """Pair on order id and judge each pair. Dates are carried and not tested."""
    exceptions: list[ReconException] = []
    portal_delivered: dict[str, list[FoodPortalRow]] = defaultdict(list)
    vendor_delivered: dict[str, list[FoodVendorRow]] = defaultdict(list)
    portal_other: list[FoodPortalRow] = []
    vendor_other: list[FoodVendorRow] = []

    for portal_row in portal_rows:
        if is_delivered(portal_row.order_status):
            portal_delivered[portal_row.order_id].append(portal_row)
        else:
            portal_other.append(portal_row)
    for vendor_row in vendor_rows:
        if is_delivered(vendor_row.order_status):
            vendor_delivered[vendor_row.order_id].append(vendor_row)
        else:
            vendor_other.append(vendor_row)

    portal_other_ids = {row.order_id for row in portal_other}
    vendor_other_ids = {row.order_id for row in vendor_other}

    for portal_row in portal_other:
        if portal_row.order_id in vendor_delivered:
            continue
        exceptions.append(
            _not_delivered(portal_row.order_status, portal_row.order_id, "Portal")
        )
    for vendor_row in vendor_other:
        if vendor_row.order_id in portal_delivered:
            continue
        exceptions.append(
            _not_delivered(vendor_row.order_status, vendor_row.order_id, "Vendor")
        )

    duplicate_ids: set[str] = set()
    for order_id, copies in portal_delivered.items():
        if len(copies) > 1:
            duplicate_ids.add(order_id)
            exceptions.append(
                make_exception(
                    ExceptionCode.DUPLICATE_ORDER_ID,
                    order_no=order_id,
                    detail=(
                        f"Partner Order ID {order_id} appears {len(copies)} times "
                        "on the portal."
                    ),
                )
            )
    for order_id, vendor_copies in vendor_delivered.items():
        if len(vendor_copies) > 1:
            duplicate_ids.add(order_id)
            exceptions.append(
                make_exception(
                    ExceptionCode.DUPLICATE_ORDER_ID,
                    order_no=order_id,
                    detail=(
                        f"order_id {order_id} appears {len(vendor_copies)} times "
                        "on the vendor report."
                    ),
                )
            )

    orders: list[FoodOrder] = []
    order_ids = sorted(set(portal_delivered) | set(vendor_delivered))
    for order_id in order_ids:
        portal = portal_delivered.get(order_id, [None])[0]
        vendor = vendor_delivered.get(order_id, [None])[0]
        flags = [
            exc.code.value
            for exc in exceptions
            if exc.order_no == order_id and exc.code is ExceptionCode.DUPLICATE_ORDER_ID
        ]

        if portal is not None and vendor is None and order_id in vendor_other_ids:
            vendor_other_row = next(
                item for item in vendor_other if item.order_id == order_id
            )
            exc = make_exception(
                ExceptionCode.STATUS_MISMATCH,
                order_no=order_id,
                detail=(
                    f"Portal status {portal.order_status}, vendor status "
                    f"{vendor_other_row.order_status}."
                ),
            )
            exceptions.append(exc)
            orders.append(
                _order(
                    order_id,
                    FoodVerdict.HELD,
                    portal=portal,
                    vendor=vendor_other_row,
                    flags=[*flags, exc.code.value],
                )
            )
            continue
        if vendor is not None and portal is None and order_id in portal_other_ids:
            portal_other_row = next(
                item for item in portal_other if item.order_id == order_id
            )
            exc = make_exception(
                ExceptionCode.STATUS_MISMATCH,
                order_no=order_id,
                detail=(
                    f"Portal status {portal_other_row.order_status}, vendor status "
                    f"{vendor.order_status}."
                ),
            )
            exceptions.append(exc)
            orders.append(
                _order(
                    order_id,
                    FoodVerdict.HELD,
                    portal=portal_other_row,
                    vendor=vendor,
                    flags=[*flags, exc.code.value],
                )
            )
            continue

        if portal is None or vendor is None:
            if portal is not None:
                exc = make_exception(
                    ExceptionCode.ORDER_MISSING_IN_VENDOR,
                    order_no=order_id,
                    amount=portal.total_amount,
                    detail=f"Portal order {order_id} has no vendor row.",
                )
                verdict = FoodVerdict.PORTAL_ONLY
            else:
                assert vendor is not None
                exc = make_exception(
                    ExceptionCode.MISSING_FROM_PORTAL,
                    order_no=order_id,
                    amount=vendor.due_from_shoppinggate,
                    detail=f"Vendor order {order_id} has no portal row.",
                )
                verdict = FoodVerdict.VENDOR_ONLY
            exceptions.append(exc)
            orders.append(
                _order(
                    order_id,
                    verdict,
                    portal=portal,
                    vendor=vendor,
                    flags=[*flags, exc.code.value],
                )
            )
            continue

        if order_id in duplicate_ids:
            orders.append(
                _order(
                    order_id,
                    FoodVerdict.HELD,
                    portal=portal,
                    vendor=vendor,
                    flags=flags,
                )
            )
            continue

        pair = pair_exceptions(portal, vendor)
        exceptions.extend(pair)
        orders.append(
            _order(
                order_id,
                FoodVerdict.SETTLED if not pair else FoodVerdict.HELD,
                portal=portal,
                vendor=vendor,
                flags=[exc.code.value for exc in pair],
            )
        )

    orders.sort(key=_sort_key)
    return orders, exceptions


def _sort_key(order: FoodOrder) -> tuple[date, str]:
    when = date.min
    if order.portal is not None and order.portal.order_date is not None:
        when = order.portal.order_date
    elif order.vendor is not None and order.vendor.requested_delivery_date is not None:
        when = order.vendor.requested_delivery_date
    return when, order.order_id


def summarise(
    portal_rows: list[FoodPortalRow],
    vendor_rows: list[FoodVendorRow],
    orders: list[FoodOrder],
    exceptions: list[ReconException],
) -> FoodSummary:
    settled = [order for order in orders if order.settled]
    held = [order for order in orders if order.verdict is FoodVerdict.HELD]
    portal_only = [
        order for order in orders if order.verdict is FoodVerdict.PORTAL_ONLY
    ]
    vendor_only = [
        order for order in orders if order.verdict is FoodVerdict.VENDOR_ONLY
    ]

    def _due(order: FoodOrder) -> Decimal:
        return order.due or Decimal(0)

    settled_due = sum((_due(order) for order in settled), Decimal(0))
    held_due = sum((_due(order) for order in held), Decimal(0))
    vendor_only_due = sum((_due(order) for order in vendor_only), Decimal(0))

    subtotal = Decimal(0)
    customer_delivery = Decimal(0)
    sg_delivery = Decimal(0)
    fees = Decimal(0)
    chef = Decimal(0)
    rates: set[Decimal] = set()
    for order in settled:
        assert order.portal is not None and order.vendor is not None
        subtotal += order.portal.sub_total
        customer_delivery += order.portal.sg_delivery
        sg_delivery += order.vendor.sg_delivery_charge
        fees += order.vendor.shoppinggate_fees
        chef += order.vendor.order_chef_total
        if order.vendor.shoppinggate_percentage is not None:
            rates.add(order.vendor.shoppinggate_percentage)

    compensation_rows = [
        row
        for row in portal_rows
        if is_delivered(row.order_status) and row.compensation_amount != 0
    ]
    portal_dates = [
        row.order_date
        for row in portal_rows
        if is_delivered(row.order_status) and row.order_date is not None
    ]

    def _count(code: ExceptionCode) -> int:
        return sum(1 for exc in exceptions if exc.code is code)

    return FoodSummary(
        portal_rows=len(portal_rows),
        vendor_rows=len(vendor_rows),
        paired=sum(
            1
            for order in orders
            if order.portal is not None and order.vendor is not None
        ),
        settled_orders=len(settled),
        settled_due=money(settled_due),
        held_orders=len(held),
        held_due=money(held_due),
        portal_only=len(portal_only),
        vendor_only=len(vendor_only),
        vendor_only_due=money(vendor_only_due),
        amount_breaks=_count(ExceptionCode.FOOD_AMOUNT_MISMATCH),
        fee_breaks=_count(ExceptionCode.FEE_MISMATCH),
        delivery_breaks=_count(ExceptionCode.DELIVERY_SPLIT_MISMATCH),
        not_delivered=_count(ExceptionCode.STATUS_NOT_DELIVERED),
        compensation_orders=len(compensation_rows),
        compensation_total=money(
            sum((row.compensation_amount for row in compensation_rows), Decimal(0))
        ),
        settled_subtotal=money(subtotal),
        settled_customer_delivery=money(customer_delivery),
        settled_sg_delivery=money(sg_delivery),
        settled_vendor_fees=money(fees),
        settled_food_gap=money(chef - subtotal),
        portal_date_start=min(portal_dates) if portal_dates else None,
        portal_date_end=max(portal_dates) if portal_dates else None,
        rates_seen=sorted(rates),
    )
