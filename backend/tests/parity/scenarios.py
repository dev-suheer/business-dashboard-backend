"""The fixed set of runs that every v2 phase must reproduce exactly.

The client has approved today's results. v2 moves the rules into the database
and replaces each category's pipeline with a generic runner, and none of that
may change a single figure or cell. Each scenario here is run once with
today's code (``scripts/capture_baseline.py``) and the result and report are
kept under ``tests/baseline/``; ``tests/test_parity.py`` then runs the same
scenario with the current code and compares.

``produce`` is the one place that says how a run is made. When a phase changes
the production path, ``produce`` changes with it, and the baseline does not.
Since Phase 2 it runs under version 1 of the category's configuration
(``app/seed/v1/``), as production does.

A category on the generic runner (Phase 4) stores its result in another shape,
so both sides are compared as a projection (``expected``/``produce``): the
summary figures, the vendor, each order's verdict, due and exception codes, and
every exception, wallet check and detected sheet. The report is still compared
cell for cell.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config_model.models import CategoryConfig
from app.config_model.runtime import use_config
from app.config_model.seed import load_seed
from app.core.config import settings
from app.recon.inputs import Upload
from app.render import generic_report
from app.runner import run as run_generic
from tests.conftest import (
    FOOD_PORTAL,
    FOOD_VENDOR,
    TRAVEL_CLAIMS,
    TRAVEL_PORTAL,
    TRAVEL_WALLET,
    VENDOR_STATEMENT,
    WORKINGS,
    write_finance_report,
)

BASELINE = Path(__file__).resolve().parents[1] / "baseline"
INPUTS = BASELINE / "inputs"

# Finance reports are built, not shipped, so the baseline keeps the exact files
# it was captured with. Each one names an order the category's sample holds.
FINANCE_INPUTS = {
    "eshop": (INPUTS / "finance-eshop.xlsx", [8986], "eshop"),
    "travel": (INPUTS / "finance-travel.xlsx", [121], "hotel"),
    "food": (INPUTS / "finance-food.xlsx", [59709], "food"),
}


@dataclass(frozen=True)
class Scenario:
    name: str
    category: str
    files: dict[str, Path] = field(default_factory=dict)


def _finance(category: str) -> Path:
    return FINANCE_INPUTS[category][0]


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "eshop",
        "eshop",
        {"portal": WORKINGS, "promo": WORKINGS, "vendor": VENDOR_STATEMENT},
    ),
    Scenario(
        "eshop-wallet-finance",
        "eshop",
        {
            "portal": WORKINGS,
            "promo": WORKINGS,
            "vendor": VENDOR_STATEMENT,
            "wallet": TRAVEL_WALLET,
            "finance": _finance("eshop"),
        },
    ),
    *(
        Scenario(
            f"travel-claim-{claim}",
            "travel",
            {"portal": TRAVEL_PORTAL, "vendor": path, "wallet": TRAVEL_WALLET},
        )
        for claim, path in TRAVEL_CLAIMS.items()
    ),
    Scenario(
        "travel-claim-13-no-wallet",
        "travel",
        {"portal": TRAVEL_PORTAL, "vendor": TRAVEL_CLAIMS["13"]},
    ),
    Scenario(
        "travel-claim-13-finance",
        "travel",
        {
            "portal": TRAVEL_PORTAL,
            "vendor": TRAVEL_CLAIMS["13"],
            "wallet": TRAVEL_WALLET,
            "finance": _finance("travel"),
        },
    ),
    Scenario("food", "food", {"portal": FOOD_PORTAL, "vendor": FOOD_VENDOR}),
    Scenario(
        "food-wallet-finance",
        "food",
        {
            "portal": FOOD_PORTAL,
            "vendor": FOOD_VENDOR,
            "wallet": TRAVEL_WALLET,
            "finance": _finance("food"),
        },
    ),
)


def write_finance_inputs() -> None:
    """Build the finance reports the scenarios use. Run once, at capture time."""
    INPUTS.mkdir(parents=True, exist_ok=True)
    for path, order_ids, vertical in FINANCE_INPUTS.values():
        write_finance_report(path, order_ids, vertical)


def produce(scenario: Scenario, out_dir: Path) -> tuple[dict[str, Any], Path]:
    """Run one scenario the way production does: the result as JSON, and the report."""
    with use_config(load_seed(scenario.category)) as config:
        return _produce(scenario, out_dir, config)


def expected(scenario: Scenario) -> dict[str, Any]:
    """The baseline result, in the shape ``produce`` returns for its category."""
    baseline: dict[str, Any] = json.loads(
        (BASELINE / scenario.name / "result.json").read_text()
    )
    orders, order = _BASELINE_ORDERS[scenario.category]
    return _plain(
        {
            "category": baseline["category"],
            "vendor_name": baseline.get("vendor_name", baseline.get("merchant_id")),
            "summary": {
                key: value
                for key, value in baseline["summary"].items()
                # A breakdown by reason or code is not a figure.
                if not isinstance(value, dict | list)
            },
            "orders": [order(item) for item in baseline[orders]],
            "exceptions": baseline["exceptions"],
            "wallet": baseline["wallet"],
            "detected": _by_role(baseline["detected"]),
        }
    )


def comparable(scenario: Scenario, result: dict[str, Any]) -> dict[str, Any]:
    """A stored result, in the shape ``expected`` gives its baseline."""
    figures = expected(scenario)["summary"]
    metrics = result["metrics"]
    entity = _RUNNER_ORDERS[scenario.category]
    return _plain(
        {
            "category": result["category"],
            "vendor_name": metrics.get("vendor_name", metrics.get("merchant_id")),
            "summary": {key: metrics[key] for key in figures},
            "orders": [
                entity(item)
                for item in result["entities"]
                # eShop's statement-only orders were never orders in v1.
                if item["verdict"] != "statement_only"
            ],
            "exceptions": result["exceptions"],
            "wallet": result["wallet"],
            "detected": _by_role(result["detected"]),
        }
    )


def _plain[T](value: T) -> T:
    """Counts were whole numbers; the runner's figures are all decimals."""
    plain: Any = value
    if isinstance(value, dict):
        plain = {key: _plain(item) for key, item in value.items()}
    elif isinstance(value, list):
        plain = [_plain(item) for item in value]
    elif isinstance(value, int) and not isinstance(value, bool):
        plain = str(value)
    return plain  # type: ignore[no-any-return]


