"""Stage 3a - curated: whole setups for activities the wiki has no loadout for.

Overrides correct a setup the wiki published. A brand-new activity has none to
correct: on release day its strategy page is prose and an empty tabber, so it
yields nothing and the library would skip it for weeks. `curated.json` fills
that gap with whole setups written by hand, by item **name**, appended here as
if extraction had found them - so they still get their ids from the wiki's own
`Module:Loadout`, and are published `curated` with the reason they exist.

An entry is a stopgap, not a second source. Once the wiki's page carries a
setup of its own, or discovery finds the activity elsewhere, the entry is
reported and validation fails the build until it is deleted.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from extract import extract_page
from overrides import activity_of
from wikiclient import WikiClient

REPO = Path(__file__).parent.parent
SETUPS = REPO / "generator" / "setups.json"
CURATED = REPO / "curated.json"


def load_curated(path: Path | None = None) -> list[dict]:
    path = path or CURATED
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("curated", [])


def add_curated(
    doc: dict, entries: list[dict], fetch: Callable[[str], tuple[str, int]]
) -> list[str]:
    """Append each entry to the setups document. Returns stale-entry notes.

    `fetch(title)` returns a page's wikitext and revision id.
    """
    notes: list[str] = []
    for entry in entries:
        activity, title = entry["activity"], entry["sourcePage"]
        text, rev_id = fetch(title)
        upstream, _ = extract_page(title, text)
        discovered = any(
            activity_of(p["page"]) == activity and p["setups"] for p in doc["pages"]
        )
        if upstream or discovered:
            notes.append(
                f"curated entry {activity!r}: the wiki now publishes a setup for "
                f"it - delete the entry from curated.json"
            )
            continue

        page = {
            "page": title,
            "revId": rev_id,
            "warnings": [],
            "setups": [
                {
                    "variant": setup["variant"],
                    "equipment": setup.get("equipment", {}),
                    "inventory": setup.get("inventory", {}),
                    "runes": setup.get("runes", {}),
                    "alternatives": {},
                    "switches": [],
                    "prepots": entry.get("prepots", []),
                    "curated": True,
                    "curationReason": entry["reason"],
                }
                for setup in entry["setups"]
            ],
        }
        # Discovery may already list the page with nothing on it; this stands
        # in for that empty result rather than sitting beside it.
        doc["pages"] = [p for p in doc["pages"] if p["page"] != title] + [page]
        doc["emptyPages"] = [t for t in doc.get("emptyPages", []) if t != title]
    return notes


def main() -> None:
    entries = load_curated()
    data = json.loads(SETUPS.read_text(encoding="utf-8"))
    client = WikiClient()
    notes = add_curated(data, entries, client.page_wikitext)
    data["curatedNotes"] = notes
    SETUPS.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"curated: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}, "
          f"{len(notes)} stale")
    for note in notes:
        print(f"   ! {note}")


if __name__ == "__main__":
    main()
