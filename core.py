"""
core.py

MERGED MODULE -- combines what used to be eleven separate foundational
files into one, as part of the PullDoctor file-count reduction pass:

    - data_models.py       (shared dataclasses: Ability/Actor/Fight/Event/GearItem/ParsedFight)
    - log_parser.py        (raw WCL JSON -> this app's own data models)
    - roster.py            (identify real players vs NPCs/pets, resolve pets to owners)
    - time_format.py        (M:SS timestamp formatting, fight-relative ms conversion)
    - difficulty_names.py  (WCL numeric difficulty ID -> human-readable name/color)
    - fight_filters.py     (filter a report's raw fight list before spending API budget)
    - class_colors.py      (official WoW class colors for the HTML report)
    - player_roles.py      (WCL tank/healer/dps grouping -> per-player role lookup)
    - role_reference_data.py (static spec->role classification tables)
    - selection_utils.py   ("1,3,5-8" style selection-string parsing)
    - cli_helpers.py       (shared fight-picking I/O helpers for CLI scripts)

These eleven were grouped together because none of them are specific
to any ONE tracked system (raid cooldowns, gear, tier sets, etc.) --
they're the shared vocabulary and plumbing every other merged module
in this project (fight_analyzers.py, boss_mechanics.py, gear.py,
tier_sets.py, damage_targets.py, raid_cooldowns.py,
defensive_cooldowns.py, consumables.py, reporting.py, automation.py)
imports from.

Sections are ordered by dependency: data_models (no deps) -> log_parser
(depends on data_models) -> roster (depends on data_models) ->
time_format (no deps) -> difficulty_names (no deps) -> fight_filters
(no deps) -> class_colors (no deps) -> role_reference_data (no deps) ->
player_roles (depends on role_reference_data, now just an earlier
section in this same file) -> selection_utils (no deps) -> cli_helpers
(no deps).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


# =======================================================================
# SECTION 1 -- shared data models
# (originally data_models.py)
#
# Shared types used by every module downstream of wcl.py's WCLClient.
# These are the app's own vocabulary -- Section 2 below (log_parser) is
# responsible for turning raw WCL JSON into these; every analyzer,
# report.py, and main.py only ever deal with these types, never raw
# dicts.
# =======================================================================

@dataclass
class Ability:
    id: int
    name: str
    type: Optional[str] = None


@dataclass
class Actor:
    id: int
    name: str
    type: str  # "Player", "NPC", or "Pet"
    subtype: Optional[str] = None
    server: Optional[str] = None
    owner_id: Optional[int] = None


@dataclass
class Fight:
    id: int
    name: str
    difficulty: Optional[int]
    kill: bool
    start_time: int
    end_time: int
    encounter_id: int
    friendly_player_ids: list[int] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return self.end_time - self.start_time


@dataclass
class Event:
    timestamp: int
    event_type: str
    data_type: str
    source_id: Optional[int] = None
    source_name: Optional[str] = None
    target_id: Optional[int] = None
    target_name: Optional[str] = None
    ability_id: Optional[int] = None
    ability_name: Optional[str] = None
    amount: Optional[int] = None
    overheal: Optional[int] = None
    absorbed: Optional[int] = None
    overkill: Optional[int] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class GearItem:
    slot: int
    item_id: int
    quality: int
    item_level: Optional[int] = None
    permanent_enchant_id: Optional[int] = None
    temporary_enchant_id: Optional[int] = None
    gem_ids: list[int] = field(default_factory=list)


@dataclass
class CombatantInfoSnapshot:
    player_id: int
    gear: list[GearItem] = field(default_factory=list)
    aura_ability_ids: set[int] = field(default_factory=set)
    aura_names: set[str] = field(default_factory=set)


@dataclass
class ParsedFight:
    fight: Fight
    actors: dict[int, Actor]
    abilities: dict[int, Ability]
    events: list[Event]
    combatant_info: dict[int, CombatantInfoSnapshot] = field(default_factory=dict)


# =======================================================================
# SECTION 2 -- log parser
# (originally log_parser.py -- turns raw WCL JSON into this app's own
# data models, Section 1 above. No network calls here.)
# =======================================================================

def parse_fight(raw_fight: dict) -> Fight:
    return Fight(
        id=raw_fight["id"], name=raw_fight["name"], difficulty=raw_fight.get("difficulty"),
        kill=bool(raw_fight.get("kill", False)), start_time=raw_fight["startTime"],
        end_time=raw_fight["endTime"], encounter_id=raw_fight.get("encounterID", 0),
        friendly_player_ids=raw_fight.get("friendlyPlayers") or [],
    )


def parse_actors(raw_master_data: dict) -> dict[int, Actor]:
    actors: dict[int, Actor] = {}
    for raw_actor in raw_master_data.get("actors", []):
        actors[raw_actor["id"]] = Actor(
            id=raw_actor["id"], name=raw_actor.get("name", "Unknown"), type=raw_actor.get("type", "Unknown"),
            subtype=raw_actor.get("subType"), server=raw_actor.get("server"), owner_id=raw_actor.get("petOwner"),
        )
    return actors


def parse_abilities(raw_master_data: dict) -> dict[int, Ability]:
    abilities: dict[int, Ability] = {}
    for raw_ability in raw_master_data.get("abilities", []):
        abilities[raw_ability["gameID"]] = Ability(id=raw_ability["gameID"], name=raw_ability.get("name", "Unknown"), type=raw_ability.get("type"))
    return abilities


def parse_event(raw_event: dict, data_type: str, actors: dict[int, Actor], abilities: dict[int, Ability]) -> Event:
    source_id = raw_event.get("sourceID")
    target_id = raw_event.get("targetID")
    ability_id = raw_event.get("abilityGameID")
    source_actor = actors.get(source_id) if source_id is not None else None
    target_actor = actors.get(target_id) if target_id is not None else None
    ability = abilities.get(ability_id) if ability_id is not None else None
    return Event(
        timestamp=raw_event["timestamp"], event_type=raw_event.get("type", "unknown"), data_type=data_type,
        source_id=source_id, source_name=source_actor.name if source_actor else None,
        target_id=target_id, target_name=target_actor.name if target_actor else None,
        ability_id=ability_id, ability_name=ability.name if ability else None,
        amount=raw_event.get("amount"), overheal=raw_event.get("overheal"),
        absorbed=raw_event.get("absorbed"), overkill=raw_event.get("overkill"), raw=raw_event,
    )


def parse_events(raw_events_by_type: dict[str, list[dict]], actors: dict[int, Actor], abilities: dict[int, Ability]) -> list[Event]:
    events: list[Event] = []
    for data_type, raw_events in raw_events_by_type.items():
        for raw_event in raw_events:
            events.append(parse_event(raw_event, data_type, actors, abilities))
    events.sort(key=lambda e: e.timestamp)
    return events


def parse_gear_item(raw_gear_item: dict, slot_index: int) -> GearItem:
    return GearItem(
        slot=slot_index, item_id=raw_gear_item.get("id", 0), quality=raw_gear_item.get("quality", 0),
        item_level=raw_gear_item.get("itemLevel"),
        permanent_enchant_id=raw_gear_item.get("permanentEnchant"),
        temporary_enchant_id=raw_gear_item.get("temporaryEnchant"),
        gem_ids=[g.get("id") for g in raw_gear_item.get("gems", []) if g.get("id")],
    )


_UNRESOLVED_ABILITY_NAME_PREFIX = "Unknown Aura (ability id"


def _resolve_aura_name(raw_aura: dict, ability_id: int, abilities: dict[int, Ability]) -> str:
    """
    Resolve one CombatantInfo aura entry to a display name, trying
    every source available, in order of reliability:
      1. An inline "name" field on the raw aura entry itself, if WCL
         ever includes one directly (some API responses do -- this is
         the most direct source of truth when present, since it can't
         be missing from a SEPARATE query the way master data can be).
      2. The report's masterData.abilities lookup (the normal path).
      3. A synthetic placeholder using the raw numeric ability ID.

    WHY STEP 3 MATTERS (this is the actual bug fix): previously, if
    step 2 failed to find the ability (returned None), the aura was
    SILENTLY DROPPED from aura_names entirely -- with ZERO indication
    anything was wrong. This is a real, demonstrated failure mode:
    Warcraft Logs' masterData.abilities list is built from abilities
    that appear in the report's OWN event stream (casts/damage/healing/
    debuffs) -- a passive, always-on raid consumable buff like a
    Vantus Rune NEVER generates its own Cast/Damage/Healing event (it
    just sits there, applied once, for the whole raid night), so it
    can legitimately be MISSING from masterData.abilities even though
    it is very much genuinely active on every player. Name-based
    consumable detection (consumables.py's analyzer section) would
    then silently report "0 had it" for a buff at 100% raid-wide
    uptime, with no error, warning, or visible symptom anywhere --
    exactly what was reported and reproduced against real report data.

    Falling back to a synthetic name keeps the aura's PRESENCE visible
    (e.g. in a diagnostic dump) instead of invisible, even in the worst
    case where neither an inline name nor a master-data match exists.
    """
    inline_name = raw_aura.get("name")
    if inline_name:
        return inline_name
    ability = abilities.get(ability_id)
    if ability:
        return ability.name
    return f"{_UNRESOLVED_ABILITY_NAME_PREFIX} {ability_id})"


def parse_combatant_info(raw_combatant_info_events: list[dict], abilities: dict[int, Ability]) -> dict[int, CombatantInfoSnapshot]:
    snapshots: dict[int, CombatantInfoSnapshot] = {}
    for raw_event in raw_combatant_info_events:
        player_id = raw_event.get("sourceID")
        if player_id is None:
            continue
        gear = [parse_gear_item(g, idx) for idx, g in enumerate(raw_event.get("gear", []))]
        aura_ability_ids: set[int] = set()
        aura_names: set[str] = set()
        for raw_aura in raw_event.get("auras", []):
            ability_id = raw_aura.get("ability")
            if ability_id is None:
                continue
            aura_ability_ids.add(ability_id)
            # See _resolve_aura_name()'s docstring -- this NEVER silently
            # drops an aura anymore, even if master data can't name it.
            aura_names.add(_resolve_aura_name(raw_aura, ability_id, abilities))
        snapshots[player_id] = CombatantInfoSnapshot(player_id=player_id, gear=gear, aura_ability_ids=aura_ability_ids, aura_names=aura_names)
    return snapshots


def parse_fight_bundle(raw_fight: dict, raw_master_data: dict, raw_events_by_type: dict[str, list[dict]]) -> ParsedFight:
    actors = parse_actors(raw_master_data)
    abilities = parse_abilities(raw_master_data)
    events_by_type = dict(raw_events_by_type)
    raw_combatant_info = events_by_type.pop("CombatantInfo", [])
    return ParsedFight(
        fight=parse_fight(raw_fight), actors=actors, abilities=abilities,
        events=parse_events(events_by_type, actors, abilities),
        combatant_info=parse_combatant_info(raw_combatant_info, abilities),
    )


# =======================================================================
# SECTION 3 -- roster
# (originally roster.py)
#
# Identifies the actual PLAYERS in a fight, as distinct from NPCs,
# bosses, and pets/summons -- and resolves a pet/summon actor back to
# the player who owns it. Every analyzer that reports "who did X"
# should filter or resolve through this section, so pets never show
# up as if they were raid members in their own right.
#
# Pure function over parsed data -- no network calls.
# =======================================================================

def get_players(actors: dict[int, Actor]) -> dict[int, Actor]:
    """Return only the actors that are real players (excludes NPCs and pets)."""
    return {actor_id: actor for actor_id, actor in actors.items() if actor.type == "Player"}


def get_player_roster(parsed_fight: ParsedFight) -> dict[int, Actor]:
    """
    Convenience wrapper: get_players() straight from a ParsedFight.
    NOTE: this returns every player anywhere in the REPORT (masterData is
    report-wide), not necessarily everyone in THIS specific pull -- subs
    or people who left/joined partway through the raid will still show
    up. For an accurate per-fight roster, use get_fight_roster() instead.
    """
    return get_players(parsed_fight.actors)


def get_fight_roster(parsed_fight: ParsedFight) -> dict[int, Actor]:
    """
    Return only the players who were actually present in THIS specific
    fight/pull, using WCL's per-fight friendlyPlayers list.
    """
    present_ids = set(parsed_fight.fight.friendly_player_ids)
    return {
        actor_id: actor
        for actor_id, actor in parsed_fight.actors.items()
        if actor_id in present_ids and actor.type == "Player"
    }


def is_player(actor_id: int | None, actors: dict[int, Actor]) -> bool:
    """True if actor_id refers to a real player (not an NPC or pet)."""
    if actor_id is None:
        return False
    actor = actors.get(actor_id)
    return actor is not None and actor.type == "Player"


def resolve_to_player(
    actor_id: int | None, actors: dict[int, Actor]
) -> Actor | None:
    """
    Resolve any actor ID to the player it should be credited to:
      - a real player  -> returns that player's Actor
      - a pet/summon with a known owner -> returns the OWNER's Actor
      - an NPC, boss, or a pet with no resolvable owner -> returns None
    """
    if actor_id is None:
        return None
    actor = actors.get(actor_id)
    if actor is None:
        return None
    if actor.type == "Player":
        return actor
    if actor.owner_id is not None:
        owner = actors.get(actor.owner_id)
        if owner is not None and owner.type == "Player":
            return owner
    return None


def resolve_to_player_id_name(
    actor_id: int | None, fallback_name: str | None, actors: dict[int, Actor]
) -> tuple[int | None, str | None]:
    """
    Same resolution as resolve_to_player, but returns an (id, name) tuple
    instead of an Actor, falling back to (actor_id, fallback_name) when
    resolution fails.
    """
    player = resolve_to_player(actor_id, actors)
    if player is not None:
        return player.id, player.name
    return actor_id, fallback_name


# =======================================================================
# SECTION 4 -- time formatting
# (originally time_format.py)
#
# One shared helper for rendering fight-relative millisecond timestamps
# as M:SS (e.g. 183000 -> "3:03") instead of raw seconds (e.g.
# "183.0s"), used consistently across report.py/reporting.py's
# analyzers, boss_mechanics.py, raid_cooldowns.py,
# defensive_cooldowns.py, fight_analyzers.py, and the HTML report.
# =======================================================================

def format_timestamp(ms: float) -> str:
    """
    Format a fight-relative timestamp in milliseconds as M:SS.
    Seconds are zero-padded to 2 digits; minutes are not padded and can
    exceed 59 (e.g. a 65-minute fight shows "65:07", not "1:05:07"),
    since encounters are never long enough to need an hours place, and
    this keeps the format consistent/sortable as plain text. Negative
    or garbage input is clamped to 0:00 rather than raising, so a
    single bad event can't crash an entire report render.
    """
    total_seconds = max(0, ms) / 1000.0
    minutes = int(total_seconds // 60)
    seconds = int(total_seconds % 60)
    return f"{minutes}:{seconds:02d}"


def fight_relative_ms(raw_ms: float, fight_start_time: float, fight_duration_ms: float) -> int:
    """
    Convert a RAW, report-relative timestamp (the convention used by
    Event.timestamp, and therefore by anything copied straight from it --
    confirmed for CooldownUsage.cast_timestamps in raid_cooldowns.py/
    defensive_cooldowns.py's shared engine sections, and ASSUMED (not
    independently verified -- flagged for a follow-up check) for
    DefensiveWindow.cast_timestamp in defensive_cooldowns.py's damage-
    prevention analyzer section, since it's built from the same
    underlying Casts events) into a FIGHT-relative offset in
    milliseconds, clamped to [0, fight_duration_ms] so a slightly-off
    timestamp can't push a marker outside its own timeline strip.
    fight_analyzers.py's death analyzer section already does this exact
    subtraction inline (time_into_fight_ms = death.timestamp -
    fight.start_time) -- this helper just makes the same conversion
    reusable and clamped for the HTML report's timeline-strip markers.
    """
    relative = raw_ms - fight_start_time
    return int(max(0, min(fight_duration_ms, relative)))


# =======================================================================
# SECTION 5 -- difficulty names
# (originally difficulty_names.py)
#
# Maps Warcraft Logs' numeric raid Fight.difficulty ID to a human-
# readable name (LFR/Normal/Heroic/Mythic), plus a suggested display
# color for each -- used by reporting.py's text/Markdown output and the
# HTML report (a colored badge, similar to the existing KILL/WIPE badge)
# so it's never ambiguous which difficulty a given pull was.
#
# IMPORTANT: WCL's numeric difficulty IDs are its OWN internal scheme --
# they are NOT the same numbers WoW's client-side API uses for the same
# concept (e.g. Blizzard's own SetRaidDifficultyID uses 14/15/16 for
# Normal/Heroic/Mythic; WCL uses 3/4/5). Never assume a WCL difficulty
# number matches anything you might see referenced elsewhere for
# "difficulty ID" -- always go through this mapping.
#
# Verified against a technical WCL API reference guide (2026-09-21):
#     WCL difficulty 3 -> Normal
#     WCL difficulty 4 -> Heroic
#     WCL difficulty 5 -> Mythic
# difficulty 1 (LFR) is included based on extremely broad, consistent
# usage across essentially every third-party WCL tool/wrapper, though it
# was not explicitly present in the one source checked for this file --
# flagged here so it can be corrected easily if a report ever shows an
# LFR pull with a different number. Because DIFFICULTY_NAMES is a plain
# dict, fixing a wrong ID (if this one ever turns out to be wrong) is a
# one-line change, and any raid whose real difficulty ID isn't in this
# dict at all falls back to a safe, honest "Difficulty <N>" label
# instead of silently mislabeling it -- see difficulty_name() below.
#
# Dungeon-only difficulty IDs (Mythic+ keystone levels, Timewalking,
# etc.) are deliberately NOT included -- this project is a raid analysis
# toolkit, and a fight with one of those difficulty IDs will simply show
# as "Difficulty <N>" rather than a guessed-wrong raid name.
# =======================================================================

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

DEFAULT_DIFFICULTY_COLOR = "#9a9db0"


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
    """Suggested display color for a Fight.difficulty value. Falls back to DEFAULT_DIFFICULTY_COLOR for anything unrecognized/missing."""
    if difficulty is None:
        return DEFAULT_DIFFICULTY_COLOR
    return DIFFICULTY_COLORS.get(difficulty, DEFAULT_DIFFICULTY_COLOR)


# =======================================================================
# SECTION 6 -- fight filters
# (originally fight_filters.py)
#
# Filters a report's RAW fight list (from WCLClient.get_report_fights(),
# which is one cheap call regardless of how many fights the report has)
# down to just the fights worth spending further API budget on --
# BEFORE any per-fight event data is fetched. This is the entire point:
# every fight you skip here is one full set of Casts/Healing/
# DamageTaken/DamageDone/Deaths/CombatantInfo/Debuffs queries you never
# have to make.
#
# Two independent criteria, both opt-out-able:
#   1. only_boss_fights -- Warcraft Logs marks every trash pull with
#      encounterID == 0; a real boss pull always has a non-zero
#      encounterID identifying which boss it was. Trash pulls carry
#      essentially zero analytical value for this project (no consumable
#      coverage worth checking, no avoidable-mechanic config exists for
#      "trash", cooldown usage against trash isn't meaningful) -- so
#      they're excluded by default.
#   2. min_duration_seconds -- filters out near-instant wipes/resets
#      (e.g. someone pulls early, tank dies in 3 seconds, raid resets).
#      These ARE real boss pulls (non-zero encounterID) but are too short
#      to contain anything worth analyzing, and reliably show up as
#      "everyone missing every consumable/cooldown" noise if included.
#      Default is 15 seconds, matching the threshold requested when this
#      section was built, but fully overridable per call.
#
# Both filters operate on the RAW dict shape returned by
# WCLClient.get_report_fights() (keys: id, name, difficulty, kill,
# startTime, endTime, encounterID, friendlyPlayers) -- deliberately
# BEFORE Section 2's Fight dataclass exists for these entries, since the
# whole point is to decide which fights are even worth building a
# Fight/ParsedFight for in the first place.
#
# Pure function over already-fetched data -- no network calls.
# =======================================================================

DEFAULT_MIN_DURATION_SECONDS = 15.0


@dataclass
class FightFilterCriteria:
    only_boss_fights: bool = True
    min_duration_seconds: float = DEFAULT_MIN_DURATION_SECONDS

    @property
    def is_a_no_op(self) -> bool:
        """True if this criteria would let every fight through unchanged -- useful for skipping filter-summary output entirely."""
        return not self.only_boss_fights and self.min_duration_seconds <= 0


@dataclass
class SkippedFight:
    fight_id: int
    name: str
    reason: str  # "trash" or "too_short"
    duration_seconds: float


@dataclass
class FightFilterResult:
    kept: list[dict] = field(default_factory=list)
    skipped: list[SkippedFight] = field(default_factory=list)

    @property
    def skipped_trash_count(self) -> int:
        return sum(1 for s in self.skipped if s.reason == "trash")

    @property
    def skipped_too_short_count(self) -> int:
        return sum(1 for s in self.skipped if s.reason == "too_short")


def filter_fights(raw_fights: list[dict], criteria: FightFilterCriteria) -> FightFilterResult:
    """
    Apply criteria to a report's raw fight list. A fight failing BOTH
    checks is recorded under "trash" (checked first) rather than
    double-counted -- in practice trash pulls are also usually short,
    so this avoids a fight showing up as "skipped for two reasons" in
    the summary.
    """
    result = FightFilterResult()
    for raw_fight in raw_fights:
        encounter_id = raw_fight.get("encounterID", 0) or 0
        duration_seconds = (raw_fight.get("endTime", 0) - raw_fight.get("startTime", 0)) / 1000
        if criteria.only_boss_fights and encounter_id == 0:
            result.skipped.append(SkippedFight(
                fight_id=raw_fight["id"], name=raw_fight.get("name", "Unknown"),
                reason="trash", duration_seconds=duration_seconds,
            ))
            continue
        if duration_seconds < criteria.min_duration_seconds:
            result.skipped.append(SkippedFight(
                fight_id=raw_fight["id"], name=raw_fight.get("name", "Unknown"),
                reason="too_short", duration_seconds=duration_seconds,
            ))
            continue
        result.kept.append(raw_fight)
    return result


def format_filter_summary(result: FightFilterResult, criteria: FightFilterCriteria) -> str:
    """Human-readable summary of what was kept/skipped and why -- shown before any expensive per-fight fetching begins."""
    total = len(result.kept) + len(result.skipped)
    if not result.skipped:
        return f"{len(result.kept)}/{total} fight(s) will be fetched (no fights filtered out)."
    lines = [f"{len(result.kept)}/{total} fight(s) will be fetched (API budget saved on {len(result.skipped)} skipped):"]
    if result.skipped_trash_count:
        lines.append(f"  {result.skipped_trash_count} trash pull(s) skipped (encounterID == 0)")
    if result.skipped_too_short_count:
        lines.append(
            f"  {result.skipped_too_short_count} short boss pull(s) skipped "
            f"(< {criteria.min_duration_seconds:.0f}s -- likely an early reset/wipe)"
        )
    return "\n".join(lines)


# =======================================================================
# SECTION 7 -- class colors
# (originally class_colors.py)
#
# Official WoW class colors (Blizzard's own RAID_CLASS_COLORS, current
# patch, verified via Wowhead/Wowpedia), used to color-code player names
# and meter bars in the HTML report.
#
# Warcraft Logs' own data (both Actor.subtype from masterData.actors,
# and the "icon" field elsewhere, e.g. "DeathKnight-Blood") is
# INCONSISTENT about whether multi-word class names contain a space --
# sometimes "DeathKnight", sometimes "Death Knight", depending on which
# endpoint/field it came from. normalize_class_name() handles both
# forms so get_class_color() works regardless of which one you feed it.
# =======================================================================

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
DEFAULT_CLASS_COLOR = "#9a9db0"

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
    """Look up a class's official color by name (either 'DeathKnight' or 'Death Knight' form). Falls back to DEFAULT_CLASS_COLOR."""
    normalized = normalize_class_name(class_name)
    if normalized is None:
        return DEFAULT_CLASS_COLOR
    return CLASS_COLORS.get(normalized, DEFAULT_CLASS_COLOR)


