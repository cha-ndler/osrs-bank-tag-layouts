"""Run the whole pipeline: discover -> extract -> encode -> validate -> publish.

Exits non-zero if validation finds anything wrong, so CI refuses to publish a
broken library rather than quietly shipping it.
"""

from __future__ import annotations

import argparse
import sys

import discover
import encode
import extract
import publish
import validate
from normalize import ITEMS
from wikiclient import CACHE_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="drop the on-disk wiki cache and re-fetch everything",
    )
    args = parser.parse_args()

    if args.fresh and CACHE_DIR.exists():
        # Clear the contents rather than the directory itself: on Windows,
        # removing a directory that was just emptied often fails with
        # PermissionError while the OS still holds a handle on it.
        removed = 0
        for path in CACHE_DIR.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
        print(f"cache cleared ({removed} entries)")
        # The item index is cached separately and would otherwise stay stale,
        # which is exactly what --fresh is meant to prevent.
        ITEMS.unlink(missing_ok=True)

    for stage in (discover, extract, encode):
        stage.main()

    code = validate.validate()
    if code != 0:
        print("\nvalidation failed - not publishing. See report.json.", file=sys.stderr)
        return code

    publish.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
