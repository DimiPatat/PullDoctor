"""test_class_colors.py -- covers normalization of both WCL class-name formats and correct color lookups for all 13 classes."""
import unittest

import class_colors


class TestNormalizeClassName(unittest.TestCase):
    def test_no_space_death_knight(self):
        self.assertEqual(class_colors.normalize_class_name("DeathKnight"), "Death Knight")

    def test_no_space_demon_hunter(self):
        self.assertEqual(class_colors.normalize_class_name("DemonHunter"), "Demon Hunter")

    def test_already_spaced_death_knight_unchanged(self):
        self.assertEqual(class_colors.normalize_class_name("Death Knight"), "Death Knight")

    def test_already_spaced_demon_hunter_unchanged(self):
        self.assertEqual(class_colors.normalize_class_name("Demon Hunter"), "Demon Hunter")

    def test_single_word_classes_unchanged(self):
        for name in ["Warrior", "Priest", "Mage", "Druid", "Hunter", "Rogue", "Shaman", "Warlock", "Paladin", "Monk", "Evoker"]:
            self.assertEqual(class_colors.normalize_class_name(name), name)

    def test_none_input(self):
        self.assertIsNone(class_colors.normalize_class_name(None))

    def test_empty_string_input(self):
        self.assertIsNone(class_colors.normalize_class_name(""))


class TestGetClassColor(unittest.TestCase):
    def test_all_thirteen_classes_have_a_color(self):
        self.assertEqual(len(class_colors.CLASS_COLORS), 13)

    def test_death_knight_no_space_form(self):
        self.assertEqual(class_colors.get_class_color("DeathKnight"), "#C41E3A")

    def test_death_knight_spaced_form(self):
        self.assertEqual(class_colors.get_class_color("Death Knight"), "#C41E3A")

    def test_demon_hunter_no_space_form(self):
        self.assertEqual(class_colors.get_class_color("DemonHunter"), "#A330C9")

    def test_demon_hunter_spaced_form(self):
        self.assertEqual(class_colors.get_class_color("Demon Hunter"), "#A330C9")

    def test_single_word_class(self):
        self.assertEqual(class_colors.get_class_color("Warrior"), "#C69B6D")
        self.assertEqual(class_colors.get_class_color("Mage"), "#3FC7EB")
        self.assertEqual(class_colors.get_class_color("Priest"), "#FFFFFF")

    def test_unknown_class_falls_back_to_default(self):
        self.assertEqual(class_colors.get_class_color("SomeBrandNewClass"), class_colors.DEFAULT_COLOR)

    def test_none_falls_back_to_default(self):
        self.assertEqual(class_colors.get_class_color(None), class_colors.DEFAULT_COLOR)

    def test_empty_string_falls_back_to_default(self):
        self.assertEqual(class_colors.get_class_color(""), class_colors.DEFAULT_COLOR)

    def test_every_color_is_a_valid_hex_string(self):
        import re
        hex_pattern = re.compile(r"^#[0-9A-Fa-f]{6}$")
        for name, color in class_colors.CLASS_COLORS.items():
            self.assertRegex(color, hex_pattern, f"{name}'s color {color!r} is not a valid hex code")


if __name__ == "__main__":
    unittest.main()
