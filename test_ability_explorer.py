"""
test_ability_explorer.py

Unit tests for ability_explorer.py using a small hand-built ParsedFight
-- no network calls. Run with:

    python test_ability_explorer.py
"""

import unittest

import ability_explorer
from data_models import Actor, Event, Fight, ParsedFight

FIGHT = Fight(
    id=1, name="Test Boss", difficulty=5, kill=False,
    start_time=0, end_time=100_000, encounter_id=1,
    friendly_player_ids=[1, 2],
)

ACTORS = {
    1: Actor(id=1, name="Alpha", type="Player", subtype="Priest"),
    2: Actor(id=2, name="Bravo", type="Player", subtype="Warrior"),
    100: Actor(id=100, name="Test Boss", type="NPC"),
    # A pet -- its "casts" should NOT show up as an NPC-sourced boss ability.
    50: Actor(id=50, name="Wild Imp", type="Pet", owner_id=1),
}


def build_events():
    return [
        # Fire Patch hits Alpha twice, Bravo once -- 3 total, 2 distinct targets
        Event(timestamp=10_000, event_type="damage", data_type="DamageTaken",
              source_id=100, target_id=1, ability_id=555, ability_name="Fire Patch", amount=1000),
        Event(timestamp=20_000, event_type="damage", data_type="DamageTaken",
              source_id=100, target_id=1, ability_id=555, ability_name="Fire Patch", amount=1500),
        Event(timestamp=25_000, event_type="damage", data_type="DamageTaken",
              source_id=100, target_id=2, ability_id=555, ability_name="Fire Patch", amount=2000),
        # A boss cast (telegraph) with no damage attached
        Event(timestamp=5_000, event_type="cast", data_type="Casts",
              source_id=100, ability_id=777, ability_name="Ominous Roar"),
        # A debuff application that later kills someone
        Event(timestamp=50_000, event_type="applydebuff", data_type="Debuffs",
              source_id=100, target_id=2, ability_id=888, ability_name="Soul Fracture"),
        Event(timestamp=90_000, event_type="death", data_type="Deaths",
              source_id=100, target_id=2, ability_name="Soul Fracture"),
        # A pet "cast" -- should be excluded, pets aren't boss abilities
        Event(timestamp=15_000, event_type="cast", data_type="Casts",
              source_id=50, ability_name="Fel Firebolt"),
        # A player cast -- should also be excluded
        Event(timestamp=16_000, event_type="cast", data_type="Casts",
              source_id=1, ability_name="Flash Heal"),
        # An untracked data type -- should be excluded
        Event(timestamp=17_000, event_type="heal", data_type="Healing",
              source_id=1, target_id=2, ability_name="Flash Heal", amount=500),
    ]


class TestAnalyzeBossAbilities(unittest.TestCase):
    def setUp(self):
        self.parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        self.summaries = ability_explorer.analyze_boss_abilities(self.parsed_fight)

    def test_only_npc_sourced_abilities_included(self):
        names = {s.ability_name for s in self.summaries}
        self.assertEqual(names, {"Fire Patch", "Ominous Roar", "Soul Fracture"})

    def test_pet_and_player_casts_excluded(self):
        names = {s.ability_name for s in self.summaries}
        self.assertNotIn("Fel Firebolt", names)
        self.assertNotIn("Flash Heal", names)

    def test_occurrence_count_and_distinct_targets(self):
        fire_patch = next(s for s in self.summaries if s.ability_name == "Fire Patch")
        self.assertEqual(fire_patch.total_occurrences, 3)
        self.assertEqual(fire_patch.unique_targets_hit, 2)

    def test_damage_stats(self):
        fire_patch = next(s for s in self.summaries if s.ability_name == "Fire Patch")
        self.assertEqual(fire_patch.total_damage, 4500)
        self.assertEqual(fire_patch.max_hit, 2000)
        self.assertAlmostEqual(fire_patch.avg_hit, 1500.0)

    def test_cast_only_ability_has_zero_damage_stats(self):
        roar = next(s for s in self.summaries if s.ability_name == "Ominous Roar")
        self.assertEqual(roar.total_damage, 0)
        self.assertEqual(roar.avg_hit, 0.0)
        self.assertEqual(roar.unique_targets_hit, 0)  # no target_id on a self-cast

    def test_first_and_last_seen(self):
        fire_patch = next(s for s in self.summaries if s.ability_name == "Fire Patch")
        self.assertEqual(fire_patch.first_seen_ms, 10_000)
        self.assertEqual(fire_patch.last_seen_ms, 25_000)

    def test_first_seen_pct(self):
        fire_patch = next(s for s in self.summaries if s.ability_name == "Fire Patch")
        # 10_000 / 100_000 = 10%
        self.assertAlmostEqual(fire_patch.first_seen_pct(FIGHT.duration_ms), 10.0)

    def test_caused_death_flag(self):
        soul_fracture = next(s for s in self.summaries if s.ability_name == "Soul Fracture")
        self.assertTrue(soul_fracture.caused_death)

        fire_patch = next(s for s in self.summaries if s.ability_name == "Fire Patch")
        self.assertFalse(fire_patch.caused_death)

    def test_sorted_by_total_occurrences_descending(self):
        self.assertEqual(self.summaries[0].ability_name, "Fire Patch")  # 3 occurrences

    def test_empty_fight_returns_empty_list(self):
        empty_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        self.assertEqual(ability_explorer.analyze_boss_abilities(empty_fight), [])


class TestFormatAbilityTable(unittest.TestCase):
    def test_table_mentions_ability_and_counts(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        summaries = ability_explorer.analyze_boss_abilities(parsed_fight)
        text = ability_explorer.format_ability_table(parsed_fight, summaries)
        self.assertIn("Fire Patch", text)
        self.assertIn("Soul Fracture", text)
        self.assertIn("YES", text)  # Soul Fracture's death flag

    def test_table_with_no_abilities(self):
        empty_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        text = ability_explorer.format_ability_table(empty_fight, [])
        self.assertIn("no NPC-sourced abilities", text)


if __name__ == "__main__":
    unittest.main()
