"""
tier_set_data.py
Loads tracked tier-set pieces from tier_sets.generated.json at import
time, exposing ready-to-use TRACKED_TIER_SET_PIECES and
TRACK_BREAKPOINTS objects. If the generated file is currently broken,
prints a warning and falls back to safe empty/seed defaults instead of
crashing every script that imports it -- same pattern as
gear_requirements_config.py / defensive_cooldown_data.py.

===========================================================================
TRACK BREAKPOINTS -- verified 2026-09-24, current as of Patch 12.1
"Midnight Season 2" (Curse of Ula'tek), live client build 12.1.0.69814
===========================================================================
Sourced from a datamined, live-client-build table (updated 2026-09-16)
cross-checked against two independent gearing guides published the same
week. All three agree on the same breakpoints:
  Adventurer starts at item level 266
  Veteran    starts at item level 279
  Champion   starts at item level 292
  Hero       starts at item level 305
  Myth       starts at item level 318 (climbs to 334 at 6/6, with a few
             Very Rare / last-two-boss drops going as high as 344 --
             still Myth track, just above the normal upgrade ceiling)
These five tracks correspond directly to this raid's difficulty floor:
LFR drops Veteran, Normal drops Champion, Heroic drops Hero, Mythic
drops Myth -- there is no raid-dropped Adventurer-track gear, only
world/leveling content.

IMPORTANT CAVEAT: Blizzard has ALREADY hotfixed Season 2 item levels
upward once this season (a +7 item level bump shortly after launch,
per patch notes). If that happens again, these seed numbers will be
stale. Rather than requiring a code change, TRACK_BREAKPOINTS checks
tier_sets.generated.json FIRST and only falls back to this seed table
if no custom breakpoints have been saved -- run
`manage_tier_sets.py set-breakpoint` to override without touching this
file.

Track colors below are the exact color/letter pairing requested when
this feature was scoped (which also happens to match WoW's familiar
item-rarity color scheme: white/green/blue/purple/orange).

TIER SET NAMES BY CLASS (informational only, NOT used for detection --
this app has no item database, see tier_set_schema.py's docstring) --
verified against Blizzard's official Patch 12.1 PTR tier-set preview
for The Venomous Abyss raid:
    Death Knight  - Baleful Grave-Knight's Crucible
    Demon Hunter  - Abyssal Doomhound's Pursuit
    Druid         - Bark of the Enigmatic Dreamwatcher
    Evoker        - Echo of Calamity
    Hunter        - Skulking Viper's Ambush
    Mage          - Primal Leywarden's Attire
    Monk          - Guile of the Monkey King
    Paladin       - Radiance of the Consecrated Flame
    Priest        - Cosmic Penitent's Raiment
    Rogue         - Chosen Bloodslayer's Hexweave
    Shaman        - Ophidian Oracle's Prophecy
    Warlock       - Damned Necrolyte's Shattered Restraints
    Warrior       - Jade Warlord's Dominion
"""
from __future__ import annotations
from tier_set_config_io import (
    TierSetConfigError,
    load_generated_tier_set_pieces,
    load_generated_track_breakpoints,
)
from tier_set_schema import TierSetPieceDefinition, TierTrackBreakpoint

SEED_TRACK_BREAKPOINTS: list[TierTrackBreakpoint] = [
    TierTrackBreakpoint(min_item_level=266, track_letter="A", track_name="Adventurer", color_hex="#ffffff"),
    TierTrackBreakpoint(min_item_level=279, track_letter="V", track_name="Veteran", color_hex="#1eff00"),
    TierTrackBreakpoint(min_item_level=292, track_letter="C", track_name="Champion", color_hex="#0070dd"),
    TierTrackBreakpoint(min_item_level=305, track_letter="H", track_name="Hero", color_hex="#a335ee"),
    TierTrackBreakpoint(min_item_level=318, track_letter="M", track_name="Myth", color_hex="#ff8000"),
]

# Informational only -- see module docstring. Not used by the analyzer,
# not required for detection, just handy for explore_tier_sets.py to
# print alongside a discovered candidate so you know which class it's
# probably for.
TIER_SET_NAMES_BY_CLASS: dict[str, str] = {
    "Death Knight": "Baleful Grave-Knight's Crucible",
    "Demon Hunter": "Abyssal Doomhound's Pursuit",
    "Druid": "Bark of the Enigmatic Dreamwatcher",
    "Evoker": "Echo of Calamity",
    "Hunter": "Skulking Viper's Ambush",
    "Mage": "Primal Leywarden's Attire",
    "Monk": "Guile of the Monkey King",
    "Paladin": "Radiance of the Consecrated Flame",
    "Priest": "Cosmic Penitent's Raiment",
    "Rogue": "Chosen Bloodslayer's Hexweave",
    "Shaman": "Ophidian Oracle's Prophecy",
    "Warlock": "Damned Necrolyte's Shattered Restraints",
    "Warrior": "Jade Warlord's Dominion",
}

# Hand-curated overrides -- take precedence over the generated file,
# same convention as gear_requirements_config.py's MANUAL_* lists.
MANUAL_TIER_SET_PIECES: list[TierSetPieceDefinition] = [
    # TierSetPieceDefinition(class_name="Paladin", slot=0, item_id=123456, notes="Head"),
]

try:
    _generated_pieces = load_generated_tier_set_pieces()
except TierSetConfigError as exc:
    print(
        "WARNING: tier_sets.generated.json could not be read -- ignoring it "
        "for this run (every player will show 0/5 tier pieces until it's fixed).\n"
        f"{exc}\n"
    )
    _generated_pieces = []

_pieces_by_key: dict[tuple[str, int], TierSetPieceDefinition] = {
    (p.class_name, p.slot): p for p in _generated_pieces
}
for _manual in MANUAL_TIER_SET_PIECES:
    _pieces_by_key[(_manual.class_name, _manual.slot)] = _manual

TRACKED_TIER_SET_PIECES: list[TierSetPieceDefinition] = sorted(
    _pieces_by_key.values(), key=lambda p: (p.class_name, p.slot)
)

try:
    _generated_breakpoints = load_generated_track_breakpoints()
except TierSetConfigError:
    _generated_breakpoints = []  # already warned above for the same file

# Custom breakpoints (if ever saved via `manage_tier_sets.py set-breakpoint`)
# take full precedence over the seed table -- see module docstring for why.
TRACK_BREAKPOINTS: list[TierTrackBreakpoint] = (
    sorted(_generated_breakpoints, key=lambda b: b.min_item_level)
    if _generated_breakpoints
    else SEED_TRACK_BREAKPOINTS
)


def track_letter_for_item_level(item_level: float | None) -> str | None:
    """
    Return the track letter (e.g. "M") for a given item level, per
    TRACK_BREAKPOINTS, or None if item_level is None or below every
    configured breakpoint (e.g. leveling/starter gear below Adventurer).
    """
    if item_level is None:
        return None
    matching = [bp for bp in TRACK_BREAKPOINTS if item_level >= bp.min_item_level]
    if not matching:
        return None
    return max(matching, key=lambda bp: bp.min_item_level).track_letter


def track_color_for_letter(track_letter: str) -> str:
    """Return the color hex for a track letter, or a neutral gray fallback if unrecognized."""
    for bp in TRACK_BREAKPOINTS:
        if bp.track_letter == track_letter:
            return bp.color_hex
    return "#8a8d9c"
