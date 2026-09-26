"""
test_death_analyzer.py

Unit tests for death_analyzer.py using a small hand-built ParsedFight --
no network calls. Run with:

    python test_death_analyzer.py
"""

import unittest

import death_analyzer
from data_models import Actor, Event, Fight, ParsedFight

FIGHT = Fight(
    id=1, name="Test Boss", difficulty=5, kill=False,
    start_time=0, end_time=20000, encounter_id=1,
)

ACTORS = {
    1: Actor(id=1, name="Healbot", type="Player", subtype="Priest"),
    2: Actor(id=2, name="Tankybot", type="Player", subtype="Warrior"),
    100: Actor(id=100, name="Test Boss", type="NPC"),
    50: Actor(id=50, name="Water Elemental", type="Pet", owner_id=1),
}


def build_events():
    return [
        # Damage ticking on Tankybot in the seconds before death
        Event(timestamp=6000, event_type="damage", data_type="DamageTaken",
              source_id=100, target_id=2, ability_name="Cleave", amount=2000),
        Event(timestamp=7000, event_type="damage", data_type="DamageTaken",
              source_id=100, target_id=2, ability_name="Cleave", amount=2500),
        # A heal landing on Tankybot in that window, partially overhealed
        Event(timestamp=6500, event_type="heal", data_type="Healing",
              source_id=1, target_id=2, ability_name="Flash Heal",
              amount=3000, overheal=1000),
        # Damage far outside the window -- should NOT be included
        Event(timestamp=0, event_type="damage", data_type="DamageTaken",
              source_id=100, target_id=2, ability_name="Cleave", amount=9999),
        # The death itself
        Event(timestamp=8000, event_type="death", data_type="Deaths",
              source_id=100, target_id=2, target_name="Tankybot",
              ability_name="Massive Cleave"),
        # Unrelated event on a different target -- should be excluded
        Event(timestamp=7500, event_type="damage", data_type="DamageTaken",
              source_id=100, target_id=1, ability_name="Cleave", amount=500),
        # A pet death -- should be excluded, pets aren't raid deaths
        Event(timestamp=9000, event_type="death", data_type="Deaths",
              source_id=100, target_id=50, target_name="Water Elemental",
              ability_name="Cleave"),
    ]


class TestAnalyzeDeaths(unittest.TestCase):
    def setUp(self):
        self.parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )

    def test_finds_the_death(self):
        reports = death_analyzer.analyze_deaths(self.parsed_fight, context_window_ms=5000)
        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertEqual(report.victim_name, "Tankybot")
        self.assertEqual(report.killing_ability_name, "Massive Cleave")
        self.assertEqual(report.time_into_fight_ms, 8000)

    def test_pet_deaths_are_excluded(self):
        # Only Tankybot's death should be reported -- Water Elemental's
        # death (a pet) must not show up as a raid death.
        reports = death_analyzer.analyze_deaths(self.parsed_fight, context_window_ms=5000)
        victim_names = {r.victim_name for r in reports}
        self.assertNotIn("Water Elemental", victim_names)

    def test_damage_window_excludes_out_of_range_and_other_targets(self):
        reports = death_analyzer.analyze_deaths(self.parsed_fight, context_window_ms=5000)
        report = reports[0]
        # Window is [3000, 8000]. Only the 6000 and 7000 damage events qualify.
        amounts = sorted(d.amount for d in report.damage_taken_before)
        self.assertEqual(amounts, [2000, 2500])
        self.assertEqual(report.total_damage_taken_in_window, 4500)

    def test_healing_window_and_effective_healing_math(self):
        reports = death_analyzer.analyze_deaths(self.parsed_fight, context_window_ms=5000)
        report = reports[0]
        self.assertEqual(len(report.healing_received_before), 1)
        # 3000 amount - 1000 overheal = 2000 effective
        self.assertEqual(report.total_effective_healing_in_window, 2000)

    def test_narrower_window_excludes_earlier_damage(self):
        reports = death_analyzer.analyze_deaths(self.parsed_fight, context_window_ms=1500)
        report = reports[0]
        # Window is [6500, 8000] -- only the 7000 damage tick qualifies now.
        self.assertEqual(report.total_damage_taken_in_window, 2500)

    def test_no_deaths_returns_empty_list(self):
        parsed_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        self.assertEqual(death_analyzer.analyze_deaths(parsed_fight), [])


class TestSummarizeWipe(unittest.TestCase):
    def test_summary_mentions_victim_and_ability(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        reports = death_analyzer.analyze_deaths(parsed_fight)
        summary = death_analyzer.summarize_wipe(parsed_fight, reports)
        self.assertIn("Tankybot", summary)
        self.assertIn("Massive Cleave", summary)

    def test_summary_with_no_deaths(self):
        parsed_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        summary = death_analyzer.summarize_wipe(parsed_fight, [])
        self.assertIn("no deaths", summary)


if __name__ == "__main__":
    unittest.main()
