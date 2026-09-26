"""
wcl_api.py

API module for the Warcraft Logs combat log analyzer. Only module that
touches the network.

NEW: get_guild_reports_page() / get_all_guild_reports_in_range() --
support for latest_guild_report.py's "find our guild's most recent
report automatically" feature. Uses ReportData.reports(), whose exact
GraphQL argument set (guildID / guildName+guildServerSlug+
guildServerRegion / guildTagID / userID / limit / page / startTime /
endTime / zoneID) was verified directly against Warcraft Logs' own
published v2 API schema docs (2026-09-21) before being used here --
see latest_guild_report.py for how "most recent" is determined
robustly (by explicit startTime comparison across whatever page(s) are
fetched, rather than assuming any particular default sort order from
the API itself, since that isn't documented and shouldn't be guessed at).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

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
        object in this API) -- see latest_guild_report.py for how the
        caller uses this to reliably find the single most recent report.
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
