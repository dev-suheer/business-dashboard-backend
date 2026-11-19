"""Travel business rules: rates, refunds, grouping and the payment split.

Travel's rules are version 1 of its configuration (``seed_travel.py``), run by
the generic runner on rows built by hand.
"""

from decimal import Decimal

import pytest

from app.recon.exceptions import ExceptionCode, ReconException
from app.runner import Row, RunResult
from tests.travel import (
    booking,
    claim_line,
    credit_line,
    export_span,
    portal_row,
    reconcile_travel,
)

BOOKING = "A260819100005500"
# The portal export spans 26 Jul to 22 Aug.
EXPORT = export_span()


def _codes(result: RunResult, order_no: str = BOOKING) -> list[ExceptionCode]:
    return [e.code for e in result.exceptions if e.order_no == order_no]


def _found(result: RunResult, code: ExceptionCode) -> list[ReconException]:
    return [e for e in result.exceptions if e.code is code]


@pytest.mark.parametrize(
    ("prefix", "vat_percent", "expected"),
    [
        ("A", Decimal(15), "0.03"),
        ("A", Decimal(0), "0.0345"),
        ("H", Decimal(15), "0.05"),
        ("H", Decimal(0), "0.0575"),
    ],
)
def test_the_four_contract_rates_are_exact(
    prefix: str, vat_percent: Decimal, expected: str
) -> None:
    booking_id = f"{prefix}260819100005500"
    result = reconcile_travel(
        [portal_row(booking_id=booking_id, vertical="Hotel" if prefix == "H" else "A")],
        [
            claim_line(
                booking_ref=f"{booking_id}_1",
                service_type=prefix,
                vat_percent=vat_percent,
            )
        ],
    )
    assert booking(result, booking_id).values["contract_rate"] == Decimal(expected)


def test_locality_comes_from_vat_not_from_the_claims_label() -> None:
    """The sample has a line marked INT that carries 15% VAT and was billed at
    the local rate. VAT is what the vendor actually charged on."""
    mislabelled = claim_line(
        dom_int_label="INT", vat_percent=Decimal(15), vat=Decimal("130.43")
    )
    assert mislabelled["line_locality"] == "local"


def test_an_international_booking_is_billed_the_rate_plus_vat() -> None:
    values = booking(reconcile_travel([portal_row()], [claim_line()]), BOOKING).values
    assert values["locality"] == "international"
    assert values["contract_rate"] == Decimal("0.0345")
    assert values["commission_as_vendor"] == Decimal("34.50")
    assert values["net_payable_as_vendor"] == Decimal("965.50")
    # We charged 3%; they bill 3.45%. The difference never reached the customer.
    assert values["sg_expected_commission"] == Decimal("30.00")
    assert values["vat_uplift_shortfall"] == Decimal("4.50")


def test_a_local_booking_has_no_uplift() -> None:
    result = reconcile_travel(
        [portal_row(vat=Decimal("130.43"))],
        [
            claim_line(
                vat_percent=Decimal(15), vat=Decimal("130.43"), commission=Decimal("30")
            )
        ],
    )
    values = booking(result, BOOKING).values
    assert values["locality"] == "local"
    assert values["contract_rate"] == Decimal("0.03")
    assert values["vat_uplift_shortfall"] == Decimal(0)


def test_commission_billed_above_the_contract_rate_is_an_overcharge() -> None:
    result = reconcile_travel(
        [
            portal_row(
                booking_id="A260629226265500",
                order_total=Decimal("6536"),
                card_amount=Decimal("6536"),
            )
        ],
        [
            claim_line(
                booking_ref="A260629226265500_1",
                total_invoice=Decimal("6536"),
                commission=Decimal("292.316775"),
            )
        ],
    )
    (overcharge,) = _found(result, ExceptionCode.VENDOR_COMMISSION_OVERCHARGE)
    # 6536 at 3.45% is 225.492; they billed 292.316775.
    assert overcharge.amount == Decimal("66.82")
    assert overcharge.detail == (
        "billed 292.32 where 225.49 is contractual (4.4724% against 3.4500%)"
    )


def test_a_credit_that_cancels_its_invoice_is_a_full_refund() -> None:
    booking_id = "H260810138915500"
    result = reconcile_travel(
        EXPORT,
        claim=[
            claim_line(
                booking_ref=f"{booking_id}_1",
                total_invoice=Decimal("850"),
                commission=Decimal("42.5"),
            ),
            credit_line(
                booking_ref=f"{booking_id}_1",
                total_invoice=Decimal("-850"),
                commission=Decimal("-42.5"),
            ),
        ],
    )
    assert booking(result, booking_id).values["refund"] == "full"


def test_a_retained_fee_makes_a_refund_partial() -> None:
    booking_id = "A26070648655500"
    result = reconcile_travel(
        EXPORT,
        claim=[
            credit_line(
                booking_ref=f"{booking_id}_257964_R0",
                total_invoice=Decimal("-14193"),
                airline_cancel_fee=Decimal("824"),
                commission=Decimal(0),
            )
        ],
    )
    refund = booking(result, booking_id)
    assert refund.values["refund"] == "partial"
    assert refund.verdict == "refunded"
    # Booked 6 Jul, before the export: the credit is for an old booking.
    assert set(_codes(result, booking_id)) == {
        ExceptionCode.PARTIAL_REFUND,
        ExceptionCode.PRIOR_PERIOD,
    }


