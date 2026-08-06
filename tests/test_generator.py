"""Offline tests for the parsing and encoding logic.

Nothing here touches the network: the wiki-facing stages are covered by
`validate.py`, which runs over real generated output. These guard the pure
functions where a silent regression would corrupt every layout at once.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "generator"))

from encode import (  # noqa: E402
    LOADOUT_MAP,
    completeness,
    parse_import_string,
    split_layout,
    tag_name,
    zigzag_index,
    zigzag_layout,
)
from extract import (  # noqa: E402
    clean_item,
    extract_page,
    parse_template,
    plink_item,
    recommended_equipment,
    split_args,
)


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
            for code in (v["importString"], v["importStringZigzag"]):
                self.assertTrue(code.startswith("banktags,1,"))
                self.assertIn(",layout,", code)
                self.assertNotIn(",,", code)

    def test_both_styles_carry_the_same_items(self):
        for v in self.record["variants"]:
            self.assertEqual(
                sorted(v["layout"].values()), sorted(v["layoutZigzag"].values())
            )

    def test_kreearra_keeps_its_two_handed_weapon(self):
        path = REPO / "data" / "kreearra.json"
        if not path.exists():
            self.skipTest("Kree'arra data not generated")
        record = json.loads(path.read_text(encoding="utf-8"))
        tbow = next(v for v in record["variants"] if "Tbow" in v["variant"])
        # 20997 = Twisted bow, supplied via the `2h1` argument.
        self.assertEqual(tbow["equipment"].get("weapon"), 20997)


if __name__ == "__main__":
    unittest.main(verbosity=2)
