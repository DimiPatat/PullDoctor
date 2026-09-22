"""
test_latest_guild_report.py

Covers find_latest_guild_report()'s core correctness guarantee: it
must return the TRUE most-recent report regardless of which direction
the underlying API happens to sort results in (ascending OR
descending by startTime) -- since that ordering isn't documented by
Warcraft Logs and must not be assumed. Every scenario below is tested
with BOTH a plausible ascending and a plausible descending page
layout, to prove the "compare page 1 + last page" strategy is robust
either way.
"""
import unittest
from unittest.mock import MagicMock

from latest_guild_report import (
    NoGuildReportsFoundError,
    find_latest_guild_report,
    find_latest_guild_report_code,
)


def make_report(code, start_time, title="Some Raid Night"):
    return {"code": code, "title": title, "startTime": start_time, "endTime": start_time + 10000, "zone": {"name": "Test Zone"}}


class TestSinglePage(unittest.TestCase):
    """All reports fit on one page -- no second fetch should even be attempted."""

    def test_finds_max_start_time_among_single_page(self):
        client = MagicMock()
        client.get_guild_reports_page.return_value = {
            "data": [make_report("AAA", 1000), make_report("BBB", 5000), make_report("CCC", 3000)],
            "total": 3, "per_page": 25, "current_page": 1, "last_page": 1,
        }
        result = find_latest_guild_report(client, "MyGuild", "silvermoon", "eu")
        self.assertEqual(result.code, "BBB")
        client.get_guild_reports_page.assert_called_once()  # only ONE API call needed

    def test_single_report_total(self):
        client = MagicMock()
        client.get_guild_reports_page.return_value = {
            "data": [make_report("ONLY", 1234)],
            "total": 1, "per_page": 25, "current_page": 1, "last_page": 1,
        }
        result = find_latest_guild_report(client, "MyGuild", "silvermoon", "eu")
        self.assertEqual(result.code, "ONLY")


class TestMultiPageAscendingOrder(unittest.TestCase):
    """
    Simulates the API sorting OLDEST first -- meaning the true most-
    recent report would be on the LAST page, not page 1.
    """

    def _mock_client(self):
        client = MagicMock()

        def side_effect(guild_name, slug, region, page, limit, start_time=None):
            if page == 1:
                return {
                    "data": [make_report("OLD1", 1000), make_report("OLD2", 2000)],
                    "total": 4, "per_page": 2, "current_page": 1, "last_page": 2,
                }
            elif page == 2:
                return {
                    "data": [make_report("OLD3", 3000), make_report("NEWEST", 9999)],
                    "total": 4, "per_page": 2, "current_page": 2, "last_page": 2,
                }
            raise AssertionError(f"Unexpected page requested: {page}")

        client.get_guild_reports_page.side_effect = side_effect
        return client

    def test_finds_newest_report_on_last_page(self):
        client = self._mock_client()
        result = find_latest_guild_report(client, "MyGuild", "silvermoon", "eu", limit=2)
        self.assertEqual(result.code, "NEWEST")

    def test_only_fetches_two_pages_not_all(self):
        """Fast-path efficiency: must fetch page 1 + last page ONLY, not walk every page in between."""
        client = self._mock_client()
        find_latest_guild_report(client, "MyGuild", "silvermoon", "eu", limit=2)
        self.assertEqual(client.get_guild_reports_page.call_count, 2)


class TestMultiPageDescendingOrder(unittest.TestCase):
    """
    Simulates the API sorting NEWEST first -- meaning the true most-
    recent report would be on PAGE 1, not the last page. This is the
    "opposite assumption" scenario -- both must work without changing
    any code, proving the algorithm doesn't secretly favor one order.
    """

    def _mock_client(self):
        client = MagicMock()

        def side_effect(guild_name, slug, region, page, limit, start_time=None):
            if page == 1:
                return {
                    "data": [make_report("NEWEST", 9999), make_report("NEW2", 8000)],
                    "total": 4, "per_page": 2, "current_page": 1, "last_page": 2,
                }
            elif page == 2:
                return {
                    "data": [make_report("OLD1", 2000), make_report("OLD2", 1000)],
                    "total": 4, "per_page": 2, "current_page": 2, "last_page": 2,
                }
            raise AssertionError(f"Unexpected page requested: {page}")

        client.get_guild_reports_page.side_effect = side_effect
        return client

    def test_finds_newest_report_on_first_page(self):
        client = self._mock_client()
        result = find_latest_guild_report(client, "MyGuild", "silvermoon", "eu", limit=2)
        self.assertEqual(result.code, "NEWEST")


