"""The setup check: a name a formula reads that is not there when it runs, or a
value of a kind its operator cannot take, is found before the setup is saved,
and nothing in the approved setups is."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from app.config_model.check import lineage, problems
from app.config_model.models import CategoryConfig
from app.config_model.runtime import use_config
from app.config_model.seed import load_seed
from app.recon.inputs import Upload
from app.runner import run
from tests.conftest import FOOD_PORTAL, FOOD_VENDOR

CATEGORIES = ("eshop", "travel", "food")

Edit = Callable[[dict[str, Any]], None]


def _unknown(name: str) -> str:
    return (
        f"Nothing is called {name!r} yet. Check the spelling, or add a column or "
        "field with that name."
    )


def _found(category: str, edit: Edit) -> list[tuple[str, str]]:
    config = load_seed(category).model_dump(mode="json")
    edit(config)
    checked = problems(CategoryConfig.model_validate(config))
    return [(p.path, p.message) for p in checked]


def _field(formula: str) -> Edit:
    """An order field added after the others."""
    return lambda config: config["model"]["fields"].append(
        {"key": "probe", "formula": formula}
    )


def _column(formula: str) -> Edit:
    """A column added to the end of the Orders sheet."""

    def edit(config: dict[str, Any]) -> None:
        orders = next(t for t in config["output"]["tabs"] if t["key"] == "orders")
        orders["columns"].append(
            {
                "key": "probe",
                "header": "Probe",
                "width": 12,
                "group": "check",
                "kind": "text",
                "value": formula,
            }
        )

    return edit


@pytest.mark.parametrize("category", CATEGORIES)
def test_the_approved_setups_have_nothing_to_fix(category: str) -> None:
    assert problems(load_seed(category)) == []


@pytest.mark.parametrize(
    ("category", "formula", "message"),
    [
        ("travel", "no_such_field + 1", _unknown("no_such_field")),
        ("travel", "param.tolerence", "param has no 'tolerence'"),
        ("travel", "portal.statuss", "portal has no 'statuss'"),
        ("food", "portal.statuss", "portal has no 'statuss'"),
        ("food", 'SUMX("nope", 1)', "No set called 'nope'"),
        ("eshop", 'LOOKUP("nope", key)', "No lookup called 'nope'"),
        ("travel", '"text" * 2', "'*' needs a number, but \"text\" is text"),
        (
            "food",
            "portal.order_status * 2",
            "'*' needs a number, but portal.order_status is text",
        ),
        (
            "food",
            'SUMX("portal", order_status)',
            "SUMX needs a number, but order_status is text",
        ),
        (
            "eshop",
            "param.promo_cutoff > 5",
            "param.promo_cutoff > 5 compares a date with a number, which can't be done. "
            "Make both sides the same kind of value.",
        ),
        (
            "food",
            'SUMIFS("portal", total_amount, order_status, "a", sub_total)',
            "SUMIFS takes value, criteria pairs",
        ),
    ],
)
def test_a_name_or_kind_that_cannot_work_is_found_where_it_is(
    category: str, formula: str, message: str
) -> None:
    found = _found(category, _field(formula))
    fields = len(load_seed(category).model.fields)  # type: ignore[union-attr]
    assert found == [(f"model.fields[{fields}].formula", message)]


def test_a_field_cannot_read_one_worked_out_after_it() -> None:
    def edit(config: dict[str, Any]) -> None:
        later = config["model"]["fields"][1]["key"]
        config["model"]["fields"].insert(0, {"key": "probe", "formula": f"{later} + 0"})

    later = load_seed("food").model.fields[1].key  # type: ignore[union-attr]
    assert _found("food", edit) == [
        (
            "model.fields[0].formula",
            f"{later!r} isn't ready here: it's worked out later. Use it in something "
            "that comes after it.",
        )
    ]


def test_a_name_close_to_one_that_is_there_is_suggested() -> None:
    first = load_seed("travel").model.fields[0].key  # type: ignore[union-attr]
    fields = len(load_seed("travel").model.fields)  # type: ignore[union-attr]
    assert _found("travel", _field(f"{first}s")) == [
        (
            f"model.fields[{fields}].formula",
            f"Nothing is called {first + 's'!r}. Did you mean {first!r}?",
        )
    ]


def test_names_are_checked_in_every_part_of_a_setup() -> None:
    def rule(config: dict[str, Any]) -> None:
        config["model"]["rules"][0]["when"] = "no_such_status = 1"

    def figure(config: dict[str, Any]) -> None:
        config["model"]["metrics"].append({"key": "probe", "formula": "nope"})

    def sheet_field(config: dict[str, Any]) -> None:
        config["inputs"][0]["sheets"][0]["fields"].append(
            {"key": "probe", "formula": "nope"}
        )

    assert _found("food", rule) == [("model.rules[0].when", _unknown("no_such_status"))]
    metrics = len(load_seed("food").model.metrics)  # type: ignore[union-attr]
    assert _found("food", figure) == [
        (f"model.metrics[{metrics}].formula", _unknown("nope"))
    ]
    sheet = load_seed("travel").inputs[0].sheets[0]
    assert _found("travel", sheet_field) == [
        (
            f"inputs[0].sheets[0].fields[{len(sheet.fields)}].formula",
            _unknown("nope"),
        )
    ]
    orders = next(t for t in load_seed("travel").output.tabs if t.key == "orders")
    tab = load_seed("travel").output.tabs.index(orders)
    assert _found("travel", _column("nope")) == [
        (
            f"output.tabs[{tab}].columns[{len(orders.columns)}].value",
            _unknown("nope"),
        )
    ]


@pytest.mark.parametrize("kind", ["wallet", "reference"])
def test_the_wallet_and_reference_lists_are_checked_like_any_upload(kind: str) -> None:
    travel = load_seed("travel")
    i = next(i for i, upload in enumerate(travel.inputs) if upload.kind == kind)
    role = travel.inputs[i].role

    def sheet_field(config: dict[str, Any]) -> None:
        config["inputs"][i]["sheets"][0]["fields"].append(
            {"key": "probe", "formula": "nope"}
        )

    assert _found("travel", sheet_field) == [
        (f"inputs[{i}].sheets[0].fields[0].formula", _unknown("nope"))
    ]
    assert _found("travel", _field(f'COUNTX("{role}")')) == []
    sets = len(travel.model.sets)  # type: ignore[union-attr]
    assert _found(
        "travel",
        lambda c: c["model"]["sets"].append(
            {"key": role, "over": "entities", "where": "TRUE"}
        ),
    ) == [
        (
            f"model.sets[{sets}].key",
            f"{role!r} is already the name of an upload's rows or of the run's own list",
        )
    ]


@pytest.mark.parametrize(
    ("category", "edit"),
    [
        # Text or a number: it cannot be told, so nothing is said.
        ("travel", _field('IF(TRUE, "a", 1) * 2')),
        # Inside an aggregate, names are the set's members'.
        ("food", _field('SUMX("portal", N(total_amount))')),
        ("food", _field('SUMIF("portal", order_status, "Delivered", total_amount)')),
        ("food", _field('COUNTIFS("portal", order_status, "<>", total_amount, ">0")')),
        # The editor's own example: a column with a formula over order fields.
        ("travel", _column("IF(both, vendor_base / portal_base, BLANK())")),
    ],
)
def test_what_can_work_is_let_through(category: str, edit: Edit) -> None:
    assert _found(category, edit) == []


def test_a_lookup_in_an_uploads_own_field_is_not_ready_yet() -> None:
    def edit(config: dict[str, Any]) -> None:
        table = config["model"]["lookups"][0]["key"]
        config["inputs"][0]["sheets"][0]["fields"].append(
            {"key": "probe", "formula": f'LOOKUP("{table}", "x")'}
        )

    sheet = load_seed("eshop").inputs[0].sheets[0]
    table = load_seed("eshop").model.lookups[0]  # type: ignore[union-attr]
    assert _found("eshop", edit) == [
        (
            f"inputs[0].sheets[0].fields[{len(sheet.fields)}].formula",
            f"Lookup {table.key!r} is made from {table.set!r}, which is not ready "
            "while the uploads are read: use a row field",
        )
    ]


def test_a_column_with_no_formula_needs_a_key_that_is_a_name() -> None:
    def edit(config: dict[str, Any]) -> None:
        orders = next(t for t in config["output"]["tabs"] if t["key"] == "orders")
        orders["columns"].append(
            {
                "key": "2nd_check",
                "header": "X",
                "width": 9,
                "group": "check",
                "kind": "text",
            }
        )

    orders = next(t for t in load_seed("food").output.tabs if t.key == "orders")
    tab = load_seed("food").output.tabs.index(orders)
    assert _found("food", edit) == [
        (
            f"output.tabs[{tab}].columns[{len(orders.columns)}].key",
            "A column with no formula reads the name its key is, and '2nd_check' "
            "is not one",
        )
    ]


def test_sets_made_from_each_other_are_said_once_each_and_nothing_follows() -> None:
    def edit(config: dict[str, Any]) -> None:
        config["model"]["sets"] += [
            {"key": "qa_a", "over": "qa_b", "where": "TRUE"},
            {"key": "qa_b", "over": "qa_a", "where": "TRUE"},
        ]
        config["model"]["metrics"].append(
            {"key": "probe", "formula": 'COUNTX("qa_a", nope_inside)'}
        )

    sets = len(load_seed("food").model.sets)  # type: ignore[union-attr]
    assert _found("food", edit) == [
        (f"model.sets[{sets}].over", "'qa_a' and 'qa_b' are made from each other"),
        (f"model.sets[{sets + 1}].over", "'qa_b' and 'qa_a' are made from each other"),
    ]

    # Travel's orders gather their lines from its first set: broken, it is said
    # where it is configured, not again on every field that reads a line.
    def itself(config: dict[str, Any]) -> None:
        config["model"]["sets"][0]["over"] = config["model"]["sets"][0]["key"]

    first = load_seed("travel").model.sets[0].key  # type: ignore[union-attr]
    assert _found("travel", itself) == [
        ("model.sets[0].over", f"{first!r} is made from itself")
    ]


def test_a_written_out_criterion_that_is_not_a_date_against_dates_is_found() -> None:
    found = _found("food", _field('COUNTIF("portal", order_date, ">=01/09/2026")'))
    fields = len(load_seed("food").model.fields)  # type: ignore[union-attr]
    assert found == [
        (
            f"model.fields[{fields}].formula",
            "COUNTIF: '>=01/09/2026' is not a number or a date (write dates as "
            "YYYY-MM-DD)",
        )
    ]
    assert _found("food", _field('COUNTIF("portal", order_date, ">=2026-09-01")')) == []


def test_a_block_naming_no_column_is_found() -> None:
    def edit(config: dict[str, Any]) -> None:
        orders = next(t for t in config["output"]["tabs"] if t["key"] == "orders")
        for section in orders["sections"]:
            for row in section.get("rows", []):
                for cell in row.get("cells", []):
                    if cell.get("formula"):
                        cell["formula"] = "={col:nope}{row}"
                        return

    found = _found("food", edit)
    assert len(found) == 1 and found[0][1] == "No column 'nope'"
    assert found[0][0].endswith(".formula")


def test_an_order_reads_its_note_before_any_rule_writes_one() -> None:
    """The note is blank until a status rule writes it, in a pair setup too."""
    edit = _field("IF(ISBLANK(note), 1, 2)")
    assert _found("food", edit) == []
    config = load_seed("food").model_dump(mode="json")
    edit(config)
    setup = CategoryConfig.model_validate(config)
    uploads = {
        "portal": Upload(role="portal", filename="p.xlsx", path=FOOD_PORTAL),
        "vendor": Upload(role="vendor", filename="v.xlsx", path=FOOD_VENDOR),
    }
    with use_config(setup):
        result = run(setup, uploads, max_rows=1_000_000)
    assert {e.values["probe"] for e in result.entities} == {Decimal(1)}


def _tabs(config: dict[str, Any]) -> list[dict[str, Any]]:
    tabs: list[dict[str, Any]] = config["output"]["tabs"]
    return tabs


def _tab_at(category: str, key: str) -> int:
    return next(
        i for i, t in enumerate(load_seed(category).output.tabs) if t.key == key
    )


def _new_tab(**fields: Any) -> dict[str, Any]:
    return {"key": "probe", "title": "Probe", **fields}


@pytest.mark.parametrize(
    ("edit", "path", "message"),
    [
        (
            lambda c: _tabs(c).append(_new_tab(kind="dashboard", title="Orders")),
            "output.tabs[8].title",
            "Two tabs are called 'Orders'",
        ),
        (
            lambda c: _tabs(c).append(_new_tab(kind="dashboard", title="orders")),
            "output.tabs[8].title",
            "Two tabs are called 'orders'",
        ),
        (
            lambda c: _tabs(c).append(_new_tab(kind="dashboard", title="A" * 32)),
            "output.tabs[8].title",
            "A tab's title is at most 31 characters",
        ),
        (
            lambda c: _tabs(c).append(_new_tab(kind="dashboard", title="Q1/Q2")),
            "output.tabs[8].title",
            "A tab's title cannot have any of [ ] : * ? / \\",
        ),
        (
            lambda c: _tabs(c).append(_new_tab(kind="dashboard", title="history")),
            "output.tabs[8].title",
            "Excel keeps the name History for itself",
        ),
        (
            lambda c: _tabs(c).append(_new_tab(kind="table")),
            "output.tabs[8].columns",
            "A table needs a column",
        ),
        (
            lambda c: _tabs(c).append(_new_tab(kind="dashboard")),
            "output.tabs[8].blocks",
            "A dashboard needs a block or a breakdown",
        ),
        (
            lambda c: _tabs(c)[3].update(requires=None),
            "output.tabs[3].requires",
            "A table of the wallet check's rows is written only when a wallet "
            "history was uploaded",
        ),
        (
            lambda c: _tabs(c)[3]["sections"][2]["styles"].update(nothing='"money"'),
            "output.tabs[3].sections[2].styles.nothing",
            "No column 'nothing'",
        ),
        (
            lambda c: _tabs(c)[3]["sections"][2]["styles"].update(
                portal_wallet='"loud"'
            ),
            "output.tabs[3].sections[2].styles.portal_wallet",
            "No format is called 'loud'",
        ),
        (
            lambda c: _tabs(c)[3]["sections"][3]["rows"][1]["cells"][1].update(
                value="wallet_check.rows_checked + nothing_here"
            ),
            "output.tabs[3].sections[3].rows[1].cells[1].value",
            _unknown("nothing_here"),
        ),
        (
            lambda c: _tabs(c).append(
                _new_tab(
                    kind="table",
                    columns=[
                        {
                            "key": "k",
                            "header": "K",
                            "width": 9,
                            "group": "meta",
                            "kind": "text",
                            "value": "key",
                        }
                    ],
                    sections=[
                        {"kind": "rows", "set": "orders"},
                        {"kind": "rows", "set": "orders"},
                    ],
                )
            ),
            "output.tabs[8].sections[1].set",
            "'orders' is listed already: a table lists a set once",
        ),
        (
            lambda c: _tabs(c).insert(1, _tabs(c).pop(4)),
            "output.tabs[1].columns[6].excel",
            "{cell:orders.sg_delivery} points at the orders rows: name a table "
            "before this tab that lists them",
        ),
        (
            lambda c: _tabs(c)[4]["columns"][6].update(
                excel="={cell:orders.nothing}/1.15"
            ),
            "output.tabs[4].columns[6].excel",
            "The Orders sheet has no column 'nothing'",
        ),
        (
            lambda c: _tabs(c)[4]["columns"][6].update(
                excel="={cell:sg_delivery}/1.15"
            ),
            "output.tabs[4].columns[6].excel",
            "{cell:sg_delivery} names a tab and a column: {cell:tab.column}",
        ),
        (
            lambda c: _tabs(c)[4]["columns"][6].update(value="key"),
            "output.tabs[4].columns[6].excel",
            "A column has a formula or an Excel formula, not both",
        ),
        (
            lambda c: _tabs(c)[4]["sections"][0].update(upload="portal"),
            "output.tabs[4].sections[0].set",
            "Name the set the rows are, or the upload",
        ),
        (
            lambda c: _tabs(c)[4]["sections"][0]["contacts"].update(match=None),
            "output.tabs[4].sections[0].contacts",
            "Find a row's contact by its upload row, or by a key and the value "
            "it matches",
        ),
        (
            lambda c: _tabs(c)[4]["sections"][0]["contacts"].update(upload="nope"),
            "output.tabs[4].sections[0].contacts.upload",
            "No upload is called 'nope'",
        ),
        (
            lambda c: _tabs(c).pop(5),
            None,
            None,
        ),
        (
            lambda c: _tabs(c)[1]["columns"][0].update(group="purple"),
            "output.tabs[1].columns[0].group",
            "No header colour band is called 'purple'",
        ),
        (
            lambda c: _tabs(c)[1]["columns"][0].update(color="red"),
            "output.tabs[1].columns[0].color",
            "'red' is not a colour: give it as #RRGGBB",
        ),
        (
            lambda c: _tabs(c)[0]["blocks"][0]["headers"].pop(),
            "output.tabs[0].blocks[0].headers",
            "A block has one header for each figure",
        ),
        (
            lambda c: _tabs(c)[0]["blocks"][0]["values"][0].update(
                metric="vendor_name"
            ),
            "output.tabs[0].blocks[0].values[0].metric",
            "vendor_name is text, and this writes a number",
        ),
        (
            lambda c: _tabs(c)[0]["breakdown"].update(rank_by=9),
            "output.tabs[0].breakdown.rank_by",
            "No such column to rank by",
        ),
        (
            lambda c: _tabs(c)[0]["breakdown"]["columns"][2].update(value=None),
            "output.tabs[0].breakdown.columns[2].value",
            "Say what the column adds up",
        ),
        (
            lambda c: _tabs(c)[0]["breakdown"]["columns"][2].update(value="nope + 1"),
            "output.tabs[0].breakdown.columns[2].value",
            _unknown("nope"),
        ),
    ],
)
def test_a_report_that_cannot_be_written_is_found(
    edit: Edit, path: str | None, message: str | None
) -> None:
    found = _found("food", edit)
    if path is None:  # the vendor copy is not needed by anything
        assert found == []
    else:
        assert (path, message) in found, found


def test_eshop_invoices_need_the_copy_they_read() -> None:
    copy = _tab_at("eshop", "portal_copy")
    invoices = _tab_at("eshop", "b2c_invoices")

    def without_copy(config: dict[str, Any]) -> None:
        _tabs(config).pop(copy)

    assert _found("eshop", without_copy) == [
        (
            f"output.tabs[{invoices}].columns[6].excel",
            "{cell:portal_copy.shippingcost} points at the copy of portal: name a "
            "tab that copies it",
        )
    ]


def test_two_tables_may_list_the_same_set() -> None:
    """The B2C invoices list the orders the Orders sheet lists too."""

    def another(config: dict[str, Any]) -> None:
        _tabs(config).append(
            _new_tab(
                kind="table",
                columns=[
                    {
                        "key": "k",
                        "header": "K",
                        "width": 9,
                        "group": "meta",
                        "kind": "text",
                        "value": "key",
                    }
                ],
                sections=[{"kind": "rows", "set": "orders", "lookups": False}],
            )
        )

    assert _found("food", another) == []


def test_lineage_says_where_each_name_comes_from() -> None:
    """A name inside an aggregate is the set's field, not a figure of the same
    name; a column with no formula reads its key; a breakdown reads its set."""
    eshop = load_seed("eshop")
    model = eshop.model
    assert model is not None
    reads = {use.path: use.reads for use in lineage(eshop)}
    k = _index_of(model.metrics, "gross_sales")
    gmv_raw = _index_of(model.fields, "gmv_raw")
    assert reads[f"model.metrics[{k}].formula"] == [f"model.fields[{gmv_raw}]"]
    orders = _tab_at("eshop", "orders")
    tab = eshop.output.tabs[orders]
    c = next(i for i, col in enumerate(tab.columns) if col.value == "key")
    assert reads[f"output.tabs[{orders}].columns[{c}].value"] == ["model.key"]
    travel = load_seed("travel")
    t = _tab_at("travel", "dashboard")
    net = _index_of(travel.model.fields, "net_payable_as_vendor")  # type: ignore[union-attr]
    assert reads_of(travel)[f"output.tabs[{t}].breakdown.columns[8].value"] == [
        f"model.fields[{net}]"
    ]


def _index_of(items: Any, key: str) -> int:
    return next(i for i, item in enumerate(items) if item.key == key)


def reads_of(config: CategoryConfig) -> dict[str, list[str]]:
    return {use.path: use.reads for use in lineage(config)}


@pytest.mark.parametrize(
    ("category", "edit", "path", "message"),
    [
        (
            "eshop",
            lambda c: c["parameters"][0].update(value="ten"),
            "parameters[0].value",
            "'ten' is not a number",
        ),
        (
            "eshop",
            lambda c: next(p for p in c["parameters"] if p["kind"] == "date").update(
                value="01/09/2026"
            ),
            "parameters[2].value",
            "'01/09/2026' is not a date, YYYY-MM-DD",
        ),
        (
            "travel",
            lambda c: c["model"]["sets"].append(
                {"key": "portal", "over": "entities", "where": "TRUE"}
            ),
            "model.sets[9].key",
            "'portal' is already the name of an upload's rows or of the run's own list",
        ),
        (
            "travel",
            lambda c: c["model"]["context"].append(
                {"key": "claim_start", "formula": "1"}
            ),
            "model.context",
            "Two run values are called 'claim_start'",
        ),
        (
            "food",
            lambda c: c["exceptions"].pop(
                next(
                    i
                    for i, e in enumerate(c["exceptions"])
                    if e["code"] == "WALLET_KEY_UNSTABLE"
                )
            ),
            "exceptions",
            "The wallet check raises WALLET_KEY_UNSTABLE: keep it in the list",
        ),
        (
            "food",
            lambda c: _tabs(c)[1]["lookup_columns"][0].update(
                key=_tabs(c)[1]["columns"][0]["key"]
            ),
            "output.tabs[1].columns",
            None,
        ),
        (
            "food",
            lambda c: c["inputs"][0]["sheets"][0].update(set="portal rows"),
            "inputs[0].sheets[0].set",
            "'portal rows' is not a name",
        ),
    ],
)
def test_settings_the_editor_now_shows_are_checked(
    category: str, edit: Edit, path: str, message: str | None
) -> None:
    found = _found(category, edit)
    if message is None:
        assert any(p == path and m.startswith("Two columns") for p, m in found), found
    else:
        assert (path, message) in found, found


def test_a_setup_without_plain_words_is_as_valid() -> None:
    config = load_seed("travel").model_dump(mode="json")

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != "about"}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    bare = CategoryConfig.model_validate(strip(config))
    assert bare.model is not None and bare.model.findings[0].about is None
    assert problems(bare) == []


# --- The wallet history and the finance report are read by name ------------------


def _upload(config: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(f for f in config["inputs"] if f["kind"] == kind)


def _drop_column(kind: str, field: str) -> Edit:
    def edit(config: dict[str, Any]) -> None:
        sheet = _upload(config, kind)["sheets"][0]
        sheet["columns"] = [c for c in sheet["columns"] if c["field"] != field]

    return edit


@pytest.mark.parametrize(
    ("edit", "path", "message"),
    [
        (
            _drop_column("wallet", "sg_order_id"),
            "inputs[2].sheets[0].columns",
            "The wallet check reads the column 'sg_order_id': put it back",
        ),
        (
            lambda config: _upload(config, "wallet")["sheets"][0].update(key="main"),
            "inputs[2].sheets",
            "The wallet check reads its sheet by the key 'history': give it that key",
        ),
    ],
)
def test_a_standard_upload_keeps_the_names_it_is_read_by(
    edit: Edit, path: str, message: str
) -> None:
    assert (path, message) in _found("travel", edit)


def test_a_part_filling_look_ups_needs_the_columns_it_fills() -> None:
    orders = _tab_at("travel", "orders")

    def no_columns(config: dict[str, Any]) -> None:
        _tabs(config)[orders]["lookup_columns"] = []

    found = _found("travel", no_columns)
    looked_up = [
        s
        for s, section in enumerate(load_seed("travel").output.tabs[orders].sections)
        if section.kind == "rows" and section.lookups
    ]
    assert [path for path, _ in found] == [
        f"output.tabs[{orders}].sections[{s}].lookups" for s in looked_up
    ]
    assert "fills the look-up columns, but the Orders sheet has none" in found[0][1]

    # Without the copy they look in, each look-up column says so.
    def no_copy(config: dict[str, Any]) -> None:
        config["output"]["tabs"] = [
            t for t in _tabs(config) if t["key"] != "finance_copy"
        ]

    columns = len(load_seed("travel").output.tabs[orders].lookup_columns)
    assert _found("travel", no_copy) == [
        (
            f"output.tabs[{orders}].lookup_columns[{c}].lookup.sheet",
            "No tab copying an upload is called 'finance_copy'",
        )
        for c in range(columns)
    ]


def test_a_look_up_column_reads_columns_its_upload_has() -> None:
    orders = _tab_at("travel", "orders")
    found = _found("travel", _drop_column("reference", "reward_amount"))
    assert found == [
        (
            f"output.tabs[{orders}].lookup_columns[0].lookup.value",
            "finance has no column 'reward_amount' on its report sheet",
        )
    ]

    def two_keys(config: dict[str, Any]) -> None:
        _tabs(config)[orders]["lookup_columns"][1]["lookup"]["key_value"] = "key"

    def no_key_column(config: dict[str, Any]) -> None:
        _tabs(config)[orders]["lookup_columns"][2]["lookup"]["key_column"] = "nosuch"

    def a_formula_too(config: dict[str, Any]) -> None:
        _tabs(config)[orders]["lookup_columns"][3]["value"] = "key"

    def on_a_column(config: dict[str, Any]) -> None:
        column = _tabs(config)[orders]["columns"][0]
        column["lookup"] = _tabs(config)[orders]["lookup_columns"][0]["lookup"]

    here = f"output.tabs[{orders}]"
    assert _found("travel", two_keys) == [
        (
            f"{here}.lookup_columns[1].lookup",
            "Find each row by a column of the table or by a formula: one",
        )
    ]
    assert _found("travel", no_key_column) == [
        (f"{here}.lookup_columns[2].lookup.key_column", "No column 'nosuch'")
    ]
    assert _found("travel", a_formula_too) == [
        (
            f"{here}.lookup_columns[3].value",
            "A look-up column is filled by its look-up: it takes no formula, "
            "Excel formula or style",
        )
    ]
    assert (
        f"{here}.columns[0].lookup",
        "Only a look-up column looks things up: add it as one",
    ) in _found("travel", on_a_column)
