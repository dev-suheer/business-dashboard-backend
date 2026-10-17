"""The reconciliation exception taxonomy (plan.md, plan-travel.md, plan-food.md).

One code means one thing. Where travel needs a check eshop also makes, it gets
its own code rather than borrowing one, so that reading an exception never
requires knowing which category produced it.
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


class ExceptionCode(StrEnum):
    ORDER_MISSING_IN_VENDOR = "ORDER_MISSING_IN_VENDOR"
    MISSING_FROM_PORTAL = "MISSING_FROM_PORTAL"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    COMMISSION_OVERCHARGE = "COMMISSION_OVERCHARGE"
    COMMISSION_WRONG_RATE = "COMMISSION_WRONG_RATE"
    RTO_WITHOUT_CREDIT_NOTE = "RTO_WITHOUT_CREDIT_NOTE"
    CREDIT_NOTE_WITHOUT_RTO = "CREDIT_NOTE_WITHOUT_RTO"
    AMBIGUOUS_CREDIT_NOTE = "AMBIGUOUS_CREDIT_NOTE"
    CLAWBACK_UNRESOLVED = "CLAWBACK_UNRESOLVED"
    UNPARSEABLE_LEDGER_ROW = "UNPARSEABLE_LEDGER_ROW"
    PROMO_ABSORBED = "PROMO_ABSORBED"
    PENDING_DELIVERY = "PENDING_DELIVERY"
    PRIOR_PERIOD = "PRIOR_PERIOD"
    LATER_PERIOD = "LATER_PERIOD"

    # --- travel (plan-travel.md section 6) ---------------------------------
    BOOKING_MISSING_IN_VENDOR = "BOOKING_MISSING_IN_VENDOR"
    BOOKING_MISSING_IN_PORTAL = "BOOKING_MISSING_IN_PORTAL"
    BOOKING_BASE_MISMATCH = "BOOKING_BASE_MISMATCH"
    VENDOR_COMMISSION_OVERCHARGE = "VENDOR_COMMISSION_OVERCHARGE"
    VENDOR_COMMISSION_UNDERCHARGE = "VENDOR_COMMISSION_UNDERCHARGE"
    SG_COMMISSION_SHORT_OF_VENDOR_RATE = "SG_COMMISSION_SHORT_OF_VENDOR_RATE"
    SG_COMMISSION_RATE_UNEXPECTED = "SG_COMMISSION_RATE_UNEXPECTED"
    COMMISSION_NOT_REVERSED_ON_REFUND = "COMMISSION_NOT_REVERSED_ON_REFUND"
    REFUND_WITHOUT_PORTAL_CANCELLATION = "REFUND_WITHOUT_PORTAL_CANCELLATION"
    CANCELLATION_WITHOUT_VENDOR_CREDIT = "CANCELLATION_WITHOUT_VENDOR_CREDIT"
    PARTIAL_REFUND = "PARTIAL_REFUND"
    PAYMENT_SPLIT_MISMATCH = "PAYMENT_SPLIT_MISMATCH"
    COMMISSION_ON_UNCOMPLETED_ORDER = "COMMISSION_ON_UNCOMPLETED_ORDER"
    VERTICAL_CONFLICT = "VERTICAL_CONFLICT"
    VAT_BASIS_CONFLICT = "VAT_BASIS_CONFLICT"
    REPEAT_BOOKING_ROW = "REPEAT_BOOKING_ROW"
    MULTI_LEG_BOOKING = "MULTI_LEG_BOOKING"
    ADJUSTMENT_APPLIED = "ADJUSTMENT_APPLIED"
    ADJUSTMENT_UNLINKED = "ADJUSTMENT_UNLINKED"
    DUPLICATE_VENDOR_LINE = "DUPLICATE_VENDOR_LINE"
    UNPARSEABLE_VENDOR_ROW = "UNPARSEABLE_VENDOR_ROW"
    WALLET_ENTRY_MISSING = "WALLET_ENTRY_MISSING"
    WALLET_AMOUNT_MISMATCH = "WALLET_AMOUNT_MISMATCH"
    WALLET_OUT_OF_WINDOW = "WALLET_OUT_OF_WINDOW"
    WALLET_HISTORY_UNMATCHED = "WALLET_HISTORY_UNMATCHED"
    WALLET_KEY_UNSTABLE = "WALLET_KEY_UNSTABLE"

    # --- food (plan-food.md section 6) -------------------------------------
    FOOD_AMOUNT_MISMATCH = "FOOD_AMOUNT_MISMATCH"
    FEE_MISMATCH = "FEE_MISMATCH"
    DELIVERY_SPLIT_MISMATCH = "DELIVERY_SPLIT_MISMATCH"
    STATUS_MISMATCH = "STATUS_MISMATCH"
    DUPLICATE_ORDER_ID = "DUPLICATE_ORDER_ID"
    REFUND_ON_DELIVERED = "REFUND_ON_DELIVERED"
    STATUS_NOT_DELIVERED = "STATUS_NOT_DELIVERED"


SEVERITIES: dict[ExceptionCode, Severity] = {
    ExceptionCode.ORDER_MISSING_IN_VENDOR: Severity.HIGH,
    ExceptionCode.MISSING_FROM_PORTAL: Severity.HIGH,
    ExceptionCode.AMOUNT_MISMATCH: Severity.HIGH,
    ExceptionCode.COMMISSION_OVERCHARGE: Severity.HIGH,
    ExceptionCode.COMMISSION_WRONG_RATE: Severity.HIGH,
    ExceptionCode.RTO_WITHOUT_CREDIT_NOTE: Severity.HIGH,
    ExceptionCode.CREDIT_NOTE_WITHOUT_RTO: Severity.HIGH,
    ExceptionCode.AMBIGUOUS_CREDIT_NOTE: Severity.HIGH,
    ExceptionCode.CLAWBACK_UNRESOLVED: Severity.HIGH,
    ExceptionCode.UNPARSEABLE_LEDGER_ROW: Severity.MEDIUM,
    ExceptionCode.PROMO_ABSORBED: Severity.INFO,
    ExceptionCode.PENDING_DELIVERY: Severity.INFO,
    ExceptionCode.PRIOR_PERIOD: Severity.INFO,
    ExceptionCode.LATER_PERIOD: Severity.INFO,
    ExceptionCode.BOOKING_MISSING_IN_VENDOR: Severity.HIGH,
    ExceptionCode.BOOKING_MISSING_IN_PORTAL: Severity.HIGH,
    ExceptionCode.BOOKING_BASE_MISMATCH: Severity.HIGH,
    ExceptionCode.VENDOR_COMMISSION_OVERCHARGE: Severity.HIGH,
    ExceptionCode.VENDOR_COMMISSION_UNDERCHARGE: Severity.MEDIUM,
    ExceptionCode.SG_COMMISSION_SHORT_OF_VENDOR_RATE: Severity.HIGH,
    ExceptionCode.SG_COMMISSION_RATE_UNEXPECTED: Severity.HIGH,
    ExceptionCode.COMMISSION_NOT_REVERSED_ON_REFUND: Severity.HIGH,
    ExceptionCode.REFUND_WITHOUT_PORTAL_CANCELLATION: Severity.HIGH,
    ExceptionCode.CANCELLATION_WITHOUT_VENDOR_CREDIT: Severity.HIGH,
    ExceptionCode.PARTIAL_REFUND: Severity.INFO,
    ExceptionCode.PAYMENT_SPLIT_MISMATCH: Severity.HIGH,
    ExceptionCode.COMMISSION_ON_UNCOMPLETED_ORDER: Severity.MEDIUM,
    ExceptionCode.VERTICAL_CONFLICT: Severity.HIGH,
    ExceptionCode.VAT_BASIS_CONFLICT: Severity.MEDIUM,
    ExceptionCode.REPEAT_BOOKING_ROW: Severity.INFO,
    ExceptionCode.MULTI_LEG_BOOKING: Severity.INFO,
    ExceptionCode.ADJUSTMENT_APPLIED: Severity.INFO,
    ExceptionCode.ADJUSTMENT_UNLINKED: Severity.MEDIUM,
    ExceptionCode.DUPLICATE_VENDOR_LINE: Severity.INFO,
    ExceptionCode.UNPARSEABLE_VENDOR_ROW: Severity.MEDIUM,
    ExceptionCode.WALLET_ENTRY_MISSING: Severity.HIGH,
    ExceptionCode.WALLET_AMOUNT_MISMATCH: Severity.HIGH,
    ExceptionCode.WALLET_OUT_OF_WINDOW: Severity.INFO,
    ExceptionCode.WALLET_HISTORY_UNMATCHED: Severity.INFO,
    ExceptionCode.WALLET_KEY_UNSTABLE: Severity.MEDIUM,
    ExceptionCode.FOOD_AMOUNT_MISMATCH: Severity.HIGH,
    ExceptionCode.FEE_MISMATCH: Severity.HIGH,
    ExceptionCode.DELIVERY_SPLIT_MISMATCH: Severity.HIGH,
    ExceptionCode.STATUS_MISMATCH: Severity.HIGH,
    ExceptionCode.DUPLICATE_ORDER_ID: Severity.HIGH,
    ExceptionCode.REFUND_ON_DELIVERED: Severity.HIGH,
    ExceptionCode.STATUS_NOT_DELIVERED: Severity.INFO,
}


class ReconException(BaseModel):
    code: ExceptionCode
    severity: Severity
    order_no: str | None = None
    amount: Decimal | None = None
    detail: str = ""


def make_exception(
    code: ExceptionCode,
    *,
    order_no: str | None = None,
    amount: Decimal | None = None,
    detail: str = "",
) -> ReconException:
    """Build an exception with the severity its code mandates.

    Severity is never passed in by the caller, so a code cannot be reported at
    two different severities from two different places.
    """
    return ReconException(
        code=code,
        severity=SEVERITIES[code],
        order_no=order_no,
        amount=amount,
        detail=detail,
    )


def sort_exceptions(exceptions: list[ReconException]) -> list[ReconException]:
    """Most severe first, then grouped by code, so the list reads top-down."""
    order = {"high": 0, "medium": 1, "info": 2}
    return sorted(
        exceptions,
        key=lambda e: (order[str(e.severity)], str(e.code), e.order_no or ""),
    )
