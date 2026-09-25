"""
tier_set_data.py
Loads tracked tier-set pieces from tier_sets.generated.json at import
time, exposing ready-to-use TRACKED_TIER_SET_PIECES and
TRACK_BREAKPOINTS objects. If the generated file is currently broken,
prints a warning and falls back to safe empty/seed defaults instead of
crashing every script that imports it.

===========================================================================
BUGFIX -- 2026-09-25: top 2 ranks of a lower track were being shown as
the NEXT track up (e.g. a fully-upgraded Heroic piece at ilvl 318 or
321 was displayed as "Myth" instead of "Hero")
===========================================================================
ROOT CAUSE (confirmed against two independent, dated sources -- one
explicitly read from LIVE 12.1 client data on 2026-09-16): Blizzard's
own upgrade system defines certain item levels as belonging to BOTH of
two adjacent tracks AT ONCE, by design, since each track's top 2 ranks
are deliberately set to the exact same item level as the next track's
bottom 2 ranks (this is what makes upgrading feel continuous across
raid tiers). The known "crossing" item levels for the current season
are:
    279 = Adventurer 5/6 = Veteran 1/6
    282 = Adventurer 6/6 = Veteran 2/6
    292 = Veteran 5/6    = Champion 1/6
    295 = Veteran 6/6    = Champion 2/6
    305 = Champion 5/6   = Hero 1/6
    308 = Champion 6/6   = Hero 2/6
    318 = Hero 5/6        = Myth 1/6
    321 = Hero 6/6        = Myth 2/6
The OLD track_letter_for_item_level() picked "the highest track whose
floor is <= this item level" -- which, by construction, ALWAYS resolves
this ambiguity in favor of the HIGHER track. That's precisely why the
top two ranks of Hero (318, 321) were shown as Myth.

THE FIX: TierTrackBreakpoint now carries BOTH a floor (min_item_level)
AND a ceiling (max_item_level) per track -- each track's own genuine
6/6 rank value (or, for Myth specifically, no ceiling at all, since it
has no higher track to be confused with, and its own special "Very
Rare" reward ranks go well above its normal 334 cap). Given an item
level, track_letter_for_item_level() now finds every track whose
[floor, ceiling] range contains it -- which will be exactly one track
for the vast majority of item levels, but exactly TWO tracks at the 8
known crossing points above -- and returns the LOWER (more
conservative) of the matches when there's more than one. This directly
fixes the reported symptom: a Heroic-track piece upgraded all the way
to 318 or 321 now correctly shows as Hero, not Myth.

HONEST LIMITATION, still true after this fix: item level ALONE can
never be a perfectly certain signal at those 8 crossing points -- the
SAME ilvl genuinely IS both things simultaneously in Blizzard's own
data model. The only way to fully disambiguate would be reading the
item's actual bonus IDs (which Warcraft Logs' raw COMBATANT_INFO gear
array does capture, per WCL's own event schema), which encode the
item's true track+rank independent of its current item level -- but
this project doesn't have a verified, current-season bonus-ID-to-track
mapping table to build that on top of (and, per this project's
existing "no item database" philosophy, fabricating one risks being
actively wrong rather than just approximate). This fix instead makes a
DELIBERATE, DOCUMENTED CHOICE to default to the lower/more
conservative track whenever ambiguous, since for a raid-gearing check
specifically, under-calling something as "still Hero" is far less
confusing than over-calling a well-upgraded Heroic piece as "Mythic".

===========================================================================
TRACK BREAKPOINTS -- verified 2026-09-25, current as of Patch 12.1
"Midnight Season 2" (Curse of Ula'tek)
===========================================================================
Full per-rank item levels (all 5 tracks x 6 ranks each) cross-checked
against TWO independent, agreeing sources -- one explicitly sourced
from a live 12.1.0.69814 client build dated 2026-09-16, the other
dated 2026-08-09 -- both giving IDENTICAL numbers:
    Adventurer: 266, 269, 272, 276, 279, 282
    Veteran:    279, 282, 285, 289, 292, 295
    Champion:   292, 295, 298, 302, 305, 308
    Hero:       305, 308, 311, 315, 318, 321
    Myth:       318, 321, 324, 328, 331, 334 (+ special reward ranks
                338, 341, 344 -- "Myth 7/8/9 equivalent", used by
                Very Rare drops and the raid's last 2 Mythic bosses)
A third source (dated 2026-08-12, one month earlier) shows slightly
different numbers (Champion 6/6=309 instead of 308, etc.) -- likely
reflecting an earlier point before a documented mid-season item-level
hotfix. The two AGREEING, more-recently-dated sources are used here.

min_item_level for each track = that track's own 1/6 rank value.
max_item_level for each track = that track's own 6/6 rank value,
EXCEPT Myth, which has no ceiling (None) to correctly capture its
above-cap special reward ranks (338/341/344) without needing to name
every one of them individually.
"""
from __future__ import annotations
from tier_set_config_io import (
    TierSetConfigError,
    load_generated_tier_set_pieces,
    load_generated_track_breakpoints,
)
from tier_set_schema import TierSetPieceDefinition, TierTrackBreakpoint

SEED_TRACK_BREAKPOINTS: list[TierTrackBreakpoint] = [
    TierTrackBreakpoint(min_item_level=266, max_item_level=282, track_letter="A", track_name="Adventurer", color_hex="#ffffff"),
    TierTrackBreakpoint(min_item_level=279, max_item_level=295, track_letter="V", track_name="Veteran", color_hex="#1eff00"),
    TierTrackBreakpoint(min_item_level=292, max_item_level=308, track_letter="C", track_name="Champion", color_hex="#0070dd"),
    TierTrackBreakpoint(min_item_level=305, max_item_level=321, track_letter="H", track_name="Hero", color_hex="#a335ee"),
    TierTrackBreakpoint(min_item_level=318, max_item_level=None, track_letter="M", track_name="Myth", color_hex="#ff8000"),
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
    TRACK_BREAKPOINTS, or None if item_level is None or doesn't fall
    within ANY configured track's [min, max] range (e.g. leveling/
    starter gear below every track's floor).

    FIXED: previously returned the HIGHEST track whose floor was <=
    item_level, with no upper bound check at all -- which meant a
    Heroic-track item upgraded to its own top 2 ranks (which share an
    item level with the bottom 2 ranks of Myth) was always misreported
    as Myth. Now finds every track whose [min_item_level,
    max_item_level] range actually CONTAINS item_level -- there will
    be exactly one match for the vast majority of item levels, but
    exactly two matches at the known "crossing" item levels (279, 282,
    292, 295, 305, 308, 318, 321) -- and returns the LOWER (more
    conservative) match when there's more than one, since item level
    alone cannot certainly disambiguate those specific values (see
    module docstring for the full explanation).
    """
    if item_level is None:
        return None
    matching = [bp for bp in TRACK_BREAKPOINTS if bp.contains(item_level)]
    if not matching:
        return None
    lowest_match = min(matching, key=lambda bp: bp.min_item_level)
    return lowest_match.track_letter


def track_color_for_letter(track_letter: str) -> str:
    """Return the color hex for a track letter, or a neutral gray fallback if unrecognized."""
    for bp in TRACK_BREAKPOINTS:
        if bp.track_letter == track_letter:
            return bp.color_hex
    return "#8a8d9c"
