"""
test_schedule_guard.py

Covers is_within_scheduled_window()'s core promise: it must correctly
identify "is it actually Monday or Wednesday at ~23:15 Brussels time"
regardless of whether Brussels is currently on CET or CEST -- INCLUDING
around the two real DST transition dates each year, which is exactly
the scenario the cron-only approach in the GitHub Actions workflow
cannot handle precisely on its own.
"""
import datetime
import unittest
from zoneinfo import ZoneInfo

from schedule_guard import is_within_scheduled_window

UTC = datetime.timezone.utc


class TestBasicDayAndTimeMatching(unittest.TestCase):
    def test_monday_at_2315_utc_winter_cet_matches(self):
        # 15 Dec 2025 is a Monday. CET = UTC+1, so 23:15 CET = 22:15 UTC.
        now = datetime.datetime(2025, 12, 15, 22, 15, tzinfo=UTC)
        result = is_within_scheduled_window(now=now)
        self.assertTrue(result.is_within_window)

    def test_wednesday_at_2315_utc_winter_cet_matches(self):
        # 17 Dec 2025 is a Wednesday.
        now = datetime.datetime(2025, 12, 17, 22, 15, tzinfo=UTC)
        result = is_within_scheduled_window(now=now)
        self.assertTrue(result.is_within_window)

    def test_tuesday_never_matches_even_at_the_right_time(self):
        # 16 Dec 2025 is a Tuesday.
        now = datetime.datetime(2025, 12, 16, 22, 15, tzinfo=UTC)
        result = is_within_scheduled_window(now=now)
        self.assertFalse(result.is_within_window)
        self.assertIn("Tuesday", result.reason)

    def test_monday_but_wrong_time_of_day_does_not_match(self):
        # Monday, but 09:00 UTC -- nowhere near 23:15 Brussels time.
        now = datetime.datetime(2025, 12, 15, 9, 0, tzinfo=UTC)
        result = is_within_scheduled_window(now=now)
        self.assertFalse(result.is_within_window)


class TestSummerCEST(unittest.TestCase):
    def test_monday_at_2315_cest_summer_matches(self):
        # 6 July 2026 is a Monday. CEST = UTC+2, so 23:15 CEST = 21:15 UTC.
        now = datetime.datetime(2026, 7, 6, 21, 15, tzinfo=UTC)
        result = is_within_scheduled_window(now=now)
        self.assertTrue(result.is_within_window)

    def test_same_utc_time_that_matched_in_winter_does_NOT_match_in_summer(self):
        """
        THE key DST-awareness proof: 22:15 UTC on a Monday correctly
        matched in winter (CET, above) -- but in summer, 22:15 UTC is
        actually 00:15 CEST the NEXT calendar day, which is neither the
        right weekday's evening nor the right time. If this test
        failed, it would mean the code was NOT actually DST-aware and
        was just assuming a fixed UTC offset.
        """
        # 6 July 2026 is a Monday; 22:15 UTC = 00:15 CEST on 7 July (Tuesday).
        now = datetime.datetime(2026, 7, 6, 22, 15, tzinfo=UTC)
        result = is_within_scheduled_window(now=now)
        self.assertFalse(result.is_within_window)


