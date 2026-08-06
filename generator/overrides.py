"""Stage 3 - overrides: curated corrections to what the wiki actually said.

Extraction is deliberately faithful. It publishes whatever the strategy page
says, including when the page is out of date, and that promise is worth keeping -
so corrections live here instead of being smuggled into the parser. They are
declared in `overrides.json`, applied by item **name** before encoding so the
result still gets its ids from the wiki's own `Module:Loadout`, and surfaced to
the reader as `curated` rather than passed off as the wiki's own answer.

Every override must still match something. When the wiki catches up, its
`replaceAll` list stops matching and validation fails the build - which is the
signal to delete the entry, rather than let this file rot into a second source of
staleness.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).parent.parent
SETUPS = REPO / "generator" / "setups.json"
OVERRIDES = REPO / "overrides.json"


def activity_of(page: str) -> str:
    return page.rsplit("/Strategies", 1)[0].strip()


def load_overrides(path: Path | None = None) -> list[dict]:
    path = path or OVERRIDES
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc.get("overrides", [])


def targets(rule: dict, activity: str, variant: str) -> bool:
    if rule.get("activity") != activity:
        return False
    wanted = rule.get("variants", "*")
    if wanted == "*":
        return True
    return variant in wanted


def replace_in_inventory(
    inventory: dict[str, str], remove: list[str], insert: list[str]
) -> dict[str, str] | None:
    """Swap items by name and close the gaps.

    Returns the rewritten inventory, or None when the items to remove are not
    all present - which means the override no longer describes this setup.
    """
    lowered = {name.lower() for name in inventory.values()}
    if not {name.lower() for name in remove} <= lowered:
        return None

    ordered = [inventory[str(n)] for n in range(1, 29) if inventory.get(str(n))]
    drop = {name.lower() for name in remove}
    first = next(i for i, name in enumerate(ordered) if name.lower() in drop)
    kept = [name for name in ordered if name.lower() not in drop]
    # The replacement takes the position of the first item it stands in for, and
    # everything else closes up behind it, so the tab has no holes.
    rebuilt = kept[:first] + list(insert) + kept[first:]
    return {str(i + 1): name for i, name in enumerate(rebuilt[:28])}


def apply_overrides(pages: list[dict], rules: list[dict]) -> tuple[int, list[str]]:
    """Rewrite setups in place. Returns (setups changed, unmatched rule notes)."""
    applied = [0] * len(rules)
    changed = 0

    for page in pages:
        activity = activity_of(page["page"])
        for setup in page["setups"]:
            for i, rule in enumerate(rules):
                if not targets(rule, activity, setup["variant"]):
                    continue
                spec = rule.get("inventory")
                if not spec:
                    continue
                rewritten = replace_in_inventory(
                    setup["inventory"], spec["replaceAll"], spec["with"]
                )
                if rewritten is None:
                    continue
                setup["inventory"] = rewritten
                setup["curated"] = True
                setup["curationReason"] = rule["reason"]
                applied[i] += 1
                changed += 1

    stale = [
        f"override {i} ({rule.get('activity')}) matched nothing - "
        f"the wiki may have caught up; delete it"
        for i, rule in enumerate(rules)
        if applied[i] == 0
    ]
    return changed, stale


def main() -> None:
    rules = load_overrides()
    data = json.loads(SETUPS.read_text(encoding="utf-8"))
    changed, stale = apply_overrides(data["pages"], rules)
    data["overrideNotes"] = stale
    SETUPS.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"overrides: {len(rules)} rule(s) -> {changed} setup(s) curated")
    for note in stale:
        print(f"   ! {note}")


if __name__ == "__main__":
    main()
