"""Stage 4 - encode: turn structured setups into bank tag import strings.

Rather than reimplement the wiki's name-to-id resolution (and inherit a second
set of bugs), we hand a synthesised `{{Loadout}}` call back to the wiki via
`action=expandtemplates` and let `Module:Loadout` do the encoding. The output is
therefore bug-compatible with what the wiki itself publishes.

Emitted format:

    banktags,1,<tagName>,<iconItemId>,layout,<pos>,<itemId>,<pos>,<itemId>,...

Positions index an 8-wide bank grid. `LOADOUT_MAP` is copied from
`Module:Loadout` and puts worn gear in columns 0-2 (shaped like the equipment
panel), leaves column 3 empty as a spacer, and lays the 28 inventory slots out
in columns 4-7 in their natural 4-wide shape.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from normalize import Normalizer, load_item_index, load_slot_index
from wikiclient import WikiClient

REPO = Path(__file__).parent.parent
SETUPS = REPO / "generator" / "setups.json"
ENCODED = REPO / "generator" / "encoded.json"

# Bank grid position -> Loadout argument name. Straight from Module:Loadout.
LOADOUT_MAP: dict[int, str] = {
    1: "head", 2: "ammo2", 4: "1", 5: "2", 6: "3", 7: "4",
    8: "cape", 9: "neck", 10: "ammo", 12: "5", 13: "6", 14: "7", 15: "8",
    16: "weapon", 17: "torso", 18: "shield", 20: "9", 21: "10", 22: "11", 23: "12",
    25: "legs", 28: "13", 29: "14", 30: "15", 31: "16",
    32: "gloves", 33: "boots", 34: "ring", 36: "17", 37: "18", 38: "19", 39: "20",
    40: "rune1", 41: "rune2", 42: "rune3", 44: "21", 45: "22", 46: "23", 47: "24",
    48: "rune4", 52: "25", 53: "26", 54: "27", 55: "28",
}

EQUIPMENT_SLOTS = (
    "head", "cape", "neck", "ammo", "ammo2", "weapon",
    "torso", "legs", "shield", "gloves", "boots", "ring",
)

BATCH_SIZE = 10
CODE_RE = re.compile(r"banktags,1,[^<\n]+")

# In-game tag names cannot contain a comma: the import format is comma
# separated, so a comma in the name silently corrupts the layout.
UNSAFE_NAME = re.compile(r"[,\n\r|{}=]")


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


def parse_import_string(code: str) -> dict[str, int]:
    """position -> itemId, from the tail of a banktags string."""
    parts = code.split(",")
    try:
        tail = parts[parts.index("layout") + 1 :]
    except ValueError:
        return {}
    layout: dict[str, int] = {}
    for pos, item in zip(tail[0::2], tail[1::2]):
        if pos.strip().isdigit() and item.strip().isdigit():
            layout[pos.strip()] = int(item.strip())
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
                  runes: dict[str, int]) -> dict[str, int]:
    """Build the plugin's ZIGZAG arrangement from resolved item ids."""
    worn = [equipment[s] for s in ZIGZAG_EQUIPMENT_ORDER if equipment.get(s)]
    inv = [inventory[str(n)] for n in range(1, 29) if inventory.get(str(n))]
    pouch = [runes[str(n)] for n in range(1, 5) if runes.get(str(n))]

    layout: dict[int, int] = {}
    i = _place(worn, layout, 0, True)
    i = _place(inv, layout, i, True)
    # The plugin lays the rune pouch out linearly, not zigzag.
    _place(pouch, layout, i, False)
    return {str(k): v for k, v in sorted(layout.items())}


def build_import_string(tag_name_: str, icon: int, layout: dict[str, int]) -> str:
    parts = ["banktags", "1", tag_name_, str(icon), "layout"]
    for pos, item_id in sorted(layout.items(), key=lambda kv: int(kv[0])):
        parts.extend([pos, str(item_id)])
    return ",".join(parts)


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
    client: WikiClient, jobs: list[dict], result_for: dict[str, dict]
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
            equipment, _, _ = split_layout(parse_import_string(code))
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


def main() -> None:
    client = WikiClient()
    index = load_item_index(client)
    norm = Normalizer(index)
    data = json.loads(SETUPS.read_text(encoding="utf-8"))

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
                    "notes": notes,
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
            layout = parse_import_string(code)
            if not layout:
                failures.append(f"{job['page']} / {job['variant']}: empty layout")
                continue
            equipment, inventory, runes = split_layout(layout)
            zigzag = zigzag_layout(equipment, inventory, runes)
            status, note = completeness(job["activity"], equipment, inventory)
            icon_id = int(code.split(",")[3]) if code.split(",")[3].isdigit() else 0
            result_for[job["key"]] = {
                "activity": job["activity"],
                "sourcePage": job["page"],
                "sourceRevId": job["revId"],
                "variant": job["variant"],
                "tagName": job["tagName"],
                "icon": icon_id,
                "importString": code,
                "importStringZigzag": build_import_string(
                    job["tagName"], icon_id, zigzag
                ),
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
                # Kept so validation can round-trip every name back to the
                # id the wiki actually chose for it.
                "sourceNames": job["sourceNames"],
                "warnings": job["notes"],
            }
            results.append(result_for[job["key"]])
        print(f"  encoded {min(start + BATCH_SIZE, len(jobs))}/{len(jobs)}", end="\r")

    resolve_alternatives(client, jobs, result_for)

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

    ENCODED.write_text(
        json.dumps({"layouts": results, "failures": failures}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nencode: {len(results)} layouts, {len(failures)} failures")
    print(f"  http={client.stats['http']} cache={client.stats['cache']}")
    print(f"  wrote {ENCODED.relative_to(REPO)}")


if __name__ == "__main__":
    main()