# =======================================================================
# SECTION 8 -- role reference data
# (originally role_reference_data.py -- static role/spec classification
# tables, keyed by WCL's "Class-Spec" icon string)
# =======================================================================

TANK_SPECS = {
    "Warrior-Protection",
    "Paladin-Protection",
    "DeathKnight-Blood",
    "Monk-Brewmaster",
    "Druid-Guardian",
    "DemonHunter-Vengeance",
}

HEALER_SPECS = {
    "Paladin-Holy",
    "Priest-Holy",
    "Priest-Discipline",
    "Druid-Restoration",
    "Shaman-Restoration",
    "Monk-Mistweaver",
    "Evoker-Preservation",
}

MELEE_SPECS = {
    "Warrior-Arms", "Warrior-Fury", "Paladin-Retribution",
    "DeathKnight-Frost", "DeathKnight-Unholy",
    "Rogue-Assassination", "Rogue-Outlaw", "Rogue-Subtlety",
    "Hunter-Survival", "Shaman-Enhancement", "Druid-Feral",
    "Monk-Windwalker", "DemonHunter-Havoc",
}

RANGED_SPECS = {
    "Hunter-BeastMastery", "Hunter-Marksmanship",
    "Mage-Arcane", "Mage-Fire", "Mage-Frost",
    "Warlock-Affliction", "Warlock-Demonology", "Warlock-Destruction",
    "Priest-Shadow", "Shaman-Elemental", "Druid-Balance",
    "Evoker-Devastation", "Evoker-Augmentation",
}


