"""Report tabs that are tables of the setup.

The Exceptions sheet was written by its own writer; it is now the standard
Exceptions table. Every test here writes the same data both ways, the old
writer (``tests/parity/legacy.py``) and the table, and requires the two
workbooks to be the same cell for cell, on every branch the old writer had.
"""

import importlib.util
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import openpyxl
import pytest
import xlsxwriter
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.config_model.check import problems
from app.config_model.models import CategoryConfig, Contacts, OutputTab
from app.config_model.rename import rename
from app.config_model.runtime import schema_of, use_config
from app.config_model.seed import load_seed
from app.config_model.standard_tabs import erp_invoices_tab, exceptions_tab, wallet_tab
from app.core.config import settings
from app.expr import Values, evaluate, text
from app.recon.cleaning import normalize_header
from app.recon.excel import SheetFormatError, SheetSchema
from app.recon.exceptions import ExceptionCode, ReconException, Severity
from app.recon.inputs import Upload
from app.recon.sheets import CopyEdits, letter, report_formats, write_cell, write_copy
from app.recon.wallet import (
    WalletCheck,
    WalletEvent,
    WalletReconciliation,
    WalletState,
)
from app.render import generic_report
from app.render.data import ReportData, TableRows
from app.render.generic import _rows, _styles, report_data
from app.render.lookups import copy_layout, write_lookups
from app.render.report import ConfigurationError, _table, render
from app.runner import RunResult
from app.runner import run as run_generic
from app.runner.result import exception_scope
from tests.conftest import (
    FOOD_PORTAL,
    FOOD_VENDOR,
    TRAVEL_CLAIMS,
    TRAVEL_PORTAL,
    TRAVEL_WALLET,
    VENDOR_STATEMENT,
    WORKINGS,
)
from tests.food import portal_schema as food_portal_schema
from tests.parity import legacy
from tests.parity.compare import _cell_state, sheet_differences, workbook_differences
from tests.parity.legacy import exceptions_sheet, finance_layout, finance_schema
from tests.parity.scenarios import BASELINE, FINANCE_INPUTS, SCENARIOS
from tests.test_sheets import PORTAL_HEADERS

API = settings.API_V1_STR
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
# The migrations that made each sheet a table, in the order they run.
# Each migration of the report's tabs, with its way up and its way down.
MIGRATIONS = (
    ("exceptions_table", "as_table", "as_sheet"),
    ("erp_invoices_table", "as_table", "as_sheet"),
    ("wallet_table", "as_table", "as_sheet"),
    ("copy_tables", "as_table", "as_sheet"),
    ("lookup_columns", "as_lookups", "as_finance"),
)
NAMES = [name for name, _up, _down in MIGRATIONS]

# Orders the exceptions name, with a status of every kind a formula can give.
ORDERS: dict[str, dict[str, Any]] = {
    "A1": {"status": None},
    "A2": {"status": ""},
    "A3": {"status": Decimal("12.50")},
    "A4": {"status": date(2026, 7, 26)},
    "A5": {"status": "Cancelled"},
    "A6": {"status": True},
}

EXCEPTIONS = [
    ReconException(
        code=ExceptionCode.BOOKING_MISSING_IN_VENDOR,
        severity=Severity.HIGH,
        order_no="A1",
        amount=Decimal("1234.567"),
        detail="not billed on the claim",
    ),
    ReconException(
        code=ExceptionCode.PRIOR_PERIOD,
        severity=Severity.INFO,
        order_no="A2",
        amount=None,
        detail="=SUM(A1:A9)",
    ),
    ReconException(
        code="A_CODE_A_SETUP_ADDED",
        severity=Severity.MEDIUM,
        order_no="A3",
        amount=Decimal(0),
        detail="https://example.com/a?b=c",
    ),
    ReconException(
        code=ExceptionCode.PARTIAL_REFUND,
        severity=Severity.MEDIUM,
        order_no="A4",
        amount=Decimal("-5.25"),
        detail="",
    ),
    ReconException(
        code=ExceptionCode.VENDOR_COMMISSION_OVERCHARGE,
        severity=Severity.HIGH,
        order_no="A5",
        amount=Decimal("66.82"),
        detail="billed 292.32 where 225.49 is contractual",
    ),
    ReconException(
        code=ExceptionCode.MULTI_LEG_BOOKING,
        severity=Severity.INFO,
        order_no="A6",
        amount=Decimal("100"),
        detail="2 invoice lines compared as one booking",
    ),
    ReconException(
        code=ExceptionCode.ADJUSTMENT_UNLINKED,
        severity=Severity.MEDIUM,
        order_no=None,
        amount=Decimal("1.00"),
        detail="no booking id in 'Rounding'",
    ),
    ReconException(
        code=ExceptionCode.UNPARSEABLE_VENDOR_ROW,
        severity=Severity.HIGH,
        order_no="",
        amount=None,
        detail="'claim' row 1: invoiced row carries no booking id",
    ),
]

STATUSES = [
    "IF(ISBLANK(order), BLANK(), order.status)",
    '""',
    'IF(ISBLANK(order), "no order", order.status & " (" & code & ")")',
]


def _book(path: Path) -> Any:
    # As the report is written (app/render/report.py).
    return xlsxwriter.Workbook(
        str(path),
        {
            "constant_memory": True,
            "in_memory": False,
            "default_date_format": "yyyy-mm-dd",
        },
    )


def _scopes(exceptions: list[ReconException]) -> list[Values]:
    root = Values({})
    orders = {key: Values(fields, parent=root) for key, fields in ORDERS.items()}
    return [exception_scope(e, orders, root) for e in exceptions]


def _as_table(path: Path, tab: OutputTab, exceptions: list[ReconException]) -> Path:
    scopes = _scopes(exceptions)
    section = tab.sections[0]
    assert section.set is not None
    rows = TableRows(
        values=_rows(tab, section, scopes),
        styles=_styles(tab, section, scopes),
        members=list(range(len(scopes))),
        links=[],
    )
    data = ReportData(
        metrics={},
        texts={},
        tables={tab.key: {0: rows}},
        listed={section.set} if scopes else set(),
        tolerances={},
        breakdowns={},
        wallet=None,
        detected=[],
        copies={},
        run_value=lambda _formula: None,
    )
    book = _book(path)
    try:
        _table(book, report_formats(book), tab, data, {}, {})
    finally:
        book.close()
    return path


def _as_sheet(path: Path, status: str, exceptions: list[ReconException]) -> Path:
    scopes = dict(zip((id(e) for e in exceptions), _scopes(exceptions), strict=True))
    book = _book(path)
    exceptions_sheet(
        book,
        report_formats(book),
        exceptions,
        # What the report did with the workbook's exception_status.
        lambda e: text(evaluate(status, scopes[id(e)])),
    )
    book.close()
    return path


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("count", [len(EXCEPTIONS), 0])
def test_the_exceptions_table_writes_what_the_sheet_did(
    tmp_path: Path, status: str, count: int
) -> None:
    exceptions = EXCEPTIONS[:count]
    tab = exceptions_tab("exceptions", "Exceptions", status)
    new = _as_table(tmp_path / "table.xlsx", tab, exceptions)
    old = _as_sheet(tmp_path / "sheet.xlsx", status, exceptions)
    assert workbook_differences(new, old) == []


def test_a_style_that_names_no_format_stops_the_report(tmp_path: Path) -> None:
    tab = exceptions_tab("exceptions", "Exceptions", '""')
    loud = tab.columns[0].model_copy(update={"style": '"lo" & "ud"'})
    tab = tab.model_copy(update={"columns": [loud, *tab.columns[1:]]})
    with pytest.raises(ConfigurationError, match="names no format 'loud'"):
        _as_table(tmp_path / "table.xlsx", tab, EXCEPTIONS[:1])


def _travel_with(edit: Any) -> list[tuple[str, str]]:
    data = load_seed("travel").model_dump(mode="json")
    edit(data)
    return [(p.path, p.message) for p in problems(CategoryConfig.model_validate(data))]


