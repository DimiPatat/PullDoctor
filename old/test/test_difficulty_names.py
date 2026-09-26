"""test_difficulty_names.py -- covers the WCL difficulty ID -> human-readable name/color mapping."""
import unittest

import difficulty_names


class TestDifficultyName(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(difficulty_names.difficulty_name(3), "Normal")

    def test_heroic(self):
        self.assertEqual(difficulty_names.difficulty_name(4), "Heroic")

    def test_mythic(self):
        self.assertEqual(difficulty_names.difficulty_name(5), "Mythic")

    def test_lfr(self):
        self.assertEqual(difficulty_names.difficulty_name(1), "LFR")

    def test_none_gives_unknown_difficulty_not_a_crash(self):
        self.assertEqual(difficulty_names.difficulty_name(None), "Unknown Difficulty")

    def test_unrecognized_numeric_id_falls_back_safely(self):
        """An unrecognized ID (e.g. a Mythic+ dungeon difficulty) must never be silently mislabeled as a raid difficulty."""
        self.assertEqual(difficulty_names.difficulty_name(99), "Difficulty 99")

    def test_missing_vs_unrecognized_are_distinguishable(self):
        """None (missing) and an unrecognized number must produce visibly DIFFERENT strings, not the same fallback."""
        self.assertNotEqual(difficulty_names.difficulty_name(None), difficulty_names.difficulty_name(99))

    def test_zero_is_not_silently_treated_as_missing(self):
        self.assertEqual(difficulty_names.difficulty_name(0), "Difficulty 0")


class TestDifficultyColor(unittest.TestCase):
    def test_every_named_difficulty_has_a_color(self):
        for difficulty_id in difficulty_names.DIFFICULTY_NAMES:
            self.assertIn(difficulty_id, difficulty_names.DIFFICULTY_COLORS)

    def test_color_is_valid_hex(self):
        import re
        hex_pattern = re.compile(r"^#[0-9A-Fa-f]{6}$")
        for difficulty_id, color in difficulty_names.DIFFICULTY_COLORS.items():
            self.assertRegex(color, hex_pattern)

    def test_unrecognized_id_falls_back_to_default_color(self):
        self.assertEqual(difficulty_names.difficulty_color(99), difficulty_names.DEFAULT_COLOR)

    def test_none_falls_back_to_default_color(self):
        self.assertEqual(difficulty_names.difficulty_color(None), difficulty_names.DEFAULT_COLOR)

    def test_heroic_and_mythic_have_distinct_colors(self):
        self.assertNotEqual(difficulty_names.difficulty_color(4), difficulty_names.difficulty_color(5))


if __name__ == "__main__":
    unittest.main()
