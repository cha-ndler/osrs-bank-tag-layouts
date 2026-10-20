"""Offline tests for the parsing and encoding logic.

Nothing here touches the network: the wiki-facing stages are covered by
`validate.py`, which runs over real generated output. These guard the pure
functions where a silent regression would corrupt every layout at once.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "generator"))

from curated import add_curated  # noqa: E402
from encode import (  # noqa: E402
    LOADOUT_MAP,
    PREPOT_POSITIONS,
    build_hub_import_string,
    build_official_import_string,
    completeness,
    parse_hub_import_string,
    parse_import_string,
    parse_loadout_map,
    parse_official_import_string,
    place_prepots,
    relay_positions,
    split_layout,
    tag_name,
    tag_tail,
    zigzag_index,
    zigzag_layout,
)
import extract as extract_module  # noqa: E402
from extract import (  # noqa: E402
    clean_item,
    drop_conflicting_offhand,
    extract_page,
    parse_template,
    plink_item,
    plink_items,
    recommended_alternatives,
    recommended_equipment,
    recommended_switches,
    split_args,
)
from normalize import Normalizer  # noqa: E402
from overrides import (  # noqa: E402
    apply_overrides,
    replace_in_inventory,
    targets,
)
from publish import (  # noqa: E402
    stable_rev_id,
    write_unless_only_timestamp,
)
from classify import ADD_LIMIT, CHANGE_LIMIT, classify  # noqa: E402
from supplies import (  # noqa: E402
    bank_sections,
    falo_items,
    groups_for,
    place_supplies,
    sherlock_items,
    stash_materials,
)
from validate import (  # noqa: E402
    COMPLETE_BASELINE,
    completeness_errors,
    curated_errors,
    equivalence_errors,
)

# Injected instead of the real wiki slot index so nothing here needs the network.
TWO_HANDED = {"tumeken's shadow", "scythe of vitur", "twisted bow"}

# Injected instead of the real wiki slot index so nothing here needs the network.
TWO_HANDED = {"tumeken's shadow", "scythe of vitur", "twisted bow"}


class TestRecommendedEquipment(unittest.TestCase):
    """{{Recommended equipment}} is used by 93 of 99 strategy pages."""

    def test_plink_returns_item_name(self):
        self.assertEqual(plink_item("{{plink|Scythe of vitur}}"), "Scythe of vitur")

    def test_plink_prefers_pic_over_category_page(self):
        # "Barrows equipment" is a category page, not a wearable item.
        self.assertEqual(
            plink_item("{{plink|Barrows equipment|pic=Torag's platebody|txt=Barrows body}}"),
            "Torag's platebody",
        )

    def test_footnotes_are_stripped(self):
        self.assertEqual(
            plink_item("{{plink|Fire cape}}{{efn|Mythical is better sometimes.}}"),
            "Fire cape",
        )

    def test_first_of_ranked_alternatives_wins(self):
        self.assertEqual(
            plink_item("{{plink|Abyssal bludgeon}} > <br/>{{plink|Zamorakian hasta}}"),
            "Abyssal bludgeon",
        )

    def test_prose_value_yields_nothing(self):
        self.assertEqual(plink_item("Ammo for killing araxyte spawns"), "")

    def test_lowest_rank_wins_and_slots_are_remapped(self):
        args = {
            "style": "Melee",
            "head1": "{{plink|Slayer helmet (i)}}",
            "body1": "{{plink|Inquisitor's hauberk}}",
            "body2": "{{plink|Torva platebody}}",
            "hands2": "{{plink|Barrows gloves}}",
            "feet1": "{{plink|Primordial boots}}",
            "special1": "{{plink|Dragon claws}}",
        }
        eq = recommended_equipment(args)
        self.assertEqual(eq["head"], "Slayer helmet (i)")
        self.assertEqual(eq["torso"], "Inquisitor's hauberk")  # body1 beats body2
        self.assertEqual(eq["gloves"], "Barrows gloves")       # only rank 2 exists
        self.assertEqual(eq["boots"], "Primordial boots")
        # `special` is a spec weapon list with no worn slot to occupy.
        self.assertNotIn("special", eq)

    def test_shared_inventory_folds_onto_preceding_gear_tabs(self):
        # Abyss tabs two gear sets, then gives one inventory for both.
        text = (
            "==Setup==\n<tabber>\nGraceful=\n"
            "{{Recommended equipment|style=Graceful|head1={{plink|Graceful hood}}"
            "|body1={{plink|Graceful top}}|legs1={{plink|Graceful legs}}"
            "|feet1={{plink|Graceful boots}}|hands1={{plink|Graceful gloves}}}}\n"
            "|-|\nDefensive=\n"
            "{{Recommended equipment|style=Defensive|head1={{plink|Rune full helm}}"
            "|body1={{plink|Rune platebody}}|legs1={{plink|Rune platelegs}}"
            "|feet1={{plink|Rune boots}}|hands1={{plink|Rune gloves}}}}\n"
            "</tabber>\n===Inventory===\n{{Inventory|1 = Pure essence}}\n"
        )
        setups, _ = extract_page("Abyss/Strategies", text)
        self.assertEqual(len(setups), 2)
        for s in setups:
            self.assertEqual(s.inventory.get("1"), "Pure essence")
            self.assertGreaterEqual(len(s.equipment), 5)


class TestCleanItem(unittest.TestCase):
    def test_strips_wiki_brackets(self):
        self.assertEqual(clean_item("[[Twisted bow]]"), "Twisted bow")

    def test_truncates_at_link_separator(self):
        # Module:Inventory splits on ';' - the tail is a link target, not a name.
        self.assertEqual(clean_item("Saradomin brew;n"), "Saradomin brew")

    def test_truncates_at_quantity(self):
        self.assertEqual(clean_item("Anglerfish\\5"), "Anglerfish")

    def test_uppercases_first_letter(self):
        self.assertEqual(clean_item("super restore(4)"), "Super restore(4)")

    def test_blank_stays_blank(self):
        self.assertEqual(clean_item("   "), "")
        self.assertEqual(clean_item(None), "")


class TestTemplateParsing(unittest.TestCase):
    def test_named_args(self):
        name, args, _ = parse_template("{{Inventory|1 = Shark|2 = Lobster}}", 0)
        self.assertEqual(name, "Inventory")
        self.assertEqual(args["1"], "Shark")
        self.assertEqual(args["2"], "Lobster")

    def test_positional_args_ignore_named_ones(self):
        # The bug this guards: counting `align` as a positional argument shifted
        # every item one slot, silently dropping inventory slot 28.
        _, args, _ = parse_template("{{Inventory|align=right|Shark|Lobster}}", 0)
        self.assertEqual(args["1"], "Shark")
        self.assertEqual(args["2"], "Lobster")
        self.assertEqual(args["align"], "right")

    def test_pipe_inside_link_does_not_split(self):
        parts = split_args("Inventory|1 = [[Elite Void|Void top]]|2 = Shark")
        self.assertEqual(len(parts), 3)

    def test_nested_template_does_not_split(self):
        parts = split_args("Inventory|1 = {{plink|Shark|pic=x}}|2 = Lobster")
        self.assertEqual(len(parts), 3)


class TestLoadoutMap(unittest.TestCase):
    def test_covers_every_slot_exactly_once(self):
        args = list(LOADOUT_MAP.values())
        self.assertEqual(len(args), len(set(args)), "duplicate argument in map")

    def test_has_all_28_inventory_slots(self):
        inv = sorted(int(v) for v in LOADOUT_MAP.values() if v.isdigit())
        self.assertEqual(inv, list(range(1, 29)))

    def test_has_equipment_and_runes(self):
        values = set(LOADOUT_MAP.values())
        for slot in ("head", "cape", "neck", "ammo", "weapon", "torso",
                     "legs", "shield", "gloves", "boots", "ring"):
            self.assertIn(slot, values)
        for n in range(1, 5):
            self.assertIn(f"rune{n}", values)

    def test_positions_fit_an_8_wide_grid(self):
        # Column 3 is the spacer between worn gear and inventory.
        self.assertTrue(all(0 <= p <= 55 for p in LOADOUT_MAP))
        self.assertFalse(any(p % 8 == 3 for p in LOADOUT_MAP))


class TestUpstreamLoadoutMap(unittest.TestCase):
    """The wiki's grid is read live and re-laid onto ours.

    On 2026-09-25 `Module:Loadout` moved rune4 from 48 to 43. The copy pinned
    here did not follow, every wiki-built layout came back with an item at a
    position the pipeline did not recognise, and the weekly refresh failed for
    three weeks. Decoding through the module's own table makes a rearrangement
    upstream a no-op for the published library.
    """

    # Trimmed from the real module source, rune4 at its new position.
    LUA = """
local p = {}
local loadoutmap = {
    [1] = 'head',
    [4] = '1',
    [42] = 'rune3',
    [43] = 'rune4',
}

function p._cleanItem(item)
    local t = { [1] = 'not part of the map' }