def _by_role(detected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The sheets read, by role: the runner reads the uploads in the order the
    upload screen lists them, v1 read the claim before the portal."""
    return sorted(detected, key=lambda item: item["role"])


# Each order as v1 stored it and as the runner does: its id, its verdict and
# why, and every figure it was reported with.
_TRAVEL_FIGURES = (
    "match_state",
    "refund",
    "locality",
    "vertical",
    "booking_date",
    "order_date",
    "portal_rows",
    "portal_base",
    "portal_commission",
    "portal_net_payable",
    "portal_status",
    "portal_payment_status",
    "payment_method",
    "wallet_amount",
    "card_amount",
    "vendor_base",
    "vendor_commission",
    "vendor_net_payable",
    "vendor_cancel_fee",
    "vendor_change_fee",
    "vendor_adjustment",
    "sg_rate",
    "vendor_rate",
    "contract_rate",
    "sg_expected_commission",
    "vendor_expected_commission",
    "base_diff",
    "commission_diff",
    "net_payable_diff",
    "commission_as_vendor",
    "net_payable_as_vendor",
    "commission_diff_as_vendor",
    "net_payable_diff_as_vendor",
    "vat_uplift_shortfall",
)

_ESHOP_FIGURES = (
    "order_date",
    "merchant_id",
    "portal_gmv",
    "vendor_amount",
    "amount_diff",
    "vendor_credit",
    "vendor_date",
    "expected_commission",
    "actual_commission",
    "commission_variance",
    "variance_class",
    "promo_base",
    "standard_base",
    "absorbed_commission",
    "payable",
    "adjustments",
    "net_payable",
    "payment_type",
    "wallet_amount",
    "non_wallet_amount",
    "coupon_value",
    "shipping_cost",
    "sg_shipping",
)

_BASELINE_ORDERS: dict[str, tuple[str, Any]] = {
    "eshop": (
        "orders",
        lambda order: {
            "order_no": order["order_no"],
            "lines": order["line_count"],
            "statuses": ", ".join(order["statuses"]),
            "treatment": order["flags"][0] if order["flags"] else None,
            "included": order["included_in_payable"],
            "matched": order["match_state"] == "matched",
            **{name: order[name] for name in _ESHOP_FIGURES},
        },
    ),
    "food": (
        "orders",
        lambda order: {
            "order_id": order["order_id"],
            "verdict": order["verdict"],
            "due": order["due"],
            "codes": order["flags"],
        },
    ),
    "travel": (
        "bookings",
        lambda booking: {
            "booking_id": booking["booking_id"],
            "verdict": booking["unmatched_reason"] or "matched",
            "note": booking["explanation"],
            "vendor_lines": booking["vendor_lines"],
            **{name: booking[name] for name in _TRAVEL_FIGURES},
        },
    ),
}

_RUNNER_ORDERS: dict[str, Any] = {
    "eshop": lambda entity: {
        "order_no": entity["key"],
        "lines": entity["fields"]["n_lines"],
        "statuses": entity["fields"]["statuses"],
        "treatment": entity["fields"]["treatment"],
        "included": entity["fields"]["included"],
        "matched": entity["fields"]["matched"],
        **{name: entity["fields"][name] for name in _ESHOP_FIGURES},
    },
    "food": lambda entity: {
        "order_id": entity["key"],
        "verdict": entity["verdict"],
        "due": entity["fields"]["due"],
        "codes": entity["codes"],
    },
    "travel": lambda entity: {
        "booking_id": entity["key"],
        "verdict": entity["verdict"],
        "note": entity["fields"]["note"],
        "vendor_lines": entity["fields"]["n_lines"],
        **{name: entity["fields"][name] for name in _TRAVEL_FIGURES},
    },
}


def _produce(
    scenario: Scenario, out_dir: Path, config: CategoryConfig
) -> tuple[dict[str, Any], Path]:
    uploads = {
        role: Upload(role=role, filename=path.name, path=path)
        for role, path in scenario.files.items()
    }
    # Every upload reaches the report, as a run gives them.
    sources = dict(scenario.files)
    report = out_dir / "report.xlsx"
    max_rows = settings.MAX_ROWS_PER_SHEET

    result = run_generic(config, uploads, max_rows=max_rows)
    generic_report(result, report, sources)
    return comparable(scenario, json.loads(result.model_dump_json())), report
