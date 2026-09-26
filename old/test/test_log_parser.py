"""
test_log_parser.py

Unit tests for log_parser.py using small hand-built fake WCL data --
no network calls, no live report needed. Run with:

    python -m unittest test_log_parser.py

or just:

    python test_log_parser.py
"""

import unittest

import log_parser
from data_models import Fight, Actor, Ability, Event


# ---------------------------------------------------------------------
# Fixtures: minimal fake WCL data, shaped like the real API responses
# ---------------------------------------------------------------------

RAW_FIGHT = {
    "id": 1,
    "name": "Test Boss",
    "difficulty": 5,
    "kill": False,
    "startTime": 1000,
    "endTime": 5000,
    "encounterID": 1234,
}

RAW_MASTER_DATA = {
    "actors": [
        {"id": 1, "name": "Healbot", "type": "Player", "subType": "Priest", "server": "Area-52"},
        {"id": 2, "name": "Tankybot", "type": "Player", "subType": "Warrior", "server": "Area-52"},
        {"id": 100, "name": "Test Boss", "type": "NPC", "subType": None, "server": None},
    ],
    "abilities": [
        {"gameID": 555, "name": "Flash Heal", "type": "Magic"},
        {"gameID": 777, "name": "Shield Slam", "type": "Physical"},
    ],
}

RAW_EVENTS_BY_TYPE = {
    "Healing": [
        {
            "timestamp": 2000,
            "type": "heal",
            "sourceID": 1,
            "targetID": 2,
            "abilityGameID": 555,
            "amount": 4500,
            "overheal": 500,
        },
    ],
    "DamageTaken": [
        {
            "timestamp": 1500,
            "type": "damage",
            "sourceID": 100,
            "targetID": 2,
            "abilityGameID": 777,
            "amount": 3000,
        },
    ],
}

RAW_COMBATANT_INFO_EVENTS = [
    {
        "timestamp": 900,
        "type": "combatantinfo",
        "sourceID": 1,
        "gear": [
            {
                "id": 240157, "slot": 6, "quality": 4, "itemLevel": 720,
                "permanentEnchant": 7452,
                "gems": [{"id": 240880, "itemLevel": 720}],
            },
            {"id": 243947, "slot": 4, "quality": 4, "itemLevel": 723},  # no enchant, no gems
        ],
        "auras": [
            {"source": 1, "ability": 555, "stacks": 1},  # resolvable via RAW_MASTER_DATA abilities
            {"source": 1, "ability": 999999, "stacks": 1},  # unresolvable -- not in abilities list
        ],
    },
]


class TestParseFight(unittest.TestCase):
    def test_parses_basic_fields(self):
        fight = log_parser.parse_fight(RAW_FIGHT)
        self.assertEqual(fight.id, 1)
        self.assertEqual(fight.name, "Test Boss")
        self.assertFalse(fight.kill)
        self.assertEqual(fight.start_time, 1000)
        self.assertEqual(fight.end_time, 5000)

    def test_duration_ms_property(self):
        fight = log_parser.parse_fight(RAW_FIGHT)
        self.assertEqual(fight.duration_ms, 4000)


class TestParseActorsAndAbilities(unittest.TestCase):
    def test_actors_keyed_by_id(self):
        actors = log_parser.parse_actors(RAW_MASTER_DATA)
        self.assertEqual(len(actors), 3)
        self.assertEqual(actors[1].name, "Healbot")
        self.assertEqual(actors[1].subtype, "Priest")
        self.assertEqual(actors[100].type, "NPC")

    def test_abilities_keyed_by_game_id(self):
        abilities = log_parser.parse_abilities(RAW_MASTER_DATA)
        self.assertEqual(len(abilities), 2)
        self.assertEqual(abilities[555].name, "Flash Heal")


