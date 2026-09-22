"""
latest_guild_report.py

Finds the single most recently-uploaded report for a guild, using
WCLClient.get_guild_reports_page() (ReportData.reports(guildName,
guildServerSlug, guildServerRegion, ...)).

WHY THIS DOESN'T JUST TRUST "PAGE 1": Warcraft Logs' public API docs
do not document what ORDER reports() returns results in (ascending or
descending by startTime) -- and guessing wrong would silently make
this whole feature return an OLD report instead of the newest one,
which is exactly the kind of bug that could go unnoticed for weeks in
an unattended scheduled job. So instead of assuming an order:

  1. Fetch page 1 (up to `limit` reports).
  2. If there's more than one page (per the pagination's own
     "last_page" field), ALSO fetch the last page.
  3. Compare every report's startTime seen across BOTH fetched pages,
     and return whichever one has the highest startTime.

This correctly finds the true most-recent report regardless of
whether the API sorts ascending (newest would be on the last page) or
descending (newest would be on page 1) -- it only costs 2 API calls
total regardless of how many reports the guild has, rather than
walking every page.

For a fully-guaranteed-correct answer regardless of how the data is
paginated/sorted (e.g. if you suspect something unusual, like results
not being sorted by time at all), pass deep_scan=True to walk every
page and compare all of them -- more API calls, but zero assumptions.
"""
from __future__ import annotations

from dataclasses import dataclass

from wcl_api import WCLAPIError, WCLClient


class NoGuildReportsFoundError(Exception):
    """Raised when a guild has zero reports at all (not a fetch error -- the guild is just empty/new)."""


@dataclass
class GuildReportSummary:
    code: str
    title: str
    start_time: float
    end_time: float
    zone_name: str | None


def _to_summary(raw_report: dict) -> GuildReportSummary:
    zone = raw_report.get("zone")
    return GuildReportSummary(
        code=raw_report["code"],
        title=raw_report.get("title", "Untitled Report"),
        start_time=raw_report["startTime"],
        end_time=raw_report["endTime"],
        zone_name=zone.get("name") if zone else None,
    )


def find_latest_guild_report(
    client: WCLClient,
    guild_name: str,
    guild_server_slug: str,
    guild_server_region: str,
    limit: int = 25,
    deep_scan: bool = False,
    start_time: float | None = None,
) -> GuildReportSummary:
    """
    Return the single most recent report for this guild. Raises
    NoGuildReportsFoundError if the guild genuinely has zero reports
    in the given window (distinguished from a real API/auth error,
    which raises WCLAPIError instead -- these are NOT the same
    situation and calling code should be able to tell them apart).

    start_time: optional UNIX ms timestamp -- if given, only reports
    starting at or after this time are considered at all (passed
    straight through to the API's own startTime filter, so an old
    guild's full report history never needs to be paginated through
    just to find something recent). See
    publish_scheduled_report.py for why the actual scheduled job
    always sets this to "the last N days" rather than leaving it
    unbounded.
    """
    first_page = client.get_guild_reports_page(
        guild_name, guild_server_slug, guild_server_region, page=1, limit=limit, start_time=start_time,
    )
    all_raw_reports: list[dict] = list(first_page.get("data", []))
    total = first_page.get("total", len(all_raw_reports))

    if total == 0 or not all_raw_reports:
        window_note = " in the requested time window" if start_time is not None else ""
        raise NoGuildReportsFoundError(
            f"Guild '{guild_name}' ({guild_server_slug}-{guild_server_region}) "
            f"has no reports{window_note} on Warcraft Logs."
        )

    last_page_number = first_page.get("last_page", 1)
    current_page_number = first_page.get("current_page", 1)

    if deep_scan:
        page = current_page_number + 1
        while page <= last_page_number:
            next_page = client.get_guild_reports_page(
                guild_name, guild_server_slug, guild_server_region, page=page, limit=limit, start_time=start_time,
            )
            all_raw_reports.extend(next_page.get("data", []))
            page += 1
    elif last_page_number > current_page_number:
        # Fast path: also fetch just the LAST page -- see module
        # docstring for why comparing page 1 + the last page (rather
        # than trusting either one alone) correctly handles the report
        # ordering regardless of which direction it's sorted in.
        last_page = client.get_guild_reports_page(
            guild_name, guild_server_slug, guild_server_region, page=last_page_number, limit=limit, start_time=start_time,
        )
        all_raw_reports.extend(last_page.get("data", []))

    summaries = [_to_summary(r) for r in all_raw_reports]
    return max(summaries, key=lambda s: s.start_time)


def find_latest_guild_report_code(
    client: WCLClient,
    guild_name: str,
    guild_server_slug: str,
    guild_server_region: str,
    limit: int = 25,
    deep_scan: bool = False,
    start_time: float | None = None,
) -> str:
    """Convenience wrapper: same as find_latest_guild_report(), but returns just the report code string."""
    return find_latest_guild_report(
        client, guild_name, guild_server_slug, guild_server_region,
        limit=limit, deep_scan=deep_scan, start_time=start_time,
    ).code
