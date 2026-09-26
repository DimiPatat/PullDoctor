"""
gear_explorer.py

Scans a fight's CombatantInfo snapshots (via gear_analyzer.analyze_gear)
and summarizes what's actually equipped across the roster, per
enchantable slot -- which enchant IDs appear and how many players have
each, which gem IDs appear anywhere, plus roster-wide item level/
quality/gem-count distributions.

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import gear_analyzer
from data_models import ParsedFight


@dataclass
class SlotEnchantDetection:
    slot: int
    slot_name: str
    # enchant_id -> set of player names seen with it. None key = "no
    # enchant equipped in this slot".
    player_names_by_enchant_id: dict[int | None, set[str]] = field(default_factory=dict)

    @property
    def distinct_enchant_ids(self) -> list[int]:
        return sorted(eid for eid in self.player_names_by_enchant_id if eid is not None)

    @property
    def players_missing_enchant(self) -> set[str]:
        return self.player_names_by_enchant_id.get(None, set())


@dataclass
class GearDistributionSummary:
    slot_detections: list[SlotEnchantDetection] = field(default_factory=list)
    gem_count_distribution: dict[int, int] = field(default_factory=dict)
    # gem_id -> set of player names seen with a gem of that ID socketed,
    # ACROSS ALL EQUIPPED PIECES (not scoped to enchantable slots).
    gem_id_player_names: dict[int, set[str]] = field(default_factory=dict)
    player_average_item_levels: list[float] = field(default_factory=list)
    player_lowest_qualities: list[int] = field(default_factory=list)
    players_without_gear_data: list[str] = field(default_factory=list)
    roster_size: int = 0


def analyze_gear_distribution(parsed_fight: ParsedFight) -> GearDistributionSummary:
    """
    Build a GearDistributionSummary from this fight's CombatantInfo
    snapshots. Players with no snapshot at all are listed separately.
    """
    reports = gear_analyzer.analyze_gear(parsed_fight)
    slot_detections: dict[int, SlotEnchantDetection] = {
        slot: SlotEnchantDetection(slot=slot, slot_name=slot_name)
        for slot, slot_name in gear_analyzer.ENCHANTABLE_SLOTS.items()
    }

    summary = GearDistributionSummary(roster_size=len(reports))

    for report in reports:
        if not report.has_data:
            summary.players_without_gear_data.append(report.player_name)
            continue

        summary.player_average_item_levels.append(report.average_item_level)
        if report.lowest_quality is not None:
            summary.player_lowest_qualities.append(report.lowest_quality)

        gem_count = report.total_gems
        summary.gem_count_distribution[gem_count] = summary.gem_count_distribution.get(gem_count, 0) + 1

        for piece in report.pieces:
            for gem_id in piece.gem_ids:
                summary.gem_id_player_names.setdefault(gem_id, set()).add(report.player_name)

        pieces_by_slot = {p.slot: p for p in report.pieces}
        for slot, detection in slot_detections.items():
            piece = pieces_by_slot.get(slot)
            enchant_key = piece.enchant_id if (piece and piece.has_enchant) else None
            detection.player_names_by_enchant_id.setdefault(enchant_key, set()).add(report.player_name)

    summary.slot_detections = [slot_detections[slot] for slot in sorted(slot_detections)]
    return summary


def format_gear_distribution_table(summary: GearDistributionSummary) -> str:
    """Render a plain-text summary, for terminal output."""
    if summary.roster_size == 0:
        return "No players found in this fight's roster."

    lines = [f"Gear distribution across {summary.roster_size} player(s):\n"]

    for detection in summary.slot_detections:
        lines.append(f"{detection.slot_name}:")
        for enchant_id in detection.distinct_enchant_ids:
            players = sorted(detection.player_names_by_enchant_id[enchant_id])
            lines.append(f"  enchant {enchant_id}: {len(players)} player(s) -- {', '.join(players)}")
        missing = sorted(detection.players_missing_enchant)
        if missing:
            lines.append(f"  (no enchant): {len(missing)} player(s) -- {', '.join(missing)}")
        lines.append("")

    if summary.gem_count_distribution:
        lines.append("Gem count distribution:")
        for count in sorted(summary.gem_count_distribution):
            lines.append(f"  {count} gem(s): {summary.gem_count_distribution[count]} player(s)")
        lines.append("")

    if summary.gem_id_player_names:
        lines.append("Gem IDs observed (across all equipped pieces):")
        for gem_id in sorted(summary.gem_id_player_names):
            players = sorted(summary.gem_id_player_names[gem_id])
            lines.append(f"  gem {gem_id}: {len(players)} player(s) -- {', '.join(players)}")
        lines.append("")

    if summary.player_average_item_levels:
        levels = summary.player_average_item_levels
        lines.append(
            f"Average item level -- min: {min(levels):.1f}  max: {max(levels):.1f}  "
            f"roster avg: {sum(levels) / len(levels):.1f}"
        )
    if summary.player_lowest_qualities:
        qualities = summary.player_lowest_qualities
        lines.append(
            f"Lowest quality tier seen -- min: {gear_analyzer.quality_name(min(qualities))}  "
            f"({min(qualities)}-{max(qualities)} range across roster)"
        )
    if summary.players_without_gear_data:
        lines.append(f"\nNo gear data at all for: {', '.join(summary.players_without_gear_data)}")

    return "\n".join(lines)
