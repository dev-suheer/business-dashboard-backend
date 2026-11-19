"""Renaming in a setup: everything that uses the renamed thing is renamed with
it, nothing else is, and a rename that would change what a formula reads is
refused."""

import copy
import re
from collections.abc import Iterator
from typing import Any

import pytest

from app.config_model.check import formulas, problems
from app.config_model.models import CategoryConfig, OutputTab
from app.config_model.rename import RenameError, rename
from app.config_model.runtime import use_config
from app.config_model.seed import load_seed
from app.recon.inputs import Upload
from app.runner import run
from tests.conftest import FOOD_PORTAL, FOOD_VENDOR


def _index(items: Any, key: str) -> int:
    return next(i for i, item in enumerate(items) if item.key == key)


def _texts(config: CategoryConfig) -> dict[str, str]:
    return dict(formulas(config))


def test_an_order_field_is_renamed_where_the_editor_cannot_reach() -> None:
    food = load_seed("food")
    k = _index(food.model.fields, "ids_match")  # type: ignore[union-attr]
    renamed = rename(food, f"model.fields[{k}]", "ids_agree")
    assert problems(renamed.config) == []
    # The configured sets the editor does not show are renamed too.
    sets = {
        path
        for path in renamed.changed
        if path.startswith("model.sets[") and path.endswith(".where")
    }
    assert sets == {"model.sets[4].where", "model.sets[5].where", "model.sets[6].where"}
    for path in sets:
        assert "ids_agree" in _texts(renamed.config)[path]
        assert "ids_match" not in _texts(renamed.config)[path]


def test_a_renamed_setup_reconciles_exactly_as_before() -> None:
    food = load_seed("food")
    k = _index(food.model.fields, "ids_match")  # type: ignore[union-attr]
    renamed = rename(food, f"model.fields[{k}]", "ids_agree").config
    uploads = {
        "portal": Upload(role="portal", filename="p.xlsx", path=FOOD_PORTAL),
        "vendor": Upload(role="vendor", filename="v.xlsx", path=FOOD_VENDOR),
    }
    results = []
    for config in (food, renamed):
        with use_config(config):
            results.append(run(config, uploads, max_rows=1_000_000))
    before, after = results
    assert after.metrics == before.metrics
    assert [(e.key, e.verdict, e.codes) for e in after.entities] == [
        (e.key, e.verdict, e.codes) for e in before.entities
    ]


def test_a_figure_is_renamed_on_the_dashboard_and_in_the_history() -> None:
    travel = load_seed("travel")
    k = _index(travel.model.metrics, "bookings")  # type: ignore[union-attr]
    renamed = rename(travel, f"model.metrics[{k}]", "booking_count")
    assert problems(renamed.config) == []
    dashboard = {
        v.metric
        for tab in renamed.config.output.tabs
        for b in tab.blocks
        for v in b.values
    }
    assert "booking_count" in dashboard and "bookings" not in dashboard
    assert any(".blocks[" in path for path in renamed.changed)


def test_a_sheet_field_is_renamed_where_its_rows_are_read_and_nowhere_else() -> None:
    travel = load_seed("travel")
    k = _index(travel.inputs[0].sheets[0].fields, "booking_id")
    renamed = rename(travel, f"inputs[0].sheets[0].fields[{k}]", "booking_ref")
    assert problems(renamed.config) == []
    model = renamed.config.model
    assert model is not None
    # The portal rows' key, and the sets and wallet check made from them.
    portal = next(m for m in model.members if m.name == "portal")
    assert portal.key == "booking_ref"
    assert "booking_ref" in model.sets[4].where and "booking_ref" in model.sets[5].where
    assert model.wallet is not None and model.wallet.reference == "booking_ref"
    # The claim lines' own booking_id is another field: their member keeps it.
    lines = next(m for m in model.members if m.name == "lines")
    assert lines.key == "booking_id"
    # The field cleans the upload's column of the same name, which it still reads.
    field = renamed.config.inputs[0].sheets[0].fields[k]
    assert field.key == "booking_ref" and "booking_id" in field.formula


