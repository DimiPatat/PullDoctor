"""
wcl_api.py

API module for the Warcraft Logs combat log analyzer.

Responsibility (and ONLY this): authenticate against the Warcraft Logs v2
API and fetch RAW JSON data. No parsing, no normalization, no analysis --
that all happens in downstream modules (e.g. log_parser.py).

Usage:
    from wcl_api import WCLClient

    client = WCLClient(client_id="...", client_secret="...")
    fights = client.get_report_fights("aBcD3fGhJkLmNpQr")
    events = client.get_report_events("aBcD3fGhJkLmNpQr", fight_id=3)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
API_URL = "https://www.warcraftlogs.com/api/v2/client"

# Valid values for the `events` endpoint's dataType argument. WCL's schema
# marks this argument as nullable, but in practice the endpoint returns a
# null `events` object (no error) if you don't specify one -- so callers
# must always pick one of these per call.
EVENT_DATA_TYPES = [
    "Buffs",
    "Casts",
    "CombatantInfo",
    "DamageDone",
    "DamageTaken",
    "Deaths",
    "Debuffs",
    "Dispels",
    "Healing",
    "Interrupts",
    "Resources",
    "Summons",
    "Threat",
]


class WCLAPIError(Exception):
    """Raised for any error talking to the Warcraft Logs API."""


class WCLAuthError(WCLAPIError):
    """Raised specifically for authentication/token failures."""


@dataclass
class RateLimitInfo:
    """Tracks the API's points-based rate limit, as reported by WCL itself."""
    points_spent_this_hour: Optional[float] = None
    limit_per_hour: Optional[float] = None
    points_reset_in_seconds: Optional[float] = None

    def update_from_ratelimit_data(self, data: dict[str, Any]) -> None:
        self.points_spent_this_hour = data.get("pointsSpentThisHour")
        self.limit_per_hour = data.get("limitPerHour")
        self.points_reset_in_seconds = data.get("pointsResetIn")


