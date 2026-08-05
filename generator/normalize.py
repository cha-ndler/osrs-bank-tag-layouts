"""Stage 3 - normalize: fix item names that the wiki resolves ambiguously.

`Module:Loadout` resolves a name by looking for an exact `item_name` first and
falling back to `page_name`. The fallback returns whichever variant the store
hands back first, which is how a bare "Saradomin brew" becomes **6687 - the
3-dose potion** instead of the 4-dose one, and "Super restore" becomes 3026.
Left alone, every generated boss layout would ship part-used potions.

The correction is deliberately narrow: rewrite a name **only** when no item is
literally called that, and the page it points at is a numbered family (doses,
jewellery charges). In that case pick the highest number - a full potion, a
fully charged glory. When an exact item does exist the wiki's answer is already
deterministic and is left untouched.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from wikiclient import WikiClient

REPO = Path(__file__).parent.parent
ITEMS = REPO / "generator" / "items.json"

DOSE_RE = re.compile(r"^(?P<base>.+?)\s*\((?P<n>\d{1,2})\)$")
PAGE_SIZE = 5000


def load_item_index(client: WikiClient, refresh: bool = False) -> dict:
    """All (item_name, page_name, item_id) rows from the wiki's bucket store."""
    if ITEMS.exists() and not refresh:
        return json.loads(ITEMS.read_text(encoding="utf-8"))

    rows: list[dict] = []
    offset = 0
    while True:
        batch = client.bucket(
            f'bucket("infobox_item").select("item_id","item_name","page_name")'
            f".limit({PAGE_SIZE}).offset({offset}).run()"
        )
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    by_name: dict[str, list[int]] = {}
    by_id: dict[str, str] = {}
    for row in rows:
        name = (row.get("item_name") or "").strip()
        ids = row.get("item_id") or []
        numeric = [int(i) for i in ids if str(i).isdigit()]
        if name and numeric:
            by_name.setdefault(name.lower(), []).extend(numeric)
            for i in numeric:
                by_id.setdefault(str(i), name)

    index = {"count": len(rows), "byName": by_name, "byId": by_id}
    ITEMS.write_text(json.dumps(index), encoding="utf-8")
    return index


def build_dose_map(index: dict) -> dict[str, str]:
    """base name (lowercased) -> highest-numbered variant, e.g. 'Saradomin brew(4)'."""
    families: dict[str, list[tuple[int, str]]] = {}
    for lowered in index["byName"]:
        m = DOSE_RE.match(lowered)
        if not m:
            continue
        base = m.group("base").strip()
        families.setdefault(base, []).append((int(m.group("n")), lowered))

    dose_map: dict[str, str] = {}
    for base, variants in families.items():
        if len(variants) < 2:
            continue
        if base in index["byName"]:
            # A real item is literally called this; the wiki is unambiguous.
            continue
        _, best = max(variants, key=lambda v: v[0])
        dose_map[base] = best
    return dose_map


class Normalizer:
    def __init__(self, index: dict) -> None:
        self.index = index
        self.dose_map = build_dose_map(index)

    def normalize(self, name: str) -> tuple[str, str | None]:
        """Return (name, note). `note` is set when the name was rewritten."""
        if not name:
            return name, None
        lowered = name.lower()
        if lowered in self.index["byName"]:
            return name, None
        target = self.dose_map.get(lowered)
        if not target:
            return name, None
        # Restore the wiki's capitalisation for the chosen variant.
        m = DOSE_RE.match(target)
        fixed = f"{name}({m.group('n')})" if m else target
        return fixed, f"{name} -> {fixed}"

    def resolve_ids(self, name: str) -> list[int]:
        return self.index["byName"].get(name.lower(), [])

    def name_for_id(self, item_id: int) -> str | None:
        return self.index["byId"].get(str(item_id))


def main() -> None:
    client = WikiClient()
    index = load_item_index(client, refresh=True)
    norm = Normalizer(index)
    print(f"normalize: item index has {index['count']} rows, "
          f"{len(index['byName'])} distinct names")
    print(f"  dose/charge families needing rewrite: {len(norm.dose_map)}")
    for probe in ["Saradomin brew", "Super restore", "Ranging potion",
                  "Stamina potion", "Anglerfish", "Zaryte crossbow"]:
        fixed, note = norm.normalize(probe)
        ids = norm.resolve_ids(fixed)
        print(f"  {probe!r:22} -> {fixed!r:26} ids={ids[:4]}")


if __name__ == "__main__":
    main()
