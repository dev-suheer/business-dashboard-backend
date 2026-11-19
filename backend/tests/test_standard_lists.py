"""The wallet history and the finance report as lists: their rows, as formulas,
lookups and report tabs read them, beside the wallet check and the finance
lookups, which read the files as before."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.config_model.check import problems
from app.config_model.models import CategoryConfig
from app.config_model.runtime import use_config
from app.config_model.seed import load_seed
from app.recon.inputs import Upload
from app.render import generic_report
from app.runner import RunResult, run
from app.runner.inputs import READERS
from tests.conftest import (
    TRAVEL_CLAIMS,
    TRAVEL_PORTAL,
    TRAVEL_WALLET,
    write_finance_report,
)

Edit = Callable[[dict[str, Any]], None]

# Every movement in the sample history, of every wallet type; the wallet check
# reads the travel ones alone.
WALLET_ROWS = 101
TRAVEL_MOVEMENTS = 46


def _travel(*edits: Edit) -> CategoryConfig:
    doc = load_seed("travel").model_dump(mode="json")
    for edit in edits:
        edit(doc)
    return CategoryConfig.model_validate(doc)


def _sheet(doc: dict[str, Any], role: str) -> dict[str, Any]:
    upload = next(f for f in doc["inputs"] if f["role"] == role)
    sheet: dict[str, Any] = upload["sheets"][0]
    return sheet


def _figure(key: str, formula: str) -> Edit:
    return lambda doc: doc["model"]["metrics"].append({"key": key, "formula": formula})


def _run(
    config: CategoryConfig, *, wallet: bool = True, finance: Path | None = None
) -> RunResult:
    paths = {"portal": TRAVEL_PORTAL, "vendor": TRAVEL_CLAIMS["13"]}
    if wallet:
        paths["wallet"] = TRAVEL_WALLET
    if finance is not None:
        paths["finance"] = finance
    uploads = {role: Upload(role, path.name, path) for role, path in paths.items()}
    with use_config(config):
        return run(config, uploads, max_rows=1_000_000)


def _history_column(header: str) -> list[Any]:
    """One column of the sample history, as the file holds it."""
    book = load_workbook(TRAVEL_WALLET, read_only=True)
    try:
        rows = list(book.worksheets[0].iter_rows(values_only=True))
    finally:
        book.close()
    at = list(rows[0]).index(header)
    return [row[at] for row in rows[1:]]


def test_the_wallet_history_is_a_list_of_every_row() -> None:
    config = _travel(_figure("wallet_rows", 'COUNTX("wallet")'))
    assert problems(config) == []

    result = _run(config)

    assert result.metrics["wallet_rows"] == WALLET_ROWS
    assert len(result.rows["wallet"]) == WALLET_ROWS
    # The wallet check, its exceptions and the sheets a run lists are as before.
    plain = _run(_travel())
    assert result.wallet == plain.wallet
    assert result.exceptions == plain.exceptions
    assert result.detected == plain.detected
    assert [m.rows for m in result.detected if m.role == "wallet"] == [TRAVEL_MOVEMENTS]


def test_a_column_and_a_field_added_to_the_wallet_history_are_read() -> None:
    def edit(doc: dict[str, Any]) -> None:
        sheet = _sheet(doc, "wallet")
        # How the list reads the amount; the wallet check reads it as it did.
        next(c for c in sheet["columns"] if c["field"] == "amount")["type"] = "money"
        sheet["columns"].append(
            {
                "field": "mobile",
                "headers": ["Mobile Number"],
                "required": False,
                "type": "text",
                "skip_if_blank": False,
            }
        )
        sheet["fields"].append({"key": "twice", "formula": "amount * 2"})

    config = _travel(
        edit,
        _figure("doubled", 'SUMX("wallet", twice)'),
        _figure("with_mobile", 'COUNTX("wallet", NOT(ISBLANK(mobile)))'),
    )
    assert problems(config) == []

    result = _run(config)

    amounts = [READERS["money"](v) for v in _history_column("Amount")]
    assert result.metrics["doubled"] == sum(amounts, Decimal(0)) * 2
    mobiles = [v for v in _history_column("Mobile Number") if v not in (None, "")]
    assert result.metrics["with_mobile"] == len(mobiles)
    assert result.wallet == _run(_travel()).wallet


def test_a_lookup_reads_the_finance_report(tmp_path: Path) -> None:
    finance = write_finance_report(tmp_path / "finance.xlsx", [121, 122], "hotel")

    def lookup(doc: dict[str, Any]) -> None:
        doc["model"]["lookups"].append(
            {
                "key": "reward",
                "set": "finance",
                "by": "TEXT(order_id)",
                "value": "reward_amount",
            }
        )

    config = _travel(
        lookup,
        _figure("finance_rows", 'COUNTX("finance")'),
        _figure("reward_121", 'LOOKUP("reward", "121")'),
    )
    assert problems(config) == []

    result = _run(config, finance=finance)

    assert result.metrics["finance_rows"] == 2
    assert result.metrics["reward_121"] == "4.13"
    assert result.detected == _run(_travel(), finance=finance).detected


def test_a_list_whose_file_was_not_uploaded_is_empty() -> None:
    result = _run(_travel(_figure("wallet_rows", 'COUNTX("wallet")')), wallet=False)

    assert result.metrics["wallet_rows"] == 0
    assert result.rows["wallet"] == []
    assert result.rows["finance"] == []


def test_a_report_tab_lists_the_wallet_rows(tmp_path: Path) -> None:
    def tab(doc: dict[str, Any]) -> None:
        doc["output"]["tabs"].append(
            {
                "key": "wallet_rows",
                "title": "Wallet rows",
                "kind": "table",
                "columns": [
                    {
                        "key": "sg_order_id",
                        "header": "SG Order ID",
                        "width": 14,
                        "group": "vendor",
                        "kind": "text",
                        "value": "sg_order_id",
                        "color": None,
                    },
                    # Never blank, so every row written shows.
                    {
                        "key": "place",
                        "header": "Row",
                        "width": 8,
                        "group": "vendor",
                        "kind": "text",
                        "value": "row",
                        "color": None,
                    },
                ],
                "sections": [
                    {
                        "kind": "rows",
                        "set": "wallet",
                        "lookups": False,
                        "tolerances": False,
                    }
                ],
            }
        )

    config = _travel(tab)
    assert problems(config) == []

    result = _run(config)
    with use_config(config):
        report = generic_report(result, tmp_path / "travel.xlsx", {})

    sheet = load_workbook(report)["Wallet rows"]
    assert [sheet["A1"].value, sheet["B1"].value] == ["SG Order ID", "Row"]
    assert [sheet.cell(r, 2).value for r in range(2, sheet.max_row + 1)] == list(
        range(1, WALLET_ROWS + 1)
    )
