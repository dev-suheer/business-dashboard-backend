"""Reading and saving a category's configuration, and archiving or deleting one.

A configuration is never edited in place: saving one adds a version, and that
version is the one runs use from then on. Every run records the version it
reconciled under, so History can always say which setup produced a report.

An archived category keeps everything but takes no runs; only an archived one
can be deleted, and deleting it removes its versions and runs.
"""

import re
import shutil
from dataclasses import dataclass

from sqlalchemy import delete as delete_rows
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config_model.check import Problem, problems
from app.config_model.models import CategoryConfig, OutputWorkbook
from app.config_model.standard_tabs import exceptions_tab
from app.db.models import Category, CategoryVersion, Run
from app.services import storage


class ConfigurationError(LookupError):
    """A category has no configuration a run can use."""


class ConfigConflict(ValueError):
    """The configuration was saved by someone else since it was opened."""


class ConfigInvalid(ValueError):
    """The configuration cannot be saved as it is."""

    def __init__(self, found: list[Problem]) -> None:
        super().__init__(f"{len(found)} problem(s) in the setup")
        self.problems = found


KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


def config_of(version: CategoryVersion) -> CategoryConfig:
    return CategoryConfig.model_validate(version.config)


def category(session: Session, key: str) -> Category:
    found = session.scalar(select(Category).where(Category.key == key))
    if found is None:
        raise ConfigurationError(f"Unknown category {key!r}")
    return found


def current(
    session: Session, key: str
) -> tuple[Category, CategoryVersion, CategoryConfig]:
    """An active category, the version runs use now, and that configuration."""
    found = category(session, key)
    if not found.active:
        raise ConfigurationError(f"{found.name} is archived: restore it to run it")
    version = found.current_version
    if version is None:
        raise ConfigurationError(f"Category {key!r} has no configuration yet")
    return found, version, config_of(version)


def versions(session: Session, found: Category) -> list[CategoryVersion]:
    """Every saved version, newest first."""
    return list(
        session.scalars(
            select(CategoryVersion)
            .where(CategoryVersion.category_id == found.id)
            .order_by(CategoryVersion.number.desc())
        )
    )


def version(session: Session, found: Category, number: int) -> CategoryVersion:
    saved = session.scalar(
        select(CategoryVersion).where(
            CategoryVersion.category_id == found.id, CategoryVersion.number == number
        )
    )
    if saved is None:
        raise ConfigurationError(f"{found.key} has no version {number}")
    return saved


def _add_version(
    session: Session, found: Category, config: CategoryConfig, note: str
) -> CategoryVersion:
    latest = session.scalar(
        select(func.max(CategoryVersion.number)).where(
            CategoryVersion.category_id == found.id
        )
    )
    saved = CategoryVersion(
        category_id=found.id,
        number=(latest or 0) + 1,
        config=config.model_dump(mode="json"),
        note=note,
    )
    session.add(saved)
    session.flush()
    found.current_version_id = saved.id
    found.current_version = saved
    return saved


def save(
    session: Session, key: str, config: CategoryConfig, note: str, base_version: int
) -> CategoryVersion:
    """Save ``config`` as the category's next version, which runs use from now.

    ``base_version`` is the version the editor opened: if another has been
    saved since, nothing is saved, rather than silently undo that one.
    """
    found = category(session, key)
    if config.category != key:
        raise ConfigInvalid([Problem("category", f"This setup is for {key!r}")])
    live = found.current_version
    if live is not None and live.number != base_version:
        raise ConfigConflict(
            f"Version {live.number} was saved after version {base_version} was opened"
        )
    found_problems = problems(config)
    if found_problems:
        raise ConfigInvalid(found_problems)
    saved = _add_version(session, found, config, note)
    session.commit()
    return saved


def blank(key: str) -> CategoryConfig:
    """A category with nothing in it yet: no uploads, no rules, and a report
    that is only the exceptions list."""
    return CategoryConfig(
        category=key,
        engine="generic",
        inputs=[],
        output=OutputWorkbook(
            tabs=[exceptions_tab("exceptions", "Exceptions", status='""')]
        ),
    )


def create(
    session: Session,
    key: str,
    name: str,
    description: str = "",
    copy_of: str | None = None,
) -> Category:
    """A new category, at version 1: blank, or a copy of another's current
    setup."""
    if not KEY.match(key):
        raise ConfigInvalid(
            [
                Problem(
                    "key",
                    "Use 2 to 40 lowercase letters, digits or _, starting with a letter",
                )
            ]
        )
    if session.scalar(select(Category).where(Category.key == key)) is not None:
        raise ConfigInvalid([Problem("key", f"A category is already called {key!r}")])
    source: Category | None = None
    if copy_of is None:
        config = blank(key)
    else:
        source, _version, original = current(session, copy_of)
        config = original.model_copy(update={"category": key})
    found = Category(key=key, name=name, description=description)
    session.add(found)
    session.flush()
    note = f"Copy of {source.name}" if source is not None else "New category"
    _add_version(session, found, config, note)
    session.commit()
    return found


def archive(session: Session, key: str) -> Category:
    """Hide a category from runs and the categories list, keeping everything."""
    found = category(session, key)
    found.active = False
    session.commit()
    return found


def restore(session: Session, key: str) -> Category:
    found = category(session, key)
    found.active = True
    session.commit()
    return found


@dataclass(frozen=True)
class Deleted:
    versions: int
    runs: int


def delete(session: Session, key: str) -> Deleted:
    """Delete an archived category for good: its versions and every run of it,
    with their uploads and reports.

    The rows go first, in one transaction, and the folders after, as
    ``retention.remove`` does: a folder left behind is only disk space, while a
    row whose files were gone would show a run whose downloads fail.
    """
    found = category(session, key)
    if found.active:
        raise ConfigInvalid([Problem("key", "Archive the category before deleting it")])
    runs = list(session.scalars(select(Run).where(Run.category_id == found.id)))
    folders = [storage.run_dir(run.id) for run in runs]
    version_count = session.scalar(
        select(func.count()).where(CategoryVersion.category_id == found.id)
    )
    for run in runs:
        session.delete(run)
    session.flush()
    found.current_version_id = None
    found.current_version = None
    session.flush()
    session.execute(
        delete_rows(CategoryVersion).where(CategoryVersion.category_id == found.id)
    )
    session.delete(found)
    session.commit()
    for folder in folders:
        if folder.exists():
            shutil.rmtree(folder)
    return Deleted(versions=version_count or 0, runs=len(runs))