def classify_dps_spec(spec_icon: str | None) -> str:
    if spec_icon in MELEE_SPECS:
        return "melee"
    return "ranged"


# =======================================================================
# SECTION 9 -- player roles
# (originally player_roles.py -- converts WCL's tank/healer/dps
# playerDetails grouping into a per-player role lookup. Depends on
# Section 8 above, now just an earlier section in this same file
# instead of a separate role_reference_data.py import.)
# =======================================================================

@dataclass
class PlayerRole:
    player_id: int
    player_name: str
    role: str  # "tank", "healer", "melee", "ranged"
    spec_icon: str | None = None


def _classify_player_role(entry: dict, bucket_role: str) -> str:
    spec_icon = entry.get("icon")
    if spec_icon in TANK_SPECS:
        return "tank"
    if spec_icon in HEALER_SPECS:
        return "healer"
    if bucket_role == "tank":
        return "tank"
    if bucket_role == "healer":
        return "healer"
    return classify_dps_spec(spec_icon)


def parse_player_roles(raw_player_details: dict) -> dict[int, PlayerRole]:
    roles: dict[int, PlayerRole] = {}
    for entry in raw_player_details.get("tanks", []) or []:
        spec_icon = entry.get("icon")
        roles[entry["id"]] = PlayerRole(
            player_id=entry["id"], player_name=entry.get("name", "Unknown"),
            role=_classify_player_role(entry, "tank"), spec_icon=spec_icon,
        )
    for entry in raw_player_details.get("healers", []) or []:
        spec_icon = entry.get("icon")
        roles[entry["id"]] = PlayerRole(
            player_id=entry["id"], player_name=entry.get("name", "Unknown"),
            role=_classify_player_role(entry, "healer"), spec_icon=spec_icon,
        )
    for entry in raw_player_details.get("dps", []) or []:
        spec_icon = entry.get("icon")
        roles[entry["id"]] = PlayerRole(
            player_id=entry["id"], player_name=entry.get("name", "Unknown"),
            role=_classify_player_role(entry, "dps"), spec_icon=spec_icon,
        )
    return roles


