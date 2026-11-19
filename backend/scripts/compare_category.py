"""Compare a category's setup with another, place by place.

    uv run python -m scripts.compare_category travel
    uv run python -m scripts.compare_category travel travel_approved

The first compares the category's current version with its approved version 1,
``app/seed/v1/travel.json``; the second with another category's current
version, archived or not. Each difference is listed by where it is in the
setup, with what was expected and what was found. Fields, figures, columns,
tabs and the like are matched by their names, so one added in the wrong place
shows as out of order rather than as every one after it differing.

It reads the database in DATABASE_URL and never writes to it.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.config_model.models import CategoryConfig
from app.config_model.seed import seed_path
from app.db.session import get_engine
from app.services import configs

# What names an item of a list, in the order tried: a field by its key, an
# exception by its code, an upload by its role, an upload's column by its field.
_NAMES = ("key", "code", "role", "field")
# Lists whose items have a key that is not their name: an order member's key is
# the formula that joins its rows, a pairing side's the field it pairs on.
_NAMED_AT = {"model.members": "name", "model.groups": "name", "model.sides": "role"}
_MISSING = object()
_WIDTH = 110


@dataclass(frozen=True)
class Difference:
    path: str
    expected: Any
    found: Any


def differences(expected: Any, found: Any, path: str = "") -> list[Difference]:
    """Every place ``found`` differs from ``expected``."""
    if isinstance(expected, dict) and isinstance(found, dict):
        out: list[Difference] = []
        for key in [*expected, *(k for k in found if k not in expected)]:
            here = f"{path}.{key}" if path else str(key)
            out += differences(
                expected.get(key, _MISSING), found.get(key, _MISSING), here
            )
        return out
    if isinstance(expected, list) and isinstance(found, list):
        by = (_NAMED_AT[path],) if path in _NAMED_AT else _NAMES
        name = _named_by(expected, found, by=by)
        if name is not None:
            return _named(expected, found, path, name)
        out = []
        for n in range(max(len(expected), len(found))):
            out += differences(
                expected[n] if n < len(expected) else _MISSING,
                found[n] if n < len(found) else _MISSING,
                f"{path}[{n}]",
            )
        return out
    return [] if expected == found else [Difference(path, expected, found)]


def _named_by(*lists: list[Any], by: tuple[str, ...] = _NAMES) -> str | None:
    """The attribute that names every item of the lists, each once."""
    items = [item for items in lists for item in items]
    if not items or not all(isinstance(item, dict) for item in items):
        return None
    for name in by:
        if all(isinstance(item.get(name), str) for item in items) and all(
            len({item[name] for item in items}) == len(items) for items in lists
        ):
            return name
    return None


def _named(
    expected: list[dict[str, Any]], found: list[dict[str, Any]], path: str, name: str
) -> list[Difference]:
    before = {item[name]: item for item in expected}
    after = {item[name]: item for item in found}
    out: list[Difference] = []
    for key in [*before, *(k for k in after if k not in before)]:
        out += differences(
            before.get(key, _MISSING), after.get(key, _MISSING), f"{path}[{key}]"
        )
    order = [key for key in before if key in after]
    if order != [key for key in after if key in before]:
        out.append(
            Difference(
                f"{path} (order)", order, [key for key in after if key in before]
            )
        )
    return out


def _setup(session: Session, key: str) -> dict[str, Any]:
    found = configs.category(session, key)
    if found.current_version is None:
        raise configs.ConfigurationError(f"Category {key!r} has no setup yet")
    return _plain(configs.config_of(found.current_version))


def _plain(config: CategoryConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def _shown(value: Any) -> str:
    if value is _MISSING:
        return "(not there)"
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= _WIDTH else f"{text[: _WIDTH - 1]}…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("key", help="the category to check, e.g. travel")
    parser.add_argument(
        "against",
        nargs="?",
        help="another category to compare with; its approved version 1 when left out",
    )
    args = parser.parse_args(argv)
    with Session(get_engine()) as session:
        try:
            found = _setup(session, args.key)
            if args.against:
                expected = _setup(session, args.against)
                source = f"the {args.against} category"
            else:
                path = seed_path(args.key)
                if not path.exists():
                    print(
                        f"There is no approved version 1 of {args.key!r}: name a category to compare with"
                    )
                    return 2
                expected = _plain(CategoryConfig.model_validate_json(path.read_text()))
                source = str(path.relative_to(path.parents[3]))
        except configs.ConfigurationError as exc:
            print(exc)
            return 2
        finally:
            session.rollback()
    # The category's own key is the one thing two categories never share.
    expected.pop("category", None)
    found.pop("category", None)
    found_differences = differences(expected, found)
    if not found_differences:
        print(f"{args.key} is identical to {source}.")
        return 0
    count = len(found_differences)
    print(
        f"{args.key} differs from {source} in {count} place{'s' if count > 1 else ''}:\n"
    )
    for difference in found_differences:
        print(difference.path)
        print(f"    expected: {_shown(difference.expected)}")
        print(f"    found:    {_shown(difference.found)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
