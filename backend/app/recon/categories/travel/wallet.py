"""Wallet reconciliation for travel (plan-travel.md section 3.8).

The check itself is shared by every category (``app.recon.wallet``). What is
travel's own is the rows it checks: every booked portal row with a wallet
share, keyed on its vertical and its ``SG Order ID``.
"""

from decimal import Decimal

from app.recon.categories.travel.models import (
    TravelPortalRow,
    WalletReconciliation,
)
from app.recon.exceptions import ReconException
from app.recon.wallet import WalletHistory as WalletHistory
from app.recon.wallet import WalletPayment
from app.recon.wallet import reconcile_wallet as reconcile_payments


def wallet_payment(row: TravelPortalRow) -> WalletPayment:
    return WalletPayment(
        vertical=row.vertical,
        sg_order_id=row.sg_order_id,
        reference=row.booking_id,
        order_date=row.order_date,
        wallet_amount=row.wallet_amount,
    )


def reconcile_wallet(
    rows: list[TravelPortalRow],
    history: WalletHistory,
    *,
    tolerance: Decimal,
    excluded: list[TravelPortalRow] | None = None,
) -> tuple[WalletReconciliation, list[ReconException]]:
    """Check every wallet-paid row against the history.

    ``excluded`` are portal rows left out of the check (they carry no booking
    id). Their movements still have a portal row, so they are not reported as
    movements without one.
    """
    return reconcile_payments(
        [wallet_payment(row) for row in rows],
        history,
        tolerance=tolerance,
        excluded=[wallet_payment(row) for row in excluded or ()],
    )
