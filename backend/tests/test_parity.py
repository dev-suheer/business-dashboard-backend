"""v2 must reproduce the approved results exactly.

Every scenario is run with the current code and compared with the baseline
captured from the code the client approved: the result field by field, and
the report cell by cell.
"""

from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.styles import Border, Side

from tests.parity.compare import json_differences, workbook_differences
from tests.parity.scenarios import BASELINE, SCENARIOS, Scenario, expected, produce


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario_matches_the_baseline(scenario: Scenario, tmp_path: Path) -> None:
    folder = BASELINE / scenario.name
    assert (folder / "result.json").exists(), (
        f"no baseline for {scenario.name}; run scripts/capture_baseline.py"
    )

    result, report = produce(scenario, tmp_path)

    assert json_differences(result, expected(scenario)) == []
    assert workbook_differences(report, folder / "report.xlsx") == []


def test_the_comparison_sees_a_changed_cell_and_format(tmp_path: Path) -> None:
    """A parity suite that cannot fail proves nothing."""
    original = BASELINE / "travel-claim-13" / "report.xlsx"
    book = load_workbook(original)
    orders = book["Orders"]
    orders["B2"] = "changed"
    orders["C2"].number_format = "0.000"
    orders["D2"].border = Border(left=Side(style="thick"))
    changed = tmp_path / "changed.xlsx"
    book.save(changed)

    found = workbook_differences(changed, original)
    assert any(d.startswith("Orders!B2 value") for d in found)
    assert any(d.startswith("Orders!C2 number_format") for d in found)
    assert any(d.startswith("Orders!D2 borders") for d in found)


def test_the_comparison_sees_a_changed_figure() -> None:
    assert json_differences({"a": [1, {"b": "2.00"}]}, {"a": [1, {"b": "2.01"}]}) == [
        "$.a[1].b: '2.00' != '2.01'"
    ]