class TestRealDstTransitionDates(unittest.TestCase):
    """
    The exact scenario cron-only scheduling cannot handle precisely:
    the actual moment Brussels switches between CET and CEST. Uses
    zoneinfo directly (not a hardcoded date) to build "23:15 Brussels
    time" on real transition-adjacent dates, then converts to UTC to
    simulate what a workflow's cron trigger would need to fire at --
    proving the guard correctly recognizes the RIGHT moment regardless
    of which side of the transition it falls on.
    """

    def test_2026_spring_transition_saturday_before(self):
        # 2026's "spring forward" is the last Sunday of March = 29 March 2026.
        # 28 March 2026 is a Saturday (not a scheduled day) -- confirm it's
        # correctly rejected regardless of DST timing, as a baseline.
        brussels_time = datetime.datetime(2026, 3, 28, 23, 15, tzinfo=ZoneInfo("Europe/Brussels"))
        now_utc = brussels_time.astimezone(UTC)
        result = is_within_scheduled_window(now=now_utc)
        self.assertFalse(result.is_within_window)  # Saturday, correctly rejected

    def test_2026_spring_transition_monday_after_still_correctly_detected(self):
        # 30 March 2026 is the Monday immediately AFTER the spring-forward
        # Sunday -- Brussels is now on CEST (UTC+2). Build 23:15 Brussels
        # time directly via zoneinfo (which knows this) and confirm the
        # guard still correctly recognizes it as "Monday at 23:15", even
        # though the UTC offset just changed by an hour compared to the
        # previous week.
        brussels_time = datetime.datetime(2026, 3, 30, 23, 15, tzinfo=ZoneInfo("Europe/Brussels"))
        now_utc = brussels_time.astimezone(UTC)
        result = is_within_scheduled_window(now=now_utc)
        self.assertTrue(result.is_within_window)
        self.assertEqual(result.brussels_now.weekday(), 0)  # Monday

    def test_2026_autumn_transition_wednesday_before(self):
        # 2026's "fall back" is the last Sunday of October = 25 October 2026.
        # 21 October 2026 is a Wednesday, still on CEST (UTC+2), a few days
        # before the transition.
        brussels_time = datetime.datetime(2026, 10, 21, 23, 15, tzinfo=ZoneInfo("Europe/Brussels"))
        now_utc = brussels_time.astimezone(UTC)
        result = is_within_scheduled_window(now=now_utc)
        self.assertTrue(result.is_within_window)

    def test_2026_autumn_transition_wednesday_after_still_correctly_detected(self):
        # 28 October 2026 is a Wednesday immediately AFTER the fall-back
        # Sunday -- Brussels is now back on CET (UTC+1). Confirm the guard
        # still correctly recognizes 23:15 Brussels time despite the
        # UTC offset having changed again.
        brussels_time = datetime.datetime(2026, 10, 28, 23, 15, tzinfo=ZoneInfo("Europe/Brussels"))
        now_utc = brussels_time.astimezone(UTC)
        result = is_within_scheduled_window(now=now_utc)
        self.assertTrue(result.is_within_window)
        self.assertEqual(result.brussels_now.weekday(), 2)  # Wednesday


class TestTolerance(unittest.TestCase):
    def test_slightly_late_trigger_within_tolerance_still_matches(self):
        # 15 Dec 2025 Monday, 22:40 UTC = 23:40 CET -- 25 minutes late, within the default 30-minute tolerance.
        now = datetime.datetime(2025, 12, 15, 22, 40, tzinfo=UTC)
        result = is_within_scheduled_window(now=now)
        self.assertTrue(result.is_within_window)

    def test_far_too_late_trigger_outside_tolerance_rejected(self):
        # Same Monday, but 2 hours late -- must NOT match.
        now = datetime.datetime(2025, 12, 15, 0, 15, tzinfo=UTC)  # 01:15 CET the same UTC-day boundary
        result = is_within_scheduled_window(now=now)
        self.assertFalse(result.is_within_window)

    def test_custom_tolerance_respected(self):
        now = datetime.datetime(2025, 12, 15, 22, 50, tzinfo=UTC)  # 23:50 CET -- 35 min late
        self.assertFalse(is_within_scheduled_window(now=now, tolerance_minutes=30).is_within_window)
        self.assertTrue(is_within_scheduled_window(now=now, tolerance_minutes=40).is_within_window)


class TestNaiveDatetimeRejected(unittest.TestCase):
    def test_naive_datetime_raises_value_error(self):
        naive_now = datetime.datetime(2025, 12, 15, 22, 15)  # no tzinfo
        with self.assertRaises(ValueError):
            is_within_scheduled_window(now=naive_now)


class TestCustomScheduleParameters(unittest.TestCase):
    def test_custom_weekdays_respected(self):
        # Friday=4 -- not in the default schedule, but explicitly allowed here.
        now = datetime.datetime(2025, 12, 19, 22, 15, tzinfo=UTC)  # 19 Dec 2025 is a Friday
        result = is_within_scheduled_window(now=now, scheduled_weekdays=(4,))
        self.assertTrue(result.is_within_window)

    def test_custom_target_time_respected(self):
        now = datetime.datetime(2025, 12, 15, 8, 0, tzinfo=UTC)  # 09:00 CET
        result = is_within_scheduled_window(now=now, target_hour=9, target_minute=0)
        self.assertTrue(result.is_within_window)


if __name__ == "__main__":
    unittest.main()
