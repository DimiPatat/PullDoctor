"""
test_html_report.py

Covers the meter-bar tweaks requested for html_report.py:
  - Player name text color in Damage Done/Healing/Damage Taken meter
    rows uses the page's own background color (_BG, "#14151a"),
    not the class color.
  - A light text-shadow halo is present so the name stays legible even
    when a low performer's bar is narrow and most of the name text
    sits directly on the (also-dark) row background -- a real
    readability bug caught and fixed via live browser screenshot
    testing before this was shipped.
  - Bar width math (100% for the top row, proportional for the rest)
    still works correctly with the new text-color/shadow styling.
"""
import re
import unittest

from data_models import Actor, Fight, ParsedFight
from damage_done_analyzer import DamageDoneSummary
from report import FightReportData
import html_report


def build_fight():
    return Fight(id=1, name="Ula'tek", difficulty=5, kill=True, start_time=0, end_time=262_000, encounter_id=3492, friendly_player_ids=[1, 2])


ACTORS = {
    1: Actor(id=1, name="Tankryte", type="Player", subtype="Warrior"),
    2: Actor(id=2, name="Illidarw", type="Player", subtype="DemonHunter"),
}


class TestMeterNameTextColor(unittest.TestCase):
    def test_meter_name_color_is_page_background_not_class_color(self):
        data = FightReportData(
            parsed_fight=ParsedFight(fight=build_fight(), actors=ACTORS, abilities={}, events=[]),
            damage_done_summaries=[DamageDoneSummary(player_id=1, player_name="Tankryte", total_damage_done=100)],
        )
        html = html_report.render_html([data], title="Test")
        self.assertIn(f"color: {html_report._BG}", html)

    def test_bg_constant_matches_expected_value(self):
        self.assertEqual(html_report._BG, "#14151a")

    def test_meter_name_has_a_light_halo_for_narrow_bar_legibility(self):
        """
        Regression test for a real bug caught during manual review: a
        low performer's narrow bar leaves most of their name sitting on
        the dark row background, which -- with dark-on-page-bg text --
        was nearly unreadable without this halo.
        """
        data = FightReportData(
            parsed_fight=ParsedFight(fight=build_fight(), actors=ACTORS, abilities={}, events=[]),
            damage_done_summaries=[
                DamageDoneSummary(player_id=1, player_name="Tankryte", total_damage_done=100),
                DamageDoneSummary(player_id=2, player_name="Illidarw", total_damage_done=5),  # a narrow, 5% bar
            ],
        )
        html = html_report.render_html([data], title="Test")
        self.assertIn("text-shadow", html)
        self.assertIn("rgba(255,255,255", html)  # a LIGHT halo, appropriate for dark-colored text

    def test_no_per_row_inline_color_style_on_meter_name_span(self):
        """The name's color now comes from the shared .meter-name CSS rule, not a per-row inline style (unlike the old class-colored-text version)."""
        data = FightReportData(
            parsed_fight=ParsedFight(fight=build_fight(), actors=ACTORS, abilities={}, events=[]),
            damage_done_summaries=[DamageDoneSummary(player_id=1, player_name="Tankryte", total_damage_done=100)],
        )
        html = html_report.render_html([data], title="Test")
        # The bar itself still gets an inline background-color (that part is unchanged);
        # confirm the NAME span itself has no inline color style anymore.
        match = re.search(r'<span class="meter-name">Tankryte</span>', html)
        self.assertIsNotNone(match, "meter-name span should have no inline style now that color is a shared CSS rule")


class TestBarWidthMathUnaffected(unittest.TestCase):
    def test_top_row_still_100_percent_and_relative_scaling_still_correct(self):
        data = FightReportData(
            parsed_fight=ParsedFight(fight=build_fight(), actors=ACTORS, abilities={}, events=[]),
            damage_done_summaries=[
                DamageDoneSummary(player_id=1, player_name="Tankryte", total_damage_done=100),
                DamageDoneSummary(player_id=2, player_name="Illidarw", total_damage_done=33),
            ],
        )
        html = html_report.render_html([data], title="Test")
        top_match = re.search(r'width:([\d.]+)%;background-color:[^"]*"></div><span class="meter-name">Tankryte</span>', html)
        second_match = re.search(r'width:([\d.]+)%;background-color:[^"]*"></div><span class="meter-name">Illidarw</span>', html)
        self.assertIsNotNone(top_match)
        self.assertIsNotNone(second_match)
        self.assertEqual(top_match.group(1), "100.0")
        self.assertEqual(second_match.group(1), "33.0")


if __name__ == "__main__":
    unittest.main()
