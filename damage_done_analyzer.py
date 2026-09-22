"""
damage_done_analyzer.py

Analyzes damage-DONE events within a parsed fight: per-player DPS
output (total damage done, breakdown by ability, breakdown by target
hit). This is the "who's dealing damage to the boss" view, as opposed
to damage_analyzer.py which covers damage TAKEN.

Pet/summon damage is folded into the owning player via
roster.resolve_to_player, so pets never appear as separate entries.
Damage from sources that aren't players and can't be resolved to an
owning player (bosses, adds hitting each other, etc.) is skipped
entirely -- this module is specifically a player DPS view.

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from data_models import ParsedFight
import roster


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
        player = roster.resolve_to_player(event.source_id, parsed_fight.actors)
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
    lines = [f"{parsed_fight.fight.name} -- damage done ({duration_ms / 1000:.1f}s):"]
    for summary in summaries:
        lines.append(
            f"  {(summary.player_name or 'Unknown')[:15]:<15} "
            f"{summary.dps(duration_ms):>8.0f} DPS  "
            f"({summary.total_damage_done:>10,} total)"
        )
    return "\n".join(lines)