class TestDeepScan(unittest.TestCase):
    """deep_scan=True must walk EVERY page, for guaranteed correctness even with 3+ pages."""

    def test_deep_scan_walks_all_pages_and_finds_true_max(self):
        client = MagicMock()

        def side_effect(guild_name, slug, region, page, limit, start_time=None):
            pages = {
                1: {"data": [make_report("A", 1000)], "total": 3, "per_page": 1, "current_page": 1, "last_page": 3},
                2: {"data": [make_report("B", 9999)], "total": 3, "per_page": 1, "current_page": 2, "last_page": 3},  # true newest, buried in the MIDDLE page
                3: {"data": [make_report("C", 5000)], "total": 3, "per_page": 1, "current_page": 3, "last_page": 3},
            }
            return pages[page]

        client.get_guild_reports_page.side_effect = side_effect
        result = find_latest_guild_report(client, "MyGuild", "silvermoon", "eu", limit=1, deep_scan=True)
        self.assertEqual(result.code, "B")
        self.assertEqual(client.get_guild_reports_page.call_count, 3)

    def test_fast_path_would_miss_a_middle_page_max_but_deep_scan_catches_it(self):
        """
        Sanity check on the test design itself: confirm the FAST path
        (page 1 + last page only) genuinely WOULD miss "B" in this
        scenario (since it's buried on page 2, neither first nor
        last) -- proving deep_scan actually matters for this case,
        rather than deep_scan and fast-path coincidentally agreeing.
        """
        client = MagicMock()

        def side_effect(guild_name, slug, region, page, limit, start_time=None):
            pages = {
                1: {"data": [make_report("A", 1000)], "total": 3, "per_page": 1, "current_page": 1, "last_page": 3},
                2: {"data": [make_report("B", 9999)], "total": 3, "per_page": 1, "current_page": 2, "last_page": 3},
                3: {"data": [make_report("C", 5000)], "total": 3, "per_page": 1, "current_page": 3, "last_page": 3},
            }
            return pages[page]

        client.get_guild_reports_page.side_effect = side_effect
        fast_result = find_latest_guild_report(client, "MyGuild", "silvermoon", "eu", limit=1, deep_scan=False)
        self.assertNotEqual(fast_result.code, "B")  # confirms this scenario really does defeat the fast path
        self.assertEqual(fast_result.code, "C")  # fast path picks the (wrong) max of page 1 + page 3 only


class TestEmptyGuild(unittest.TestCase):
    def test_zero_reports_raises_specific_exception(self):
        client = MagicMock()
        client.get_guild_reports_page.return_value = {
            "data": [], "total": 0, "per_page": 25, "current_page": 1, "last_page": 1,
        }
        with self.assertRaises(NoGuildReportsFoundError):
            find_latest_guild_report(client, "EmptyGuild", "silvermoon", "eu")


class TestStartTimeFilterPassthrough(unittest.TestCase):
    """start_time must be forwarded to EVERY get_guild_reports_page() call, not just the first."""

    def test_start_time_passed_to_single_page_call(self):
        client = MagicMock()
        client.get_guild_reports_page.return_value = {
            "data": [make_report("A", 1000)], "total": 1, "per_page": 25, "current_page": 1, "last_page": 1,
        }
        find_latest_guild_report(client, "MyGuild", "silvermoon", "eu", start_time=123456.0)
        _, kwargs = client.get_guild_reports_page.call_args
        self.assertEqual(kwargs.get("start_time"), 123456.0)

    def test_start_time_passed_to_deep_scan_follow_up_pages(self):
        client = MagicMock()

        def side_effect(guild_name, slug, region, page, limit, start_time=None):
            self.assertEqual(start_time, 999.0)  # every single call must receive it
            pages = {
                1: {"data": [make_report("A", 1000)], "total": 2, "per_page": 1, "current_page": 1, "last_page": 2},
                2: {"data": [make_report("B", 2000)], "total": 2, "per_page": 1, "current_page": 2, "last_page": 2},
            }
            return pages[page]

        client.get_guild_reports_page.side_effect = side_effect
        result = find_latest_guild_report(client, "MyGuild", "silvermoon", "eu", limit=1, deep_scan=True, start_time=999.0)
        self.assertEqual(result.code, "B")


class TestConvenienceWrapper(unittest.TestCase):
    def test_find_latest_guild_report_code_returns_just_the_code_string(self):
        client = MagicMock()
        client.get_guild_reports_page.return_value = {
            "data": [make_report("XYZ123", 5000)], "total": 1, "per_page": 25, "current_page": 1, "last_page": 1,
        }
        code = find_latest_guild_report_code(client, "MyGuild", "silvermoon", "eu")
        self.assertEqual(code, "XYZ123")
        self.assertIsInstance(code, str)


if __name__ == "__main__":
    unittest.main()
