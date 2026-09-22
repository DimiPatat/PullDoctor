"""
test_healing_analyzer.py

Unit tests for healing_analyzer.py using a small hand-built ParsedFight --
no network calls. Run with:

    python test_healing_analyzer.py
"""

import unittest

import healing_analyzer
from data_models import Actor, Event, Fight, ParsedFight

FIGHT = Fight(
    id=1, name="Test Boss", difficulty=5, kill=True,
    start_time=0, end_time=10000, encounter_id=1,  # 10s fight
)

ACTORS = {
    1: Actor(id=1, name="Healbot", type="Player", subtype="Priest"),
    2: Actor(id=2, name="Discobot", type="Player", subtype="Priest"),
    10: Actor(id=10, name="Tankybot", type="Player", subtype="Warrior"),
    11: Actor(id=11, name="DPSbot", type="Player", subtype="Rogue"),
    # A pet owned by Healbot -- its heals should be credited to Healbot.
    50: Actor(id=50, name="Wild Imp", type="Pet", owner_id=1),
}


def build_events():
    return [
        # Healbot heals Tankybot: 5000 effective, 1000 overheal (wasted, ON TOP), via Flash Heal
        Event(timestamp=1000, event_type="heal", data_type="Healing",
              source_id=1, source_name="Healbot", target_id=10, target_name="Tankybot",
              ability_name="Flash Heal", amount=5000, overheal=1000),
        # Healbot heals DPSbot: 2000 effective, no overheal, via Renew
        Event(timestamp=2000, event_type="heal", data_type="Healing",
              source_id=1, source_name="Healbot", target_id=11, target_name="DPSbot",
              ability_name="Renew", amount=2000, overheal=0),
        # Discobot heals Tankybot: 0 effective, 3000 overheal (fully wasted)
        Event(timestamp=3000, event_type="heal", data_type="Healing",
              source_id=2, source_name="Discobot", target_id=10, target_name="Tankybot",
              ability_name="Power Word: Shield", amount=0, overheal=3000),
        # Healbot's pet (Wild Imp) heals Tankybot for 500 -- should be
        # credited to Healbot, not to "Wild Imp".
        Event(timestamp=3500, event_type="heal", data_type="Healing",
              source_id=50, source_name="Wild Imp", target_id=10, target_name="Tankybot",
              ability_name="Fel Regeneration", amount=500, overheal=0),
        # A non-healing event that should be ignored entirely
        Event(timestamp=4000, event_type="damage", data_type="DamageTaken",
              source_id=99, target_id=10, ability_name="Cleave", amount=999),
    ]


class TestAnalyzeHealing(unittest.TestCase):
    def setUp(self):
        self.parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        self.summaries = healing_analyzer.analyze_healing(self.parsed_fight)

    def test_one_summary_per_player_pets_folded_in(self):
        # Healbot, Discobot -- NOT a separate "Wild Imp" entry.
        self.assertEqual(len(self.summaries), 2)
        names = {s.healer_name for s in self.summaries}
        self.assertEqual(names, {"Healbot", "Discobot"})

    def test_pet_healing_credited_to_owner(self):
        healbot = next(s for s in self.summaries if s.healer_name == "Healbot")
        # 5000 (Flash Heal) + 2000 (Renew) + 500 (pet's Fel Regeneration) = 7500
        self.assertEqual(healbot.total_effective_healing, 7500)
        self.assertEqual(healbot.healing_by_ability["Fel Regeneration"], 500)

    def test_sorted_by_effective_healing_descending(self):
        self.assertEqual(self.summaries[0].healer_name, "Healbot")
        self.assertEqual(self.summaries[0].total_effective_healing, 7500)
        self.assertEqual(self.summaries[1].healer_name, "Discobot")
        self.assertEqual(self.summaries[1].total_effective_healing, 0)

    def test_effective_healing_never_negative(self):
        # Regression check for the amount-minus-overheal bug: effective
        # healing must equal `amount` directly, never go negative.
        for summary in self.summaries:
            self.assertGreaterEqual(summary.total_effective_healing, 0)

    def test_overheal_percent_uses_additive_raw_total(self):
        healbot = next(s for s in self.summaries if s.healer_name == "Healbot")
        # effective=7500, overheal=1000 -> raw=8500 -> ~11.8%
        self.assertAlmostEqual(healbot.overheal_percent, 1000 / 8500 * 100, places=1)

        discobot = next(s for s in self.summaries if s.healer_name == "Discobot")
        # effective=0, overheal=3000 -> raw=3000 -> 100% overheal, not >100%
        self.assertEqual(discobot.overheal_percent, 100.0)

    def test_hps_calculation(self):
        healbot = next(s for s in self.summaries if s.healer_name == "Healbot")
        # 7500 effective over a 10s fight = 750 HPS
        self.assertEqual(healbot.hps(FIGHT.duration_ms), 750.0)

    def test_ignores_non_healing_events(self):
        healer_ids = {s.healer_id for s in self.summaries}
        self.assertNotIn(99, healer_ids)

    def test_empty_fight_returns_empty_list(self):
        empty_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        self.assertEqual(healing_analyzer.analyze_healing(empty_fight), [])


class TestAnalyzeHealingReceived(unittest.TestCase):
    def setUp(self):
        self.parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        self.summaries = healing_analyzer.analyze_healing_received(self.parsed_fight)

    def test_one_summary_per_target(self):
        names = {s.target_name for s in self.summaries}
        self.assertEqual(names, {"Tankybot", "DPSbot"})

    def test_effective_healing_received_totals(self):
        tankybot = next(s for s in self.summaries if s.target_name == "Tankybot")
        # 5000 (Healbot) + 0 (Discobot) + 500 (pet -> Healbot) = 5500
        self.assertEqual(tankybot.total_effective_healing_received, 5500)

        dpsbot = next(s for s in self.summaries if s.target_name == "DPSbot")
        self.assertEqual(dpsbot.total_effective_healing_received, 2000)

    def test_healing_by_source_folds_pet_into_owner(self):
        tankybot = next(s for s in self.summaries if s.target_name == "Tankybot")
        # Healbot's own 5000 + pet's 500, folded together under "Healbot"
        self.assertEqual(tankybot.healing_by_source["Healbot"], 5500)
        self.assertNotIn("Wild Imp", tankybot.healing_by_source)
        self.assertEqual(tankybot.healing_by_source["Discobot"], 0)


class TestSummarizeHealing(unittest.TestCase):
    def test_summary_mentions_healer_and_hps(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        summaries = healing_analyzer.analyze_healing(parsed_fight)
        text = healing_analyzer.summarize_healing(parsed_fight, summaries)
        self.assertIn("Healbot", text)
        self.assertIn("750", text)

    def test_summary_with_no_healing(self):
        parsed_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        text = healing_analyzer.summarize_healing(parsed_fight, [])
        self.assertIn("no healing", text)


if __name__ == "__main__":
    unittest.main()