end
"""

    def test_reads_the_module_table(self):
        self.assertEqual(
            parse_loadout_map(self.LUA),
            {1: "head", 4: "1", 42: "rune3", 43: "rune4"},
        )

    def test_missing_table_is_an_error_not_an_empty_map(self):
        with self.assertRaises(ValueError):
            parse_loadout_map("local p = {}\nreturn p")

    def test_moved_slot_lands_back_on_our_position(self):
        upstream = dict(LOADOUT_MAP)
        del upstream[48]
        upstream[43] = "rune4"
        relay = relay_positions(upstream)
        code = "banktags,1,T,995,layout,43,560,1,4151"
        self.assertEqual(parse_import_string(code, relay), {"48": 560, "1": 4151})

    def test_unchanged_map_is_the_identity(self):
        relay = relay_positions(dict(LOADOUT_MAP))
        self.assertTrue(all(k == v for k, v in relay.items()))

    def test_new_or_renamed_argument_fails_loudly(self):
        # A slot we have no position for cannot be laid out, so guessing one
        # would publish a layout the README does not describe.
        upstream = dict(LOADOUT_MAP)
        upstream[3] = "quiver"
        with self.assertRaisesRegex(ValueError, "quiver"):
            relay_positions(upstream)
        upstream = {k: v for k, v in LOADOUT_MAP.items() if v != "ring"}
        with self.assertRaisesRegex(ValueError, "ring"):
            relay_positions(upstream)


class TestImportString(unittest.TestCase):
    CODE = "banktags,1,Test Tag,995,layout,1,4151,4,995,55,565"

    def test_parses_position_pairs(self):
        layout = parse_import_string(self.CODE)
        self.assertEqual(layout, {"1": 4151, "4": 995, "55": 565})

    def test_splits_into_sections(self):
        equipment, inventory, runes = split_layout(parse_import_string(self.CODE))
        self.assertEqual(equipment["head"], 4151)  # position 1
        self.assertEqual(inventory["1"], 995)      # position 4
        self.assertEqual(inventory["28"], 565)     # position 55
        self.assertEqual(runes, {})

    def test_tag_name_never_contains_a_comma(self):
        # A comma would split the CSV and corrupt the import.
        name = tag_name("Chest (Rogues' Castle)", "Best 3 item, low risk")
        self.assertNotIn(",", name)

    def test_tag_name_survives_runelites_own_filter(self):
        # RuneLite drops "</>:" from an imported name (TabInterface.FILTERED_CHARS),
        # so "Budget Melee/Range" used to arrive in game as "Budget MeleeRange"
        # while the site went on showing the slash.
        name = tag_name("Tormented Demon", "Budget Melee/Range")
        self.assertEqual(name, "Tormented Demon Budget Melee Range")
        for char in "</>:":
            self.assertNotIn(char, name)

    def test_generic_variants_do_not_repeat_the_activity(self):
        self.assertEqual(tag_name("Sarachnis", "Setup"), "Sarachnis")
        self.assertEqual(tag_name("Vorkath", "Ranged"), "Vorkath Ranged")

    def test_long_names_drop_the_parenthetical_rather_than_truncating(self):
        name = tag_name("Fortis Colosseum", "Melee only (not recommended for first quiver)")
        self.assertEqual(name, "Fortis Colosseum Melee only")
        self.assertLessEqual(len(name), 60)

    def test_long_names_never_cut_mid_word(self):
        name = tag_name("A" * 30, "supercalifragilistic expialidocious extravaganza")
        self.assertLessEqual(len(name), 60)
        self.assertFalse(name.endswith(" "))
        # Whatever survives must be whole words from the original.
        for word in name.split():
            self.assertIn(word, ("A" * 30 + " supercalifragilistic expialidocious extravaganza").split())


class TestHubImportString(unittest.TestCase):
    """The published RuneLite format, checked against how the plugins read it.

    Both importers are transcribed here rather than pointed at: the failure
    these guard against is a string that looks fine and imports as something
    else, which only a parse can catch.
    """

    LAYOUT = {"1": 4151, "4": 995, "12": 995, "55": 565}

    def code(self, name="Test Tag", icon=995, layout=None):
        return build_hub_import_string(name, icon, layout or self.LAYOUT)

    def test_prefix_is_what_both_importers_dispatch_on(self):
        # TabInterface picks importBtlTag over importTag on this prefix, and the
        # hub plugin rejects anything without it.
        self.assertTrue(self.code().startswith("banktaglayoutsplugin:Test Tag,"))

    def test_pairs_are_item_then_position(self):
        # The built-in format is the other way round, so a swap here would put
        # every item in the slot numbered after its own id.
        self.assertIn("4151:1", self.code())
        self.assertNotIn("1:4151", self.code())

    def test_round_trips_to_the_layout_it_was_built_from(self):
        parsed = parse_hub_import_string(self.code())
        self.assertEqual(parsed["layout"], {k: int(v) for k, v in self.LAYOUT.items()})
        self.assertEqual(parsed["tagName"], "Test Tag")
        self.assertEqual(parsed["icon"], 995)

    def test_every_laid_out_item_is_also_tagged(self):
        # Neither importer tags anything from the layout pairs; an item missing
        # from the tail is laid out into a tab that then filters it away.
        parsed = parse_hub_import_string(self.code())
        # Negatives are variant tags (TestVariantTags); the exact ones must
        # cover the layout on their own for an importer that ignores the sign.
        self.assertEqual(
            {i for i in parsed["tagItems"] if i > 0},
            set(int(v) for v in self.LAYOUT.values()),
        )

    def test_a_repeated_item_is_tagged_once(self):
        # A tag is a set and a layout is not: 24 pure essence is one tagged item.
        parsed = parse_hub_import_string(self.code())
        self.assertEqual(parsed["tagItems"].count(995), 1)

    def test_name_is_the_same_on_both_halves(self):
        # The hub importer takes the name from the prefix and the built-in one
        # skips the tail's copy, so a disagreement is silent in both.
        code = self.code()
        head = code.split(",")[0][len("banktaglayoutsplugin:") :]
        tail = next(p for p in code.split(",") if p.startswith("banktag:"))
        self.assertEqual(head, tail[len("banktag:") :])

    def test_an_empty_field_is_rejected(self):
        # RuneLite splits with omitEmptyStrings(), so an empty field would be
        # dropped rather than noticed - and dropping one shifts the layout.
        with self.assertRaises(ValueError):
            parse_hub_import_string(self.code().replace("banktag:", ",banktag:"))

    def test_a_missing_tag_section_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_hub_import_string("banktaglayoutsplugin:Test Tag,4151:1")

    def test_the_builtin_format_is_not_accepted(self):
        with self.assertRaises(ValueError):
            parse_hub_import_string("banktags,1,Test Tag,995,layout,1,4151")


class TestVariantTags(unittest.TestCase):
    """A tag that accepts the item a player actually owns.

    RuneLite reads a negative id in the tag list as "this item and every
    variant of it" (TagManager.getItemId: abs, canonicalize, then
    -ItemVariationMapping.map). Its layouts already draw a variant in the
    original's slot, but only once the variant passes the tag filter, so an
    exact-only tag left an Infernal cape (l) owner looking at a faded slot.
    """

    def test_gear_also_accepts_its_variants(self):
        self.assertEqual(tag_tail([21295, 6685]), [21295, 6685, -21295, -6685])

    def test_doses_and_charges_stay_exact(self):
        # A variant tag on a potion pulls every dose into the tab.
        self.assertEqual(tag_tail([21295, 6685], exact_only={6685}), [21295, 6685, -21295])

    def test_curated_equivalents_follow_and_accept_variants_too(self):
        tail = tag_tail([21295, 6685], equivalents={21295: [21285]})
        self.assertEqual(tail, [21295, 6685, -21295, -6685, 21285, -21285])

    def test_nothing_is_listed_twice(self):
        tail = tag_tail([1, 2, 1], equivalents={1: [2, 3], 2: [3]})
        self.assertEqual(len(tail), len(set(tail)))
        self.assertEqual(tail[:2], [1, 2])

    def test_string_carries_the_tail_and_still_round_trips(self):
        code = build_hub_import_string(
            "Inferno", 21295, {"8": 21295, "4": 6685},
            exact_only={6685}, equivalents={21295: [21285]},
        )
        self.assertTrue(code.endswith(",banktag:Inferno,21295,6685,21295,-21295,21285,-21285"))
        parsed = parse_hub_import_string(code)
        self.assertEqual(parsed["layout"], {"8": 21295, "4": 6685})
        self.assertEqual(parsed["tagItems"], [6685, 21295, -21295, 21285, -21285])

    def test_a_malformed_negative_is_still_rejected(self):
        with self.assertRaises(ValueError):
            parse_hub_import_string("banktaglayoutsplugin:T,1:1,banktag:T,1,1,--5")

    def test_an_equivalent_that_resolves_to_nothing_fails_the_build(self):
        # Same contract as overrides.json: a curated entry that stops matching
        # must be fixed or deleted, not left to rot.
        self.assertEqual(equivalence_errors([]), [])
        errors = equivalence_errors(["Infernal max cape"])
        self.assertEqual(len(errors), 1)
        self.assertIn("Infernal max cape", errors[0])


class TestOfficialImportString(unittest.TestCase):
    """The official client's format, added to OSRS on 15 July 2026."""

    def test_header_is_version_then_item_count(self):
        code = build_official_import_string({"0": 4151, "9": 995})
        self.assertEqual(code.split(",")[:2], ["1", "2"])

    def test_position_becomes_column_then_row(self):
        # Position 9 is column 1 of row 1 on the 8-wide grid. Column and row the
        # wrong way round still parses, which is why this is pinned.
        self.assertEqual(build_official_import_string({"9": 995}), "1,1,995,1,1")

    def test_round_trips_to_the_layout_it_was_built_from(self):
        layout = {"1": 4151, "4": 995, "12": 995, "55": 565}
        self.assertEqual(
            parse_official_import_string(build_official_import_string(layout)),
            {k: int(v) for k, v in layout.items()},
        )

    def test_a_count_that_disagrees_with_the_body_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_official_import_string("1,3,995,0,0")

    def test_the_runelite_formats_are_not_accepted(self):
        for code in (
            "banktags,1,Test Tag,995,layout,1,4151",
            "banktaglayoutsplugin:Test Tag,4151:1,banktag:Test Tag,995,4151",
        ):
            with self.assertRaises(ValueError):
                parse_official_import_string(code)


