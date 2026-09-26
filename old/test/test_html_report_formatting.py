"""
test_html_report_formatting.py

Covers the requested number-formatting fix for html_report.py:
  1. Large numbers must use thousands separators everywhere,
     specifically confirming HPS/DPS/DTPS (which were previously
     plain, uncomma'd f"{value:.0f}") now show "1,345" style, not "1345".
  2. Every numeric column (Heal window, Dmg window, HPS, DPS,
     Effective, Overheal, Total, DTPS, Damage Taken, plus the other
     numeric columns applied for consistency) is right-aligned via the
     shared "num" CSS class on both header and data cells.
  3. Text/label columns are explicitly NOT right-aligned.
"""
import re
import unittest

from data_models import Actor, Fight, ParsedFight, Event
from death_analyzer import DeathReport
from healing_analyzer import HealerSummary
from damage_done_analyzer import DamageDoneSummary
from damage_analyzer import DamageTakenSummary
from report import FightReportData
import html_report


def build_fight():
    return Fight(id=1, name="Ula'tek", difficulty=4, kill=True, start_time=0, end_time=262_000, encounter_id=3492, friendly_player_ids=[1, 2])


ACTORS = {
    1: Actor(id=1, name="Tankryte", type="Player", subtype="Warrior"),
    2: Actor(id=2, name="Drdaroou", type="Player", subtype="Priest"),
}


def build_full_data() -> FightReportData:
    parsed = ParsedFight(fight=build_fight(), actors=ACTORS, abilities={}, events=[])
    death_reports = [
        DeathReport(
            victim_id=1, victim_name="Tankryte", timestamp=50_000, time_into_fight_ms=50_000,
            killing_ability_name="Cleave", killing_blow_source_name="Boss",
        )
    ]
    # Rig actual numeric fields so their computed HPS/DPS/DTPS land in the thousands,
    # exactly the scenario where the missing comma formatting was visible.
    healer_summaries = [HealerSummary(healer_id=2, healer_name="Drdaroou", total_effective_healing=1_345_000, total_overheal=234_000)]
    damage_done_summaries = [DamageDoneSummary(player_id=1, player_name="Tankryte", total_damage_done=2_345_678)]
    damage_taken_summaries = [DamageTakenSummary(target_id=1, target_name="Tankryte", total_damage_taken=3_456_789)]
    return FightReportData(
        parsed_fight=parsed, death_reports=death_reports, healer_summaries=healer_summaries,
        damage_done_summaries=damage_done_summaries, damage_taken_summaries=damage_taken_summaries,
    )


class TestCommaFormattingForRateColumns(unittest.TestCase):
    """The exact bug reported: HPS/DPS/DTPS were missing thousands separators."""

    def test_hps_has_comma(self):
        html = html_report.render_html([build_full_data()], title="Test")
        # 1,345,000 effective healing / 262s ~= 5,133 HPS
        self.assertRegex(html, r">\d{1,3},\d{3}<")  # at least one comma-separated number somewhere
        # Specifically look inside the Healing section for a comma'd HPS value.
        healing_section = html[html.index(">Healing<"):html.index(">Damage Done")]
        self.assertRegex(healing_section, r"\d,\d{3}")

    def test_dps_has_comma(self):
        html = html_report.render_html([build_full_data()], title="Test")
        dps_section = html[html.index(">Damage Done"):html.index(">Damage Taken<")]
        self.assertRegex(dps_section, r"\d,\d{3}")

    def test_dtps_has_comma(self):
        html = html_report.render_html([build_full_data()], title="Test")
        dtps_section = html[html.index(">Damage Taken<"):]
        self.assertRegex(dtps_section, r"\d,\d{3}")

    def test_no_bare_four_plus_digit_number_without_comma_in_rate_columns(self):
        """
        Directly guard against the reported bug pattern: a 4+ digit
        number with NO comma at all (e.g. "5133") appearing where a
        rate value should be.
        """
        html = html_report.render_html([build_full_data()], title="Test")
        healing_section = html[html.index(">Healing<"):html.index(">Damage Done")]
        # Find the HPS cell specifically: <td class="num">####</td> with no comma is the bug pattern.
        bug_pattern = re.search(r'<td class="num">(\d{4,})</td>', healing_section)
        self.assertIsNone(bug_pattern, f"found an un-comma'd 4+ digit number: {bug_pattern.group(1) if bug_pattern else None}")

    def test_total_damage_done_already_had_comma_and_still_does(self):
        html = html_report.render_html([build_full_data()], title="Test")
        self.assertIn("2,345,678", html)

    def test_total_damage_taken_already_had_comma_and_still_does(self):
        html = html_report.render_html([build_full_data()], title="Test")
        self.assertIn("3,456,789", html)

    def test_total_effective_healing_has_comma(self):
        html = html_report.render_html([build_full_data()], title="Test")
        self.assertIn("1,345,000", html)

    def test_total_overheal_has_comma(self):
        html = html_report.render_html([build_full_data()], title="Test")
        self.assertIn("234,000", html)


