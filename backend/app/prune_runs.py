"""Remove runs older than the retention limit: their History rows, uploads and
reports. Without --apply it only lists them.

uv run python app/prune_runs.py               # what RUN_RETENTION_DAYS would remove
uv run python app/prune_runs.py --apply       # remove them
uv run python app/prune_runs.py --days 365    # another limit, for this once
"""

import argparse

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_engine
from app.services import retention


def _days(value: str) -> int:
    days = int(value)
    if days < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return days


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=_days,
        default=settings.RUN_RETENTION_DAYS,
        help="Remove runs started more than this many days ago (default: RUN_RETENTION_DAYS).",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Remove them; otherwise only list them."
    )
    args = parser.parse_args(argv)
    if args.days is None:
        print("Retention is off: RUN_RETENTION_DAYS is not set, so every run is kept.")
        return 0

    with Session(get_engine(settings.DATABASE_URL), expire_on_commit=False) as session:
        runs = retention.expired(session, args.days)
        if not runs:
            print(f"No run is older than {args.days} days.")
            return 0
        size = 0
        for run in runs:
            kept = retention.stored_bytes(run)
            size += kept
            print(
                f"Run {run.id:04d}  {run.category.name:<12} "
                f"{run.started_at:%Y-%m-%d}  {run.status:<9} {kept / 1e6:8.1f} MB"
            )
        summary = f"{len(runs)} run(s) older than {args.days} days, {size / 1e6:.1f} MB"
        if not args.apply:
            print(f"{summary}. Nothing removed: run again with --apply to remove them.")
            return 0
        retention.remove(session, runs)
        print(f"Removed {summary}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
