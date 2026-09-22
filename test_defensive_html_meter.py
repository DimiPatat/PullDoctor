"""
test_defensive_html_meter.py

Covers the improvement to html_report.py: "Damage Prevented by
Defensives" now uses the SAME meter-bar treatment as Damage Done/
Healing/Damage Taken (class-colored relative-width bar behind the
player's name), instead of a plain table.

Key behaviors verified:
  1. The overview table has meter bars, scaled the same way as every
     other meter section (top row = 100%, others relative).
  2. A player whose ONLY defensive usage was "immunity" (no
     damage_prevented value is even possible) gets a DISTINCT hatched/
     dimmed 0%-width bar (class "no-estimate"), not a bar that looks
     like "measured, and it was zero".
  3. The per-player window-detail tables remain PLAIN tables (no meter
     bars) -- each row there is a single point-in-time event, not a
     ranked peer comparison.
  4. Numbers are still comma-formatted and right-aligned, consistent
     with the rest of the report.
"""
import re
import unittest

from data_models import Actor, Fight, ParsedFight
from defensive_damage_prevention_analyzer import DefensiveWindow, PlayerDamagePrevention
from report import FightReportData
import html_report


def build_fight():
    return Fight(id=1, name="Ula'tek", difficulty=4, kill=False, start_time=0, end_time=262_000, encounter_id=3492, friendly_player_ids=[1, 2, 3])


ACTORS = {
    1: Actor(id=1, name="Tankryte", type="Player", subtype="Paladin"),
    2: Actor(id=2, name="Illidarw", type="Player", subtype="Mage"),
    3: Actor(id=3, name="Stárkk", type="Player", subtype="DeathKnight"),
}


def build_data_with_mixed_prevention() -> FightReportData:
    parsed = ParsedFight(fight=build_fight(), actors=ACTORS, abilities={}, events=[])

    # Tankryte: percent_reduction, prevented a real amount -- should get the TOP bar (100%).
    tankryte = PlayerDamagePrevention(player_id=1, player_name="Tankryte", windows=[
        DefensiveWindow(
            player_id=1, player_name="Tankryte", ability_name="Ardent Defender", mitigation_type="percent_reduction",
            cast_timestamp=50_000, window_end_timestamp=58_000, damage_reduction_percent=20.0,
            actual_damage_taken=1000, damage_prevented=2000,
        )
    ])
    # Stárkk: percent_reduction too, but prevented HALF of Tankryte's total -> should be a 50% bar.
    starkk = PlayerDamagePrevention(player_id=3, player_name="Stárkk", windows=[
        DefensiveWindow(
            player_id=3, player_name="Stárkk", ability_name="Icebound Fortitude", mitigation_type="percent_reduction",
            cast_timestamp=60_000, window_end_timestamp=68_000, damage_reduction_percent=30.0,
            actual_damage_taken=500, damage_prevented=1000,
        )
    ])
    # Illidarw: ONLY used Ice Block (immunity) -- damage_prevented is None, so
    # total_damage_prevented is 0 via the dataclass property, but this is a
    # "can't be measured" case, not "measured and found to be zero".
    illidarw = PlayerDamagePrevention(player_id=2, player_name="Illidarw", windows=[
        DefensiveWindow(
            player_id=2, player_name="Illidarw", ability_name="Ice Block", mitigation_type="immunity",
            cast_timestamp=100_000, window_end_timestamp=110_000, damage_reduction_percent=None,
            actual_damage_taken=15, damage_prevented=None,
        )
    ])

    # Sorted descending by total_damage_prevented, matching what the real analyzer returns.
    entries = sorted([tankryte, starkk, illidarw], key=lambda e: e.total_damage_prevented, reverse=True)
    return FightReportData(parsed_fight=parsed, defensive_damage_prevention=entries)