# =======================================================================
# SECTION 10 -- selection parsing
# (originally selection_utils.py)
#
# Shared "1,3,5-8" style selection-string parsing, used by every
# _cli.py's "explore"/interactive subcommand in this project, so the
# parsing rules only need to live in one place.
# =======================================================================

def parse_selection(text: str, maximum: int) -> list[int]:
    """
    Parse a selection string like '1,3,5-8' into sorted, ZERO-based
    indexes in range [0, maximum). Raises ValueError with a readable
    message for anything malformed or out of range. An empty/blank
    string returns an empty list (the caller's "select nothing" case).
    """
    chosen: set[int] = set()
    compact = text.strip().replace(" ", "")
    if not compact:
        return []
    for part in compact.split(","):
        if not part:
            continue
        if re.fullmatch(r"\d+", part):
            start = end = int(part)
        elif re.fullmatch(r"\d+-\d+", part):
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                start, end = end, start
        else:
            raise ValueError(f"Invalid selection component: {part!r}")
        if start < 1 or end > maximum:
            raise ValueError(f"Selection must be between 1 and {maximum}.")
        chosen.update(range(start - 1, end))
    return sorted(chosen)


# =======================================================================
# SECTION 11 -- CLI fight-picking helpers
# (originally cli_helpers.py)
#
# Small shared helpers for CLI scripts that need a person to pick a
# fight from a report. Not an analyzer -- plain I/O helpers.
#
# print_fight_list()/prompt_for_fight_id()/resolve_fight_id() accept an
# optional `all_raw_fights` -- the FULL unfiltered fight list -- so that
# when `fights` has already been filtered (via Section 6's
# fight_filters), the displayed numbering can still show a short note
# like "(3 trash/short pulls hidden)" rather than silently renumbering
# fights in a way that might confuse someone cross-referencing against
# the Warcraft Logs website's own fight list. This is purely a display
# nicety; the filtering decision itself always happens in Section 6,
# before these functions are ever called.
# =======================================================================