def test_a_same_named_thing_somewhere_else_is_left_alone() -> None:
    food = load_seed("food")
    doc = food.model_dump(mode="json")
    # An order field named as a portal column: the column stays as it is read.
    doc["model"]["fields"].append({"key": "total_amount", "formula": "1"})
    doc["model"]["metrics"].append(
        {
            "key": "probe",
            "formula": 'SUMX("entities", N(total_amount)) + SUMX("portal", total_amount)',
        }
    )
    config = CategoryConfig.model_validate(doc)
    fields = len(food.model.fields)  # type: ignore[union-attr]
    renamed = rename(config, f"model.fields[{fields}]", "one")
    probe = _texts(renamed.config)[f"model.metrics[{len(food.model.metrics)}].formula"]  # type: ignore[union-attr]
    assert probe == 'SUMX("entities", N(one)) + SUMX("portal", total_amount)'


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("2x", "is not a name"),
        ("TRUE", "is a value, not a name"),
        ("verdict", "There is already something called 'verdict' there"),
        ("n_repeats", "There is already something called 'n_repeats' there"),
    ],
)
def test_a_name_that_cannot_be_taken_is_refused(name: str, message: str) -> None:
    with pytest.raises(RenameError, match=message):
        rename(load_seed("travel"), "model.fields[0]", name)


def test_a_rename_that_would_change_what_a_formula_reads_is_refused() -> None:
    travel = load_seed("travel")
    doc = travel.model_dump(mode="json")
    orders = next(t for t in doc["output"]["tabs"] if t["key"] == "orders")
    # An Orders column reading a figure: renamed after an order field, it would
    # read the order's field instead.
    orders["columns"].append(
        {
            "key": "probe",
            "header": "P",
            "width": 9,
            "group": "check",
            "kind": "text",
            "value": "bookings",
        }
    )
    config = CategoryConfig.model_validate(doc)
    k = _index(config.model.metrics, "bookings")  # type: ignore[union-attr]
    with pytest.raises(RenameError, match="would change what a formula reads"):
        rename(config, f"model.metrics[{k}]", travel.model.fields[0].key)  # type: ignore[union-attr]


def invoices_of(config: CategoryConfig) -> OutputTab:
    return next(x for x in config.output.tabs if x.key == "b2c_invoices")


def test_a_column_is_renamed_with_its_totals_blocks_and_lookups() -> None:
    food = load_seed("food")
    t = next(i for i, tab in enumerate(food.output.tabs) if tab.key == "orders")
    orders = food.output.tabs[t]
    c = _index(orders.columns, "sg_delivery")
    renamed = rename(food, f"output.tabs[{t}].columns[{c}]", "sg_delivery_fee")
    tab = renamed.config.output.tabs[t]
    column = tab.columns[c]
    assert column.key == "sg_delivery_fee"
    # It had no formula: it keeps reading the name it read.
    assert column.value == (orders.columns[c].value or "sg_delivery")
    # The B2C invoices' unit price reads the column's cell.
    invoices = next(x for x in renamed.config.output.tabs if x.key == "b2c_invoices")
    unit_price = next(x for x in invoices.columns if x.key == "unit_price")
    assert unit_price.excel == "={cell:orders.sg_delivery_fee}/1.15"
    assert (
        f"output.tabs[{food.output.tabs.index(invoices_of(food))}].columns[6].excel"
        in (renamed.changed)
    )
    assert problems(renamed.config) == []

    k = _index(orders.columns, "sg_bear")
    renamed = rename(food, f"output.tabs[{t}].columns[{k}]", "sg_share")
    tab = renamed.config.output.tabs[t]
    cells = [
        cell.formula or ""
        for section in tab.sections
        for row in section.rows
        for cell in row.cells
    ]
    assert any("{col:sg_share}" in formula for formula in cells)
    assert not any("{col:sg_bear}" in formula for formula in cells)
    assert any("sg_share" in section.formats for section in tab.sections)
    assert problems(renamed.config) == []


def test_a_column_key_must_be_a_name_and_new_to_its_sheet() -> None:
    food = load_seed("food")
    t = next(i for i, tab in enumerate(food.output.tabs) if tab.key == "orders")
    with pytest.raises(RenameError, match="is not a name"):
        rename(food, f"output.tabs[{t}].columns[0]", "net-payable 2")
    with pytest.raises(RenameError, match="already has a column"):
        rename(food, f"output.tabs[{t}].columns[0]", food.output.tabs[t].columns[1].key)


def test_renaming_there_and_back_gives_the_setup_back() -> None:
    travel = load_seed("travel")
    for path, key in list(_elements(travel))[::17]:
        there = rename(travel, path, f"{key}_qa").config
        back = rename(there, path, key).config
        assert back.model_dump() == travel.model_dump(), path


