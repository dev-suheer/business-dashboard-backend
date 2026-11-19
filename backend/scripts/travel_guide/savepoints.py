"""The walkthrough's save points, as the setups they save.

Each is built from a blank category the way the walkthrough builds it: the
editor's own starting points (a blank category, the model "Start how orders are
made" makes), then the approved setup's parts in the walkthrough's order. The
setup check must find nothing at each, and the last must be the approved setup
itself: then every value of it is somewhere in the walkthrough, and every save
point can be saved.
"""

import copy
from dataclasses import dataclass
from typing import Any

from app.config_model.check import Problem, problems
from app.config_model.models import CategoryConfig
from app.services.configs import blank


@dataclass(frozen=True)
class SavePoint:
    # Its number, and what the walkthrough says to write as what changed.
    number: int
    changed: str
    config: CategoryConfig


class GuideBroken(Exception):
    """A save point the setup check refuses, or a last one that is not the
    approved setup."""


def started(category: str) -> dict[str, Any]:
    """The model "Start how orders are made → Gather rows by a key" makes, as
    the editor makes it (``OrdersDialog.tsx`` ``Start``); its members are
    replaced in the same step."""
    return {
        "builder": "group",
        "sides": [],
        "members": [],
        "lookups": [],
        "row_fields": [],
        "groups": [],
        "fields": [],
        "context": [],
        "stops": [],
        "row_rules": [],
        "rules": [
            {
                "when": "TRUE",
                "verdict": "Matched",
                "exception": None,
                "checks": False,
                "clean": None,
                "broken": None,
                "note": None,
                "about": "Everything else: the order matches.",
            }
        ],
        "checks": [],
        "findings": [],
        "run_rules": [],
        "sort": ["key"],
        "wallet": None,
        "sets": [],
        "metrics": [],
        "history": {
            "counted": 'COUNTX("entities")',
            "total": 'COUNTX("entities")',
            "unit": '"orders"',
            "net_payable": "0",
            "vendor": "BLANK()",
            "period_start": "BLANK()",
            "period_end": "BLANK()",
            "report_name": f'"reconciliation-{category}.xlsx"',
        },
    }


def order_lists(seed: CategoryConfig) -> set[str]:
    """The lists made from orders (every order, or a list of them), not rows."""
    assert seed.model is not None
    found = {"entities"}
    for each in seed.model.sets:
        if each.over in found:
            found.add(each.key)
    return found


def build(seed: CategoryConfig) -> list[SavePoint]:
    """The save points, in order, each checked; raises GuideBroken if one is
    refused or the last is not ``seed``."""
    approved = seed.model_dump(mode="json")
    model = approved["model"]
    draft = blank(seed.category).model_dump(mode="json")
    points: list[SavePoint] = []

    def save(changed: str) -> None:
        config = CategoryConfig.model_validate(copy.deepcopy(draft))
        found = problems(config)
        if found:
            raise GuideBroken(_refused(len(points) + 1, changed, found))
        points.append(SavePoint(len(points) + 1, changed, config))

    # 4. Rates and tolerances.
    draft["parameters"] = approved["parameters"]
    save("Rates and tolerances")

    # 5. The uploads, the dropped rows' exception left for step 7.2.
    inputs = copy.deepcopy(approved["inputs"])
    for upload in inputs:
        for sheet in upload["sheets"]:
            if sheet["unique"] is not None:
                sheet["unique"]["exception"] = None
    draft["inputs"] = inputs
    save("Uploads")

    # 6. How orders are made: its members, and the lists of rows.
    by_orders = order_lists(seed)
    draft["model"] = started(seed.category)
    draft["model"]["members"] = model["members"]
    draft["model"]["sets"] = [s for s in model["sets"] if s["over"] not in by_orders]
    save("How orders are made")

    # 7. The exception list, then the dropped rows' exception.
    draft["exceptions"] = approved["exceptions"]
    draft["inputs"] = approved["inputs"]
    save("Exception list")

    # 8. Run values, stops, upload-row rules.
    for part in ("context", "stops", "row_rules"):
        draft["model"][part] = model[part]
    save("Run values, stops and upload-row rules")

    # 9. Order fields; the lists of orders; figures.
    draft["model"]["fields"] = model["fields"]
    save("Order fields")
    draft["model"]["sets"] = model["sets"]
    save("Lists of orders")
    draft["model"]["metrics"] = model["metrics"]
    save("Figures")

    # 10. Status rules and exceptions, and the order of the orders.
    for part in ("rules", "checks", "findings", "run_rules", "sort"):
        draft["model"][part] = model[part]
    save("Status rules and exceptions")

    # 11. The wallet check.
    draft["model"]["wallet"] = model["wallet"]
    save("Wallet check")

    # 12. Workbook settings.
    draft["model"]["history"] = model["history"]
    draft["output"]["texts"] = approved["output"]["texts"]
    draft["labels"] = approved["labels"]
    save("Workbook settings")

    # 13. The report: the Exceptions table's Status, the Orders and Dashboard
    # tabs before it; then the rest, and the Orders look-up columns, which look
    # in the Finance Report copy added with the rest.
    tabs = {tab["key"]: tab for tab in approved["output"]["tabs"]}
    first = [t["key"] for t in approved["output"]["tabs"]]
    first = first[: first.index("exceptions") + 1]
    draft["output"]["tabs"] = [_without_lookups(tabs[key]) for key in first]
    save("Orders and Dashboard tabs")
    draft["output"]["tabs"] = approved["output"]["tabs"]
    save("Report workbook")

    if points[-1].config != seed:
        raise GuideBroken(
            "The last save point is not the approved setup: the walkthrough "
            "leaves a part of it out"
        )
    return points


def _without_lookups(tab: dict[str, Any]) -> dict[str, Any]:
    """A tab before its look-up columns are added: none, and no part fills them."""
    tab = copy.deepcopy(tab)
    tab["lookup_columns"] = []
    for section in tab.get("sections", []):
        section["lookups"] = False
    return tab


def _refused(number: int, changed: str, found: list[Problem]) -> str:
    listed = "\n".join(f"  {p.path}: {p.message}" for p in found)
    return f"Save point {number} ({changed}) is refused by the setup check:\n{listed}"
