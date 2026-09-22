"""
test_difficulty_in_reports.py

Covers the actual requested fix: fight difficulty (LFR/Normal/Heroic/
Mythic) must now be visible in BOTH the text/Markdown report (report.py)
and the HTML report (html_report.py), so it's never ambiguous which
difficulty a pull was.
"""
import unittest

from data_models import Actor, Fight, ParsedFight
from report import FightReportData
import report
import html_report

ACTORS = {1: Actor(id=1, name="Tankryte", type="Player", subtype="Warrior")}


def build_fight(difficulty, name="Ula'tek", kill=True):
    return Fight(
        id=1, name=name, difficulty=difficulty, kill=kill,
        start_time=0, end_time=262_000, encounter_id=3492, friendly_player_ids=[1],
    )


def build_data(difficulty, name="Ula'tek", kill=True) -> FightReportData:
    fight = build_fight(difficulty, name=name, kill=kill)
    return FightReportData(parsed_fight=ParsedFight(fight=fight, actors=ACTORS, abilities={}, events=[]))


class TestTextReportShowsDifficulty(unittest.TestCase):
    def test_heroic_appears_in_text_header(self):
        text = report.render_text(build_data(4))
        self.assertIn("Heroic", text)

    def test_mythic_appears_in_text_header(self):
        text = report.render_text(build_data(5))
        self.assertIn("Mythic", text)

    def test_normal_appears_in_text_header(self):
        text = report.render_text(build_data(3))
        self.assertIn("Normal", text)

    def test_lfr_appears_in_text_header(self):
        text = report.render_text(build_data(1))
        self.assertIn("LFR", text)

    def test_heroic_and_mythic_produce_visibly_different_headers(self):
        """The exact problem reported: it must be UNAMBIGUOUS which difficulty a pull was."""
        heroic_text = report.render_text(build_data(4))
        mythic_text = report.render_text(build_data(5))
        heroic_header = heroic_text.splitlines()[0]
        mythic_header = mythic_text.splitlines()[0]
        self.assertNotEqual(heroic_header, mythic_header)
        self.assertIn("Heroic", heroic_header)
        self.assertIn("Mythic", mythic_header)

    def test_missing_difficulty_does_not_crash_and_says_unknown(self):
        text = report.render_text(build_data(None))
        self.assertIn("Unknown Difficulty", text)

    def test_unrecognized_difficulty_id_shown_honestly_not_guessed(self):
        text = report.render_text(build_data(99))
        self.assertIn("Difficulty 99", text)


class TestMarkdownReportShowsDifficulty(unittest.TestCase):
    def test_heroic_appears_in_markdown_header(self):
        md = report.render_markdown(build_data(4))
        self.assertIn("Heroic", md.splitlines()[0])

    def test_mythic_appears_in_markdown_header(self):
        md = report.render_markdown(build_data(5))
        self.assertIn("Mythic", md.splitlines()[0])

    def test_markdown_header_starts_with_hash_and_includes_fight_name(self):
        md = report.render_markdown(build_data(5))
        first_line = md.splitlines()[0]
        self.assertTrue(first_line.startswith("# Ula'tek"))
        self.assertIn("Mythic", first_line)


class TestHtmlReportShowsDifficulty(unittest.TestCase):
    def test_heroic_badge_appears_in_html(self):
        html = html_report.render_html([build_data(4)], title="Test")
        self.assertIn("HEROIC", html)

    def test_mythic_badge_appears_in_html(self):
        html = html_report.render_html([build_data(5)], title="Test")
        self.assertIn("MYTHIC", html)

    def test_lfr_badge_appears_in_html(self):
        html = html_report.render_html([build_data(1)], title="Test")
        self.assertIn("LFR", html)

    def test_difficulty_badge_has_its_own_distinct_color_per_difficulty(self):
        heroic_html = html_report.render_html([build_data(4)], title="Test")
        mythic_html = html_report.render_html([build_data(5)], title="Test")
        # Extract the color used in each badge's inline style.
        import re
        heroic_color = re.search(r'color:(#[0-9A-Fa-f]{6});">HEROIC', heroic_html)
        mythic_color = re.search(r'color:(#[0-9A-Fa-f]{6});">MYTHIC', mythic_html)
        self.assertIsNotNone(heroic_color)
        self.assertIsNotNone(mythic_color)
        self.assertNotEqual(heroic_color.group(1), mythic_color.group(1))

    def test_difficulty_badge_appears_once_per_pull_not_once_per_boss(self):
        """
        The same boss name can appear at more than one difficulty in a
        single report (e.g. Heroic clear earlier, Mythic progression
        later) -- confirm each PULL gets its own correctly-labeled
        badge, not one shared badge for the whole boss group.
        """
        heroic_pull = build_data(4, name="Ula'tek")
        mythic_pull = build_data(5, name="Ula'tek")
        html = html_report.render_html([heroic_pull, mythic_pull], title="Test")
        self.assertIn("HEROIC", html)
        self.assertIn("MYTHIC", html)
        # Both pulls must be present under the SAME boss group (same boss name),
        # yet each shows its own correct difficulty -- proving per-pull granularity.
        self.assertEqual(html.count('data-boss="Ula&#x27;tek"'), 1)  # one boss-section, not two
        self.assertEqual(html.count("HEROIC"), 1)
        self.assertEqual(html.count("MYTHIC"), 1)

    def test_missing_difficulty_shows_unknown_not_a_crash(self):
        html = html_report.render_html([build_data(None)], title="Test")
        self.assertIn("UNKNOWN DIFFICULTY", html)

    def test_difficulty_badge_appears_before_kill_wipe_badge(self):
        """Reasonable reading order: difficulty first, then kill/wipe status."""
        html = html_report.render_html([build_data(5, kill=True)], title="Test")
        difficulty_pos = html.index("MYTHIC")
        kill_pos = html.index('class="badge kill"')
        self.assertLess(difficulty_pos, kill_pos)


if __name__ == "__main__":
    unittest.main()
