"""
test_damage_analyzer.py

Unit tests for damage_analyzer.py using a small hand-built ParsedFight --
no network calls. Run with:

    python test_damage_analyzer.py
"""

import unittest

import damage_analyzer
from data_models import Actor, Event, Fight, ParsedFight

FIGHT = Fight(
    id=1, name="Test Boss", difficulty=5, kill=True,
    start_time=0, end_time=10000, encounter_id=1,  # 10s fight
)

ACTORS = {
    10: Actor(id=10, name="Tankybot", type="Player", subtype="Warrior"),
    11: Actor(id=11, name="DPSbot", type="Player", subtype="Rogue"),
    100: Actor(id=100, name="Test Boss", type="NPC"),
    101: Actor(id=101, name="Adds", type="NPC"),
    50: Actor(id=50, name="Water Elemental", type="Pet", owner_id=11),
}


def build_events():
    return [
        # Boss hits Tankybot with Cleave twice
        Event(timestamp=1000, event_type="damage", data_type="DamageTaken",
              source_id=100, source_name="Test Boss", target_id=10, target_name="Tankybot",
              ability_name="Cleave", amount=3000),
        Event(timestamp=2000, event_type="damage", data_type="DamageTaken",
              source_id=100, source_name="Test Boss", target_id=10, target_name="Tankybot",
              ability_name="Cleave", amount=4000),
        # Boss hits DPSbot with Fireball
        Event(timestamp=3000, event_type="damage", data_type="DamageTaken",
              source_id=100, source_name="Test Boss", target_id=11, target_name="DPSbot",
              ability_name="Fireball", amount=8000),
        # Adds hit Tankybot with Bite
        Event(timestamp=4000, event_type="damage", data_type="DamageTaken",
              source_id=101, source_name="Adds", target_id=10, target_name="Tankybot",
              ability_name="Bite", amount=1500),
        # A non-damage-taken event that should be ignored entirely
        Event(timestamp=5000, event_type="heal", data_type="Healing",
              source_id=1, target_id=10, ability_name="Flash Heal", amount=999),
        # Damage landing on a pet -- should be excluded, pets aren't raid damage-taken
        Event(timestamp=6000, event_type="damage", data_type="DamageTaken",
              source_id=100, source_name="Test Boss", target_id=50, target_name="Water Elemental",
              ability_name="Cleave", amount=12345),
    ]


class TestAnalyzeDamageTaken(unittest.TestCase):
    def setUp(self):
        self.parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        self.summaries = damage_analyzer.analyze_damage_taken(self.parsed_fight)

    def test_one_summary_per_target(self):
        self.assertEqual(len(self.summaries), 2)
        names = {s.target_name for s in self.summaries}
        self.assertEqual(names, {"Tankybot", "DPSbot"})

    def test_sorted_by_total_damage_descending(self):
        # Tankybot: 3000 + 4000 + 1500 = 8500
        # DPSbot: 8000
        self.assertEqual(self.summaries[0].target_name, "Tankybot")
        self.assertEqual(self.summaries[0].total_damage_taken, 8500)
        self.assertEqual(self.summaries[1].target_name, "DPSbot")
        self.assertEqual(self.summaries[1].total_damage_taken, 8000)

    def test_damage_by_ability_breakdown(self):
        tankybot = next(s for s in self.summaries if s.target_name == "Tankybot")
        self.assertEqual(tankybot.damage_by_ability["Cleave"], 7000)
        self.assertEqual(tankybot.damage_by_ability["Bite"], 1500)

    def test_damage_by_source_breakdown(self):
        tankybot = next(s for s in self.summaries if s.target_name == "Tankybot")
        self.assertEqual(tankybot.damage_by_source["Test Boss"], 7000)
        self.assertEqual(tankybot.damage_by_source["Adds"], 1500)

    def test_dtps_calculation(self):
        tankybot = next(s for s in self.summaries if s.target_name == "Tankybot")
        # 8500 over a 10s fight = 850 DTPS
        self.assertEqual(tankybot.dtps(FIGHT.duration_ms), 850.0)

    def test_ignores_non_damage_taken_events(self):
        target_ids = {s.target_id for s in self.summaries}
        self.assertNotIn(1, target_ids)  # the heal event's target shouldn't leak in

    def test_pet_damage_taken_is_excluded(self):
        # Water Elemental (a pet) took 12345 damage -- must not appear
        # as a raid member taking damage.
        target_names = {s.target_name for s in self.summaries}
        self.assertNotIn("Water Elemental", target_names)

    def test_empty_fight_returns_empty_list(self):
        empty_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        self.assertEqual(damage_analyzer.analyze_damage_taken(empty_fight), [])


class TestAnalyzeDamageSources(unittest.TestCase):
    def setUp(self):
        self.parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        self.summaries = damage_analyzer.analyze_damage_sources(self.parsed_fight)

    def test_one_summary_per_source(self):
        names = {s.source_name for s in self.summaries}
        self.assertEqual(names, {"Test Boss", "Adds"})

    def test_sorted_by_total_dealt_descending(self):
        # Test Boss: 3000 + 4000 + 8000 = 15000
        # Adds: 1500
        self.assertEqual(self.summaries[0].source_name, "Test Boss")
        self.assertEqual(self.summaries[0].total_damage_dealt, 15000)
        self.assertEqual(self.summaries[1].source_name, "Adds")
        self.assertEqual(self.summaries[1].total_damage_dealt, 1500)

    def test_damage_by_target_breakdown(self):
        boss = next(s for s in self.summaries if s.source_name == "Test Boss")
        self.assertEqual(boss.damage_by_target["Tankybot"], 7000)
        self.assertEqual(boss.damage_by_target["DPSbot"], 8000)


class TestGetBiggestHits(unittest.TestCase):
    def test_returns_top_n_sorted_descending(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        hits = damage_analyzer.get_biggest_hits(parsed_fight, top_n=2)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].amount, 8000)
        self.assertEqual(hits[0].ability_name, "Fireball")
        self.assertEqual(hits[1].amount, 4000)

    def test_excludes_non_damage_events(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        hits = damage_analyzer.get_biggest_hits(parsed_fight, top_n=10)
        self.assertEqual(len(hits), 4)  # not 5 -- the heal event is excluded


class TestSummarizeDamageTaken(unittest.TestCase):
    def test_summary_mentions_target_and_dtps(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        summaries = damage_analyzer.analyze_damage_taken(parsed_fight)
        text = damage_analyzer.summarize_damage_taken(parsed_fight, summaries)
        self.assertIn("Tankybot", text)
        self.assertIn("850", text)

    def test_summary_with_no_damage(self):
        parsed_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        text = damage_analyzer.summarize_damage_taken(parsed_fight, [])
        self.assertIn("no damage-taken", text)


if __name__ == "__main__":
    unittest.main()
