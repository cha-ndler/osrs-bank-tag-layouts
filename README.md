# OSRS Bank Tag Layouts

Bank tag layouts for Old School RuneScape bosses, raids and activities,
generated automatically from the [OSRS Wiki](https://oldschool.runescape.wiki/)
strategy pages.

**340 layouts across 103 activities, in both layout styles, for RuneLite and for
the official client.** Browse and copy them at
[the GitHub Pages site](https://cha-ndler.github.io/osrs-bank-tag-layouts/), or
consume `data/*.json` directly. The RuneLite string imports as a working layout
under both of RuneLite's layout plugins, with nothing to convert afterwards.

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

### RuneLite

1. Copy an import string (from the site with **Copy for → RuneLite**, or
   `importString` in any `data/*.json`).
2. In game, right-click the **New tag tab** button in your bank.
3. Choose either **Import tag tab** or, if you have the Bank Tag Layouts plugin,
   **Import tag tab with layout**. Both work, and neither leaves you anything to
   convert afterwards.

### Official client and mobile

Old School added bank tags of its own on 17 June 2026 and an import/export
string for them on 15 July 2026. Copy with **Copy for → Official client**, or
take `importStringOfficial` from `data/*.json`, then import it on the tag in
game. That format carries the items and their positions but no name or icon, so
name the tab yourself — the site shows the name we would have used.

### Why there are two RuneLite plugins, and why it no longer matters

RuneLite has two layout implementations: the built-in Bank Tags plugin, which
grew layouts of its own in 2024, and the older Plugin Hub **Bank Tag Layouts**
plugin. Each has its own import string, and only one of the two crosses over:

| String                                            | Import tag tab (built-in) | Import tag tab with layout (hub) |
| ------------------------------------------------- | ------------------------- | -------------------------------- |
| `banktags,1,…` (built-in format)                  | built-in layout           | **rejected**                     |
| `banktaglayoutsplugin:…` (hub format)             | built-in layout           | hub layout                       |

So a `banktags,1,…` string — which is what this repository used to publish, and
what the wiki and every other layout site still hand out — can only be imported
through the built-in option. A player running the hub plugin then lands on a
built-in layout and has to right-click the tab and **Convert to hub layout** by
hand, every single time.

Publishing the hub format instead removes the choice: whichever import option
you use, you get a layout in the plugin that put the option there. The built-in
plugin has read this format since RuneLite 1.10.34 (June 2024), the same release
that gave it layouts at all, so nothing is given up by preferring it.

### Layout styles

Both RuneLite plugins support two arrangements, and both are published for every
layout. Pick one with the toggle on the site.

- **Presets** (`importString`, `importStringOfficial`, `layout`) — worn gear in
  the left three columns, shaped like the equipment screen, column four blank as
  a spacer, and the 28 inventory slots filling columns five to eight in their
  normal 4-wide shape.
- **Zigzag** (`importStringZigzag`, `importStringZigzagOfficial`,
  `layoutZigzag`) — the hub plugin's own default. Items pack two per column
  across row pairs (0, 8, 1, 9, 2, 10 …), gear first, then inventory, then the
  rune pouch laid out linearly.

Both describe the same items; only the positions differ. The zigzag generator is
a direct port of `LayoutGenerator.toZigZagIndex`, and a test pins the index
sequence against values traced from the plugin.

### Tag names

A tag name is filtered on the way in: RuneLite drops `<`, `>`, `/` and `:`
(`TabInterface.FILTERED_CHARS`), and a comma would split the import string
itself. Names are sanitised before publishing so what the site shows is what the
tab is called in game — "Budget Melee/Range" is published as "Budget Melee
Range" rather than arriving as "Budget MeleeRange".

### Completeness

Each variant carries a `completeness` field:

- `complete` — at least 8 gear slots and 10 inventory items.
- `minimal` — genuinely small, with a `completenessNote` explaining why
  (Tempoross needs four items; the Gauntlet's gear is crafted inside).
- `partial` — fewer items than expected and not a known-small activity, so the
  wiki page may not list a full loadout.

`validate.py` floors the **number** of complete layouts and fails the build if
it drops, which is what stops a parser regression shipping quietly. Counting
rather than taking a ratio is deliberate. A ratio moves for two unrelated
reasons — gear going missing, and the wiki simply publishing more pages — and
every small setup upstream adds dilutes it, including the ones recognised as
correctly `minimal`. Four new pages once walked an 89.0% library under an 88.5%
floor while the number of complete layouts had not moved at all, and the weekly
refresh stopped opening pull requests for a month. A count cannot be diluted by
growth, and it is the stricter of the two on a real regression: growth can hide
lost gear inside a ratio, never inside a count. The ratio is still reported in
`report.json`, because it describes the library even when it cannot gate it.

Counts elsewhere are floored for the mirror-image reason: a discovery failure
that halves the corpus leaves every surviving layout complete, so activity and
layout counts are held down too, and so is the number of item references that
resolve to no id at all — the check that would otherwise stay silent about
exactly the items it failed to find.

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
      "importString":         "banktaglayoutsplugin:…,banktag:…",
      "importStringZigzag":   "banktaglayoutsplugin:…,banktag:…",
      "importStringOfficial": "1,28,6746,4,0,…",
      "layout":     { "4": 6746, "5": 21255 },
      "equipment":  { "weapon": 26374 },
      "inventory":  { "1": 6746 },
      "runes":      { "1": 560 },
      "switches":   [{ "name": "Voidwaker", "id": 27690 }],
      "warnings":   ["Saradomin brew -> Saradomin brew(4)"]
    }
  ]
}
```

`layout` is the canonical form — a position-to-item map that needs no parsing.
Every `importString*` field is derived from it, which is why the site ships the
map alone and rebuilds whichever string you asked for in the browser.
`equipment` / `inventory` / `runes` are the same data split by section.
`switches` sits beside them: spec weapons the guide expects you to bring, which
occupy no worn slot and so appear in no position.

## How it works

```
discover → extract → overrides → encode → validate → publish
```

- **discover** — finds every article-space page transcluding `Template:Inventory`
  and keeps the `/Strategies` subpages (100 of 269; the rest use the template
  illustratively). `User:` sandbox drafts match the suffix too and are excluded —
  they are personal working copies, not the wiki's published meta.
- **extract** — parses `{{Equipment}}`, `{{Recommended equipment}}`,
  `{{Inventory}}` and `{{Rune pouch}}`, and pairs them per scope. Pages use
  several shapes: tabbers (Doom, Zulrah), wikitables whose header cells name the
  variants (Chambers of Xeric), and a tabbed set of gear variants followed by one
  shared inventory in a sibling section (Abyss, Gemstone Crab).
- **overrides** — applies the curated corrections in `overrides.json` (see below).
  Nothing else in the pipeline second-guesses the wiki.
- **encode** — hands a synthesised `{{Loadout}}` call back to the wiki through
  `action=expandtemplates`, so `Module:Loadout` does the name-to-id resolution.
  No second implementation to keep in sync.
- **validate** — structure, item existence, position round-trip, a dose
  regression check, and floors on how much library survived. Every published
  string is parsed back the way the client that reads it parses it and must
  come out as the layout it was built from, so a format only one of the three
  importers accepts cannot ship. Publishing is blocked on failure.
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

A rewrite reuses the wiki's own spelling rather than rebuilding the name, which
matters more than it sounds: 72 of the 189 dose families are written `X (4)`
with a space, and `X(4)` is not an item at all. A rebuilt name resolves to
nothing and `Module:Loadout` drops the slot silently, so the correction was
deleting the very items it existed to fix.

The same rule covers **disambiguated pages**. Nightmare Zone guides ask for an
`Overload (Nightmare Zone)`; no item is called that, so the wiki falls through to
the page and returns whichever dose comes first — the 3-dose one. Resolving the
page ourselves picks `Overload (4)`.

### Stepping down from best-in-slot

`{{Recommended equipment}}` ranks every slot best first, and **90% of filled
slots list more than one option**. The layout itself uses rank 1, but the whole
ladder is published, so a slot on the site can be stepped through with ◀ ▶ — left
disabled once you are on the wiki's own pick, right walking down toward what you
can actually afford. Copy then gives you *your* layout, not the wiki's.

Two things follow from the data rather than from preference:

- **About half the library has arrows.** 52% of setups come from
  `{{Recommended equipment}}`; the rest are `{{Equipment}}` blocks, which are a
  single hand-authored loadout with no ranking to offer. Inventing one is not
  this repo's job.
- **Inventories have no arrows.** `{{Inventory}}` is a flat positional list with
  nothing to rank.

Stepping the weapon onto a two-hander clears the off-hand, and stepping back
restores it — which is why a shield's ladder is published even for setups that
cannot currently wear one.

The site rebuilds layouts in the browser, so the layout maths exists twice.
`docs/layout.js` holds the port, `LOADOUT_MAP` and the zigzag order are shipped
in `layouts.json` rather than copied, and `tests/check_layout_port.mjs` rebuilds
every published layout in both styles and requires it to match the generator
exactly. CI runs it on every pull request.

### When the wiki is out of date

Extraction is faithful on purpose: it publishes what the strategy page says, even
when the page has fallen behind the game. `Abyss/Strategies` still lists a small,
medium, large *and* giant pouch, and never mentions the colossal pouch — which is
made by stitching those four together, consuming them, and holds 40 essence in
one slot against 30 across four.

Corrections therefore live in **`overrides.json`**, applied by a separate stage,
never folded into the parser. Each entry states the items it replaces and **why**,
and affected layouts are badged `curated` on the site with that reason, so a
correction is never passed off as the wiki's own answer.

The file is designed to clean itself up. An override that matches nothing —
because the wiki has caught up — **fails the build**, which forces the entry to be
deleted rather than left to become a second source of staleness.

### Two equipment templates

`{{Equipment}}` names slots directly. `{{Recommended equipment}}` — used by 94
of the 100 pages — is different: it calls them `body` / `hands` / `feet` rather
than torso / gloves / boots, wraps values in `{{plink}}`, and ranks options per
slot as `weapon1`..`weapon4`, best first. We take the best-ranked option, and
prefer a `plink`'s `pic=` over its link target when the link points at a
category page such as "Barrows equipment".

Its `special` list is the one ranked group with no worn slot to occupy, so it
cannot join `equipment` — but a guide that lists a Voidwaker means you to bring
one. Those are published as **`switches`**, a name-and-id list beside the layout
rather than a position inside it.

A page often carries both templates in the same tab: a concrete `{{Equipment}}`
setup, and a `{{Recommended equipment}}` upgrades-and-downgrades table beside it.
The concrete block wins; between two of a kind, the one filling more slots does.
Choosing by whichever sat nearer the inventory made the winner an accident of
typing order, which is how The Hueycoatl published a melee layout wearing
nothing but a weapon.

### Slots the wiki decides at render time

735 inventory slots hold a template call rather than an item name — `{{Cheap
food}}`, `{{Cheap prayer}}`, `{{MinPrice|…}}`. These resolve to whatever is
cheapest when the pipeline runs, so they move with the Grand Exchange and not
with the guide. They are resolved as the wiki resolves them, and each one is
noted in the variant's `warnings`, so a reviewer can tell price drift from a
real upstream edit in the weekly refresh diff.

## Scope and limitations

- **Variants are the wiki's, not ours.** You get Melee / Ranged / Magic /
  Budget / Max / Learner, because that is what the wiki encodes.
- **There is no solo/team axis.** Chambers of Xeric, Theatre of Blood and Tombs
  of Amascut all tab by combat style, not by team size. Nothing upstream
  distinguishes a solo setup from a 3-scale one, so nothing here does either.
- **Coverage follows the wiki.** If a page has no structured setup, it produces
  no layout. `report.json` names every page that yielded nothing
  (`skippedPages`) and every block extraction had to drop
  (`extractionWarnings`).
- **Discovery keys on `Template:Inventory`.** Eight `/Strategies` pages use
  `{{Recommended equipment}}` without an inventory and are therefore not found:
  Blast Furnace, Blast mine, Deranged archaeologist, Forestry, Frost dragon,
  Giants' Foundry, Lava dragon, Trouble Brewing.
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
