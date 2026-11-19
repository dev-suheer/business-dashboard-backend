"""Make the guide.

    uv run --with playwright python -m scripts.travel_guide
    uv run --with playwright python -m scripts.travel_guide --keep-previous

It checks every save point first, and stops if one is refused. The
screenshots are taken from the editor served against the test database;
``--shots DIR`` uses ones taken before instead. The guide replaces
``travel-rebuild-guide.pdf`` beside the two repositories; with
``--keep-previous`` the one it replaces is kept, named by the day it was made.
"""

import argparse
import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

from app.config_model.seed import load_seed
from app.core.config import BACKEND_DIR
from scripts.travel_guide import render, screens
from scripts.travel_guide.savepoints import GuideBroken, build

OUT = BACKEND_DIR.parents[1] / "travel-rebuild-guide.pdf"


def main() -> int:
    parser = argparse.ArgumentParser(description="Make the Travel rebuild guide.")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--shots", type=Path, help="screenshots taken before")
    parser.add_argument("--html", type=Path, help="also write the HTML here")
    parser.add_argument(
        "--keep-previous",
        action="store_true",
        help="keep the guide it replaces, named by the day it was made",
    )
    args = parser.parse_args()
    seed = load_seed("travel")
    try:
        points = build(seed)
        print(f"{len(points)} save points, each passing the setup check")
        with tempfile.TemporaryDirectory(prefix="travel-guide-shots-") as taken:
            if args.shots:
                shots = {p.stem: p for p in sorted(args.shots.glob("*.png"))}
            else:
                shots = screens.capture(Path(taken))
                print(f"{len(shots)} screenshots of the editor")
            html = render.document(seed, points, shots, date.today())
            missing = [key for key in screens.KEYS if key not in shots]
            if missing:
                raise GuideBroken(f"Screenshots missing: {', '.join(missing)}")
            if args.html:
                args.html.write_text(html)
            out: Path = args.out
            if args.keep_previous and out.exists():
                day = datetime.fromtimestamp(out.stat().st_mtime).date()
                kept = out.with_name(f"{out.stem}-{day.isoformat()}{out.suffix}")
                if not kept.exists():
                    shutil.copy2(out, kept)
                    print(f"kept the previous guide as {kept}")
            render.pdf(html, out)
    except GuideBroken as broken:
        print(f"The guide was not made: {broken}", file=sys.stderr)
        return 1
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
