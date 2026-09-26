"""
test_damage_done_analyzer.py

Unit tests for damage_done_analyzer.py using a small hand-built
ParsedFight -- no network calls. Run with:

    python test_damage_done_analyzer.py
"""

import unittest

import damage_done_analyzer
from data_models import Actor, Event, Fight, ParsedFight

FIGHT = Fight(
    id=1, name="Test Boss", difficulty=5, kill=True,
    start_time=0, end_time=10000, encounter_id=1,  # 10s fight
)

ACTORS = {
    10: Actor(id=10, name="Tankybot", type="Player", subtype="Warrior"),
    11: Actor(id=11, name="Huntard", type="Player", subtype="Hunter"),
    100: Actor(id=100, name="Test Boss", type="NPC"),
    101: Actor(id=101, name="Add", type="NPC"),
    # Huntard's pet -- its damage should be folded into Huntard.
    50: Actor(id=50, name="Ferocious Wolf", type="Pet", owner_id=11),
    # A pet whose owner isn't in the actor list -- damage should be dropped.
    51: Actor(id=51, name="Orphan Pet", type="Pet", owner_id=999),
}


def build_events():
    return [
        # Tankybot hits the boss twice with Devastate
        Event(timestamp=1000, event_type="damage", data_type="DamageDone",
              source_id=10, source_name="Tankybot", target_id=100, target_name="Test Boss",
              ability_name="Devastate", amount=3000),
        Event(timestamp=2000, event_type="damage", data_type="DamageDone",
              source_id=10, source_name="Tankybot", target_id=100, target_name="Test Boss",
              ability_name="Devastate", amount=3500),
        # Huntard hits the boss with Aimed Shot
        Event(timestamp=3000, event_type="damage", data_type="DamageDone",
              source_id=11, source_name="Huntard", target_id=100, target_name="Test Boss",
              ability_name="Aimed Shot", amount=9000),
        # Huntard's pet hits an add -- should be credited to Huntard
        Event(timestamp=4000, event_type="damage", data_type="DamageDone",
              source_id=50, source_name="Ferocious Wolf", target_id=101, target_name="Add",
              ability_name="Bite", amount=1200),
        # The boss hits a player (this is DamageTaken territory, not DamageDone,
        # but even if it leaked in here it must not create a "Test Boss" DPS entry)
        Event(timestamp=5000, event_type="damage", data_type="DamageDone",
              source_id=100, source_name="Test Boss", target_id=10, target_name="Tankybot",
              ability_name="Cleave", amount=5000),
        # Orphan pet's damage -- unresolvable owner, should be dropped entirely
        Event(timestamp=6000, event_type="damage", data_type="DamageDone",
              source_id=51, source_name="Orphan Pet", target_id=100, target_name="Test Boss",
              ability_name="Claw", amount=777),
        # A non-damage-done event that should be ignored entirely
        Event(timestamp=7000, event_type="heal", data_type="Healing",
              source_id=10, target_id=10, ability_name="Bandage", amount=999),
    ]


class TestAnalyzeDamageDone(unittest.TestCase):
    def setUp(self):
        self.parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        self.summaries = damage_done_analyzer.analyze_damage_done(self.parsed_fight)

    def test_only_real_players_appear(self):
        names = {s.player_name for s in self.summaries}
        self.assertEqual(names, {"Tankybot", "Huntard"})

    def test_pet_damage_folded_into_owner(self):
        huntard = next(s for s in self.summaries if s.player_name == "Huntard")
        # 9000 (Aimed Shot) + 1200 (pet's Bite) = 10200
        self.assertEqual(huntard.total_damage_done, 10200)
        self.assertEqual(huntard.damage_by_ability["Bite"], 1200)
        self.assertEqual(huntard.damage_by_target["Add"], 1200)

    def test_boss_source_excluded_entirely(self):
        # Test Boss dealing damage to Tankybot must not create a "Test Boss" entry.
        names = {s.player_name for s in self.summaries}
        self.assertNotIn("Test Boss", names)

    def test_orphan_pet_damage_dropped(self):
        # Orphan Pet's 777 damage has no resolvable owner -- must not
        # appear anywhere, and must not leak in under "Orphan Pet" either.
        names = {s.player_name for s in self.summaries}
        self.assertNotIn("Orphan Pet", names)
        total_across_all = sum(s.total_damage_done for s in self.summaries)
        self.assertNotIn(777, [s.total_damage_done for s in self.summaries])

    def test_sorted_by_total_damage_descending(self):
        self.assertEqual(self.summaries[0].player_name, "Huntard")
        self.assertEqual(self.summaries[0].total_damage_done, 10200)
        self.assertEqual(self.summaries[1].player_name, "Tankybot")
        self.assertEqual(self.summaries[1].total_damage_done, 6500)

    def test_dps_calculation(self):
        tankybot = next(s for s in self.summaries if s.player_name == "Tankybot")
        # 6500 over a 10s fight = 650 DPS
        self.assertEqual(tankybot.dps(FIGHT.duration_ms), 650.0)

    def test_damage_by_ability_breakdown(self):
        tankybot = next(s for s in self.summaries if s.player_name == "Tankybot")
        self.assertEqual(tankybot.damage_by_ability["Devastate"], 6500)

    def test_ignores_non_damage_done_events(self):
        # The heal event must not affect any player's damage total.
        tankybot = next(s for s in self.summaries if s.player_name == "Tankybot")
        self.assertEqual(tankybot.total_damage_done, 6500)  # not 7499

    def test_empty_fight_returns_empty_list(self):
        empty_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        self.assertEqual(damage_done_analyzer.analyze_damage_done(empty_fight), [])


class TestSummarizeDamageDone(unittest.TestCase):
    def test_summary_mentions_player_and_dps(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        summaries = damage_done_analyzer.analyze_damage_done(parsed_fight)
        text = damage_done_analyzer.summarize_damage_done(parsed_fight, summaries)
        self.assertIn("Huntard", text)
        self.assertIn("1020", text)  # 10200 total damage done substring

    def test_summary_with_no_damage(self):
        parsed_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        text = damage_done_analyzer.summarize_damage_done(parsed_fight, [])
        self.assertIn("no damage-done", text)


if __name__ == "__main__":
    unittest.main()
