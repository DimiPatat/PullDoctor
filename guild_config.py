"""
guild_config.py

Identifies YOUR guild for latest_guild_report.py's "find the most
recent report automatically" feature. Fill these three values in
before using --latest-guild-report on any script, or before running
the GitHub Actions workflow.

WHERE TO FIND THESE VALUES: open your guild's page on Warcraft Logs
(warcraftlogs.com -> search your guild -> click it). The URL looks
like:

    https://www.warcraftlogs.com/guild/id/123456
        -- OR --
    https://www.warcraftlogs.com/guild/<region>/<server-slug>/<Guild+Name>

If your guild's URL uses the second form, read the three pieces
straight out of it:

    https://www.warcraftlogs.com/guild/eu/silvermoon/My+Raid+Team
                                      ^^  ^^^^^^^^^^  ^^^^^^^^^^^^
                                  region  server_slug   guild_name
                                                        (+ decode "+" as a space)

GUILD_SERVER_SLUG is case-sensitive and must match exactly how
Warcraft Logs spells your realm's slug (e.g. "silvermoon", not
"Silvermoon" or "silver-moon") -- if in doubt, copy it directly from
the URL rather than typing it from memory.

GUILD_SERVER_REGION is the short region code: "us", "eu", "kr", "tw",
or "cn".
"""
from __future__ import annotations

GUILD_NAME = "Undercover Socials"
GUILD_SERVER_SLUG = "Silvermoon"
GUILD_SERVER_REGION = "eu"  # "us", "eu", "kr", "tw", or "cn"

_PLACEHOLDER = object()  # not used directly, see below

def is_configured() -> bool:
    """
    True once the three placeholder values above have actually been
    edited. Used by latest_guild_report.py to fail with a clear,
    actionable error message (rather than a confusing "guild not
    found" from the API itself) if someone runs the scheduled
    automation before filling these in.
    """
    return (
        bool(GUILD_NAME.strip())
        and bool(GUILD_SERVER_SLUG.strip())
        and GUILD_SERVER_REGION in {"us", "eu", "kr", "tw", "cn"}
    )
