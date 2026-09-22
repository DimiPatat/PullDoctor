"""
avoidable_damage_analyzer.py

Reports how many times each player took an avoidable hit, per mechanic,
for whichever boss the fight is (matched via Fight.encounter_id against
avoidable_damage_data.ENCOUNTERS). If there's no config for a given
encounter yet, this returns an empty result rather than guessing.

Some mechanics are logged with a PLAYER as the event's source --
AvoidableMechanic.require_source_differs_from_target excludes the case
where source_id == target_id (the carrier's own expected hit).

SHARED/SPLIT damage mechanics are handled via
AvoidableMechanic.damage_multiplier_threshold: when set, a hit only
counts as avoidable if it's at least that many times the MEDIAN hit
amount recorded for that ability in that same pull.

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from avoidable_damage_data import AvoidableMechanic, EncounterConfig
from data_models import Event, ParsedFight
import roster


@dataclass
class PlayerAvoidableDamage:
    player_id: int
    player_name: str
    hits_by_mechanic: dict[str, int] = field(default_factory=dict)
    damage_by_mechanic: dict[str, int] = field(default_factory=dict)

    @property
    def total_avoidable_hits(self) -> int:
        return sum(self.hits_by_mechanic.values())

    @property
    def total_avoidable_damage(self) -> int:
        return sum(self.damage_by_mechanic.values())

    @property
    def distinct_mechanics_hit(self) -> int:
        """Number of DIFFERENT mechanics that hit this player at least once (not raw hit count)."""
        return sum(1 for count in self.hits_by_mechanic.values() if count > 0)


def get_encounter_config(encounter_id: int, configs: dict[int, EncounterConfig]) -> EncounterConfig | None:
    return configs.get(encounter_id)


def _should_count(event: Event, mechanic: AvoidableMechanic) -> bool:
    """
    True if this event is even a CANDIDATE for this mechanic -- i.e. it
    passes the require_source_differs_from_target check. Does NOT apply
    the damage_multiplier_threshold check (a separate, later step).
    """
    if mechanic.require_source_differs_from_target and event.source_id == event.target_id:
        return False
    return True


def _candidate_damage_events_by_ability(
    parsed_fight: ParsedFight, damage_tracked: dict[str, AvoidableMechanic]
) -> dict[str, list[Event]]:
    """
    DamageTaken events for each damage-tracked mechanic that pass
    _should_count. Shared by both analyze_avoidable_damage and
    compute_mechanic_baselines so the two can never disagree about
    which events form the candidate pool a median is computed from.
    """
    events_by_ability: dict[str, list[Event]] = {}
    for event in parsed_fight.events:
        if event.data_type != "DamageTaken" or event.ability_name not in damage_tracked:
            continue
        mechanic = damage_tracked[event.ability_name]
        if not _should_count(event, mechanic):
            continue
        events_by_ability.setdefault(event.ability_name, []).append(event)
    return events_by_ability


def compute_mechanic_baselines(parsed_fight: ParsedFight, config: EncounterConfig) -> dict[str, float]:
    """
    Median observed DamageTaken hit amount, per damage-tracked mechanic
    in `config`, for THIS pull. A mechanic with zero matching
    DamageTaken events this pull is simply absent from the returned
    dict (not present with a 0.0 value).
    """
    damage_tracked = {m.ability_name: m for m in config.mechanics if "damage" in m.track_via}
    events_by_ability = _candidate_damage_events_by_ability(parsed_fight, damage_tracked)
    return {
        name: statistics.median(e.amount or 0 for e in events)
        for name, events in events_by_ability.items()
    }


def analyze_avoidable_damage(
    parsed_fight: ParsedFight,
    configs: dict[int, EncounterConfig],
) -> list[PlayerAvoidableDamage]:
    """
    Build a PlayerAvoidableDamage report per player in the fight roster,
    for whichever mechanics are configured for this fight's encounter.
    Returns an empty list if there's no config for this encounter yet.
    """
    config = get_encounter_config(parsed_fight.fight.encounter_id, configs)
    if config is None or not config.mechanics:
        return []

    fight_roster = roster.get_fight_roster(parsed_fight)
    reports: dict[int, PlayerAvoidableDamage] = {
        player_id: PlayerAvoidableDamage(player_id=player_id, player_name=actor.name)
        for player_id, actor in fight_roster.items()
    }

    damage_tracked = {m.ability_name: m for m in config.mechanics if "damage" in m.track_via}
    debuff_tracked = {m.ability_name: m for m in config.mechanics if "debuff" in m.track_via}

    baselines = {
        name: statistics.median(e.amount or 0 for e in events)
        for name, events in _candidate_damage_events_by_ability(parsed_fight, damage_tracked).items()
    }

    for event in parsed_fight.events:
        if event.ability_name is None:
            continue
        if event.data_type == "DamageTaken" and event.ability_name in damage_tracked:
            mechanic = damage_tracked[event.ability_name]
            if not _should_count(event, mechanic):
                continue

            if mechanic.damage_multiplier_threshold is not None:
                median = baselines.get(event.ability_name, 0.0)
                amount = event.amount or 0
                if median <= 0 or amount < median * mechanic.damage_multiplier_threshold:
                    continue

            report = reports.get(event.target_id)
            if report is None:
                continue
            name = event.ability_name
            report.hits_by_mechanic[name] = report.hits_by_mechanic.get(name, 0) + 1
            report.damage_by_mechanic[name] = (
                report.damage_by_mechanic.get(name, 0) + (event.amount or 0)
            )
        elif (
            event.data_type == "Debuffs"
            and event.event_type == "applydebuff"
            and event.ability_name in debuff_tracked
        ):
            mechanic = debuff_tracked[event.ability_name]
            if not _should_count(event, mechanic):
                continue
            report = reports.get(event.target_id)
            if report is None:
                continue
            name = event.ability_name
            report.hits_by_mechanic[name] = report.hits_by_mechanic.get(name, 0) + 1
    return sorted(reports.values(), key=lambda r: r.total_avoidable_hits, reverse=True)


def summarize_avoidable_damage(
    parsed_fight: ParsedFight,
    reports: list[PlayerAvoidableDamage],
    config: EncounterConfig | None,
) -> str:
    """
    Return a short human-readable summary of avoidable hits per player.
    For any mechanic configured with a damage_multiplier_threshold, a
    trailing legend line shows the median it was compared against.
    """
    if config is None:
        return (
            f"{parsed_fight.fight.name}: no avoidable-damage config for this "
            f"encounter yet (encounter_id={parsed_fight.fight.encounter_id})."
        )
    if not reports or all(r.total_avoidable_hits == 0 for r in reports):
        return f"{parsed_fight.fight.name}: no avoidable hits taken. Clean pull!"
    lines = [f"{parsed_fight.fight.name} -- avoidable damage:"]
    for report in reports:
        if report.total_avoidable_hits == 0:
            continue
        breakdown = ", ".join(
            f"{name} x{count}" for name, count in report.hits_by_mechanic.items() if count > 0
        )
        lines.append(
            f"  {(report.player_name or 'Unknown')[:15]:<15} {report.total_avoidable_hits} hit(s)  ({breakdown})"
        )

    thresholded = [m for m in config.mechanics if m.damage_multiplier_threshold is not None]
    if thresholded:
        baselines = compute_mechanic_baselines(parsed_fight, config)
        lines.append("")
        for mechanic in thresholded:
            median = baselines.get(mechanic.ability_name)
            if median is None:
                lines.append(
                    f"  ({mechanic.ability_name}: flagged at >= {mechanic.damage_multiplier_threshold}x "
                    f"median hit -- no hits recorded this pull to compute a median from)"
                )
            else:
                lines.append(
                    f"  ({mechanic.ability_name}: flagged at >= {mechanic.damage_multiplier_threshold}x "
                    f"median hit, median={median:,.0f})"
                )
    return "\n".join(lines)
