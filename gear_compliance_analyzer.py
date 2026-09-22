"""
gear_compliance_analyzer.py

Turns gear_analyzer.py's raw per-player gear FACTS into pass/fail
COMPLIANCE against a configured GearRequirements profile. Where
gear_analyzer.py only ever reports "this player has an enchant in
slot X" or "no enchant", this module can additionally judge "...and
it's the RIGHT enchant" once specific enchant IDs are configured, plus
roster-wide item level / quality / gem-count/quality thresholds.

BACKWARD COMPATIBILITY: an empty/unconfigured GearRequirements makes
every check a no-op -- every enchantable slot without an explicit
EnchantRequirement falls back to "any enchant counts, just don't be
missing one" (identical to gear_analyzer.py's original behavior).

v1 scope: off-hand and non-enchantable slots are never checked. Gems
are checked two ways: min_gem_count (roster-wide minimum total count)
and required_gem_ids (a flat quality whitelist across all slots).

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import gear_analyzer
from data_models import ParsedFight
from gear_schema import EnchantRequirement, GearRequirements


@dataclass
class PlayerGearCompliance:
    player_id: int
    player_name: str
    has_data: bool = True

    # slot_name -> True/False, one entry per checked enchantable slot
    # (a slot with skip_check=True is simply absent from this dict).
    enchant_pass_by_slot: dict[str, bool] = field(default_factory=dict)

    gem_pass: bool = True  # total-count threshold (min_gem_count)
    gem_quality_pass: bool = True  # every socketed gem is in required_gem_ids
    item_level_pass: bool = True
    quality_pass: bool = True

    average_item_level: float = 0.0
    lowest_quality: int | None = None
    total_gems: int = 0
    # The specific socketed gem IDs that did NOT match required_gem_ids.
    wrong_quality_gem_ids: list[int] = field(default_factory=list)

    @property
    def failed_enchant_slots(self) -> list[str]:
        return [slot for slot, passed in self.enchant_pass_by_slot.items() if not passed]

    @property
    def fully_compliant(self) -> bool:
        """
        True only if gear data exists AND every checked dimension
        passes. A player with has_data=False is never compliant.
        """
        return (
            self.has_data
            and not self.failed_enchant_slots
            and self.gem_pass
            and self.gem_quality_pass
            and self.item_level_pass
            and self.quality_pass
        )


def _check_enchant_slot(
    piece, requirement: EnchantRequirement | None
) -> bool:
    """
    True if this gear piece satisfies the slot's requirement (or the
    default "any enchant" fallback when no requirement is configured).
    """
    if piece is None:
        return False
    if not piece.has_enchant:
        return False
    if requirement is None or not requirement.required_enchant_ids:
        return True
    return piece.enchant_id in requirement.required_enchant_ids


def analyze_gear_compliance(
    parsed_fight: ParsedFight,
    requirements: GearRequirements,
) -> list[PlayerGearCompliance]:
    """
    Build a PlayerGearCompliance report per player in the fight roster.
    Players with no CombatantInfo snapshot get has_data=False and are
    never compliant.
    """
    gear_reports = gear_analyzer.analyze_gear(parsed_fight)
    requirement_by_slot = {r.slot: r for r in requirements.enchant_requirements}

    results: list[PlayerGearCompliance] = []
    for report in gear_reports:
        if not report.has_data:
            results.append(PlayerGearCompliance(
                player_id=report.player_id, player_name=report.player_name, has_data=False,
            ))
            continue

        compliance = PlayerGearCompliance(
            player_id=report.player_id, player_name=report.player_name,
            average_item_level=report.average_item_level,
            lowest_quality=report.lowest_quality,
            total_gems=report.total_gems,
        )

        pieces_by_slot = {p.slot: p for p in report.pieces}
        for slot, slot_name in gear_analyzer.ENCHANTABLE_SLOTS.items():
            requirement = requirement_by_slot.get(slot)
            if requirement is not None and requirement.skip_check:
                continue
            piece = pieces_by_slot.get(slot)
            compliance.enchant_pass_by_slot[slot_name] = _check_enchant_slot(piece, requirement)

        if requirements.min_gem_count is not None:
            compliance.gem_pass = report.total_gems >= requirements.min_gem_count
        if requirements.required_gem_ids:
            all_gem_ids = [gem_id for piece in report.pieces for gem_id in piece.gem_ids]
            required_set = set(requirements.required_gem_ids)
            compliance.wrong_quality_gem_ids = sorted(
                {gem_id for gem_id in all_gem_ids if gem_id not in required_set}
            )
            compliance.gem_quality_pass = not compliance.wrong_quality_gem_ids
        if requirements.min_item_level is not None:
            compliance.item_level_pass = report.average_item_level >= requirements.min_item_level
        if requirements.min_quality is not None:
            compliance.quality_pass = (
                report.lowest_quality is not None and report.lowest_quality >= requirements.min_quality
            )

        results.append(compliance)

    return sorted(results, key=lambda c: c.player_name or "")


def summarize_gear_compliance(
    parsed_fight: ParsedFight,
    results: list[PlayerGearCompliance],
    requirements: GearRequirements,
) -> str:
    """Return a short human-readable summary of gear compliance per player."""
    if not results:
        return f"{parsed_fight.fight.name}: no roster data available for gear compliance checks."

    has_any_configured_check = (
        requirements.enchant_requirements
        or requirements.min_item_level is not None
        or requirements.min_quality is not None
        or requirements.min_gem_count is not None
        or requirements.required_gem_ids
    )

    lines = [f"{parsed_fight.fight.name} -- gear compliance:"]
    for compliance in results:
        name = (compliance.player_name or "Unknown")[:15]
        if not compliance.has_data:
            lines.append(f"  {name:<15} no gear data (no CombatantInfo snapshot)")
            continue

        problems: list[str] = []
        if compliance.failed_enchant_slots:
            problems.append(f"missing/wrong enchant: {', '.join(compliance.failed_enchant_slots)}")
        if not compliance.gem_pass:
            problems.append(f"gems {compliance.total_gems} < required {requirements.min_gem_count}")
        if not compliance.gem_quality_pass:
            problems.append(f"wrong-quality gem(s): {', '.join(str(g) for g in compliance.wrong_quality_gem_ids)}")
        if not compliance.item_level_pass:
            problems.append(
                f"ilvl {compliance.average_item_level:.1f} < required {requirements.min_item_level}"
            )
        if not compliance.quality_pass:
            lowest_name = (
                gear_analyzer.quality_name(compliance.lowest_quality)
                if compliance.lowest_quality is not None else "n/a"
            )
            problems.append(f"quality {lowest_name} below required tier {requirements.min_quality}")

        if problems:
            lines.append(f"  {name:<15} FAIL -- {'; '.join(problems)}")
        else:
            lines.append(f"  {name:<15} PASS")

    if not has_any_configured_check:
        lines.append("")
        lines.append(
            "  (No gear compliance rules configured yet -- every player passes by default. "
            "Use explore_gear.py to set enchant/item-level/quality/gem requirements.)"
        )
    elif requirements.required_gem_ids:
        lines.append("")
        lines.append(
            f"  (Gem quality whitelist: {len(requirements.required_gem_ids)} accepted ID(s) -- "
            f"any socketed gem outside this set fails the check.)"
        )

    if requirements.notes:
        lines.append("")
        lines.append(f"  Profile notes: {requirements.notes}")

    return "\n".join(lines)
