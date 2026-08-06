"""Stage 2 - extract: turn strategy wikitext into structured gear setups.

Pages encode setups two different ways, and both are common enough that
guessing is not acceptable:

* **Tabber** (Doom, Nex, Zulrah) - one `<tabber>` chunk per playstyle, each
  holding `{{Equipment}}` then `{{Inventory}}` then optional `{{Rune pouch}}`.
* **Wikitable** (Chambers of Xeric) - a table whose `!` header cells name the
  variants, with a row of five `{{Equipment}}` blocks followed by a row of five
  `{{Inventory}}` blocks.

Pairing therefore works per *scope* (a tabber chunk, or a heading section).
Within a scope, if the equipment and inventory counts match they are zipped in
document order - which is correct for interleaved and blocked layouts alike.
When the counts disagree we fall back to adjacency and report what went
unclaimed, so a stray illustrative block (Nex's transportation gear) is dropped
loudly rather than silently welded onto the wrong setup.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from normalize import load_slot_index
from wikiclient import WikiClient

REPO = Path(__file__).parent.parent
CORPUS = REPO / "generator" / "corpus.json"
SETUPS = REPO / "generator" / "setups.json"

TEMPLATES = ("Equipment", "Recommended equipment", "Inventory", "Rune pouch")

# Equipment slots Module:Loadout understands. `*quantity` args are display-only.
EQUIPMENT_SLOTS = (
    "head", "cape", "neck", "ammo", "ammo2", "weapon",
    "torso", "legs", "shield", "gloves", "boots", "ring",
)

# {{Recommended equipment}} is the template most strategy pages actually use
# (93 of 99). It names slots differently from {{Equipment}} and ranks options
# per slot as head1..head4, best first.
RECOMMENDED_SLOT_MAP = {
    "head": "head",
    "neck": "neck",
    "cape": "cape",
    "ammo": "ammo",
    "weapon": "weapon",
    "body": "torso",
    "legs": "legs",
    "shield": "shield",
    "hands": "gloves",
    "feet": "boots",
    "ring": "ring",
    # Two-handers live under `2h`, not `weapon`. Missing this cost Kree'arra its
    # Twisted bow on a variant literally named "Tbow / Bowfa".
    "2h": "weapon",
    # `special` lists spec-attack weapons, which have no worn slot to occupy.
}

# A slot name may start with a digit ("2h"), so the leading class cannot be
# letters-only. The rank is always the trailing digits.
RANKED_SLOT_RE = re.compile(r"^(?P<slot>[a-z0-9]+?)(?P<rank>\d*)$")

# When a page lists both `weapon1` and `2h1` they are alternatives at equal
# rank; prefer the one-handed entry so the shield slot stays meaningful.
SLOT_SOURCE_PRIORITY = {"weapon": 0, "2h": 1}
PLINK_RE = re.compile(r"\{\{\s*plink[a-z]*\b", re.IGNORECASE)

# A slot value of "None if using two-handed weapon, such as {{plinkp|Toxic
# blowpipe}}" says the slot stays *empty*. The linked item is an example of what
# empties it, not something to wear - read literally it put a blowpipe in the
# shield slot. "Empty" is deliberately absent: Empty bucket and Empty jug are
# 24 real items.
EMPTY_SLOT_RE = re.compile(r"^\s*(none|nothing|n/a)\b", re.IGNORECASE)

# Lowercased names of every item the wiki records as occupying the `2h` slot.
# Populated by main() from the wiki's own infoboxes; tests inject their own.
TWO_HANDED_NAMES: set[str] = set()

# Lowercased item name -> the slot the wiki says it occupies. Used to throw out
# options that a slot's prose merely mentions: Gemstone Crab lists "darts with
# {{plink|Twisted buckler}}" under `weapon`, and a buckler is not a weapon.
SLOT_OF_NAME: dict[str, str] = {}

# Only these two slots are filtered. The wiki deliberately lists Amethyst dart
# under `ammo` - blowpipe ammunition - although a dart's own slot is `weapon`,
# and that convention is correct where it appears.
STRICT_SLOTS = ("weapon", "shield")

HEADING_RE = re.compile(r"(?m)^(=+)\s*(.+?)\s*\1\s*$")
TAB_LABEL_RE = re.compile(r"(?m)^([^=|{}\n]{1,60}?)\s*=\s*$")
TABLE_HEADER_RE = re.compile(r"(?m)^!(.+)$")

# A section whose heading names a loadout ("Example melee setup", "Equipment")
# is publishing gear on purpose. One that does not ("Transportation") is
# illustrating something else and must not become a bank tag.
SETUP_HEADING_RE = re.compile(r"setup|gear|equipment|loadout|inventory", re.IGNORECASE)


def clean_item(value: str) -> str:
    """Mirror Module:Loadout._cleanItem / Module:Inventory value parsing.

    Values look like ``ItemName``, ``[[Item Name]]``, ``Item;link`` or
    ``Item\\5`` (quantity). Brackets are stripped and the name is everything
    before the first ``;`` or ``\\``.
    """
    if value is None:
        return ""
    v = value.replace("[", "").replace("]", "").strip()
    v = re.split(r"[;\\]", v, maxsplit=1)[0].strip()
    if not v:
        return ""
    m = re.fullmatch(r"\{\{\s*[Pp]link[^|]*\|([^|}]+).*\}\}", v)
    if m:
        v = m.group(1).strip()
    v = re.sub(r"<[^>]+>", "", v).strip()
    v = v.strip("'\" ")
    if not v:
        return ""
    return v[0].upper() + v[1:]


def strip_templates(text: str, names: tuple[str, ...]) -> str:
    """Remove whole `{{name|...}}` calls, honouring nesting."""
    pattern = re.compile(r"\{\{\s*(" + "|".join(names) + r")\b", re.IGNORECASE)
    while True:
        m = pattern.search(text)
        if not m:
            return text
        depth, i = 0, m.start()
        while i < len(text):
            if text[i : i + 2] == "{{":
                depth += 1
                i += 2
            elif text[i : i + 2] == "}}":
                depth -= 1
                i += 2
                if depth == 0:
                    break
            else:
                i += 1
        if depth != 0:
            return text
        text = text[: m.start()] + text[i:]


def plink_items(value: str) -> list[str]:
    """Every real item named by a slot value, in the order it lists them.

    `{{plink|Barrows equipment|pic=Torag's platebody}}` points at a category
    page, so `pic` - the icon actually drawn - is the truer item name.

    A single rank often names several interchangeable items ("Max cape /
    Hitpoints cape", "mace / axe > hasta"); 24% of ranks do. The first is the
    one the layout uses, and the rest are alternatives a player can step to.
    """
    if not value:
        return []
    value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    # A <ref> is a footnote about the slot, not a list of things to wear. Callisto
    # qualifies a crossbow with "Only if using {{plink|Ruby bolts (e)}}", and
    # mining that put bolts in the weapon ladder.
    value = re.sub(r"<ref\b[^>]*/>", "", value)
    value = re.sub(r"<ref\b.*?</ref>", "", value, flags=re.DOTALL | re.IGNORECASE)
    value = strip_templates(value, ("efn", "efn2", "GEP", "NoCoins"))

    if EMPTY_SLOT_RE.match(value):
        return []

    out: list[str] = []
    for m in PLINK_RE.finditer(value):
        parsed = parse_template(value, m.start())
        if not parsed:
            continue
        _, args, _ = parsed
        name = clean_item(args.get("pic") or args.get("1") or "")
        if name and name not in out:
            out.append(name)
    if out:
        return out

    # No plink at all: a bare wiki link is still a real reference, unless it
    # points within the page. Abyssal Sire's ammo slot reads
    # "[[#Phase 1 equipment|...]]", which is a cross-reference, not an item.
    link = re.search(r"\[\[([^|\]]+)", value)
    if link and not link.group(1).lstrip().startswith("#"):
        name = clean_item(link.group(1))
        return [name] if name else []
    return []


def plink_item(value: str) -> str:
    """The item a slot value resolves to - its first named item."""
    items = plink_items(value)
    return items[0] if items else ""


def recommended_equipment(args: dict[str, str]) -> dict[str, str]:
    """Best-ranked item per slot from a {{Recommended equipment}} block.

    Derived from the full ranked list rather than computed alongside it, so the
    published loadout is always the head of the ladder the site steps through -
    they cannot drift apart.
    """
    return {
        slot: options[0]
        for slot, options in recommended_alternatives(args).items()
        if options
    }


def recommended_alternatives(args: dict[str, str]) -> dict[str, list[str]]:
    """Every option a {{Recommended equipment}} block offers, best first.

    The template ranks each slot as `head1`..`head4`, and `recommended_equipment`
    keeps only rank 1 - which is the whole loadout a player is offered. 90% of
    filled slots list more than one option, so the rest is the ladder someone
    without best-in-slot needs to climb down.

    Ordering is the wiki's: by rank, then by the order items appear within a
    rank. `weapon` and `2h` merge into one list under the same tie-break as
    `recommended_equipment`, since they are alternatives for the same hand.
    """
    ranked: dict[str, list[tuple[int, int, str]]] = {}
    for key, raw in args.items():
        m = RANKED_SLOT_RE.match(key)
        if not m:
            continue
        source = m.group("slot")
        slot = RECOMMENDED_SLOT_MAP.get(source)
        if not slot:
            continue
        rank = int(m.group("rank")) if m.group("rank") else 1
        priority = SLOT_SOURCE_PRIORITY.get(source, 0)
        for item in plink_items(raw):
            ranked.setdefault(slot, []).append((rank, priority, item))

    out: dict[str, list[str]] = {}
    for slot, entries in ranked.items():
        seen: list[str] = []
        for _, _, item in sorted(entries, key=lambda e: (e[0], e[1])):
            if item in seen:
                continue
            if not fits_slot(item, slot):
                continue
            seen.append(item)
        if seen:
            out[slot] = seen
    return out


def fits_slot(item: str, slot: str) -> bool:
    """Could this item be worn in this slot, according to the wiki?

    A slot's value is prose as often as it is a list, and the prose names items
    that go elsewhere: "darts with {{plink|Twisted buckler}}", "(with
    {{plinkp|Tome of fire}})". Those read as alternatives and are not.

    Only `weapon` and `shield` are judged, and only when the item's slot is
    known - see STRICT_SLOTS.
    """
    if slot not in STRICT_SLOTS or not SLOT_OF_NAME:
        return True
    known = SLOT_OF_NAME.get(item.lower())
    if known is None:
        return True
    return ("weapon" if known == "2h" else known) == slot


def split_args(body: str) -> list[str]:
    """Split a template body on top-level ``|`` only."""
    parts: list[str] = []
    buf: list[str] = []
    brace = bracket = 0
    i = 0
    while i < len(body):
        two = body[i : i + 2]
        if two in ("{{", "[["):
            if two == "{{":
                brace += 1
            else:
                bracket += 1
            buf.append(two)
            i += 2
            continue
        if two in ("}}", "]]"):
            if two == "}}":
                brace -= 1
            else:
                bracket -= 1
            buf.append(two)
            i += 2
            continue
        ch = body[i]
        if ch == "|" and brace == 0 and bracket == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def parse_template(text: str, start: int) -> tuple[str, dict[str, str], int] | None:
    """Parse the template beginning at ``start`` (index of ``{{``).

    Positional arguments get their own counter, exactly as MediaWiki does:
    ``{{Inventory|align=right|Shark|Lobster}}`` makes Shark argument ``1``,
    not ``2``.
    """
    depth = 0
    i = start
    while i < len(text):
        if text[i : i + 2] == "{{":
            depth += 1
            i += 2
            continue
        if text[i : i + 2] == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                break
            continue
        i += 1
    if depth != 0:
        return None

    inner = text[start + 2 : i - 2]
    chunks = split_args(inner)
    name = chunks[0].strip()

    args: dict[str, str] = {}
    positional = 0
    for chunk in chunks[1:]:
        key, sep, value = chunk.partition("=")
        # A "=" only makes it named when the key is a plain identifier.
        if sep and re.fullmatch(r"[A-Za-z0-9 _-]{1,30}", key.strip()):
            args[key.strip().lower()] = value.strip()
        else:
            positional += 1
            args[str(positional)] = chunk.strip()
    return name, args, i


@dataclass
class Occurrence:
    name: str
    args: dict[str, str]
    start: int
    end: int


@dataclass
class Scope:
    start: int
    end: int
    label: str
    kind: str  # "tab" | "section"


@dataclass
class Setup:
    variant: str
    equipment: dict[str, str] = field(default_factory=dict)
    inventory: dict[str, str] = field(default_factory=dict)
    runes: dict[str, str] = field(default_factory=dict)
    # Ranked options per slot, best first, when the source ranked them. Empty
    # for {{Equipment}}, which is one hand-authored loadout with no ladder.
    alternatives: dict[str, list[str]] = field(default_factory=dict)
    order: int = 0  # document position, for page-level merging


def find_occurrences(text: str) -> list[Occurrence]:
    out: list[Occurrence] = []
    pattern = r"\{\{\s*(" + "|".join(t.replace(" ", r"\s") for t in TEMPLATES) + r")\b"
    for m in re.finditer(pattern, text, re.IGNORECASE):
        parsed = parse_template(text, m.start())
        if not parsed:
            continue
        name, args, end = parsed
        canonical = re.sub(r"\s+", " ", name.strip().lower())
        for t in TEMPLATES:
            if canonical == t.lower():
                out.append(Occurrence(t, args, m.start(), end))
                break
    out.sort(key=lambda o: o.start)
    return out


def build_scopes(text: str) -> list[Scope]:
    """Tabber chunks take priority; everything else falls back to sections."""
    scopes: list[Scope] = []

    for block in re.finditer(r"<tabber>(.*?)</tabber>", text, re.DOTALL | re.IGNORECASE):
        body, base = block.group(1), block.start(1)
        bounds = [0]
        for sep in re.finditer(r"(?m)^\|-\|\s*$", body):
            bounds.append(sep.start())
            bounds.append(sep.end())
        bounds.append(len(body))
        for a, b in zip(bounds[0::2], bounds[1::2]):
            chunk = body[a:b]
            label_match = TAB_LABEL_RE.search(chunk)
            label = label_match.group(1).strip() if label_match else ""
            scopes.append(Scope(base + a, base + b, label, "tab"))

    headings = list(HEADING_RE.finditer(text))
    for idx, m in enumerate(headings):
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        scopes.append(Scope(m.end(), end, m.group(2).strip(), "section"))
    if not headings:
        scopes.append(Scope(0, len(text), "Setup", "section"))

    return scopes


def scope_for(pos: int, scopes: list[Scope]) -> Scope | None:
    """Innermost scope containing `pos`; tabs win over sections."""
    best: Scope | None = None
    for s in scopes:
        if s.start <= pos < s.end:
            if best is None:
                best = s
            elif s.kind == "tab" and best.kind == "section":
                best = s
            elif s.kind == best.kind and (s.end - s.start) < (best.end - best.start):
                best = s
    return best


def table_headers(text: str, scope: Scope, count: int) -> list[str] | None:
    """Header cells of a wikitable inside `scope`, if they match `count`."""
    region = text[scope.start : scope.end]
    for table in re.finditer(r"^\{\|(.*?)^\|\}", region, re.DOTALL | re.MULTILINE):
        headers: list[str] = []
        for m in TABLE_HEADER_RE.finditer(table.group(1)):
            for cell in m.group(1).split("!!"):
                cell = re.sub(r"\[\[([^|\]]*\|)?([^\]]+)\]\]", r"\2", cell)
                cell = re.sub(r"^[^|]*\|(?!\|)", "", cell) if "|" in cell else cell
                cell = re.sub(r"<[^>]+>", "", cell).strip().strip("'")
                if cell:
                    headers.append(cell)
        if len(headers) == count:
            return headers
    return None


def _slots(occ: Occurrence | None, keys, limit: int | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    if occ is None:
        return out
    for key in keys:
        item = clean_item(occ.args.get(key, ""))
        if item:
            out[key] = item
    return out


def _merge_shared(setups: list[Setup]) -> list[Setup]:
    """Join gear-only and inventory-only runs that belong to each other.

    Pages share one half across several of the other, in both directions:

    * **N gear, 1 inventory** - Abyss tabs two gear sets then gives a single
      ``===Inventory===`` for both; Gemstone Crab tabs four melee tiers then one
      ``====Melee inventory====``.
    * **1 gear, N inventories** - Vardorvis has one gear table then Normal and
      Awakened inventories; Lizardman shaman has one per combat style then an
      inventory per location.

    Runs must be adjacent in document order; anything already complete resets
    the pairing so a finished setup never steals a later inventory.
    """
    runs: list[tuple[str, list[Setup]]] = []
    for setup in setups:
        if setup.equipment and not setup.inventory:
            kind = "gear"
        elif setup.inventory and not setup.equipment:
            kind = "inv"
        else:
            kind = "both"
        if runs and runs[-1][0] == kind:
            runs[-1][1].append(setup)
        else:
            runs.append((kind, [setup]))

    out: list[Setup] = []
    i = 0
    while i < len(runs):
        kind, group = runs[i]
        nxt = runs[i + 1] if i + 1 < len(runs) else None

        if kind == "gear" and nxt and nxt[0] == "inv":
            gears, invs = group, nxt[1]
            if len(gears) == 1:
                # One gear set, several inventories: every inventory keeps its
                # own name and inherits the shared gear.
                for inv in invs:
                    inv.equipment = dict(gears[0].equipment)
                    inv.alternatives = dict(gears[0].alternatives)
                out.extend(invs)
            elif len(invs) == 1:
                for gear in gears:
                    gear.inventory = dict(invs[0].inventory)
                    if not gear.runes:
                        gear.runes = dict(invs[0].runes)
                out.extend(gears)
            elif len(gears) == len(invs):
                for gear, inv in zip(gears, invs):
                    gear.inventory = dict(inv.inventory)
                    if not gear.runes:
                        gear.runes = dict(inv.runes)
                out.extend(gears)
            else:
                # Ambiguous counts: keep both rather than guess a pairing.
                out.extend(gears)
                out.extend(invs)
            i += 2
            continue

        out.extend(group)
        i += 1

    # Last resort: an inventory that still has no gear inherits it from the
    # nearest preceding setup that does. Pages such as Lizardman shaman give one
    # gear table per combat style and then an inventory per location, and the
    # first of those locations consumes the table during scope-local pairing,
    # orphaning the rest. Only fires when gear exists earlier on the page, so
    # genuinely gearless activities (Vale Totems) are left alone.
    carried: dict[str, str] = {}
    carried_alts: dict[str, list[str]] = {}
    for setup in out:
        if setup.equipment:
            carried = setup.equipment
            carried_alts = setup.alternatives
        elif carried:
            setup.equipment = dict(carried)
            setup.alternatives = dict(carried_alts)
    return out


def drop_conflicting_offhand(
    equipment: dict[str, str], two_handed: set[str]
) -> str | None:
    """Clear the shield slot when the chosen weapon needs both hands.

    `{{Recommended equipment}}` ranks every slot independently, so a block whose
    best weapon is two-handed still lists the off-hands belonging to its
    *lower-ranked one-handed* alternatives - the wiki even annotates them, as in
    "shield1 = None if using [[two-handed weapons]]". Taking rank 1 from each
    column therefore produced a loadout nobody can wear: Tumeken's shadow beside
    an Elidinis' ward, a Scythe of vitur beside an Avernic defender.

    The page's own ranking is trusted, so the weapon stays and the shield goes.
    Returns the dropped item name, or None when nothing conflicted.
    """
    weapon = equipment.get("weapon")
    shield = equipment.get("shield")
    if not weapon or not shield:
        return None
    if weapon.lower() not in two_handed:
        return None
    del equipment["shield"]
    return shield


def extract_page(
    title: str, text: str, two_handed: set[str] | None = None
) -> tuple[list[Setup], list[str]]:
    if two_handed is None:
        two_handed = TWO_HANDED_NAMES
    occurrences = find_occurrences(text)
    scopes = build_scopes(text)
    warnings: list[str] = []
    setups: list[Setup] = []

    # Group occurrences by the scope that contains them.
    grouped: dict[int, list[Occurrence]] = {}
    scope_by_id: dict[int, Scope] = {}
    for occ in occurrences:
        s = scope_for(occ.start, scopes)
        if s is None:
            continue
        key = id(s)
        grouped.setdefault(key, []).append(occ)
        scope_by_id[key] = s

    for key, occs in grouped.items():
        scope = scope_by_id[key]
        equips = [o for o in occs if o.name in ("Equipment", "Recommended equipment")]
        invs = [o for o in occs if o.name == "Inventory"]
        pouches = [o for o in occs if o.name == "Rune pouch"]

        pairs: list[tuple[Occurrence | None, Occurrence | None]] = []

        if not invs:
            # Gear-only loadouts are real and useful, but only when the page
            # deliberately tabbed them as a playstyle. A bare {{Equipment}} in a
            # prose section (Nex's transportation gear) is illustrative, not a
            # setup, so it is dropped loudly instead of published.
            if not equips:
                continue
            if scope.kind != "tab" and not SETUP_HEADING_RE.search(scope.label):
                warnings.append(
                    f"{len(equips)} unpaired {{{{Equipment}}}} in '{scope.label}' ignored "
                    f"(no inventory in scope)"
                )
                continue
            pairs = [(e, None) for e in equips]
        elif len(equips) == len(invs):
            pairs = list(zip(equips, invs))
        else:
            # Counts disagree: pair by adjacency, report the leftovers.
            claimed: set[int] = set()
            for inv in invs:
                match = None
                for e in reversed([e for e in equips if e.end <= inv.start]):
                    if e.start not in claimed:
                        match = e
                        break
                if match:
                    claimed.add(match.start)
                pairs.append((match, inv))
            leftover = len(equips) - len(claimed)
            if leftover > 0:
                warnings.append(
                    f"{leftover} unpaired {{{{Equipment}}}} in '{scope.label}' ignored "
                    f"({len(equips)} equipment vs {len(invs)} inventory)"
                )

        headers = table_headers(text, scope, len(pairs)) if len(pairs) > 1 else None

        for idx, (eq, inv) in enumerate(pairs):
            # {{Recommended equipment}} carries a `style` ("Melee", "Tekton"),
            # which beats a generic heading like "Equipment" as a variant name.
            style = clean_item(eq.args.get("style", "")) if eq else ""

            if headers:
                variant = headers[idx]
            elif scope.kind == "tab" and scope.label:
                variant = scope.label
            elif style:
                variant = style
            else:
                variant = scope.label or "Setup"

            # The rune pouch belongs to this setup if it sits after this
            # setup's last block and before the next setup starts.
            anchor_end = inv.end if inv is not None else (eq.end if eq else 0)
            next_start = len(text)
            if idx + 1 < len(pairs):
                nxt_eq, nxt_inv = pairs[idx + 1]
                candidates = [o.start for o in (nxt_eq, nxt_inv) if o is not None]
                if candidates:
                    next_start = min(candidates)

            pouch = None
            for p in pouches:
                if anchor_end < p.start < next_start:
                    pouch = p
                    break

            setup = Setup(variant=variant, order=anchor_end)
            if eq is not None and eq.name == "Recommended equipment":
                setup.alternatives = recommended_alternatives(eq.args)
                setup.equipment = {
                    slot: options[0]
                    for slot, options in setup.alternatives.items()
                    if options
                }
            else:
                setup.equipment = _slots(eq, EQUIPMENT_SLOTS)
            setup.inventory = _slots(inv, [str(n) for n in range(1, 29)])
            setup.runes = _slots(pouch, [str(n) for n in range(1, 5)])

            if not setup.inventory and not setup.equipment:
                continue
            setups.append(setup)

    # Pages commonly tab several gear variants and then give ONE inventory in a
    # sibling section that applies to all of them (Abyss: default/Defensive gear
    # then ===Inventory===; Gemstone Crab: four melee tiers then ====Melee
    # inventory====). Scope-local pairing cannot see across that boundary, so
    # fold each shared inventory back onto the gear variants it follows.
    setups.sort(key=lambda s: s.order)
    setups = _merge_shared(setups)

    # Enforced once, after merging, so the setups that inherit their gear from
    # another variant are covered by the same pass rather than a second copy of
    # the rule.
    for setup in setups:
        dropped = drop_conflicting_offhand(setup.equipment, two_handed)
        if dropped:
            warnings.append(
                f"'{setup.variant}': dropped off-hand {dropped} - "
                f"{setup.equipment['weapon']} is two-handed"
            )

    # Many pages carry a {{Recommended equipment}} table in an ==Equipment==
    # section *and* full setups further down that already reflect it. Once the
    # complete setups exist, the standalone gear tables are duplicates, so keep
    # them only on pages that offer nothing better (Theatre of Blood, Shellbane
    # gryphon). A handful of slots is a fragment, not a loadout.
    MIN_GEAR_ONLY_PIECES = 5
    has_complete = any(s.inventory and s.equipment for s in setups)
    if has_complete:
        setups = [s for s in setups if s.inventory]
    else:
        setups = [
            s
            for s in setups
            if s.inventory or len(s.equipment) >= MIN_GEAR_ONLY_PIECES
        ]

    setups.sort(key=lambda s: s.variant)

    # Disambiguate repeated labels so every variant is addressable.
    # Case-insensitive: "Lower Level" and "Lower level" come from different
    # sections but would be indistinguishable as bank tab names.
    seen: dict[str, int] = {}
    for s in setups:
        key = s.variant.lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            s.variant = f"{s.variant} ({seen[key]})"

    return setups, warnings


def main() -> None:
    global TWO_HANDED_NAMES, SLOT_OF_NAME
    client = WikiClient()
    slot_index = load_slot_index(client)
    TWO_HANDED_NAMES = set(slot_index["twoHandedNames"])
    SLOT_OF_NAME = slot_index["nameSlot"]
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    pages_out = []
    total_setups = 0
    empty_pages: list[str] = []
    offhands_dropped = 0

    for title in corpus["pages"]:
        text, rev_id = client.page_wikitext(title)
        setups, warnings = extract_page(title, text)
        offhands_dropped += sum(1 for w in warnings if "dropped off-hand" in w)
        total_setups += len(setups)
        if not setups:
            empty_pages.append(title)
        pages_out.append(
            {
                "page": title,
                "revId": rev_id,
                "warnings": warnings,
                "setups": [
                    {
                        "variant": s.variant,
                        "equipment": s.equipment,
                        "inventory": s.inventory,
                        "runes": s.runes,
                        # Only slots that actually offer a choice; a
                        # single-option list is just the equipment again.
                        "alternatives": {
                            slot: options
                            for slot, options in s.alternatives.items()
                            if len(options) > 1
                        },
                    }
                    for s in setups
                ],
            }
        )

    SETUPS.write_text(
        json.dumps({"pages": pages_out, "emptyPages": empty_pages}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"extract: {len(pages_out)} pages -> {total_setups} setups")
    print(f"  {len(empty_pages)} pages yielded none")
    print(f"  {offhands_dropped} off-hands dropped (weapon is two-handed)")
    print(f"  http={client.stats['http']} cache={client.stats['cache']}")
    print(f"  wrote {SETUPS.relative_to(REPO)}")


if __name__ == "__main__":
    main()
