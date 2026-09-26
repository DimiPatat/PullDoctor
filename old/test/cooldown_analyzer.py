"""cooldown_analyzer.py -- generic cooldown usage/efficiency engine, reused for raid + defensive cooldowns.

CHANGED: summarize_cooldown_usage()'s header now shows fight duration as
M:SS (e.g. "3:03") via time_format.format_timestamp(), instead of raw
seconds (e.g. "183.0s"), matching report.py, death_analyzer.py, and
html_report.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from data_models import ParsedFight
import roster
from time_format import format_timestamp


@dataclass
class CooldownDefinition:
    ability_name: str
    cooldown_seconds: float
    category: str | None = None


@dataclass
class CooldownUsage:
    player_id: int | None
    player_name: str | None
    ability_name: str
    cooldown_seconds: float
    category: str | None
    cast_timestamps: list[int] = field(default_factory=list)

    @property
    def num_casts(self) -> int:
        return len(self.cast_timestamps)

    def theoretical_max_casts(self, fight_duration_ms: int) -> int:
        if self.cooldown_seconds <= 0 or fight_duration_ms <= 0:
            return 0
        return int((fight_duration_ms / 1000) // self.cooldown_seconds) + 1

    def efficiency(self, fight_duration_ms: int) -> float:
        max_casts = self.theoretical_max_casts(fight_duration_ms)
        return min(self.num_casts / max_casts, 1.0) if max_casts else 0.0


def analyze_cooldown_usage(parsed_fight: ParsedFight, cooldown_definitions: list[CooldownDefinition]) -> list[CooldownUsage]:
    definitions_by_name = {d.ability_name: d for d in cooldown_definitions}
    usages: dict[tuple[int, str], CooldownUsage] = {}
    for event in parsed_fight.events:
        if event.data_type != "Casts" or event.ability_name not in definitions_by_name:
            continue
        player = roster.resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue
        definition = definitions_by_name[event.ability_name]
        key = (player.id, definition.ability_name)
        if key not in usages:
            usages[key] = CooldownUsage(player_id=player.id, player_name=player.name, ability_name=definition.ability_name, cooldown_seconds=definition.cooldown_seconds, category=definition.category)
        usages[key].cast_timestamps.append(event.timestamp)
    for usage in usages.values():
        usage.cast_timestamps.sort()
    return sorted(usages.values(), key=lambda u: (u.player_name or "", u.ability_name))


def summarize_cooldown_usage(parsed_fight: ParsedFight, usages: list[CooldownUsage]) -> str:
    if not usages:
        return f"{parsed_fight.fight.name}: no tracked cooldown usage recorded."
    duration_ms = parsed_fight.fight.duration_ms
    lines = [f"{parsed_fight.fight.name} -- cooldown usage ({format_timestamp(duration_ms)}):"]
    for usage in usages:
        max_casts = usage.theoretical_max_casts(duration_ms)
        lines.append(f"  {(usage.player_name or 'Unknown')[:15]:<15} {usage.ability_name[:25]:<25} {usage.num_casts}/{max_casts} casts  ({usage.efficiency(duration_ms) * 100:>5.1f}% efficiency)")
    return "\n".join(lines)
