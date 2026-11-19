"""The report is written from the output configuration: change it, and the workbook follows."""

from dataclasses import replace
from pathlib import Path
from typing import Any, get_args

import pytest
import xlsxwriter
from openpyxl import load_workbook

from app.config_model.models import (
    BreakdownColumn,
    CategoryConfig,
    OutputTab,
    ReportFormat,
)
from app.config_model.runtime import use_config
from app.config_model.seed import load_seed
from app.recon.sheets import report_formats
from app.render import generic_report
from app.render.report import ConfigurationError
from app.runner import RunResult
from tests.parity.scenarios import SCENARIOS
from tests.travel import run_travel

SCENARIO = next(s for s in SCENARIOS if s.name == "travel-claim-13-finance")
SOURCES = {
    r: p for r, p in SCENARIO.files.items() if r in ("portal", "vendor", "finance")
}


@pytest.fixture(scope="module")
def result() -> RunResult:
    return run_travel("13")


def _tab(config: CategoryConfig, key: str) -> OutputTab:
    return next(t for t in config.output.tabs if t.key == key)


def _with_tab(config: CategoryConfig, key: str, **changes: Any) -> CategoryConfig:
    tabs = [
        t.model_copy(update=changes) if t.key == key else t for t in config.output.tabs
    ]
    return config.model_copy(
        update={"output": config.output.model_copy(update={"tabs": tabs})}
    )


def _render(config: CategoryConfig, result: RunResult, path: Path) -> Any:
    """The same run, reported under an edited configuration."""
    with use_config(config):
        generic_report(replace(result, config=config), path, SOURCES)
    return load_workbook(path)


def test_a_column_can_be_renamed_moved_or_dropped(
    result: RunResult, tmp_path: Path
) -> None:
    seed = load_seed("travel")
    columns = list(_tab(seed, "orders").columns)
    verdict = next(c for c in columns if c.key == "verdict")
    edited = [verdict.model_copy(update={"header": "Result"})] + [
        c for c in columns if c.key not in {"verdict", "transaction_id"}
    ]
    book = _render(
        _with_tab(seed, "orders", columns=edited), result, tmp_path / "r.xlsx"
    )

    headers = [cell.value for cell in next(book["Orders"].iter_rows(max_row=1))]
    assert headers[0] == "Result"
    assert "Transaction ID" not in headers
    assert "Verdict" not in headers
    # The totals and the client summary still find their columns by key.
    assert book["Orders"]["A2"].value in {
        "Base differs",
        "Matched",
        "Base & commission differ",
        "Commission differs",
    }


def test_renamed_tabs_keep_their_references(result: RunResult, tmp_path: Path) -> None:
    seed = load_seed("travel")
    config = _with_tab(seed, "orders", title="Bookings")
    config = _with_tab(config, "b2c_invoices", title="ERP Import")
    config = _with_tab(config, "finance_copy", title="Spins")
    book = _render(config, result, tmp_path / "r.xlsx")

    assert book.sheetnames == [
        "Dashboard",
        "Bookings",
        "Exceptions",
        "Wallet",
        "ERP Import",
        "Portal",
        "Vendor",
        "Spins",
    ]
    # Invoice unit prices read the renamed Orders sheet...
    unit_price = book["ERP Import"]["G2"].value
    assert unit_price.startswith("=Bookings!")
    # ...and the cashback lookups read the renamed finance copy.
    orders = book["Bookings"]
    # openpyxl reads them back as array formulas; their text is the formula.
    formulas = [
        getattr(cell.value, "text", cell.value)
        for row in orders.iter_rows(min_row=2, max_row=2)
        for cell in row
    ]
    lookups = [f for f in formulas if isinstance(f, str) and "XLOOKUP" in f]
    assert lookups and all("'Spins'!" in formula for formula in lookups)


def test_a_tab_can_be_left_out(result: RunResult, tmp_path: Path) -> None:
    seed = load_seed("travel")
    tabs = [t for t in seed.output.tabs if t.key != "b2c_invoices"]
    config = seed.model_copy(
        update={"output": seed.output.model_copy(update={"tabs": tabs})}
    )
    assert (
        "B2C Invoices ERP"
        not in _render(config, result, tmp_path / "r.xlsx").sheetnames
    )


def test_a_figure_the_run_does_not_have_is_named_in_the_error(
    result: RunResult, tmp_path: Path
) -> None:
    seed = load_seed("travel")
    dashboard = _tab(seed, "dashboard")
    block = dashboard.blocks[0]
    broken = block.model_copy(
        update={
            "values": [
                block.values[0].model_copy(update={"metric": "no_such_figure"}),
                *block.values[1:],
            ]
        }
    )
    config = _with_tab(seed, "dashboard", blocks=[broken, *dashboard.blocks[1:]])
    with pytest.raises(ConfigurationError, match="no_such_figure"):
        _render(config, result, tmp_path / "r.xlsx")


def _dashboard_rows(book: Any) -> list[tuple[Any, ...]]:
    return list(book["Dashboard"].iter_rows(values_only=True))