def _exceptions_at() -> int:
    return next(
        i
        for i, t in enumerate(load_seed("travel").output.tabs)
        if t.key == "exceptions"
    )


def test_the_check_refuses_a_style_naming_no_format() -> None:
    t = _exceptions_at()

    def edit(data: dict[str, Any]) -> None:
        column = data["output"]["tabs"][t]["columns"][0]
        column["style"] = 'IF(severity = "high", "loud", "info")'

    assert _travel_with(edit) == [
        (f"output.tabs[{t}].columns[0].style", "No format is called 'loud'")
    ]


def test_the_check_reads_only_what_a_style_gives() -> None:
    """A text compared in a condition is not a format, only an outcome is."""
    t = _exceptions_at()

    def edit(data: dict[str, Any]) -> None:
        column = data["output"]["tabs"][t]["columns"][0]
        column["style"] = 'IFS(code = "ANY_CODE", "high", TRUE, "info")'

    assert _travel_with(edit) == []


def test_a_style_is_checked_on_the_rows_it_is_worked_out_on() -> None:
    t = _exceptions_at()

    def edit(data: dict[str, Any]) -> None:
        column = data["output"]["tabs"][t]["columns"][0]
        column["style"] = 'IF(nothing_here = "x", "high", "info")'

    found = _travel_with(edit)
    assert len(found) == 1
    assert found[0][0] == f"output.tabs[{t}].columns[0].style"
    assert "nothing_here" in found[0][1]


def test_a_rename_reaches_a_style() -> None:
    travel = load_seed("travel")
    data = travel.model_dump(mode="json")
    orders = next(
        i for i, t in enumerate(data["output"]["tabs"]) if t["key"] == "orders"
    )
    data["output"]["tabs"][orders]["columns"][0]["style"] = (
        'IF(match_state = "matched", "money", "money_bad")'
    )
    config = CategoryConfig.model_validate(data)
    assert config.model is not None
    k = next(i for i, f in enumerate(config.model.fields) if f.key == "match_state")
    renamed = rename(config, f"model.fields[{k}]", "matching").config
    style = renamed.output.tabs[orders].columns[0].style
    assert style == 'IF(matching = "matched", "money", "money_bad")'
    assert problems(renamed) == []


UNREADABLE = (
    "Write each format's name in quotes, or BLANK() to keep the column's own "
    "format, so the check can see what this style gives"
)


@pytest.mark.parametrize(
    "style",
    [
        '"lo" & "ud"',
        "TEXT(severity)",
        "severity",
        'IF(severity = "high", "high", code)',
        'COALESCE(code, "info")',
        "1",
    ],
)
def test_the_check_refuses_a_style_it_cannot_read(style: str) -> None:
    """What such a style gives is only known on the run, where a name that is
    no format would stop the report."""
    t = _exceptions_at()

    def edit(data: dict[str, Any]) -> None:
        data["output"]["tabs"][t]["columns"][0]["style"] = style

    assert _travel_with(edit) == [(f"output.tabs[{t}].columns[0].style", UNREADABLE)]


@pytest.mark.parametrize(
    "style",
    [
        '"money"',
        'IF(severity = "high", "high", BLANK())',
        'IF(severity = "high", "high", "")',
        'IFS(severity = "high", "high", severity = "info", BLANK())',
        'COALESCE(BLANK(), "info")',
        'IF(TRUE, IF(code = "X", "high", "info"), BLANK())',
        "BLANK()",
    ],
)
def test_the_check_takes_names_in_quotes_and_blank(style: str) -> None:
    t = _exceptions_at()

    def edit(data: dict[str, Any]) -> None:
        data["output"]["tabs"][t]["columns"][0]["style"] = style

    assert _travel_with(edit) == []


def test_the_check_refuses_a_parts_own_style_it_cannot_read() -> None:
    w = next(
        i for i, t in enumerate(load_seed("travel").output.tabs) if t.key == "wallet"
    )

    def edit(data: dict[str, Any]) -> None:
        data["output"]["tabs"][w]["sections"][2]["styles"]["portal_wallet"] = (
            "TEXT(vertical)"
        )

    assert _travel_with(edit) == [
        (f"output.tabs[{w}].sections[2].styles.portal_wallet", UNREADABLE)
    ]


def test_a_style_giving_blank_keeps_the_columns_own_format(tmp_path: Path) -> None:
    """On a row where the style gives blank, the cell is written as if the
    column had no style; elsewhere in the format the style names."""
    base = exceptions_tab("exceptions", "Exceptions", '""')

    def styled(style: str | None) -> OutputTab:
        first = base.columns[0].model_copy(update={"style": style})
        return base.model_copy(update={"columns": [first, *base.columns[1:]]})

    partly = load_workbook(
        _as_table(
            tmp_path / "partly.xlsx",
            styled('IF(severity = "high", "high", BLANK())'),
            EXCEPTIONS,
        )
    ).active
    plain = load_workbook(
        _as_table(tmp_path / "plain.xlsx", styled(None), EXCEPTIONS)
    ).active
    high = load_workbook(
        _as_table(tmp_path / "high.xlsx", styled('"high"'), EXCEPTIONS)
    ).active
    for row in range(2, len(EXCEPTIONS) + 2):
        expected = high if partly.cell(row, 1).value == "high" else plain
        assert _cell_state(partly.cell(row, 1)) == _cell_state(expected.cell(row, 1))
    assert {partly.cell(r, 1).value for r in range(2, len(EXCEPTIONS) + 2)} >= {
        "high",
        "info",
    }


def _migration(name: str) -> ModuleType:
    path = next(VERSIONS.glob(f"*_{name}.py"))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _up(config: dict[str, Any]) -> dict[str, Any]:
    for name, up, _down in MIGRATIONS:
        config = getattr(_migration(name), up)(config)
    return config


def _down(config: dict[str, Any]) -> dict[str, Any]:
    for name, _up, down in reversed(MIGRATIONS):
        config = getattr(_migration(name), down)(config)
    return config


def _after(name: str, tab: dict[str, Any]) -> dict[str, Any]:
    """A tab a migration stored, as the migrations after it leave it."""
    config: dict[str, Any] = {"output": {"tabs": [tab]}}
    for later, up, _down in MIGRATIONS[NAMES.index(name) + 1 :]:
        config = getattr(_migration(later), up)(config)
    tabs: list[dict[str, Any]] = config["output"]["tabs"]
    return tabs[0]


def test_the_migrations_store_todays_exceptions_table() -> None:
    status = "order.portal_status"
    stored = _migration("exceptions_table").exceptions_table(
        "exceptions", "Exceptions", None, status
    )
    assert _after("exceptions_table", stored) == exceptions_tab(
        "exceptions", "Exceptions", status
    ).model_dump(mode="json")


def test_the_migration_stores_todays_erp_invoices_table() -> None:
    settings_ = {
        "unit_cell": "orders.commission_charged",
        "partner": "TRIM(TEXT(portal.provider))",
        "order_id": "key",
        "bill_date": "portal.order_date",
        "product": '"Hotel Booking"',
        "rows": "matched",
        "where": "n_lines > 0",
        "contact": "portal.provider",
    }
    contacts = {"upload": "portal", "key": None, "match": None, "row": "portal"}
    stored = _migration("erp_invoices_table").erp_table(
        "b2c", "B2C", "portal", contacts=contacts, **settings_
    )
    today = erp_invoices_tab("b2c", "B2C", contacts=Contacts(**contacts), **settings_)
    assert _after("erp_invoices_table", stored) == {
        **today.model_dump(mode="json"),
        "requires": "portal",
    }


def test_the_migration_stores_todays_wallet_table() -> None:
    stored = _migration("wallet_table").wallet_table("wallet", "Wallet", "wallet")
    assert _after("wallet_table", stored) == wallet_tab("wallet", "Wallet").model_dump(
        mode="json"
    )


