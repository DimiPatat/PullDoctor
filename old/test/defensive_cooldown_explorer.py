"""
defensive_cooldown_explorer.py

Scans a ParsedFight for defensive cooldowns matching a known reference
dictionary (defensive_cooldown_data.SEED_REFERENCE_DEFENSIVE_COOLDOWNS),
so explore_defensive_cooldowns.py can show "here's what this log
actually shows people using" instead of asking you to type every
ability name from memory.

Detection uses player-sourced Casts events only. Pure function over a
ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from defensive_cooldown_schema import DefensiveCooldownDefinition
from data_models import ParsedFight
import roster


@dataclass
class DetectedDefensiveCooldown:
    definition: DefensiveCooldownDefinition
    casts_by_player: dict[str, int] = field(default_factory=dict)
    ability_ids_seen: set[int] = field(default_factory=set)
    first_seen_ms: int | None = None

    @property
    def player_count(self) -> int:
        return len(self.casts_by_player)

    @property
    def total_casts(self) -> int:
        return sum(self.casts_by_player.values())


def analyze_known_defensive_cooldown_matches(
    parsed_fight: ParsedFight,
    seed_definitions: list[DefensiveCooldownDefinition],
) -> list[DetectedDefensiveCooldown]:
    by_name = {definition.ability_name: definition for definition in seed_definitions}
    detected: dict[str, DetectedDefensiveCooldown] = {}

    for event in parsed_fight.events:
        if event.data_type != "Casts" or not event.ability_name:
            continue
        definition = by_name.get(event.ability_name)
        if definition is None:
            continue
        player = roster.resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue

        entry = detected.setdefault(
            definition.ability_name, DetectedDefensiveCooldown(definition=definition)
        )
        entry.casts_by_player[player.name] = entry.casts_by_player.get(player.name, 0) + 1
        if event.ability_id:
            entry.ability_ids_seen.add(event.ability_id)
        if entry.first_seen_ms is None or event.timestamp < entry.first_seen_ms:
            entry.first_seen_ms = event.timestamp

    return sorted(
        detected.values(),
        key=lambda d: (d.definition.class_name or "", d.definition.ability_name),
    )


def format_detected_table(detected: list[DetectedDefensiveCooldown]) -> str:
    if not detected:
        return "No known defensive cooldowns detected in this pull."
    header = f"{'Ability':<28} {'Class':<14} {'CD(s)':>6} {'Players':>8} {'TotalCasts':>11}"
    lines = ["Detected defensive cooldowns (matched against the known reference list):", header, "-" * len(header)]
    for entry in detected:
        lines.append(
            f"{entry.definition.ability_name:<28.28} {(entry.definition.class_name or ''):<14.14} "
            f"{entry.definition.cooldown_seconds:>6.0f} {entry.player_count:>8} {entry.total_casts:>11}"
        )
    return "\n".join(lines)