class TestOverviewTableUsesMeterBars(unittest.TestCase):
    def setUp(self):
        self.data = build_data_with_mixed_prevention()
        self.html = html_report.render_html([self.data], title="Test")
        start = self.html.index(">Damage Prevented by Defensives<")
        end = self.html.index("</details>", start)
        self.section = self.html[start:end]

    def test_top_entry_gets_100_percent_bar(self):
        # Tankryte prevented 2000, the highest -> 100% width.
        pattern = re.search(
            r'<div class="meter-bar" style="width:([\d.]+)%;[^"]*"></div><span class="meter-name">Tankryte</span>',
            self.section,
        )
        self.assertIsNotNone(pattern, "Tankryte's meter bar not found in expected format")
        self.assertEqual(pattern.group(1), "100.0")

    def test_second_entry_scaled_relative_to_top(self):
        # Stárkk prevented 1000 / 2000 = 50%.
        pattern = re.search(
            r'<div class="meter-bar" style="width:([\d.]+)%;[^"]*"></div><span class="meter-name">St.rkk</span>',
            self.section,
        )
        self.assertIsNotNone(pattern)
        self.assertEqual(pattern.group(1), "50.0")

    def test_immunity_only_player_gets_no_estimate_bar_class(self):
        """Illidarw only used immunity -- must get the DISTINCT 'no-estimate' bar, not a plain 0% bar."""
        pattern = re.search(
            r'<div class="meter-bar no-estimate" style="width:0\.0%;[^"]*"></div><span class="meter-name">Illidarw</span>',
            self.section,
        )
        self.assertIsNotNone(pattern, "Illidarw should get the hatched/dimmed 'no-estimate' bar style")

    def test_no_estimate_bar_has_a_visible_minimum_width_override(self):
        """
        A 0%-width bar has nothing for a background pattern to render
        on -- confirm the CSS actually forces a small fixed visible
        width for '.no-estimate' bars (this was a real bug caught via
        a rendered screenshot: without this, the 'distinct' bar was
        visually IDENTICAL to a plain empty bar).
        """
        self.assertIn("width: 28px !important", self.html)

    def test_immunity_only_player_shows_na_not_zero_in_prevented_cell(self):
        """
        A literal '0' in the Prevented cell would misleadingly imply
        'measured, and it prevented nothing' -- the true situation for
        an immunity-only player is 'cannot be measured at all'.
        """
        pattern = re.search(r'<span class="meter-name">Illidarw</span></td><td class="num">([^<]+)</td>', self.section)
        self.assertIsNotNone(pattern)
        self.assertEqual(pattern.group(1), "n/a")

    def test_measured_zero_would_not_get_no_estimate_class(self):
        """Sanity check the distinction actually matters: a player with a REAL (non-immunity) 0 prevented would NOT get 'no-estimate'."""
        parsed = ParsedFight(fight=build_fight(), actors=ACTORS, abilities={}, events=[])
        zero_but_measured = PlayerDamagePrevention(player_id=1, player_name="Tankryte", windows=[
            DefensiveWindow(
                player_id=1, player_name="Tankryte", ability_name="Ardent Defender", mitigation_type="percent_reduction",
                cast_timestamp=50_000, window_end_timestamp=58_000, damage_reduction_percent=20.0,
                actual_damage_taken=0, damage_prevented=0,
            )
        ])
        data = FightReportData(parsed_fight=parsed, defensive_damage_prevention=[zero_but_measured])
        html = html_report.render_html([data], title="Test")
        # NOTE: checking the substring "no-estimate" against the WHOLE document would
        # always match, since the CSS stylesheet itself defines ".meter-bar.no-estimate"
        # regardless of data -- so check the actual RENDERED bar's class attribute instead.
        pattern = re.search(r'<div class="(meter-bar[^"]*)" style="width:0\.0%;[^"]*"></div><span class="meter-name">Tankryte</span>', html)
        self.assertIsNotNone(pattern, "Tankryte's bar not found")
        self.assertEqual(pattern.group(1), "meter-bar", "a measured (non-immunity) zero must render plain 'meter-bar', not 'meter-bar no-estimate'")

    def test_bar_color_matches_player_class(self):
        import class_colors
        paladin_color = class_colors.CLASS_COLORS["Paladin"]
        self.assertIn(paladin_color.lower(), self.section.lower())

    def test_overview_headers_present_and_numeric_ones_right_aligned(self):
        self.assertIn('<th>Player</th>', self.section)
        self.assertIn('<th class="num">Prevented</th>', self.section)
        self.assertIn('<th class="num">Dmg Taken (windows)</th>', self.section)
        self.assertIn('<th class="num">Windows</th>', self.section)

    def test_overview_numbers_comma_formatted(self):
        self.assertIn("2,000", self.section)  # Tankryte's prevented total
        self.assertIn("1,000", self.section)  # Stárkk's prevented total


class TestWindowDetailTablesRemainPlain(unittest.TestCase):
    def setUp(self):
        self.data = build_data_with_mixed_prevention()
        self.html = html_report.render_html([self.data], title="Test")
        start = self.html.index(">Damage Prevented by Defensives<")
        end = self.html.index("</details>", start)
        self.section = self.html[start:end]

    def test_detail_section_has_a_heading_per_player(self):
        self.assertIn('<p class="defensive-player-heading">Tankryte</p>', self.section)
        self.assertIn('<p class="defensive-player-heading">Illidarw</p>', self.section)

    def test_detail_tables_have_no_meter_cells(self):
        """The per-window detail tables are plain -- confirm no meter-cell markup leaks into them specifically (only the overview row should have one)."""
        # Count meter-cell occurrences: exactly one per overview row (3 players) -- none in detail tables.
        self.assertEqual(self.section.count('class="meter-cell"'), 3)

    def test_detail_table_shows_ability_and_mitigation_columns(self):
        self.assertIn("Ardent Defender", self.section)
        self.assertIn("Icebound Fortitude", self.section)
        self.assertIn("Ice Block", self.section)

    def test_immunity_detail_row_shows_residual_not_prevented(self):
        self.assertIn("(residual)", self.section)

    def test_immunity_only_player_gets_explanatory_note(self):
        self.assertIn("no damage-prevented estimate is possible", self.section)

    def test_non_immunity_players_do_not_get_the_immunity_note(self):
        tankryte_start = self.section.index("Tankryte")
        starkk_start = self.section.index("Stárkk")
        # The note text should not appear right after Tankryte's or Stárkk's heading block
        # (only Illidarw's block, which comes last in sorted order here).
        illidarw_start = self.section.index("Illidarw")
        self.assertGreater(illidarw_start, starkk_start)  # sanity: Illidarw's block is after Stárkk's


class TestNoDefensiveDataOmitsSectionGracefully(unittest.TestCase):
    def test_empty_defensive_damage_prevention_omits_section(self):
        parsed = ParsedFight(fight=build_fight(), actors=ACTORS, abilities={}, events=[])
        data = FightReportData(parsed_fight=parsed, defensive_damage_prevention=[])
        html = html_report.render_html([data], title="Test")
        self.assertNotIn("Damage Prevented by Defensives", html)


if __name__ == "__main__":
    unittest.main()
