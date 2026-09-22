"""
test_docs_index.py

Covers docs_index.py: parsing report_filename.py-style filenames,
sorting by PARSED TIMESTAMP (not filename string order -- the key
correctness requirement, since raid names have different lengths), and
graceful handling of stray/malformed files.
"""
import datetime
import os
import shutil
import tempfile
import unittest

import docs_index


class TestParseReportFilename(unittest.TestCase):
    def test_parses_valid_filename(self):
        result = docs_index.parse_report_filename("TheVenomousAbyss-200920261147.html")
        self.assertIsNotNone(result)
        self.assertEqual(result.raid_name, "TheVenomousAbyss")
        self.assertEqual(result.generated_at, datetime.datetime(2026, 9, 20, 11, 47))

    def test_returns_none_for_unrelated_file(self):
        self.assertIsNone(docs_index.parse_report_filename("readme.html"))

    def test_returns_none_for_impossible_date(self):
        # month=13 doesn't exist -- must not raise, must return None.
        self.assertIsNone(docs_index.parse_report_filename("Test-999913001200.html"))

    def test_returns_none_for_non_html_extension(self):
        self.assertIsNone(docs_index.parse_report_filename("TheVenomousAbyss-200920261147.txt"))

    def test_short_raid_name(self):
        result = docs_index.parse_report_filename("Ulatek-190920261639.html")
        self.assertEqual(result.raid_name, "Ulatek")


class TestScanReportsDirectorySortsByParsedTimestampNotFilenameText(unittest.TestCase):
    """
    THE key correctness requirement: string-sorting filenames would be
    WRONG here, because raid names have different lengths/characters,
    so alphabetical order has no relationship to chronological order.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _touch(self, filename):
        open(os.path.join(self.tmp_dir, filename), "w").close()

    def test_sorted_newest_first_across_different_raid_names(self):
        # Deliberately chosen so filename STRING order would be totally different
        # from chronological order: "Aaa..." sorts first alphabetically but is
        # actually the OLDEST; "Zzz..." sorts last alphabetically but is NEWEST.
        self._touch("Zzznewestraid-010120260900.html")   # 1 Jan 2026 -- actually oldest chronologically
        self._touch("Aaaoldraid-200920261200.html")       # 20 Sep 2026 -- actually newest chronologically
        self._touch("MiddleRaid-150520261000.html")       # 15 May 2026 -- actually in between

        reports = docs_index.scan_reports_directory(self.tmp_dir)
        ordered_names = [r.raid_name for r in reports]
        self.assertEqual(ordered_names, ["Aaaoldraid", "MiddleRaid", "Zzznewestraid"])

    def test_stray_non_matching_html_file_is_silently_excluded(self):
        self._touch("TheVenomousAbyss-200920261147.html")
        self._touch("some_other_page.html")  # not a report this project generated
        reports = docs_index.scan_reports_directory(self.tmp_dir)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].raid_name, "TheVenomousAbyss")

    def test_non_html_files_ignored(self):
        self._touch("TheVenomousAbyss-200920261147.html")
        self._touch("notes.txt")
        reports = docs_index.scan_reports_directory(self.tmp_dir)
        self.assertEqual(len(reports), 1)

    def test_missing_directory_returns_empty_list_not_an_error(self):
        reports = docs_index.scan_reports_directory(os.path.join(self.tmp_dir, "does_not_exist"))
        self.assertEqual(reports, [])

    def test_empty_directory_returns_empty_list(self):
        reports = docs_index.scan_reports_directory(self.tmp_dir)
        self.assertEqual(reports, [])


class TestRenderIndexHtml(unittest.TestCase):
    def test_empty_list_shows_friendly_message(self):
        html = docs_index.render_index_html([])
        self.assertIn("No reports have been generated yet", html)

    def test_reports_rendered_as_links_with_correct_href(self):
        reports = [docs_index.IndexedReport(filename="Ulatek-190920261639.html", raid_name="Ulatek", generated_at=datetime.datetime(2026, 9, 19, 16, 39))]
        html = docs_index.render_index_html(reports, reports_subfolder_name="reports")
        self.assertIn('href="reports/Ulatek-190920261639.html"', html)
        self.assertIn("Ulatek", html)

    def test_custom_subfolder_name_respected(self):
        reports = [docs_index.IndexedReport(filename="Ulatek-190920261639.html", raid_name="Ulatek", generated_at=datetime.datetime(2026, 9, 19, 16, 39))]
        html = docs_index.render_index_html(reports, reports_subfolder_name="archive")
        self.assertIn('href="archive/Ulatek-190920261639.html"', html)

    def test_html_escaping_applied_to_raid_name(self):
        reports = [docs_index.IndexedReport(filename="A&B-190920261639.html", raid_name="A&B", generated_at=datetime.datetime(2026, 9, 19, 16, 39))]
        html = docs_index.render_index_html(reports)
        self.assertNotIn(">A&B<", html)  # must be escaped, e.g. A&amp;B
        self.assertIn("A&amp;B", html)


class TestBuildIndex(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.reports_dir = os.path.join(self.tmp_dir, "docs", "reports")
        os.makedirs(self.reports_dir)
        self.index_path = os.path.join(self.tmp_dir, "docs", "index.html")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_writes_index_file_and_returns_reports(self):
        open(os.path.join(self.reports_dir, "Ulatek-190920261639.html"), "w").close()
        reports = docs_index.build_index(self.reports_dir, self.index_path)
        self.assertEqual(len(reports), 1)
        self.assertTrue(os.path.isfile(self.index_path))
        with open(self.index_path) as f:
            content = f.read()
        self.assertIn("Ulatek", content)
        self.assertIn('href="reports/Ulatek-190920261639.html"', content)


if __name__ == "__main__":
    unittest.main()