@pytest.mark.parametrize("category", ["eshop", "travel", "food"])
def test_the_migrations_go_back_and_forth(category: str) -> None:
    seed = load_seed(category).model_dump(mode="json")
    back = _down(seed)
    kinds = {t["key"]: t["kind"] for t in back["output"]["tabs"]}
    assert kinds["exceptions"] == "exceptions"
    assert kinds["b2c_invoices"] == "erp_invoices"
    assert kinds["wallet"] == "wallet"
    assert kinds["portal_copy"] == kinds["vendor_copy"] == "source_copy"
    assert kinds["finance_copy"] == "finance_copy"
    orders = next(t for t in back["output"]["tabs"] if t["key"] == "orders")
    assert [c["key"] for c in orders["finance_columns"]] == [
        "cashback_reward",
        "vertical_name",
        "reward_code",
        "reward_type",
        "outcome",
    ]
    assert "lookup_columns" not in orders
    assert {f["kind"] for f in back["inputs"]} == {"rows", "wallet", "finance"}
    assert back["output"]["exception_status"] is not None
    invoices = next(t for t in back["output"]["tabs"] if t["key"] == "b2c_invoices")
    assert invoices["invoices"]["partner"] == "TRIM(TEXT(" + (
        "merchantname))" if category == "eshop" else "portal.provider))"
    )
    assert _up(back) == seed


def test_a_tab_started_from_the_template_writes_the_approved_sheet(
    runs_client: TestClient, tmp_path: Path
) -> None:
    """The editor's way: take the standard Exceptions table, fill its Status,
    save, run. The report is the approved one, Exceptions sheet included."""
    scenario = next(s for s in SCENARIOS if s.name == "travel-claim-13-finance")
    standard = runs_client.get(f"{API}/standard")
    assert standard.status_code == 200, standard.text
    template = standard.json()["tabs"]["exceptions"]
    template["columns"][3]["value"] = "TEXT(order.portal_status)"

    opened = runs_client.get(f"{API}/categories/travel/config").json()
    config = opened["config"]
    at = next(
        i for i, t in enumerate(config["output"]["tabs"]) if t["key"] == "exceptions"
    )
    config["output"]["tabs"][at] = {
        **template,
        "key": "exceptions",
        "title": "Exceptions",
    }
    saved = runs_client.put(
        f"{API}/categories/travel/config",
        json={"config": config, "note": "test", "base_version": opened["version"]},
    )
    assert saved.status_code == 200, saved.text

    run = runs_client.post(
        f"{API}/runs",
        data={"category": "travel"},
        files={
            role: (path.name, path.read_bytes(), XLSX)
            for role, path in scenario.files.items()
        },
    ).json()
    assert run["status"] == "completed", run
    report = runs_client.get(f"{API}/runs/{run['number']}/report")
    downloaded = tmp_path / "report.xlsx"
    downloaded.write_bytes(report.content)
    assert (
        workbook_differences(downloaded, BASELINE / scenario.name / "report.xlsx") == []
    )


# --- B2C Invoices ERP ---------------------------------------------------------------

ERP = "B2C Invoices ERP"

_TRAVEL = {
    "set": "matched",
    "partner": "TRIM(TEXT(portal.provider))",
    "order_id": "key",
    "contact_source": "portal",
    "contact_row": "portal",
    "bill_date": "portal.order_date",
    "product": 'IF(portal.vertical = "hotel", "Hotel Booking", "Flight Booking")',
    "unit_column": "commission_charged",
}
_FOOD = {
    "set": "orders",
    "partner": "TRIM(TEXT(portal.provider))",
    "order_id": "key",
    "contact": "vendor.customer_name",
    "contact_source": "portal",
    "contact_key": "partnerorderid",
    "bill_date": "portal.order_date",
    "product": '"Food Delivery"',
    "unit_column": "sg_delivery",
}
_ESHOP = {
    "upload": "portal",
    "where": (
        'AND(IDTEXT(ordernumber) <> "", '
        'NOT(ISBLANK(LOOKUP("billed", UPPER(CLEAN(IDTEXT(ordernumber)))))), '
        'ISTEXT(shippingownership), TRIM(shippingownership) = "sg", '
        "ISNUMBER(shippingcost), shippingcost > 0)"
    ),
    "partner": "TRIM(TEXT(merchantname))",
    "order_id": "IDTEXT(ordernumber)",
    "bill_date": "date",
    "product": '"eShop Delivery"',
    "unit_header": "shippingcost",
}

# The approved settings, and others that reach every branch the old lines had.
ERP_CASES: list[tuple[str, dict[str, Any]]] = [
    ("travel", _TRAVEL),
    ("travel", {**_TRAVEL, "where": "n_lines > 1"}),
    (
        "travel",
        {
            **_TRAVEL,
            "contact_row": None,
            "contact_key": "partnerorderid",
            "contact": "portal.provider",
        },
    ),
    ("travel", {**_TRAVEL, "contact_source": None, "contact_row": None}),
    (
        "travel",
        {
            **_TRAVEL,
            "partner": '""',
            "order_id": '""',
            "product": '""',
            "bill_date": "BLANK()",
        },
    ),
    ("travel", {**_TRAVEL, "bill_date": '"not a date"', "unit_column": "order_total"}),
    ("travel", {**_TRAVEL, "set": "entities"}),
    ("travel", {**_TRAVEL, "where": "FALSE"}),
    ("food", _FOOD),
    ("food", {**_FOOD, "contact_source": None, "contact_key": None}),
    ("food", {**_FOOD, "order_id": 'key & "-1"', "where": "NOT(ISBLANK(vendor))"}),
    ("eshop", _ESHOP),
    ("eshop", {**_ESHOP, "where": "TRUE"}),
    ("eshop", {**_ESHOP, "unit_header": "nothere"}),
]

_FILES = {
    "travel": lambda: {
        "portal": TRAVEL_PORTAL,
        "vendor": TRAVEL_CLAIMS["13"],
        "wallet": TRAVEL_WALLET,
    },
    "food": lambda: {"portal": FOOD_PORTAL, "vendor": FOOD_VENDOR},
    "eshop": lambda: {
        "portal": WORKINGS,
        "promo": WORKINGS,
        "vendor": VENDOR_STATEMENT,
    },
}


@cache
def _sample_run(category: str) -> tuple[RunResult, tuple[tuple[str, Path], ...]]:
    config = load_seed(category)
    files = _FILES[category]()
    uploads = {role: Upload(role, path.name, path) for role, path in files.items()}
    with use_config(config):
        result = run_generic(config, uploads, max_rows=1_000_000)
    return result, tuple(files.items())


def _old_erp(
    result: RunResult, spec: legacy.Invoices, sources: dict[str, Path], path: Path
) -> Path:
    """The sheet as the old writer wrote it, from the old settings."""
    lines: list[legacy.CashbackLine] | None
    if spec.upload is not None:
        lines = legacy._upload_lines(result, spec, "Portal", sources)
    else:
        # Where the Orders sheet put each of its rows, which the unit price reads.
        orders = next(t for t in result.config.output.tabs if t.key == "orders")
        scratch = _book(path.with_name("orders.xlsx"))
        try:
            data = report_data(result, sources)
            layout = _table(scratch, report_formats(scratch), orders, data, {}, {})
        finally:
            scratch.close()
        assert spec.set is not None
        listed = layout if spec.set in layout.rows else None
        lines = legacy._set_lines(result, spec, listed, sources)
    book = _book(path)
    try:
        legacy.cashback_sheet(book, report_formats(book), lines or [], title=ERP)
    finally:
        book.close()
    return path