def _elements(config: CategoryConfig) -> Iterator[tuple[str, str]]:
    model = config.model
    assert model is not None
    for k, named in enumerate(model.fields):
        yield f"model.fields[{k}]", named.key
    for k, named in enumerate(model.metrics):
        yield f"model.metrics[{k}]", named.key
    for i, upload in enumerate(config.inputs):
        for j, sheet in enumerate(upload.sheets):
            for k, named in enumerate(sheet.fields):
                yield f"inputs[{i}].sheets[{j}].fields[{k}]", named.key
            if upload.kind == "rows":
                for c, column in enumerate(sheet.columns):
                    yield f"inputs[{i}].sheets[{j}].columns[{c}]", column.field
    for i, spec in enumerate(model.row_fields):
        for k, named in enumerate(spec.fields):
            yield f"model.row_fields[{i}].fields[{k}]", named.key
    for t, tab in enumerate(config.output.tabs):
        for c, column in enumerate(tab.columns):
            yield f"output.tabs[{t}].columns[{c}]", column.key


@pytest.mark.parametrize(
    ("category", "step"), [("food", 1), ("eshop", 4), ("travel", 4)]
)
def test_every_field_figure_and_column_renames_cleanly(
    category: str, step: int
) -> None:
    config = load_seed(category)
    for path, key in list(_elements(config))[::step]:
        try:
            renamed = rename(config, path, f"{key}_qa")
        except RenameError as exc:
            assert _read_with_another(config, path, exc), path
            continue
        assert problems(renamed.config) == [], path


def _read_with_another(config: CategoryConfig, path: str, exc: RenameError) -> bool:
    """An upload's column that a formula reads on rows that may come from
    another upload with a column of the same name: Food's order_status, on the
    rows left out of the pairing. It can only be renamed with that one."""
    found = re.fullmatch(r"inputs\[(\d+)\]\.sheets\[(\d+)\]\.columns\[(\d+)\]", path)
    if found is None or "may also be another" not in exc.message:
        return False
    i, j, c = (int(n) for n in found.groups())
    field = config.inputs[i].sheets[j].columns[c].field
    return any(
        column.field == field
        for k, upload in enumerate(config.inputs)
        if k != i and upload.kind == "rows"
        for sheet in upload.sheets
        for column in sheet.columns
    )


def test_a_field_is_renamed_in_the_breakdown_columns() -> None:
    travel = load_seed("travel")
    k = _index(travel.model.fields, "net_payable_as_vendor")  # type: ignore[union-attr]
    renamed = rename(travel, f"model.fields[{k}]", "net_to_vendor")
    t = next(i for i, tab in enumerate(travel.output.tabs) if tab.breakdown)
    spec = renamed.config.output.tabs[t].breakdown
    assert spec is not None
    assert spec.columns[8].value == "net_to_vendor * share"
    assert f"output.tabs[{t}].breakdown.columns[8].value" in renamed.changed
    assert problems(renamed.config) == []


# --- An upload's columns ----------------------------------------------------------


def _column(config: CategoryConfig, i: int, j: int, field: str) -> int:
    columns = config.inputs[i].sheets[j].columns
    return next(c for c, column in enumerate(columns) if column.field == field)


def test_an_upload_column_is_renamed_with_the_formulas_and_side_that_read_it() -> None:
    food = load_seed("food")
    c = _column(food, 0, 0, "order_id")
    renamed = rename(food, f"inputs[0].sheets[0].columns[{c}]", "partner_order_id")
    assert problems(renamed.config) == []
    assert renamed.config.inputs[0].sheets[0].columns[c].field == "partner_order_id"
    model = renamed.config.model
    assert model is not None
    # The portal side pairs on it; the vendor's own order_id is another column.
    assert [side.key for side in model.sides] == ["partner_order_id", "order_id"]
    assert f"inputs[0].sheets[0].columns[{c}].field" in renamed.changed

    uploads = {
        "portal": Upload(role="portal", filename="p.xlsx", path=FOOD_PORTAL),
        "vendor": Upload(role="vendor", filename="v.xlsx", path=FOOD_VENDOR),
    }
    results = []
    for config in (food, renamed.config):
        with use_config(config):
            results.append(run(config, uploads, max_rows=1_000_000))
    before, after = results
    assert after.metrics == before.metrics
    assert [(e.key, e.verdict, e.codes) for e in after.entities] == [
        (e.key, e.verdict, e.codes) for e in before.entities
    ]


