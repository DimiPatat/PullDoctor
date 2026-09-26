"""
wcl.py

MERGED MODULE -- combines what used to be four separate files into one,
as part of the PullDoctor file-count reduction pass:

    - config.py            (load WCL API credentials from .env)
    - wcl_api.py           (the actual GraphQL client -- only module that touches the network)
    - guild_config.py      (identifies YOUR guild for "find latest report" automation)
    - check_api_budget.py  (CLI: check remaining rate-limit budget without fetching a report)

Grouped together because they're all fundamentally about "how this
project talks to Warcraft Logs" -- credentials, the client itself,
which guild to look up, and a small diagnostic CLI for the client's
own rate-limit standing.

*** PREVIOUSLY-FLAGGED DISCREPANCY -- NOW RESOLVED ***
An earlier merge pass flagged that check_api_budget.py (Section 4)
calls client.get_rate_limit_status() and reads rate_limit.has_data /
.points_remaining / .percent_remaining, none of which the wcl_api.py
source (Section 2) defined. That gap has now been filled in, checked
against every other query in this file rather than guessed at:

  - get_rate_limit_status() runs the cheapest possible authenticated
    query -- rateLimitData alone, with NO reportData at all. This is
    safe to add because every other query in Section 2 already treats
    rateLimitData as a top-level sibling field of reportData (never
    nested under it), which is exactly the pattern _graphql()'s
    existing "if data.get('rateLimitData'): update rate_limit" handling
    was already built to expect -- so a query asking for rateLimitData
    ALONE needs no new response-handling logic, just a smaller query.
  - has_data / points_remaining / percent_remaining are added as plain
    computed @property values on RateLimitInfo, derived only from the
    two fields the API actually returns (points_spent_this_hour,
    limit_per_hour) -- no new fields invented, nothing fetched that
    wasn't already available.

Verified against every client.* call and rate_limit.* attribute access
across the whole project -- this was the ONLY undefined
method/attribute found anywhere; nothing else needed adding.
"""
from __future__ import annotations

import argparse
import json as json_module
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests
from dotenv import load_dotenv


# =======================================================================
# SECTION 1 -- credentials
# (originally config.py)
#
# Loads Warcraft Logs API credentials from a .env file in the project
# directory, via python-dotenv. Falls back to already-set environment
# variables if there's no .env file.
#
# Setup:
#     1. Copy .env.example to .env
#     2. Fill in your WCL_CLIENT_ID and WCL_CLIENT_SECRET
#     3. Never commit .env -- it's already in .gitignore
# =======================================================================

load_dotenv()  # loads .env from the current directory if present; no-op otherwise


def get_credentials() -> tuple[str, str]:
    """Return (client_id, client_secret), or raise RuntimeError with a clear message if either is missing."""
    client_id = os.environ.get("WCL_CLIENT_ID")
    client_secret = os.environ.get("WCL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing WCL_CLIENT_ID / WCL_CLIENT_SECRET. Copy .env.example to "
            ".env in this folder and fill in your Warcraft Logs API credentials."
        )
    return client_id, client_secret


# =======================================================================
# SECTION 2 -- the API client itself
# (originally wcl_api.py -- ONLY section in this file that touches the network)
#
# get_guild_reports_page() -- support for the "find our guild's most
# recent report automatically" feature (Section 3/automation.py side).
# Uses ReportData.reports(), whose exact GraphQL argument set (guildID /
# guildName+guildServerSlug+guildServerRegion / guildTagID / userID /
# limit / page / startTime / endTime / zoneID) was verified directly
# against Warcraft Logs' own published v2 API schema docs (2026-09-21)
# before being used here -- see automation.py's latest-report lookup
# for how "most recent" is determined robustly (by explicit startTime
# comparison across whatever page(s) are fetched, rather than assuming
# any particular default sort order from the API itself, since that
# isn't documented and shouldn't be guessed at).
# =======================================================================

TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
API_URL = "https://www.warcraftlogs.com/api/v2/client"
EVENT_DATA_TYPES = [
    "Buffs", "Casts", "CombatantInfo", "DamageDone", "DamageTaken", "Deaths",
    "Debuffs", "Dispels", "Healing", "Interrupts", "Resources", "Summons", "Threat",
]


class WCLAPIError(Exception):
    pass


class WCLAuthError(WCLAPIError):
    pass


