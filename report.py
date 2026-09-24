"""
report.py
Pure rendering module: combines already-computed analyzer results into
text/Markdown. No analyzers called here.

Timestamps are rendered as M:SS (e.g. 3:03) via
time_format.format_timestamp(), rather than raw seconds (e.g. 183.0s),
across every section of every output format (.txt, .md, and
html_report.py's .html) for consistency and easier reading during raid
review.
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
from time_format import format_timestamp


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
        f"{fight.name}  [{difficulty_str}]  ({status}, {format_timestamp(fight.duration_ms)})",
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


def _role_of(player_id: int | None, data: FightReportData) -> str:
    if player_id is None:
        return ""
    role_info = data.player_roles.get(player_id)
    return role_info.role if role_info else ""


def render_markdown(data: FightReportData) -> str:
    fight = data.parsed_fight.fight
    status = "Kill" if fight.kill else "Wipe"
    difficulty_str = difficulty_names.difficulty_name(fight.difficulty)
    duration_ms = fight.duration_ms
    lines = [f"# {fight.name} -- {difficulty_str} {status} ({format_timestamp(duration_ms)})", ""]

    # 1. Deaths
    if data.death_reports:
        lines.append("## Deaths")
        rows = [[format_timestamp(r.time_into_fight_ms), r.victim_name or "Unknown", r.killing_ability_name or "Unknown",
                  f"{r.total_damage_taken_in_window:,}", f"{r.total_effective_healing_in_window:,}"] for r in data.death_reports]
        lines.append(_md_table(["Time", "Victim", "Killed By", "Dmg (window)", "Effective Heal (window)"], rows))
        lines.append("")

    # 2. Avoidable Damage
    if data.avoidable_config is not None:
        lines.append("## Avoidable Damage")
        hit_reports = [r for r in data.avoidable_reports if r.total_avoidable_hits > 0]
        rows = [
            [r.player_name, str(r.total_avoidable_hits),
             ", ".join(f"{n} x{c}" for n, c in r.hits_by_mechanic.items() if c > 0)]
            for r in hit_reports
        ]
        if rows:
            lines.append(_md_table(["Player", "Total Hits", "Breakdown"], rows))
        else:
            lines.append("_No avoidable hits taken. Clean pull!_")
        lines.append("")

    # 3. Healing -- restricted to tank/healer rows whenever role data is available.
    if data.healer_summaries:
        lines.append("## Healing")
        has_role_data = bool(data.player_roles)
        relevant = [
            s for s in data.healer_summaries
            if not has_role_data or _role_of(s.healer_id, data) in ("tank", "healer")
        ]
        rows = [
            [s.healer_name, f"{s.hps(duration_ms):,.0f}", f"{s.total_effective_healing:,}",
             f"{s.total_overheal:,}", f"{s.overheal_percent:.1f}%"]
            for s in relevant
        ]
        if rows:
            lines.append(_md_table(["Healer", "HPS", "Effective", "Overheal", "Overheal %"], rows))
        else:
            lines.append("_No healing recorded from healers/tanks._" if has_role_data else "_No healing recorded._")
        lines.append("")

    # 4. Damage Done (DPS)
    if data.damage_done_summaries:
        lines.append("## Damage Done (DPS)")
        rows = [[s.player_name, f"{s.dps(duration_ms):,.0f}", f"{s.total_damage_done:,}"] for s in data.damage_done_summaries]
        lines.append(_md_table(["Player", "DPS", "Total"], rows))
        lines.append("")

    # 5. Damage Taken
    if data.damage_taken_summaries:
        lines.append("## Damage Taken")
        rows = [[s.target_name, f"{s.dtps(duration_ms):,.0f}", f"{s.total_damage_taken:,}"] for s in data.damage_taken_summaries]
        lines.append(_md_table(["Player", "DTPS", "Total"], rows))
        lines.append("")

    # 6. Biggest Hits
    if data.biggest_hits:
        note = " (tanks excluded)" if data.player_roles and any(r.role == "tank" for r in data.player_roles.values()) else ""
        lines.append(f"## Biggest Hits{note}")
        rows = [[f"{hit.amount:,}", hit.ability_name or "Unknown", hit.target_name or "Unknown"] for hit in data.biggest_hits]
        lines.append(_md_table(["Amount", "Ability", "Target"], rows))
        lines.append("")

    # 7. Raid Cooldown Usage
    if data.cooldown_usages:
        lines.append("## Raid Cooldown Usage")
        rows = [
            [(u.player_name or "Unknown"), u.ability_name, f"{u.num_casts}/{u.theoretical_max_casts(duration_ms)}",
             f"{u.efficiency(duration_ms) * 100:.1f}%"]
            for u in data.cooldown_usages
        ]
        lines.append(_md_table(["Player", "Ability", "Casts/Max", "Efficiency"], rows))
        lines.append("")

    # 8. Defensive Cooldown Usage
    if data.defensive_cooldown_usages:
        lines.append("## Defensive Cooldown Usage")
        rows = [
            [(u.player_name or "Unknown"), u.ability_name, f"{u.num_casts}/{u.theoretical_max_casts(duration_ms)}",
             f"{u.efficiency(duration_ms) * 100:.1f}%"]
            for u in data.defensive_cooldown_usages
        ]
        lines.append(_md_table(["Player", "Ability", "Casts/Max", "Efficiency"], rows))
        lines.append("")

    # 9. Damage Prevented by Defensives
    if data.defensive_damage_prevention:
        lines.append("## Damage Prevented by Defensives")
        overview_rows = []
        for e in data.defensive_damage_prevention:
            has_estimate = bool(e.windows_with_estimate)
            prevented_cell = f"{e.total_damage_prevented:,}" if has_estimate else "n/a"
            overview_rows.append([
                e.player_name, prevented_cell,
                f"{e.total_actual_damage_taken_during_windows:,}", str(len(e.windows)),
            ])
        lines.append(_md_table(["Player", "Prevented", "Dmg Taken (windows)", "Windows"], overview_rows))
        lines.append("")
        for entry in data.defensive_damage_prevention:
            if not entry.windows:
                continue
            lines.append(f"**{entry.player_name}**")
            if not entry.windows_with_estimate:
                lines.append(
                    "_Only immunity-type defensives used -- no damage-prevented estimate is possible "
                    "for these (see Prevented column above showing \"n/a\", not a measured zero)._"
                )
            detail_rows = []
            for w in entry.windows:
                if w.mitigation_type == "immunity":
                    detail_rows.append([
                        format_timestamp(w.cast_timestamp), w.ability_name, "immunity",
                        f"{w.actual_damage_taken:,} (residual)", "n/a",
                    ])
                else:
                    detail_rows.append([
                        format_timestamp(w.cast_timestamp), w.ability_name,
                        f"{w.damage_reduction_percent:.0f}% / {w.window_duration_seconds:.0f}s",
                        f"{w.actual_damage_taken:,}", f"{w.damage_prevented:,}",
                    ])
            lines.append(_md_table(["Time", "Ability", "Mitigation", "Dmg Taken", "Prevented"], detail_rows))
            lines.append("")

    # 10. Consumables
    if data.consumable_results:
        lines.append("## Consumables")
        ranked = sorted(
            data.consumable_results,
            key=lambda p: len(p.missing_categories(data.consumable_categories)), reverse=True,
        )
        rows = [
            [p.player_name, str(len(p.missing_categories(data.consumable_categories))),
             ", ".join(p.missing_categories(data.consumable_categories)) or "all covered"]
            for p in ranked
        ]
        lines.append(_md_table(["Player", "# Missing", "Missing"], rows))
        if data.vantus_check is not None:
            vc = data.vantus_check
            if not vc.threshold_met:
                lines.append(f"\n_Vantus Rune: not majority-used ({len(vc.players_with)} had it)._")
            elif vc.players_missing:
                lines.append(f"\n_Vantus Rune: majority using it -- missing: {', '.join(vc.players_missing)}._")
            else:
                lines.append("\n_Vantus Rune: everyone who should have it, has it._")
        lines.append("")

    # 11. Gear Check
    if data.gear_reports:
        lines.append("## Gear Check")
        rows = []
        for r in data.gear_reports:
            if not r.has_data:
                rows.append([r.player_name, "no data", "no data", "no data", "no data"])
                continue
            lowest = gear_analyzer.quality_name(r.lowest_quality) if r.lowest_quality is not None else "n/a"
            rows.append([
                r.player_name, f"{r.average_item_level:.1f}", lowest,
                str(r.total_gems), ", ".join(r.missing_enchant_slots) or "none",
            ])
        lines.append(_md_table(["Player", "Avg iLvl", "Lowest Quality", "Gems", "Missing Enchants"], rows))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report(data: FightReportData, output_dir: str, base_filename: str) -> tuple[str, str]:
    import os
    text_path = os.path.join(output_dir, f"{base_filename}.txt")
    markdown_path = os.path.join(output_dir, f"{base_filename}.md")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(render_text(data))
    with open(markdown_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(data))
    return text_path, markdown_path
