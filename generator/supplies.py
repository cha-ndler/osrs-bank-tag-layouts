"""Stage 3b - supplies: what a clue needs after the first step.

A Treasure Trails layout from `{{Loadout}}` is what to carry when the clue
starts. A clue's later steps ask for more: Falo the Bard wants to see one item
per verse, Sherlock sets a skilling task, an emote step needs a STASH unit that
may not be built yet. A player who banks before every step wants those items in
the same tab, so each tier's layout gains rows of them below the inventory.

Every group comes from a wiki page, named with its revision so the layout's
provenance covers it:

* **Bank** - the tier's own `====Bank====` table on Treasure Trails/Strategies.
* **STASH unit** - the build recipe for the tier's STASH (`recipe` bucket):
  planks, nails, tools, and gold leaf at master. Members only, so the
  free-to-play beginner layout skips it.
* **Falo the Bard** (master) - the first item listed for each verse; any other
  accepted form is a variant the tag already covers.
* **Sherlock** (elite, master) - the items each challenge names.

This stage only reads names. `encode` resolves them through `Module:Loadout`
like every other item, and lays them out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from extract import HEADING_RE, clean_item, plink_items
from wikiclient import WikiClient

REPO = Path(__file__).parent.parent
SUPPLIES = REPO / "generator" / "supplies.json"

ACTIVITY = "Treasure Trails"
STRATEGIES = "Treasure Trails/Strategies"
FALO = "Falo the Bard"
SHERLOCK = {"elite": "Sherlock/Elite", "master": "Sherlock/Master"}
STASH_PAGE = "STASH"
TIERS = ("beginner", "easy", "medium", "hard", "elite", "master")

LINK_RE = re.compile(r"\[\[([^|\]#]+)(?:\|[^\]]*)?\]\]")
PAREN_RE = re.compile(r"\([^()]*\)")


def tier_of(variant: str) -> str | None:
    lowered = variant.lower()
    return next((t for t in TIERS if lowered.startswith(t)), None)


def _sections(text: str) -> list[tuple[int, str, int, int]]:
    """(level, heading, body start, body end) for every heading."""
    heads = list(HEADING_RE.finditer(text))
    return [
        (len(m.group(1)), m.group(2).strip(), m.end(),
         heads[i + 1].start() if i + 1 < len(heads) else len(text))
        for i, m in enumerate(heads)
    ]


def bank_sections(text: str) -> dict[str, list[str]]:
    """Setup heading -> the items its `Bank` subsection lists.

    The setup headings are the tab labels, so they are the variant names.
    """
    sections = _sections(text)
    out: dict[str, list[str]] = {}
    for i, (level, heading, _, _) in enumerate(sections):
        if heading.lower() == "bank":
            continue
        for sub_level, sub, start, end in sections[i + 1 :]:
            if sub_level <= level:
                break
            if sub.lower() == "bank":
                names = plink_items_all(text[start:end])
                if names:
                    out[heading] = names
                break
    return out


def plink_items_all(text: str) -> list[str]:
    """Every plinked item in a block, once each, in order."""
    out: list[str] = []
    for m in re.finditer(r"\{\{\s*plink[a-z]*\s*\|[^}]*\}\}", text, re.IGNORECASE):
        for name in plink_items(m.group(0)):
            if name not in out:
                out.append(name)
    return out


def _rows(table: str) -> list[str]:
    # The closing `|}` would otherwise read as one more cell of the last row.
    table = re.sub(r"\n\|\}\s*$", "", table)
    return [r for r in re.split(r"\n\|-[^\n]*", table)[1:] if r.strip()]


def _first_table(text: str) -> str:
    m = re.search(r"\{\|.*?\n\|\}", text, re.DOTALL)
    return m.group(0) if m else ""


def falo_items(text: str) -> list[str]:
    """One item per verse: the first the table lists under its lyric.

    A verse spans several rows (`rowspan`), one per accepted item. Only the
    row that carries the lyric cell starts a verse; the rest are alternatives.
    """
    out: list[str] = []
    for row in _rows(_first_table(text)):
        cells = [c for c in re.split(r"\n\|", "\n" + row.strip()) if c.strip()]
        starts_verse = any("plink" not in c.lower() for c in cells)
        items = plink_items_all(row)
        if starts_verse and items and items[0] not in out:
            out.append(items[0])
    return out


def sherlock_items(text: str) -> list[str]:
    """Items each challenge names, from its first sentence, outside brackets.

    The column mixes requirements with notes: "[[Abyssal whip]] (frozen,
    volcanic and abyssal tentacle also work, the ornament kit does not)".
    Brackets and later sentences are commentary, so only the requirement
    itself is read.
    """
    out: list[str] = []
    for row in _rows(_first_table(text)):
        cells = re.split(r"\n\|", "\n" + row.strip())
        cells = [c for c in cells if c.strip()]
        if len(cells) < 4:
            continue
        first = re.split(r"\.\s|\n", cells[3].strip(), maxsplit=1)[0]
        while PAREN_RE.search(first):
            first = PAREN_RE.sub("", first)
        for link in LINK_RE.findall(first):
            name = clean_item(link)
            if name and name not in out:
                out.append(name)
    return out


def stash_materials(rows: list[dict]) -> dict[str, list[str]]:
    """tier -> materials and tools to build that tier's STASH units."""
    out: dict[str, list[str]] = {}
    for row in rows:
        production = json.loads(row.get("production_json") or "{}")
        tier = (production.get("output", {}).get("subtxt") or "").lower()
        if tier not in TIERS:
            continue
        names = [m["name"] for m in production.get("materials", []) if m.get("name")]
        names += [t.strip() for t in (production.get("tools") or "").split(",") if t.strip()]
        out[tier] = list(dict.fromkeys(names))
    return out


