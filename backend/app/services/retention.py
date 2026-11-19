"""Retention: runs older than a limit are removed whole, their History row with
their uploads, report and result.

Nothing here runs on its own. ``app/prune_runs.py`` calls it, and removes
anything only when told to.
"""

import shutil
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Run
from app.services import storage


def expired(session: Session, days: int, now: datetime | None = None) -> list[Run]:
    """The runs started more than ``days`` days ago, oldest first."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    return list(
        session.scalars(select(Run).where(Run.started_at < cutoff).order_by(Run.id))
    )


def stored_bytes(run: Run) -> int:
    """What a run takes on disk."""
    directory = storage.run_dir(run.id)
    if not directory.exists():
        return 0
    return sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())


def remove(session: Session, runs: list[Run]) -> None:
    """Delete the runs and then their folders.

    The rows go first: a folder left behind by a failed delete is only disk
    space, while a row whose files were already gone would show a run in
    History whose downloads fail.
    """
    folders = [storage.run_dir(run.id) for run in runs]
    for run in runs:
        session.delete(run)
    session.commit()
    for folder in folders:
        if folder.exists():
            shutil.rmtree(folder)