def _new_erp(
    result: RunResult, spec: dict[str, Any], sources: dict[str, Path], path: Path
) -> Path:
    """The whole report, its ERP tab the table the old settings migrate to."""
    tabs = result.config.output.tabs
    # The tabs as they were stored when the ERP sheet became a table: the
    # copies were their own kinds then.
    today = _migration("lookup_columns").as_finance(
        result.config.model_dump(mode="json")
    )
    stored = _migration("copy_tables").as_sheet(today)["output"]["tabs"]
    old_tab = {"key": "b2c_invoices", "title": ERP, "kind": "erp_invoices"}
    table = _migration("erp_invoices_table")._erp_from(
        {**old_tab, "invoices": spec}, stored
    )
    new_tabs = [
        OutputTab.model_validate(_after("erp_invoices_table", table))
        if t.key == "b2c_invoices"
        else t
        for t in tabs
    ]
    output = result.config.output.model_copy(update={"tabs": new_tabs})
    config = result.config.model_copy(update={"output": output})
    assert problems(config) == [] or spec.get("set") == "entities"
    changed = replace(result, config=config)
    with use_config(config):
        render(config, report_data(changed, sources), path, sources)
    return path


@pytest.mark.parametrize(("category", "spec"), ERP_CASES)
def test_the_erp_table_writes_what_the_sheet_did(
    tmp_path: Path, category: str, spec: dict[str, Any]
) -> None:
    result, files = _sample_run(category)
    sources = dict(files)
    old = _old_erp(result, legacy.Invoices(**spec), sources, tmp_path / "old.xlsx")
    new = _new_erp(result, spec, sources, tmp_path / "new.xlsx")
    assert sheet_differences(new, old, ERP) == []


@pytest.mark.parametrize(
    "value",
    [
        date(2026, 7, 26),
        datetime(2026, 7, 26),
        datetime(2026, 7, 26, 13, 45, 10),
        "2026-07-26",
        Decimal("5"),
        None,
    ],
)
def test_a_datetime_column_writes_what_the_bill_date_did(
    tmp_path: Path, value: Any
) -> None:
    def old(sheet: Any, fmt: dict[str, Any]) -> None:
        legacy._write_bill_date(sheet, fmt, 1, value)

    def new(sheet: Any, fmt: dict[str, Any]) -> None:
        write_cell(sheet, fmt, 1, 3, value, "meta", "datetime")

    assert _same_cells(tmp_path, old, new) == []


@pytest.mark.parametrize("value", ["12345", "00123", "A260726", "", "12.5", "٣"])
def test_an_id_column_writes_what_the_order_id_did(tmp_path: Path, value: str) -> None:
    def old(sheet: Any, _fmt: dict[str, Any]) -> None:
        legacy._write_order_id(sheet, 1, 1, value)

    def new(sheet: Any, fmt: dict[str, Any]) -> None:
        write_cell(sheet, fmt, 1, 1, value, "meta", "id")

    assert _same_cells(tmp_path, old, new) == []


@pytest.mark.parametrize("value", ["Hotel Booking", "", "=1+1", "123"])
def test_a_string_column_writes_text_as_it_is(tmp_path: Path, value: str) -> None:
    def old(sheet: Any, _fmt: dict[str, Any]) -> None:
        sheet.write_string(1, 4, value)

    def new(sheet: Any, fmt: dict[str, Any]) -> None:
        write_cell(sheet, fmt, 1, 4, value, "meta", "string")

    assert _same_cells(tmp_path, old, new) == []


def _same_cells(tmp_path: Path, old: Any, new: Any) -> list[str]:
    paths = []
    for name, write in (("old", old), ("new", new)):
        path = tmp_path / f"{name}.xlsx"
        book = _book(path)
        try:
            write(book.add_worksheet("S"), report_formats(book))
        finally:
            book.close()
        paths.append(path)
    return sheet_differences(paths[1], paths[0], "S")


def test_a_table_reading_the_uploads_waits_for_them(tmp_path: Path) -> None:
    """Without the uploads, as without them before, there is no ERP sheet."""
    result, _files = _sample_run("travel")
    path = render(result.config, report_data(result), tmp_path / "r.xlsx")
    book = openpyxl.load_workbook(path, read_only=True)
    assert ERP not in book.sheetnames
    assert "Orders" in book.sheetnames
    book.close()


def test_the_erp_template_writes_the_approved_sheet(
    runs_client: TestClient, tmp_path: Path
) -> None:
    """The editor's way: start from the standard ERP table, name its rows, its
    contacts and its unit price's cell, save, run: the approved report."""
    scenario = next(s for s in SCENARIOS if s.name == "travel-claim-13-finance")
    template = runs_client.get(f"{API}/standard").json()["tabs"]["erp_invoices"]
    values = {
        "partner": "TEXT(TRIM(TEXT(portal.provider)))",
        "contact": "TEXT(COALESCE(contact, BLANK()))",
        "bill_date": "portal.order_date",
        "product": 'TEXT(IF(portal.vertical = "hotel", "Hotel Booking", "Flight Booking"))',
    }
    for column in template["columns"]:
        if column["key"] in values:
            column["value"] = values[column["key"]]
        if column["key"] == "unit_price":
            column["excel"] = "={cell:orders.commission_charged}/1.15"
    template["sections"][0].update(
        set="matched",
        contacts={"upload": "portal", "key": None, "match": None, "row": "portal"},
    )
    opened = runs_client.get(f"{API}/categories/travel/config").json()
    config = opened["config"]
    at = next(
        i for i, t in enumerate(config["output"]["tabs"]) if t["key"] == "b2c_invoices"
    )
    config["output"]["tabs"][at] = {**template, "key": "b2c_invoices", "title": ERP}
    saved = runs_client.put(
        f"{API}/categories/travel/config",
        json={"config": config, "note": "test", "base_version": opened["version"]},
    )
    assert saved.status_code == 200, saved.text
    run = runs_client.post(
        f"{API}/runs",
        data={"category": "travel"},
        files={
            role: (path.name, path.read_bytes(), XLSX)
            for role, path in scenario.files.items()
        },
    ).json()
    assert run["status"] == "completed", run
    report = runs_client.get(f"{API}/runs/{run['number']}/report")
    downloaded = tmp_path / "report.xlsx"
    downloaded.write_bytes(report.content)
    assert (
        workbook_differences(downloaded, BASELINE / scenario.name / "report.xlsx") == []
    )


# --- Wallet -------------------------------------------------------------------------


def _check(state: WalletState, **fields: Any) -> WalletCheck:
    base: dict[str, Any] = {
        "vertical": "hotel",
        "sg_order_id": "1201",
        "booking_id": "H260726000001",
        "order_date": date(2026, 7, 26),
        "portal_wallet_amount": Decimal("150.00"),
        "history_paid": Decimal("150.00"),
        "history_refunded": Decimal("0"),
        "events": 1,
        "diff": Decimal("0"),
    }
    return WalletCheck(state=state, **{**base, **fields})


def _event(**fields: Any) -> WalletEvent:
    base: dict[str, Any] = {
        "source": "wallet",
        "row": 4,
        "vertical": "flight",
        "sg_order_id": "9902",
        "amount": Decimal("-75.50"),
        "message": "Refund",
        "event_date": date(2026, 7, 28),
    }
    return WalletEvent(**{**base, **fields})


WALLETS = {
    "everything": WalletReconciliation(
        window_start=date(2026, 7, 1),
        window_end=date(2026, 7, 31),
        rows_checked=5,
        matched=1,
        amount_mismatch=1,
        out_of_window=1,
        missing=1,
        unkeyed=2,
        checks=[
            _check(WalletState.MATCHED),
            _check(
                WalletState.AMOUNT_MISMATCH,
                history_paid=Decimal("140.00"),
                diff=Decimal("10.00"),
                events=2,
            ),
            _check(
                WalletState.OUT_OF_WINDOW,
                booking_id=None,
                order_date=None,
                history_paid=None,
                history_refunded=None,
                diff=None,
                events=0,
            ),
            _check(
                WalletState.MISSING,
                vertical="",
                booking_id="",
                portal_wallet_amount=Decimal("0"),
                history_paid=None,
                diff=Decimal("-150.00"),
                events=0,
            ),
        ],
        unmatched_history=[
            _event(),
            _event(message=None, event_date=None),
            _event(message="", amount=Decimal("12.345")),
            _event(message="=SUM(A1)", sg_order_id="", vertical=""),
        ],
    ),
    "no checks": WalletReconciliation(
        window_start=date(2026, 7, 1),
        rows_checked=0,
        unkeyed=3,
        unmatched_history=[_event()],
    ),
    "no movements": WalletReconciliation(
        rows_checked=1,
        matched=1,
        checks=[_check(WalletState.MATCHED)],
    ),
    "nothing": WalletReconciliation(),
}


