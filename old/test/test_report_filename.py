"""
test_report_filename.py

Covers report_filename.py: sanitization, the exact DDMMYYYYHHMM date/
time format (generation time, NOT fight/log time), the dash separator
between the raid name and the timestamp (per the latest requested
format), the guaranteed ".html" extension (including via the new
ensure_html_extension() safety net for a user-supplied override path),
full path construction (the "reports/" subfolder), and
ensure_reports_dir()'s directory-creation behavior.
"""
import datetime
import os
import shutil
import tempfile
import unittest

import report_filename


class TestSanitizeForFilename(unittest.TestCase):
    def test_strips_spaces(self):
        self.assertEqual(report_filename.sanitize_for_filename("The Venomous Abyss"), "TheVenomousAbyss")

    def test_strips_apostrophes(self):
        self.assertEqual(report_filename.sanitize_for_filename("Ky'veza's Lair"), "KyvezasLair")

    def test_empty_string_falls_back(self):
        self.assertEqual(report_filename.sanitize_for_filename(""), "Raid")

    def test_pure_punctuation_falls_back(self):
        self.assertEqual(report_filename.sanitize_for_filename("!!! ??? ---"), "Raid")


class TestBuildReportFilename(unittest.TestCase):
    def test_matches_the_exact_requested_example(self):
        """The precise scenario given in the latest request: 'The Venoumous Abyss' generated 20 Sep 2026 11:47 -> TheVenoumousAbyss-200920261147.html."""
        generated_at = datetime.datetime(2026, 9, 20, 11, 47)
        result = report_filename.build_report_filename("The Venomous Abyss", generated_at=generated_at)
        self.assertEqual(result, "TheVenomousAbyss-200920261147.html")

    def test_dash_separator_present_between_name_and_timestamp(self):
        generated_at = datetime.datetime(2026, 9, 20, 11, 47)
        result = report_filename.build_report_filename("Ulatek", generated_at=generated_at)
        self.assertEqual(result, "Ulatek-200920261147.html")
        # Confirm there is EXACTLY one dash, right before the 12-digit timestamp.
        name_part, _, rest = result.partition("-")
        self.assertEqual(name_part, "Ulatek")
        self.assertEqual(rest, "200920261147.html")

    def test_date_format_is_day_month_year_no_separators_zero_padded(self):
        generated_at = datetime.datetime(2027, 1, 5, 9, 3)
        result = report_filename.build_report_filename("Ulatek", generated_at=generated_at)
        self.assertEqual(result, "Ulatek-050120270903.html")

    def test_always_ends_with_html_extension(self):
        result = report_filename.build_report_filename("Ulatek", generated_at=datetime.datetime(2026, 1, 1, 0, 0))
        self.assertTrue(result.endswith(".html"))

    def test_uses_now_when_generated_at_omitted(self):
        before = datetime.datetime.now()
        result = report_filename.build_report_filename("Ulatek")
        after = datetime.datetime.now()
        timestamp_str = result[len("Ulatek-"):-len(".html")]
        parsed = datetime.datetime.strptime(timestamp_str, "%d%m%Y%H%M")
        self.assertGreaterEqual(parsed, before.replace(second=0, microsecond=0) - datetime.timedelta(minutes=1))
        self.assertLessEqual(parsed, after.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1))


class TestBuildReportPath(unittest.TestCase):
    def test_default_directory_is_reports(self):
        path = report_filename.build_report_path("Ulatek", generated_at=datetime.datetime(2026, 9, 20, 11, 47))
        expected = os.path.join("reports", "Ulatek-200920261147.html")
        self.assertEqual(path, expected)

    def test_matches_full_requested_example_end_to_end(self):
        path = report_filename.build_report_path("The Venomous Abyss", generated_at=datetime.datetime(2026, 9, 20, 11, 47))
        expected = os.path.join("reports", "TheVenomousAbyss-200920261147.html")
        self.assertEqual(path, expected)


class TestEnsureHtmlExtension(unittest.TestCase):
    def test_adds_extension_when_missing(self):
        self.assertEqual(report_filename.ensure_html_extension("my_report"), "my_report.html")

    def test_leaves_already_correct_extension_unchanged(self):
        self.assertEqual(report_filename.ensure_html_extension("my_report.html"), "my_report.html")

    def test_case_insensitive_check_does_not_double_up(self):
        self.assertEqual(report_filename.ensure_html_extension("my_report.HTML"), "my_report.HTML")

    def test_wrong_extension_gets_html_appended_rather_than_replaced(self):
        # Deliberately conservative: we only GUARANTEE it ends in .html,
        # we don't try to strip/guess a different existing extension.
        self.assertEqual(report_filename.ensure_html_extension("my_report.txt"), "my_report.txt.html")


class TestEnsureReportsDir(unittest.TestCase):
    def setUp(self):
        self.tmp_root = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.tmp_root)

    def tearDown(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_creates_directory_if_missing(self):
        self.assertFalse(os.path.isdir("reports"))
        report_filename.ensure_reports_dir()
        self.assertTrue(os.path.isdir("reports"))

    def test_is_idempotent_if_directory_already_exists(self):
        report_filename.ensure_reports_dir()
        report_filename.ensure_reports_dir()
        self.assertTrue(os.path.isdir("reports"))


if __name__ == "__main__":
    unittest.main()
