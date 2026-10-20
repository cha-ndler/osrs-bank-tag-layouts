"""Stage 4 - encode: turn structured setups into bank tag import strings.

Rather than reimplement the wiki's name-to-id resolution (and inherit a second
set of bugs), we hand a synthesised `{{Loadout}}` call back to the wiki via
`action=expandtemplates` and let `Module:Loadout` do the encoding. The output is
therefore bug-compatible with what the wiki itself publishes.

What the wiki hands back is the built-in Bank Tags plugin's own format:

    banktags,1,<tagName>,<iconItemId>,layout,<pos>,<itemId>,<pos>,<itemId>,...

That is not what gets published - see `build_hub_import_string` for why - but it
is what `layout` is parsed out of. Positions index an 8-wide bank grid.
`LOADOUT_MAP` is the grid this library publishes: worn gear in columns 0-2
(shaped like the equipment panel), column 3 empty as a spacer, and the 28
inventory slots in columns 4-7 in their natural 4-wide shape. The wiki's own
grid is read live from `Module:Loadout` and only used to decode what it hands
back, so the module being rearranged upstream cannot move anything here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from normalize import DOSE_RE, Normalizer, load_item_index, load_slot_index
from supplies import ACTIVITY as SUPPLY_ACTIVITY, SUPPLIES, groups_for, place_supplies
from wikiclient import WikiClient

REPO = Path(__file__).parent.parent
SETUPS = REPO / "generator" / "setups.json"
ENCODED = REPO / "generator" / "encoded.json"
EQUIVALENTS = REPO / "equivalents.json"

# Bank grid position -> Loadout argument name. Taken from Module:Loadout as of
# 2025-09 and pinned: this is the published grid, not a copy to keep in sync.
LOADOUT_MAP: dict[int, str] = {
    1: "head", 2: "ammo2", 4: "1", 5: "2", 6: "3", 7: "4",
    8: "cape", 9: "neck", 10: "ammo", 12: "5", 13: "6", 14: "7", 15: "8",
    16: "weapon", 17: "torso", 18: "shield", 20: "9", 21: "10", 22: "11", 23: "12",
    25: "legs", 28: "13", 29: "14", 30: "15", 31: "16",
    32: "gloves", 33: "boots", 34: "ring", 36: "17", 37: "18", 38: "19", 39: "20",
    40: "rune1", 41: "rune2", 42: "rune3", 44: "21", 45: "22", 46: "23", 47: "24",
    48: "rune4", 52: "25", 53: "26", 54: "27", 55: "28",
}

# Prepots - potions drunk before the fight, so banked with the layout but never
# carried in it - go in the row under the inventory, in its four columns. Clear
# of every LOADOUT_MAP position and of the rune pouch's row.
PREPOT_POSITIONS = (60, 61, 62, 63)

EQUIPMENT_SLOTS = (
    "head", "cape", "neck", "ammo", "ammo2", "weapon",
    "torso", "legs", "shield", "gloves", "boots", "ring",
)

MODULE_PAGE = "Module:Loadout"
# `local loadoutmap = { [1] = 'head', ... }` in the module source.
MODULE_TABLE_RE = re.compile(r"local\s+loadoutmap\s*=\s*\{(?P<body>.*?)\n\s*\}", re.DOTALL)
MODULE_ENTRY_RE = re.compile(r"\[(\d+)\]\s*=\s*'([^']+)'")

BATCH_SIZE = 10
CODE_RE = re.compile(r"banktags,1,[^<\n]+")

# Characters a tag name cannot survive. A comma splits the CSV itself, so it
# corrupts the layout outright; `<`, `>`, `/` and `:` are dropped on the way in
# by RuneLite's own filter (`TabInterface.FILTERED_CHARS`, which both importers
# and the hub plugin apply), which silently turned "Budget Melee/Range" into
# "Budget MeleeRange" in game while the site went on showing the slash.
UNSAFE_NAME = re.compile(r"[,\n\r|{}=<>/:]")


def activity_name(page: str) -> str:
    return page.rsplit("/Strategies", 1)[0].strip()


MAX_TAG_NAME = 60

# A setup counts as complete with most gear slots filled and a real inventory.
COMPLETE_EQUIPMENT = 8
COMPLETE_INVENTORY = 10

# Activities whose setups are genuinely small. Without this, a correct
# four-item Tempoross layout is indistinguishable from a parsing failure.
MINIMAL_ACTIVITIES = {
    "Tempoross": "Tempoross needs only a few items; supplies come from the fight.",
    "The Gauntlet": "Gear and supplies are crafted inside the Gauntlet.",
    "Wintertodt": "Warm clothing plus a few tools is the whole setup.",
    "Guardians of the Rift": "Essence and a couple of tools; the rest is gathered inside.",
    "Chest (Rogues' Castle)": "Deliberately low-risk wilderness setups carry almost nothing.",
    "Vale Totems": "The page documents routes rather than a full loadout.",
    "Nightmare Zone": "Absorptions and a few potions by design.",
    "Barbarian Assault": "Minigame supplies are provided inside.",
    "Fishing Trawler": "Minigame; almost nothing is brought in.",
    "Hunters' Rumours": "A few tools and traps rather than a combat loadout.",
    "Moons of Peril": "Per-boss weapon tables; the shared armour is on the other variants.",
}


def completeness(activity: str, equipment: dict, inventory: dict) -> tuple[str, str]:
    """(status, note) - `complete`, `minimal` (small on purpose) or `partial`."""
    if len(equipment) >= COMPLETE_EQUIPMENT and len(inventory) >= COMPLETE_INVENTORY:
        return "complete", ""
    note = MINIMAL_ACTIVITIES.get(activity)
    if note:
        return "minimal", note
    return "partial", "Fewer items than a typical setup; the wiki page may not list a full loadout."


def tag_name(activity: str, variant: str) -> str:
    variant = (variant or "").strip()
    generic = variant.lower() in ("setup", "setups", "equipment", "inventory", "")
    name = activity if generic else f"{activity} {variant}"
    name = UNSAFE_NAME.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()

    if len(name) <= MAX_TAG_NAME:
        return name

    # Parenthetical asides are the first thing to go - dropping
    # "(not recommended for first quiver)" beats truncating into "first quive".
    trimmed = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if trimmed and len(trimmed) <= MAX_TAG_NAME:
        return trimmed

    # Otherwise cut on a word boundary rather than mid-word.
    cut = (trimmed or name)[:MAX_TAG_NAME]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.strip()


def build_loadout_wikitext(name: str, icon: str, setup: dict) -> str:
    lines = ["{{Loadout", f"|loadoutname = {name}"]
    if icon:
        lines.append(f"|loadouticon = {icon}")
    for slot in EQUIPMENT_SLOTS:
        value = setup["equipment"].get(slot)
        if value:
            lines.append(f"|{slot} = {value}")
    for n in range(1, 29):
        value = setup["inventory"].get(str(n))
        if value:
            lines.append(f"|{n} = {value}")
    for n in range(1, 5):
        value = setup["runes"].get(str(n))
        if value:
            lines.append(f"|rune{n} = {value}")
    lines.append("}}")
    return "\n".join(lines)


def normalize_alternatives(
    alternatives: dict[str, list[str]], norm: Normalizer
) -> dict[str, list[str]]:
    """Same dose correction the worn items get, so a swap cannot regress it."""
    out: dict[str, list[str]] = {}
    for slot, options in alternatives.items():
        fixed: list[str] = []
        for name in options:
            corrected, _ = norm.normalize(name)
            if corrected not in fixed:
                fixed.append(corrected)
        if fixed:
            out[slot] = fixed
    return out


def normalize_switches(switches: list[str], norm: Normalizer) -> list[str]:
    """Same dose correction the worn items get, applied to the spec weapons."""
    out: list[str] = []
    for name in switches:
        corrected, _ = norm.normalize(name)
        if corrected and corrected not in out:
            out.append(corrected)
    return out


# The leading name of a template call left in a slot value, e.g. "Cheap food".
DYNAMIC_CALL_RE = re.compile(r"\{\{\s*([^|}]+)")


def dynamic_notes(setup: dict) -> list[str]:
    """Flag slots whose item is chosen by a template rather than named outright.

    `{{Cheap food}}` and `{{Cheap prayer}}` resolve to whatever is cheapest at
    render time, so those slots move with the Grand Exchange rather than with
    the guide - 735 of them across the library. Recording it is what lets a
    reviewer tell price drift from a real upstream edit in the weekly refresh.

    `{{#var:...}}` is a different problem: it is a page-local variable that
    expands to nothing once the block is rendered on its own, so the slot is
    lost rather than merely unstable.
    """
    notes: list[str] = []
    for section in ("equipment", "inventory", "runes"):
        for key, value in setup[section].items():
            if "{{" not in value:
                continue
            m = DYNAMIC_CALL_RE.search(value)
            call = (m.group(1).strip() if m else value)[:40]
            if call.startswith("#"):
                note = (
                    f"{section} {key} uses {{{{{call}}}}}, a page-local value that "
                    f"does not resolve on its own - the slot is dropped"
                )
            else:
                note = f"{section} {key} resolved via {{{{{call}}}}} (price-dependent)"
            if note not in notes:
                notes.append(note)
    return notes


def normalize_setup(setup: dict, norm: Normalizer) -> tuple[dict, list[str]]:
    notes: list[str] = []
    out = {"equipment": {}, "inventory": {}, "runes": {}}
    for section in ("equipment", "inventory", "runes"):
        for key, value in setup[section].items():
            fixed, note = norm.normalize(value)
            out[section][key] = fixed
            if note and note not in notes:
                notes.append(note)
    return out, notes


def parse_loadout_map(source: str) -> dict[int, str]:
    """The position table out of `Module:Loadout`'s Lua source."""
    m = MODULE_TABLE_RE.search(source)
    if not m:
        raise ValueError(f"{MODULE_PAGE} no longer defines loadoutmap")
    return {int(pos): arg for pos, arg in MODULE_ENTRY_RE.findall(m.group("body"))}


