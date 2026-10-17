"""Travel business rules: rates, refunds, grouping and the payment split."""

from datetime import date
from decimal import Decimal

import pytest

from app.recon.categories.travel import rules
from app.recon.categories.travel.models import (
    BillingType,
    BookingStatus,
    Locality,
    PaymentStatus,
    RefundKind,
    TravelInvoiceLine,
    TravelPortalRow,
    TravelRateConfig,
    UnmatchedReason,
    Vertical,
)
from app.recon.exceptions import ExceptionCode


def portal_row(**overrides: object) -> TravelPortalRow:
    values: dict[str, object] = {
        "source": "portal.xlsx",
        "sheet": "Flight",
        "row": 1,
        "sg_order_id": "100",
        "booking_id": "A260819100005500",
        "vertical": Vertical.FLIGHT,
        "order_date": date(2026, 8, 19),
        "status": BookingStatus.PROCESSED,
        "payment_status": PaymentStatus.PAID,
        "payment_method": "Card",
        "order_total": Decimal("1000"),
        "vat": Decimal(0),
        "commission_charged": Decimal("30"),
        "net_payable": Decimal("970"),
        "card_amount": Decimal("1000"),
    }
    values.update(overrides)
    return TravelPortalRow(**values)  # type: ignore[arg-type]


def invoice_line(**overrides: object) -> TravelInvoiceLine:
    values: dict[str, object] = {
        "source": "claim.xlsx",
        "sheet": "Sheet1",
        "row": 1,
        "booking_id": "A260819100005500",
        "booking_ref": "A260819100005500_1",
        "invoice_no": "2500000001",
        "line_item": "000001",
        "billing_type": BillingType.INVOICE,
        "service_type": "A",
        "issue_date": date(2026, 8, 19),
        "total_invoice": Decimal("1000"),
        "commission": Decimal("34.50"),
        "net_payable": Decimal("965.50"),
    }
    values.update(overrides)
    return TravelInvoiceLine(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("vertical", "locality", "expected"),
    [
        (Vertical.FLIGHT, Locality.LOCAL, "0.03"),
        (Vertical.FLIGHT, Locality.INTERNATIONAL, "0.0345"),
        (Vertical.HOTEL, Locality.LOCAL, "0.05"),
        (Vertical.HOTEL, Locality.INTERNATIONAL, "0.0575"),
    ],
)
def test_the_four_contract_rates_are_exact(
    travel_rates: TravelRateConfig,
    vertical: Vertical,
    locality: Locality,
    expected: str,
) -> None:
    assert travel_rates.contract_rate(vertical, locality) == Decimal(expected)


def test_locality_comes_from_vat_not_from_the_claims_label() -> None:
    """The sample has a line marked INT that carries 15% VAT and was billed at
    the local rate. VAT is what the vendor actually charged on."""
    mislabelled = invoice_line(
        dom_int_label="INT", vat_percent=Decimal(15), vat=Decimal("130.43")
    )
    assert rules.locality_of_line(mislabelled) is Locality.LOCAL