def test_the_breakdown_is_designed_column_by_column(
    result: RunResult, tmp_path: Path
) -> None:
    seed = load_seed("travel")
    spec = _tab(seed, "dashboard").breakdown
    assert spec is not None
    designed = spec.model_copy(
        update={
            "name_header": "By provider",
            "columns": [
                BreakdownColumn(header="Bookings", add="count", kind="int"),
                BreakdownColumn(header="Commission", value="line_commission"),
                BreakdownColumn(
                    header="With a promo code",
                    add="count",
                    when="NOT(ISBLANK(portal.promocode))",
                    kind="int",
                ),
            ],
            "rank_by": 1,
            "total": "sum",
        }
    )
    book = _render(
        _with_tab(seed, "dashboard", breakdown=designed), result, tmp_path / "r.xlsx"
    )

    rows = _dashboard_rows(book)
    at = next(i for i, row in enumerate(rows) if row[0] == "By provider")
    assert rows[at][:5] == (
        "By provider",
        "Bookings",
        "Commission",
        "With a promo code",
        None,
    )
    total, *named = [r for r in rows[at + 1 :] if r[0] is not None]
    assert total[0] == "TOTAL"
    # Ranked by commission, most first; the TOTAL row adds the rows up.
    commissions = [row[2] for row in named]
    assert commissions == sorted(commissions, reverse=True)
    assert total[1] == sum(row[1] for row in named)
    assert round(total[2], 2) == round(sum(commissions), 2)
    assert all(row[3] <= row[1] for row in named)
    # The table has its own filter, across its columns.
    assert book["Dashboard"].auto_filter.ref == f"A{at + 1}:D{at + 2 + len(named)}"


def test_a_dashboard_without_a_breakdown_ends_with_its_blocks(
    result: RunResult, tmp_path: Path
) -> None:
    seed = load_seed("travel")
    book = _render(
        _with_tab(seed, "dashboard", breakdown=None), result, tmp_path / "r.xlsx"
    )
    rows = [row for row in _dashboard_rows(book) if any(v is not None for v in row)]
    cells = [v for row in rows for v in row if v is not None]
    assert "Breakdown By Most Used" not in cells
    # The last rows are the last block: its headers, then its figures.
    last = _tab(seed, "dashboard").blocks[-1]
    assert rows[-2][2 : 2 + len(last.headers)] == tuple(last.headers)
    assert all(isinstance(v, int | float) for v in rows[-1][2 : 2 + len(last.values)])
    assert not book["Dashboard"].auto_filter.ref


def test_a_header_takes_the_colour_given_it(result: RunResult, tmp_path: Path) -> None:
    seed = load_seed("travel")
    columns = list(_tab(seed, "orders").columns)
    coloured = {0: "#FFC000", 1: "#ffc000", 2: "#002060"}
    edited = [
        c.model_copy(update={"color": coloured[i]}) if i in coloured else c
        for i, c in enumerate(columns)
    ]
    book = _render(
        _with_tab(seed, "orders", columns=edited), result, tmp_path / "r.xlsx"
    )
    header = next(book["Orders"].iter_rows(max_row=1))
    fills = [cell.fill.fgColor.rgb for cell in header]
    assert fills[:3] == ["FFFFC000", "FFFFC000", "FF002060"]
    # White text on the dark one, the default on the light ones.
    assert header[2].font.color.rgb == "FFFFFFFF"
    assert header[0].font.color is None or header[0].font.color.rgb != "FFFFFFFF"
    assert header[0].font.bold and header[2].border.left.style == "thin"
    # The others keep their band's colour.
    untouched = _render(seed, result, tmp_path / "seed.xlsx")
    seed_fills = [
        cell.fill.fgColor.rgb for cell in next(untouched["Orders"].iter_rows(max_row=1))
    ]
    assert fills[3:] == seed_fills[3:]


def test_without_the_finance_tab_there_are_no_lookups(
    result: RunResult, tmp_path: Path
) -> None:
    seed = load_seed("travel")
    tabs = [t for t in seed.output.tabs if t.key != "finance_copy"]
    config = seed.model_copy(
        update={"output": seed.output.model_copy(update={"tabs": tabs})}
    )
    book = _render(config, result, tmp_path / "r.xlsx")
    assert "Finance Report" not in book.sheetnames
    orders = book["Orders"]
    headers = [cell.value for cell in next(orders.iter_rows(max_row=1))]
    assert len(headers) == len(_tab(seed, "orders").columns)
    formulas = [
        getattr(cell.value, "text", cell.value)
        for row in orders.iter_rows(min_row=2, max_row=3)
        for cell in row
    ]
    assert not [f for f in formulas if isinstance(f, str) and "XLOOKUP" in f]


def test_a_table_moved_after_the_invoices_is_still_read(
    result: RunResult, tmp_path: Path
) -> None:
    """The invoices read the table that lists their set, wherever it is."""
    seed = load_seed("travel")
    orders = _tab(seed, "orders").model_copy(update={"title": "My Orders"})
    tabs = [t for t in seed.output.tabs if t.key != "orders"]
    at = next(i for i, t in enumerate(tabs) if t.key == "b2c_invoices")
    tabs.insert(at, orders)
    config = seed.model_copy(
        update={"output": seed.output.model_copy(update={"tabs": tabs})}
    )
    book = _render(config, result, tmp_path / "r.xlsx")
    assert book["B2C Invoices ERP"]["G2"].value.startswith("='My Orders'!")


def test_the_formats_a_setup_can_name_are_the_reports_own(tmp_path: Path) -> None:
    book = xlsxwriter.Workbook(str(tmp_path / "f.xlsx"))
    try:
        made = set(report_formats(book))
    finally:
        book.close()
    assert set(get_args(ReportFormat)) == made