def test_a_column_cleaned_by_a_field_of_its_name_keeps_the_field() -> None:
    travel = load_seed("travel")
    c = _column(travel, 0, 0, "booking_id")
    renamed = rename(travel, f"inputs[0].sheets[0].columns[{c}]", "partner_order_id")
    assert problems(renamed.config) == []
    sheet = renamed.config.inputs[0].sheets[0]
    field = next(f for f in sheet.fields if f.key == "booking_id")
    # The field now cleans the renamed column, and the rest still read the field.
    assert "partner_order_id" in field.formula and "booking_id" not in field.formula
    model = renamed.config.model
    assert model is not None
    assert next(m for m in model.members if m.name == "portal").key == "booking_id"


def test_a_column_is_renamed_on_every_sheet_of_its_rows() -> None:
    food = load_seed("food")
    doc = food.model_dump(mode="json")
    twin = copy.deepcopy(doc["inputs"][0]["sheets"][0])
    twin["key"] = "portal_2"
    doc["inputs"][0]["sheets"].append(twin)
    config = CategoryConfig.model_validate(doc)
    c = _column(config, 0, 0, "order_date")
    renamed = rename(config, f"inputs[0].sheets[0].columns[{c}]", "ordered_on")
    assert [s.columns[c].field for s in renamed.config.inputs[0].sheets] == [
        "ordered_on",
        "ordered_on",
    ]
    assert not any(
        "order_date" in text
        for path, text in _texts(renamed.config).items()
        if path.startswith("inputs[0]")
    )


def test_a_wallet_column_keeps_its_name() -> None:
    travel = load_seed("travel")
    i = next(i for i, upload in enumerate(travel.inputs) if upload.kind == "wallet")
    with pytest.raises(
        RenameError,
        match="The wallet check reads the Wallet history's columns by these names",
    ):
        rename(travel, f"inputs[{i}].sheets[0].columns[0]", "other")


def test_a_reference_files_column_is_renamed_with_its_look_ups() -> None:
    travel = load_seed("travel")
    i = next(i for i, upload in enumerate(travel.inputs) if upload.kind == "reference")
    renamed = rename(travel, f"inputs[{i}].sheets[0].columns[0]", "order_ref")
    assert renamed.config.inputs[i].sheets[0].columns[0].field == "order_ref"
    orders = renamed.config.tab("orders")
    assert orders is not None
    assert {c.lookup.match for c in orders.lookup_columns if c.lookup} == {"order_ref"}
    assert problems(renamed.config) == []


@pytest.mark.parametrize("kind", ["wallet", "reference"])
def test_a_column_added_to_a_wallet_or_reference_list_is_renamed(kind: str) -> None:
    doc = load_seed("travel").model_dump(mode="json")
    i = next(i for i, upload in enumerate(doc["inputs"]) if upload["kind"] == kind)
    role = doc["inputs"][i]["role"]
    columns = doc["inputs"][i]["sheets"][0]["columns"]
    columns.append({"field": "extra", "headers": ["Extra"]})
    doc["model"]["metrics"].append(
        {"key": "probe", "formula": f'COUNTX("{role}", NOT(ISBLANK(extra)))'}
    )
    config = CategoryConfig.model_validate(doc)

    renamed = rename(
        config, f"inputs[{i}].sheets[0].columns[{len(columns) - 1}]", "other"
    )

    assert problems(renamed.config) == []
    assert renamed.config.inputs[i].sheets[0].columns[-1].field == "other"
    assert renamed.config.model.metrics[-1].formula == (  # type: ignore[union-attr]
        f'COUNTX("{role}", NOT(ISBLANK(other)))'
    )


def test_an_upload_column_cannot_take_a_name_its_sheet_has() -> None:
    travel = load_seed("travel")
    c = _column(travel, 0, 0, "order_time")
    for taken in ("sg_order_id", "row"):
        with pytest.raises(RenameError, match="already something called"):
            rename(travel, f"inputs[0].sheets[0].columns[{c}]", taken)
    # A field's name, where the field would then replace what a formula reads.
    c = _column(travel, 0, 0, "order_total")
    with pytest.raises(RenameError, match="would change what a formula reads"):
        rename(travel, f"inputs[0].sheets[0].columns[{c}]", "locality")
