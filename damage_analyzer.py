"""
damage_analyzer.py

Analyzes damage-taken events within a parsed fight: per-player output
(total damage taken, DTPS, breakdown by ability and by source) and
per-source output (which boss/NPC/ability is dealing the most damage).
Also surfaces the single biggest hits.

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from data_models import Event, ParsedFight
import roster


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
    return event.data_type == "DamageTaken" and roster.is_player(event.target_id, actors)


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
    lines = [f"{parsed_fight.fight.name} -- damage taken ({duration_ms / 1000:.1f}s):"]
    for summary in summaries:
        lines.append(
            f"  {(summary.target_name or 'Unknown')[:15]:<15} "
            f"{summary.dtps(duration_ms):>8.0f} DTPS  "
            f"({summary.total_damage_taken:>10,} total)"
        )
    return "\n".join(lines)
