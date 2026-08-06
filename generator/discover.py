"""Stage 1 - discover: find the wiki pages that carry gear setups.

`Template:Inventory` is transcluded ~450 times across the wiki, but most of
those are illustrative (what a herb sack looks like) rather than a loadout.
The `/Strategies` subpages are the high-signal subset: boss and raid guides
where the template genuinely encodes "bring this".
"""

from __future__ import annotations

import json
from pathlib import Path

from wikiclient import WikiClient

REPO = Path(__file__).parent.parent
CORPUS = REPO / "generator" / "corpus.json"

INVENTORY_TEMPLATE = "Template:Inventory"
STRATEGY_SUFFIX = "/Strategies"


def discover(client: WikiClient) -> dict:
    # Article space only (namespace 0). User sandbox drafts such as
    # "User:Discograph/Zulrah/Strategies" match the suffix too, but they are
    # personal working copies, not the wiki's published meta.
    all_pages = client.embedded_in(INVENTORY_TEMPLATE, namespace=0)
    strategies = sorted(p for p in all_pages if p.endswith(STRATEGY_SUFFIX))
    return {
        "template": INVENTORY_TEMPLATE,
        "totalTransclusions": len(all_pages),
        "pages": strategies,
    }


def main() -> None:
    client = WikiClient()
    corpus = discover(client)
    CORPUS.write_text(json.dumps(corpus, indent=2), encoding="utf-8")
    print(
        f"discover: {corpus['totalTransclusions']} transclusions -> "
        f"{len(corpus['pages'])} strategy pages"
    )
    print(f"  wrote {CORPUS.relative_to(REPO)}")


if __name__ == "__main__":
    main()
