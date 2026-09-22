"""
test_report.py

Unit tests for report.py using small hand-built analyzer results --
no network calls, no dependency on actually running the analyzers
against real data. Run with:

    python test_report.py
"""

import os
import shutil
import tempfile
import unittest

import report
from avoidable_damage_analyzer import PlayerAvoidableDamage
from avoidable_damage_data import AvoidableMechanic, EncounterConfig
from consumables_analyzer import PlayerConsumables, VantusRuneCheck
from cooldown_analyzer import CooldownUsage
from damage_analyzer import DamageTakenSummary
from damage_done_analyzer import DamageDoneSummary
from data_models import Actor, Event, Fight, ParsedFight
from death_analyzer import DeathReport
from gear_analyzer import PlayerGearReport, GearPieceReport
from healing_analyzer import HealerSummary

FIGHT = Fight(
    id=1, name="Test Boss", difficulty=5, kill=False,
    start_time=0, end_time=100_000, encounter_id=1,
    friendly_player_ids=[1, 2],
)
ACTORS = {
    1: Actor(id=1, name="Alpha", type="Player", subtype="Priest"),
    2: Actor(id=2, name="Bravo", type="Player", subtype="Warrior"),
}


def build_minimal_data() -> report.FightReportData:
    """Just a ParsedFight, nothing else -- the minimal valid input."""
    parsed_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])
    return report.FightReportData(parsed_fight=parsed_fight)


def build_full_data() -> report.FightReportData:
    """One of everything, to exercise every section of both renderers."""
    parsed_fight = ParsedFight(fight=FIGHT, actors=ACTORS, abilities={}, events=[])

    death_reports = [
        DeathReport(
            victim_id=2, victim_name="Bravo", timestamp=90_000, time_into_fight_ms=90_000,
            killing_ability_name="Big Cleave", killing_blow_source_name="Test Boss",
        )
    ]
    healer_summaries = [
        HealerSummary(healer_id=1, healer_name="Alpha", total_effective_healing=5000, total_overheal=500)
    ]
    damage_taken_summaries = [
        DamageTakenSummary(target_id=2, target_name="Bravo", total_damage_taken=8000)
    ]
    biggest_hits = [
        Event(timestamp=5000, event_type="damage", data_type="DamageTaken",
              target_id=2, target_name="Bravo", ability_name="Cleave", amount=4000)
    ]
    damage_done_summaries = [
        DamageDoneSummary(player_id=2, player_name="Bravo", total_damage_done=12000)
    ]
    cooldown_usages = [
        CooldownUsage(
            player_id=1, player_name="Alpha", ability_name="Divine Hymn",
            cooldown_seconds=120, category="raid_cd", cast_timestamps=[0, 60_000],
        )
    ]
    consumable_results = [
        PlayerConsumables(player_id=1, player_name="Alpha", items_by_category={"food": ["Amani Cornucopia"]}),
        PlayerConsumables(player_id=2, player_name="Bravo", items_by_category={}),
        PlayerConsumables(
            player_id=3, player_name="Charlie",
            items_by_category={"food": ["Amani Cornucopia"], "health_potion": ["Silvermoon Health Potion"]},
        ),
    ]
    vantus_check = VantusRuneCheck(threshold_met=True, players_with=["Alpha"], players_missing=["Bravo"])
    gear_reports = [
        PlayerGearReport(
            player_id=1, player_name="Alpha",
            pieces=[GearPieceReport(
                slot=4, slot_name="Chest", item_id=111, quality=4, item_level=720,
                is_enchantable_slot=True, has_enchant=False, enchant_id=None, gem_count=1,
            )],
        )
    ]
    avoidable_config = EncounterConfig(
        encounter_id=1, encounter_name="Test Boss",
        mechanics=[AvoidableMechanic(ability_name="Fire Patch", track_via=("damage",))],
    )
    avoidable_reports = [
        PlayerAvoidableDamage(player_id=2, player_name="Bravo", hits_by_mechanic={"Fire Patch": 2}),
    ]

    return report.FightReportData(
        parsed_fight=parsed_fight,
        death_reports=death_reports,
        healer_summaries=healer_summaries,
        damage_taken_summaries=damage_taken_summaries,
        biggest_hits=biggest_hits,
        damage_done_summaries=damage_done_summaries,
        cooldown_usages=cooldown_usages,
        consumable_results=consumable_results,
        consumable_categories=["food", "health_potion"],
        vantus_check=vantus_check,
        gear_reports=gear_reports,
        avoidable_reports=avoidable_reports,
        avoidable_config=avoidable_config,
    )


