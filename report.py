"""
report.py

Pure rendering module: combines already-computed analyzer results into
text/Markdown. No analyzers called here.

CHANGED: the fight's difficulty (LFR/Normal/Heroic/Mythic -- see
difficulty_names.py) is now included in the header of both render_text
and render_markdown, right alongside the kill/wipe status -- this was
previously missing, making it unclear whether a given pull was e.g.
Heroic or Mythic just from looking at the report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from avoidable_damage_analyzer import PlayerAvoidableDamage
from avoidable_damage_data import EncounterConfig
import avoidable_damage_analyzer
from consumables_analyzer import PlayerConsumables, VantusRuneCheck
import consumables_analyzer
from cooldown_analyzer import CooldownUsage
import cooldown_analyzer
from damage_analyzer import DamageTakenSummary
import damage_analyzer
from damage_done_analyzer import DamageDoneSummary
import damage_done_analyzer
from data_models import Event, ParsedFight
from death_analyzer import DeathReport
import death_analyzer
from defensive_damage_prevention_analyzer import PlayerDamagePrevention
import difficulty_names
from gear_analyzer import PlayerGearReport
import gear_analyzer
from healing_analyzer import HealerSummary
import healing_analyzer
from player_roles import PlayerRole


@dataclass
class FightReportData:
    parsed_fight: ParsedFight
    death_reports: list[DeathReport] = field(default_factory=list)
    healer_summaries: list[HealerSummary] = field(default_factory=list)
    damage_taken_summaries: list[DamageTakenSummary] = field(default_factory=list)
    biggest_hits: list[Event] = field(default_factory=list)
    damage_done_summaries: list[DamageDoneSummary] = field(default_factory=list)
    cooldown_usages: list[CooldownUsage] = field(default_factory=list)
    defensive_cooldown_usages: list[CooldownUsage] = field(default_factory=list)
    defensive_damage_prevention: list[PlayerDamagePrevention] = field(default_factory=list)
    consumable_results: list[PlayerConsumables] = field(default_factory=list)
    consumable_categories: list[str] = field(default_factory=list)
    vantus_check: VantusRuneCheck | None = None
    gear_reports: list[PlayerGearReport] = field(default_factory=list)
    avoidable_reports: list[PlayerAvoidableDamage] = field(default_factory=list)
    avoidable_config: EncounterConfig | None = None
    player_roles: dict[int, PlayerRole] = field(default_factory=dict)


def render_text(data: FightReportData) -> str:
    fight = data.parsed_fight.fight
    status = "KILL" if fight.kill else "wipe"
    difficulty_str = difficulty_names.difficulty_name(fight.difficulty)
    sections = [
        f"{fight.name}  [{difficulty_str}]  ({status}, {fight.duration_ms / 1000:.1f}s)",
        "=" * 60,
    ]

    def section(title: str, body: str) -> None:
        sections.append(f"\n-- {title} " + "-" * max(0, 50 - len(title)))
        sections.append(body)

    if data.death_reports or data.parsed_fight.events:
        section("Deaths", death_analyzer.summarize_wipe(data.parsed_fight, data.death_reports))
    if data.avoidable_config is not None:
        section("Avoidable Damage", avoidable_damage_analyzer.summarize_avoidable_damage(data.parsed_fight, data.avoidable_reports, data.avoidable_config))
    if data.healer_summaries:
        section("Healing", healing_analyzer.summarize_healing(data.parsed_fight, data.healer_summaries))
    if data.damage_done_summaries:
        section("Damage Done (DPS)", damage_done_analyzer.summarize_damage_done(data.parsed_fight, data.damage_done_summaries))
    if data.damage_taken_summaries:
        section("Damage Taken", damage_analyzer.summarize_damage_taken(data.parsed_fight, data.damage_taken_summaries))
    if data.biggest_hits:
        note = " (tanks excluded)" if data.player_roles and any(r.role == "tank" for r in data.player_roles.values()) else ""
        hits_lines = [f"  {hit.amount:>10,}  {hit.ability_name or 'Unknown':<25} on {hit.target_name or 'Unknown'}" for hit in data.biggest_hits]
        section(f"Biggest Hits{note}", "\n".join(hits_lines))
    if data.cooldown_usages:
        section("Raid Cooldown Usage", cooldown_analyzer.summarize_cooldown_usage(data.parsed_fight, data.cooldown_usages))
    if data.defensive_cooldown_usages:
        section("Defensive Cooldown Usage", cooldown_analyzer.summarize_cooldown_usage(data.parsed_fight, data.defensive_cooldown_usages))
    if data.consumable_results:
        section("Consumables", consumables_analyzer.summarize_consumables(data.parsed_fight, data.consumable_results, data.consumable_categories, vantus_check=data.vantus_check))
    if data.gear_reports:
        section("Gear Check", gear_analyzer.summarize_gear(data.gear_reports))
    return "\n".join(sections)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No data._"
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def render_markdown(data: FightReportData) -> str:
    fight = data.parsed_fight.fight
    status = "Kill" if fight.kill else "Wipe"
    difficulty_str = difficulty_names.difficulty_name(fight.difficulty)
    duration_ms = fight.duration_ms
    lines = [f"# {fight.name} -- {difficulty_str} {status} ({duration_ms / 1000:.1f}s)", ""]
    if data.death_reports:
        lines.append("## Deaths")
        rows = [[f"{r.time_into_fight_ms / 1000:.1f}s", r.victim_name or "Unknown", r.killing_ability_name or "Unknown",
                  f"{r.total_damage_taken_in_window:,}", f"{r.total_effective_healing_in_window:,}"] for r in data.death_reports]
        lines.append(_md_table(["Time", "Victim", "Killed By", "Dmg (window)", "Effective Heal (window)"], rows))
        lines.append("")
    return "\n".join(lines)


def write_report(data: FightReportData, output_dir: str, base_filename: str) -> tuple[str, str]:
    import os
    text_path = os.path.join(output_dir, f"{base_filename}.txt")
    markdown_path = os.path.join(output_dir, f"{base_filename}.md")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(render_text(data))
    with open(markdown_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(data))
    return text_path, markdown_path