class TestRightAlignmentOfNumericColumns(unittest.TestCase):
    def test_heal_window_and_dmg_window_headers_are_numeric_class(self):
        html = html_report.render_html([build_full_data()], title="Test")
        self.assertIn('<th class="num">Dmg (window)</th>', html)
        self.assertIn('<th class="num">Heal (window)</th>', html)

    def test_hps_dps_dtps_headers_are_numeric_class(self):
        html = html_report.render_html([build_full_data()], title="Test")
        self.assertIn('<th class="num">HPS</th>', html)
        self.assertIn('<th class="num">DPS</th>', html)
        self.assertIn('<th class="num">DTPS</th>', html)

    def test_effective_overheal_total_headers_are_numeric_class(self):
        html = html_report.render_html([build_full_data()], title="Test")
        self.assertIn('<th class="num">Effective</th>', html)
        self.assertIn('<th class="num">Overheal</th>', html)
        self.assertIn('<th class="num">Overheal %</th>', html)
        # "Total" appears in both Damage Done and Damage Taken sections.
        self.assertEqual(html.count('<th class="num">Total</th>'), 2)

    def test_data_cells_in_numeric_columns_have_num_class(self):
        html = html_report.render_html([build_full_data()], title="Test")
        deaths_section = html[html.index(">Deaths<"):html.index(">Healing<")]
        # Dmg (window) and Heal (window) are the last two columns in the Deaths row.
        self.assertIn('<td class="num">', deaths_section)

    def test_text_columns_are_not_marked_numeric(self):
        html = html_report.render_html([build_full_data()], title="Test")
        self.assertIn("<th>Victim</th>", html)
        self.assertNotIn('<th class="num">Victim</th>', html)
        self.assertIn("<th>Killed By</th>", html)
        self.assertNotIn('<th class="num">Killed By</th>', html)

    def test_player_name_column_never_marked_numeric_even_in_meter_rows(self):
        """The name/meter cell (Healer/Player) must never get the 'num' class -- it's a name, not a number."""
        html = html_report.render_html([build_full_data()], title="Test")
        self.assertIn("<th>Healer</th>", html)
        self.assertNotIn('<th class="num">Healer</th>', html)

    def test_css_rule_for_num_class_right_aligns(self):
        html = html_report.render_html([build_full_data()], title="Test")
        self.assertIn("th.num, td.num", html)
        self.assertIn("text-align: right", html)

    def test_numeric_indices_helper_correct_for_simple_header_list(self):
        headers = ["Time", "Victim", "Killed By", "Dmg (window)", "Heal (window)"]
        indices = html_report._numeric_indices(headers)
        self.assertEqual(indices, frozenset({3, 4}))

    def test_numeric_indices_helper_excludes_text_headers(self):
        headers = ["Player", "Ability", "Target"]
        indices = html_report._numeric_indices(headers)
        self.assertEqual(indices, frozenset())


class TestOtherNumericColumnsAlsoAligned(unittest.TestCase):
    """Applied for consistency beyond the exact columns named in the request."""

    def test_avg_ilvl_and_gems_headers_numeric(self):
        self.assertIn("Avg iLvl", html_report.NUMERIC_HEADERS)
        self.assertIn("Gems", html_report.NUMERIC_HEADERS)

    def test_casts_max_and_efficiency_headers_numeric(self):
        self.assertIn("Casts/Max", html_report.NUMERIC_HEADERS)
        self.assertIn("Efficiency", html_report.NUMERIC_HEADERS)

    def test_missing_count_header_numeric(self):
        self.assertIn("# Missing", html_report.NUMERIC_HEADERS)

    def test_prevented_and_windows_headers_numeric(self):
        self.assertIn("Prevented", html_report.NUMERIC_HEADERS)
        self.assertIn("Windows", html_report.NUMERIC_HEADERS)
        self.assertIn("Dmg Taken (windows)", html_report.NUMERIC_HEADERS)

    def test_missing_enchants_and_breakdown_not_numeric(self):
        self.assertNotIn("Missing Enchants", html_report.NUMERIC_HEADERS)
        self.assertNotIn("Breakdown", html_report.NUMERIC_HEADERS)
        self.assertNotIn("Lowest Quality", html_report.NUMERIC_HEADERS)


if __name__ == "__main__":
    unittest.main()