def relay_positions(upstream: dict[int, str]) -> dict[int, int]:
    """Wiki grid position -> the position the same slot takes in ours.

    A slot that only moved is translated silently; that is the whole point.
    A slot that appeared, vanished or was renamed has no answer here, and
    guessing one would publish a grid nobody designed, so it stops the run.
    """
    ours = {arg: pos for pos, arg in LOADOUT_MAP.items()}
    theirs = set(upstream.values())
    added, removed = sorted(theirs - set(ours)), sorted(set(ours) - theirs)
    if added or removed or len(theirs) != len(upstream):
        raise ValueError(
            f"{MODULE_PAGE} slot arguments changed (added {added}, removed "
            f"{removed}); LOADOUT_MAP needs a position for each by hand"
        )
    return {pos: ours[arg] for pos, arg in upstream.items()}


def load_relay(client: WikiClient) -> tuple[dict[int, int], list[str]]:
    """The live relay, plus a note for every slot the wiki has moved."""
    source, _ = client.page_wikitext(MODULE_PAGE)
    upstream = parse_loadout_map(source)
    relay = relay_positions(upstream)
    moved = [
        f"{arg}: wiki {pos}, published {relay[pos]}"
        for pos, arg in sorted(upstream.items())
        if relay[pos] != pos
    ]
    return relay, moved


