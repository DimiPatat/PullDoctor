"""
death_analyzer.py

Analyzes deaths within a parsed fight. Builds a short window of context
around each death: damage landing on the victim beforehand, and healing
they were actually receiving, so you can tell "unhealable damage spike"
apart from "healer missed it".

Pure function over a ParsedFight -- no network, no other analyzer deps.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from data_models import Event, ParsedFight
import roster

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
        # FIXED: WCL's Healing event `amount` is ALREADY effective (net)
        # healing -- `overheal` is a separate, ADDITIVE figure for the
        # portion wasted on top of that, not a slice carved out of
        # `amount`. The old formula (amount - overheal) double-
        # subtracted overheal and could go negative, which is
        # physically impossible for real healing.
        return sum(h.amount for h in self.healing_received_before)


def _is_death_event(event: Event, actors) -> bool:
    is_death = event.data_type == "Deaths" or event.event_type == "death"
    return is_death and roster.is_player(event.target_id, actors)


# Ability names that carry NO real information about what killed the
# player -- either because log_parser.py couldn't resolve the ID at all
# (ability_name is None/empty), OR because WCL/the game itself logged a
# real but generic placeholder name for the death event (confirmed
# against a real report where "Unknown Ability" was a literal, non-
# empty ability_name during a mass-wipe from a hard enrage).
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
        seconds_in = report.time_into_fight_ms / 1000
        lines.append(
            f"  {seconds_in:>6.1f}s  {(report.victim_name or 'Unknown')[:20]:<20} "
            f"died to {report.killing_ability_name or 'Unknown'} "
            f"(dmg in {DEFAULT_CONTEXT_WINDOW_MS // 1000}s before: "
            f"{report.total_damage_taken_in_window}, "
            f"effective healing: {report.total_effective_healing_in_window})"
        )
    return "\n".join(lines)
