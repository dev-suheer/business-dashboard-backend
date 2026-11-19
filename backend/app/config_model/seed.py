"""Version 1 of each category's configuration: the rules the client approved.

Each category's uploads and rules are written out as formulas in
``seed_eshop.py``, ``seed_travel.py`` and ``seed_food.py``, each the v1 pipeline
it replaced, rule for rule; its report in ``seed_output.py``, translated from
the v1 exporters. The rates and tolerances come from the settings the code
used, the wallet history and finance report sheets from the shared readers.
The parity tests prove all of it identical to what the client signed off. Each
rule, stop, list and exception also carries its plain words (``seed_words.py``),
which no run reads. Run ``uv run python -m scripts.build_seed`` to write it to
``app/seed/v1/``; the migrations load those files.
"""

import inspect
import re
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from app.config_model import seed_eshop, seed_food, seed_travel, seed_words
from app.config_model.models import (
    CategoryConfig,
    ExceptionDef,
    InputColumn,
    InputFile,
    InputSheet,
    Model,
    Parameter,
)
from app.config_model.seed_output import OUTPUTS
from app.core.config import settings
from app.recon import exceptions, wallet
from app.recon.excel import ColumnSpec, SheetSchema

SEED_DIR = Path(__file__).resolve().parents[1] / "seed" / "v1"
CATEGORIES = ("eshop", "travel", "food")


def _sheet(key: str, schema: SheetSchema) -> InputSheet:
    return InputSheet(
        key=key,
        name=schema.name,
        columns=[
            InputColumn(field=c.field, headers=list(c.aliases), required=c.required)
            for c in schema.columns
        ],
        fingerprint=list(schema.fingerprint),
        exclude=list(schema.exclude),
    )


# The finance report's caption for a category that finds its cashback by SG Order ID.
FINANCE_CAPTION = "Optional · cashback by SG Order ID"

# The approved setups' finance report: the spin (cashback) report, one export
# per category, a reference file the Orders sheet's look-up columns find each
# order's cashback in. A finished recon workbook also carries the reward columns, bolted onto
# its Orders sheet; Spin Event ID is what the source table has and that sheet
# does not.
FINANCE_SCHEMA = SheetSchema(
    name="finance report",
    fingerprint=(
        "Spin Event ID",
        "Order ID",
        "Reward Amount (SAR)",
        "Reward Code",
        "Reward Type",
        "Outcome",
        "Vertical Name",
    ),
    exclude=("MATCH ID'S", "Match ID's", "Partner Order ID"),
    columns=(
        ColumnSpec("order_id", ("Order ID",), required=True),
        ColumnSpec("reward_amount", ("Reward Amount (SAR)",), required=True),
        ColumnSpec("vertical_name", ("Vertical Name",), required=True),
        ColumnSpec("reward_code", ("Reward Code",), required=True),
        ColumnSpec("reward_type", ("Reward Type",), required=True),
        ColumnSpec("outcome", ("Outcome",), required=True),
    ),
)


def wallet_upload() -> InputFile:
    """The wallet history upload, as the wallet check reads it."""
    return InputFile(
        role="wallet",
        title="Wallet history",
        caption="Optional · proves the wallet share",
        required=False,
        sheets=[_sheet("history", wallet.WALLET_SCHEMA)],
        kind="wallet",
    )


def finance_upload(caption: str = FINANCE_CAPTION) -> InputFile:
    """The approved setups' finance report upload: a reference file, which the
    Orders sheet's look-up columns find each order's cashback in."""
    return InputFile(
        role="finance",
        title="Finance report",
        caption=caption,
        required=False,
        sheets=[_sheet("report", FINANCE_SCHEMA)],
        kind="reference",
    )


def _decimal(
    key: str, label: str, kind: str, value: object, *, overridable: bool = False
) -> Parameter:
    return Parameter.model_validate(
        {
            "key": key,
            "label": label,
            "kind": kind,
            "value": str(value),
            "overridable": overridable,
        }
    )


_CODE = re.compile(r"ExceptionCode\.([A-Z_]+)")


def _exceptions(
    modules: Iterable[ModuleType],
    model: Model | None = None,
    inputs: Iterable[InputFile] = (),
) -> list[ExceptionDef]:
    """Every exception the category can raise, with its severity, in the order
    the taxonomy lists them: the ones its rules raise, and the ones found by
    reading the source of the library modules it calls."""
    raised = {
        code for module in modules for code in _CODE.findall(inspect.getsource(module))
    }
    if model is not None:
        raised |= {
            rule.code
            for rule in [
                *model.row_rules,
                *model.checks,
                *model.findings,
                *model.run_rules,
            ]
        }
        raised |= {r.exception.code for r in model.rules if r.exception is not None}
    raised |= {
        sheet.unique.exception.code
        for file in inputs
        for sheet in file.sheets
        if sheet.unique is not None and sheet.unique.exception is not None
    }
    return [
        ExceptionDef.model_validate({"code": str(code), "severity": str(severity)})
        for code, severity in exceptions.SEVERITIES.items()
        if str(code) in raised
    ]


