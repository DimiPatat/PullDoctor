"""
raid_scorecard.py

A single, unified per-player "scorecard" that synthesizes results from
every other player-facing check already built into this project --
consumables, gear compliance, and avoidable boss-mechanic damage -- into
one ranked table.

DESIGN NOTE -- why "number of distinct problems", not a blended number:
Each source analyzer reports on a different SCALE. If summed directly,
one spammy mechanic would dwarf "forgot to flask" even though both are
really just "one thing to go fix." The scorecard's headline number --
problem_count -- counts DISTINCT PROBLEM AREAS per category (one
missing consumable category = 1, one failed gear check = 1, one
avoidable MECHANIC that hit them at all = 1, regardless of how many
times), not raw magnitudes. Raw magnitudes are still fully preserved on
each entry.

DESIGN NOTE -- why defensive cooldown usage is informational, not
scored: whether a player "should" have used a defensive cooldown
depends on context this project doesn't model -- sitting on Divine
Shield through a clean pull isn't a mistake. Defensive cooldown data is
attached for visibility but deliberately EXCLUDED from problem_count.

DESIGN NOTE -- missing gear data is NOT the same as a gear failure:
A player with no CombatantInfo snapshot (has_data=False) means "we
can't tell", not "they failed a check". Tracked as its own
gear_data_missing flag, NOT counted in problem_count.

Every input to build_raid_scorecard() is OPTIONAL (None) -- the
scorecard composes gracefully from however many of the underlying
analyzers you've actually run.

Pure function over already-computed analyzer outputs -- no network
calls, and this module does not call any other analyzer itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import gear_analyzer
from avoidable_damage_analyzer import PlayerAvoidableDamage
from consumables_analyzer import PlayerConsumables
from cooldown_analyzer import CooldownUsage
from data_models import ParsedFight
from gear_compliance_analyzer import PlayerGearCompliance
from gear_schema import GearRequirements
import roster


@dataclass
class PlayerScorecardEntry:
    player_id: int
    player_name: str

    # --- Consumables ---
    consumable_missing_categories: list[str] = field(default_factory=list)

    # --- Gear compliance ---
    gear_data_missing: bool = False  # True = "we can't tell", NOT a failure
    gear_failed_checks: list[str] = field(default_factory=list)

    # --- Avoidable damage ---
    avoidable_mechanics_hit: list[str] = field(default_factory=list)  # readable "Name xN" strings
    avoidable_distinct_mechanic_count: int = 0  # contributes to problem_count
    avoidable_total_hits: int = 0  # raw total hit count -- informational only

    # --- Defensive cooldowns (informational only) ---
    defensive_usages: list[CooldownUsage] = field(default_factory=list)

    @property
    def problem_count(self) -> int:
        """The scorecard's headline number -- see module docstring for the design rationale."""
        return (
            len(self.consumable_missing_categories)
            + len(self.gear_failed_checks)
            + self.avoidable_distinct_mechanic_count
        )

    @property
    def is_clean(self) -> bool:
        return self.problem_count == 0

    @property
    def defensive_total_casts(self) -> int:
        return sum(u.num_casts for u in self.defensive_usages)

    def defensive_total_theoretical_max(self, fight_duration_ms: int) -> int:
        return sum(u.theoretical_max_casts(fight_duration_ms) for u in self.defensive_usages)

    def defensive_overall_efficiency(self, fight_duration_ms: int) -> float:
        """
        Blended efficiency across every tracked defensive this player
        actually cast at least once. Returns 0.0 if they have no
        tracked defensive usage at all (informational only).
        """
        max_total = self.defensive_total_theoretical_max(fight_duration_ms)
        if max_total == 0:
            return 0.0
        return min(self.defensive_total_casts / max_total, 1.0)


def _describe_gear_failures(
    compliance: PlayerGearCompliance, requirements: GearRequirements | None
) -> list[str]:
    """Build readable one-line descriptions for each failed gear check."""
    problems: list[str] = []
    if compliance.failed_enchant_slots:
        problems.append(f"missing/wrong enchant: {', '.join(compliance.failed_enchant_slots)}")
    if not compliance.gem_pass:
        required = requirements.min_gem_count if requirements else None
        suffix = f" < required {required}" if required is not None else " below required count"
        problems.append(f"gems {compliance.total_gems}{suffix}")
    if not compliance.gem_quality_pass:
        problems.append(f"wrong-quality gem(s): {', '.join(str(g) for g in compliance.wrong_quality_gem_ids)}")
    if not compliance.item_level_pass:
        required = requirements.min_item_level if requirements else None
        suffix = f" < required {required}" if required is not None else " below required"
        problems.append(f"ilvl {compliance.average_item_level:.1f}{suffix}")
    if not compliance.quality_pass:
        lowest_name = gear_analyzer.quality_name(compliance.lowest_quality) if compliance.lowest_quality is not None else "n/a"
        required_name = (
            gear_analyzer.quality_name(requirements.min_quality)
            if requirements and requirements.min_quality is not None else None
        )
        suffix = f" below required {required_name}" if required_name else " below required tier"
        problems.append(f"quality {lowest_name}{suffix}")
    return problems