def groups_for(variant: str, sources: dict) -> list[dict]:
    """The supply groups one Treasure Trails variant carries, in order."""
    tier = tier_of(variant)
    groups = []

    def add(group: str, page: str, names: list[str]) -> None:
        if names:
            groups.append({"group": group, "source": {"page": page,
                           "revId": sources["revIds"].get(page, 0)}, "names": names})

    add("Bank", STRATEGIES, sources["bank"].get(variant, []))
    if tier and "free-to-play" not in variant.lower():
        add("STASH unit", STASH_PAGE, sources["stash"].get(tier, []))
    if tier == "master":
        add("Falo the Bard", FALO, sources["falo"])
    if tier in SHERLOCK:
        add("Sherlock", SHERLOCK[tier], sources["sherlock"].get(tier, []))
    return groups


# The presets grid fills rows 0-6, so supplies never start above row 8: a
# short layout still leaves the grid's own slots (the rune pouch row) to it.
FIRST_SUPPLY_ROW = 8


def place_supplies(layout: dict[str, int], groups: list[list[int]]) -> dict[str, int]:
    """Lay groups out below a layout: a blank row, then a row-aligned run each.

    One rule for both layout styles, so the site can rebuild either.
    """
    out = dict(layout)
    groups = [g for g in groups if g]
    if not groups:
        return out
    top = max((int(p) for p in layout), default=-1)
    cursor = max(top // 8 + 2, FIRST_SUPPLY_ROW) * 8
    for ids in groups:
        for i, item_id in enumerate(ids):
            out[str(cursor + i)] = item_id
        cursor = ((cursor + len(ids) - 1) // 8 + 1) * 8
    return out


def main() -> None:
    client = WikiClient()
    strategies, strategies_rev = client.page_wikitext(STRATEGIES)
    falo, falo_rev = client.page_wikitext(FALO)
    sherlock = {}
    rev_ids = {STRATEGIES: strategies_rev, FALO: falo_rev}
    for tier, page in SHERLOCK.items():
        text, rev_ids[page] = client.page_wikitext(page)
        sherlock[tier] = sherlock_items(text)
    # The recipe lives in a bucket, which carries no revision; the page that
    # stores it does, and that is what an edit to the recipe moves.
    _, rev_ids[STASH_PAGE] = client.page_wikitext(STASH_PAGE)
    stash_rows = client.bucket(
        'bucket("recipe").select("page_name","production_json")'
        '.where("page_name","STASH").run()'
    )
    sources = {
        "revIds": rev_ids,
        "bank": bank_sections(strategies),
        "stash": stash_materials(stash_rows),
        "falo": falo_items(falo),
        "sherlock": sherlock,
    }
    SUPPLIES.write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"supplies: bank tables for {len(sources['bank'])} setups, "
        f"STASH recipes for {len(sources['stash'])} tiers, "
        f"{len(sources['falo'])} Falo items, "
        f"Sherlock {', '.join(f'{t} {len(v)}' for t, v in sherlock.items())}"
    )


if __name__ == "__main__":
    main()