def _wallet_as_table(path: Path, tab: OutputTab, wallet: WalletReconciliation) -> Path:
    checks = [Values(c.model_dump()) for c in wallet.checks]
    events = [Values(e.model_dump()) for e in wallet.unmatched_history]
    lists = {"wallet_checks": checks, "wallet_unmatched": events}
    root = Values({"wallet_check": wallet.model_dump()}, collections=lists.get)
    tables: dict[int, TableRows] = {}
    for s, section in enumerate(tab.sections):
        if section.kind == "rows" and section.set is not None:
            members = lists[section.set]
            tables[s] = TableRows(
                values=_rows(tab, section, members),
                styles=_styles(tab, section, members),
                members=list(range(len(members))),
                links=[],
            )
    data = ReportData(
        metrics={},
        texts={},
        tables={tab.key: tables},
        listed={name for name, members in lists.items() if members},
        tolerances={},
        breakdowns={},
        wallet=wallet,
        detected=[],
        copies={},
        run_value=lambda formula: evaluate(formula, root),
    )
    book = _book(path)
    try:
        _table(book, report_formats(book), tab, data, {}, {})
    finally:
        book.close()
    return path


def _wallet_as_sheet(path: Path, wallet: WalletReconciliation) -> Path:
    book = _book(path)
    try:
        legacy.wallet_sheet(book, report_formats(book), wallet, "Wallet")
    finally:
        book.close()
    return path


@pytest.mark.parametrize("name", list(WALLETS))
def test_the_wallet_table_writes_what_the_sheet_did(tmp_path: Path, name: str) -> None:
    wallet = WALLETS[name]
    tab = wallet_tab("wallet", "Wallet")
    new = _wallet_as_table(tmp_path / "table.xlsx", tab, wallet)
    old = _wallet_as_sheet(tmp_path / "sheet.xlsx", wallet)
    assert workbook_differences(new, old) == []


def test_a_rename_reaches_a_parts_own_formats() -> None:
    food = load_seed("food")
    t = next(i for i, tab in enumerate(food.output.tabs) if tab.key == "wallet")
    c = next(
        i
        for i, col in enumerate(food.output.tabs[t].columns)
        if col.key == "portal_wallet"
    )
    renamed = rename(food, f"output.tabs[{t}].columns[{c}]", "wallet_share")
    unmatched = renamed.config.output.tabs[t].sections[2]
    assert unmatched.styles == {"wallet_share": '"money"'}
    assert unmatched.values["wallet_share"] == "amount"
    assert problems(renamed.config) == []


def test_a_parts_own_style_giving_blank_is_as_if_unlisted(tmp_path: Path) -> None:
    """A part's own format that comes out blank leaves the cell as a column
    the part gives no format writes it."""
    wallet = WALLETS["everything"]
    assert wallet.unmatched_history
    tab = wallet_tab("wallet", "Wallet")

    def with_styles(styles: dict[str, str]) -> OutputTab:
        sections = list(tab.sections)
        sections[2] = sections[2].model_copy(update={"styles": styles})
        return tab.model_copy(update={"sections": sections})

    blank = _wallet_as_table(
        tmp_path / "blank.xlsx", with_styles({"portal_wallet": "BLANK()"}), wallet
    )
    unlisted = _wallet_as_table(tmp_path / "unlisted.xlsx", with_styles({}), wallet)
    assert workbook_differences(blank, unlisted) == []


def test_the_wallet_template_writes_the_approved_sheet(
    runs_client: TestClient, tmp_path: Path
) -> None:
    """The editor's way: start from the standard Wallet table, save, run with a
    wallet history: the approved report, Wallet sheet included."""
    scenario = next(s for s in SCENARIOS if s.name == "travel-claim-13-finance")
    template = runs_client.get(f"{API}/standard").json()["tabs"]["wallet"]
    opened = runs_client.get(f"{API}/categories/travel/config").json()
    config = opened["config"]
    at = next(i for i, t in enumerate(config["output"]["tabs"]) if t["key"] == "wallet")
    config["output"]["tabs"][at] = {**template, "key": "wallet", "title": "Wallet"}
    saved = runs_client.put(
        f"{API}/categories/travel/config",
        json={"config": config, "note": "test", "base_version": opened["version"]},
    )
    assert saved.status_code == 200, saved.text
    run = runs_client.post(
        f"{API}/runs",
        data={"category": "travel"},
        files={
            role: (path.name, path.read_bytes(), XLSX)
            for role, path in scenario.files.items()
        },
    ).json()
    assert run["status"] == "completed", run
    report = runs_client.get(f"{API}/runs/{run['number']}/report")
    downloaded = tmp_path / "report.xlsx"
    downloaded.write_bytes(report.content)
    assert (
        workbook_differences(downloaded, BASELINE / scenario.name / "report.xlsx") == []
    )


# --- Copies of the uploads ----------------------------------------------------------

COPY_TABS = [
    ("travel", "portal_copy"),
    ("travel", "vendor_copy"),
    ("food", "portal_copy"),
    ("food", "vendor_copy"),
    ("eshop", "portal_copy"),
    ("eshop", "vendor_copy"),
]


def _copy_both(
    tmp_path: Path,
    path: Path,
    sheets: list[str],
    schema: Any,
    style: str,
    edits: CopyEdits | None = None,
) -> tuple[Path, Path]:
    """The copy written by the table's writer, and by the old one."""
    new, old = tmp_path / "new.xlsx", tmp_path / "old.xlsx"
    for out, legacy_writer in ((new, False), (old, True)):
        book = _book(out)
        try:
            fmt = report_formats(book)
            if legacy_writer:
                legacy._write_source_sheet(
                    book, fmt, "Copy", path, sheets, schema, style
                )
            else:
                write_copy(book, fmt, "Copy", path, sheets, schema, style, edits)
        finally:
            book.close()
    return new, old


@pytest.mark.parametrize(("category", "key"), COPY_TABS)
def test_a_copy_writes_what_the_copy_did(
    tmp_path: Path, category: str, key: str
) -> None:
    result, files = _sample_run(category)
    config = result.config
    tab = next(t for t in config.output.tabs if t.key == key)
    assert tab.source is not None and tab.sheet is not None
    sheet = config.sheet(tab.source, tab.sheet)
    assert sheet is not None
    sheets = {d.role: d.sheets for d in result.detected}[tab.source]
    new, old = _copy_both(
        tmp_path, dict(files)[tab.source], sheets, schema_of(sheet), "portal_header"
    )
    assert workbook_differences(new, old) == []


@pytest.mark.parametrize("category", ["eshop", "travel", "food"])
def test_a_finance_copy_writes_what_the_copy_did(tmp_path: Path, category: str) -> None:
    config = load_seed(category)
    path = FINANCE_INPUTS[category][0]
    with use_config(config):
        layout = finance_layout(path)
        schema = finance_schema()
    assert layout is not None
    new, old = _copy_both(tmp_path, path, layout.sheets, schema, "cashback_header")
    assert workbook_differences(new, old) == []


# --- Look-up columns: the finance report's five, as the finance columns were ----