class WCLClient:
    """
    Thin client for the Warcraft Logs v2 GraphQL API.

    Handles:
      - OAuth2 client-credentials token exchange + refresh
      - GraphQL request wrapping
      - Pagination over the `events` endpoint
      - Basic rate-limit tracking
      - Raising clear errors for bad report codes / private reports / etc.

    Does NOT handle:
      - Turning results into your own data models (see log_parser.py)
      - Any analysis of the data
    """

    def __init__(self, client_id: str, client_secret: str, timeout: int = 30):
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

        self.rate_limit = RateLimitInfo()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _get_access_token(self) -> str:
        """Return a valid access token, fetching/refreshing it if needed."""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        try:
            response = requests.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise WCLAuthError(f"Could not reach token endpoint: {exc}") from exc

        if response.status_code != 200:
            raise WCLAuthError(
                f"Token request failed ({response.status_code}): {response.text}"
            )

        payload = response.json()
        token = payload.get("access_token")
        expires_in = payload.get("expires_in", 3600)

        if not token:
            raise WCLAuthError(f"No access_token in response: {payload}")

        self._access_token = token
        # Refresh a little early to be safe.
        self._token_expires_at = time.time() + expires_in - 60
        return token

    # ------------------------------------------------------------------
    # Low-level GraphQL call
    # ------------------------------------------------------------------

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        token = self._get_access_token()

        try:
            response = requests.post(
                API_URL,
                json={"query": query, "variables": variables},
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise WCLAPIError(f"Request failed: {exc}") from exc

        if response.status_code != 200:
            raise WCLAPIError(
                f"API request failed ({response.status_code}): {response.text}"
            )

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

    # ------------------------------------------------------------------
    # Public: report-level data
    # ------------------------------------------------------------------

    def get_report_fights(self, report_code: str) -> dict[str, Any]:
        """
        Fetch raw report metadata + fight list for a report code.
        Raises WCLAPIError if the report doesn't exist or isn't accessible.
        """
        query = """
        query ReportFights($code: String!) {
            rateLimitData { pointsSpentThisHour limitPerHour pointsResetIn }
            reportData {
                report(code: $code) {
                    title
                    startTime
                    endTime
                    zone { name }
                    fights {
                        id
                        name
                        difficulty
                        kill
                        startTime
                        endTime
                        encounterID
                    }
                }
            }
        }
        """
        data = self._graphql(query, {"code": report_code})
        report = data.get("reportData", {}).get("report")

        if report is None:
            raise WCLAPIError(
                f"Report '{report_code}' not found or not accessible "
                "(check the code, and that the report isn't private)."
            )

        return report

    def get_report_master_data(self, report_code: str) -> dict[str, Any]:
        """
        Fetch raw masterData for a report: the actor list (players, NPCs,
        pets) and ability list, keyed by the IDs used in events. Needed to
        resolve event sourceID/targetID/abilityGameID into readable names.

        This is report-wide (WCL's masterData field takes no per-fight
        filter) -- the same actors/abilities lookup applies to every
        fight in the report.
        """
        query = """
        query ReportMasterData($code: String!) {
            rateLimitData { pointsSpentThisHour limitPerHour pointsResetIn }
            reportData {
                report(code: $code) {
                    masterData {
                        actors {
                            id
                            name
                            type
                            subType
                            server
                            petOwner
                        }
                        abilities {
                            gameID
                            name
                            type
                        }
                    }
                }
            }
        }
        """
        data = self._graphql(query, {"code": report_code})
        master_data = data.get("reportData", {}).get("report", {}).get("masterData")

        if master_data is None:
            raise WCLAPIError(
                f"No masterData returned for report '{report_code}'."
            )

        return master_data

    def get_report_events(
        self,
        report_code: str,
        fight_id: int,
        event_type: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """
        Fetch ALL raw events of one type for a given fight, transparently
        paginating through WCL's events endpoint. Returns a flat list of
        raw event dicts, in chronological order.

        event_type is REQUIRED -- despite WCL's schema marking `dataType`
        as nullable, leaving it unset causes the API to silently return
        `events: null` with no error. Must be one of EVENT_DATA_TYPES.
        Use get_report_events_multi() to fetch several types at once.
        """
        if event_type not in EVENT_DATA_TYPES:
            raise ValueError(
                f"event_type must be one of {EVENT_DATA_TYPES}, got {event_type!r}"
            )

        query = """
        query ReportEvents(
            $code: String!
            $fightIDs: [Int!]
            $startTime: Float!
            $endTime: Float!
            $dataType: EventDataType!
            $limit: Int!
        ) {
            rateLimitData { pointsSpentThisHour limitPerHour pointsResetIn }
            reportData {
                report(code: $code) {
                    events(
                        fightIDs: $fightIDs
                        startTime: $startTime
                        endTime: $endTime
                        dataType: $dataType
                        limit: $limit
                    ) {
                        data
                        nextPageTimestamp
                    }
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
            data = self._graphql(
                query,
                {
                    "code": report_code,
                    "fightIDs": [fight_id],
                    "startTime": cursor,
                    "endTime": end_time,
                    "dataType": event_type,
                    "limit": limit,
                },
            )

            events_block = data.get("reportData", {}).get("report", {}).get("events")
            if events_block is None:
                raise WCLAPIError(
                    f"No events returned for report '{report_code}', fight {fight_id}, "
                    f"type '{event_type}'."
                )

            page = events_block.get("data", [])
            all_events.extend(page)

            next_ts = events_block.get("nextPageTimestamp")
            if not next_ts:
                break
            cursor = next_ts

        return all_events

    def get_report_events_multi(
        self,
        report_code: str,
        fight_id: int,
        event_types: list[str],
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Fetch several event types for the same fight in one call, since
        analysis almost always needs more than one type together (e.g.
        Casts + Healing + Deaths). Returns a dict keyed by event_type,
        each value being that type's raw event list -- still unmodified,
        raw WCL data per type, just grouped for convenience.

        Looks up the fight's start/end time once and reuses it across all
        types, rather than re-fetching per type.
        """
        if start_time is None or end_time is None:
            start_time, end_time = self._get_fight_window(report_code, fight_id)

        results: dict[str, list[dict[str, Any]]] = {}
        for event_type in event_types:
            results[event_type] = self.get_report_events(
                report_code,
                fight_id,
                event_type=event_type,
                start_time=start_time,
                end_time=end_time,
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_fight_window(self, report_code: str, fight_id: int) -> tuple[int, int]:
        """Look up a single fight's start/end time from the report's fight list."""
        report = self.get_report_fights(report_code)
        for fight in report.get("fights", []):
            if fight["id"] == fight_id:
                return fight["startTime"], fight["endTime"]
        raise WCLAPIError(f"Fight id {fight_id} not found in report '{report_code}'.")
