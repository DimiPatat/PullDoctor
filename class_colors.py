"""
class_colors.py

Official WoW class colors (Blizzard's own RAID_CLASS_COLORS, current
patch, verified via Wowhead/Wowpedia), used to color-code player names
and meter bars in html_report.py.

Warcraft Logs' own data (both Actor.subtype from masterData.actors, and
the "icon" field elsewhere, e.g. "DeathKnight-Blood") is INCONSISTENT
about whether multi-word class names contain a space -- sometimes
"DeathKnight", sometimes "Death Knight", depending on which endpoint/
field it came from. normalize_class_name() handles both forms so
get_class_color() works regardless of which one you feed it.
"""
from __future__ import annotations

import re

CLASS_COLORS: dict[str, str] = {
    "Death Knight": "#C41E3A",
    "Demon Hunter": "#A330C9",
    "Druid": "#FF7C0A",
    "Evoker": "#33937F",
    "Hunter": "#AAD372",
    "Mage": "#3FC7EB",
    "Monk": "#00FF98",
    "Paladin": "#F48CBA",
    "Priest": "#FFFFFF",
    "Rogue": "#FFF468",
    "Shaman": "#0070DD",
    "Warlock": "#8788EE",
    "Warrior": "#C69B6D",
}

# Used whenever a player's class is unknown/missing, or isn't one of
# the 13 known classes above (e.g. an NPC/pet somehow routed through
# this lookup) -- matches the report's existing muted "dim text" color
# so an unrecognized entry blends in neutrally rather than standing out
# as an alarming/wrong color.
DEFAULT_COLOR = "#9a9db0"

# Matches a lowercase letter immediately followed by an uppercase
# letter with NO space between them -- i.e. exactly the boundary where
# a "DeathKnight"-style (no-space) class name needs a space inserted
# to become "Death Knight". Already-spaced input ("Death Knight") has
# no such boundary (the letter before the uppercase "K" is a space, not
# a lowercase letter), so this is a safe no-op on already-correct input.
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")


def normalize_class_name(name: str | None) -> str | None:
    """
    Convert a no-space class name ("DeathKnight", "DemonHunter") into
    the spaced form used as CLASS_COLORS' keys ("Death Knight", "Demon
    Hunter"). Already-spaced input, and single-word class names
    (Warrior, Priest, Mage, ...), pass through unchanged. Returns None
    for empty/missing input.
    """
    if not name:
        return None
    return _CAMEL_BOUNDARY_RE.sub(" ", name)


def get_class_color(class_name: str | None) -> str:
    """Look up a class's official color by name (either 'DeathKnight' or 'Death Knight' form). Falls back to DEFAULT_COLOR."""
    normalized = normalize_class_name(class_name)
    if normalized is None:
        return DEFAULT_COLOR
    return CLASS_COLORS.get(normalized, DEFAULT_COLOR)
