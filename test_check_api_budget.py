"""
test_check_api_budget.py

Covers wcl_api.RateLimitInfo's derived properties (points_remaining,
percent_remaining, has_data), WCLClient.get_rate_limit_status()'s
network interaction (mocked -- no real API calls), and
check_api_budget.py's CLI output in both human-readable and --json
modes, including the low/critical budget warning thresholds.
"""
import contextlib
import io
import json
import unittest
from unittest.mock import MagicMock, patch

from wcl_api import RateLimitInfo, WCLAPIError, WCLClient
import check_api_budget


# ---------------------------------------------------------------------
# RateLimitInfo derived properties
# ---------------------------------------------------------------------
class TestRateLimitInfoProperties(unittest.TestCase):
    def test_has_data_false_before_any_update(self):
        info = RateLimitInfo()
        self.assertFalse(info.has_data)

    def test_has_data_true_after_update(self):
        info = RateLimitInfo()
        info.update_from_ratelimit_data({"pointsSpentThisHour": 100, "limitPerHour": 3600, "pointsResetIn": 1200})
        self.assertTrue(info.has_data)

    def test_points_remaining_basic(self):
        info = RateLimitInfo()
        info.update_from_ratelimit_data({"pointsSpentThisHour": 100, "limitPerHour": 3600, "pointsResetIn": 1200})
        self.assertEqual(info.points_remaining, 3500)

    def test_points_remaining_none_before_data(self):
        info = RateLimitInfo()
        self.assertIsNone(info.points_remaining)

    def test_points_remaining_never_negative_even_if_overspent(self):
        """Guard against a hypothetical overspend (e.g. concurrent clients) producing a negative 'remaining'."""
        info = RateLimitInfo()
        info.update_from_ratelimit_data({"pointsSpentThisHour": 4000, "limitPerHour": 3600, "pointsResetIn": 60})
        self.assertEqual(info.points_remaining, 0.0)

    def test_percent_remaining_basic(self):
        info = RateLimitInfo()
        info.update_from_ratelimit_data({"pointsSpentThisHour": 900, "limitPerHour": 3600, "pointsResetIn": 1200})
        self.assertEqual(info.percent_remaining, 75.0)

    def test_percent_remaining_zero_limit_does_not_divide_by_zero(self):
        info = RateLimitInfo()
        info.update_from_ratelimit_data({"pointsSpentThisHour": 0, "limitPerHour": 0, "pointsResetIn": 0})
        self.assertIsNone(info.percent_remaining)

    def test_percent_remaining_full_budget(self):
        info = RateLimitInfo()
        info.update_from_ratelimit_data({"pointsSpentThisHour": 0, "limitPerHour": 3600, "pointsResetIn": 3600})
        self.assertEqual(info.percent_remaining, 100.0)

    def test_percent_remaining_none_before_data(self):
        info = RateLimitInfo()
        self.assertIsNone(info.percent_remaining)


# ---------------------------------------------------------------------
# WCLClient.get_rate_limit_status() -- mocked network, no real calls
# ---------------------------------------------------------------------
class TestGetRateLimitStatus(unittest.TestCase):
    def _client_with_mocked_graphql(self, rate_limit_payload: dict) -> WCLClient:
        client = WCLClient("fake_id", "fake_secret")
        client._graphql = MagicMock(return_value={"rateLimitData": rate_limit_payload})
        # _graphql normally updates client.rate_limit itself (see real
        # implementation) -- replicate that here since we've replaced
        # the whole method with a mock that skips the real body.
        client.rate_limit.update_from_ratelimit_data(rate_limit_payload)
        return client

    def test_returns_updated_rate_limit_info(self):
        client = self._client_with_mocked_graphql({"pointsSpentThisHour": 500, "limitPerHour": 3600, "pointsResetIn": 900})
        result = client.get_rate_limit_status()
        self.assertEqual(result.points_spent_this_hour, 500)
        self.assertEqual(result.limit_per_hour, 3600)
        self.assertEqual(result.points_reset_in_seconds, 900)

    def test_query_requests_no_report_data(self):
        """Confirm the query string itself never references reportData -- this must stay a report-free, low-cost query."""
        client = WCLClient("fake_id", "fake_secret")
        captured_query = {}

        def fake_graphql(query, variables):
            captured_query["query"] = query
            captured_query["variables"] = variables
            client.rate_limit.update_from_ratelimit_data({"pointsSpentThisHour": 1, "limitPerHour": 3600, "pointsResetIn": 3599})
            return {"rateLimitData": {"pointsSpentThisHour": 1, "limitPerHour": 3600, "pointsResetIn": 3599}}

        client._graphql = fake_graphql
        client.get_rate_limit_status()
        self.assertNotIn("reportData", captured_query["query"])
        self.assertEqual(captured_query["variables"], {})


