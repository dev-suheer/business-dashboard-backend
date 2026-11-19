"""The sample cycle: portal tab against vendor tab, 1-6 Sep 2026."""

from datetime import date
from decimal import Decimal

from app.recon.exceptions import ExceptionCode
from tests.conftest import FOOD_PORTAL, FOOD_VENDOR
from tests.food import run_food


def test_the_sample_settles_the_orders_whose_ids_match() -> None:
    result = run_food(portal=FOOD_PORTAL, vendor=FOOD_VENDOR)
    figures = result.metrics

    assert result.category == "food"
    assert figures["vendor_name"] == "The Chefz"
    assert figures["portal_rows"] == 302
    assert figures["vendor_rows"] == 302
    assert figures["paired"] == 298
    assert figures["portal_only"] == 4
    assert figures["vendor_only"] == 4
    assert figures["settled_orders"] == 297
    assert figures["held_orders"] == 1
    assert figures["settled_due"] == Decimal("20748.07")
    assert figures["held_due"] == Decimal("49.75")
    assert figures["amount_breaks"] == 1
    assert figures["fee_breaks"] == 0
    assert figures["delivery_breaks"] == 0
    assert figures["not_delivered"] == 0
    assert figures["compensation_orders"] == 17
    assert figures["compensation_total"] == Decimal(776)
    assert figures["portal_date_start"] == date(2026, 9, 1)
    assert figures["portal_date_end"] == date(2026, 9, 6)
    assert result.history == {
        "counted": 297,
        "total": 306,
        "unit": "orders",
        "net_payable": Decimal("20748.07"),
        "vendor": "The Chefz",
        "period_start": date(2026, 9, 1),
        "period_end": date(2026, 9, 6),
        "report_name": "reconciliation-food-2026-09-01-to-2026-09-06.xlsx",
    }

    orders = result.entities
    held = next(
        o
        for o in orders
        if o.verdict != "settled" and o.values["portal"] and o.values["vendor"]
    )
    assert held.key == "290238794"
    assert held.values["amount_diff"] == Decimal(-4)
    assert [e.code for e in result.exceptions if e.order_no == "290238794"] == [
        ExceptionCode.FOOD_AMOUNT_MISMATCH
    ]

    portal_only = {o.key for o in orders if o.verdict == "portal_only"}
    vendor_only = {o.key for o in orders if o.verdict == "vendor_only"}
    assert portal_only == {"291072289", "291072796", "291073381", "291073584"}
    assert vendor_only == {"289926686", "289927106", "289927845", "289928292"}

    # Thirteen pairs fall on different calendar days and still settle.
    settled = [o for o in orders if o.verdict == "settled"]
    crossed = [
        o
        for o in settled
        if o.values["portal"]["order_date"]
        != o.values["vendor"]["requested_delivery_date"]
    ]
    assert len(crossed) == 13
    assert max(abs(o.values["fee_diff"]) for o in settled) <= Decimal("0.01")
