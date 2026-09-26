"""
gear_analyzer.py

Reports on gear quality and enchant/gem coverage per player, using the
CombatantInfo snapshot captured at pull start.

IMPORTANT LIMITATION: this app has no item database, so enchants and
gems are reported as PRESENT/MISSING plus their raw IDs -- never by
name. Quality is WCL's own numeric rarity tier.

Enchantable-slot coverage covers Head, Shoulder, Chest, Legs, Feet,
Rings (x2), and Main Hand. Off-hand (slot 16) is reported separately
and NOT counted toward "missing enchants," since it might be a
shield/holdable item that isn't enchantable at all.

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import roster

ENCHANTABLE_SLOTS = {
    0: "Head", 2: "Shoulder", 4: "Chest", 6: "Legs", 7: "Feet",
    10: "Ring 1", 11: "Ring 2", 15: "Main Hand",
}
OFF_HAND_SLOT = 16
NON_ARMOR_SLOTS = {3, 17}  # Shirt, Ranged/Relic -- excluded from ilvl/quality averages

QUALITY_NAMES = {
    0: "Poor", 1: "Common", 2: "Uncommon", 3: "Rare",
    4: "Epic", 5: "Legendary", 6: "Artifact", 7: "Heirloom",
}


def quality_name(quality: int) -> str:
    return QUALITY_NAMES.get(quality, f"Unknown({quality})")


@dataclass
class GearPieceReport:
    slot: int
    slot_name: str
    item_id: int
    quality: int
    item_level: int | None
    is_enchantable_slot: bool
    has_enchant: bool
    enchant_id: int | None
    gem_count: int
    gem_ids: list[int] = field(default_factory=list)

    @property
    def quality_name(self) -> str:
        return quality_name(self.quality)


@dataclass
class PlayerGearReport:
    player_id: int
    player_name: str
    pieces: list[GearPieceReport] = field(default_factory=list)
    has_data: bool = True

    @property
    def average_item_level(self) -> float:
        """Average across real, actually-equipped gear pieces only (excludes NON_ARMOR_SLOTS and empty slots)."""
        levels = [
            p.item_level for p in self.pieces
            if p.item_level is not None and p.slot not in NON_ARMOR_SLOTS and p.item_id != 0
        ]
        return sum(levels) / len(levels) if levels else 0.0

    @property
    def total_gems(self) -> int:
        return sum(p.gem_count for p in self.pieces)

    @property
    def missing_enchant_slots(self) -> list[str]:
        return [p.slot_name for p in self.pieces if p.is_enchantable_slot and not p.has_enchant]

    @property
    def lowest_quality(self) -> int | None:
        qualities = [p.quality for p in self.pieces if p.slot not in NON_ARMOR_SLOTS]
        return min(qualities) if qualities else None


def _build_gear_piece_report(gear_item) -> GearPieceReport:
    is_enchantable = gear_item.slot in ENCHANTABLE_SLOTS
    slot_name = ENCHANTABLE_SLOTS.get(gear_item.slot, "Off Hand" if gear_item.slot == OFF_HAND_SLOT else f"Slot {gear_item.slot}")
    return GearPieceReport(
        slot=gear_item.slot, slot_name=slot_name, item_id=gear_item.item_id, quality=gear_item.quality,
        item_level=gear_item.item_level, is_enchantable_slot=is_enchantable,
        has_enchant=gear_item.permanent_enchant_id is not None, enchant_id=gear_item.permanent_enchant_id,
        gem_count=len(gear_item.gem_ids), gem_ids=list(gear_item.gem_ids),
    )


def analyze_gear(parsed_fight) -> list[PlayerGearReport]:
    """
    Build a PlayerGearReport for every player in the fight roster.
    Players with no CombatantInfo snapshot still get an entry, marked
    has_data=False, rather than being silently omitted.

    SORTING (changed): results are sorted by average_item_level
    ASCENDING (lowest gear first) rather than alphabetically -- the
    idea being the player(s) most worth a raid lead's attention (the
    ones behind on gear) show up at the top of the list, not buried
    wherever their name happens to fall alphabetically. Players with
    no gear data at all (has_data=False) are sorted to the very END,
    since "unknown" isn't the same as "actually has low ilvl" and
    shouldn't visually masquerade as the worst-geared player. Ties
    within either group are broken by player name for stability.
    """
    fight_roster = roster.get_fight_roster(parsed_fight)
    reports = []
    for player_id, actor in fight_roster.items():
        snapshot = parsed_fight.combatant_info.get(player_id)
        if snapshot is None:
            reports.append(PlayerGearReport(player_id=player_id, player_name=actor.name, has_data=False))
            continue
        pieces = [_build_gear_piece_report(g) for g in snapshot.gear]
        reports.append(PlayerGearReport(player_id=player_id, player_name=actor.name, pieces=pieces))
    return sorted(
        reports,
        key=lambda r: (not r.has_data, r.average_item_level if r.has_data else 0.0, r.player_name or ""),
    )


def summarize_gear(reports: list[PlayerGearReport]) -> str:
    """Return a short human-readable summary of enchant/gem/quality coverage, in the same order as `reports` (lowest ilvl first)."""
    if not reports:
        return "No gear data available (no CombatantInfo snapshots for this fight)."
    lines = ["Gear check (sorted by item level, lowest first):"]
    for report in reports:
        name = (report.player_name or "Unknown")[:15]
        if not report.has_data:
            lines.append(f"  {name:<15} no gear data (no CombatantInfo snapshot)")
            continue
        missing = report.missing_enchant_slots
        lowest = report.lowest_quality
        lowest_str = f"{quality_name(lowest)}" if lowest is not None else "n/a"
        lines.append(
            f"  {name:<15} avg ilvl {report.average_item_level:>6.1f}  "
            f"lowest quality: {lowest_str:<10}  gems: {report.total_gems}"
        )
        if missing:
            lines.append(f"    missing enchants: {', '.join(missing)}")
    return "\n".join(lines)