@pytest.mark.parametrize("category", ["eshop", "travel", "food"])
@pytest.mark.parametrize("hidden", [(), ("Spin Event ID",), ("Outcome", "Order ID")])
def test_the_look_up_columns_write_what_the_finance_columns_did(
    tmp_path: Path, category: str, hidden: tuple[str, ...]
) -> None:
    config = load_seed(category)
    path = FINANCE_INPUTS[category][0]
    orders = config.tab("orders")
    copy = config.tab("finance_copy")
    assert orders is not None and copy is not None and copy.source and copy.sheet
    sheet = config.sheet(copy.source, copy.sheet)
    assert sheet is not None
    left_out = frozenset(normalize_header(h) for h in hidden)
    with use_config(config):
        old_layout = replace(finance_layout(path, left_out), title=copy.title)
    new_layout = copy_layout(path, schema_of(sheet), copy.source, copy.title, left_out)
    keys = ["C2", '"8986"', '"121"', None, 'TEXT(C3,"0")']
    new, old = tmp_path / "new.xlsx", tmp_path / "old.xlsx"
    for out, legacy_writer in ((new, False), (old, True)):
        book = _book(out)
        try:
            fmt = report_formats(book)
            ws = book.add_worksheet("Orders")
            for row, key in enumerate(keys, start=1):
                if legacy_writer:
                    legacy.write_cashback(ws, fmt, row, 3, key, old_layout)
                else:
                    write_lookups(
                        ws,
                        fmt,
                        row,
                        3,
                        [(c, new_layout, key) for c in orders.lookup_columns],
                    )
        finally:
            book.close()
    assert workbook_differences(new, old) == []


def test_a_reference_file_without_its_sheet_is_refused_as_before(
    tmp_path: Path,
) -> None:
    config = load_seed("travel")
    upload = config.input("finance")
    assert upload is not None and upload.kind == "reference"
    schema = schema_of(upload.sheets[0])
    for path in (TRAVEL_PORTAL, _finance_without(tmp_path, "Reward Code")):
        with use_config(config), pytest.raises(SheetFormatError) as old:
            finance_layout(path)
        with pytest.raises(SheetFormatError) as new:
            copy_layout(path, schema, "finance")
        assert str(new.value) == str(old.value)


def _finance_without(tmp_path: Path, header: str) -> Path:
    """The travel finance report with one of its headers renamed away."""
    book = load_workbook(FINANCE_INPUTS["travel"][0])
    for ws in book.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value == header:
                    cell.value = "Something else"
    path = tmp_path / "finance-missing.xlsx"
    book.save(path)
    return path


def _bonus_file(path: Path, orders: list[str]) -> Path:
    """A reference file of a kind no code knows: a bonus per order."""
    book = Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Bonuses"
    ws.append(["Booking", "Bonus Amount", "Note"])
    for n, order in enumerate(orders):
        ws.append([order, 5 + n, f"bonus {n}"])
    book.save(path)
    return path


def _with_bonus(data: dict[str, Any]) -> None:
    """A bonus reference file, its copy, and a look-up column on Orders."""
    data["inputs"].append(
        {
            "role": "bonus",
            "title": "Bonus report",
            "caption": "",
            "required": False,
            "kind": "reference",
            "sheets": [
                {
                    "key": "bonuses",
                    "name": "bonus report",
                    "columns": [
                        {"field": "booking", "headers": ["Booking"], "required": True},
                        {
                            "field": "bonus_amount",
                            "headers": ["Bonus Amount"],
                            "required": True,
                            "type": "money",
                        },
                    ],
                    "fingerprint": ["Booking", "Bonus Amount"],
                }
            ],
        }
    )
    data["output"]["tabs"].append(
        {
            **_tab(data, "finance_copy"),
            "key": "bonus_copy",
            "title": "Bonus",
            "source": "bonus",
            "sheet": "bonuses",
            "requires": "bonus",
            "header_format": "header",
        }
    )
    _tab(data, "orders")["lookup_columns"].append(
        {
            "key": "bonus",
            "header": "Bonus",
            "width": 12,
            "group": "meta",
            "kind": "money",
            "lookup": {
                "sheet": "bonus_copy",
                "match": "booking",
                "value": "bonus_amount",
                "key_column": "sg_order_id",
            },
        }
    )


def test_a_new_reference_file_is_looked_up_with_no_code(tmp_path: Path) -> None:
    chosen = next(s for s in SCENARIOS if s.name == "travel-claim-13-finance")
    data = load_seed("travel").model_dump(mode="json")
    _with_bonus(data)
    config = CategoryConfig.model_validate(data)
    assert problems(config) == []
    plain = {role: Upload(role, p.name, p) for role, p in chosen.files.items()}
    with use_config(config):
        result = run_generic(config, plain, max_rows=1_000_000)
    orders = [
        str(v.get("sg_order_id"))
        for v in report_data(result, dict(chosen.files)).tables["orders"][0].values[:3]
    ]
    bonus = _bonus_file(tmp_path / "bonus.xlsx", orders)
    files = {**chosen.files, "bonus": bonus}
    uploads = {role: Upload(role, p.name, p) for role, p in files.items()}
    with use_config(config):
        result = run_generic(config, uploads, max_rows=settings.MAX_ROWS_PER_SHEET)
        path = tmp_path / "report.xlsx"
        generic_report(result, path, files)
    book = openpyxl.load_workbook(path)
    assert "Bonus" in book.sheetnames
    sheet = book["Orders"]
    headers = [c.value for c in sheet[1]]
    seed_orders = load_seed("travel").tab("orders")
    assert seed_orders is not None
    own = len(seed_orders.columns)
    assert headers[own : own + 6] == [
        *(c.header for c in seed_orders.lookup_columns),
        "Bonus",
    ]
    cell = sheet.cell(row=2, column=own + 6).value
    formula = getattr(cell, "text", cell)
    key = letter([c.key for c in seed_orders.columns].index("sg_order_id"))
    assert formula == (
        f'=IF({key}2="","NA",_xlfn.IFNA(_xlfn.XLOOKUP(IFERROR(VALUE({key}2),{key}2),'
        f"'Bonus'!A:A,'Bonus'!B:B,NA(),0),_xlfn.XLOOKUP({key}2&\"\",'Bonus'!A:A,"
        f"'Bonus'!B:B,\"NA\",0)))"
    )
    # A run's sheet list does not name a reference file.
    assert "bonus" not in {d.role for d in result.detected}

    # Without the bonus file, its copy and its column are left out; the rest is
    # the finance scenario's report.
    with use_config(config):
        result = run_generic(config, plain, max_rows=settings.MAX_ROWS_PER_SHEET)
        without = tmp_path / "without.xlsx"
        generic_report(result, without, dict(chosen.files))
    left = openpyxl.load_workbook(without)
    assert "Bonus" not in left.sheetnames
    assert "Bonus" not in [c.value for c in left["Orders"][1]]
    assert workbook_differences(without, BASELINE / chosen.name / "report.xlsx") == []


def test_a_renamed_reference_column_is_followed_by_its_look_ups() -> None:
    config = load_seed("travel")
    i = next(i for i, f in enumerate(config.inputs) if f.role == "finance")
    c = next(
        c
        for c, col in enumerate(config.inputs[i].sheets[0].columns)
        if col.field == "reward_amount"
    )
    renamed = rename(config, f"inputs[{i}].sheets[0].columns[{c}]", "reward")
    orders = renamed.config.tab("orders")
    assert orders is not None and orders.lookup_columns[0].lookup is not None
    assert orders.lookup_columns[0].lookup.value == "reward"
    assert problems(renamed.config) == []
    t = renamed.config.output.tabs.index(orders)
    assert f"output.tabs[{t}].lookup_columns[0].lookup.value" in renamed.changed
    # A column the look-ups find each row by follows its new key.
    k = next(k for k, col in enumerate(orders.columns) if col.key == "sg_order_id")
    moved = rename(renamed.config, f"output.tabs[{t}].columns[{k}]", "sg_id")
    after = moved.config.tab("orders")
    assert after is not None
    assert {c.lookup.key_column for c in after.lookup_columns if c.lookup} == {"sg_id"}
    assert problems(moved.config) == []


