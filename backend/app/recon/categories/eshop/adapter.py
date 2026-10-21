"""The eShop category, wired to the generic pipeline."""

from app.recon.categories.base import CategoryAdapter
from app.recon.categories.eshop import parser, rules
from app.recon.models import PortalLine


def _new_accumulator(line: PortalLine) -> rules.OrderAccumulator:
    return rules.OrderAccumulator(
        order_no=line.order_no,
        order_date=line.order_date,
        merchant_id=line.merchant_id,
    )


ADAPTER: CategoryAdapter[rules.OrderAccumulator] = CategoryAdapter(
    name="eshop",
    portal_schema=parser.PORTAL_SCHEMA,
    ledger_schema=parser.LEDGER_SCHEMA,
    promo_schema=parser.PROMO_SCHEMA,
    settlement_schema=parser.SETTLEMENT_SCHEMA,
    read_portal=parser.read_portal,
    read_ledger=parser.read_ledger,
    read_promo=parser.read_promo,
    read_settlements=parser.read_settlements,
    new_accumulator=_new_accumulator,
    evaluate_line=rules.evaluate_line,
    build_order=rules.build_order,
    wallet_type=parser.wallet_type,
    wallet_vertical=parser.WALLET_VERTICAL,
    wallet_order_id=parser.wallet_order_id,
)
