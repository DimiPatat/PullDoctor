"""
test_consumables_analyzer.py

Unit tests for consumables_analyzer.py using a small hand-built
ParsedFight -- no network calls. Run with:

    python test_consumables_analyzer.py
"""

import unittest

import consumables_analyzer
from consumable_data import ConsumableDefinition
from data_models import Actor, CombatantInfoSnapshot, Event, Fight, ParsedFight

FIGHT = Fight(
    id=1, name="Test Boss", difficulty=5, kill=True,
    start_time=0, end_time=300_000, encounter_id=1,
    friendly_player_ids=[1, 2, 3],
)

ACTORS = {
    1: Actor(id=1, name="Alpha", type="Player", subtype="Priest"),
    2: Actor(id=2, name="Bravo", type="Player", subtype="Warrior"),
    3: Actor(id=3, name="Charlie", type="Player", subtype="Mage"),
}

DEFS = [
    ConsumableDefinition("Silvermoon Health Potion", "health_potion"),
    ConsumableDefinition("Amani Cornucopia", "food"),
    ConsumableDefinition("Thalassian Phoenix Oil", "oil"),
]


def build_combatant_info():
    return {
        # Alpha: has food pre-popped (aura at pull start), no potion, no oil
        1: CombatantInfoSnapshot(player_id=1, aura_names={"Amani Cornucopia"}),
        # Bravo: has food AND oil pre-popped
        2: CombatantInfoSnapshot(player_id=2, aura_names={"Amani Cornucopia", "Thalassian Phoenix Oil"}),
        # Charlie: nothing pre-popped
        3: CombatantInfoSnapshot(player_id=3, aura_names=set()),
    }


def build_events():
    return [
        # Charlie drinks a health potion reactively mid-fight
        Event(timestamp=50_000, event_type="cast", data_type="Casts",
              source_id=3, source_name="Charlie", ability_name="Silvermoon Health Potion"),
        # Bravo also drinks a health potion, on top of the pre-popped food/oil
        Event(timestamp=60_000, event_type="cast", data_type="Casts",
              source_id=2, source_name="Bravo", ability_name="Silvermoon Health Potion"),
        # A non-cast event that should be ignored
        Event(timestamp=70_000, event_type="heal", data_type="Healing",
              source_id=1, target_id=2, ability_name="Flash Heal", amount=500),
    ]


def build_parsed_fight():
    return ParsedFight(
        fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        combatant_info=build_combatant_info(),
    )


class TestAnalyzeConsumables(unittest.TestCase):
    def setUp(self):
        self.parsed_fight = build_parsed_fight()
        self.results = consumables_analyzer.analyze_consumables(self.parsed_fight, DEFS)

    def test_one_entry_per_player_in_fight_roster(self):
        self.assertEqual(len(self.results), 3)
        names = {r.player_name for r in self.results}
        self.assertEqual(names, {"Alpha", "Bravo", "Charlie"})

    def test_prepull_aura_detected(self):
        alpha = next(r for r in self.results if r.player_name == "Alpha")
        self.assertTrue(alpha.used("food"))
        self.assertEqual(alpha.items_by_category["food"], ["Amani Cornucopia"])
        self.assertFalse(alpha.used("health_potion"))
        self.assertFalse(alpha.used("oil"))

    def test_mid_fight_cast_detected(self):
        charlie = next(r for r in self.results if r.player_name == "Charlie")
        self.assertTrue(charlie.used("health_potion"))
        self.assertFalse(charlie.used("food"))

    def test_both_sources_combine_for_same_player(self):
        bravo = next(r for r in self.results if r.player_name == "Bravo")
        self.assertTrue(bravo.used("food"))
        self.assertTrue(bravo.used("oil"))
        self.assertTrue(bravo.used("health_potion"))  # from the mid-fight cast

    def test_missing_categories(self):
        alpha = next(r for r in self.results if r.player_name == "Alpha")
        self.assertEqual(
            set(alpha.missing_categories(["health_potion", "food", "oil"])),
            {"health_potion", "oil"},
        )

        bravo = next(r for r in self.results if r.player_name == "Bravo")
        self.assertEqual(bravo.missing_categories(["health_potion", "food", "oil"]), [])

    def test_player_with_nothing_still_appears(self):
        empty_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=[], combatant_info={},
        )
        results = consumables_analyzer.analyze_consumables(empty_fight, DEFS)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r.items_by_category, {})


