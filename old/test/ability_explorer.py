"""
ability_explorer.py

Summarizes every ability cast/used by NPCs (bosses, adds -- never
players or pets) in a fight, PLUS abilities genuinely sourced BY a
player that also hit another player (possible carried/soak mechanics),
to help identify which are worth adding to boss_mechanics_config.py.

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from data_models import ParsedFight

RELEVANT_DATA_TYPES = ("Casts", "DamageTaken", "Debuffs")


@dataclass
class AbilitySummary:
    ability_name: str
    ability_id: int | None = None
    occurrences_by_type: dict[str, int] = field(default_factory=dict)
    target_ids_hit: set[int] = field(default_factory=set)
    total_damage: int = 0
    max_hit: int = 0
    first_seen_ms: int | None = None
    last_seen_ms: int | None = None
    caused_death: bool = False

    @property
    def total_occurrences(self) -> int:
        return sum(self.occurrences_by_type.values())

    @property
    def unique_targets_hit(self) -> int:
        return len(self.target_ids_hit)

    @property
    def damage_hit_count(self) -> int:
        return self.occurrences_by_type.get("DamageTaken", 0)

    @property
    def avg_hit(self) -> float:
        if self.damage_hit_count == 0:
            return 0.0
        return self.total_damage / self.damage_hit_count

    def first_seen_pct(self, fight_duration_ms: int) -> float:
        if self.first_seen_ms is None or fight_duration_ms <= 0:
            return 0.0
        return (self.first_seen_ms / fight_duration_ms) * 100


def _is_npc_source(source_id: int | None, actors) -> bool:
    if source_id is None:
        return False
    actor = actors.get(source_id)
    return actor is not None and actor.type == "NPC"


def _is_player_source(source_id: int | None, actors) -> bool:
    if source_id is None:
        return False
    actor = actors.get(source_id)
    return actor is not None and actor.type == "Player"


def _is_player_target(target_id: int | None, actors) -> bool:
    if target_id is None:
        return False
    actor = actors.get(target_id)
    return actor is not None and actor.type == "Player"


def analyze_boss_abilities(parsed_fight: ParsedFight) -> list[AbilitySummary]:
    """Build an AbilitySummary per distinct NPC-sourced ability name seen in this fight, sorted by total occurrences descending."""
    death_ability_names = {
        e.ability_name for e in parsed_fight.events if e.data_type == "Deaths" and e.ability_name
    }
    summaries: dict[str, AbilitySummary] = {}
    for event in parsed_fight.events:
        if event.data_type not in RELEVANT_DATA_TYPES:
            continue
        if not event.ability_name:
            continue
        if not _is_npc_source(event.source_id, parsed_fight.actors):
            continue

        name = event.ability_name
        if name not in summaries:
            summaries[name] = AbilitySummary(ability_name=name, ability_id=event.ability_id)
        summary = summaries[name]
        summary.occurrences_by_type[event.data_type] = summary.occurrences_by_type.get(event.data_type, 0) + 1

        if event.data_type == "DamageTaken":
            amount = event.amount or 0
            summary.total_damage += amount
            summary.max_hit = max(summary.max_hit, amount)

        if event.data_type in ("DamageTaken", "Debuffs") and event.target_id is not None:
            summary.target_ids_hit.add(event.target_id)

        if summary.first_seen_ms is None or event.timestamp < summary.first_seen_ms:
            summary.first_seen_ms = event.timestamp
        if summary.last_seen_ms is None or event.timestamp > summary.last_seen_ms:
            summary.last_seen_ms = event.timestamp

        if name in death_ability_names:
            summary.caused_death = True

    return sorted(summaries.values(), key=lambda s: s.total_occurrences, reverse=True)


@dataclass
class PlayerSourcedAbilitySummary:
    """
    A DamageTaken/Debuffs ability whose SOURCE actor is a PLAYER and
    whose TARGET actor is also a PLAYER (never an NPC). self_hit_count
    is the carrier's own hit (source==target); other_hit_count is a
    DIFFERENT player getting caught by it -- usually the real mistake.
    """
    ability_name: str
    ability_id: int | None = None
    occurrences_by_type: dict[str, int] = field(default_factory=dict)
    self_hit_count: int = 0
    other_hit_count: int = 0
    self_damage: int = 0
    other_damage: int = 0
    other_target_ids: set[int] = field(default_factory=set)
    max_hit: int = 0
    first_seen_ms: int | None = None
    last_seen_ms: int | None = None
    caused_death: bool = False

    @property
    def total_occurrences(self) -> int:
        return self.self_hit_count + self.other_hit_count

    @property
    def unique_other_targets_hit(self) -> int:
        return len(self.other_target_ids)

    def first_seen_pct(self, fight_duration_ms: int) -> float:
        if self.first_seen_ms is None or fight_duration_ms <= 0:
            return 0.0
        return (self.first_seen_ms / fight_duration_ms) * 100


def analyze_player_sourced_abilities(
    parsed_fight: ParsedFight, min_other_hits: int = 0
) -> list[PlayerSourcedAbilitySummary]:
    """
    Build a PlayerSourcedAbilitySummary per ability sourced by a PLAYER
    that also hit ANOTHER player. min_other_hits filters the returned
    list to abilities with at least this many other_hit_count. Default
    0 returns everything (including self-only abilities).
    """
    death_ability_names = {
        e.ability_name for e in parsed_fight.events if e.data_type == "Deaths" and e.ability_name
    }

    summaries: dict[str, PlayerSourcedAbilitySummary] = {}
    for event in parsed_fight.events:
        if event.data_type not in ("DamageTaken", "Debuffs"):
            continue
        if not event.ability_name:
            continue
        if not _is_player_source(event.source_id, parsed_fight.actors):
            continue
        if not _is_player_target(event.target_id, parsed_fight.actors):
            continue
        if event.data_type == "Debuffs" and event.event_type != "applydebuff":
            continue

        name = event.ability_name
        if name not in summaries:
            summaries[name] = PlayerSourcedAbilitySummary(ability_name=name, ability_id=event.ability_id)
        summary = summaries[name]
        summary.occurrences_by_type[event.data_type] = summary.occurrences_by_type.get(event.data_type, 0) + 1

        is_self_hit = event.source_id == event.target_id
        amount = event.amount or 0 if event.data_type == "DamageTaken" else 0

        if is_self_hit:
            summary.self_hit_count += 1
            summary.self_damage += amount
        else:
            summary.other_hit_count += 1
            summary.other_damage += amount
            if event.target_id is not None:
                summary.other_target_ids.add(event.target_id)

        if event.data_type == "DamageTaken":
            summary.max_hit = max(summary.max_hit, amount)

        if summary.first_seen_ms is None or event.timestamp < summary.first_seen_ms:
            summary.first_seen_ms = event.timestamp
        if summary.last_seen_ms is None or event.timestamp > summary.last_seen_ms:
            summary.last_seen_ms = event.timestamp

        if name in death_ability_names:
            summary.caused_death = True

    results = sorted(summaries.values(), key=lambda s: s.total_occurrences, reverse=True)
    if min_other_hits > 0:
        results = [s for s in results if s.other_hit_count >= min_other_hits]
    return results
