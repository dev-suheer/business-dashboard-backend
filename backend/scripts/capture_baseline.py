"""Capture the parity baseline from the code as it is today.

Run once, before v2 changes anything, from the backend folder:

    uv run python -m scripts.capture_baseline

Each scenario in ``tests/parity/scenarios.py`` writes its result as JSON and its
report workbook under ``tests/baseline/<scenario>/``. Those files are the
approved behaviour every later phase is checked against, so re-running this
after the rules have moved would only bless whatever the new code does. It
refuses to overwrite an existing baseline unless told to.
"""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from tests.parity.scenarios import BASELINE, SCENARIOS, produce, write_finance_inputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing baseline (only while today's code is still in place)",
    )
    args = parser.parse_args()

    if any(BASELINE.glob("*/result.json")) and not args.overwrite:
        print(
            f"A baseline already exists in {BASELINE}; pass --overwrite to replace it."
        )
        return 1

    write_finance_inputs()
    for scenario in SCENARIOS:
        target = BASELINE / scenario.name
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            result, report = produce(scenario, Path(tmp))
            shutil.copyfile(report, target / "report.xlsx")
        (target / "result.json").write_text(
            json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        )
        print(f"captured {scenario.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
