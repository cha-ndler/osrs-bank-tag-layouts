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
SLOTS = REPO / "generator" / "slots.json"

DOSE_RE = re.compile(r"^(?P<base>.+?)\s*\((?P<n>\d{1,2})\)$")
PAGE_SIZE = 5000

# Bumped whenever the on-disk item index grows a field the pipeline relies on,
# so a cache from an older shape is refetched rather than half-read.
INDEX_VERSION = 2


def sweep(client: WikiClient, table: str, fields: tuple[str, ...]) -> list[dict]:
    """Every row of a bucket table, following the offset pages."""
    selected = ",".join(f'"{f}"' for f in fields)
    rows: list[dict] = []
    offset = 0
    while True:
        batch = client.bucket(
            f'bucket("{table}").select({selected})'
            f".limit({PAGE_SIZE}).offset({offset}).run()"
        )
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def load_item_index(client: WikiClient, refresh: bool = False) -> dict:
    """All (item_name, page_name, item_id) rows from the wiki's bucket store."""
    if ITEMS.exists() and not refresh:
        cached = json.loads(ITEMS.read_text(encoding="utf-8"))
        # An index written before `byPage` existed would make every page-name
        # lookup silently return nothing, which is the failure this cache is
        # meant to prevent. Re-fetch instead of trusting the old shape.
        if cached.get("version") == INDEX_VERSION:
            return cached

    rows = sweep(client, "infobox_item", ("item_id", "item_name", "page_name"))

    by_name: dict[str, list[int]] = {}
    by_page: dict[str, list[int]] = {}
    by_id: dict[str, str] = {}
    for row in rows:
        name = (row.get("item_name") or "").strip()
        page = (row.get("page_name") or "").strip()
        ids = row.get("item_id") or []
        numeric = [int(i) for i in ids if str(i).isdigit()]
        if not numeric:
            continue
        if name:
            by_name.setdefault(name.lower(), []).extend(numeric)
            for i in numeric:
                by_id.setdefault(str(i), name)
        # `Module:Loadout` falls back to the page name when no item is called
        # what the setup asked for, so a name we cannot resolve here is one we
        # cannot verify either. Indexing pages is what closes that blind spot.
        if page:
            by_page.setdefault(page.lower(), []).extend(numeric)

    index = {
        "version": INDEX_VERSION,
        "count": len(rows),
        "byName": by_name,
        "byPage": by_page,
        "byId": by_id,
    }
    ITEMS.write_text(json.dumps(index), encoding="utf-8")
    return index


def load_slot_index(client: WikiClient, refresh: bool = False) -> dict:
    """Which equipment slot each item occupies, straight from the wiki.

    `infobox_bonuses` records one slot per *page* - two-handers get the distinct
    value ``2h`` - and `infobox_item` maps ids and names onto pages. No page in
    the store carries conflicting slots, so joining on the page name is
    unambiguous.

    This is what lets extraction know that a shield cannot be worn alongside the
    weapon it picked. `{{Recommended equipment}}` ranks each slot independently,
    so without it a Tumeken's shadow setup also claims an Elidinis' ward.
    """
    if SLOTS.exists() and not refresh:
        return json.loads(SLOTS.read_text(encoding="utf-8"))

    page_slot: dict[str, str] = {}
    for row in sweep(client, "infobox_bonuses", ("page_name", "equipment_slot")):
        page = (row.get("page_name") or "").strip()
        slot = (row.get("equipment_slot") or "").strip()
        if page and slot:
            page_slot[page] = slot

    # Pages are named on the wiki as often as items are, and a setup may cite
    # either, so both spellings resolve.
    two_handed: set[str] = {p.lower() for p, s in page_slot.items() if s == "2h"}
    id_slot: dict[str, str] = {}
    name_slot: dict[str, str] = {p.lower(): s for p, s in page_slot.items()}
    for row in sweep(client, "infobox_item", ("item_id", "item_name", "page_name")):
        slot = page_slot.get((row.get("page_name") or "").strip())
        if not slot:
            continue
        name = (row.get("item_name") or "").strip()
        if name:
            name_slot.setdefault(name.lower(), slot)
            if slot == "2h":
                two_handed.add(name.lower())
        for item_id in row.get("item_id") or []:
            if str(item_id).isdigit():
                id_slot[str(item_id)] = slot

    index = {
        "twoHandedNames": sorted(two_handed),
        "twoHandedIds": sorted(int(i) for i, s in id_slot.items() if s == "2h"),
        "idSlot": id_slot,
        "nameSlot": name_slot,
    }
    SLOTS.write_text(json.dumps(index), encoding="utf-8")
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


def build_page_dose_map(index: dict) -> dict[str, str]:
    """page name (lowercased) -> highest-numbered item that page holds.

    Some setups cite a *disambiguated page* rather than an item: Nightmare Zone
    guides ask for an ``Overload (Nightmare Zone)``. No item is called that, so
    `Module:Loadout` falls through to the page and returns whichever dose the
    store hands back first - which is how every NMZ layout came to ship the
    3-dose overload. Resolving the page ourselves picks the full one.
    """
    page_map: dict[str, str] = {}
    for page, ids in index.get("byPage", {}).items():
        if page in index["byName"]:
            # A real item is literally called this; the wiki is unambiguous.
            continue
        variants: list[tuple[int, str]] = []
        for item_id in ids:
            name = index["byId"].get(str(item_id))
            if not name:
                continue
            m = DOSE_RE.match(name.lower())
            if m:
                variants.append((int(m.group("n")), name.lower()))
        if len(variants) < 2:
            continue
        page_map[page] = max(variants, key=lambda v: v[0])[1]
    return page_map


class Normalizer:
    def __init__(self, index: dict) -> None:
        self.index = index
        self.dose_map = build_dose_map(index)
        self.page_dose_map = build_page_dose_map(index)

    def _canonical(self, lowered: str) -> str:
        """The wiki's own spelling of a lowercased item name.

        Rebuilding the name from the caller's spelling loses whatever the wiki
        actually wrote. That matters: 72 of the dose families are spelled
        ``X (4)`` with a space, and ``X(4)`` is not an item, so a rebuilt name
        resolves to nothing and the slot is dropped from the layout entirely.
        """
        for item_id in self.index["byName"].get(lowered, []):
            name = self.index["byId"].get(str(item_id))
            if name:
                return name
        return lowered

    def normalize(self, name: str) -> tuple[str, str | None]:
        """Return (name, note). `note` is set when the name was rewritten."""
        if not name:
            return name, None
        lowered = name.lower()
        if lowered in self.index["byName"]:
            return name, None
        target = self.dose_map.get(lowered) or self.page_dose_map.get(lowered)
        if not target:
            return name, None
        fixed = self._canonical(target)
        if fixed == name:
            return name, None
        return fixed, f"{name} -> {fixed}"

    def resolve_ids(self, name: str) -> list[int]:
        lowered = name.lower()
        # Item name first, then page name - the order `Module:Loadout` uses.
        return self.index["byName"].get(lowered) or self.index.get("byPage", {}).get(
            lowered, []
        )

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
