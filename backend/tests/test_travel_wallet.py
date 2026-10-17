"""Wallet reconciliation: aggregation, coverage, and an unstable key."""

from datetime import date
from decimal import Decimal

from app.recon.categories.travel.models import (
    BookingStatus,
    TravelPortalRow,
    Vertical,
    WalletEvent,
    WalletState,
)
from app.recon.categories.travel.wallet import WalletHistory, reconcile_wallet
from app.recon.exceptions import ExceptionCode

TOLERANCE = Decimal("0.01")


def event(amount: str, *, order: str = "97", day: int = 9) -> WalletEvent:
    return WalletEvent(
        source="wallet.xlsx",
        row=1,
        vertical=Vertical.HOTEL,
        sg_order_id=order,
        amount=Decimal(amount),
        event_date=date(2026, 8, day),
    )


def row(wallet: str, *, order: str = "97", day: int = 9) -> TravelPortalRow:
    return TravelPortalRow(
        source="portal.xlsx",
        sheet="Hotel",
        row=1,
        sg_order_id=order,
        booking_id=None,
        vertical=Vertical.HOTEL,
        order_date=date(2026, 8, day),
        status=BookingStatus.PROCESSED,
        order_total=Decimal(wallet),
        wallet_amount=Decimal(wallet),
    )


def history(*events: WalletEvent) -> WalletHistory:
    built = WalletHistory()
    for item in events:
        built.add(item)
    return built


def test_a_single_payment_matches() -> None:
    found, exceptions = reconcile_wallet(
        [row("476.73")], history(event("-476.73")), tolerance=TOLERANCE
    )
    assert found.matched == 1
    assert found.checks[0].state is WalletState.MATCHED
    assert exceptions == []


def test_a_retry_loop_matches_on_what_is_left() -> None:
    """One sample order was charged and refunded four times before it stuck."""
    events = []
    for _ in range(4):
        events.append(event("-90.81"))
    for _ in range(3):
        events.append(event("90.81"))
    found, exceptions = reconcile_wallet(
        [row("90.81")], history(*events), tolerance=TOLERANCE
    )
    check = found.checks[0]
    assert check.state is WalletState.MATCHED
    assert check.history_paid == Decimal("363.24")
    assert check.history_refunded == Decimal("272.43")
    assert check.events == 7
    assert exceptions == []


def test_a_refunded_booking_matches_on_what_was_paid() -> None:
    """The portal records the payment; the refund came later."""
    found, _ = reconcile_wallet(
        [row("387.77")],
        history(event("-387.77"), event("387.77")),
        tolerance=TOLERANCE,
    )
    assert found.checks[0].state is WalletState.MATCHED


def test_a_different_amount_is_reported() -> None:
    found, exceptions = reconcile_wallet(
        [row("100.00")], history(event("-60.00")), tolerance=TOLERANCE
    )
    # A key that was found is not evidence of a numbering mismatch.
    assert found.checks[0].state is WalletState.AMOUNT_MISMATCH
    assert found.checks[0].diff == Decimal("40.00")
    assert [e.code for e in exceptions] == [ExceptionCode.WALLET_AMOUNT_MISMATCH]


def test_a_row_outside_the_files_window_is_not_reported_as_missing() -> None:
    """The history covers its own dates, which need not be the export's."""
    covered = history(
        event("-10.00", order="1", day=1), event("-10.00", order="2", day=15)
    )
    found, exceptions = reconcile_wallet(
        [row("50.00", order="99", day=22)], covered, tolerance=TOLERANCE
    )
    assert found.out_of_window == 1
    assert found.missing == 0
    assert next(e.code for e in exceptions) is ExceptionCode.WALLET_OUT_OF_WINDOW


def test_a_row_inside_the_window_with_no_movement_is_missing() -> None:
    covered = history(
        event("-10.00", order="1", day=1), event("-10.00", order="2", day=15)
    )
    found, exceptions = reconcile_wallet(
        [row("50.00", order="99", day=9)], covered, tolerance=TOLERANCE
    )
    assert found.missing == 1
    assert ExceptionCode.WALLET_ENTRY_MISSING in {e.code for e in exceptions}


def test_a_movement_with_no_portal_row_is_reported() -> None:
    found, exceptions = reconcile_wallet(
        [row("10.00", order="1", day=9)],
        history(event("-10.00", order="1", day=9), event("-99.00", order="2", day=9)),
        tolerance=TOLERANCE,
    )
    assert len(found.unmatched_history) == 1
    assert ExceptionCode.WALLET_HISTORY_UNMATCHED in {e.code for e in exceptions}


def test_two_exports_of_different_generations_are_called_out() -> None:
    """SG Order ID is a per-export sequence, so a mismatched pair would
    otherwise report every booking as missing."""
    covered = history(*(event("-10.00", order=str(n), day=9) for n in range(1, 9)))
    rows = [row("50.00", order=str(n), day=9) for n in range(100, 108)]
    _, exceptions = reconcile_wallet(rows, covered, tolerance=TOLERANCE)
    assert ExceptionCode.WALLET_KEY_UNSTABLE in {e.code for e in exceptions}


def test_a_handful_of_gaps_is_not_called_a_numbering_mismatch() -> None:
    """Below the minimum sample it cannot be told from a few real gaps."""
    covered = history(event("-10.00", order="1", day=9))
    rows = [row("50.00", order="99", day=9)]
    _, exceptions = reconcile_wallet(rows, covered, tolerance=TOLERANCE)
    assert ExceptionCode.WALLET_KEY_UNSTABLE not in {e.code for e in exceptions}