def test_an_international_booking_is_billed_the_rate_plus_vat(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A260819100005500")
    acc.add_portal(portal_row())
    acc.add_line(invoice_line(), travel_rates)
    booking = rules.build_booking(acc, travel_rates)

    assert booking.locality is Locality.INTERNATIONAL
    assert booking.contract_rate == Decimal("0.0345")
    assert booking.commission_as_vendor == Decimal("34.50")
    assert booking.net_payable_as_vendor == Decimal("965.50")
    # We charged 3%; they bill 3.45%. The difference never reached the customer.
    assert booking.sg_expected_commission == Decimal("30.00")
    assert booking.vat_uplift_shortfall == Decimal("4.50")


def test_a_local_booking_has_no_uplift(travel_rates: TravelRateConfig) -> None:
    acc = rules.BookingAccumulator(booking_id="A260819100005500")
    acc.add_portal(portal_row(vat=Decimal("130.43")))
    acc.add_line(
        invoice_line(
            vat_percent=Decimal(15), vat=Decimal("130.43"), commission=Decimal("30")
        ),
        travel_rates,
    )
    booking = rules.build_booking(acc, travel_rates)

    assert booking.locality is Locality.LOCAL
    assert booking.contract_rate == Decimal("0.03")
    assert booking.vat_uplift_shortfall == Decimal(0)


def test_commission_billed_above_the_contract_rate_is_an_overcharge(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A260629226265500")
    acc.add_portal(
        portal_row(booking_id="A260629226265500", order_total=Decimal("6536"))
    )
    acc.add_line(
        invoice_line(
            booking_id="A260629226265500",
            total_invoice=Decimal("6536"),
            commission=Decimal("292.316775"),
        ),
        travel_rates,
    )
    booking = rules.build_booking(acc, travel_rates)
    found = rules.booking_exceptions(
        booking,
        acc,
        travel_rates,
        export=rules.Window(date(2026, 6, 1), date(2026, 8, 31)),
        claim=None,
    )
    overcharge = next(
        e for e in found if e.code is ExceptionCode.VENDOR_COMMISSION_OVERCHARGE
    )
    # 6536 at 3.45% is 225.492; they billed 292.316775.
    assert overcharge.amount == Decimal("66.82")


def test_a_credit_that_cancels_its_invoice_is_a_full_refund(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="H260810138915500")
    acc.add_line(
        invoice_line(total_invoice=Decimal("850"), commission=Decimal("42.5")),
        travel_rates,
    )
    acc.add_line(
        invoice_line(
            line_item="000002",
            billing_type=BillingType.CREDIT_FOR_RETURNS,
            total_invoice=Decimal("-850"),
            commission=Decimal("-42.5"),
        ),
        travel_rates,
    )
    assert rules.refund_kind(acc, travel_rates) is RefundKind.FULL


def test_a_retained_fee_makes_a_refund_partial(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A26070648655500")
    acc.add_line(
        invoice_line(
            billing_type=BillingType.CREDIT_FOR_RETURNS,
            booking_ref="A26070648655500_257964_R0",
            is_reversal_ref=True,
            total_invoice=Decimal("-14193"),
            airline_cancel_fee=Decimal("824"),
            commission=Decimal(0),
        ),
        travel_rates,
    )
    assert rules.refund_kind(acc, travel_rates) is RefundKind.PARTIAL


def test_a_refund_that_leaves_commission_standing_is_reported(
    travel_rates: TravelRateConfig,
) -> None:
    """The flight credits carry commission 0, so the invoice's commission stays."""
    acc = rules.BookingAccumulator(booking_id="A260819100005500")
    acc.add_line(invoice_line(commission=Decimal("34.50")), travel_rates)
    acc.add_line(
        invoice_line(
            line_item="000002",
            billing_type=BillingType.CREDIT_FOR_RETURNS,
            total_invoice=Decimal("-1000"),
            commission=Decimal(0),
        ),
        travel_rates,
    )
    booking = rules.build_booking(acc, travel_rates)
    found = rules.booking_exceptions(
        booking,
        acc,
        travel_rates,
        export=rules.Window(date(2026, 8, 1), date(2026, 8, 31)),
        claim=None,
    )
    assert any(e.code is ExceptionCode.COMMISSION_NOT_REVERSED_ON_REFUND for e in found)


def test_a_reversed_commission_is_not_reported(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="H260810138915500")
    acc.add_line(invoice_line(commission=Decimal("42.5")), travel_rates)
    acc.add_line(
        invoice_line(
            line_item="000002",
            billing_type=BillingType.CREDIT_FOR_RETURNS,
            total_invoice=Decimal("-1000"),
            commission=Decimal("-42.5"),
        ),
        travel_rates,
    )
    booking = rules.build_booking(acc, travel_rates)
    found = rules.booking_exceptions(
        booking,
        acc,
        travel_rates,
        export=rules.Window(date(2026, 8, 1), date(2026, 8, 31)),
        claim=None,
    )
    assert not any(
        e.code is ExceptionCode.COMMISSION_NOT_REVERSED_ON_REFUND for e in found
    )


def test_multi_leg_lines_are_compared_as_one_booking(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A260813120395500")
    acc.add_portal(
        portal_row(booking_id="A260813120395500", order_total=Decimal("958.79"))
    )
    acc.add_line(
        invoice_line(total_invoice=Decimal("481.38"), commission=Decimal("16.60761")),
        travel_rates,
    )
    acc.add_line(
        invoice_line(
            line_item="000002",
            total_invoice=Decimal("477.41"),
            commission=Decimal("16.470645"),
        ),
        travel_rates,
    )
    booking = rules.build_booking(acc, travel_rates)

    assert booking.vendor_lines == 2
    assert booking.vendor_base == Decimal("958.79")
    assert booking.base_diff == Decimal(0)
    assert "MULTI_LEG" in booking.flags


def test_a_repeated_portal_row_is_marked_and_never_counted(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A260819100005500")
    acc.add_portal(portal_row(sg_order_id="100", order_total=Decimal("1000")))
    acc.add_portal(portal_row(sg_order_id="101", row=2, order_total=Decimal("1000")))
    acc.add_line(invoice_line(), travel_rates)
    booking = rules.build_booking(acc, travel_rates)

    # Two rows seen, one booking's worth of money.
    assert booking.portal_rows == 2
    assert booking.portal_base == Decimal("1000")
    found = rules.booking_exceptions(
        booking,
        acc,
        travel_rates,
        export=rules.Window(date(2026, 8, 1), date(2026, 8, 31)),
        claim=None,
    )
    assert sum(1 for e in found if e.code is ExceptionCode.REPEAT_BOOKING_ROW) == 1


def test_a_payment_split_that_does_not_add_up_is_reported(
    travel_rates: TravelRateConfig,
) -> None:
    row = portal_row(
        payment_method="Wallet",
        order_total=Decimal("276.25"),
        wallet_amount=Decimal("13.00"),
        card_amount=Decimal(0),
    )
    found = rules.row_exceptions(row, travel_rates)
    assert [e.code for e in found] == [ExceptionCode.PAYMENT_SPLIT_MISMATCH]
    assert found[0].amount == Decimal("263.25")


def test_commission_on_an_expired_order_is_only_reported_when_money_moved(
    travel_rates: TravelRateConfig,
) -> None:
    """The portal computes a notional commission on every row, paid or not."""
    unpaid = portal_row(
        status=BookingStatus.EXPIRED, payment_status=PaymentStatus.PENDING
    )
    assert rules.row_exceptions(unpaid, travel_rates) == []

    paid = portal_row(
        status=BookingStatus.EXPIRED, payment_status=PaymentStatus.REFUNDED
    )
    assert [e.code for e in rules.row_exceptions(paid, travel_rates)] == [
        ExceptionCode.COMMISSION_ON_UNCOMPLETED_ORDER
    ]


def test_payment_methods_are_cut_by_label_and_by_instrument() -> None:
    methods = rules.PaymentMethodAccumulator()
    methods.add(
        portal_row(
            payment_method="Apple Pay + Wallet",
            order_total=Decimal("100"),
            wallet_amount=Decimal("40"),
            card_amount=Decimal("60"),
        )
    )
    methods.add(
        portal_row(
            payment_method=None, order_total=Decimal("10"), card_amount=Decimal("10")
        )
    )
    summary = methods.build()

    by_label = {m.label: m for m in summary.by_label}
    assert by_label["Apple Pay + Wallet"].order_total == Decimal("100")
    assert by_label[rules.BLANK_METHOD].order_total == Decimal("10")

    by_instrument = {m.label: m for m in summary.by_instrument}
    # The combined payment is split at what actually moved on each instrument.
    assert by_instrument["Wallet"].order_total == Decimal("40")
    assert by_instrument["Apple Pay"].order_total == Decimal("60")


EXPORT = rules.Window(start=date(2026, 7, 26), end=date(2026, 8, 22))
CLAIM = rules.Window(start=date(2026, 8, 16), end=date(2026, 8, 22))


def explain(
    acc: rules.BookingAccumulator, rates: TravelRateConfig
) -> tuple[UnmatchedReason | None, str]:
    booking = rules.build_booking(acc, rates)
    return rules.explain(booking, export=EXPORT, claim=CLAIM)


def test_a_booking_both_sides_carry_is_matched(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A260819100005500")
    acc.add_portal(portal_row())
    acc.add_line(invoice_line(), travel_rates)
    assert explain(acc, travel_rates) == (None, "")


def test_a_refund_explains_a_booking_before_its_cancellation(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A260819100005500")
    acc.add_portal(portal_row(status=BookingStatus.CANCELLED))
    acc.add_line(invoice_line(), travel_rates)
    acc.add_line(
        invoice_line(
            line_item="000002",
            billing_type=BillingType.CREDIT_FOR_RETURNS,
            total_invoice=Decimal("-1000"),
            commission=Decimal("-34.50"),
            net_payable=Decimal("-965.50"),
        ),
        travel_rates,
    )
    reason, detail = explain(acc, travel_rates)
    assert reason is UnmatchedReason.REFUNDED
    assert detail == "full refund credited on the claim; the portal shows Cancelled"


def test_a_cancellation_the_claim_still_bills_is_cancelled(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A260819100005500")
    acc.add_portal(portal_row(status=BookingStatus.CANCELLED))
    acc.add_line(invoice_line(), travel_rates)
    reason, detail = explain(acc, travel_rates)
    assert reason is UnmatchedReason.CANCELLED
    assert "no credit note" in detail


def test_a_portal_booking_inside_the_claims_week_is_missing_from_it(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A260819100005500")
    acc.add_portal(portal_row())
    reason, detail = explain(acc, travel_rates)
    assert reason is UnmatchedReason.MISSING_IN_VENDOR
    assert detail == (
        "not billed on the claim; booked 2026-08-19, inside this claim's week "
        "(2026-08-16 to 2026-08-22)"
    )


def test_a_portal_booking_from_another_week_is_expected_on_another_claim(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A260801100005500")
    acc.add_portal(portal_row(booking_id="A260801100005500"))
    reason, detail = explain(acc, travel_rates)
    assert reason is UnmatchedReason.MISSING_IN_VENDOR
    assert "before this claim's week" in detail
    assert detail.endswith("so expected on another claim")


def test_a_claim_line_with_no_portal_row_is_missing_from_the_portal(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A260819100005500")
    acc.add_line(invoice_line(), travel_rates)
    reason, detail = explain(acc, travel_rates)
    assert reason is UnmatchedReason.MISSING_IN_PORTAL
    assert detail.startswith("billed on the claim, no portal row")
    assert "inside the portal export" in detail


def test_an_unfinished_portal_booking_is_other(
    travel_rates: TravelRateConfig,
) -> None:
    acc = rules.BookingAccumulator(booking_id="A260819100005500")
    acc.add_portal(portal_row(status=BookingStatus.PENDING))
    reason, detail = explain(acc, travel_rates)
    assert reason is UnmatchedReason.OTHER
    assert detail == "Pending on the portal; not on the claim"