def print_fight_list(fights: list[dict], all_raw_fights: list[dict] | None = None) -> None:
    for fight in fights:
        status = "KILL" if fight["kill"] else "wipe"
        duration_s = (fight["endTime"] - fight["startTime"]) / 1000
        print(f"  #{fight['id']:>3}  {fight['name']:<30} {status}  ({duration_s:.0f}s)")
    if all_raw_fights is not None and len(all_raw_fights) > len(fights):
        hidden = len(all_raw_fights) - len(fights)
        print(f"  ({hidden} trash/short pull(s) hidden -- see --include-trash / --min-duration to change this)")


def prompt_for_fight_id(fights: list[dict], all_raw_fights: list[dict] | None = None) -> int:
    print_fight_list(fights, all_raw_fights=all_raw_fights)
    valid_ids = {f["id"] for f in fights}
    while True:
        choice = input("\nWhich fight number? ").strip()
        if choice.isdigit() and int(choice) in valid_ids:
            return int(choice)
        print(f"'{choice}' isn't one of the fight numbers listed above -- try again.")


def resolve_fight_id(
    fights: list[dict],
    explicit_fight_id: int | None,
    all_raw_fights: list[dict] | None = None,
) -> int:
    """
    Return explicit_fight_id if it's valid for this (possibly filtered)
    fight list; otherwise prompt interactively. If explicit_fight_id
    was filtered OUT by Section 6's fight_filters (e.g. it's a trash
    pull or a too-short pull) but genuinely exists in the report, this
    gives a clear explanation rather than a bare "not found" -- since
    that's a likely point of confusion once fight-filtering is involved.
    """
    valid_ids = {f["id"] for f in fights}
    if explicit_fight_id is not None:
        if explicit_fight_id not in valid_ids:
            if all_raw_fights is not None:
                raw_match = next((f for f in all_raw_fights if f["id"] == explicit_fight_id), None)
                if raw_match is not None:
                    duration_s = (raw_match["endTime"] - raw_match["startTime"]) / 1000
                    is_trash = (raw_match.get("encounterID", 0) or 0) == 0
                    reason = "it's a trash pull" if is_trash else f"it's only {duration_s:.0f}s long"
                    print(
                        f"Fight #{explicit_fight_id} exists in this report, but was filtered out "
                        f"because {reason}. Use --include-trash and/or --min-duration 0 to include it."
                    )
                    raise SystemExit(1)
            print(f"Fight #{explicit_fight_id} not found in this report. Available fights:")
            print_fight_list(fights, all_raw_fights=all_raw_fights)
            raise SystemExit(1)
        return explicit_fight_id
    return prompt_for_fight_id(fights, all_raw_fights=all_raw_fights)
