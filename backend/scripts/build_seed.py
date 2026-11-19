"""Write version 1 of each category's configuration from the approved code.

    uv run python -m scripts.build_seed

Writes ``app/seed/v1/{eshop,travel,food}.json``. Those files are loaded by the
migration that creates ``category_version``, so this is run once, while the code
still holds the approved rules; ``tests/test_config.py`` fails if the files and
the code ever disagree.
"""

import sys

from app.config_model.seed import CATEGORIES, SEED_DIR, build_seed, seed_json, seed_path


def main() -> int:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    for category in CATEGORIES:
        seed_path(category).write_text(seed_json(build_seed(category)))
        print(f"wrote {seed_path(category)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
