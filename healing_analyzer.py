"""
healing_analyzer.py

Analyzes healing events within a parsed fight: per-healer output
(total effective healing, overheal, HPS, breakdown by spell) and
per-target output (who received how much healing, and from whom).

WCL's `amount` field on a Healing event is already the EFFECTIVE
healing done (HP actually restored); `overheal` is a SEPARATE,
additive figure for the portion that was wasted on top of that:
    effective healing   = amount              (NOT amount - overheal)
    raw healing attempt = amount + overheal
    overheal %          = overheal / (amount + overheal)

Pet/summon actors are attributed back to the owning player via
Actor.owner_id, so a "healer" list only ever shows players.

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from data_models import ParsedFight
import roster


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
        healer_id, healer_name = roster.resolve_to_player_id_name(
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
        _, source_name = roster.resolve_to_player_id_name(event.source_id, event.source_name, parsed_fight.actors)
        source_name = source_name or "Unknown"
        summary.healing_by_source[source_name] = summary.healing_by_source.get(source_name, 0) + effective
    return sorted(summaries.values(), key=lambda s: s.total_effective_healing_received, reverse=True)


def summarize_healing(parsed_fight: ParsedFight, summaries: list[HealerSummary]) -> str:
    """Return a short human-readable per-healer summary, HPS-ranked."""
    if not summaries:
        return f"{parsed_fight.fight.name}: no healing recorded."
    duration_ms = parsed_fight.fight.duration_ms
    lines = [f"{parsed_fight.fight.name} -- healing ({duration_ms / 1000:.1f}s):"]
    for summary in summaries:
        lines.append(
            f"  {(summary.healer_name or 'Unknown')[:15]:<15} "
            f"{summary.hps(duration_ms):>8.0f} HPS  "
            f"({summary.total_effective_healing:>10,} effective, "
            f"{summary.overheal_percent:>5.1f}% overheal)"
        )
    return "\n".join(lines)