HEADERS = PORTAL_HEADERS
_ROWS: list[tuple[Any, ...]] = [
    (
        1,
        "A260726",
        "Hotel",
        Decimal("10.5"),
        2,
        1.25,
        9,
        "Delivered",
        10,
        "=1+1",
    ),
    (
        2,
        "",
        None,
        0,
        True,
        False,
        datetime(2026, 7, 26, 13, 45),
        datetime(2026, 7, 26),
        date(2026, 7, 1),
        time(9, 30),
    ),
    (None,) * 10,
    (3, "A" * 60, "Flight", -4, None, None, None, None, None, "x"),
]


def _workbook(path: Path, sheets: dict[str, tuple[list[tuple[Any, ...]], int]]) -> Path:
    """Sheets of title rows (their count given), the header, and rows."""
    book = Workbook()
    book.remove(book.active)
    for name, (rows, titles) in sheets.items():
        sheet = book.create_sheet(name)
        for t in range(titles):
            sheet.append((f"Title {t + 1}", None, "beside", t))
        sheet.append(HEADERS)
        for values in rows:
            sheet.append(
                tuple(float(v) if isinstance(v, Decimal) else v for v in values)
            )
    book.save(path)
    return path


HAND_MADE = {
    "titles above the header": {"Sheet": (_ROWS, 2)},
    "two stacked sheets": {"Hotel": (_ROWS[:2], 0), "Flight": (_ROWS[2:], 1)},
    "no rows": {"Sheet": ([], 0)},
}


@pytest.mark.parametrize("name", list(HAND_MADE))
def test_a_hand_made_copy_writes_what_the_copy_did(tmp_path: Path, name: str) -> None:
    sheets = HAND_MADE[name]
    path = _workbook(tmp_path / "upload.xlsx", sheets)
    new, old = _copy_both(
        tmp_path, path, list(sheets), food_portal_schema(), "vendor_header"
    )
    assert workbook_differences(new, old) == []


def test_a_sheet_with_no_header_is_copied_as_it_is(tmp_path: Path) -> None:
    book = Workbook()
    book.active.append(("nothing", "like", "a", "header"))
    book.active.append((1, 2, 3, 4))
    path = tmp_path / "upload.xlsx"
    book.save(path)
    edits = CopyEdits(hidden=frozenset({"like"}), added=(("Added", 9),))
    new, old = _copy_both(
        tmp_path, path, [book.active.title], food_portal_schema(), "header", edits
    )
    # No header, so nothing to leave out and nowhere to add.
    assert workbook_differences(new, old) == []


# Finds the header by two columns only, so the upload without Partner Order ID
# still has one.
LOOSE = SheetSchema(
    name="upload", columns=(), fingerprint=("SG Order ID", "Order Total")
)


@pytest.mark.parametrize("name", list(HAND_MADE))
def test_a_hidden_column_is_the_upload_without_it(tmp_path: Path, name: str) -> None:
    """Leaving a column out writes what copying the upload without that column
    wrote: every row, the title rows too, and the widths."""
    sheets = HAND_MADE[name]
    path = _workbook(tmp_path / "upload.xlsx", sheets)
    gone = 1  # Partner Order ID, and the title rows' second cell
    without = tmp_path / "without.xlsx"
    book = Workbook()
    book.remove(book.active)
    for sheet_name, (rows, titles) in sheets.items():
        sheet = book.create_sheet(sheet_name)
        for t in range(titles):
            sheet.append(("Title " + str(t + 1), "beside", t))
        sheet.append((*HEADERS[:gone], *HEADERS[gone + 1 :]))
        for values in rows:
            kept = values[:gone] + values[gone + 1 :]
            sheet.append(tuple(float(v) if isinstance(v, Decimal) else v for v in kept))
    book.save(without)
    edits = CopyEdits(hidden=frozenset({"partnerorderid"}))
    new, _old = _copy_both(tmp_path, path, list(sheets), LOOSE, "vendor_header", edits)
    expected = tmp_path / "expected.xlsx"
    book = _book(expected)
    try:
        legacy._write_source_sheet(
            book,
            report_formats(book),
            "Copy",
            without,
            list(sheets),
            LOOSE,
            "vendor_header",
        )
    finally:
        book.close()
    assert workbook_differences(new, expected) == []


def test_added_columns_follow_the_last_on_each_row_after_the_header(
    tmp_path: Path,
) -> None:
    path = _workbook(tmp_path / "upload.xlsx", {"Sheet": (_ROWS, 2)})
    written: list[tuple[int, int, dict[str, int]]] = []

    def write(sheet: Any, row: int, first: int, landed: dict[str, int]) -> None:
        written.append((row, first, dict(landed)))
        sheet.write_string(row, first, f"row {row}")

    edits = CopyEdits(
        hidden=frozenset({"vertical"}), added=(("Note", 21), ("More", 9)), write=write
    )
    new, _old = _copy_both(
        tmp_path, path, ["Sheet"], food_portal_schema(), "vendor_header", edits
    )
    sheet = load_workbook(new)["Copy"]
    header = 2  # 0-based, below two title rows
    last = len(HEADERS) - 1  # one column left out
    assert sheet.cell(header + 1, last + 1).value == "Note"
    assert sheet.cell(header + 1, last + 2).value == "More"
    assert sheet.cell(header + 1, last + 1).font.b
    # Excel keeps a width with its padding: 21 is stored as 21.71.
    assert round(sheet.column_dimensions[letter(last)].width) == 22
    assert round(sheet.column_dimensions[letter(last + 1)].width) == 10
    # Every row after the header with a value, and only those: the empty row
    # gets nothing.
    assert [row for row, _first, _landed in written] == [3, 4, 6]
    assert {first for _row, first, _landed in written} == {last}
    landed = written[0][2]
    assert landed["sgorderid"] == 0 and landed["ordertotal"] == 2
    assert "vertical" not in landed
    assert sheet.auto_filter.ref == f"A{header + 1}:{letter(last + 1)}{len(_ROWS) + 3}"


def _report(
    category: str, scenario: str, edit: Any, tmp_path: Path
) -> openpyxl.Workbook:
    """A scenario's report under its seed with one tab edited."""
    chosen = next(s for s in SCENARIOS if s.name == scenario)
    config = load_seed(category)
    data = config.model_dump(mode="json")
    edit(data)
    config = CategoryConfig.model_validate(data)
    assert problems(config) == []
    uploads = {role: Upload(role, p.name, p) for role, p in chosen.files.items()}
    with use_config(config):
        result = run_generic(config, uploads, max_rows=1_000_000)
        path = tmp_path / "report.xlsx"
        render(
            config, report_data(result, dict(chosen.files)), path, dict(chosen.files)
        )
    return openpyxl.load_workbook(path)


def _tab(data: dict[str, Any], key: str) -> dict[str, Any]:
    tab: dict[str, Any] = next(t for t in data["output"]["tabs"] if t["key"] == key)
    return tab


def _column_of(sheet: Any, header: str, row: int = 1) -> str:
    for cell in sheet[row]:
        if isinstance(cell.value, str) and normalize_header(cell.value) == header:
            return letter(cell.column - 1)
    raise AssertionError(f"no {header!r} on row {row}")


