"""
fight_analyzers.py

MERGED MODULE -- combines what used to be four separate "core fight
stats" analyzers into one, as part of the PullDoctor file-count
reduction pass:

    - damage_analyzer.py       (damage TAKEN: per-player + per-source + biggest hits)
    - damage_done_analyzer.py  (damage DONE: per-player DPS view)
    - death_analyzer.py        (deaths, with damage/healing context window)
    - healing_analyzer.py      (healing: per-healer + per-target received)

These four were natural candidates for a single module: each is a
short, independent, pure-function analyzer over one ParsedFight, each
already cross-referenced the others in their own "CHANGED" docstring
notes (all four format their header timestamps the same way, via
format_timestamp(), explicitly "matching report.py,
death_analyzer.py, cooldown_analyzer.py, ..." etc.), and none of them
depend on each other's internals -- they only share the same handful
of upstream imports, which are now imported once at the top of this
file instead of four times.

IMPORT FIX (this update): this file previously did `import roster`,
`from data_models import Event, ParsedFight`, and `from time_format
import format_timestamp` at module scope -- all three of those
standalone modules have since been folded into core.py (Sections 3, 1,
and 4 respectively), so every one of those imports would now fail at
import time and take down main.py / generate_html_report.py /
automation.py with it (all three import run_all_analyzers, which calls
into this module).

Every symbol is imported from core.py instead, and the three
`roster.`-prefixed call sites (roster.is_player,
roster.resolve_to_player, roster.resolve_to_player_id_name) now call
the same functions directly, unprefixed. Every function/argument
signature is otherwise unchanged -- only the import path and the
dropped module prefix changed.

Sections below are ordered: damage taken -> damage done -> deaths ->
healing, matching the rough order these appear in a typical pull
report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core import (
    Event,
    ParsedFight,
    format_timestamp,
    is_player,
    resolve_to_player,
    resolve_to_player_id_name,
)


# =======================================================================
# SECTION 1 -- damage TAKEN
# (originally damage_analyzer.py)
#
# Per-player output (total damage taken, DTPS, breakdown by ability and
# by source) and per-source output (which boss/NPC/ability is dealing
# the most damage). Also surfaces the single biggest hits.
# =======================================================================


@dataclass
class DamageTakenSummary:
    target_id: int | None
    target_name: str | None
    total_damage_taken: int = 0
    damage_by_ability: dict[str, int] = field(default_factory=dict)
    damage_by_source: dict[str, int] = field(default_factory=dict)

    def dtps(self, fight_duration_ms: int) -> float:
        return self.total_damage_taken / (fight_duration_ms / 1000) if fight_duration_ms > 0 else 0.0


@dataclass
class DamageSourceSummary:
    source_id: int | None
    source_name: str | None
    total_damage_dealt: int = 0
    damage_by_ability: dict[str, int] = field(default_factory=dict)
    damage_by_target: dict[str, int] = field(default_factory=dict)


def _is_damage_taken_event(event: Event, actors) -> bool:
    return event.data_type == "DamageTaken" and is_player(event.target_id, actors)


def analyze_damage_taken(parsed_fight: ParsedFight) -> list[DamageTakenSummary]:
    """
    Build a DamageTakenSummary per player/actor who took any damage,
    sorted by total damage taken descending. This is the FULL roster
    view (e.g. tanks included) -- see get_biggest_hits() for the
    separate, optionally-filtered "biggest single hits" view.
    """
    summaries: dict[int, DamageTakenSummary] = {}
    for event in parsed_fight.events:
        if not _is_damage_taken_event(event, parsed_fight.actors):
            continue
        target_id = event.target_id
        if target_id not in summaries:
            summaries[target_id] = DamageTakenSummary(target_id=target_id, target_name=event.target_name)
        summary = summaries[target_id]
        amount = event.amount or 0
        summary.total_damage_taken += amount
        ability_name = event.ability_name or "Unknown"
        summary.damage_by_ability[ability_name] = summary.damage_by_ability.get(ability_name, 0) + amount
        source_name = event.source_name or "Unknown"
        summary.damage_by_source[source_name] = summary.damage_by_source.get(source_name, 0) + amount
    return sorted(summaries.values(), key=lambda s: s.total_damage_taken, reverse=True)


def analyze_damage_sources(parsed_fight: ParsedFight) -> list[DamageSourceSummary]:
    """Build a DamageSourceSummary per damage source (boss, add, etc.), sorted by total damage dealt descending."""
    summaries: dict[int, DamageSourceSummary] = {}
    for event in parsed_fight.events:
        if not _is_damage_taken_event(event, parsed_fight.actors):
            continue
        source_id = event.source_id
        if source_id not in summaries:
            summaries[source_id] = DamageSourceSummary(source_id=source_id, source_name=event.source_name)
        summary = summaries[source_id]
        amount = event.amount or 0
        summary.total_damage_dealt += amount
        ability_name = event.ability_name or "Unknown"
        summary.damage_by_ability[ability_name] = summary.damage_by_ability.get(ability_name, 0) + amount
        target_name = event.target_name or "Unknown"
        summary.damage_by_target[target_name] = summary.damage_by_target.get(target_name, 0) + amount
    return sorted(summaries.values(), key=lambda s: s.total_damage_dealt, reverse=True)


def get_biggest_hits(
    parsed_fight: ParsedFight,
    top_n: int = 5,
    exclude_player_ids: set[int] | None = None,
) -> list[Event]:
    """
    Return the top_n single damage-taken events by amount, across all
    players.

    exclude_player_ids: optional set of player actor IDs to leave out
    of this ranking entirely -- e.g. tanks, who routinely take the
    single biggest hits in a fight simply by doing their job (standing
    in melee eating auto-attacks/boss abilities), which would otherwise
    dominate a "biggest hits" list and crowd out hits that are actually
    noteworthy for a non-tank (a healer/DPS taking a hit that size is
    usually far more interesting -- a mechanic they should have avoided,
    or a spike that needed a defensive/external).

    This is a GENERIC exclusion parameter, not hardcoded to "tank" --
    the caller decides which player IDs to pass in (main.py passes the
    set of players WCL's playerDetails currently classifies as tanks
    for this fight). Excluded players are NOT removed from
    analyze_damage_taken()'s per-player damage-taken table -- only from
    this specific top-N ranking.
    """
    exclude_player_ids = exclude_player_ids or set()
    damage_events = [
        e for e in parsed_fight.events
        if _is_damage_taken_event(e, parsed_fight.actors) and e.target_id not in exclude_player_ids
    ]
    damage_events.sort(key=lambda e: e.amount or 0, reverse=True)
    return damage_events[:top_n]


def summarize_damage_taken(parsed_fight: ParsedFight, summaries: list[DamageTakenSummary]) -> str:
    """Return a short human-readable per-player damage-taken summary, DTPS-ranked."""
    if not summaries:
        return f"{parsed_fight.fight.name}: no damage-taken events recorded."
    duration_ms = parsed_fight.fight.duration_ms
    lines = [f"{parsed_fight.fight.name} -- damage taken ({format_timestamp(duration_ms)}):"]
    for summary in summaries:
        lines.append(
            f"  {(summary.target_name or 'Unknown')[:15]:<15} "
            f"{summary.dtps(duration_ms):>8.0f} DTPS  "
            f"({summary.total_damage_taken:>10,} total)"
        )
    return "\n".join(lines)


# =======================================================================
# SECTION 2 -- damage DONE
# (originally damage_done_analyzer.py)
#
# Per-player DPS output (total damage done, breakdown by ability,
# breakdown by target hit). This is the "who's dealing damage to the
# boss" view, as opposed to Section 1 above which covers damage TAKEN.
#
# Pet/summon damage is folded into the owning player via
# core.resolve_to_player, so pets never appear as separate entries.
# Damage from sources that aren't players and can't be resolved to an
# owning player (bosses, adds hitting each other, etc.) is skipped
# entirely -- this section is specifically a player DPS view.
# =======================================================================


@dataclass
class DamageDoneSummary:
    player_id: int | None
    player_name: str | None
    total_damage_done: int = 0
    damage_by_ability: dict[str, int] = field(default_factory=dict)
    damage_by_target: dict[str, int] = field(default_factory=dict)

    def dps(self, fight_duration_ms: int) -> float:
        return self.total_damage_done / (fight_duration_ms / 1000) if fight_duration_ms > 0 else 0.0


def _is_damage_done_event(event) -> bool:
    return event.data_type == "DamageDone"


def analyze_damage_done(parsed_fight: ParsedFight) -> list[DamageDoneSummary]:
    """Build a DamageDoneSummary per player (pets folded into their owner), sorted by total damage done descending."""
    summaries: dict[int, DamageDoneSummary] = {}
    for event in parsed_fight.events:
        if not _is_damage_done_event(event):
            continue
        player = resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue
        if player.id not in summaries:
            summaries[player.id] = DamageDoneSummary(player_id=player.id, player_name=player.name)
        summary = summaries[player.id]
        amount = event.amount or 0
        summary.total_damage_done += amount
        ability_name = event.ability_name or "Unknown"
        summary.damage_by_ability[ability_name] = summary.damage_by_ability.get(ability_name, 0) + amount
        target_name = event.target_name or "Unknown"
        summary.damage_by_target[target_name] = summary.damage_by_target.get(target_name, 0) + amount
    return sorted(summaries.values(), key=lambda s: s.total_damage_done, reverse=True)


def summarize_damage_done(parsed_fight: ParsedFight, summaries: list[DamageDoneSummary]) -> str:
    """Return a short human-readable per-player DPS summary, DPS-ranked."""
    if not summaries:
        return f"{parsed_fight.fight.name}: no damage-done events recorded."
    duration_ms = parsed_fight.fight.duration_ms
    lines = [f"{parsed_fight.fight.name} -- damage done ({format_timestamp(duration_ms)}):"]
    for summary in summaries:
        lines.append(
            f"  {(summary.player_name or 'Unknown')[:15]:<15} "
            f"{summary.dps(duration_ms):>8.0f} DPS  "
            f"({summary.total_damage_done:>10,} total)"
        )
    return "\n".join(lines)


# =======================================================================
# SECTION 3 -- deaths
# (originally death_analyzer.py)
#
# Builds a short window of context around each death: damage landing
# on the victim beforehand, and healing they were actually receiving,
# so you can tell "unhealable damage spike" apart from "healer missed
# it". Pure function over a ParsedFight -- no other analyzer deps.
# =======================================================================

DEFAULT_CONTEXT_WINDOW_MS = 5000


@dataclass
class DamageInstance:
    timestamp: int
    source_name: str | None
    ability_name: str | None
    amount: int


@dataclass
class HealInstance:
    timestamp: int
    source_name: str | None
    ability_name: str | None
    amount: int
    overheal: int


@dataclass
class DeathReport:
    victim_id: int | None
    victim_name: str | None
    timestamp: int
    time_into_fight_ms: int
    killing_ability_name: str | None
    killing_blow_source_name: str | None
    damage_taken_before: list[DamageInstance] = field(default_factory=list)
    healing_received_before: list[HealInstance] = field(default_factory=list)

    @property
    def total_damage_taken_in_window(self) -> int:
        return sum(d.amount for d in self.damage_taken_before)

    @property
    def total_effective_healing_in_window(self) -> int:
        # WCL's Healing event `amount` is ALREADY effective (net)
        # healing -- `overheal` is a separate, ADDITIVE figure for the
        # portion wasted on top of that, not a slice carved out of
        # `amount`. A formula like (amount - overheal) would double-
        # subtract overheal and could go negative, which is physically
        # impossible for real healing.
        return sum(h.amount for h in self.healing_received_before)


def _is_death_event(event: Event, actors) -> bool:
    is_death = event.data_type == "Deaths" or event.event_type == "death"
    return is_death and is_player(event.target_id, actors)


# Ability names that carry NO real information about what killed the
# player -- either because core.py's log-parser section couldn't
# resolve the ID at all (ability_name is None/empty), OR because
# WCL/the game itself logged a real but generic placeholder name for
# the death event (confirmed against a real report where "Unknown
# Ability" was a literal, non-empty ability_name during a mass-wipe
# from a hard enrage).
_UNINFORMATIVE_KILLING_ABILITY_NAMES = {
    "unknown ability",
    "unknown",
    "",
}


def _resolve_killing_ability(death: Event, damage_before: list[DamageInstance]) -> str | None:
    """
    Fall back to the ability from the LAST DamageTaken hit on this
    victim in the context window immediately before death, when
    death.ability_name is missing OR is one of the uninformative
    placeholder strings above. damage_before is already sorted
    chronologically, so its last entry is the most recent hit.

    Returns the original (possibly uninformative) name -- or None --
    if there's no usable damage to fall back to, rather than
    fabricating a guess.
    """
    name = death.ability_name
    is_uninformative = not name or name.strip().lower() in _UNINFORMATIVE_KILLING_ABILITY_NAMES
    if not is_uninformative:
        return name
    if damage_before:
        fallback_name = damage_before[-1].ability_name
        if fallback_name and fallback_name.strip().lower() not in _UNINFORMATIVE_KILLING_ABILITY_NAMES:
            return fallback_name
    return name


def analyze_deaths(
    parsed_fight: ParsedFight,
    context_window_ms: int = DEFAULT_CONTEXT_WINDOW_MS,
) -> list[DeathReport]:
    """
    Find every death in the fight and build a DeathReport for each,
    including damage-taken and healing-received in the context window
    immediately before it. Returns reports sorted chronologically.
    """
    death_events = [e for e in parsed_fight.events if _is_death_event(e, parsed_fight.actors)]
    reports: list[DeathReport] = []
    for death in death_events:
        victim_id = death.target_id
        window_start = death.timestamp - context_window_ms
        damage_before = [
            DamageInstance(
                timestamp=e.timestamp, source_name=e.source_name,
                ability_name=e.ability_name, amount=e.amount or 0,
            )
            for e in parsed_fight.events
            if e.data_type == "DamageTaken" and e.target_id == victim_id
            and window_start <= e.timestamp <= death.timestamp
        ]
        damage_before.sort(key=lambda d: d.timestamp)
        healing_before = [
            HealInstance(
                timestamp=e.timestamp, source_name=e.source_name,
                ability_name=e.ability_name, amount=e.amount or 0, overheal=e.overheal or 0,
            )
            for e in parsed_fight.events
            if e.data_type == "Healing" and e.target_id == victim_id
            and window_start <= e.timestamp <= death.timestamp
        ]
        reports.append(
            DeathReport(
                victim_id=victim_id, victim_name=death.target_name, timestamp=death.timestamp,
                time_into_fight_ms=death.timestamp - parsed_fight.fight.start_time,
                killing_ability_name=_resolve_killing_ability(death, damage_before),
                killing_blow_source_name=death.source_name,
                damage_taken_before=damage_before, healing_received_before=healing_before,
            )
        )
    reports.sort(key=lambda r: r.timestamp)
    return reports


def summarize_wipe(parsed_fight: ParsedFight, death_reports: list[DeathReport]) -> str:
    """Return a short human-readable summary line for a wipe: who died, in what order, how far into the fight."""
    if not death_reports:
        return f"{parsed_fight.fight.name}: no deaths recorded."
    lines = [f"{parsed_fight.fight.name} -- {len(death_reports)} death(s):"]
    for report in death_reports:
        time_label = format_timestamp(report.time_into_fight_ms)
        lines.append(
            f"  {time_label:>6}  {(report.victim_name or 'Unknown')[:20]:<20} "
            f"died to {report.killing_ability_name or 'Unknown'} "
            f"(dmg in {DEFAULT_CONTEXT_WINDOW_MS // 1000}s before: "
            f"{report.total_damage_taken_in_window}, "
            f"effective healing: {report.total_effective_healing_in_window})"
        )
    return "\n".join(lines)


# =======================================================================
# SECTION 4 -- healing
# (originally healing_analyzer.py)
#
# Per-healer output (total effective healing, overheal, HPS, breakdown
# by spell) and per-target output (who received how much healing, and
# from whom).
#
# WCL's `amount` field on a Healing event is already the EFFECTIVE
# healing done (HP actually restored); `overheal` is a SEPARATE,
# additive figure for the portion that was wasted on top of that:
#     effective healing   = amount              (NOT amount - overheal)
#     raw healing attempt = amount + overheal
#     overheal %          = overheal / (amount + overheal)
#
# Pet/summon actors are attributed back to the owning player via
# Actor.owner_id, so a "healer" list only ever shows players.
# =======================================================================


@dataclass
class HealerSummary:
    healer_id: int | None
    healer_name: str | None
    total_effective_healing: int = 0
    total_overheal: int = 0
    healing_by_ability: dict[str, int] = field(default_factory=dict)

    @property
    def total_raw_healing(self) -> int:
        return self.total_effective_healing + self.total_overheal

    @property
    def overheal_percent(self) -> float:
        raw = self.total_raw_healing
        return (self.total_overheal / raw) * 100 if raw else 0.0

    def hps(self, fight_duration_ms: int) -> float:
        return self.total_effective_healing / (fight_duration_ms / 1000) if fight_duration_ms > 0 else 0.0


@dataclass
class TargetHealingSummary:
    target_id: int | None
    target_name: str | None
    total_effective_healing_received: int = 0
    healing_by_source: dict[str, int] = field(default_factory=dict)


def _is_healing_event(event) -> bool:
    return event.data_type == "Healing"


def analyze_healing(parsed_fight: ParsedFight) -> list[HealerSummary]:
    """Build a HealerSummary per player who cast (or whose pet cast) any heals, sorted by effective healing descending."""
    summaries: dict[int, HealerSummary] = {}
    for event in parsed_fight.events:
        if not _is_healing_event(event):
            continue
        healer_id, healer_name = resolve_to_player_id_name(
            event.source_id, event.source_name, parsed_fight.actors
        )
        if healer_id not in summaries:
            summaries[healer_id] = HealerSummary(healer_id=healer_id, healer_name=healer_name)
        summary = summaries[healer_id]
        effective = event.amount or 0
        overheal = event.overheal or 0
        summary.total_effective_healing += effective
        summary.total_overheal += overheal
        ability_name = event.ability_name or "Unknown"
        summary.healing_by_ability[ability_name] = summary.healing_by_ability.get(ability_name, 0) + effective
    return sorted(summaries.values(), key=lambda s: s.total_effective_healing, reverse=True)


def analyze_healing_received(parsed_fight: ParsedFight) -> list[TargetHealingSummary]:
    """Build a TargetHealingSummary per actor who received any healing, sorted by total effective healing received descending."""
    summaries: dict[int, TargetHealingSummary] = {}
    for event in parsed_fight.events:
        if not _is_healing_event(event):
            continue
        target_id = event.target_id
        if target_id not in summaries:
            summaries[target_id] = TargetHealingSummary(target_id=target_id, target_name=event.target_name)
        summary = summaries[target_id]
        effective = event.amount or 0
        summary.total_effective_healing_received += effective
        _, source_name = resolve_to_player_id_name(event.source_id, event.source_name, parsed_fight.actors)
        source_name = source_name or "Unknown"
        summary.healing_by_source[source_name] = summary.healing_by_source.get(source_name, 0) + effective
    return sorted(summaries.values(), key=lambda s: s.total_effective_healing_received, reverse=True)


def summarize_healing(parsed_fight: ParsedFight, summaries: list[HealerSummary]) -> str:
    """Return a short human-readable per-healer summary, HPS-ranked."""
    if not summaries:
        return f"{parsed_fight.fight.name}: no healing recorded."
    duration_ms = parsed_fight.fight.duration_ms
    lines = [f"{parsed_fight.fight.name} -- healing ({format_timestamp(duration_ms)}):"]
    for summary in summaries:
        lines.append(
            f"  {(summary.healer_name or 'Unknown')[:15]:<15} "
            f"{summary.hps(duration_ms):>8.0f} HPS  "
            f"({summary.total_effective_healing:>10,} effective, "
            f"{summary.overheal_percent:>5.1f}% overheal)"
        )
    return "\n".join(lines)