class TestLoadoutBlocks(unittest.TestCase):
    """`{{Loadout}}` carries a whole setup in one call.

    Zulrah and Treasure Trails moved to it in September 2026 and stopped
    transcluding `{{Inventory}}`, so both fell out of the library without a
    single error: 13 layouts gone, hidden in the activity count by new pages.
    """

    ZULRAH = (
        "==Equipment==\n{{Recommended equipment|style = Magic|head1 = Ancestral hat}}\n"
        "==Inventory Setups==\n<tabber>\nMax Magic & Ranged=\n{{Loadout\n"
        "|loadoutname = Zulrah (max hybrid)\n|loadouticon = Pet Snakeling\n"
        "|align = right\n|display = stacked\n|head = Ancestral hat\n"
        "|weapon = Eye of Ayak\n|shield = Elidinis' ward (f)\n|1=Twisted bow\n"
        "|2=Masori body (f)\n|28 = Shark\n|rune1 = Blood rune\n|rune4 = Death rune\n}}\n"
        "|-|\nMagic Only=\n{{Loadout\n|head = Ahrim's hood\n|1=Shark\n}}\n</tabber>"
    )

    def test_each_block_is_one_setup_named_by_its_tab(self):
        setups, warnings = extract_page("Zulrah/Strategies", self.ZULRAH)
        self.assertEqual([s.variant for s in setups], ["Magic Only", "Max Magic & Ranged"])
        hybrid = setups[1]
        self.assertEqual(hybrid.equipment["weapon"], "Eye of Ayak")
        self.assertEqual(hybrid.inventory["1"], "Twisted bow")
        self.assertEqual(hybrid.inventory["28"], "Shark")
        self.assertEqual(hybrid.runes, {"1": "Blood rune", "4": "Death rune"})
        self.assertEqual(warnings, [])

    def test_display_arguments_are_not_items(self):
        setups, _ = extract_page("Zulrah/Strategies", self.ZULRAH)
        hybrid = setups[1]
        everything = {**hybrid.equipment, **hybrid.inventory, **hybrid.runes}.values()
        for leaked in ("right", "stacked", "Pet Snakeling", "Zulrah (max hybrid)"):
            self.assertNotIn(leaked, everything)

    def test_outside_a_tab_the_block_names_itself(self):
        text = (
            "==Medium clue scrolls==\n{{Loadout|loadoutname = Medium clues"
            "|head = Graceful hood|1 = Spade}}\n"
            "{{Loadout|loadoutname = Medium clues (implings)|1 = Butterfly net}}\n"
        )
        setups, _ = extract_page("Treasure Trails/Strategies", text)
        self.assertEqual(
            [s.variant for s in setups], ["Medium clues", "Medium clues (implings)"]
        )

    def test_does_not_steal_a_neighbouring_equipment_block(self):
        # The older templates in the same scope still pair among themselves.
        text = (
            "<tabber>\nMelee=\n{{Equipment|head = Torva full helm}}\n"
            "{{Inventory|1 = Shark}}\n|-|\nRanged=\n"
            "{{Loadout|head = Masori mask (f)|1 = Anglerfish}}\n</tabber>"
        )
        setups, warnings = extract_page("X/Strategies", text)
        by_variant = {s.variant: s for s in setups}
        self.assertEqual(by_variant["Melee"].inventory, {"1": "Shark"})
        self.assertEqual(by_variant["Ranged"].equipment, {"head": "Masori mask (f)"})
        self.assertEqual(warnings, [])


class TestExtractPairing(unittest.TestCase):
    def test_tabber_pairs_equipment_with_inventory(self):
        text = (
            "<tabber>\nMelee=\n{{Equipment|head = Torva full helm}}\n"
            "{{Inventory|1 = Shark}}\n|-|\nRanged=\n"
            "{{Equipment|head = Masori mask (f)}}\n{{Inventory|1 = Anglerfish}}\n</tabber>"
        )
        setups, warnings = extract_page("X/Strategies", text)
        self.assertEqual([s.variant for s in setups], ["Melee", "Ranged"])
        self.assertEqual(setups[0].equipment["head"], "Torva full helm")
        self.assertEqual(setups[1].inventory["1"], "Anglerfish")
        self.assertEqual(warnings, [])

    def test_blocked_table_layout_pairs_by_column(self):
        # Chambers of Xeric lists all equipment, then all inventories.
        text = (
            "===Setups===\n{|\n!Minimum\n!Max\n|-\n"
            "|{{Equipment|head = Helm of neitiznot}}\n"
            "|{{Equipment|head = Torva full helm}}\n|-\n"
            "|{{Inventory|1 = Shark}}\n|{{Inventory|1 = Anglerfish}}\n|}\n"
        )
        setups, _ = extract_page("X/Strategies", text)
        by_variant = {s.variant: s for s in setups}
        self.assertEqual(set(by_variant), {"Minimum", "Max"})
        self.assertEqual(by_variant["Minimum"].equipment["head"], "Helm of neitiznot")
        self.assertEqual(by_variant["Max"].inventory["1"], "Anglerfish")

    def test_stray_equipment_in_prose_section_is_dropped(self):
        # Nex illustrates transportation gear; it is not a loadout.
        text = "==Transportation==\n{{Equipment|head = Slayer helmet}}\n"
        setups, warnings = extract_page("Nex/Strategies", text)
        self.assertEqual(setups, [])
        self.assertTrue(any("unpaired" in w for w in warnings))

    def test_gear_only_tab_is_kept_when_page_offers_nothing_better(self):
        text = (
            "<tabber>\nMelee=\n{{Equipment|head = Torva full helm|torso = Torva platebody"
            "|legs = Torva platelegs|weapon = Scythe of vitur|boots = Primordial boots"
            "|gloves = Ferocious gloves}}\n</tabber>"
        )
        setups, _ = extract_page("X/Strategies", text)
        self.assertEqual(len(setups), 1)
        self.assertEqual(setups[0].variant, "Melee")
        self.assertEqual(setups[0].inventory, {})

    def test_gear_fragment_is_not_published(self):
        # A two-slot block is an illustration, not a loadout.
        text = "<tabber>\nLarvae=\n{{Equipment|weapon = Darklight|shield = Book of the dead}}\n</tabber>"
        setups, _ = extract_page("X/Strategies", text)
        self.assertEqual(setups, [])

    def test_complete_setups_suppress_redundant_gear_tables(self):
        # The ==Equipment== table is the source the full setup already reflects.
        text = (
            "==Equipment==\n<tabber>\nRanged=\n"
            "{{Equipment|head = Masori mask (f)|torso = Masori body (f)|legs = Masori chaps (f)"
            "|weapon = Twisted bow|boots = Pegasian boots|gloves = Zaryte vambraces}}\n</tabber>\n"
            "==Inventory setups==\n<tabber>\nMax=\n"
            "{{Equipment|head = Masori mask (f)|weapon = Twisted bow}}\n"
            "{{Inventory|1 = Anglerfish}}\n</tabber>\n"
        )
        setups, _ = extract_page("X/Strategies", text)
        self.assertEqual([s.variant for s in setups], ["Max"])