# ---------------------------------------------------------------------
# check_api_budget.py CLI output
# ---------------------------------------------------------------------
def run_cli(argv, rate_limit_data):
    """Run check_api_budget.main() with credentials + the API call both mocked."""
    captured = io.StringIO()
    with patch("check_api_budget.config.get_credentials", return_value=("id", "secret")), \
         patch("check_api_budget.WCLClient") as client_cls, \
         patch("sys.argv", ["check_api_budget.py", *argv]), \
         contextlib.redirect_stdout(captured):
        client_instance = client_cls.return_value
        rate_limit = RateLimitInfo()
        rate_limit.update_from_ratelimit_data(rate_limit_data)
        client_instance.get_rate_limit_status.return_value = rate_limit
        check_api_budget.main()
    return captured.getvalue()


class TestCLIHumanReadableOutput(unittest.TestCase):
    def test_normal_budget_shows_no_warning(self):
        output = run_cli([], {"pointsSpentThisHour": 500, "limitPerHour": 3600, "pointsResetIn": 1800})
        self.assertIn("Points spent this hour:", output)
        self.assertIn("500.00", output)
        self.assertIn("3,600.00", output)
        self.assertNotIn("WARNING", output)
        self.assertNotIn("OUT of API budget", output)

    def test_low_budget_shows_note(self):
        # 20% remaining -- should hit the <25% "consider pacing" note, not the <10% warning
        output = run_cli([], {"pointsSpentThisHour": 2880, "limitPerHour": 3600, "pointsResetIn": 300})
        self.assertIn("less than 25%", output)
        self.assertNotIn("WARNING", output)

    def test_critical_budget_shows_warning(self):
        # 5% remaining -- should hit the <10% WARNING
        output = run_cli([], {"pointsSpentThisHour": 3420, "limitPerHour": 3600, "pointsResetIn": 60})
        self.assertIn("WARNING", output)
        self.assertIn("less than 10%", output)

    def test_zero_remaining_shows_out_of_budget_message(self):
        output = run_cli([], {"pointsSpentThisHour": 3600, "limitPerHour": 3600, "pointsResetIn": 30})
        self.assertIn("OUT of API budget", output)

    def test_reset_time_formatted_as_minutes(self):
        output = run_cli([], {"pointsSpentThisHour": 100, "limitPerHour": 3600, "pointsResetIn": 1800})
        self.assertIn("30.0 min", output)

    def test_reset_time_under_a_minute_formatted_as_seconds(self):
        output = run_cli([], {"pointsSpentThisHour": 100, "limitPerHour": 3600, "pointsResetIn": 45})
        self.assertIn("45s", output)

    def test_missing_credentials_exits_cleanly(self):
        with patch("check_api_budget.config.get_credentials", side_effect=RuntimeError("no creds configured")), \
             patch("sys.argv", ["check_api_budget.py"]):
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured), self.assertRaises(SystemExit) as ctx:
                check_api_budget.main()
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("no creds configured", captured.getvalue())

    def test_api_error_exits_cleanly(self):
        with patch("check_api_budget.config.get_credentials", return_value=("id", "secret")), \
             patch("check_api_budget.WCLClient") as client_cls, \
             patch("sys.argv", ["check_api_budget.py"]):
            client_cls.return_value.get_rate_limit_status.side_effect = WCLAPIError("network exploded")
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured), self.assertRaises(SystemExit) as ctx:
                check_api_budget.main()
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("network exploded", captured.getvalue())


class TestCLIJsonOutput(unittest.TestCase):
    def test_json_output_is_valid_and_complete(self):
        output = run_cli(["--json"], {"pointsSpentThisHour": 500, "limitPerHour": 3600, "pointsResetIn": 1800})
        parsed = json.loads(output)
        self.assertEqual(parsed["points_spent_this_hour"], 500)
        self.assertEqual(parsed["limit_per_hour"], 3600)
        self.assertEqual(parsed["points_remaining"], 3100)
        self.assertAlmostEqual(parsed["percent_remaining"], 86.111, places=2)
        self.assertEqual(parsed["points_reset_in_seconds"], 1800)

    def test_json_output_never_prints_human_readable_text(self):
        output = run_cli(["--json"], {"pointsSpentThisHour": 3600, "limitPerHour": 3600, "pointsResetIn": 10})
        self.assertNotIn("OUT of API budget", output)  # JSON mode is machine-readable only
        json.loads(output)  # must still parse cleanly as pure JSON


class TestFormatSecondsAsMinutes(unittest.TestCase):
    def test_none_returns_unknown(self):
        self.assertEqual(check_api_budget.format_seconds_as_minutes(None), "unknown")

    def test_zero_seconds(self):
        self.assertEqual(check_api_budget.format_seconds_as_minutes(0), "0s")

    def test_exactly_one_minute(self):
        self.assertEqual(check_api_budget.format_seconds_as_minutes(60), "1.0 min")

    def test_fractional_minutes(self):
        self.assertEqual(check_api_budget.format_seconds_as_minutes(90), "1.5 min")


if __name__ == "__main__":
    unittest.main()
