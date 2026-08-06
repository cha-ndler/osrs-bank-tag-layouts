"""Stage 6 - publish: write the canonical data files and the site payload.

`data/*.json` is the artifact everything else consumes - the future RuneLite
plugin can vendor it verbatim, since each record already carries the layout as a
position->itemId map and does not need the import string re-parsed.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from encode import LOADOUT_MAP, ZIGZAG_EQUIPMENT_ORDER

REPO = Path(__file__).parent.parent
ENCODED = REPO / "generator" / "encoded.json"
DATA = REPO / "data"
DOCS = REPO / "docs"
INDEX = REPO / "index.json"

WIKI = "https://oldschool.runescape.wiki/w/"


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"['’]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "unnamed"


def content_hash(layouts: list[dict]) -> str:
    """Stable over content, blind to when the run happened."""
    payload = json.dumps(
        [[x["tagName"], x["importString"]] for x in layouts], sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    data = json.loads(ENCODED.read_text(encoding="utf-8"))
    layouts = data["layouts"]

    by_activity: dict[str, list[dict]] = {}
    for entry in layouts:
        by_activity.setdefault(entry["activity"], []).append(entry)

    # Clear stale activity files without removing the directory: on Windows,
    # rmdir on a just-emptied directory intermittently fails with
    # PermissionError while the OS still holds a handle on it.
    DATA.mkdir(parents=True, exist_ok=True)
    for stale in DATA.glob("*.json"):
        stale.unlink(missing_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = []
    site_rows = []

    for activity in sorted(by_activity):
        entries = sorted(by_activity[activity], key=lambda e: e["variant"])
        slug = slugify(activity)
        record = {
            "activity": activity,
            "slug": slug,
            "sourcePage": entries[0]["sourcePage"],
            "sourceUrl": WIKI + entries[0]["sourcePage"].replace(" ", "_"),
            "sourceRevId": entries[0]["sourceRevId"],
            "contentHash": content_hash(entries),
            "variants": [
                {
                    "variant": e["variant"],
                    "tagName": e["tagName"],
                    "icon": e["icon"],
                    "completeness": e["completeness"],
                    "completenessNote": e["completenessNote"],
                    "importString": e["importString"],
                    "importStringZigzag": e["importStringZigzag"],
                    "layout": e["layout"],
                    "layoutZigzag": e["layoutZigzag"],
                    "curated": e["curated"],
                    "curationReason": e["curationReason"],
                    "equipment": e["equipment"],
                    "inventory": e["inventory"],
                    "runes": e["runes"],
                    "alternatives": e["alternatives"],
                    "twoHandedWeapons": e["twoHandedWeapons"],
                    "warnings": e["warnings"],
                }
                for e in entries
            ],
        }
        (DATA / f"{slug}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        manifest.append(
            {
                "activity": activity,
                "slug": slug,
                "file": f"data/{slug}.json",
                "variantCount": len(entries),
                "sourceRevId": record["sourceRevId"],
                "contentHash": record["contentHash"],
            }
        )
        for e in entries:
            site_rows.append(
                {
                    "activity": activity,
                    "slug": slug,
                    "variant": e["variant"],
                    "tagName": e["tagName"],
                    "icon": e["icon"],
                    "completeness": e["completeness"],
                    "completenessNote": e["completenessNote"],
                    "importString": e["importString"],
                    "importStringZigzag": e["importStringZigzag"],
                    "layout": e["layout"],
                    "layoutZigzag": e["layoutZigzag"],
                    "sourceUrl": record["sourceUrl"],
                    "curated": e["curated"],
                    "curationReason": e["curationReason"],
                    # The site rebuilds the layout after every swap, so it needs
                    # the slot-keyed source, not just the finished position map.
                    "equipment": e["equipment"],
                    "inventory": e["inventory"],
                    "runes": e["runes"],
                    "alternatives": e["alternatives"],
                    "twoHandedWeapons": e["twoHandedWeapons"],
                    "warnings": e["warnings"],
                }
            )

    INDEX.write_text(
        json.dumps(
            {
                "generatedAt": generated_at,
                "activityCount": len(manifest),
                "layoutCount": len(layouts),
                "activities": manifest,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (DOCS / "layouts.json").write_text(
        json.dumps(
            {
                "generatedAt": generated_at,
                # Shipped rather than duplicated in JavaScript: the site rebuilds
                # layouts client-side when a slot is swapped, and a second copy
                # of these constants would be free to drift from encode.py.
                "loadoutMap": {str(k): v for k, v in LOADOUT_MAP.items()},
                "zigzagOrder": list(ZIGZAG_EQUIPMENT_ORDER),
                "layouts": site_rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"publish: {len(manifest)} activities, {len(layouts)} layouts")
    print(f"  wrote data/*.json, index.json, docs/layouts.json")


if __name__ == "__main__":
    main()