class TestTwoHandedOffhand(unittest.TestCase):
    """A two-handed weapon leaves no hand for an off-hand.

    {{Recommended equipment}} ranks each slot independently, so its shield column
    describes the off-hands belonging to the *lower-ranked one-handed* weapons.
    Reading rank 1 from every column once put Tumeken's shadow beside an
    Elidinis' ward in 64 of 330 published setups.
    """

    def _magic_setup(self, weapon: str) -> str:
        return (
            "<tabber>\nMagic=\n"
            "{{Recommended equipment"
            f"|weapon1 = {{{{plink|{weapon}}}}}"
            "|weapon2 = {{plink|Sanguinesti staff}}"
            "|shield1 = {{plink|Elidinis' ward (f)}}"
            "|head1 = {{plink|Ancestral hat}}}}\n"
            "{{Inventory|1 = Shark}}\n</tabber>"
        )

    def test_two_handed_weapon_drops_the_offhand(self):
        setups, warnings = extract_page(
            "X/Strategies", self._magic_setup("Tumeken's shadow"), TWO_HANDED
        )
        self.assertEqual(len(setups), 1)
        self.assertEqual(setups[0].equipment["weapon"], "Tumeken's shadow")
        self.assertNotIn("shield", setups[0].equipment)
        self.assertTrue(any("dropped off-hand" in w for w in warnings))

    def test_one_handed_weapon_keeps_its_offhand(self):
        # The guard against over-correcting: most setups legitimately pair a
        # one-handed weapon with a shield and must be left alone.
        setups, warnings = extract_page(
            "X/Strategies", self._magic_setup("Sanguinesti staff"), TWO_HANDED
        )
        self.assertEqual(setups[0].equipment["shield"], "Elidinis' ward (f)")
        self.assertEqual(warnings, [])

    def test_the_2h_parameter_path_also_clears_the_shield(self):
        # Only 6 blocks in the corpus use `2h`; 176 put a two-hander in
        # `weapon1`. Both routes must end up in the same place.
        text = (
            "<tabber>\nRanged=\n"
            "{{Recommended equipment"
            "|2h1 = {{plink|Twisted bow}}"
            "|shield1 = {{plink|Twisted buckler}}"
            "|head1 = {{plink|Masori mask (f)}}}}\n"
            "{{Inventory|1 = Shark}}\n</tabber>"
        )
        setups, _ = extract_page("X/Strategies", text, TWO_HANDED)
        self.assertEqual(setups[0].equipment["weapon"], "Twisted bow")
        self.assertNotIn("shield", setups[0].equipment)

    def test_inherited_gear_is_corrected_too(self):
        # Gear tabs sharing one inventory copy the equipment dict, so the rule
        # has to run after merging or the copies keep the bad shield.
        text = (
            "<tabber>\nBudget=\n"
            "{{Recommended equipment|weapon1 = {{plink|Scythe of vitur}}"
            "|shield1 = {{plink|Avernic defender}}|head1 = {{plink|Torva full helm}}}}\n"
            "</tabber>\n"
            "===Inventory===\n{{Inventory|1 = Shark}}\n"
        )
        setups, _ = extract_page("X/Strategies", text, TWO_HANDED)
        self.assertTrue(setups)
        for setup in setups:
            self.assertNotIn("shield", setup.equipment)

    def test_none_means_the_slot_stays_empty(self):
        # The wiki writes the rule out longhand; read literally it put a
        # blowpipe in the shield slot of two published layouts.
        self.assertEqual(plink_item("None if using [[two-handed weapons]]."), "")
        self.assertEqual(
            plink_item(
                "None if using two-handed weapon, such as {{plinkp|Toxic blowpipe}}"
            ),
            "",
        )

    def test_empty_named_items_are_not_swallowed(self):
        # 24 real items start with "Empty", so the rule must not cover them.
        self.assertEqual(plink_item("{{plink|Empty bucket}}"), "Empty bucket")

    def test_nothing_is_dropped_without_a_conflict(self):
        equipment = {"weapon": "Emberlight", "shield": "Avernic defender"}
        self.assertIsNone(drop_conflicting_offhand(equipment, TWO_HANDED))
        self.assertEqual(equipment["shield"], "Avernic defender")


class TestAlternatives(unittest.TestCase):
    """The wiki ranks every slot; the site lets players step down that ranking."""

    def setUp(self):
        # fits_slot consults the wiki's slot data; inject a small one.
        self._saved = extract_module.SLOT_OF_NAME
        extract_module.SLOT_OF_NAME = {
            "abyssal whip": "weapon",
            "scythe of vitur": "2h",
            "ghrazi rapier": "weapon",
            "avernic defender": "shield",
            "twisted buckler": "shield",
            "ruby bolts (e)": "ammo",
        }

    def tearDown(self):
        extract_module.SLOT_OF_NAME = self._saved

    def test_every_item_in_a_rank_is_offered(self):
        self.assertEqual(
            plink_items("{{plink|Max cape}} / {{plink|Hitpoints cape}}"),
            ["Max cape", "Hitpoints cape"],
        )

    def test_repeats_are_collapsed(self):
        self.assertEqual(
            plink_items("{{plink|Fire cape}} > {{plink|Fire cape}}"), ["Fire cape"]
        )

    def test_footnote_contents_are_not_offered(self):
        # Callisto qualifies a crossbow with "Only if using {{plink|Ruby bolts
        # (e)}}"; mining the footnote put ammo in the weapon ladder.
        self.assertEqual(
            plink_items(
                "{{plink|Rune crossbow}}<ref>Only if using {{plink|Ruby bolts (e)}}</ref>"
            ),
            ["Rune crossbow"],
        )

    def test_a_link_into_the_page_is_not_an_item(self):
        # Abyssal Sire's ammo slot points at "[[#Phase 1 equipment]]".
        self.assertEqual(plink_items("[[#Phase 1 equipment|see above]]"), [])

    def test_ranks_come_out_best_first(self):
        alts = recommended_alternatives(
            {
                "weapon1": "{{plink|Scythe of vitur}}",
                "weapon3": "{{plink|Ghrazi rapier}}",
                "weapon2": "{{plink|Abyssal whip}}",
            }
        )
        self.assertEqual(
            alts["weapon"], ["Scythe of vitur", "Abyssal whip", "Ghrazi rapier"]
        )

    def test_two_handers_join_the_weapon_ladder(self):
        alts = recommended_alternatives(
            {"2h1": "{{plink|Scythe of vitur}}", "weapon2": "{{plink|Abyssal whip}}"}
        )
        self.assertEqual(alts["weapon"], ["Scythe of vitur", "Abyssal whip"])

    def test_items_that_belong_elsewhere_are_dropped(self):
        # Gemstone Crab lists "darts with {{plink|Twisted buckler}}" under
        # `weapon`; a buckler is not a weapon.
        alts = recommended_alternatives(
            {"weapon1": "{{plink|Abyssal whip}} with {{plink|Twisted buckler}}"}
        )
        self.assertEqual(alts["weapon"], ["Abyssal whip"])

    def test_a_shield_ladder_keeps_only_shields(self):
        alts = recommended_alternatives(
            {"shield1": "{{plink|Avernic defender}} / {{plink|Abyssal whip}}"}
        )
        self.assertEqual(alts["shield"], ["Avernic defender"])

    def test_the_worn_item_is_the_head_of_its_ladder(self):
        args = {
            "weapon1": "{{plink|Scythe of vitur}}",
            "weapon2": "{{plink|Abyssal whip}}",
            "shield1": "{{plink|Avernic defender}}",
        }
        equipment = recommended_equipment(args)
        alts = recommended_alternatives(args)
        for slot, item in equipment.items():
            self.assertEqual(item, alts[slot][0])

    def test_the_shield_ladder_survives_a_two_handed_weapon(self):
        # This is what lets the site put the off-hand back when the player
        # steps the weapon down to a one-handed option.
        text = (
            "<tabber>\nMelee=\n"
            "{{Recommended equipment|weapon1 = {{plink|Scythe of vitur}}"
            "|weapon2 = {{plink|Ghrazi rapier}}"
            "|shield1 = {{plink|Avernic defender}}"
            "|shield2 = {{plink|Twisted buckler}}}}\n"
            "{{Inventory|1 = Shark}}\n</tabber>"
        )
        setups, _ = extract_page("X/Strategies", text, {"scythe of vitur"})
        setup = setups[0]
        self.assertNotIn("shield", setup.equipment)
        self.assertEqual(
            setup.alternatives["shield"], ["Avernic defender", "Twisted buckler"]
        )


class TestOverrides(unittest.TestCase):
    """Curated corrections, kept separate from the faithful extraction."""

    RULE = {
        "activity": "Abyss",
        "variants": "*",
        "reason": "The colossal pouch is made from the four smaller ones.",
        "inventory": {
            "replaceAll": ["Small pouch", "Medium pouch", "Large pouch", "Giant pouch"],
            "with": ["Colossal pouch"],
        },
    }

    def _page(self, inventory):
        return [
            {
                "page": "Abyss/Strategies",
                "setups": [{"variant": "Defensive", "equipment": {}, "inventory": inventory,
                            "runes": {}}],
            }
        ]

    def test_replacement_takes_the_first_slot_and_gaps_close(self):
        inventory = {"1": "Giant pouch", "2": "Large pouch", "3": "Pure essence",
                     "4": "Small pouch", "5": "Medium pouch", "6": "Pure essence"}
        out = replace_in_inventory(
            inventory, self.RULE["inventory"]["replaceAll"], ["Colossal pouch"]
        )
        self.assertEqual(
            out, {"1": "Colossal pouch", "2": "Pure essence", "3": "Pure essence"}
        )
        # Contiguous: a tab with holes in it looks like a parsing failure.
        self.assertEqual(list(out), [str(n) for n in range(1, len(out) + 1)])

    def test_a_partial_match_is_not_applied(self):
        # Missing the giant pouch: the rule no longer describes this setup, and
        # applying half of it would produce something the wiki never said.
        inventory = {"1": "Small pouch", "2": "Medium pouch", "3": "Large pouch"}
        self.assertIsNone(
            replace_in_inventory(
                inventory, self.RULE["inventory"]["replaceAll"], ["Colossal pouch"]
            )
        )

    def test_applying_marks_the_setup_and_records_the_reason(self):
        pages = self._page({"1": "Small pouch", "2": "Medium pouch",
                            "3": "Large pouch", "4": "Giant pouch"})
        changed, stale = apply_overrides(pages, [self.RULE])
        setup = pages[0]["setups"][0]
        self.assertEqual(changed, 1)
        self.assertEqual(stale, [])
        self.assertTrue(setup["curated"])
        self.assertIn("colossal", setup["curationReason"].lower())

    def test_an_override_that_matches_nothing_is_reported(self):
        # This is what makes the file self-cleaning: once the wiki catches up the
        # rule stops matching, validation fails, and it gets deleted rather than
        # rotting into a second source of staleness.
        pages = self._page({"1": "Colossal pouch", "2": "Pure essence"})
        changed, stale = apply_overrides(pages, [self.RULE])
        self.assertEqual(changed, 0)
        self.assertEqual(len(stale), 1)
        self.assertIn("Abyss", stale[0])

    def test_a_rule_only_touches_the_variants_it_names(self):
        rule = dict(self.RULE, variants=["Graceful"])
        self.assertFalse(targets(rule, "Abyss", "Defensive"))
        self.assertTrue(targets(rule, "Abyss", "Graceful"))
        self.assertFalse(targets(rule, "Zulrah", "Graceful"))