def test_a_refund_that_leaves_commission_standing_is_reported() -> None:
    """The flight credits carry commission 0, so the invoice's commission stays."""
    result = reconcile_travel(
        EXPORT,
        claim=[
            claim_line(commission=Decimal("34.50")),
            credit_line(total_invoice=Decimal("-1000"), commission=Decimal(0)),
        ],
    )
    assert ExceptionCode.COMMISSION_NOT_REVERSED_ON_REFUND in _codes(result)


def test_a_reversed_commission_is_not_reported() -> None:
    result = reconcile_travel(
        EXPORT,
        claim=[
            claim_line(booking_ref="H260810138915500_1", commission=Decimal("42.5")),
            credit_line(
                booking_ref="H260810138915500_1",
                total_invoice=Decimal("-1000"),
                commission=Decimal("-42.5"),
            ),
        ],
    )
    assert not _found(result, ExceptionCode.COMMISSION_NOT_REVERSED_ON_REFUND)


def test_multi_leg_lines_are_compared_as_one_booking() -> None:
    booking_id = "A260813120395500"
    result = reconcile_travel(
        [
            portal_row(
                booking_id=booking_id,
                order_total=Decimal("958.79"),
                card_amount=Decimal("958.79"),
            )
        ],
        [
            claim_line(
                booking_ref=f"{booking_id}_1",
                total_invoice=Decimal("481.38"),
                commission=Decimal("16.60761"),
            ),
            claim_line(
                booking_ref=f"{booking_id}_1",
                line_item="000002",
                total_invoice=Decimal("477.41"),
                commission=Decimal("16.470645"),
            ),
        ],
    )
    values = booking(result, booking_id).values
    assert values["n_lines"] == 2
    assert values["vendor_base"] == Decimal("958.79")
    assert values["base_diff"] == Decimal(0)
    assert ExceptionCode.MULTI_LEG_BOOKING in _codes(result, booking_id)


def test_a_repeated_portal_row_is_marked_and_never_counted() -> None:
    result = reconcile_travel(
        [portal_row(sg_order_id="100"), portal_row(sg_order_id="101")],
        [claim_line()],
    )
    order = booking(result, BOOKING)
    # Two rows seen, one booking's worth of money.
    assert order.values["portal_rows"] == 2
    assert order.values["portal_base"] == Decimal("1000")
    assert order.values["portal"]["sg_order_id"] == "100"
    (repeat,) = _found(result, ExceptionCode.REPEAT_BOOKING_ROW)
    assert repeat.detail == (
        "portal row 1 of 'portal' repeats this booking; excluded from the totals"
    )


def test_the_earliest_portal_row_is_the_booking_whatever_order_they_come_in() -> None:
    first, second, third = (
        portal_row(sg_order_id="105"),
        portal_row(sg_order_id="107"),
        portal_row(sg_order_id="101"),
    )
    result = reconcile_travel([first, second, third], [claim_line()])
    order = booking(result, BOOKING)
    assert order.values["portal"]["sg_order_id"] == "101"
    # Set aside in the order they gave way: 107 at once, then 105.
    assert [r["sg_order_id"] for r in order.rows["repeats"]] == ["107", "105"]


def test_a_payment_split_that_does_not_add_up_is_reported() -> None:
    result = reconcile_travel(
        [
            portal_row(
                payment_method="Wallet",
                order_total=Decimal("276.25"),
                wallet_amount=Decimal("13.00"),
                card_amount=Decimal(0),
            )
        ]
    )
    (split,) = _found(result, ExceptionCode.PAYMENT_SPLIT_MISMATCH)
    assert split.amount == Decimal("263.25")
    assert split.detail == (
        "Wallet: wallet 13.00 + card 0.00 does not make the order total 276.25"
    )


def test_a_row_with_no_booking_id_is_named_by_its_order() -> None:
    result = reconcile_travel(
        [
            portal_row(
                booking_id=None,
                sg_order_id="198",
                wallet_amount=Decimal("13.00"),
                card_amount=Decimal(0),
            )
        ]
    )
    assert result.entities == []
    (split,) = _found(result, ExceptionCode.PAYMENT_SPLIT_MISMATCH)
    assert split.order_no == "flight order 198"


def test_commission_on_an_expired_order_is_only_reported_when_money_moved() -> None:
    """The portal computes a notional commission on every row, paid or not."""
    unpaid = reconcile_travel([portal_row(status="Expired", payment_status="Pending")])
    assert not _found(unpaid, ExceptionCode.COMMISSION_ON_UNCOMPLETED_ORDER)

    paid = reconcile_travel([portal_row(status="Expired", payment_status="Refunded")])
    (found,) = _found(paid, ExceptionCode.COMMISSION_ON_UNCOMPLETED_ORDER)
    assert found.detail == (
        "paid for (Refunded) but Expired, and commission was still taken"
    )