def build_raid_scorecard(
    parsed_fight: ParsedFight,
    consumable_results: list[PlayerConsumables] | None = None,
    mandatory_consumable_categories: list[str] | None = None,
    gear_compliance_results: list[PlayerGearCompliance] | None = None,
    gear_requirements: GearRequirements | None = None,
    avoidable_damage_reports: list[PlayerAvoidableDamage] | None = None,
    defensive_cooldown_usages: list[CooldownUsage] | None = None,
) -> list[PlayerScorecardEntry]:
    """
    Combine whichever analyzer results are provided into one ranked
    list of PlayerScorecardEntry, sorted by problem_count DESCENDING.

    Every list parameter is optional. gear_requirements is only used to
    make gear failure descriptions more specific.

    The player roster is the UNION of every player_id seen across
    whatever result lists were given. If NONE were given, falls back to
    this fight's own roster so every present player still gets a
    (fully clean) entry.
    """
    entries_by_id: dict[int, PlayerScorecardEntry] = {}

    def _get_or_create(player_id: int, player_name: str) -> PlayerScorecardEntry:
        if player_id not in entries_by_id:
            entries_by_id[player_id] = PlayerScorecardEntry(player_id=player_id, player_name=player_name)
        return entries_by_id[player_id]

    if consumable_results:
        categories = mandatory_consumable_categories or []
        for player_consumables in consumable_results:
            entry = _get_or_create(player_consumables.player_id, player_consumables.player_name)
            entry.consumable_missing_categories = player_consumables.missing_categories(categories)

    if gear_compliance_results:
        for compliance in gear_compliance_results:
            entry = _get_or_create(compliance.player_id, compliance.player_name)
            if not compliance.has_data:
                entry.gear_data_missing = True
            else:
                entry.gear_failed_checks = _describe_gear_failures(compliance, gear_requirements)

    if avoidable_damage_reports:
        for report in avoidable_damage_reports:
            entry = _get_or_create(report.player_id, report.player_name)
            entry.avoidable_mechanics_hit = [
                f"{name} x{count}" for name, count in report.hits_by_mechanic.items() if count > 0
            ]
            entry.avoidable_distinct_mechanic_count = report.distinct_mechanics_hit
            entry.avoidable_total_hits = report.total_avoidable_hits

    if defensive_cooldown_usages:
        for usage in defensive_cooldown_usages:
            if usage.player_id is None:
                continue
            entry = _get_or_create(usage.player_id, usage.player_name or "Unknown")
            entry.defensive_usages.append(usage)

    if not entries_by_id:
        for player_id, actor in roster.get_fight_roster(parsed_fight).items():
            _get_or_create(player_id, actor.name)

    return sorted(
        entries_by_id.values(),
        key=lambda e: (-e.problem_count, e.player_name or ""),
    )


def get_player_entry(entries: list[PlayerScorecardEntry], player_name: str) -> PlayerScorecardEntry | None:
    """Convenience lookup: find one player's scorecard entry by name (case-insensitive)."""
    for entry in entries:
        if entry.player_name and entry.player_name.lower() == player_name.lower():
            return entry
    return None


def format_scorecard_table(entries: list[PlayerScorecardEntry]) -> str:
    """Render a plain-text overview table: one row per player, ranked worst-to-best."""
    if not entries:
        return "No players to score for this fight."

    header = f"{'Player':<15} {'Problems':>8} {'Consumables':>11} {'Gear':>16} {'Avoidable':>10}"
    lines = ["Raid scorecard (ranked worst to best):", header, "-" * len(header)]
    for entry in entries:
        gear_cell = "no data" if entry.gear_data_missing else str(len(entry.gear_failed_checks))
        lines.append(
            f"{entry.player_name[:15]:<15} {entry.problem_count:>8} "
            f"{len(entry.consumable_missing_categories):>11} {gear_cell:>16} "
            f"{entry.avoidable_distinct_mechanic_count:>10}"
        )
    return "\n".join(lines)


def format_scorecard_detail(entry: PlayerScorecardEntry, fight_duration_ms: int = 0) -> str:
    """Render one player's full scorecard detail -- every category spelled out."""
    lines = [f"{entry.player_name} -- {entry.problem_count} problem area(s){' -- CLEAN PULL' if entry.is_clean else ''}"]

    if entry.consumable_missing_categories:
        lines.append(f"  Consumables missing: {', '.join(entry.consumable_missing_categories)}")

    if entry.gear_data_missing:
        lines.append("  Gear: no data (no CombatantInfo snapshot -- not a failure, just unknown)")
    elif entry.gear_failed_checks:
        lines.append(f"  Gear failed: {'; '.join(entry.gear_failed_checks)}")

    if entry.avoidable_mechanics_hit:
        lines.append(
            f"  Avoidable damage: {entry.avoidable_total_hits} total hit(s) across "
            f"{entry.avoidable_distinct_mechanic_count} mechanic(s) -- {', '.join(entry.avoidable_mechanics_hit)}"
        )

    if entry.defensive_usages:
        efficiency = entry.defensive_overall_efficiency(fight_duration_ms) * 100 if fight_duration_ms else 0.0
        names = ", ".join(u.ability_name for u in entry.defensive_usages)
        lines.append(
            f"  Defensive cooldowns used (informational, not scored): {names} "
            f"({entry.defensive_total_casts} total cast(s), ~{efficiency:.0f}% blended efficiency)"
        )

    return "\n".join(lines)


def summarize_raid_scorecard(
    parsed_fight: ParsedFight, entries: list[PlayerScorecardEntry], show_clean_players: bool = True
) -> str:
    """Report-ready summary string: the overview table, followed by full detail for every player."""
    if not entries:
        return f"{parsed_fight.fight.name}: no roster data available for a scorecard."
    if all(e.is_clean for e in entries):
        return f"{parsed_fight.fight.name}: every scored player is clean this pull!"

    lines = [f"{parsed_fight.fight.name} -- raid scorecard:", "", format_scorecard_table(entries), ""]
    for entry in entries:
        if not entry.is_clean or show_clean_players:
            lines.append(format_scorecard_detail(entry, parsed_fight.fight.duration_ms))
    return "\n".join(lines)