class TestCurated(unittest.TestCase):
    """Whole setups for an activity the wiki has no loadout for yet."""

    ENTRY = {
        "activity": "The Fractured Archive",
        "sourcePage": "The Fractured Archive/Strategies",
        "reason": "Curated from launch-day play; the wiki had no loadout yet.",
        "prepots": ["Divine ranging potion(4)", "Saturated heart"],
        "setups": [
            {
                "variant": "Setup",
                "equipment": {"weapon": "Zaryte crossbow"},
                "inventory": {"1": "Twisted bow"},
                "runes": {"1": "Blood rune"},
            }
        ],
    }
    # What the page looked like on release day: an empty equipment tabber.
    EMPTY = "==Equipment==\n<tabber>\n\n</tabber>\n==Bosses==\nProse only."
    CAUGHT_UP = (
        "==Equipment==\n<tabber>\nMax=\n{{Loadout\n|weapon = Twisted bow\n"
        "|1 = Shark\n}}\n</tabber>"
    )

    def _doc(self, pages=None, empty=None):
        return {"pages": pages or [], "emptyPages": empty or []}

    def _fetch(self, text, rev=123):
        return lambda title: (text, rev)

    def test_an_entry_becomes_a_page_shaped_like_extraction(self):
        doc = self._doc()
        notes = add_curated(doc, [self.ENTRY], self._fetch(self.EMPTY, 15378036))
        self.assertEqual(notes, [])
        page = doc["pages"][0]
        self.assertEqual(page["page"], "The Fractured Archive/Strategies")
        # The live revision of the page it stands in for, so provenance says
        # which version of the page had nothing to offer.
        self.assertEqual(page["revId"], 15378036)
        setup = page["setups"][0]
        self.assertEqual(setup["variant"], "Setup")
        self.assertEqual(setup["equipment"], {"weapon": "Zaryte crossbow"})
        self.assertEqual(setup["prepots"], ["Divine ranging potion(4)", "Saturated heart"])
        self.assertEqual(setup["alternatives"], {})
        self.assertEqual(setup["switches"], [])
        self.assertTrue(setup["curated"])
        self.assertEqual(setup["curationReason"], self.ENTRY["reason"])

    def test_the_page_is_no_longer_reported_as_yielding_nothing(self):
        title = self.ENTRY["sourcePage"]
        doc = self._doc(
            pages=[{"page": title, "revId": 1, "warnings": [], "setups": []}],
            empty=[title],
        )
        add_curated(doc, [self.ENTRY], self._fetch(self.EMPTY))
        self.assertEqual(doc["emptyPages"], [])
        self.assertEqual([p["page"] for p in doc["pages"]], [title])
        self.assertEqual(len(doc["pages"][0]["setups"]), 1)

    def test_an_entry_retires_once_the_page_has_a_setup(self):
        # The same contract as an override that stopped matching: the wiki has
        # caught up, so the build fails until the entry is deleted.
        doc = self._doc()
        notes = add_curated(doc, [self.ENTRY], self._fetch(self.CAUGHT_UP))
        self.assertEqual(doc["pages"], [])
        self.assertEqual(len(notes), 1)
        self.assertIn("The Fractured Archive", notes[0])

    def test_an_entry_retires_once_discovery_finds_the_activity(self):
        discovered = {
            "page": "The Fractured Archive/Strategies",
            "revId": 2,
            "warnings": [],
            "setups": [{"variant": "Max", "equipment": {}, "inventory": {"1": "Shark"},
                        "runes": {}}],
        }
        doc = self._doc(pages=[discovered])
        notes = add_curated(doc, [self.ENTRY], self._fetch(self.EMPTY))
        self.assertEqual(doc["pages"], [discovered])
        self.assertEqual(len(notes), 1)


class TestPrepots(unittest.TestCase):
    """Potions drunk before the fight: banked beside the layout, never carried."""

    EQUIPMENT = {
        "head": 26382, "cape": 28951, "neck": 33639, "ammo": 33595, "ammo2": 21944,
        "weapon": 26374, "torso": 27238, "shield": 21000, "legs": 27241,
        "gloves": 22981, "boots": 31097, "ring": 28307,
    }
    INVENTORY = dict(zip(
        (str(n) for n in range(1, 29)),
        (30634, 21021, 20714, 33631, 12002, 21024, 28313, 22325, 24420, 29801,
         13441, 21295, 24421, 20997, 13441, 6685, 6685, 6685, 10925, 6685, 6685,
         10925, 10925, 6685, 6685, 10925, 27281, 28922),
    ))
    RUNES = {"1": 21880, "2": 565, "3": 4696}
    PREPOTS = [23685, 23733, 23745, 27641]

    # The Fractured Archive's layout as first built and checked by hand: the
    # prepots fill the four gear-band cells the twelve worn items leave free.
    REFERENCE = (
        "banktaglayoutsplugin:The Fractured Archive,26382:0,33639:1,27238:2,27241:3,"
        "31097:4,33595:5,23685:6,23745:7,28951:8,26374:9,21000:10,22981:11,28307:12,"
        "21944:13,23733:14,27641:15,30634:16,20714:17,12002:18,28313:19,24420:20,"
        "13441:21,24421:22,13441:23,21021:24,33631:25,21024:26,22325:27,29801:28,"
        "21295:29,20997:30,6685:31,6685:32,10925:33,6685:34,10925:35,6685:36,"
        "27281:37,6685:40,6685:41,10925:42,6685:43,10925:44,28922:45,21880:48,"
        "565:49,4696:50,banktag:The Fractured Archive,26374"
    )

    def test_zigzag_puts_prepots_straight_after_the_worn_items(self):
        layout = zigzag_layout(self.EQUIPMENT, self.INVENTORY, self.RUNES, self.PREPOTS)
        self.assertEqual(layout, parse_hub_import_string(self.REFERENCE)["layout"])

    def test_without_prepots_zigzag_is_unchanged(self):
        self.assertEqual(
            zigzag_layout(self.EQUIPMENT, self.INVENTORY, self.RUNES, []),
            zigzag_layout(self.EQUIPMENT, self.INVENTORY, self.RUNES),
        )

    def test_presets_put_prepots_in_the_row_under_the_inventory(self):
        out = place_prepots({"4": 1}, self.PREPOTS)
        self.assertEqual(
            out, {"4": 1, "60": 23685, "61": 23733, "62": 23745, "63": 27641}
        )
        # Row 7, the inventory's four columns: below slot 25-28, clear of the
        # rune pouch and of every LOADOUT_MAP position.
        self.assertEqual(PREPOT_POSITIONS, (60, 61, 62, 63))
        self.assertFalse(set(PREPOT_POSITIONS) & set(LOADOUT_MAP))

    def test_more_prepots_than_the_row_holds_is_refused(self):
        with self.assertRaises(ValueError):
            place_prepots({}, [1, 2, 3, 4, 5])


class TestCuratedErrors(unittest.TestCase):
    """A curated name that stops resolving must fail, not vanish."""

    def _entry(self, **over):
        entry = {
            "activity": "The Fractured Archive",
            "variant": "Setup",
            "curated": True,
            "sourceNames": {
                "equipment": {"weapon": "Zaryte crossbow"},
                "inventory": {"1": "Twisted bow", "2": "Obligator"},
                "runes": {"1": "Blood rune"},
            },
            "equipment": {"weapon": 26374},
            "inventory": {"1": 20997, "2": 34715},
            "runes": {"1": 565},
            "sourcePrepots": ["Saturated heart"],
            "prepots": [{"name": "Saturated heart", "id": 27641}],
        }
        entry.update(over)
        return entry

    def test_a_fully_resolved_entry_passes(self):
        self.assertEqual(curated_errors(self._entry()), [])

    def test_a_slot_the_wiki_dropped_is_reported(self):
        # Module:Loadout drops a name it cannot resolve without a word, so
        # nothing else would notice the Obligator going missing.
        errors = curated_errors(self._entry(inventory={"1": 20997}))
        self.assertEqual(len(errors), 1)
        self.assertIn("Obligator", errors[0])

    def test_a_prepot_the_wiki_dropped_is_reported(self):
        errors = curated_errors(self._entry(prepots=[]))
        self.assertEqual(len(errors), 1)
        self.assertIn("Saturated heart", errors[0])

    def test_wiki_layouts_are_not_held_to_it(self):
        # {{Cheap food}} and friends legitimately resolve to nothing at times.
        self.assertEqual(
            curated_errors(self._entry(curated=False, inventory={"1": 20997})), []
        )


class TestRunePouchPairing(unittest.TestCase):
    """A pouch the page publishes must reach the setup it belongs to."""

    def test_pouch_above_the_inventory_still_attaches(self):
        # Pages put the pouch between the gear and the inventory as often as
        # after it. Anchoring on the inventory's end missed every one of those,
        # which left Tombs of Amascut with four of five variants runeless.
        text = (
            "<tabber>\nMelee=\n{{Equipment|head = Torva full helm}}\n"
            "{{Rune pouch|1 = Blood rune|2 = Death rune}}\n"
            "{{Inventory|1 = Shark}}\n|-|\nMagic=\n"
            "{{Equipment|head = Ancestral hat}}\n"
            "{{Rune pouch|1 = Chaos rune|2 = Nature rune}}\n"
            "{{Inventory|1 = Anglerfish}}\n</tabber>"
        )
        setups, _ = extract_page("X/Strategies", text)
        by_variant = {s.variant: s for s in setups}
        self.assertEqual(by_variant["Melee"].runes["1"], "Blood rune")
        self.assertEqual(by_variant["Magic"].runes["2"], "Nature rune")

    def test_a_pouch_is_not_shared_between_setups(self):
        # One pouch, two setups: it belongs to the setup it sits inside, and the
        # other must not inherit it.
        text = (
            "<tabber>\nMelee=\n{{Equipment|head = Torva full helm}}\n"
            "{{Inventory|1 = Shark}}\n|-|\nMagic=\n"
            "{{Equipment|head = Ancestral hat}}\n"
            "{{Rune pouch|1 = Chaos rune}}\n"
            "{{Inventory|1 = Anglerfish}}\n</tabber>"
        )
        setups, _ = extract_page("X/Strategies", text)
        by_variant = {s.variant: s for s in setups}
        self.assertEqual(by_variant["Melee"].runes, {})
        self.assertEqual(by_variant["Magic"].runes["1"], "Chaos rune")