# --- Why a booking is out of the matched output ---------------------------------

# Against the export (26 Jul to 22 Aug), these two bookings put the claim's own
# week at 16 to 22 Aug.
WEEK = [
    claim_line(booking_ref="A260816000000001_1"),
    claim_line(booking_ref="A260822000000001_1"),
]


def _explain(portal: list[Row], claim: list[Row]) -> tuple[str | None, str]:
    order = booking(reconcile_travel([*EXPORT, *portal], [*claim, *WEEK]), BOOKING)
    return order.verdict, order.values["note"]


def test_a_booking_both_sides_carry_is_matched() -> None:
    assert _explain([portal_row()], [claim_line()]) == ("matched", "")


def test_a_refund_explains_a_booking_before_its_cancellation() -> None:
    reason, detail = _explain(
        [portal_row(status="Cancelled")],
        [
            claim_line(),
            credit_line(
                total_invoice=Decimal("-1000"),
                commission=Decimal("-34.50"),
                net_payable=Decimal("-965.50"),
            ),
        ],
    )
    assert reason == "refunded"
    assert detail == "full refund credited on the claim; the portal shows Cancelled"


def test_a_cancellation_the_claim_still_bills_is_cancelled() -> None:
    reason, detail = _explain([portal_row(status="Cancelled")], [claim_line()])
    assert reason == "cancelled"
    assert (
        detail == "cancelled on the portal, but the claim bills it with no credit note"
    )


def test_a_portal_booking_inside_the_claims_week_is_missing_from_it() -> None:
    reason, detail = _explain([portal_row()], [])
    assert reason == "missing_in_vendor"
    assert detail == (
        "not billed on the claim; booked 2026-08-19, inside this claim's week "
        "(2026-08-16 to 2026-08-22)"
    )


def test_a_portal_booking_from_another_week_is_expected_on_another_claim() -> None:
    result = reconcile_travel(
        [*EXPORT, portal_row(booking_id="A260801100005500")], WEEK
    )
    order = booking(result, "A260801100005500")
    assert order.verdict == "missing_in_vendor"
    assert order.values["note"] == (
        "not billed on the claim; booked 2026-08-01, before this claim's week "
        "(2026-08-16 to 2026-08-22), so expected on another claim"
    )
    # Billed on another week's claim: expected, and only for information.
    (placed,) = _found(result, ExceptionCode.PRIOR_PERIOD)
    assert placed.order_no == "A260801100005500"


def test_a_claim_line_with_no_portal_row_is_missing_from_the_portal() -> None:
    reason, detail = _explain([], [claim_line()])
    assert reason == "missing_in_portal"
    assert detail == (
        "billed on the claim, no portal row; booked 2026-08-19, inside the portal "
        "export (2026-07-26 to 2026-08-22)"
    )


def test_an_unfinished_portal_booking_is_other() -> None:
    reason, detail = _explain([portal_row(status="Pending")], [])
    assert reason == "other"
    assert detail == "Pending on the portal; not on the claim"


def test_a_cancelled_booking_inside_the_claims_week_with_no_credit_is_reported() -> (
    None
):
    """Kept as v1 reported it: a cancellation the claim correctly never billed,
    inside its week, raises the missing line and the missing credit both."""
    result = reconcile_travel([portal_row(status="Cancelled")], WEEK)
    assert _codes(result) == [
        ExceptionCode.BOOKING_MISSING_IN_VENDOR,
        ExceptionCode.CANCELLATION_WITHOUT_VENDOR_CREDIT,
    ]


# --- Lines the claim cannot use ------------------------------------------------


def test_an_adjustment_names_the_booking_it_corrects() -> None:
    adjustment = claim_line(
        booking_ref=None,
        customer_id="Commission refund for A260629226265500",
        invoice_no=None,
        commission=Decimal("-66.82"),
    )
    unlinked = claim_line(
        booking_ref=None,
        customer_id="Rounding",
        invoice_no=None,
        commission=Decimal("1.00"),
    )
    result = reconcile_travel(EXPORT, claim=[adjustment, unlinked])
    order = booking(result, "A260629226265500")
    assert order.verdict == "missing_in_portal"
    assert order.values["vendor_adjustment"] == Decimal("-66.82")
    assert order.values["note"].startswith("adjusted by -66.82 on the claim")
    (applied,) = _found(result, ExceptionCode.ADJUSTMENT_APPLIED)
    assert applied.detail == "Commission refund for A260629226265500"
    (orphan,) = _found(result, ExceptionCode.ADJUSTMENT_UNLINKED)
    assert orphan.detail == "no booking id in 'Rounding'"
    assert orphan.order_no is None


def test_an_invoiced_row_with_no_booking_id_is_reported_not_counted() -> None:
    skipped = claim_line(booking_ref=None, invoice_no="2500000002")
    result = reconcile_travel(EXPORT, claim=[skipped])
    assert result.entities == []
    (found,) = _found(result, ExceptionCode.UNPARSEABLE_VENDOR_ROW)
    assert found.detail == (
        "'claim' row 1: invoiced row carries no booking id (2500000002)"
    )