class TestCheckVantusRune(unittest.TestCase):
    RUNE_NAME = "Vantus Rune: Tides"

    def test_below_threshold_flags_nobody(self):
        # Only Alpha has it -- 1 out of 3 is below half, so nobody should be flagged.
        combatant_info = {
            1: CombatantInfoSnapshot(player_id=1, aura_names={self.RUNE_NAME}),
            2: CombatantInfoSnapshot(player_id=2, aura_names=set()),
            3: CombatantInfoSnapshot(player_id=3, aura_names=set()),
        }
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=[], combatant_info=combatant_info,
        )
        result = consumables_analyzer.check_vantus_rune(parsed_fight, self.RUNE_NAME)
        self.assertFalse(result.threshold_met)
        self.assertEqual(result.players_missing, [])
        self.assertEqual(result.players_with, ["Alpha"])

    def test_at_or_above_threshold_flags_missing(self):
        # Alpha and Bravo have it -- 2 out of 3 meets the half threshold.
        combatant_info = {
            1: CombatantInfoSnapshot(player_id=1, aura_names={self.RUNE_NAME}),
            2: CombatantInfoSnapshot(player_id=2, aura_names={self.RUNE_NAME}),
            3: CombatantInfoSnapshot(player_id=3, aura_names=set()),
        }
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=[], combatant_info=combatant_info,
        )
        result = consumables_analyzer.check_vantus_rune(parsed_fight, self.RUNE_NAME)
        self.assertTrue(result.threshold_met)
        self.assertEqual(result.players_missing, ["Charlie"])

    def test_nobody_has_it_does_not_flag(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=[], combatant_info={},
        )
        result = consumables_analyzer.check_vantus_rune(parsed_fight, self.RUNE_NAME)
        self.assertFalse(result.threshold_met)
        self.assertEqual(result.players_missing, [])

    def test_everyone_has_it(self):
        combatant_info = {
            pid: CombatantInfoSnapshot(player_id=pid, aura_names={self.RUNE_NAME})
            for pid in (1, 2, 3)
        }
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=[], combatant_info=combatant_info,
        )
        result = consumables_analyzer.check_vantus_rune(parsed_fight, self.RUNE_NAME)
        self.assertTrue(result.threshold_met)
        self.assertEqual(result.players_missing, [])


class TestSummarizeConsumables(unittest.TestCase):
    def test_summary_mentions_missing_categories(self):
        parsed_fight = build_parsed_fight()
        results = consumables_analyzer.analyze_consumables(parsed_fight, DEFS)
        text = consumables_analyzer.summarize_consumables(
            parsed_fight, results, ["health_potion", "food", "oil"]
        )
        self.assertIn("Alpha", text)
        self.assertIn("missing", text)

    def test_summary_includes_vantus_section_when_provided(self):
        parsed_fight = build_parsed_fight()
        results = consumables_analyzer.analyze_consumables(parsed_fight, DEFS)
        vantus = consumables_analyzer.VantusRuneCheck(
            threshold_met=True, players_with=["Alpha", "Bravo"], players_missing=["Charlie"]
        )
        text = consumables_analyzer.summarize_consumables(
            parsed_fight, results, ["health_potion"], vantus_check=vantus
        )
        self.assertIn("Vantus Rune", text)
        self.assertIn("Charlie", text)


if __name__ == "__main__":
    unittest.main()
