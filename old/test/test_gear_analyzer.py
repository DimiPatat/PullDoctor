"""
test_gear_analyzer.py

Unit tests for gear_analyzer.py using hand-built CombatantInfo fixture
data -- no network calls. Run with:

    python test_gear_analyzer.py
"""

import unittest

import gear_analyzer
from data_models import Actor, CombatantInfoSnapshot, Fight, GearItem, ParsedFight

FIGHT = Fight(
    id=1, name="Test Boss", difficulty=5, kill=True,
    start_time=0, end_time=1000, encounter_id=1,
    friendly_player_ids=[1],
)

ACTORS = {
    1: Actor(id=1, name="Alpha", type="Player", subtype="Priest"),
}


def build_gear():
    return [
        # Head: enchantable, HAS an enchant, 1 gem
        GearItem(slot=0, item_id=1001, quality=4, item_level=720,
                 permanent_enchant_id=555, gem_ids=[9001]),
        # Chest: enchantable, NO enchant, no gems
        GearItem(slot=4, item_id=1002, quality=4, item_level=723),
        # Ring 1: enchantable, HAS an enchant, 2 gems
        GearItem(slot=10, item_id=1003, quality=3, item_level=710,
                 permanent_enchant_id=556, gem_ids=[9002, 9003]),
        # Off-hand: NOT counted toward missing enchants even without one
        GearItem(slot=16, item_id=1004, quality=4, item_level=720),
        # Waist: not an enchantable slot at all, shouldn't affect missing list
        GearItem(slot=5, item_id=1005, quality=2, item_level=700),
    ]


class TestAnalyzeGear(unittest.TestCase):
    def setUp(self):
        combatant_info = {1: CombatantInfoSnapshot(player_id=1, gear=build_gear())}
        self.parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=[], combatant_info=combatant_info,
        )
        self.reports = gear_analyzer.analyze_gear(self.parsed_fight)

    def test_one_report_per_player(self):
        self.assertEqual(len(self.reports), 1)
        self.assertEqual(self.reports[0].player_name, "Alpha")

    def test_missing_enchant_slots_excludes_offhand_and_nonenchantable(self):
        report = self.reports[0]
        # Only Chest is enchantable AND missing an enchant.
        self.assertEqual(report.missing_enchant_slots, ["Chest"])

    def test_total_gems_summed_across_pieces(self):
        report = self.reports[0]
        self.assertEqual(report.total_gems, 3)  # 1 (Head) + 0 (Chest) + 2 (Ring 1)

    def test_average_item_level(self):
        report = self.reports[0]
        # (720 + 723 + 710 + 720 + 700) / 5 = 714.6
        self.assertAlmostEqual(report.average_item_level, 714.6, places=1)

    def test_lowest_quality(self):
        report = self.reports[0]
        self.assertEqual(report.lowest_quality, 2)  # the Waist piece

    def test_player_with_no_combatant_info_gets_explicit_no_data_entry(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=[], combatant_info={},
        )
        reports = gear_analyzer.analyze_gear(parsed_fight)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].player_name, "Alpha")
        self.assertFalse(reports[0].has_data)
        self.assertEqual(reports[0].pieces, [])


class TestQualityName(unittest.TestCase):
    def test_known_qualities(self):
        self.assertEqual(gear_analyzer.quality_name(4), "Epic")
        self.assertEqual(gear_analyzer.quality_name(0), "Poor")

    def test_unknown_quality_falls_back_gracefully(self):
        self.assertIn("Unknown", gear_analyzer.quality_name(99))


class TestSummarizeGear(unittest.TestCase):
    def test_summary_mentions_player_and_missing_enchant(self):
        combatant_info = {1: CombatantInfoSnapshot(player_id=1, gear=build_gear())}
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=[], combatant_info=combatant_info,
        )
        reports = gear_analyzer.analyze_gear(parsed_fight)
        text = gear_analyzer.summarize_gear(reports)
        self.assertIn("Alpha", text)
        self.assertIn("Chest", text)

    def test_summary_with_no_data(self):
        text = gear_analyzer.summarize_gear([])
        self.assertIn("No gear data", text)

    def test_summary_shows_explicit_no_data_for_missing_snapshot(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=[], combatant_info={},
        )
        reports = gear_analyzer.analyze_gear(parsed_fight)
        text = gear_analyzer.summarize_gear(reports)
        self.assertIn("Alpha", text)
        self.assertIn("no gear data", text)


if __name__ == "__main__":
    unittest.main()
