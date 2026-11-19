"""How each of the report's tabs is added in the editor, and what is changed after.

A tab is added by "+ Tab": a Dashboard, or a Table that starts empty or from
one of the standard sheets (``GET /standard``). This makes each tab as the
editor's Add-tab dialog makes it (``Dialogs.tsx`` ``AddTabDialog``), then
lists what differs from the approved tab: the settings to change after adding
it. A difference the editor has no place for stops the guide.
"""

from dataclasses import dataclass, field
from typing import Any

from app.api.routes.categories import read_standard
from app.config_model.models import CategoryConfig, OutputTab
from scripts.travel_guide.savepoints import GuideBroken


@dataclass(frozen=True)
class Change:
    # Where it is set ("Partner column", "Sheet layout, part 1", "Tab settings"),
    # the setting's label, and its value as it is typed.
    where: str
    setting: str
    value: str
    formula: bool = True


@dataclass(frozen=True)
class Added:
    tab: OutputTab
    # "Table" or "Dashboard", and for a table what it starts from.
    kind: str
    start: str | None
    # The Add-tab dialog's other choices, by their label.
    choices: list[tuple[str, str]] = field(default_factory=list)
    # What is changed once it is added; None when everything is typed by hand.
    changes: list[Change] | None = None
    # Already there in a new category.
    present: bool = False


# A column's settings, by the label of the column's panel, as the guide lists them.
_COLUMN = {
    "value": "Formula (in the bar)",
    "excel": "Excel formula",
    "style": "Style",
    "header": "Header",
    "width": "Width",
    "kind": "Shown as",
    "group": "Band",
    "color": "Header colour",
}
_COPY_HEADERS = {
    "header": "Plain",
    "portal_header": "Portal band",
    "check_header": "Check band",
    "vendor_header": "Vendor band",
    "meta_header": "Meta band",
    "cashback_header": "Cashback band",
}


def written(config: CategoryConfig, tab: OutputTab) -> str:
    if tab.requires is None:
        return "Always"
    title = next(f.title for f in config.inputs if f.role == tab.requires)
    return f"Only when {title} is uploaded"


def added(seed: CategoryConfig) -> list[Added]:
    """Each of the approved report's tabs, in order, as it is added."""
    standard = read_standard().tabs
    tabs = seed.output.tabs
    found = []
    for i, tab in enumerate(tabs):
        before = tabs[:i]
        found.append(_added(seed, tab, before, standard))
    return found


def _added(
    seed: CategoryConfig,
    tab: OutputTab,
    before: list[OutputTab],
    standard: dict[str, OutputTab],
) -> Added:
    def renamed(template: OutputTab, **update: Any) -> OutputTab:
        return template.model_copy(
            deep=True, update={"key": tab.key, "title": tab.title, **update}
        )

    if tab.kind == "dashboard":
        return Added(tab, "Dashboard", None)
    if tab.source is not None:
        upload = next(f for f in seed.inputs if f.role == tab.source)
        sheet = next(s for s in upload.sheets if s.key == tab.sheet)
        made = renamed(
            standard["copy"],
            source=upload.role,
            sheet=sheet.key,
            header_format="header",
            # A copy of a file to look things up in is written when it came.
            requires=upload.role if upload.kind == "reference" else None,
        )
        choices = [("Upload", upload.title), ("Its sheet", sheet.name)]
        return Added(
            tab, "Table", "Copy of an upload", choices, _changes(made, tab, seed)
        )
    lists = [s.set for s in tab.sections if s.kind == "rows" and s.set]
    if lists[:1] == ["exceptions"]:
        made = renamed(standard["exceptions"])
        return Added(
            tab, "Table", "Exceptions", [], _changes(made, tab, seed), present=True
        )
    if lists[:1] == ["wallet_checks"]:
        made = renamed(standard["wallet"])
        return Added(tab, "Table", "Wallet check", [], _changes(made, tab, seed))
    unit = next((c for c in tab.columns if c.key == "unit_price"), None)
    if unit is not None and unit.excel is not None:
        billed = lists[0]
        made = renamed(standard["erp_invoices"])
        made.sections[0] = made.sections[0].model_copy(update={"set": billed})
        lister = next(
            t
            for t in before
            if t.kind == "table"
            and any(s.kind == "rows" and s.set == billed for s in t.sections)
        )
        made.columns[:] = [
            c.model_copy(
                update={"excel": f"={{cell:{lister.key}.{lister.columns[0].key}}}/1.15"}
            )
            if c.key == "unit_price"
            else c
            for c in made.columns
        ]
        choices = [("The rows it bills", billed)]
        return Added(tab, "Table", "ERP invoices", choices, _changes(made, tab, seed))
    return Added(tab, "Table", "Empty", [("The rows it lists", lists[0])])


def _changes(
    made: OutputTab, approved: OutputTab, seed: CategoryConfig
) -> list[Change]:
    """What to change on ``made`` for it to be ``approved``."""
    changes: list[Change] = []
    was = made.model_dump(mode="json")
    now = approved.model_dump(mode="json")
    for name in sorted(set(was) | set(now)):
        if was[name] == now[name]:
            continue
        if name == "header_format":
            changes.append(
                Change(
                    "Tab settings",
                    "Header row",
                    _COPY_HEADERS[now[name]],
                    formula=False,
                )
            )
        elif name == "columns":
            changes += _column_changes(was[name], now[name], approved.key)
        elif name == "sections":
            titles = {f.role: f.title for f in seed.inputs}
            changes += _section_changes(was[name], now[name], approved.key, titles)
        else:
            raise GuideBroken(f"The {approved.title} tab's {name} has no place here")
    return changes


def _column_changes(
    was: list[dict[str, Any]], now: list[dict[str, Any]], tab: str
) -> list[Change]:
    if [c["key"] for c in was] != [c["key"] for c in now]:
        raise GuideBroken(f"The {tab} tab's columns are not the template's")
    changes = []
    for old, new in zip(was, now, strict=True):
        for name in old:
            if old[name] == new[name]:
                continue
            if name not in _COLUMN:
                raise GuideBroken(f"The {tab} tab's {new['key']} {name} has no place")
            changes.append(
                Change(
                    f"{new['header']} column",
                    _COLUMN[name],
                    "" if new[name] is None else str(new[name]),
                    formula=name in ("value", "excel", "style"),
                )
            )
    return changes


def _section_changes(
    was: list[dict[str, Any]],
    now: list[dict[str, Any]],
    tab: str,
    titles: dict[str, str],
) -> list[Change]:
    if len(was) != len(now):
        raise GuideBroken(f"The {tab} tab's parts are not the template's")
    changes = []
    for n, (old, new) in enumerate(zip(was, now, strict=True), 1):
        for name in old:
            if old[name] == new[name]:
                continue
            if name != "contacts":
                raise GuideBroken(f"The {tab} tab's part {n} {name} has no place here")
            changes += _contacts(n, new[name], titles)
    return changes


def _contacts(n: int, contacts: dict[str, Any], titles: dict[str, str]) -> list[Change]:
    """The rows part's Contact names: the upload, then where its row is found."""
    where = f"Sheet layout, part {n}"
    upload = f"{titles[contacts['upload']]}, its second Customer column"
    found = [Change(where, "Contact names", upload, formula=False)]
    if contacts["key"] is None:
        found.append(Change(where, "on this row's own upload row", contacts["row"]))
    else:
        found.append(
            Change(where, "on the upload row whose header", contacts["key"], False)
        )
        found.append(Change(where, "holds this row's key", contacts["match"]))
    return found