class TestGearBlockChoice(unittest.TestCase):
    """Which block wins when a scope offers more gear than inventories."""

    RICH = (
        "{{Recommended equipment|style = Melee"
        "|head1 = {{plink|Torva full helm}}|neck1 = {{plink|Amulet of rancour}}"
        "|cape1 = {{plink|Infernal cape}}|body1 = {{plink|Torva platebody}}"
        "|legs1 = {{plink|Torva platelegs}}|shield1 = {{plink|Avernic defender}}"
        "|hands1 = {{plink|Ferocious gloves}}|feet1 = {{plink|Primordial boots}}"
        "|ring1 = {{plink|Ultor ring}}|weapon1 = {{plink|Osmumten's fang}}}}"
    )

    def test_the_fuller_block_wins_over_the_nearer_one(self):
        # The Hueycoatl shape: a one-slot table sat closer to the inventory than
        # the real eleven-slot one, so the published melee layout wore nothing
        # but a weapon.
        text = (
            "==Setup==\n" + self.RICH + "\n"
            "{{Recommended equipment|style = Melee|weapon1 = {{plink|Dragon hunter lance}}}}\n"
            "{{Inventory|1 = Shark}}\n"
        )
        setups, _ = extract_page("X/Strategies", text)
        self.assertEqual(len(setups), 1)
        self.assertGreaterEqual(len(setups[0].equipment), 10)
        self.assertEqual(setups[0].equipment["head"], "Torva full helm")

    def test_a_concrete_setup_beats_an_upgrades_table(self):
        # Barrows shape: the hand-authored {{Equipment}} is the loadout; the
        # {{Recommended equipment}} beside it is the upgrades reference.
        text = (
            "==Setup==\n"
            "{{Equipment|head = Helm of neitiznot|cape = Fire cape|neck = Amulet of fury"
            "|weapon = Abyssal whip|torso = Bandos chestplate|legs = Bandos tassets"
            "|shield = Dragon defender|gloves = Barrows gloves|boots = Dragon boots"
            "|ring = Berserker ring}}\n" + self.RICH + "\n"
            "{{Inventory|1 = Shark}}\n"
        )
        setups, _ = extract_page("X/Strategies", text)
        self.assertEqual(len(setups), 1)
        self.assertEqual(setups[0].equipment["head"], "Helm of neitiznot")

    def test_the_unpaired_block_is_still_reported(self):
        text = (
            "==Setup==\n" + self.RICH + "\n"
            "{{Recommended equipment|style = Melee|weapon1 = {{plink|Dragon hunter lance}}}}\n"
            "{{Inventory|1 = Shark}}\n"
        )
        _, warnings = extract_page("X/Strategies", text)
        self.assertTrue(any("unpaired" in w for w in warnings))


class TestSwitches(unittest.TestCase):
    """`special` is a spec weapon to bring, not a slot to wear."""

    def test_special_ranks_are_captured_best_first(self):
        args = {
            "style": "Melee",
            "special2": "{{plink|Bandos godsword}}",
            "special1": "{{plink|Voidwaker}}",
            "head1": "{{plink|Torva full helm}}",
        }
        self.assertEqual(recommended_switches(args), ["Voidwaker", "Bandos godsword"])

    def test_switches_never_become_worn_equipment(self):
        args = {"special1": "{{plink|Dragon claws}}", "head1": "{{plink|Torva full helm}}"}
        self.assertNotIn("special", recommended_equipment(args))
        self.assertEqual(list(recommended_equipment(args)), ["head"])

    def test_a_rank_naming_several_weapons_keeps_them_all(self):
        args = {"special1": "{{plink|Voidwaker}} / {{plink|Dragon claws}}"}
        self.assertEqual(recommended_switches(args), ["Voidwaker", "Dragon claws"])

    def test_extraction_carries_switches_onto_the_setup(self):
        text = (
            "==Setup==\n{{Recommended equipment|style = Melee"
            "|head1 = {{plink|Torva full helm}}|special1 = {{plink|Voidwaker}}}}\n"
            "{{Inventory|1 = Shark}}\n"
        )
        setups, _ = extract_page("X/Strategies", text)
        self.assertEqual(setups[0].switches, ["Voidwaker"])


class TestNormalizer(unittest.TestCase):
    """Name correction, against a miniature of the real bucket index.

    Every case here is a name the live wiki resolves the way the assertion
    says; the ids are the real ones.
    """

    INDEX = {
        "version": 2,
        "byName": {
            # A family the wiki spells with a space before the dose.
            "moonlight moth mix (1)": [29213],
            "moonlight moth mix (2)": [29195],
            # A family it spells without one.
            "saradomin brew(3)": [6687],
            "saradomin brew(4)": [6685],
            "overload (1)": [11733],
            "overload (2)": [11732],
            "overload (3)": [11731],
            "overload (4)": [11730],
            "teleport to house": [8013],
        },
        "byPage": {
            "moonlight moth mix": [29213, 29195],
            "saradomin brew": [6687, 6685],
            "overload (nightmare zone)": [11730, 11731, 11732, 11733],
            "teleport to house (tablet)": [8013],
        },
        "byId": {
            "29213": "Moonlight moth mix (1)",
            "29195": "Moonlight moth mix (2)",
            "6687": "Saradomin brew(3)",
            "6685": "Saradomin brew(4)",
            "11733": "Overload (1)",
            "11732": "Overload (2)",
            "11731": "Overload (3)",
            "11730": "Overload (4)",
            "8013": "Teleport to house",
        },
    }

    def setUp(self):
        self.norm = Normalizer(self.INDEX)

    def test_spaced_family_keeps_the_wikis_spacing(self):
        # Rebuilding this as "Moonlight moth mix(2)" names no item at all, so
        # Module:Loadout returns nothing and the slot vanishes from the layout.
        fixed, note = self.norm.normalize("Moonlight moth mix")
        self.assertEqual(fixed, "Moonlight moth mix (2)")
        self.assertEqual(self.norm.resolve_ids(fixed), [29195])
        self.assertTrue(note)

    def test_unspaced_family_is_still_unspaced(self):
        fixed, _ = self.norm.normalize("Saradomin brew")
        self.assertEqual(fixed, "Saradomin brew(4)")
        self.assertEqual(self.norm.resolve_ids(fixed), [6685])

    def test_disambiguated_page_resolves_to_the_full_dose(self):
        # No item is called this, so the wiki falls through to the page and
        # hands back whichever dose comes first - the 3-dose one.
        fixed, _ = self.norm.normalize("Overload (Nightmare Zone)")
        self.assertEqual(fixed, "Overload (4)")
        self.assertEqual(self.norm.resolve_ids(fixed), [11730])

    def test_an_explicit_dose_is_left_alone(self):
        fixed, note = self.norm.normalize("Overload (3)")
        self.assertEqual(fixed, "Overload (3)")
        self.assertIsNone(note)

    def test_resolve_falls_back_to_the_page_name(self):
        # The item is called "Teleport to house"; the setup cites the page.
        # Without this the round-trip check has nothing to compare against.
        self.assertEqual(self.norm.resolve_ids("Teleport to house (tablet)"), [8013])

    def test_every_rewrite_names_a_real_item(self):
        for base in list(self.norm.dose_map) + list(self.norm.page_dose_map):
            fixed, _ = self.norm.normalize(base)
            self.assertTrue(
                self.norm.resolve_ids(fixed),
                f"{base!r} was rewritten to {fixed!r}, which is not an item",
            )


class TestZigzag(unittest.TestCase):
    """Port fidelity against LayoutGenerator.toZigZagIndex."""

    def test_index_sequence_matches_the_plugin(self):
        got = [zigzag_index(i) for i in range(18)]
        self.assertEqual(
            got, [0, 8, 1, 9, 2, 10, 3, 11, 4, 12, 5, 13, 6, 14, 7, 15, 16, 24]
        )

    def test_sixteen_items_fill_exactly_two_rows(self):
        used = sorted(zigzag_index(i) for i in range(16))
        self.assertEqual(used, list(range(16)))

    def test_equipment_uses_runelite_slot_order(self):
        eq = {"head": 1, "cape": 2, "neck": 3, "weapon": 4, "torso": 5}
        layout = zigzag_layout(eq, {}, {})
        # head->0, cape->8, neck->1, weapon->9, torso->2
        self.assertEqual(layout["0"], 1)
        self.assertEqual(layout["8"], 2)
        self.assertEqual(layout["1"], 3)
        self.assertEqual(layout["9"], 4)
        self.assertEqual(layout["2"], 5)

    def test_rune_pouch_is_linear_not_zigzag(self):
        layout = zigzag_layout({"head": 1}, {"1": 2}, {"1": 10, "2": 11, "3": 12})
        rune_positions = sorted(int(p) for p, v in layout.items() if v in (10, 11, 12))
        self.assertEqual(
            rune_positions,
            [rune_positions[0], rune_positions[0] + 1, rune_positions[0] + 2],
        )

    def test_same_items_as_presets(self):
        eq = {"head": 1, "cape": 2, "weapon": 3}
        inv = {str(n): 100 + n for n in range(1, 29)}
        runes = {"1": 560, "2": 565}
        zz = zigzag_layout(eq, inv, runes)
        expected = sorted([1, 2, 3] + [100 + n for n in range(1, 29)] + [560, 565])
        self.assertEqual(sorted(zz.values()), expected)


