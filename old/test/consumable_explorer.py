"""
consumable_explorer.py

Scans a ParsedFight for consumables matching a known reference
dictionary (consumable_data.SEED_REFERENCE_CONSUMABLES), so
explore_consumables.py can show "here's what this log actually shows
you using" instead of asking you to type every item name from memory.

Detection uses two sources:
  - CombatantInfo aura_names (pre-pull buffs -- food, oils, runes,
    flasks popped before the pull began)
  - Casts events during the fight (mid-fight reactive use -- a health
    potion drunk during a damage phase, a Healthstone used on cooldown)

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from consumable_schema import ConsumableDefinition
from data_models import ParsedFight
import roster


@dataclass
class DetectedConsumable:
    definition: ConsumableDefinition
    player_names: set[str] = field(default_factory=set)
    detected_via: set[str] = field(default_factory=set)  # {"pre-pull aura", "mid-fight cast"}
    ability_ids_seen: set[int] = field(default_factory=set)
    first_seen_ms: int | None = None

    @property
    def player_count(self) -> int:
        return len(self.player_names)


def analyze_known_consumable_matches(
    parsed_fight: ParsedFight,
    seed_definitions: list[ConsumableDefinition],
) -> list[DetectedConsumable]:
    """
    Build a DetectedConsumable for every seed_definitions entry that was
    actually observed at least once in this fight. Definitions never
    observed are simply absent from the result. Sorted by (category,
    name) for a stable, readable grouping when displayed.
    """
    by_name = {definition.name: definition for definition in seed_definitions}
    detected: dict[str, DetectedConsumable] = {}
    fight_roster = roster.get_fight_roster(parsed_fight)

    for player_id, actor in fight_roster.items():
        snapshot = parsed_fight.combatant_info.get(player_id)
        if snapshot is None:
            continue
        for aura_name in snapshot.aura_names:
            definition = by_name.get(aura_name)
            if definition is None:
                continue
            entry = detected.setdefault(definition.name, DetectedConsumable(definition=definition))
            entry.player_names.add(actor.name)
            entry.detected_via.add("pre-pull aura")

    for event in parsed_fight.events:
        if event.data_type != "Casts" or not event.ability_name:
            continue
        definition = by_name.get(event.ability_name)
        if definition is None:
            continue
        player = roster.resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue
        entry = detected.setdefault(definition.name, DetectedConsumable(definition=definition))
        entry.player_names.add(player.name)
        entry.detected_via.add("mid-fight cast")
        if event.ability_id:
            entry.ability_ids_seen.add(event.ability_id)
        if entry.first_seen_ms is None or event.timestamp < entry.first_seen_ms:
            entry.first_seen_ms = event.timestamp

    return sorted(detected.values(), key=lambda d: (d.definition.category, d.definition.name))


def format_detected_table(detected: list[DetectedConsumable], roster_size: int) -> str:
    """Render a plain-text table of detected consumables, for terminal output."""
    if not detected:
        return "No known consumables detected in this pull."
    header = f"{'Item':<32} {'Category':<15} {'Players':>9}  {'Detected Via':<28}"
    lines = ["Detected consumables (matched against the known reference list):", header, "-" * len(header)]
    for entry in detected:
        via_text = "/".join(sorted(entry.detected_via))
        coverage = f"{entry.player_count}/{roster_size}"
        lines.append(
            f"{entry.definition.name:<32.32} {entry.definition.category:<15.15} "
            f"{coverage:>9}  {via_text:<28}"
        )
    return "\n".join(lines)
