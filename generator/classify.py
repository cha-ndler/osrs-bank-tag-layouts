"""Decide whether a refresh can merge itself or must wait for a person.

`validate.py` proves a run is *well formed*: every string imports, every id is
the one the wiki meant. It cannot tell a normal week on the wiki from a page
being gutted, because a gutted page is still well formed. Zulrah dropped out of
the library that way when its guide moved to `{{Loadout}}`, and nothing failed.

So the refresh is compared against what is already published. A week that only
moves a few layouts and adds a few is what the merged refreshes have looked
like, and it merges without anyone clicking. Anything that takes a layout away,
costs one its completeness, or touches far more than usual opens the pull
request labelled for review instead, with the reasons in its body.

Usage (from the workflow, after the pipeline has written `data/`):

    python generator/classify.py --base HEAD --body pr-body.md

prints `routine=true` or `routine=false` for `$GITHUB_OUTPUT`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
DATA = REPO / "data"

# Calibrated on the merged refreshes (#16-#18): an ordinary week changed 1-4
# layouts and removed none. The one month-long catch-up changed 35 and was
# reviewed by hand, which is where a run that size belongs.
CHANGE_LIMIT = 10
ADD_LIMIT = 10

Key = tuple[str, str]


def label(key: Key) -> str:
    return f"{key[0]} / {key[1]}"


def classify(old: dict[Key, dict], new: dict[Key, dict]) -> dict:
    """Compare two libraries keyed by (activity, variant)."""
    removed = sorted(set(old) - set(new))
    added = sorted(set(new) - set(old))
    kept = sorted(set(old) & set(new))
    changed = [k for k in kept if old[k]["layout"] != new[k]["layout"]]
    downgraded = [
        k for k in kept
        if old[k]["completeness"] == "complete" and new[k]["completeness"] != "complete"
    ]
    lost_activities = sorted({k[0] for k in old} - {k[0] for k in new})

    reasons: list[str] = []
    for activity in lost_activities:
        reasons.append(f"activity {activity} is no longer in the library")
    gone = [k for k in removed if k[0] not in lost_activities]
    if gone:
        # Usually the wiki renaming a tab, which shows up as one removed and one
        # added. Cheap to confirm by eye, and a real loss looks identical.
        reasons.append(
            f"{len(gone)} layout(s) removed: " + ", ".join(label(k) for k in gone)
        )
    for k in downgraded:
        reasons.append(f"{label(k)} is no longer complete ({new[k]['completeness']})")
    if len(changed) > CHANGE_LIMIT:
        reasons.append(f"{len(changed)} layouts changed (routine limit {CHANGE_LIMIT})")
    if len(added) > ADD_LIMIT:
        reasons.append(f"{len(added)} layouts added (routine limit {ADD_LIMIT})")

    return {
        "routine": not reasons,
        "reasons": reasons,
        "removed": removed,
        "added": added,
        "changed": changed,
        "downgraded": downgraded,
    }


def _variants(records: list[dict]) -> dict[Key, dict]:
    out: dict[Key, dict] = {}
    for record in records:
        for variant in record["variants"]:
            out[(record["activity"], variant["variant"])] = variant
    return out


def published_at(ref: str) -> dict[Key, dict]:
    """The library as committed at `ref`."""
    names = subprocess.run(
        ["git", "ls-tree", "--name-only", ref, "data/"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    records = [
        json.loads(
            subprocess.run(
                ["git", "show", f"{ref}:{name}"],
                cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=True,
            ).stdout
        )
        for name in names
        if name.endswith(".json")
    ]
    return _variants(records)


def on_disk() -> dict[Key, dict]:
    return _variants(
        [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DATA.glob("*.json"))]
    )


def body(result: dict) -> str:
    lines = ["Automated regeneration from the OSRS Wiki strategy pages.", ""]
    if result["routine"]:
        lines += [
            "**Routine.** Nothing was removed or lost completeness, and the change "
            "is the size of an ordinary week. This merges itself once CI passes.",
        ]
    else:
        lines += [
            "**Needs review.** This refresh is outside what merges unattended:",
            "",
            *[f"- {r}" for r in result["reasons"]],
            "",
            "A large or lossy change usually means an upstream page or template was "
            "restructured. Check the affected pages before merging; if the pipeline "
            "is reading them wrong, fix the generator rather than the data.",
        ]
    lines += ["", "| | Layouts |", "|---|---|"]
    for name in ("changed", "added", "removed"):
        keys = result[name]
        shown = ", ".join(label(k) for k in keys[:15])
        if len(keys) > 15:
            shown += f", and {len(keys) - 15} more"
        lines.append(f"| {name} ({len(keys)}) | {shown or '-'} |")
    lines += [
        "",
        "`report.json` lists every item-name normalisation and any validation "
        "findings for this run.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="HEAD", help="git ref of the published library")
    parser.add_argument("--body", type=Path, help="write the pull request body here")
    args = parser.parse_args()

    result = classify(published_at(args.base), on_disk())
    if args.body:
        args.body.write_text(body(result), encoding="utf-8")
    print(f"routine={'true' if result['routine'] else 'false'}")


if __name__ == "__main__":
    main()
