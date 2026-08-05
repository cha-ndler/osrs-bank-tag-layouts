# OSRS Bank Tag Layouts

Bank tag layouts for Old School RuneScape bosses, raids and activities,
generated automatically from the [OSRS Wiki](https://oldschool.runescape.wiki/)
strategy pages.

**330 layouts across 102 activities.** Browse and copy them at
[the GitHub Pages site](https://cha-ndler.github.io/osrs-bank-tag-layouts/), or
consume `data/*.json` directly.

## Why

A bank tag layout is just a set of `(gridPosition, itemId)` pairs that lays out
everything you need for an activity in one click. There is no maintained library
covering the game: the largest community site has zero God Wars entries, the
next has around forty layouts total, and the wiki's own clue-tag guide is
hand-written and has not been touched since June 2025.

The wiki already encodes the meta for every major boss in structured templates,
and already ships an encoder that turns them into import strings — it was just
only ever wired up on a single page. This repository closes that gap.

## Using a layout

1. Copy an import string (from the site, or `importString` in any `data/*.json`).
2. In game, right-click the **New tag tab** button in your bank.
3. Choose **Import tag tab**.

Worn gear lands in the left three columns, laid out like the equipment screen.
Column four is left blank as a spacer. The 28 inventory slots fill columns five
through eight in their normal 4-wide shape.

## Data format

`index.json` is the manifest. Each `data/<slug>.json` holds one activity:

```json
{
  "activity": "Doom of Mokhaiotl",
  "sourceUrl": "https://oldschool.runescape.wiki/w/Doom_of_Mokhaiotl/Strategies",
  "sourceRevId": 15285926,
  "contentHash": "…",
  "variants": [
    {
      "variant": "Max Ranged",
      "tagName": "Doom of Mokhaiotl Max Ranged",
      "importString": "banktags,1,…",
      "layout":     { "4": 6746, "5": 21255 },
      "equipment":  { "weapon": 26374 },
      "inventory":  { "1": 6746 },
      "runes":      { "1": 560 },
      "warnings":   ["Saradomin brew -> Saradomin brew(4)"]
    }
  ]
}
```

`layout` is the canonical form — a position-to-item map that needs no parsing.
`equipment` / `inventory` / `runes` are the same data split by section.

## How it works

```
discover → extract → encode → validate → publish
```

- **discover** — finds every page transcluding `Template:Inventory` and keeps the
  `/Strategies` subpages (102 of 450 transclusions; the rest are illustrative).
- **extract** — parses `{{Equipment}}` / `{{Inventory}}` / `{{Rune pouch}}` and
  pairs them per scope. Pages use two different shapes: tabbers (Doom, Zulrah)
  and wikitables whose header cells name the variants (Chambers of Xeric).
- **encode** — hands a synthesised `{{Loadout}}` call back to the wiki through
  `action=expandtemplates`, so `Module:Loadout` does the name-to-id resolution.
  No second implementation to keep in sync.
- **validate** — structure, item existence, position round-trip and a dose
  regression check. Publishing is blocked on failure.
- **publish** — writes `data/`, `index.json`, `report.json` and the site payload.

### The dose correction

`Module:Loadout` resolves an item by exact name first, then falls back to the
page name and takes whatever comes back. A bare `Saradomin brew` therefore
resolves to **6687, the 3-dose potion**, and `Super restore` to `3026`. Verified
against the game cache. Left alone, roughly a third of the library would ship
part-used potions.

The fix is deliberately narrow: a name is rewritten **only** when no item is
literally called that and the page it points at is a numbered family, in which
case the highest number wins. When an exact item exists the wiki is already
unambiguous and is left alone — which is why setups that explicitly ask for a
`Ranging potion(3)` still get one. Every rewrite is logged to `report.json`.

## Scope and limitations

- **Variants are the wiki's, not ours.** You get Melee / Ranged / Magic /
  Budget / Max / Learner, because that is what the wiki encodes.
- **There is no solo/team axis.** Chambers of Xeric, Theatre of Blood and Tombs
  of Amascut all tab by combat style, not by team size. Nothing upstream
  distinguishes a solo setup from a 3-scale one, so nothing here does either.
- **Coverage follows the wiki.** If a page has no structured setup, it produces
  no layout. `report.json` lists everything skipped and why.
- Layouts are a starting point. Swap in what you actually own.

## Running it yourself

```bash
pip install -r requirements.txt
python generator/run.py            # reuses the on-disk wiki cache
python generator/run.py --fresh    # re-fetch everything
python -m unittest discover -s tests
```

The generator identifies itself with a contact address and serialises requests.
Please keep it polite if you fork it.

## Attribution

Setup data comes from the OSRS Wiki, available under
[CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/). Item
icons are served by `static.runelite.net`. Not affiliated with Jagex.
