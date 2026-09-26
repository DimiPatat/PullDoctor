"""
test_cooldown_analyzer.py

Unit tests for cooldown_analyzer.py using a small hand-built ParsedFight
-- no network calls. Run with:

    python test_cooldown_analyzer.py
"""

import unittest

import cooldown_analyzer
from data_models import Actor, Event, Fight, ParsedFight

# 5-minute fight -- long enough to fit multiple casts of a 2-minute cooldown.
FIGHT = Fight(
    id=1, name="Test Boss", difficulty=5, kill=True,
    start_time=0, end_time=300_000, encounter_id=1,
)

ACTORS = {
    1: Actor(id=1, name="Healbot", type="Player", subtype="Priest"),
    2: Actor(id=2, name="Discobot", type="Player", subtype="Priest"),
}

DEFS = [
    cooldown_analyzer.CooldownDefinition(
        ability_name="Divine Hymn", cooldown_seconds=120, category="raid_cd"
    ),
]


def build_events():
    return [
        # Healbot casts Divine Hymn at 0s and again at 130s (efficient: 2 casts)
        Event(timestamp=0, event_type="cast", data_type="Casts",
              source_id=1, source_name="Healbot", ability_name="Divine Hymn"),
        Event(timestamp=130_000, event_type="cast", data_type="Casts",
              source_id=1, source_name="Healbot", ability_name="Divine Hymn"),
        # Discobot only casts it once, at 200s (inefficient use of the window)
        Event(timestamp=200_000, event_type="cast", data_type="Casts",
              source_id=2, source_name="Discobot", ability_name="Divine Hymn"),
        # A cast of an ability we're NOT tracking -- should be ignored
        Event(timestamp=50_000, event_type="cast", data_type="Casts",
              source_id=1, source_name="Healbot", ability_name="Flash Heal"),
        # A non-cast event that should be ignored entirely
        Event(timestamp=60_000, event_type="heal", data_type="Healing",
              source_id=1, target_id=2, ability_name="Divine Hymn", amount=500),
    ]


class TestAnalyzeCooldownUsage(unittest.TestCase):
    def setUp(self):
        self.parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        self.usages = cooldown_analyzer.analyze_cooldown_usage(self.parsed_fight, DEFS)

    def test_one_usage_per_player_per_tracked_ability(self):
        self.assertEqual(len(self.usages), 2)
        names = {u.player_name for u in self.usages}
        self.assertEqual(names, {"Healbot", "Discobot"})

    def test_cast_timestamps_recorded_in_order(self):
        healbot = next(u for u in self.usages if u.player_name == "Healbot")
        self.assertEqual(healbot.cast_timestamps, [0, 130_000])
        self.assertEqual(healbot.num_casts, 2)

    def test_untracked_ability_and_non_cast_events_ignored(self):
        healbot = next(u for u in self.usages if u.player_name == "Healbot")
        # Only the 2 Divine Hymn casts should count, not Flash Heal or the heal event.
        self.assertEqual(healbot.num_casts, 2)

    def test_gaps_ms(self):
        healbot = next(u for u in self.usages if u.player_name == "Healbot")
        self.assertEqual(healbot.gaps_ms, [130_000])

        discobot = next(u for u in self.usages if u.player_name == "Discobot")
        self.assertEqual(discobot.gaps_ms, [])  # only 1 cast -- no gaps

    def test_theoretical_max_casts(self):
        healbot = next(u for u in self.usages if u.player_name == "Healbot")
        # 300s fight, 120s cooldown -> floor(300/120) + 1 = 2 + 1 = 3
        self.assertEqual(healbot.theoretical_max_casts(FIGHT.duration_ms), 3)

    def test_efficiency_calculation(self):
        healbot = next(u for u in self.usages if u.player_name == "Healbot")
        # 2 casts / 3 max = 0.667
        self.assertAlmostEqual(healbot.efficiency(FIGHT.duration_ms), 2 / 3, places=3)

        discobot = next(u for u in self.usages if u.player_name == "Discobot")
        # 1 cast / 3 max = 0.333
        self.assertAlmostEqual(discobot.efficiency(FIGHT.duration_ms), 1 / 3, places=3)

    def test_efficiency_capped_at_one(self):
        # Simulate a player who somehow cast more than the theoretical max.
        usage = cooldown_analyzer.CooldownUsage(
            player_id=1, player_name="Overcaster", ability_name="Divine Hymn",
            cooldown_seconds=120, category="raid_cd",
            cast_timestamps=[0, 10_000, 20_000, 30_000, 40_000],
        )
        self.assertEqual(usage.efficiency(FIGHT.duration_ms), 1.0)

    def test_untracked_definitions_produce_no_usage(self):
        empty_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        self.assertEqual(cooldown_analyzer.analyze_cooldown_usage(empty_fight, DEFS), [])

    def test_zero_cooldown_seconds_does_not_crash(self):
        bad_def = cooldown_analyzer.CooldownDefinition(ability_name="X", cooldown_seconds=0)
        usage = cooldown_analyzer.CooldownUsage(
            player_id=1, player_name="P", ability_name="X", cooldown_seconds=0, category=None,
        )
        self.assertEqual(usage.theoretical_max_casts(FIGHT.duration_ms), 0)
        self.assertEqual(usage.efficiency(FIGHT.duration_ms), 0.0)


class TestSummarizeCooldownUsage(unittest.TestCase):
    def test_summary_mentions_player_and_ratio(self):
        parsed_fight = ParsedFight(
            fight=FIGHT, actors=ACTORS, abilities={}, events=build_events(),
        )
        usages = cooldown_analyzer.analyze_cooldown_usage(parsed_fight, DEFS)
        text = cooldown_analyzer.summarize_cooldown_usage(parsed_fight, usages)
        self.assertIn("Healbot", text)
        self.assertIn("2/3", text)

    def test_summary_with_no_usage(self):
        parsed_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
        text = cooldown_analyzer.summarize_cooldown_usage(parsed_fight, [])
        self.assertIn("no tracked cooldown usage", text)


if __name__ == "__main__":
    unittest.main()
