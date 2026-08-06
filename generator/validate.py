"""Stage 5 - validate: prove every generated string is importable and correct.

Checks, in order of how badly they would hurt a player:

1. **Structure** - parses as `banktags,1,<name>,<icon>,layout,<pos>,<id>,...`,
   the tag name carries no comma (which would corrupt the import), positions are
   real grid slots, and no position is claimed twice.
2. **Existence** - every id is a real item.
3. **Round-trip** - the id sitting at each position is one the wiki genuinely
   resolves that slot's source name to. This is what catches a silent
   off-by-one in the position map.
4. **Dose regression** - no part-used potion where the source asked for a
   generic one. The bug this guards against affected ~a third of the library.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from encode import LOADOUT_MAP
from normalize import DOSE_RE, Normalizer, load_item_index
from wikiclient import WikiClient

REPO = Path(__file__).parent.parent
ENCODED = REPO / "generator" / "encoded.json"
REPORT = REPO / "report.json"

VALID_POSITIONS = set(LOADOUT_MAP)

# Ratchet. Raise it when the real figure improves; never lower it to make a
# regression pass.
COMPLETENESS_BASELINE = 0.88

# Variant names that promise a weapon ("Tbow / Bowfa", "Melee (scythe)").
WEAPON_HINT_RE = re.compile(
    r"tbow|bowfa|scythe|shadow|blowpipe|crossbow|halberd|maul|whip|claws|bludgeon|"
    r"chinchompa|staff|wand|axe|sword|spear|hasta|mace|dagger|bow\b",
    re.IGNORECASE,
)


def source_name_for(layout_pos: str, source: dict) -> str | None:
    arg = LOADOUT_MAP.get(int(layout_pos))
    if arg is None:
        return None
    if arg.isdigit():
        return source["inventory"].get(arg)
    if arg.startswith("rune"):
        return source["runes"].get(arg[4:])
    return source["equipment"].get(arg)


def partial_dose_ids(index: dict) -> dict[int, str]:
    """ids whose name is X(n) when some X(m>n) also exists."""
    families: dict[str, list[tuple[int, str]]] = {}
    for name in index["byName"]:
        m = DOSE_RE.match(name)
        if m:
            families.setdefault(m.group("base").strip(), []).append((int(m.group("n")), name))
    partial: dict[int, str] = {}
    for base, variants in families.items():
        if len(variants) < 2:
            continue
        top = max(n for n, _ in variants)
        for n, name in variants:
            if n < top:
                for item_id in index["byName"][name]:
                    partial[item_id] = name
    return partial


def validate() -> int:
    client = WikiClient()
    index = load_item_index(client)
    norm = Normalizer(index)
    data = json.loads(ENCODED.read_text(encoding="utf-8"))
    layouts = data["layouts"]
    partial = partial_dose_ids(index)

    errors: list[str] = []
    dose_flags: list[dict] = []
    unresolved: list[dict] = []

    for entry in layouts:
        label = f"{entry['activity']} / {entry['variant']}"
        code = entry["importString"]

        if not code.startswith("banktags,1,"):
            errors.append(f"{label}: bad prefix")
            continue
        parts = code.split(",")
        if "layout" not in parts:
            errors.append(f"{label}: missing layout marker")
            continue
        if "," in entry["tagName"]:
            errors.append(f"{label}: tag name contains a comma")

        tail = parts[parts.index("layout") + 1 :]
        if len(tail) % 2 != 0:
            errors.append(f"{label}: odd number of layout values")
            continue

        seen: set[int] = set()
        for pos_s, id_s in zip(tail[0::2], tail[1::2]):
            if not pos_s.isdigit() or not id_s.isdigit():
                errors.append(f"{label}: non-numeric pair {pos_s},{id_s}")
                continue
            pos, item_id = int(pos_s), int(id_s)
            if pos not in VALID_POSITIONS:
                errors.append(f"{label}: position {pos} is not a grid slot")
            if pos in seen:
                errors.append(f"{label}: position {pos} used twice")
            seen.add(pos)

            if norm.name_for_id(item_id) is None:
                errors.append(f"{label}: unknown item id {item_id}")
                continue

            source = source_name_for(pos_s, entry["sourceNames"])
            if source:
                valid_ids = norm.resolve_ids(source)
                if valid_ids and item_id not in valid_ids:
                    unresolved.append(
                        {
                            "layout": label,
                            "position": pos,
                            "sourceName": source,
                            "gotId": item_id,
                            "gotName": norm.name_for_id(item_id),
                            "expectedIds": valid_ids[:5],
                        }
                    )

            if item_id in partial:
                dose_flags.append(
                    {
                        "layout": label,
                        "position": pos,
                        "item": partial[item_id],
                        "itemId": item_id,
                        "sourceName": source,
                    }
                )

    # A part-dose item is only acceptable when the wiki asked for it by name.
    bad_doses = [d for d in dose_flags if not (d["sourceName"] or "").endswith(")")]

    # --- completeness gate -------------------------------------------------
    # Half the library once shipped with no worn gear at all and nothing caught
    # it. Hold the ratio at or above the recorded baseline so the next parser
    # regression fails the build instead of publishing quietly.
    complete = [e for e in layouts if e.get("completeness") == "complete"]
    ratio = len(complete) / len(layouts) if layouts else 0
    gate_errors: list[str] = []
    if ratio < COMPLETENESS_BASELINE:
        gate_errors.append(
            f"complete ratio {ratio:.1%} is below the {COMPLETENESS_BASELINE:.0%} baseline"
        )

    # A variant that names a weapon must actually carry one.
    for e in layouts:
        if e.get("equipment") and "weapon" not in e["equipment"]:
            if WEAPON_HINT_RE.search(e["variant"]):
                gate_errors.append(
                    f"{e['activity']} / {e['variant']}: variant names a weapon "
                    f"but no weapon slot was extracted"
                )

    # Both layout styles must describe the same set of items.
    for e in layouts:
        presets = sorted(e["layout"].values())
        zigzag = sorted(e.get("layoutZigzag", {}).values())
        if presets != zigzag:
            gate_errors.append(
                f"{e['activity']} / {e['variant']}: zigzag item multiset differs from presets"
            )

    errors.extend(gate_errors)

    report = {
        "layouts": len(layouts),
        "completeRatio": round(ratio, 4),
        "completenessBaseline": COMPLETENESS_BASELINE,
        "completenessCounts": {
            status: len([e for e in layouts if e.get("completeness") == status])
            for status in ("complete", "minimal", "partial")
        },
        "encodeFailures": data.get("failures", []),
        "errors": errors,
        "roundTripMismatches": unresolved,
        "partialDoseAccepted": [d for d in dose_flags if d not in bad_doses],
        "partialDoseUnexpected": bad_doses,
        "normalizations": sorted(
            {w for e in layouts for w in e["warnings"]}
        ),
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"validate: {len(layouts)} layouts")
    print(f"  complete / minimal / partial: "
          f"{report['completenessCounts']['complete']} / "
          f"{report['completenessCounts']['minimal']} / "
          f"{report['completenessCounts']['partial']}  "
          f"({ratio:.1%}, baseline {COMPLETENESS_BASELINE:.0%})")
    print(f"  structural errors        : {len(errors)}")
    print(f"  round-trip mismatches    : {len(unresolved)}")
    print(f"  part-dose (explicit, ok) : {len(dose_flags) - len(bad_doses)}")
    print(f"  part-dose (unexpected)   : {len(bad_doses)}")
    print(f"  distinct normalizations  : {len(report['normalizations'])}")
    print(f"  wrote {REPORT.relative_to(REPO)}")

    for e in errors[:10]:
        print(f"   ! {e}")
    for u in unresolved[:10]:
        print(f"   ? {u['layout']} pos {u['position']}: "
              f"{u['sourceName']!r} -> {u['gotName']!r} ({u['gotId']})")
    for b in bad_doses[:10]:
        print(f"   ! part dose {b['item']} in {b['layout']} from {b['sourceName']!r}")

    return 1 if (errors or bad_doses) else 0


if __name__ == "__main__":
    sys.exit(validate())
