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
    """Stable over content, blind to when the run happened.

    Hashes the layouts themselves rather than an import string built from them,
    so changing how a string is encoded - which is a change to how the same
    layout is spelled, not to the layout - cannot restate every activity's
    provenance.
    """
    payload = json.dumps(
        [
            [x["tagName"], sorted(x["layout"].items()), sorted(x["layoutZigzag"].items())]
            for x in layouts
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def previous_provenance() -> dict[str, tuple[str, int]]:
    """`{slug: (contentHash, sourceRevId)}` for the library already on disk.

    Read before the old files are cleared, so a run can tell whether it is
    republishing the same content under a newer revision id.
    """
    prior: dict[str, tuple[str, int]] = {}
    for path in DATA.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            prior[record["slug"]] = (record["contentHash"], record["sourceRevId"])
        except (ValueError, KeyError):
            continue  # unreadable or pre-dating these fields; treat as absent
    return prior


def stable_rev_id(
    slug: str, digest: str, rev_id: int, previous: dict[str, tuple[str, int]]
) -> int:
    """The revision id to publish: the recorded one while content is unchanged.

    Most wiki edits touch prose, a category or a nearby table and leave the
    setup alone, so taking the newest id rewrote three quarters of `data/` every
    week for no change a player could see - and buried the handful of real
    changes the refresh review exists to catch. The recorded id has not stopped
    being true: this content did come from that revision. Freshness is not lost,
    it is `generatedAt` in index.json.
    """
    prior = previous.get(slug)
    if prior and prior[0] == digest:
        return prior[1]
    return rev_id


def write_unless_only_timestamp(path: Path, payload: dict, **dump: object) -> bool:
    """Write `payload`, unless `generatedAt` is the only thing that would move.

    Returns whether anything was written. `generatedAt` says when the pipeline
    ran, which is every week regardless of what it found - so on its own it
    turned "nothing upstream changed" into a diff, and the weekly job into a
    pull request nobody needed to read.
    """
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            existing = None
        if isinstance(existing, dict):
            drop = lambda d: {k: v for k, v in d.items() if k != "generatedAt"}
            if drop(existing) == drop(payload):
                return False
    path.write_text(json.dumps(payload, ensure_ascii=False, **dump), encoding="utf-8")
    return True


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
    # Snapshot before clearing: the ids below are compared against it.
    previous = previous_provenance()
    for stale in DATA.glob("*.json"):
        stale.unlink(missing_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = []
    site_rows = []

    for activity in sorted(by_activity):
        entries = sorted(by_activity[activity], key=lambda e: e["variant"])
        slug = slugify(activity)
        digest = content_hash(entries)
        rev_id = stable_rev_id(
            slug, digest, entries[0]["sourceRevId"], previous
        )
        record = {
            "activity": activity,
            "slug": slug,
            "sourcePage": entries[0]["sourcePage"],
            "sourceUrl": WIKI + entries[0]["sourcePage"].replace(" ", "_"),
            "sourceRevId": rev_id,
            "contentHash": digest,
            "variants": [
                {
                    "variant": e["variant"],
                    "tagName": e["tagName"],
                    "icon": e["icon"],
                    "completeness": e["completeness"],
                    "completenessNote": e["completenessNote"],
                    "importString": e["importString"],
                    "importStringZigzag": e["importStringZigzag"],
                    "importStringOfficial": e["importStringOfficial"],
                    "importStringZigzagOfficial": e["importStringZigzagOfficial"],
                    "layout": e["layout"],
                    "layoutZigzag": e["layoutZigzag"],
                    "curated": e["curated"],
                    "curationReason": e["curationReason"],
                    "equipment": e["equipment"],
                    "inventory": e["inventory"],
                    "runes": e["runes"],
                    "alternatives": e["alternatives"],
                    "twoHandedWeapons": e["twoHandedWeapons"],
                    # Spec weapons the guide expects you to bring. They wear no
                    # slot, so they sit beside the layout rather than in it.
                    "switches": e["switches"],
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
                    # No import strings: the site rebuilds one after every slot
                    # swap anyway, so shipping four spellings of a layout it
                    # already has cost a third of the payload to duplicate what
                    # layout.js can derive. data/*.json still carries them, and
                    # check_layout_port.mjs holds the browser to them.
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
                    # Spec weapons the guide expects you to bring. They wear no
                    # slot, so they sit beside the layout rather than in it.
                    "switches": e["switches"],
                    "warnings": e["warnings"],
                }
            )

    wrote_index = write_unless_only_timestamp(
        INDEX,
        {
            "generatedAt": generated_at,
            "activityCount": len(manifest),
            "layoutCount": len(layouts),
            "activities": manifest,
        },
        indent=2,
    )
    wrote_site = write_unless_only_timestamp(
        DOCS / "layouts.json",
        {
            "generatedAt": generated_at,
            # Shipped rather than duplicated in JavaScript: the site rebuilds
            # layouts client-side when a slot is swapped, and a second copy of
            # these constants would be free to drift from encode.py.
            "loadoutMap": {str(k): v for k, v in LOADOUT_MAP.items()},
            "zigzagOrder": list(ZIGZAG_EQUIPMENT_ORDER),
            "layouts": site_rows,
        },
    )

    reused = sum(
        1 for a in manifest if previous.get(a["slug"], (None,))[0] == a["contentHash"]
    )
    print(f"publish: {len(manifest)} activities, {len(layouts)} layouts")
    print(f"  wrote data/*.json")
    print(f"  index.json / docs payload: "
          f"{'written' if wrote_index else 'unchanged'} / "
          f"{'written' if wrote_site else 'unchanged'}")
    print(f"  unchanged activities keeping their recorded revision: {reused}")


if __name__ == "__main__":
    main()