def test_a_copy_with_edits_keeps_what_points_into_it(tmp_path: Path) -> None:
    """The eShop Portal copy, its 'time' column left out and three columns
    added: the ERP's unit price still points at Shipping Cost."""

    def edit(data: dict[str, Any]) -> None:
        portal = _tab(data, "portal_copy")
        portal["hidden"] = ["time"]
        portal["columns"] = [
            {
                "key": "doubled",
                "header": "Doubled",
                "width": 12,
                "group": "meta",
                "kind": "money",
                "value": "IF(ISNUMBER(shippingcost), shippingcost * 2, BLANK())",
            },
            {
                "key": "live",
                "header": "Live",
                "width": 11,
                "group": "meta",
                "kind": "money",
                "excel": "={col:shippingcost}{row}*2",
                "style": '"money"',
            },
            {
                "key": "lost",
                "header": "Lost",
                "width": 10,
                "group": "meta",
                "kind": "text",
                "excel": "={col:nothere}{row}",
            },
        ]

    book = _report("eshop", "eshop", edit, tmp_path)
    portal = book["Portal"]
    headers = [c.value for c in portal[1] if c.value is not None]
    assert "time" not in headers and headers[-3:] == ["Doubled", "Live", "Lost"]
    shipping = _column_of(portal, "shippingcost")
    erp = book["B2C Invoices ERP"]
    unit = erp["G2"].value
    assert unit.startswith(f"=Portal!{shipping}") and unit.endswith("/1.15")
    row = int(unit[len(f"=Portal!{shipping}") : -len("/1.15")])
    live = _column_of(portal, "live")
    assert portal[f"{live}{row}"].value == f"={shipping}{row}*2"
    doubled = _column_of(portal, "doubled")
    value = portal[f"{shipping}{row}"].value
    assert portal[f"{doubled}{row}"].value == pytest.approx(2 * float(value))
    lost = _column_of(portal, "lost")
    assert portal[f"{lost}{row}"].value is None


def test_the_finance_lookups_follow_the_copy(tmp_path: Path) -> None:
    def edit(data: dict[str, Any]) -> None:
        _tab(data, "finance_copy")["hidden"] = ["User ID"]

    book = _report("travel", "travel-claim-13-finance", edit, tmp_path)
    finance = book["Finance Report"]
    order_id = _column_of(finance, "orderid", row=14)
    assert order_id == "B"  # was C, before User ID left
    assert finance["B1"].value is None  # the title row's second cell left too
    lookups = [
        cell.value.text if hasattr(cell.value, "text") else cell.value
        for row in book["Orders"].iter_rows(min_row=2, max_row=2)
        for cell in row
        if "Finance Report" in str(getattr(cell.value, "text", cell.value))
    ]
    assert lookups and all(
        f"'Finance Report'!{order_id}:{order_id}" in f for f in lookups
    )


def test_a_copys_column_styled_blank_keeps_its_own_format(tmp_path: Path) -> None:
    """Two columns added to the Portal copy with the same value, one styled
    money_bad above 1000 and blank below: below, it is written as the plain one."""

    def column(key: str, style: str | None) -> dict[str, Any]:
        return {
            "key": key,
            "header": key.title(),
            "width": 12,
            "group": "meta",
            "kind": "money",
            "value": "ordertotal",
            "excel": None,
            "color": None,
            "style": style,
        }

    def edit(data: dict[str, Any]) -> None:
        _tab(data, "portal_copy")["columns"] = [
            column("styled", 'IF(ordertotal > 1000, "money_bad", BLANK())'),
            column("plain", None),
        ]

    portal = _report("travel", "travel-claim-13", edit, tmp_path)["Portal"]
    header = next(
        row
        for row in range(1, portal.max_row + 1)
        if any(cell.value == "Styled" for cell in portal[row])
    )
    styled = _column_of(portal, "styled", row=header)
    plain = _column_of(portal, "plain", row=header)
    above = below = 0
    for row in range(header + 1, portal.max_row + 1):
        value = portal[f"{plain}{row}"].value
        if not isinstance(value, int | float):
            continue
        cell, twin = portal[f"{styled}{row}"], portal[f"{plain}{row}"]
        if value > 1000:
            above += 1
            assert _cell_state(cell) != _cell_state(twin)
        else:
            below += 1
            assert _cell_state(cell) == _cell_state(twin)
    assert above and below


def test_look_up_columns_need_a_part_that_fills_them() -> None:
    """With Fills the look-up columns unticked on every part, the Orders
    look-up columns would be written empty."""
    travel = load_seed("travel").model_dump(mode="json")
    t = next(
        i for i, tab in enumerate(travel["output"]["tabs"]) if tab["key"] == "orders"
    )
    for section in travel["output"]["tabs"][t]["sections"]:
        section["lookups"] = False
    found = [
        (p.path, p.message) for p in problems(CategoryConfig.model_validate(travel))
    ]
    assert found == [
        (
            f"output.tabs[{t}].lookup_columns",
            "The Orders sheet has look-up columns, but none of its parts fills "
            "them: tick Fills the look-up columns in its Sheet layout, or remove "
            "the columns",
        )
    ]
    # One part looking them up is enough.
    travel["output"]["tabs"][t]["sections"][0]["lookups"] = True
    assert problems(CategoryConfig.model_validate(travel)) == []


def test_a_copy_cannot_leave_out_what_another_tab_reads() -> None:
    finance = load_seed("travel").model_dump(mode="json")
    _tab(finance, "finance_copy")["hidden"] = ["Order ID", "Spin Event ID"]
    found = [
        (p.path, p.message) for p in problems(CategoryConfig.model_validate(finance))
    ]
    t = next(
        i for i, x in enumerate(finance["output"]["tabs"]) if x["key"] == "finance_copy"
    )
    assert found == [
        (
            f"output.tabs[{t}].hidden[0]",
            "'Order ID' cannot be left out: a look-up column reads it",
        )
    ]
    eshop = load_seed("eshop").model_dump(mode="json")
    _tab(eshop, "portal_copy")["hidden"] = ["Shipping Cost"]
    found = [
        (p.path, p.message) for p in problems(CategoryConfig.model_validate(eshop))
    ]
    t = next(
        i for i, x in enumerate(eshop["output"]["tabs"]) if x["key"] == "portal_copy"
    )
    assert found == [
        (
            f"output.tabs[{t}].hidden[0]",
            "'Shipping Cost' cannot be left out: "
            "{cell:portal_copy.shippingcost} reads it",
        )
    ]


def test_a_copy_has_no_parts() -> None:
    data = load_seed("food").model_dump(mode="json")
    _tab(data, "vendor_copy")["sections"] = [{"kind": "rows", "set": "orders"}]
    found = [(p.path, p.message) for p in problems(CategoryConfig.model_validate(data))]
    t = next(
        i for i, x in enumerate(data["output"]["tabs"]) if x["key"] == "vendor_copy"
    )
    assert (
        f"output.tabs[{t}].sections",
        "A copy writes its upload's rows as they arrived: it has no parts",
    ) in found


def test_the_copy_template_writes_the_approved_sheet(
    runs_client: TestClient, tmp_path: Path
) -> None:
    """The editor's way: start from the standard copy, name the upload and its
    sheet, save, run: the approved report."""
    scenario = next(s for s in SCENARIOS if s.name == "eshop-wallet-finance")
    templates = runs_client.get(f"{API}/standard").json()["tabs"]
    opened = runs_client.get(f"{API}/categories/eshop/config").json()
    config = opened["config"]
    tabs = config["output"]["tabs"]
    for i, tab in enumerate(tabs):
        if tab["key"] == "portal_copy":
            tabs[i] = {
                **templates["copy"],
                "key": "portal_copy",
                "title": "Portal",
                "source": "portal",
                "sheet": "portal",
                "header_format": "portal_header",
            }
        if tab["key"] == "finance_copy":
            # A copy of a file to look things up in is written when it came.
            tabs[i] = {
                **templates["copy"],
                "key": "finance_copy",
                "title": tab["title"],
                "source": "finance",
                "sheet": "report",
                "header_format": "cashback_header",
                "requires": "finance",
            }
    saved = runs_client.put(
        f"{API}/categories/eshop/config",
        json={"config": config, "note": "test", "base_version": opened["version"]},
    )
    assert saved.status_code == 200, saved.text
    run = runs_client.post(
        f"{API}/runs",
        data={"category": "eshop"},
        files={
            role: (path.name, path.read_bytes(), XLSX)
            for role, path in scenario.files.items()
        },
    ).json()
    assert run["status"] == "completed", run
    report = runs_client.get(f"{API}/runs/{run['number']}/report")
    downloaded = tmp_path / "report.xlsx"
    downloaded.write_bytes(report.content)
    assert (
        workbook_differences(downloaded, BASELINE / scenario.name / "report.xlsx") == []
    )