class TestRenderTextMinimal(unittest.TestCase):
    def test_minimal_data_does_not_crash_and_includes_fight_name(self):
        text = report.render_text(build_minimal_data())
        self.assertIn("Test Boss", text)
        self.assertIn("wipe", text)


class TestRenderTextFull(unittest.TestCase):
    def setUp(self):
        self.text = report.render_text(build_full_data())

    def test_includes_every_section_header(self):
        for section in [
            "Deaths", "Healing", "Damage Taken", "Biggest Hits",
            "Damage Done (DPS)", "Cooldown Usage", "Consumables",
            "Gear Check", "Avoidable Damage",
        ]:
            self.assertIn(section, self.text)

    def test_includes_key_data_points(self):
        self.assertIn("Bravo", self.text)
        self.assertIn("Big Cleave", self.text)
        self.assertIn("Fire Patch", self.text)


class TestRenderMarkdownMinimal(unittest.TestCase):
    def test_minimal_data_renders_just_the_title(self):
        md = report.render_markdown(build_minimal_data())
        self.assertTrue(md.startswith("# Test Boss"))
        # No section headers should appear when there's no data for them.
        self.assertNotIn("## Healing", md)


class TestRenderMarkdownFull(unittest.TestCase):
    def setUp(self):
        self.md = report.render_markdown(build_full_data())

    def test_includes_every_section_as_markdown_header(self):
        for section in [
            "## Deaths", "## Healing", "## Damage Taken", "## Biggest Hits",
            "## Damage Done (DPS)", "## Cooldown Usage", "## Consumables",
            "## Gear Check", "## Avoidable Damage",
        ]:
            self.assertIn(section, self.md)

    def test_tables_use_markdown_pipe_syntax(self):
        self.assertIn("| Healer | HPS | Effective | Overheal % |", self.md)
        self.assertIn("|---|---|---|---|", self.md)

    def test_vantus_rune_section_present(self):
        self.assertIn("Vantus Rune", self.md)
        self.assertIn("Bravo", self.md)

    def test_missing_consumables_shown_per_player(self):
        self.assertIn("health_potion", self.md)
        self.assertIn("food, health_potion", self.md)  # Bravo has nothing recorded -- missing both
        self.assertIn("_all covered_", self.md)  # Charlie has both categories covered

    def test_gear_missing_enchant_shown(self):
        self.assertIn("Chest", self.md)


class TestWriteReport(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_writes_both_files_with_matching_content(self):
        data = build_full_data()
        text_path, md_path = report.write_report(data, self.tmp_dir, "test_fight_report")

        self.assertTrue(os.path.exists(text_path))
        self.assertTrue(os.path.exists(md_path))
        self.assertTrue(text_path.endswith(".txt"))
        self.assertTrue(md_path.endswith(".md"))

        with open(text_path) as f:
            text_content = f.read()
        with open(md_path) as f:
            md_content = f.read()

        self.assertIn("Test Boss", text_content)
        self.assertIn("Test Boss", md_content)
        self.assertEqual(text_content, report.render_text(data))
        self.assertEqual(md_content, report.render_markdown(data))


if __name__ == "__main__":
    unittest.main()
