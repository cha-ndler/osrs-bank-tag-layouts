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
    parse_import_string,
    split_layout,
    tag_name,
)
from extract import clean_item, extract_page, parse_template, split_args  # noqa: E402


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

    def test_gear_only_tab_is_kept(self):
        # Theatre of Blood tabs a melee loadout with no inventory block.
        text = "<tabber>\nMelee=\n{{Equipment|head = Torva full helm}}\n</tabber>"
        setups, _ = extract_page("X/Strategies", text)
        self.assertEqual(len(setups), 1)
        self.assertEqual(setups[0].variant, "Melee")
        self.assertEqual(setups[0].inventory, {})


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
            code = v["importString"]
            self.assertTrue(code.startswith("banktags,1,"))
            self.assertIn(",layout,", code)
            self.assertNotIn(",,", code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
