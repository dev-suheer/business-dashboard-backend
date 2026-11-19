"""The wallet check: every wallet-paid row proved against the wallet history.

The check itself is the library's (``app.recon.wallet``); the configuration
says which rows are checked and which are left out, how each one's vertical,
order id, reference, date and wallet amount are read, and which Wallet Type
texts of the history name each vertical.
"""

from collections.abc import Sequence
from typing import Any

from app.config_model.models import WalletCheck
from app.expr import Scope, evaluate
from app.expr import text as as_text
from app.recon.cleaning import normalize_header
from app.recon.exceptions import ReconException
from app.recon.inputs import Upload
from app.recon.models import SheetManifest
from app.recon.wallet import (
    WalletPayment,
    WalletReconciliation,
    load_wallet,
    reconcile_wallet,
)


def check_wallet(
    spec: WalletCheck,
    upload: Upload,
    rows: Sequence[Scope],
    excluded: Sequence[Scope],
    *,
    scope: Scope,
    max_rows: int,
    detected: list[SheetManifest],
) -> tuple[WalletReconciliation, list[ReconException]]:
    def key(raw: Any) -> str:
        text = normalize_header(raw)
        for char in spec.strip:
            text = text.replace(char, "")
        return text

    verticals = {key(name): vertical for name, vertical in spec.wallet_types.items()}

    def wallet_type(raw: Any) -> str | None:
        return verticals.get(key(raw))

    history, sheet = load_wallet(upload, wallet_type=wallet_type, max_rows=max_rows)
    detected.append(
        SheetManifest(
            role=upload.role,
            filename=upload.filename,
            sheets=[sheet],
            rows=history.events,
        )
    )
    return reconcile_wallet(
        [_payment(spec, row) for row in rows],
        history,
        tolerance=evaluate(spec.tolerance, scope),
        excluded=[_payment(spec, row) for row in excluded],
    )


def _payment(spec: WalletCheck, row: Scope) -> WalletPayment:
    return WalletPayment(
        vertical=as_text(evaluate(spec.vertical, row)),
        sg_order_id=evaluate(spec.sg_order_id, row),
        reference=evaluate(spec.reference, row),
        order_date=evaluate(spec.order_date, row),
        wallet_amount=evaluate(spec.amount, row),
    )
