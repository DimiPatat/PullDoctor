"""
test_avoidable_damage_analyzer.py

Unit tests for avoidable_damage_analyzer.py using a small hand-built
encounter config and ParsedFight -- no network calls. Run with:

    python test_avoidable_damage_analyzer.py
"""

import unittest

import avoidable_damage_analyzer as ada
from avoidable_damage_data import AvoidableMechanic, EncounterConfig
from data_models import Actor, Event, Fight, ParsedFight

FIGHT = Fight(
    id=1, name="Test Boss", difficulty=5, kill=True,
    start_time=0, end_time=60_000, encounter_id=999,
    friendly_player_ids=[1, 2],
)

ACTORS = {
    1: Actor(id=1, name="Alpha", type="Player", subtype="Priest"),
    2: Actor(id=2, name="Bravo", type="Player", subtype="Warrior"),
}

CONFIG = EncounterConfig(
    encounter_id=999,
    encounter_name="Test Boss",
    mechanics=[
        AvoidableMechanic(
            ability_name="Fire Patch", category="ground_effect", track_via=("damage",)
        ),
        AvoidableMechanic(
            ability_name="Creeping Rot", category="stacking_debuff", track_via=("debuff",)
        ),
    ],
)
CONFIGS = {999: CONFIG}


def build_events():
    return [
        # Alpha stands in Fire Patch twice (2 damage ticks)
        Event(timestamp=1000, event_type="damage", data_type="DamageTaken",
              source_id=100, target_id=1, ability_name="Fire Patch", amount=500),
        Event(timestamp=2000, event_type="damage", data_type="DamageTaken",
              source_id=100, target_id=1, ability_name="Fire Patch", amount=500),
        # Bravo gets Creeping Rot applied once
        Event(timestamp=3000, event_type="applydebuff", data_type="Debuffs",
              source_id=100, target_id=2, ability_name="Creeping Rot"),
        # A refresh of the same debuff -- should NOT count as a new application
        Event(timestamp=4000, event_type="refreshdebuff", data_type="Debuffs",
              source_id=100, target_id=2, ability_name="Creeping Rot"),
        # Damage from an untracked ability -- should be ignored
        Event(timestamp=5000, event_type="damage", data_type="DamageTaken",
              source_id=100, target_id=1, ability_name="Regular Melee", amount=2000),
    ]


class TestAnalyzeAvoidableDamage(unittest.TestCase):
    def setUp(self):
        self.parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        self.reports = ada.analyze_avoidable_damage(self.parsed_fight, CONFIGS)

    def test_one_report_per_player_in_roster(self):
        self.assertEqual(len(self.reports), 2)
        names = {r.player_name for r in self.reports}
        self.assertEqual(names, {"Alpha", "Bravo"})

    def test_damage_tracked_mechanic_counts_each_hit(self):
        alpha = next(r for r in self.reports if r.player_name == "Alpha")
        self.assertEqual(alpha.hits_by_mechanic["Fire Patch"], 2)
        self.assertEqual(alpha.damage_by_mechanic["Fire Patch"], 1000)
        self.assertEqual(alpha.total_avoidable_hits, 2)

    def test_debuff_tracked_mechanic_counts_applications_not_refreshes(self):
        bravo = next(r for r in self.reports if r.player_name == "Bravo")
        self.assertEqual(bravo.hits_by_mechanic["Creeping Rot"], 1)  # not 2
        self.assertEqual(bravo.total_avoidable_hits, 1)

    def test_untracked_ability_ignored(self):
        alpha = next(r for r in self.reports if r.player_name == "Alpha")
        self.assertNotIn("Regular Melee", alpha.hits_by_mechanic)

    def test_sorted_by_total_hits_descending(self):
        self.assertEqual(self.reports[0].player_name, "Alpha")  # 2 hits > 1

    def test_no_config_for_encounter_returns_empty_list(self):
        other_fight = Fight(
            id=2, name="Unconfigured Boss", difficulty=5, kill=True,
            start_time=0, end_time=1000, encounter_id=111, friendly_player_ids=[1],
        )
        parsed_fight = ParsedFight(fight=other_fight, actors=ACTORS, abilities={}, events=[])
        self.assertEqual(ada.analyze_avoidable_damage(parsed_fight, CONFIGS), [])

    def test_no_hits_still_returns_player_with_zero(self):
        clean_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        reports = ada.analyze_avoidable_damage(clean_fight, CONFIGS)
        self.assertEqual(len(reports), 2)
        for r in reports:
            self.assertEqual(r.total_avoidable_hits, 0)


class TestSummarizeAvoidableDamage(unittest.TestCase):
    def test_summary_mentions_player_and_mechanic(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        reports = ada.analyze_avoidable_damage(parsed_fight, CONFIGS)
        text = ada.summarize_avoidable_damage(parsed_fight, reports, CONFIG)
        self.assertIn("Alpha", text)
        self.assertIn("Fire Patch", text)

    def test_summary_with_no_config(self):
        parsed_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        text = ada.summarize_avoidable_damage(parsed_fight, [], None)
        self.assertIn("no avoidable-damage config", text)

    def test_summary_with_clean_pull(self):
        clean_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        reports = ada.analyze_avoidable_damage(clean_fight, CONFIGS)
        text = ada.summarize_avoidable_damage(clean_fight, reports, CONFIG)
        self.assertIn("Clean pull", text)


if __name__ == "__main__":
    unittest.main()
