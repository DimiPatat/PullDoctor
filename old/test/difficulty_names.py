"""
difficulty_names.py

Maps Warcraft Logs' numeric raid Fight.difficulty ID to a human-
readable name (LFR/Normal/Heroic/Mythic), plus a suggested display
color for each -- used by report.py (text/Markdown) and html_report.py
(a colored badge, similar to the existing KILL/WIPE badge) so it's
never ambiguous which difficulty a given pull was.

IMPORTANT: WCL's numeric difficulty IDs are its OWN internal scheme --
they are NOT the same numbers WoW's client-side API uses for the same
concept (e.g. Blizzard's own SetRaidDifficultyID uses 14/15/16 for
Normal/Heroic/Mythic; WCL uses 3/4/5). Never assume a WCL difficulty
number matches anything you might see referenced elsewhere for
"difficulty ID" -- always go through this mapping.

Verified against a technical WCL API reference guide (2026-09-21):
    WCL difficulty 3 -> Normal
    WCL difficulty 4 -> Heroic
    WCL difficulty 5 -> Mythic

difficulty 1 (LFR) is included based on extremely broad, consistent
usage across essentially every third-party WCL tool/wrapper, though it
was not explicitly present in the one source checked for this file --
flagged here so it can be corrected easily if a report ever shows an
LFR pull with a different number. Because DIFFICULTY_NAMES is a plain
dict, fixing a wrong ID (if this one ever turns out to be wrong) is a
one-line change, and any raid whose real difficulty ID isn't in this
dict at all falls back to a safe, honest "Difficulty <N>" label
instead of silently mislabeling it -- see difficulty_name() below.

Dungeon-only difficulty IDs (Mythic+ keystone levels, Timewalking,
etc.) are deliberately NOT included -- this project is a raid analysis
toolkit, and a fight with one of those difficulty IDs will simply show
as "Difficulty <N>" rather than a guessed-wrong raid name.
"""
from __future__ import annotations

DIFFICULTY_NAMES: dict[int, str] = {
    1: "LFR",
    3: "Normal",
    4: "Heroic",
    5: "Mythic",
}

# Rough WoW-style difficulty colors, for the HTML report's badge.
# Not pulled from any single authoritative source -- chosen to be
# immediately recognizable (grey=LFR, green=Normal, blue=Heroic,
# orange=Mythic matches the general color association most raiders
# already have from the in-game UI and popular addons/sites).
DIFFICULTY_COLORS: dict[int, str] = {
    1: "#9d9d9d",
    3: "#4caf50",
    4: "#0070dd",
    5: "#ff8000",
}

DEFAULT_COLOR = "#9a9db0"


def difficulty_name(difficulty: int | None) -> str:
    """
    Human-readable difficulty name for a Fight.difficulty value.
    Returns "Difficulty <N>" for any numeric value not in
    DIFFICULTY_NAMES (e.g. a dungeon/Mythic+ difficulty ID, or a raid
    difficulty this mapping doesn't know about yet) -- never guesses,
    never silently mislabels. Returns "Unknown Difficulty" only when
    the value itself is missing (None) rather than just unrecognized,
    so those two distinct situations don't look identical in a report.
    """
    if difficulty is None:
        return "Unknown Difficulty"
    return DIFFICULTY_NAMES.get(difficulty, f"Difficulty {difficulty}")


def difficulty_color(difficulty: int | None) -> str:
    """Suggested display color for a Fight.difficulty value. Falls back to DEFAULT_COLOR for anything unrecognized/missing."""
    if difficulty is None:
        return DEFAULT_COLOR
    return DIFFICULTY_COLORS.get(difficulty, DEFAULT_COLOR)
