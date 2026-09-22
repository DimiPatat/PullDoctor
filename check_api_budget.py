"""
check_api_budget.py

Checks how much Warcraft Logs API rate-limit budget you have left this
hour, WITHOUT fetching a report -- uses WCLClient.get_rate_limit_status(),
the cheapest possible authenticated query against the v2 API (it asks
for nothing except your own rate-limit standing).

Warcraft Logs' v2 API is points-based: every query costs a variable
number of points depending on how much data it returns, and you're
capped at a certain number of points per ROLLING HOUR (the cap depends
on your account/client tier -- there's no fixed universal number).
Fetching a single small fight typically costs relatively little; a
large multi-hour raid night fetched via generate_html_report.py can add
up. This tool lets you check your standing before/after a big pull, or
diagnose a "rate limit exceeded" error from another script.

Usage:
    python check_api_budget.py
    python check_api_budget.py --json
"""
from __future__ import annotations

import argparse
import json as json_module
import sys

from wcl_api import WCLClient, WCLAPIError
import config


def format_seconds_as_minutes(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    minutes = seconds / 60
    if minutes < 1:
        return f"{seconds:.0f}s"
    return f"{minutes:.1f} min"


def print_budget_report(rate_limit) -> None:
    print("Warcraft Logs API rate-limit status")
    print("=" * 40)

    if not rate_limit.has_data:
        print("No rate-limit data available (this shouldn't happen -- the query may have failed silently).")
        return

    spent = rate_limit.points_spent_this_hour or 0.0
    limit = rate_limit.limit_per_hour or 0.0
    remaining = rate_limit.points_remaining or 0.0
    percent_remaining = rate_limit.percent_remaining or 0.0
    reset_in = rate_limit.points_reset_in_seconds

    print(f"Points spent this hour:  {spent:>10,.2f}")
    print(f"Points limit per hour:   {limit:>10,.2f}")
    print(f"Points remaining:        {remaining:>10,.2f}  ({percent_remaining:.1f}% left)")
    print(f"Resets in:               {format_seconds_as_minutes(reset_in)}")

    print()
    if percent_remaining <= 0:
        print("*** You are OUT of API budget for this hour. Requests will start failing until it resets. ***")
    elif percent_remaining < 10:
        print("*** WARNING: less than 10% of your hourly budget remains. ***")
    elif percent_remaining < 25:
        print("Note: less than 25% of your hourly budget remains -- consider pacing large fetches "
              "(e.g. generate_html_report.py on a big raid night) until it resets.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check remaining Warcraft Logs API rate-limit budget, without fetching a report."
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print machine-readable JSON instead of a human-readable summary.",
    )
    args = parser.parse_args()

    try:
        client_id, client_secret = config.get_credentials()
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)

    client = WCLClient(client_id, client_secret)

    try:
        rate_limit = client.get_rate_limit_status()
    except WCLAPIError as exc:
        print(f"Error checking API budget: {exc}")
        sys.exit(1)

    if args.json:
        print(json_module.dumps({
            "points_spent_this_hour": rate_limit.points_spent_this_hour,
            "limit_per_hour": rate_limit.limit_per_hour,
            "points_remaining": rate_limit.points_remaining,
            "percent_remaining": rate_limit.percent_remaining,
            "points_reset_in_seconds": rate_limit.points_reset_in_seconds,
        }, indent=2))
    else:
        print_budget_report(rate_limit)


if __name__ == "__main__":
    main()
