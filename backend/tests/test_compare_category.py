"""Comparing a rebuilt setup with the approved one: each difference is found
once, where it is, and a thing added out of place shows as out of order."""

from typing import Any

from app.config_model.seed import load_seed
from scripts.compare_category import Difference, differences


def _travel() -> dict[str, Any]:
    return load_seed("travel").model_dump(mode="json")


def test_the_same_setup_has_no_differences() -> None:
    assert differences(_travel(), _travel()) == []


def test_a_changed_formula_is_named_by_its_field() -> None:
    found = _travel()
    field = found["model"]["fields"][3]
    field["formula"] = "1"
    expected = _travel()["model"]["fields"][3]
    assert differences(_travel(), found) == [
        Difference(f"model.fields[{field['key']}].formula", expected["formula"], "1")
    ]


def test_a_field_out_of_place_is_out_of_order_not_every_one_after_it() -> None:
    found = _travel()
    fields = found["model"]["fields"]
    fields.insert(0, fields.pop(5))
    (difference,) = differences(_travel(), found)
    assert difference.path == "model.fields (order)"
    assert difference.found[0] == fields[0]["key"]


def test_a_missing_column_and_an_extra_one_are_both_listed() -> None:
    found = _travel()
    columns = found["inputs"][0]["sheets"][0]["columns"]
    gone = columns.pop(2)
    columns.append({**gone, "field": "extra"})
    paths = [d.path for d in differences(_travel(), found)]
    assert paths == [
        f"inputs[portal].sheets[portal].columns[{gone['field']}]",
        "inputs[portal].sheets[portal].columns[extra]",
    ]


def test_rules_without_names_are_compared_in_place() -> None:
    found = _travel()
    found["model"]["rules"][1]["verdict"] = "Other"
    expected = _travel()["model"]["rules"][1]["verdict"]
    assert differences(_travel(), found) == [
        Difference("model.rules[1].verdict", expected, "Other")
    ]


def test_a_member_is_named_by_its_name_not_its_key() -> None:
    found = _travel()
    portal = next(m for m in found["model"]["members"] if m["name"] == "portal")
    portal["key"] = "sg_order_id"
    assert differences(_travel(), found) == [
        Difference("model.members[portal].key", "booking_id", "sg_order_id")
    ]