class TestParseEvents(unittest.TestCase):
    def setUp(self):
        self.actors = log_parser.parse_actors(RAW_MASTER_DATA)
        self.abilities = log_parser.parse_abilities(RAW_MASTER_DATA)

    def test_resolves_names_correctly(self):
        events = log_parser.parse_events(RAW_EVENTS_BY_TYPE, self.actors, self.abilities)
        heal_event = next(e for e in events if e.event_type == "heal")
        self.assertEqual(heal_event.source_name, "Healbot")
        self.assertEqual(heal_event.target_name, "Tankybot")
        self.assertEqual(heal_event.ability_name, "Flash Heal")
        self.assertEqual(heal_event.amount, 4500)
        self.assertEqual(heal_event.overheal, 500)

    def test_sorts_chronologically_across_types(self):
        # DamageTaken event (ts=1500) should come before Healing event (ts=2000)
        # even though Healing appears first in the input dict.
        events = log_parser.parse_events(RAW_EVENTS_BY_TYPE, self.actors, self.abilities)
        self.assertEqual([e.timestamp for e in events], [1500, 2000])

    def test_unknown_actor_or_ability_id_does_not_crash(self):
        raw_events = {
            "Casts": [
                {"timestamp": 1000, "type": "cast", "sourceID": 999, "abilityGameID": 111},
            ]
        }
        events = log_parser.parse_events(raw_events, self.actors, self.abilities)
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0].source_name)  # unknown ID -> None, not a crash
        self.assertIsNone(events[0].ability_name)

    def test_raw_dict_preserved_for_unmodeled_fields(self):
        raw_events = {
            "Casts": [
                {"timestamp": 1000, "type": "cast", "sourceID": 1, "someWeirdField": "xyz"},
            ]
        }
        events = log_parser.parse_events(raw_events, self.actors, self.abilities)
        self.assertEqual(events[0].raw["someWeirdField"], "xyz")


class TestParseCombatantInfo(unittest.TestCase):
    def setUp(self):
        self.abilities = log_parser.parse_abilities(RAW_MASTER_DATA)
        self.snapshots = log_parser.parse_combatant_info(
            RAW_COMBATANT_INFO_EVENTS, self.abilities
        )

    def test_one_snapshot_per_player(self):
        self.assertEqual(set(self.snapshots.keys()), {1})

    def test_gear_parsed_with_enchant_and_gems(self):
        gear = self.snapshots[1].gear
        self.assertEqual(len(gear), 2)
        legs = next(g for g in gear if g.slot == 6)
        self.assertEqual(legs.item_id, 240157)
        self.assertEqual(legs.quality, 4)
        self.assertEqual(legs.permanent_enchant_id, 7452)
        self.assertEqual(legs.gem_ids, [240880])

    def test_gear_without_enchant_or_gems(self):
        chest = next(g for g in self.snapshots[1].gear if g.slot == 4)
        self.assertIsNone(chest.permanent_enchant_id)
        self.assertEqual(chest.gem_ids, [])

    def test_auras_resolved_where_possible(self):
        snapshot = self.snapshots[1]
        self.assertIn(555, snapshot.aura_ability_ids)
        self.assertIn(999999, snapshot.aura_ability_ids)
        # 555 resolves to "Flash Heal" via RAW_MASTER_DATA; 999999 doesn't exist there
        self.assertEqual(snapshot.aura_names, {"Flash Heal"})

    def test_empty_input_returns_empty_dict(self):
        self.assertEqual(log_parser.parse_combatant_info([], self.abilities), {})


class TestParseFightBundle(unittest.TestCase):
    def test_bundles_everything_together(self):
        parsed = log_parser.parse_fight_bundle(RAW_FIGHT, RAW_MASTER_DATA, RAW_EVENTS_BY_TYPE)
        self.assertEqual(parsed.fight.name, "Test Boss")
        self.assertEqual(len(parsed.actors), 3)
        self.assertEqual(len(parsed.abilities), 2)
        self.assertEqual(len(parsed.events), 2)

    def test_combatant_info_excluded_from_generic_events(self):
        events_with_combatant_info = dict(RAW_EVENTS_BY_TYPE)
        events_with_combatant_info["CombatantInfo"] = RAW_COMBATANT_INFO_EVENTS

        parsed = log_parser.parse_fight_bundle(
            RAW_FIGHT, RAW_MASTER_DATA, events_with_combatant_info
        )
        # CombatantInfo events must not leak into the generic events list...
        self.assertEqual(len(parsed.events), 2)
        self.assertTrue(all(e.data_type != "CombatantInfo" for e in parsed.events))
        # ...but must show up in parsed.combatant_info instead.
        self.assertIn(1, parsed.combatant_info)


if __name__ == "__main__":
    unittest.main()
