"""
test_roster.py

Unit tests for roster.py -- no network calls. Run with:

    python test_roster.py
"""

import unittest

import roster
from data_models import Actor, Fight, ParsedFight

ACTORS = {
    1: Actor(id=1, name="Healbot", type="Player", subtype="Priest"),
    10: Actor(id=10, name="Tankybot", type="Player", subtype="Warrior"),
    100: Actor(id=100, name="Test Boss", type="NPC"),
    50: Actor(id=50, name="Wild Imp", type="Pet", owner_id=1),
    51: Actor(id=51, name="Orphan Pet", type="Pet", owner_id=999),  # owner not in actors
}

FIGHT = Fight(id=1, name="Test Boss", difficulty=5, kill=True,
              start_time=0, end_time=1000, encounter_id=1)


class TestGetPlayers(unittest.TestCase):
    def test_excludes_npcs_and_pets(self):
        players = roster.get_players(ACTORS)
        self.assertEqual(set(players.keys()), {1, 10})

    def test_get_player_roster_from_parsed_fight(self):
        parsed_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        players = roster.get_player_roster(parsed_fight)
        self.assertEqual(set(players.keys()), {1, 10})


class TestIsPlayer(unittest.TestCase):
    def test_true_for_player(self):
        self.assertTrue(roster.is_player(1, ACTORS))

    def test_false_for_npc_and_pet(self):
        self.assertFalse(roster.is_player(100, ACTORS))
        self.assertFalse(roster.is_player(50, ACTORS))

    def test_false_for_none_or_unknown_id(self):
        self.assertFalse(roster.is_player(None, ACTORS))
        self.assertFalse(roster.is_player(9999, ACTORS))


class TestResolveToPlayer(unittest.TestCase):
    def test_player_resolves_to_self(self):
        result = roster.resolve_to_player(1, ACTORS)
        self.assertEqual(result.name, "Healbot")

    def test_pet_resolves_to_owner(self):
        result = roster.resolve_to_player(50, ACTORS)
        self.assertEqual(result.name, "Healbot")

    def test_npc_resolves_to_none(self):
        self.assertIsNone(roster.resolve_to_player(100, ACTORS))

    def test_pet_with_unresolvable_owner_returns_none(self):
        self.assertIsNone(roster.resolve_to_player(51, ACTORS))

    def test_unknown_actor_id_returns_none(self):
        self.assertIsNone(roster.resolve_to_player(9999, ACTORS))

    def test_none_id_returns_none(self):
        self.assertIsNone(roster.resolve_to_player(None, ACTORS))


class TestResolveToPlayerIdName(unittest.TestCase):
    def test_pet_resolves_to_owner_id_and_name(self):
        result = roster.resolve_to_player_id_name(50, "Wild Imp", ACTORS)
        self.assertEqual(result, (1, "Healbot"))

    def test_unresolvable_falls_back_to_original(self):
        result = roster.resolve_to_player_id_name(100, "Test Boss", ACTORS)
        self.assertEqual(result, (100, "Test Boss"))

    def test_orphan_pet_falls_back_to_original(self):
        result = roster.resolve_to_player_id_name(51, "Orphan Pet", ACTORS)
        self.assertEqual(result, (51, "Orphan Pet"))


if __name__ == "__main__":
    unittest.main()
