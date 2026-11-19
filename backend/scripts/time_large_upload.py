"""Time one run on a generated portal export of the size asked for.

The food fixtures are copied over and over, each copy under new order ids, into a
portal export of the size asked for and a vendor report with the same orders. The
run goes through the same service as POST /runs, on the test database
(TEST_DATABASE_URL) and a throwaway file store, and is removed afterwards.

    uv run python scripts/time_large_upload.py             # 20 MB, about five minutes
    uv run python scripts/time_large_upload.py --mb 195    # at the 200 MB limit: over an hour
"""

import argparse
import resource
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import xlsxwriter
from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_engine
from app.recon.inputs import Upload
from app.services import retention, storage
from app.services.engines import RunOptions
from app.services.runs import execute_run

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "food"
# Ids a copy adds to every order id of the one before, so each copy is new orders.
STRIDE = 10_000_000
PORTAL_ID = "Partner Order ID"
VENDOR_ID = "order_id"


def _rows(path: Path, key: str) -> tuple[list[Any], list[list[Any]]]:
    """A fixture's header row (the first naming ``key``) and its order rows (those
    with an order id under it)."""
    sheet = load_workbook(path, read_only=True).worksheets[0]
    rows = [list(r) for r in sheet.iter_rows(values_only=True)]
    start = next(i for i, row in enumerate(rows) if key in row)
    header = rows[start]
    at = header.index(key)
    return header, [r for r in rows[start + 1 :] if isinstance(r[at], int)]


def _write(
    path: Path, header: list[Any], rows: list[list[Any]], key: str, copies: int
) -> None:
    at = header.index(key)
    book = xlsxwriter.Workbook(str(path), {"constant_memory": True})
    sheet = book.add_worksheet(path.stem.upper().replace("_", " "))
    dates = book.add_format({"num_format": "yyyy-mm-dd hh:mm"})
    sheet.write_row(0, 0, header)
    line = 1
    for copy in range(copies):
        for row in rows:
            for col, value in enumerate(row):
                if col == at and isinstance(value, int):
                    value += copy * STRIDE
                if value is None:
                    continue
                if hasattr(value, "year"):
                    sheet.write_datetime(line, col, value, dates)
                else:
                    sheet.write(line, col, value)
            line += 1
    book.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mb", type=float, default=20, help="Portal export size to aim for."
    )
    args = parser.parse_args()
    if not settings.TEST_DATABASE_URL:
        print("TEST_DATABASE_URL is not set; this only runs on the test database.")
        return 1

    portal_header, portal = _rows(FIXTURES / "portal.xlsx", PORTAL_ID)
    vendor_header, vendor = _rows(FIXTURES / "vendor.xlsx", VENDOR_ID)

    with tempfile.TemporaryDirectory(prefix="recon-timing-") as scratch:
        work = Path(scratch)
        # How big one copy is, from a sample of copies.
        sample = work / "sample.xlsx"
        _write(sample, portal_header, portal, PORTAL_ID, 200)
        per_copy = sample.stat().st_size / 200
        copies = max(1, round(args.mb * 1e6 / per_copy))
        copies = min(copies, settings.MAX_ROWS_PER_SHEET // len(portal))

        clock = time.perf_counter()
        _write(work / "portal.xlsx", portal_header, portal, PORTAL_ID, copies)
        _write(work / "vendor.xlsx", vendor_header, vendor, VENDOR_ID, copies)
        made = time.perf_counter() - clock
        sizes = {
            p: (work / f"{p}.xlsx").stat().st_size / 1e6 for p in ("portal", "vendor")
        }
        print(
            f"Portal export {sizes['portal']:.1f} MB, {copies * len(portal):,} orders; "
            f"vendor report {sizes['vendor']:.1f} MB, {copies * len(vendor):,} rows "
            f"(written in {made:.0f} s)"
        )

        settings.RUN_STORAGE_DIR = work / "store"
        uploads = {
            role: Upload(role=role, filename=f"{role}.xlsx", path=work / f"{role}.xlsx")
            for role in ("portal", "vendor")
        }
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        with Session(
            get_engine(settings.TEST_DATABASE_URL), expire_on_commit=False
        ) as session:
            clock = time.perf_counter()
            run = execute_run(session, "food", uploads, RunOptions())
            took = time.perf_counter() - clock
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            report = retention.stored_bytes(run)
            print(
                f"Run {run.status} in {took:.0f} s ({run.duration_ms / 1000:.0f} s reconciling and writing the report)"
            )
            if run.error:
                print(f"  {run.error}")
            if run.summary:
                print(
                    f"  {run.summary['counted']:,} orders counted, {run.summary['not_counted']:,} not"
                )
            print(
                f"  peak memory {peak / 1e6:.1f} GB (before the run: {before / 1e6:.1f} GB)"
            )
            print(f"  kept on disk: {report / 1e6:.0f} MB")
            if run.report_path:
                # The report opens, and how big its largest sheet is unzipped.
                path = storage.resolve(run.report_path)
                book = load_workbook(path, read_only=True)
                print(
                    f"  report {path.stat().st_size / 1e6:.0f} MB, "
                    f"tabs: {', '.join(book.sheetnames)}"
                )
                book.close()
                with zipfile.ZipFile(path) as archive:
                    largest = max(archive.infolist(), key=lambda i: i.file_size)
                print(f"  largest sheet {largest.file_size / 1e9:.1f} GB unzipped")
            retention.remove(session, [run])
    return 0


if __name__ == "__main__":
    sys.exit(main())