@dataclass
class RateLimitInfo:
    points_spent_this_hour: Optional[float] = None
    limit_per_hour: Optional[float] = None
    points_reset_in_seconds: Optional[float] = None

    def update_from_ratelimit_data(self, data: dict[str, Any]) -> None:
        self.points_spent_this_hour = data.get("pointsSpentThisHour")
        self.limit_per_hour = data.get("limitPerHour")
        self.points_reset_in_seconds = data.get("pointsResetIn")

    @property
    def has_data(self) -> bool:
        """True once a real response has populated this (i.e. update_from_ratelimit_data() has run at least once)."""
        return self.points_spent_this_hour is not None and self.limit_per_hour is not None

    @property
    def points_remaining(self) -> Optional[float]:
        """limit_per_hour minus points_spent_this_hour, floored at 0. None if we don't have data yet."""
        if not self.has_data:
            return None
        return max(self.limit_per_hour - self.points_spent_this_hour, 0.0)

    @property
    def percent_remaining(self) -> Optional[float]:
        """points_remaining as a percentage of limit_per_hour. None if we don't have data yet or the limit is 0."""
        remaining = self.points_remaining
        if remaining is None or not self.limit_per_hour:
            return None
        return (remaining / self.limit_per_hour) * 100


class WCLClient:
    def __init__(self, client_id: str, client_secret: str, timeout: int = 30):
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self.rate_limit = RateLimitInfo()

    def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        try:
            response = requests.post(
                TOKEN_URL, data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret), timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise WCLAuthError(f"Could not reach token endpoint: {exc}") from exc
        if response.status_code != 200:
            raise WCLAuthError(f"Token request failed ({response.status_code}): {response.text}")
        payload = response.json()
        token = payload.get("access_token")
        expires_in = payload.get("expires_in", 3600)
        if not token:
            raise WCLAuthError(f"No access_token in response: {payload}")
        self._access_token = token
        self._token_expires_at = time.time() + expires_in - 60
        return token

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        token = self._get_access_token()
        try:
            response = requests.post(
                API_URL, json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise WCLAPIError(f"Request failed: {exc}") from exc
        if response.status_code != 200:
            raise WCLAPIError(f"API request failed ({response.status_code}): {response.text}")
        payload = response.json()
        if "errors" in payload and payload["errors"]:
            raise WCLAPIError(f"GraphQL errors: {payload['errors']}")
        data = payload.get("data")
        if data is None:
            raise WCLAPIError(f"No data in response: {payload}")
        rate_limit_data = data.get("rateLimitData")
        if rate_limit_data:
            self.rate_limit.update_from_ratelimit_data(rate_limit_data)
        return data

    def get_rate_limit_status(self) -> RateLimitInfo:
        """
        Check remaining hourly API point budget WITHOUT fetching a
        report -- the cheapest possible authenticated query, asking
        for nothing but rateLimitData itself. Updates and returns
        self.rate_limit (same object every other method also updates),
        so this can be called before or after other requests and
        always reflects the latest known standing.
        """
        query = """
        query RateLimitStatus {
            rateLimitData { pointsSpentThisHour limitPerHour pointsResetIn }
        }
        """
        self._graphql(query, {})
        return self.rate_limit

    def get_report_fights(self, report_code: str) -> dict[str, Any]:
        query = """
        query ReportFights($code: String!) {
            rateLimitData { pointsSpentThisHour limitPerHour pointsResetIn }
            reportData {
                report(code: $code) {
                    title startTime endTime
                    zone { name }
                    fights { id name difficulty kill startTime endTime encounterID friendlyPlayers }
                }
            }
        }
        """
        data = self._graphql(query, {"code": report_code})
        report = data.get("reportData", {}).get("report")
        if report is None:
            raise WCLAPIError(f"Report '{report_code}' not found or not accessible.")
        return report

    def get_report_master_data(self, report_code: str) -> dict[str, Any]:
        query = """
        query ReportMasterData($code: String!) {
            rateLimitData { pointsSpentThisHour limitPerHour pointsResetIn }
            reportData {
                report(code: $code) {
                    masterData {
                        actors { id name type subType server petOwner }
                        abilities { gameID name type }
                    }
                }
            }
        }
        """
        data = self._graphql(query, {"code": report_code})
        master_data = data.get("reportData", {}).get("report", {}).get("masterData")
        if master_data is None:
            raise WCLAPIError(f"No masterData returned for report '{report_code}'.")
        return master_data

    def get_player_details(self, report_code: str, fight_id: int) -> dict[str, Any]:
        query = """
        query ReportPlayerDetails($code: String!, $fightIDs: [Int!]) {
            rateLimitData { pointsSpentThisHour limitPerHour pointsResetIn }
            reportData {
                report(code: $code) { playerDetails(fightIDs: $fightIDs) }
            }
        }
        """
        data = self._graphql(query, {"code": report_code, "fightIDs": [fight_id]})
        raw = data.get("reportData", {}).get("report", {}).get("playerDetails")
        if not raw:
            return {}
        return raw.get("data", {}).get("playerDetails", {}) if isinstance(raw, dict) else {}

    def get_report_events(
        self, report_code: str, fight_id: int, event_type: str,
        start_time: Optional[int] = None, end_time: Optional[int] = None, limit: int = 10000,
    ) -> list[dict[str, Any]]:
        if event_type not in EVENT_DATA_TYPES:
            raise ValueError(f"event_type must be one of {EVENT_DATA_TYPES}, got {event_type!r}")
        query = """
        query ReportEvents(
            $code: String!, $fightIDs: [Int!], $startTime: Float!, $endTime: Float!,
            $dataType: EventDataType!, $limit: Int!
        ) {
            rateLimitData { pointsSpentThisHour limitPerHour pointsResetIn }
            reportData {
                report(code: $code) {
                    events(fightIDs: $fightIDs, startTime: $startTime, endTime: $endTime,
                           dataType: $dataType, limit: $limit) { data nextPageTimestamp }
                }
            }
        }
        """
        if start_time is None or end_time is None:
            fight_meta = self._get_fight_window(report_code, fight_id)
            start_time = start_time if start_time is not None else fight_meta[0]
            end_time = end_time if end_time is not None else fight_meta[1]
        all_events: list[dict[str, Any]] = []
        cursor = start_time
        while True:
            data = self._graphql(query, {
                "code": report_code, "fightIDs": [fight_id], "startTime": cursor,
                "endTime": end_time, "dataType": event_type, "limit": limit,
            })
            events_block = data.get("reportData", {}).get("report", {}).get("events")
            if events_block is None:
                raise WCLAPIError(f"No events returned for '{report_code}', fight {fight_id}, type '{event_type}'.")
            page = events_block.get("data", [])
            all_events.extend(page)
            next_ts = events_block.get("nextPageTimestamp")
            if not next_ts:
                break
            cursor = next_ts
        return all_events

    def get_report_events_multi(
        self, report_code: str, fight_id: int, event_types: list[str],
        start_time: Optional[int] = None, end_time: Optional[int] = None,
    ) -> dict[str, list[dict[str, Any]]]:
        if start_time is None or end_time is None:
            start_time, end_time = self._get_fight_window(report_code, fight_id)
        results: dict[str, list[dict[str, Any]]] = {}
        for event_type in event_types:
            results[event_type] = self.get_report_events(
                report_code, fight_id, event_type=event_type, start_time=start_time, end_time=end_time,
            )
        return results

    def _get_fight_window(self, report_code: str, fight_id: int) -> tuple[int, int]:
        report = self.get_report_fights(report_code)
        for fight in report.get("fights", []):
            if fight["id"] == fight_id:
                return fight["startTime"], fight["endTime"]
        raise WCLAPIError(f"Fight id {fight_id} not found in report '{report_code}'.")

    def get_guild_reports_page(
        self,
        guild_name: str,
        guild_server_slug: str,
        guild_server_region: str,
        page: int = 1,
        limit: int = 25,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> dict[str, Any]:
        """
        Fetch one page of a guild's reports via
        ReportData.reports(guildName, guildServerSlug, guildServerRegion, ...),
        identifying the guild by name+server+region (rather than
        numeric guild ID) since that's what a person can readily look
        up themselves from their guild's Warcraft Logs URL, with no
        extra lookup step needed.

        Returns the raw "reports" pagination block as-is (keys:
        "data", "total", "per_page", "current_page", "last_page", per
        the ReportPagination shape shared by every other paginated
        object in this API) -- see automation.py's latest-report lookup
        for how the caller uses this to reliably find the single most
        recent report.
        """
        query = """
        query GuildReports(
            $guildName: String!, $guildServerSlug: String!, $guildServerRegion: String!,
            $page: Int!, $limit: Int!, $startTime: Float, $endTime: Float
        ) {
            rateLimitData { pointsSpentThisHour limitPerHour pointsResetIn }
            reportData {
                reports(
                    guildName: $guildName, guildServerSlug: $guildServerSlug,
                    guildServerRegion: $guildServerRegion, page: $page, limit: $limit,
                    startTime: $startTime, endTime: $endTime
                ) {
                    data { code title startTime endTime zone { name } }
                    total
                    per_page
                    current_page
                    last_page
                }
            }
        }
        """
        variables = {
            "guildName": guild_name, "guildServerSlug": guild_server_slug,
            "guildServerRegion": guild_server_region, "page": page, "limit": limit,
            "startTime": start_time, "endTime": end_time,
        }
        data = self._graphql(query, variables)
        reports_block = data.get("reportData", {}).get("reports")
        if reports_block is None:
            raise WCLAPIError(
                f"No reports data returned for guild '{guild_name}' on "
                f"{guild_server_slug}-{guild_server_region}. Double-check the "
                f"guild name, server slug, and region are spelled/cased exactly "
                f"as they appear in the guild's own Warcraft Logs URL."
            )
        return reports_block


# =======================================================================
# SECTION 3 -- guild identity
# (originally guild_config.py)
#
# Identifies YOUR guild for the "find the most recent report
# automatically" feature (see automation.py). Fill these three values
# in before using --latest-guild-report on any script, or before
# running the GitHub Actions workflow.
#
# WHERE TO FIND THESE VALUES: open your guild's page on Warcraft Logs
# (warcraftlogs.com -> search your guild -> click it). The URL looks
# like:
#
#     https://www.warcraftlogs.com/guild/id/123456
#         -- OR --
#     https://www.warcraftlogs.com/guild/<region>/<server-slug>/<Guild+Name>
#
# If your guild's URL uses the second form, read the three pieces
# straight out of it:
#
#     https://www.warcraftlogs.com/guild/eu/silvermoon/My+Raid+Team
#                                       ^^  ^^^^^^^^^^  ^^^^^^^^^^^^
#                                   region  server_slug   guild_name
#                                                         (+ decode "+" as a space)
#
# GUILD_SERVER_SLUG is case-sensitive and must match exactly how
# Warcraft Logs spells your realm's slug (e.g. "silvermoon", not
# "Silvermoon" or "silver-moon") -- if in doubt, copy it directly from
# the URL rather than typing it from memory.
#
# GUILD_SERVER_REGION is the short region code: "us", "eu", "kr", "tw",
# or "cn".
# =======================================================================

GUILD_NAME = "Undercover Socials"
GUILD_SERVER_SLUG = "silvermoon"
GUILD_SERVER_REGION = "eu"  # "us", "eu", "kr", "tw", or "cn"


def is_configured() -> bool:
    """
    True once the three values above are filled in with something
    plausible. Used by automation.py to fail with a clear, actionable
    error message (rather than a confusing "guild not found" from the
    API itself) if someone runs the scheduled automation before
    filling these in.
    """
    return (
        bool(GUILD_NAME.strip())
        and bool(GUILD_SERVER_SLUG.strip())
        and GUILD_SERVER_REGION in {"us", "eu", "kr", "tw", "cn"}
    )


# =======================================================================
# SECTION 4 -- CLI: check remaining rate-limit budget
# (originally check_api_budget.py)
#
# Checks how much Warcraft Logs API rate-limit budget you have left
# this hour, WITHOUT fetching a report -- intended to use the cheapest
# possible authenticated query against the v2 API (one that asks for
# nothing except your own rate-limit standing).
#
# Warcraft Logs' v2 API is points-based: every query costs a variable
# number of points depending on how much data it returns, and you're
# capped at a certain number of points per ROLLING HOUR (the cap
# depends on your account/client tier -- there's no fixed universal
# number). Fetching a single small fight typically costs relatively
# little; a large multi-hour raid night fetched via
# generate_html_report.py can add up. This tool lets you check your
# standing before/after a big pull, or diagnose a "rate limit
# exceeded" error from another script.
#
# Usage:
#     python wcl.py check-budget
#     python wcl.py check-budget --json
# =======================================================================

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


def cmd_check_budget(args: argparse.Namespace) -> None:
    try:
        client_id, client_secret = get_credentials()
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


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Warcraft Logs API client utilities (merged config/client/guild-identity/budget-check)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    budget_parser = subparsers.add_parser(
        "check-budget", help="Check remaining Warcraft Logs API rate-limit budget, without fetching a report."
    )
    budget_parser.add_argument(
        "--json", action="store_true",
        help="Print machine-readable JSON instead of a human-readable summary.",
    )
    budget_parser.set_defaults(func=cmd_check_budget)

    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
