"""The Travel rebuild guide (``scripts/travel_guide``) holds the approved setup.

Its save points are built the way the walkthrough builds Travel: each must pass
the setup check and the last must be the approved setup itself. The guide's
text is made without a browser; the screenshots and the PDF are not.
"""

import re
from datetime import date
from html import unescape

import pytest

from app.config_model.models import CategoryConfig
from app.config_model.seed import load_seed
from scripts.travel_guide import savepoints, screens
from scripts.travel_guide.reference import Reference, letter
from scripts.travel_guide.render import document
from scripts.travel_guide.savepoints import GuideBroken, build
from scripts.travel_guide.tabs import added
from scripts.travel_guide.walkthrough import Walkthrough


@pytest.fixture(scope="module")
def seed() -> CategoryConfig:
    return load_seed("travel")


@pytest.fixture(scope="module")
def html(seed: CategoryConfig) -> str:
    return document(seed, build(seed), {}, date(2026, 11, 16))


def test_every_save_point_passes_and_the_last_is_the_approved_setup(
    seed: CategoryConfig,
) -> None:
    points = build(seed)
    assert [p.number for p in points] == list(range(1, 14))
    assert points[-1].config == seed
    # Each adds something: no two save points save the same setup.
    dumps = [p.config.model_dump_json() for p in points]
    assert len(set(dumps)) == len(dumps)


def test_a_part_the_walkthrough_never_adds_stops_the_guide(
    seed: CategoryConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The walkthrough gathers rows by a key; a setup that pairs them is never
    # reached, whatever the check says of each save point.
    monkeypatch.setattr(savepoints, "problems", lambda config: [])
    assert seed.model is not None
    paired = seed.model_copy(
        update={"model": seed.model.model_copy(update={"builder": "pair"})}
    )
    with pytest.raises(GuideBroken, match="leaves a part of it out"):
        build(paired)


def test_a_save_point_the_check_refuses_stops_the_guide(seed: CategoryConfig) -> None:
    broken = seed.model_dump(mode="json")
    broken["model"]["fields"][0]["formula"] = "no_such_name + 1"
    with pytest.raises(GuideBroken, match="Save point 6"):
        build(CategoryConfig.model_validate(broken))


def test_each_tab_is_added_the_way_the_editor_adds_it(seed: CategoryConfig) -> None:
    ways = {a.tab.key: a for a in added(seed)}
    assert [(a.kind, a.start) for a in ways.values()] == [
        ("Dashboard", None),
        ("Table", "Empty"),
        ("Table", "Exceptions"),
        ("Table", "Wallet check"),
        ("Table", "ERP invoices"),
        ("Table", "Copy of an upload"),
        ("Table", "Copy of an upload"),
        ("Table", "Copy of an upload"),
    ]
    assert ways["exceptions"].present
    erp = {(c.where, c.setting): c.value for c in ways["b2c_invoices"].changes or []}
    assert erp[("Invoice lines/Unit Price column", "Excel formula")] == (
        "={cell:orders.commission_charged}/1.15"
    )
    assert erp[("Sheet layout, part 1", "on this row's own upload row")] == "portal"
    assert [c.value for c in ways["portal_copy"].changes or []] == ["Portal band"]
    assert ways["wallet"].changes == []
    # The finance report is copied like any upload; only its header row changes.
    assert [(c.setting, c.value) for c in ways["finance_copy"].changes or []] == [
        ("Header row", "Cashback band")
    ]


def test_part_b_holds_every_value(seed: CategoryConfig, html: str) -> None:
    tables = Reference(seed).all()
    assert [n for n, _, _ in tables] == list(range(1, 30))
    assert seed.model is not None
    text = unescape(html)
    formulas = [x.formula for x in seed.model.fields + seed.model.metrics]
    formulas += [x.formula for x in seed.model.context]
    formulas += [r.when for r in seed.model.rules + seed.model.findings]
    formulas += [r.detail for r in seed.model.findings + seed.model.row_rules]
    # A tab typed by hand, every column; one from a standard sheet, what changes.
    for way in added(seed):
        if way.changes is None:
            formulas += [c.value for c in way.tab.columns if c.value]
        else:
            formulas += [c.value for c in way.changes]
    for formula in formulas:
        assert formula in text, formula
    for exception in seed.exceptions:
        assert exception.code in text


def test_part_a_is_todays_editor(seed: CategoryConfig, html: str) -> None:
    # Gone from the editor: the run period, On its order, the invoice lines
    # dialog, the Exceptions setting and the old tab kinds.
    text = unescape(html)
    for gone in (
        "period.start",
        "period.end",
        "On its order",
        "Invoice lines…",
        "Set up the invoice lines",
        "The Exceptions sheet's Status column",
        "What it is (in + Tab)",
        # Gone too: template files, and the finance report's own lookups.
        "Add template",
        "Template:",
        "Optional template",
        "Finance lookups find a row by",
        "The finance report's lookups",
        # Nothing ready-made of one file: no finance shortcuts.
        "The finance report's 5 columns",
        "Start from Finance report",
        "added whole by + Add an upload",
    ):
        assert gone not in text, gone
    walkthrough = Walkthrough(seed, build(seed), {})
    walkthrough.html()
    assert sorted(walkthrough.figures) == sorted(screens.KEYS)
    assert len(re.findall("SAVE POINT", text)) == 13


def test_column_letters() -> None:
    assert [letter(i) for i in (0, 25, 26, 51, 52)] == ["A", "Z", "AA", "AZ", "BA"]