def parse_import_string(code: str, relay: dict[int, int] | None = None) -> dict[str, int]:
    """position -> itemId, from the tail of a banktags string.

    `relay` translates positions from the grid the string was built on (the
    wiki's) onto `LOADOUT_MAP`. Without it positions are taken as they are.
    """
    parts = code.split(",")
    try:
        tail = parts[parts.index("layout") + 1 :]
    except ValueError:
        return {}
    layout: dict[str, int] = {}
    for pos, item in zip(tail[0::2], tail[1::2]):
        if pos.strip().isdigit() and item.strip().isdigit():
            at = int(pos)
            if relay is not None:
                # A position the module does not map holds nothing it laid out.
                if at not in relay:
                    continue
                at = relay[at]
            layout[str(at)] = int(item.strip())
    return layout


def split_layout(layout: dict[str, int]) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Split a position map back into equipment / inventory / runes."""
    equipment: dict[str, int] = {}
    inventory: dict[str, int] = {}
    runes: dict[str, int] = {}
    for pos, item_id in layout.items():
        arg = LOADOUT_MAP.get(int(pos))
        if arg is None:
            continue
        if arg.isdigit():
            inventory[arg] = item_id
        elif arg.startswith("rune"):
            runes[arg[4:]] = item_id
        else:
            equipment[arg] = item_id
    return equipment, inventory, runes


# RuneLite EquipmentInventorySlot order, which is the order the plugin walks
# worn items in. Not the order our JSON happens to store them.
ZIGZAG_EQUIPMENT_ORDER = (
    "head", "cape", "neck", "weapon", "torso", "shield",
    "legs", "gloves", "boots", "ring", "ammo", "ammo2",
)


def zigzag_index(i: int) -> int:
    """Port of LayoutGenerator.toZigZagIndex.

    Items fill a two-row block column by column: 0, 8, 1, 9, 2, 10 ... so a
    16-item run covers exactly two rows of the 8-wide bank grid.
    """
    row = (i // 16) * 2
    j = i - (i // 16) * 16
    return (0 if j % 2 == 0 else 8) + (j // 2) + row * 8


def _place(items: list[int], layout: dict[int, int], i: int, use_zigzag: bool) -> int:
    """Port of LayoutGenerator.layoutItems, including its cursor advance."""
    for item_id in items:
        layout[zigzag_index(i) if use_zigzag else i] = item_id
        i += 1
    if items and layout:
        highest = max(layout)
        # After a group the cursor jumps to the next row-pair (zigzag) or the
        # next row (linear), which is what keeps groups visually separated.
        i = (highest // 16 * 2 + 2) * 8 if use_zigzag else (highest // 8 + 1) * 8
    return i


def zigzag_layout(equipment: dict[str, int], inventory: dict[str, int],
                  runes: dict[str, int], prepots: list[int] = ()) -> dict[str, int]:
    """Build the plugin's ZIGZAG arrangement from resolved item ids.

    Prepots go in the gear band's free cells at its top right: right-aligned
    on the top row, and only past that on the bottom row, right-aligned too.
    """
    worn = [equipment[s] for s in ZIGZAG_EQUIPMENT_ORDER if equipment.get(s)]
    inv = [inventory[str(n)] for n in range(1, 29) if inventory.get(str(n))]
    pouch = [runes[str(n)] for n in range(1, 5) if runes.get(str(n))]

    layout: dict[int, int] = {}
    i = _place(worn, layout, 0, True)
    for pos, item_id in zip(prepot_cells(layout, len(prepots)), prepots):
        layout[pos] = item_id
    if prepots:
        i = max(i, 16)
    i = _place(inv, layout, i, True)
    # The plugin lays the rune pouch out linearly, not zigzag.
    _place(pouch, layout, i, False)
    return {str(k): v for k, v in sorted(layout.items())}


def prepot_cells(layout: dict[int, int], count: int) -> list[int]:
    """The gear-band cells zigzag gives `count` prepots, worn items placed."""
    top = [c for c in range(8) if c not in layout]
    bottom = [c for c in range(8, 16) if c not in layout]
    on_top = min(count, len(top))
    cells = top[len(top) - on_top:] + bottom[len(bottom) - (count - on_top):]
    if len(cells) < count:
        raise ValueError(f"{count} prepots, but the gear band has {len(cells)} free cells")
    return cells


def place_prepots(layout: dict[str, int], ids: list[int]) -> dict[str, int]:
    """The presets layout with its prepots in the row under the inventory."""
    if len(ids) > len(PREPOT_POSITIONS):
        raise ValueError(
            f"{len(ids)} prepots, but the row under the inventory holds "
            f"{len(PREPOT_POSITIONS)}"
        )
    out = dict(layout)
    for pos, item_id in zip(PREPOT_POSITIONS, ids):
        out[str(pos)] = item_id
    return out


# The Plugin Hub "Bank Tag Layouts" export format:
#
#   banktaglayoutsplugin:<name>,<itemId>:<pos>,...,banktag:<name>,<icon>,<itemId>,...
#
# This is what gets published, because it is the only one of the two RuneLite
# formats both importers accept:
#
#   * "Import tag tab" (built-in) dispatches on the prefix and reads it in
#     `TabInterface.importBtlTag`, producing a built-in layout. Shipped in
#     RuneLite 1.10.34, the same release that added built-in layouts at all.
#   * "Import tag tab with layout" (hub plugin) reads it and produces a hub
#     layout.
#
# `banktags,1,...` only satisfies the first: handed to the hub importer it is
# rejected outright, and imported through the built-in one it lands as a
# built-in layout that a hub-mode player then has to "Convert to hub layout" by
# hand. One string that needs no conversion in either plugin beats two strings
# and a choice.
#
# Both importers take the tab's items from the `banktag:` tail and none from the
# layout pairs, so every id in the layout has to appear there too or the tab
# imports with a layout and nothing in it.
HUB_PREFIX = "banktaglayoutsplugin:"
HUB_TAG_MARKER = "banktag:"


def tag_tail(
    ids: list[int],
    exact_only: set[int] | frozenset[int] = frozenset(),
    equivalents: dict[int, list[int]] | None = None,
) -> list[int]:
    """The items a tag lists, in the order the string carries them.

    1. Every laid-out id, exactly. An importer that ignores the sign below
       still tags the layout.
    2. The same ids negated, which RuneLite reads as "and every variant of
       it" (`TagManager.getItemId`): the (l), (i), (or), broken, charged and
       recoloured forms. Its layouts then draw a variant in the original's
       slot. Doses and charges stay exact (`exact_only`), or one potion would
       pull every part-used dose into the tab.
    3. Curated equivalents that carry a different name (Infernal max cape for
       Infernal cape), which no variant mapping links. They are tagged, so they
       show in the tab, but cannot share the original's slot.

    A tag is a set, and a layout is not: 24 pure essence is one tagged item.
    """
    equivalents = equivalents or {}
    seen = list(dict.fromkeys(ids))
    tail = list(seen)
    tail += [-i for i in seen if i not in exact_only]
    for i in seen:
        for e in equivalents.get(i, []):
            tail.append(e)
            if e not in exact_only:
                tail.append(-e)
    return list(dict.fromkeys(tail))


def build_hub_import_string(
    tag_name_: str,
    icon: int,
    layout: dict[str, int],
    exact_only: set[int] | frozenset[int] = frozenset(),
    equivalents: dict[int, list[int]] | None = None,
) -> str:
    pairs = sorted(layout.items(), key=lambda kv: int(kv[0]))
    parts = [HUB_PREFIX + tag_name_]
    parts.extend(f"{item_id}:{pos}" for pos, item_id in pairs)
    parts.append(HUB_TAG_MARKER + tag_name_)
    parts.append(str(icon))
    parts.extend(
        str(i) for i in tag_tail([item_id for _, item_id in pairs], exact_only, equivalents)
    )
    return ",".join(parts)


def build_official_import_string(layout: dict[str, int]) -> str:
    """The official client's own bank tag string, added 15 July 2026.

        1,<itemCount>,(<itemId>,<column>,<row>)*

    Same 8-wide grid, but positions are split into column and row, and the
    string carries no name or icon - those are set on the tag in game. Format
    taken from the wiki's own RuneLite-to-official converter
    (`MediaWiki:Gadget-banktag-converter-core.js`).
    """
    pairs = sorted(layout.items(), key=lambda kv: int(kv[0]))
    parts = ["1", str(len(pairs))]
    for pos, item_id in pairs:
        parts.extend([str(item_id), str(int(pos) % 8), str(int(pos) // 8)])
    return ",".join(parts)


def parse_hub_import_string(code: str) -> dict:
    """`{tagName, icon, layout, tagItems}`, parsed the way the plugins parse it.

    Mirrors `TabInterface.importBtlTag`: split on commas, take the name off the
    prefix, read `<itemId>:<pos>` pairs until the `banktag:` marker, then the
    icon, then the tagged items. Raises ValueError on anything neither importer
    would accept, so validation fails loudly rather than shipping a dud string.
    """
    if not code.startswith(HUB_PREFIX):
        raise ValueError("missing banktaglayoutsplugin: prefix")
    # RuneLite splits with Guava's omitEmptyStrings().trimResults(), so an empty
    # field would be dropped rather than shifting the pairs - and the shift is
    # what a player would see. Reject it here instead.
    parts = code.split(",")
    if any(part.strip() != part or not part for part in parts):
        raise ValueError("empty or padded field")

    tag_name_ = parts[0][len(HUB_PREFIX) :]
    layout: dict[str, int] = {}
    i = 1
    while i < len(parts) and not parts[i].startswith(HUB_TAG_MARKER):
        item_s, _, pos_s = parts[i].partition(":")
        if not item_s.isdigit() or not pos_s.isdigit():
            raise ValueError(f"bad layout pair {parts[i]!r}")
        if pos_s in layout:
            raise ValueError(f"position {pos_s} used twice")
        layout[pos_s] = int(item_s)
        i += 1
    if i >= len(parts):
        raise ValueError("missing banktag: section")
    if parts[i][len(HUB_TAG_MARKER) :] != tag_name_:
        raise ValueError("layout name and tag name disagree")
    i += 1
    if i >= len(parts) or not parts[i].isdigit():
        raise ValueError("missing icon")
    icon = int(parts[i])
    tag_items = []
    for part in parts[i + 1 :]:
        # A leading minus is a variation tag; anything else non-numeric would
        # throw out of Integer.parseInt and fail the whole import.
        if not part.removeprefix("-").isdigit():
            raise ValueError(f"bad tagged item {part!r}")
        tag_items.append(int(part))
    return {
        "tagName": tag_name_,
        "icon": icon,
        "layout": layout,
        "tagItems": tag_items,
    }


def parse_official_import_string(code: str) -> dict[str, int]:
    """position -> itemId, from an official-client string. Raises on garbage."""
    parts = code.split(",")
    if len(parts) < 2 or parts[0] != "1":
        raise ValueError("missing version 1 header")
    if not parts[1].isdigit():
        raise ValueError("non-numeric item count")
    count = int(parts[1])
    body = parts[2:]
    if len(body) != count * 3:
        raise ValueError(f"count says {count} items, body carries {len(body) / 3}")
    layout: dict[str, int] = {}
    for item_s, col_s, row_s in zip(body[0::3], body[1::3], body[2::3]):
        if not (item_s.isdigit() and col_s.isdigit() and row_s.isdigit()):
            raise ValueError("non-numeric triple")
        col, row = int(col_s), int(row_s)
        if col > 7:
            raise ValueError(f"column {col} is off an 8-wide grid")
        pos = str(row * 8 + col)
        if pos in layout:
            raise ValueError(f"position {pos} used twice")
        layout[pos] = int(item_s)
    return layout


def choose_icon(setup: dict) -> str:
    """A recognisable tab icon: the weapon, else the first inventory item."""
    weapon = setup["equipment"].get("weapon")
    if weapon:
        return weapon
    for n in range(1, 29):
        item = setup["inventory"].get(str(n))
        if item:
            return item
    for slot in EQUIPMENT_SLOTS:
        if setup["equipment"].get(slot):
            return setup["equipment"][slot]
    return ""


def resolve_alternatives(
    client: WikiClient, jobs: list[dict], result_for: dict[str, dict],
    relay: dict[int, int],
) -> None:
    """Give every ranked option the id the wiki would have given it.

    Resolution goes back through `Module:Loadout` rather than through our own
    item index, so an item picked with the arrows is the same id the wiki would
    have published had it been rank 1. A loadout holds one item per slot, so the
    ladder is walked in depth order: one synthetic loadout per rung, each
    carrying every slot's option at that depth.
    """
    rungs: list[dict] = []
    for job in jobs:
        alts = job["altNames"]
        if not alts or job["key"] not in result_for:
            continue
        depth = max(len(options) for options in alts.values())
        for k in range(depth):
            slots = {
                slot: options[k] for slot, options in alts.items() if k < len(options)
            }
            if slots:
                rungs.append({"key": f"{job['key']}A{k:02d}", "parent": job["key"],
                              "depth": k, "slots": slots})
    if not rungs:
        return

    resolved: dict[str, dict[int, dict[str, int]]] = {}
    for start in range(0, len(rungs), BATCH_SIZE):
        batch = rungs[start : start + BATCH_SIZE]
        text = "\n".join(
            build_loadout_wikitext(
                rung["key"], "", {"equipment": rung["slots"], "inventory": {}, "runes": {}}
            )
            for rung in batch
        )
        rendered = client.expand_templates(text)
        codes = {}
        for code in CODE_RE.findall(rendered):
            parts = code.split(",")
            if len(parts) > 2:
                codes[parts[2]] = code.strip()
        for rung in batch:
            code = codes.get(rung["key"])
            if not code:
                continue
            equipment, _, _ = split_layout(parse_import_string(code, relay))
            resolved.setdefault(rung["parent"], {})[rung["depth"]] = equipment
        print(f"  options {min(start + BATCH_SIZE, len(rungs))}/{len(rungs)}", end="\r")

    for job in jobs:
        by_depth = resolved.get(job["key"])
        if not by_depth:
            continue
        ladders: dict[str, list[int]] = {}
        for slot in job["altNames"]:
            ids: list[int] = []
            for depth in sorted(by_depth):
                item_id = by_depth[depth].get(slot)
                # An option the wiki cannot resolve is dropped rather than
                # guessed at; the rest of the ladder is still usable.
                if item_id and item_id not in ids:
                    ids.append(item_id)
            if len(ids) > 1:
                ladders[slot] = ids
        result_for[job["key"]]["alternatives"] = ladders


def resolve_switches(
    client: WikiClient, jobs: list[dict], result_for: dict[str, dict],
    relay: dict[int, int],
) -> None:
    """Give every spec weapon the id the wiki would have given it.

    A switch occupies no worn slot, so it is resolved in the inventory of a
    throwaway loadout - the same `Module:Loadout` pass the worn items take, so a
    switch and a weapon naming the same item can never disagree.
    """
    pending = [j for j in jobs if j["switchNames"] and j["key"] in result_for]
    if not pending:
        return

    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        text = "\n".join(
            build_loadout_wikitext(
                f"{j['key']}S",
                "",
                {
                    "equipment": {},
                    "inventory": {
                        str(n): name
                        for n, name in enumerate(j["switchNames"][:28], start=1)
                    },
                    "runes": {},
                },
            )
            for j in batch
        )
        rendered = client.expand_templates(text)
        codes = {}
        for code in CODE_RE.findall(rendered):
            parts = code.split(",")
            if len(parts) > 2:
                codes[parts[2]] = code.strip()

        for job in batch:
            code = codes.get(f"{job['key']}S")
            if not code:
                continue
            _, inventory, _ = split_layout(parse_import_string(code, relay))
            resolved = []
            for n, name in enumerate(job["switchNames"][:28], start=1):
                item_id = inventory.get(str(n))
                # A name the wiki cannot resolve is dropped rather than guessed
                # at; the rest of the list is still usable.
                if item_id:
                    resolved.append({"name": name, "id": item_id})
            result_for[job["key"]]["switches"] = resolved
        print(f"  switches {min(start + BATCH_SIZE, len(pending))}/{len(pending)}", end="\r")


def add_supplies(
    client: WikiClient,
    results: list[dict],
    norm: Normalizer,
    relay: dict[int, int],
    tagging: dict,
) -> list[str]:
    """Lay each clue tier's supply groups out below its layout.

    Returns every name the wiki could not resolve. Those are reported rather
    than guessed at: the source columns mix items with page links ("Graceful
    outfit", "Blast Mine"), and a bank tag cannot hold a page.
    """
    for entry in results:
        entry["supplies"] = []
    if not SUPPLIES.exists():
        return []
    sources = json.loads(SUPPLIES.read_text(encoding="utf-8"))
    clue = [e for e in results if e["activity"] == SUPPLY_ACTIVITY]
    planned = {id(e): groups_for(e["variant"], sources) for e in clue}
    # The dose fix applies here as everywhere: a bare "Ranging potion" would
    # otherwise resolve to the 3-dose one.
    named = {
        n: norm.normalize(n)[0]
        for groups in planned.values() for g in groups for n in g["names"]
    }
    ids = resolve_names(client, list(named.values()), relay)

    dropped: list[str] = []
    for entry in clue:
        used = set(entry["layout"].values())
        supplies = []
        for group in planned[id(entry)]:
            items = []
            for name in group["names"]:
                item_id = ids.get(named[name])
                if not item_id:
                    dropped.append(f"{entry['variant']} / {group['group']}: {name}")
                    continue
                # Already in the layout or an earlier group: one copy is enough.
                if item_id in used:
                    continue
                used.add(item_id)
                items.append({"name": named[name], "id": item_id})
            if items:
                supplies.append(
                    {"group": group["group"], "source": group["source"], "items": items}
                )
        entry["supplies"] = supplies
        rows = [[i["id"] for i in g["items"]] for g in supplies]
        entry["layout"] = place_supplies(entry["layout"], rows)
        entry["layoutZigzag"] = place_supplies(entry["layoutZigzag"], rows)
        icon, name = entry["icon"], entry["tagName"]
        entry["importString"] = build_hub_import_string(name, icon, entry["layout"], **tagging)
        entry["importStringZigzag"] = build_hub_import_string(
            name, icon, entry["layoutZigzag"], **tagging
        )
        entry["importStringOfficial"] = build_official_import_string(entry["layout"])
        entry["importStringZigzagOfficial"] = build_official_import_string(
            entry["layoutZigzag"]
        )
    return dropped


def library_tagging(
    results: list[dict], exact_only: set[int], equivalents: dict[int, list[int]]
) -> dict:
    reachable: set[int] = set()
    for entry in results:
        reachable |= set(entry["layout"].values())
        for options in entry["alternatives"].values():
            reachable |= set(options)
    eq = {i: equivalents[i] for i in sorted(reachable) if i in equivalents}
    reachable |= {e for ids in eq.values() for e in ids}
    return {
        "exactOnly": sorted(reachable & exact_only),
        "equivalents": {str(i): ids for i, ids in eq.items()},
    }


def resolve_names(
    client: WikiClient, names: list[str], relay: dict[int, int]
) -> dict[str, int]:
    """name -> the id `Module:Loadout` gives it, via throwaway inventories.

    The same pass worn items take, so a curated name and a layout naming the
    same item can never disagree about its id. A name the wiki cannot resolve
    is simply absent from the result.
    """
    names = list(dict.fromkeys(names))
    chunks = [names[i : i + 28] for i in range(0, len(names), 28)]
    out: dict[str, int] = {}
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = list(enumerate(chunks[start : start + BATCH_SIZE], start=start))
        text = "\n".join(
            build_loadout_wikitext(
                f"BTLNAMES{n:04d}",
                "",
                {
                    "equipment": {},
                    "inventory": {str(k): name for k, name in enumerate(chunk, start=1)},
                    "runes": {},
                },
            )
            for n, chunk in batch
        )
        rendered = client.expand_templates(text)
        codes = {}
        for code in CODE_RE.findall(rendered):
            parts = code.split(",")
            if len(parts) > 2:
                codes[parts[2]] = code.strip()
        for n, chunk in batch:
            code = codes.get(f"BTLNAMES{n:04d}")
            if not code:
                continue
            _, inventory, _ = split_layout(parse_import_string(code, relay))
            for k, name in enumerate(chunk, start=1):
                if inventory.get(str(k)):
                    out[name] = inventory[str(k)]
    return out


def load_equivalents(
    client: WikiClient, relay: dict[int, int], path: Path | None = None
) -> tuple[dict[int, list[int]], list[str]]:
    """`equivalents.json` as id -> [ids], plus every name that did not resolve."""
    path = path or EQUIVALENTS
    if not path.exists():
        return {}, []
    rules = json.loads(path.read_text(encoding="utf-8")).get("equivalents", [])
    names = [n for r in rules for n in (r["item"], *r["accept"])]
    ids = resolve_names(client, names, relay)
    unresolved = [n for n in dict.fromkeys(names) if n not in ids]
    mapping: dict[int, list[int]] = {}
    for rule in rules:
        if rule["item"] not in ids:
            continue
        accepted = [ids[n] for n in rule["accept"] if n in ids]
        mapping.setdefault(ids[rule["item"]], [])
        mapping[ids[rule["item"]]] += [i for i in accepted if i not in mapping[ids[rule["item"]]]]
    return mapping, unresolved


def main() -> None:
    client = WikiClient()
    index = load_item_index(client)
    norm = Normalizer(index)
    data = json.loads(SETUPS.read_text(encoding="utf-8"))
    relay, moved = load_relay(client)
    for note in moved:
        print(f"  {MODULE_PAGE} moved {note}")
    equivalents, unresolved_equivalents = load_equivalents(client, relay)
    # Doses and charges: tagging their variants would pull every part-used
    # potion and part-charged jewellery into the tab.
    exact_only = {
        int(item_id) for item_id, name in index["byId"].items() if DOSE_RE.match(name)
    }
    tagging = {"exact_only": exact_only, "equivalents": equivalents}

    jobs = []
    for page in data["pages"]:
        activity = activity_name(page["page"])
        for setup in page["setups"]:
            normalized, notes = normalize_setup(setup, norm)
            name = tag_name(activity, setup["variant"])
            jobs.append(
                {
                    "page": page["page"],
                    "revId": page["revId"],
                    "activity": activity,
                    "variant": setup["variant"],
                    "tagName": name,
                    "sourceNames": normalized,
                    "altNames": normalize_alternatives(
                        setup.get("alternatives", {}), norm
                    ),
                    "switchNames": normalize_switches(setup.get("switches", []), norm),
                    "prepotNames": normalize_switches(setup.get("prepots", []), norm),
                    "notes": notes + dynamic_notes(normalized),
                    "curated": setup.get("curated", False),
                    "curationReason": setup.get("curationReason", ""),
                }
            )

    # Display names collide once truncated ("Fortis Colosseum Melee only (not
    # recommended for..." appears twice), so they cannot key a batched response
    # - doing so silently pairs one setup's items with another's. Each job gets
    # a unique synthetic key instead, and the real name is substituted back into
    # the finished string afterwards.
    used: dict[str, int] = {}
    for i, job in enumerate(jobs):
        job["key"] = f"BTLKEY{i:05d}"
        base = job["tagName"]
        used[base] = used.get(base, 0) + 1
        if used[base] > 1:
            job["tagName"] = f"{base[:56]} {used[base]}".strip()

    # Prepots wear no slot either, so they resolve the way supplies do.
    prepot_ids = resolve_names(
        client, [n for j in jobs for n in j["prepotNames"]], relay
    )

    results = []
    result_for: dict[str, dict] = {}
    failures = []
    for start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[start : start + BATCH_SIZE]
        text = "\n".join(
            build_loadout_wikitext(j["key"], choose_icon(j["sourceNames"]), j["sourceNames"])
            for j in batch
        )
        rendered = client.expand_templates(text)
        codes = {}
        for code in CODE_RE.findall(rendered):
            parts = code.split(",")
            if len(parts) > 2:
                codes[parts[2]] = code.strip()

        for job in batch:
            code = codes.get(job["key"])
            if code:
                # Swap the synthetic key for the player-facing tag name.
                parts = code.split(",")
                parts[2] = job["tagName"]
                code = ",".join(parts)
            if not code:
                failures.append(f"{job['page']} / {job['variant']}: no loadout code returned")
                continue
            layout = parse_import_string(code, relay)
            if not layout:
                failures.append(f"{job['page']} / {job['variant']}: empty layout")
                continue
            equipment, inventory, runes = split_layout(layout)
            # A name the wiki cannot resolve is dropped here; validation then
            # fails the curated entry that named it.
            prepots = [
                {"name": n, "id": prepot_ids[n]}
                for n in job["prepotNames"] if n in prepot_ids
            ]
            ids = [p["id"] for p in prepots]
            layout = place_prepots(layout, ids)
            zigzag = zigzag_layout(equipment, inventory, runes, ids)
            status, note = completeness(job["activity"], equipment, inventory)
            icon_id = int(code.split(",")[3]) if code.split(",")[3].isdigit() else 0
            result_for[job["key"]] = {
                "activity": job["activity"],
                "sourcePage": job["page"],
                "sourceRevId": job["revId"],
                "variant": job["variant"],
                "tagName": job["tagName"],
                "icon": icon_id,
                # Built from the parsed layout rather than passed through from
                # the wiki, so both styles and both clients come out of one
                # place and cannot disagree about what the layout is.
                "importString": build_hub_import_string(
                    job["tagName"], icon_id, layout, **tagging
                ),
                "importStringZigzag": build_hub_import_string(
                    job["tagName"], icon_id, zigzag, **tagging
                ),
                "importStringOfficial": build_official_import_string(layout),
                "importStringZigzagOfficial": build_official_import_string(zigzag),
                "layout": layout,
                "layoutZigzag": zigzag,
                "completeness": status,
                "completenessNote": note,
                "curated": job["curated"],
                "curationReason": job["curationReason"],
                "equipment": equipment,
                "inventory": inventory,
                "runes": runes,
                # Filled by resolve_alternatives once every option has an id.
                "alternatives": {},
                # Likewise, by resolve_switches.
                "switches": [],
                "prepots": prepots,
                # Kept so validation can round-trip every name back to the
                # id the wiki actually chose for it.
                "sourceNames": job["sourceNames"],
                "sourcePrepots": job["prepotNames"],
                "warnings": job["notes"],
            }
            results.append(result_for[job["key"]])
        print(f"  encoded {min(start + BATCH_SIZE, len(jobs))}/{len(jobs)}", end="\r")

    resolve_alternatives(client, jobs, result_for, relay)
    resolve_switches(client, jobs, result_for, relay)
    supply_dropped = add_supplies(client, results, norm, relay, tagging)

    # Which weapon options need both hands. The site clears the off-hand when
    # one is chosen and restores it on the way back, so stepping through the
    # weapon ladder can never produce a loadout nobody can wear.
    two_handed = set(load_slot_index(client)["twoHandedIds"])
    for entry in results:
        options = entry["alternatives"].get("weapon", [])
        worn = entry["equipment"].get("weapon")
        entry["twoHandedWeapons"] = sorted(
            {i for i in [*options, worn] if i and i in two_handed}
        )

    # What extraction had to throw away never reached any published artifact,
    # so a page that quietly stopped yielding a setup was invisible. Carry it
    # forward so `report.json` can say what was skipped and why.
    extraction = {
        "emptyPages": data.get("emptyPages", []),
        "warnings": {
            page["page"]: page["warnings"] for page in data["pages"] if page["warnings"]
        },
    }

    ENCODED.write_text(
        json.dumps(
            {
                "layouts": results,
                "failures": failures,
                "extraction": extraction,
                "upstreamGridMoves": moved,
                # What the site needs to build the same tag tail after a swap:
                # only the ids this library can actually reach, not the index.
                "tagging": library_tagging(results, exact_only, equivalents),
                "unresolvedEquivalents": unresolved_equivalents,
                "supplyDropped": supply_dropped,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nencode: {len(results)} layouts, {len(failures)} failures")
    print(f"  {len(extraction['emptyPages'])} pages yielded no setup, "
          f"{sum(len(w) for w in extraction['warnings'].values())} extraction warnings")
    print(f"  http={client.stats['http']} cache={client.stats['cache']}")
    print(f"  wrote {ENCODED.relative_to(REPO)}")


if __name__ == "__main__":
    main()