def _eshop() -> CategoryConfig:
    inputs = [
        seed_eshop.PORTAL,
        seed_eshop.VENDOR,
        seed_eshop.PROMO,
        seed_eshop.SETTLEMENTS,
        wallet_upload(),
        finance_upload("Optional · cashback by order number"),
    ]
    return CategoryConfig(
        category="eshop",
        engine="generic",
        inputs=inputs,
        parameters=[
            _decimal(
                "standard_rate",
                "Standard commission rate",
                "rate",
                settings.STANDARD_RATE,
                overridable=True,
            ),
            _decimal(
                "promo_rate",
                "Promotional commission rate",
                "rate",
                settings.PROMO_RATE,
                overridable=True,
            ),
            _decimal(
                "promo_cutoff",
                "Promotion ends (inclusive)",
                "date",
                settings.PROMO_CUTOFF.isoformat(),
                overridable=True,
            ),
            _decimal(
                "line_tolerance", "Line tolerance", "tolerance", settings.LINE_TOLERANCE
            ),
            _decimal(
                "order_tolerance",
                "Order tolerance",
                "tolerance",
                settings.ORDER_TOLERANCE,
            ),
        ],
        exceptions=_exceptions([wallet], seed_eshop.MODEL, inputs),
        labels={"match_state": seed_eshop.MATCH_STATE_LABELS},
        output=OUTPUTS["eshop"](),
        model=seed_eshop.MODEL,
    )


def _travel() -> CategoryConfig:
    inputs = [
        seed_travel.PORTAL,
        seed_travel.VENDOR,
        wallet_upload(),
        finance_upload(),
    ]
    return CategoryConfig(
        category="travel",
        engine="generic",
        inputs=inputs,
        parameters=[
            _decimal(
                "hotel_rate",
                "Hotel commission rate",
                "rate",
                settings.TRAVEL_HOTEL_RATE,
                overridable=True,
            ),
            _decimal(
                "flight_rate",
                "Flight commission rate",
                "rate",
                settings.TRAVEL_FLIGHT_RATE,
                overridable=True,
            ),
            _decimal(
                "vat_rate",
                "VAT added on international bookings",
                "rate",
                settings.TRAVEL_VAT_RATE,
                overridable=True,
            ),
            _decimal(
                "base_tolerance",
                "Amount tolerance",
                "tolerance",
                settings.TRAVEL_BASE_TOLERANCE,
            ),
            _decimal(
                "commission_tolerance",
                "Commission tolerance",
                "tolerance",
                settings.TRAVEL_COMMISSION_TOLERANCE,
            ),
            _decimal(
                "rate_tolerance",
                "Rate tolerance",
                "tolerance",
                settings.TRAVEL_RATE_TOLERANCE,
            ),
        ],
        exceptions=_exceptions([wallet], seed_travel.MODEL, inputs),
        labels={"unmatched_reason": seed_travel.REASON_LABELS},
        output=OUTPUTS["travel"](),
        model=seed_travel.MODEL,
    )


def _food() -> CategoryConfig:
    return CategoryConfig(
        category="food",
        engine="generic",
        inputs=[
            seed_food.PORTAL,
            seed_food.VENDOR,
            wallet_upload(),
            finance_upload(),
        ],
        parameters=seed_food.PARAMETERS,
        exceptions=_exceptions([wallet], seed_food.MODEL),
        output=OUTPUTS["food"](),
        model=seed_food.MODEL,
    )


BUILDERS = {"eshop": _eshop, "travel": _travel, "food": _food}


def build_seed(category: str) -> CategoryConfig:
    return _worded(BUILDERS[category](), seed_words.WORDS[category])


def _worded(config: CategoryConfig, words: dict[str, list[str]]) -> CategoryConfig:
    """The setup with the plain words of each rule, stop, list and exception."""
    model = config.model
    assert model is not None
    updates = {}
    for part, texts in words.items():
        items = getattr(model, part)
        if len(items) != len(texts):
            raise ValueError(
                f"{config.category} has {len(items)} {part} and words for {len(texts)}"
            )
        updates[part] = [
            item.model_copy(update={"about": text})
            for item, text in zip(items, texts, strict=True)
        ]
    exceptions = [
        e.model_copy(update={"about": seed_words.EXCEPTIONS[e.code]})
        for e in config.exceptions
    ]
    return config.model_copy(
        update={"model": model.model_copy(update=updates), "exceptions": exceptions}
    )


def seed_path(category: str) -> Path:
    return SEED_DIR / f"{category}.json"


def load_seed(category: str) -> CategoryConfig:
    return CategoryConfig.model_validate_json(seed_path(category).read_text())


def seed_json(config: CategoryConfig) -> str:
    return config.model_dump_json(indent=2) + "\n"