class TestCompleteness(unittest.TestCase):
    def test_full_setup_is_complete(self):
        status, note = completeness("Araxxor", {f"s{i}": i for i in range(11)},
                                    {str(n): n for n in range(1, 29)})
        self.assertEqual(status, "complete")
        self.assertEqual(note, "")

    def test_known_small_activity_is_minimal_with_a_reason(self):
        status, note = completeness("Tempoross", {"head": 1}, {"1": 2})
        self.assertEqual(status, "minimal")
        self.assertTrue(note)

    def test_unexpected_gap_is_partial(self):
        status, note = completeness("Some Boss", {"head": 1}, {"1": 2})
        self.assertEqual(status, "partial")
        self.assertTrue(note)


class TestPublishedData(unittest.TestCase):
    """Golden checks against real generated output, when it exists."""

    @classmethod
    def setUpClass(cls):
        cls.path = REPO / "data" / "doom-of-mokhaiotl.json"
        if not cls.path.exists():
            raise unittest.SkipTest("run the pipeline first")
        cls.record = json.loads(cls.path.read_text(encoding="utf-8"))

    def test_has_both_wiki_variants(self):
        self.assertEqual(
            sorted(v["variant"] for v in self.record["variants"]),
            ["Budget", "Max Ranged"],
        )

    def test_max_ranged_matches_the_wiki_setup(self):
        v = next(x for x in self.record["variants"] if x["variant"] == "Max Ranged")
        self.assertEqual(v["equipment"]["weapon"], 26374)   # Zaryte crossbow
        self.assertEqual(v["equipment"]["head"], 27235)     # Masori mask (f)
        self.assertEqual(v["equipment"]["ring"], 25975)     # Lightbearer
        self.assertEqual(len(v["inventory"]), 28)

    def test_potions_are_full_dose(self):
        # The wiki resolves a bare "Saradomin brew" to the 3-dose item (6687).
        # Every published layout must carry the 4-dose ones instead.
        for v in self.record["variants"]:
            ids = set(v["layout"].values())
            self.assertIn(6685, ids, "Saradomin brew(4) missing")
            self.assertNotIn(6687, ids, "3-dose Saradomin brew leaked through")
            self.assertNotIn(3026, ids, "3-dose Super restore leaked through")

    def test_import_string_is_well_formed(self):
        for v in self.record["variants"]:
            for field, layout_field in (
                ("importString", "layout"),
                ("importStringZigzag", "layoutZigzag"),
            ):
                parsed = parse_hub_import_string(v[field])
                self.assertEqual(parsed["tagName"], v["tagName"])
                self.assertEqual(parsed["icon"], v["icon"])
                self.assertEqual(
                    parsed["layout"], {k: int(x) for k, x in v[layout_field].items()}
                )
            for field, layout_field in (
                ("importStringOfficial", "layout"),
                ("importStringZigzagOfficial", "layoutZigzag"),
            ):
                self.assertEqual(
                    parse_official_import_string(v[field]),
                    {k: int(x) for k, x in v[layout_field].items()},
                )

    def test_both_styles_carry_the_same_items(self):
        for v in self.record["variants"]:
            self.assertEqual(
                sorted(v["layout"].values()), sorted(v["layoutZigzag"].values())
            )

    def test_no_published_layout_pairs_a_two_hander_with_a_shield(self):
        # Item ids rather than names, so this needs neither the network nor the
        # cached slot index. Every one of these once shipped beside an off-hand.
        # Zaryte crossbow is deliberately absent: it is one-handed and pairs
        # with a Twisted buckler, which is a loadout that must keep working.
        two_handed = {
            27275: "Tumeken's shadow",
            22325: "Scythe of vitur",
            22664: "Scythe of vitur (uncharged)",
            20997: "Twisted bow",
            25865: "Bow of faerdhinen",
            12926: "Toxic blowpipe",
        }
        offenders = []
        for path in sorted((REPO / "data").glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            for variant in record.get("variants", []):
                equipment = variant.get("equipment", {})
                weapon = equipment.get("weapon")
                if weapon in two_handed and equipment.get("shield"):
                    offenders.append(
                        f"{path.stem} / {variant['variant']}: {two_handed[weapon]}"
                        f" + shield {equipment['shield']}"
                    )
        self.assertEqual(offenders, [])

    def test_no_weapon_sits_in_the_shield_slot(self):
        # 12926 = Toxic blowpipe, mined out of "None if using two-handed
        # weapon, such as {{plinkp|Toxic blowpipe}}".
        for path in sorted((REPO / "data").glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            for variant in record.get("variants", []):
                self.assertNotEqual(
                    variant.get("equipment", {}).get("shield"),
                    12926,
                    f"{path.stem} / {variant['variant']} wears a blowpipe as an off-hand",
                )

    def test_abyss_carries_a_colossal_pouch_not_the_four_it_replaces(self):
        path = REPO / "data" / "abyss.json"
        if not path.exists():
            self.skipTest("Abyss data not generated")
        record = json.loads(path.read_text(encoding="utf-8"))
        # 5509/5511/5513/5515 are the small, medium, large and giant pouches -
        # consumed when the colossal pouch is made, so they cannot coexist.
        superseded = {5509, 5510, 5511, 5512, 5513, 5514, 5515, 6819}
        colossal = {26784, 26786, 26906}
        for variant in record["variants"]:
            ids = set(variant["layout"].values())
            self.assertTrue(ids & colossal, f"{variant['variant']} has no colossal pouch")
            self.assertFalse(
                ids & superseded,
                f"{variant['variant']} still carries a superseded pouch",
            )
            self.assertTrue(variant["curated"])
            self.assertTrue(variant["curationReason"])

    def test_kreearra_keeps_its_two_handed_weapon(self):
        path = REPO / "data" / "kreearra.json"
        if not path.exists():
            self.skipTest("Kree'arra data not generated")
        record = json.loads(path.read_text(encoding="utf-8"))
        tbow = next(v for v in record["variants"] if "Tbow" in v["variant"])
        # 20997 = Twisted bow, supplied via the `2h1` argument.
        self.assertEqual(tbow["equipment"].get("weapon"), 20997)


class TestCompletenessGate(unittest.TestCase):
    """The ratchet must survive the wiki growing and still catch lost gear.

    A ratio floor did neither: four new upstream pages - two of them the kind
    MINIMAL_ACTIVITIES blesses as correctly small - dragged the ratio under its
    floor while the number of complete layouts had not changed, and the weekly
    refresh went a month without opening a pull request.
    """

    @staticmethod
    def corpus(complete: int, minimal: int = 0, partial: int = 0) -> list[dict]:
        return (
            [{"completeness": "complete"}] * complete
            + [{"completeness": "minimal"}] * minimal
            + [{"completeness": "partial"}] * partial
        )

    def test_growth_alone_never_trips_the_gate(self):
        # Ratio here is 290/400 = 72.5%, far under any ratio floor this library
        # ever held - yet nothing was lost, so the gate must stay silent.
        grown = self.corpus(COMPLETE_BASELINE, minimal=60, partial=50)
        self.assertEqual(completeness_errors(grown), [])

    def test_losing_one_complete_layout_trips_the_gate(self):
        regressed = self.corpus(COMPLETE_BASELINE - 1, minimal=60, partial=50)
        errors = completeness_errors(regressed)
        self.assertEqual(len(errors), 1)
        self.assertIn(str(COMPLETE_BASELINE - 1), errors[0])

    def test_a_shrinking_corpus_is_judged_on_what_is_left(self):
        # Discovery returning almost nothing: the ratio would read 100%.
        self.assertEqual(len(completeness_errors(self.corpus(3))), 1)

    def test_the_published_library_clears_the_baseline(self):
        index = REPO / "index.json"
        if not index.exists():
            self.skipTest("library not generated")
        counts = json.loads(
            (REPO / "report.json").read_text(encoding="utf-8")
        )["completenessCounts"]
        self.assertGreaterEqual(counts["complete"], COMPLETE_BASELINE)


class TestRefreshChurn(unittest.TestCase):
    """The weekly refresh must produce a diff only when a layout really moved.

    Its own comment has always promised that. Two fields broke the promise on
    their own: `generatedAt`, rewritten every run, and `sourceRevId`, rewritten
    whenever the wiki page moved at all. A run that found the wiki completely
    unchanged still opened a pull request, and a week of ordinary prose edits
    rewrote three quarters of `data/` - burying the real changes the review
    exists to catch.
    """

    def test_recorded_revision_survives_an_edit_that_changed_no_setup(self):
        previous = {"vardorvis": ("abc123", 15082725)}
        self.assertEqual(
            stable_rev_id("vardorvis", "abc123", 15316292, previous), 15082725
        )

    def test_a_real_content_change_takes_the_new_revision(self):
        previous = {"vardorvis": ("abc123", 15082725)}
        self.assertEqual(
            stable_rev_id("vardorvis", "def456", 15316292, previous), 15316292
        )

    def test_a_new_activity_takes_the_revision_it_came_from(self):
        self.assertEqual(stable_rev_id("tangleroot", "abc123", 15316292, {}), 15316292)

    def test_a_timestamp_only_change_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            first = {"generatedAt": "2026-09-03T22:27:59Z", "layoutCount": 340}
            self.assertTrue(write_unless_only_timestamp(path, first, indent=2))
            before = path.read_bytes()

            later = {"generatedAt": "2026-09-04T02:29:54Z", "layoutCount": 340}
            self.assertFalse(write_unless_only_timestamp(path, later, indent=2))
            # Byte-identical: the old timestamp is kept rather than rewritten.
            self.assertEqual(path.read_bytes(), before)

    def test_a_real_change_is_written_even_though_the_timestamp_moved_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            write_unless_only_timestamp(
                path, {"generatedAt": "2026-09-03T22:27:59Z", "layoutCount": 340}
            )
            self.assertTrue(
                write_unless_only_timestamp(
                    path, {"generatedAt": "2026-09-04T02:29:54Z", "layoutCount": 341}
                )
            )
            self.assertEqual(json.loads(path.read_text())["layoutCount"], 341)

    def test_a_missing_file_is_always_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "does-not-exist.json"
            self.assertTrue(write_unless_only_timestamp(path, {"generatedAt": "x"}))
            self.assertTrue(path.exists())


def _variant(layout, completeness="complete"):
    return {"layout": layout, "completeness": completeness}


class TestClueSupplies(unittest.TestCase):
    """Rows below a clue layout for what later steps ask for."""

    FALO = (
        "{| class=\"wikitable lighttable\"\n!Lyric\n! colspan=\"2\" |Item\n|-\n"
        "| rowspan=\"2\" |A blood red weapon, a strong curved sword.\n"
        "|{{plinkt|Dragon scimitar}}\n|-\n|{{plinkt|Dragon scimitar (or)}}\n|-\n"
        "|A mark used to increase one's grace.\n|{{plinkt|Mark of Grace}}\n|-\n"
        "| rowspan=\"2\" |A book that preaches.\n"
        "|{{plinkt|God book|pic=Book of Balance}}<ref group=n name=god>Any book.</ref>\n"
        "|-\n|{{plinkt|God book (or)|pic=Book of Balance (or)}}\n|}"
    )

    def test_falo_takes_one_item_per_verse(self):
        self.assertEqual(
            falo_items(self.FALO), ["Dragon scimitar", "Mark of Grace", "Book of Balance"]
        )

    SHERLOCK = (
        "{| class=\"wikitable\"\n!Task\n!Skill\n!Other\n!Items required\n|-\n"
        "|Equip a whip.\n|{{SCP|Attack|70}}\n|None\n"
        "|[[Abyssal whip]] ([[Abyssal tentacle]] also works, "
        "[[Abyssal whip (or)|ornament]] does not)\n|-\n"
        "|Teleport to a spirit tree.\n|{{SCP|Farming|83}}\n|None\n"
        "|[[Spirit sapling]], [[spade]]. A [[superior garden]] works too.\n\nMore prose.\n"
        "|-\n|Slay a Nechryael.\n|{{SCP|Slayer|80}}\n|None\n|None\n|}"
    )

    def test_sherlock_reads_the_requirement_not_the_notes(self):
        self.assertEqual(
            sherlock_items(self.SHERLOCK), ["Abyssal whip", "Spirit sapling", "Spade"]
        )

    def test_bank_tables_are_keyed_by_their_setup_heading(self):
        text = (
            "===Master clues===\n{{Loadout|1=Spade}}\n====Inventory====\nprose\n"
            "====Bank====\n{|\n|{{plinkt|Ring of visibility}}\n|{{plinkp|Lockpick}}\n|}\n"
            "===Elite clues===\n====Inventory====\n"
        )
        self.assertEqual(
            bank_sections(text), {"Master clues": ["Ring of visibility", "Lockpick"]}
        )

    def test_stash_recipe_per_tier(self):
        rows = [{"production_json": json.dumps({
            "materials": [{"name": "Mahogany plank"}, {"name": "Bronze nails"},
                          {"name": "Gold leaf"}],
            "tools": "Hammer, Saw", "output": {"subtxt": "Master"}})}]
        self.assertEqual(
            stash_materials(rows),
            {"master": ["Mahogany plank", "Bronze nails", "Gold leaf", "Hammer", "Saw"]},
        )

    SOURCES = {
        "revIds": {"Treasure Trails/Strategies": 1, "STASH": 2, "Falo the Bard": 3,
                   "Sherlock/Master": 4, "Sherlock/Elite": 5},
        "bank": {"Master clues": ["Lockpick"], "Beginner clues (free-to-play)": ["Trout"]},
        "stash": {"master": ["Gold leaf"], "beginner": ["Plank"]},
        "falo": ["Basalt"],
        "sherlock": {"master": ["Knife"], "elite": ["Needle"]},
    }

    def test_master_carries_every_group_with_its_source(self):
        groups = groups_for("Master clues", self.SOURCES)
        self.assertEqual(
            [g["group"] for g in groups], ["Bank", "STASH unit", "Falo the Bard", "Sherlock"]
        )
        self.assertEqual(groups[2]["source"], {"page": "Falo the Bard", "revId": 3})

    def test_free_to_play_skips_the_members_only_stash(self):
        groups = groups_for("Beginner clues (free-to-play)", self.SOURCES)
        self.assertEqual([g["group"] for g in groups], ["Bank"])

    def test_supplies_start_after_a_blank_row_and_each_group_on_its_own(self):
        layout = {"1": 10, "55": 11}  # last used row is 6
        placed = place_supplies(layout, [[1, 2, 3], list(range(100, 109)), [7]])
        self.assertEqual(placed["1"], 10)
        self.assertEqual([placed[str(p)] for p in (64, 65, 66)], [1, 2, 3])
        self.assertNotIn("67", placed)
        # Nine items run onto a second row; the next group starts after them.
        self.assertEqual(placed["72"], 100)
        self.assertEqual(placed["80"], 108)
        self.assertEqual(placed["88"], 7)

    def test_a_short_layout_still_keeps_supplies_out_of_the_grid(self):
        # Master clues stops at row 4; its supplies once landed in the rune
        # pouch row, at positions that are not grid slots.
        placed = place_supplies({"1": 10, "39": 11}, [[1]])
        self.assertEqual(placed["64"], 1)
        self.assertNotIn("48", placed)

    def test_no_groups_leaves_the_layout_alone(self):
        self.assertEqual(place_supplies({"1": 10}, [[], []]), {"1": 10})


class TestRefreshClassifier(unittest.TestCase):
    """Which refreshes merge themselves, and which wait for a person.

    Calibrated on the merged refreshes: an ordinary week changed 1-4 layouts
    and removed nothing.
    """

    BASE = {
        ("Vorkath", "Ranged"): _variant({"1": 1}),
        ("Vorkath", "Melee"): _variant({"1": 2}),
        ("Zulrah", "Magic"): _variant({"1": 3}),
    }

    def test_an_ordinary_week_is_routine(self):
        new = dict(self.BASE)
        new[("Vorkath", "Ranged")] = _variant({"1": 9})
        new[("Nex", "Max")] = _variant({"1": 4})
        result = classify(self.BASE, new)
        self.assertTrue(result["routine"], result["reasons"])
        self.assertEqual(result["changed"], [("Vorkath", "Ranged")])
        self.assertEqual(result["added"], [("Nex", "Max")])

    def test_no_change_is_routine(self):
        self.assertTrue(classify(self.BASE, dict(self.BASE))["routine"])

    def test_a_vanished_activity_waits_for_review(self):
        # The failure this exists for: Zulrah fell out of the library silently.
        new = {k: v for k, v in self.BASE.items() if k[0] != "Zulrah"}
        result = classify(self.BASE, new)
        self.assertFalse(result["routine"])
        self.assertTrue(any("Zulrah" in r for r in result["reasons"]))

    def test_a_removed_variant_waits_for_review(self):
        new = {k: v for k, v in self.BASE.items() if k != ("Vorkath", "Melee")}
        result = classify(self.BASE, new)
        self.assertFalse(result["routine"])
        self.assertEqual(result["removed"], [("Vorkath", "Melee")])

    def test_lost_completeness_waits_for_review(self):
        new = dict(self.BASE)
        new[("Vorkath", "Melee")] = _variant({"1": 2}, "partial")
        result = classify(self.BASE, new)
        self.assertFalse(result["routine"])
        self.assertTrue(any("Vorkath / Melee" in r for r in result["reasons"]))

    def test_a_wide_change_waits_for_review(self):
        base = {("A", str(i)): _variant({"1": i}) for i in range(CHANGE_LIMIT + 1)}
        new = {k: _variant({"1": 1000 + i}) for i, k in enumerate(base)}
        self.assertFalse(classify(base, new)["routine"])
        within = dict(base)
        for k in list(base)[:CHANGE_LIMIT]:
            within[k] = _variant({"1": -1})
        self.assertTrue(classify(base, within)["routine"])

    def test_a_flood_of_new_layouts_waits_for_review(self):
        new = dict(self.BASE)
        for i in range(ADD_LIMIT + 1):
            new[("New", str(i))] = _variant({"1": i})
        self.assertFalse(classify(self.BASE, new)["routine"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
