"""
reporting.py

MERGED MODULE -- combines what used to be five separate files into one,
as part of the PullDoctor file-count reduction pass:
    - report_filename.py  (build the reports/<Name>-<DDMMYYYY><HHMM>.html output path)
    - docs_index.py        (GitHub Pages index.html listing every generated report)
    - report.py            (FightReportData + plain-text/Markdown rendering)
    - raid_scorecard.py    (unified per-player "how many problems" scorecard)
    - html_report.py       (the full collapsible/filterable HTML report)

These five were grouped together because they're all part of the same
"turn already-computed analyzer results into an artifact a raid lead
actually reads" layer -- none of them run an analyzer themselves (see
each section's own docstring for that guarantee); they only compose,
format, and write out what other merged modules (core.py, gear.py,
boss_mechanics.py, consumables.py, fight_analyzers.py,
raid_cooldowns.py, defensive_cooldowns.py, tier_sets.py) already
produced.

Sections are ordered by dependency: report_filename (no deps) ->
docs_index (no deps, but its docstring references Section 1's naming
convention) -> report (depends on every analyzer module + core.py) ->
raid_scorecard (depends on consumables.py/gear.py/boss_mechanics.py/
defensive_cooldowns.py/core.py) -> html_report (depends on Section 3's
FightReportData, tier_sets.py, core.py).

NOTE on cross-file symbol renames: the five original files imported
from a dozen now-merged modules under their OLD standalone names
(avoidable_damage_analyzer.py, consumables_analyzer.py,
cooldown_analyzer.py, damage_analyzer.py, damage_done_analyzer.py,
death_analyzer.py, defensive_damage_prevention_analyzer.py,
gear_analyzer.py, gear_compliance_analyzer.py, gear_schema.py,
healing_analyzer.py, tier_set_analyzer.py, tier_set_data.py,
tier_set_schema.py, class_colors.py, difficulty_names.py,
player_roles.py, roster.py, time_format.py, data_models.py). All of
these have since been merged elsewhere in this project (fight_
analyzers.py, boss_mechanics.py, consumables.py, gear.py,
defensive_cooldowns.py, raid_cooldowns.py, tier_sets.py, core.py) --
every import below has been updated to pull from the CURRENT merged
location instead. Every renamed call site keeps the exact same
function/argument signature as the original; only the import path
changed.

NOTE on the duplicated CooldownUsage engine: raid_cooldowns.py and
defensive_cooldowns.py each carry their OWN (byte-for-byte identical)
copy of CooldownUsage/summarize_cooldown_usage, per that merge's own
documented "duplicated on purpose to avoid a 13th shared module"
decision. FightReportData below keeps that distinction explicit
(cooldown_usages: list[RaidCooldownUsage], defensive_cooldown_usages:
list[DefensiveCooldownUsage]) rather than collapsing them back into one
ambiguous type -- this is actually MORE precise than the original
single-cooldown_analyzer.py version, not a behavior change, since both
classes are structurally identical and every call site already used
them via simple duck-typing.

NOTE for whoever next touches generate_html_report.py: that CLI (not
part of this merge batch) currently does `import html_report` and
`import report_filename` at module scope -- both names no longer exist
as standalone files after this merge. It needs `import reporting` (or
`from reporting import ...`) instead, calling `reporting.write_html_report(...)`,
`reporting.ensure_reports_dir()`, and `reporting.build_report_path(...)`
in place of the old module-qualified calls. Flagged here rather than
silently touched, since that file wasn't part of this merge batch.
"""
from __future__ import annotations

import datetime
import html as html_module
import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field

from boss_mechanics import EncounterConfig, PlayerAvoidableDamage, summarize_avoidable_damage
from consumables import PlayerConsumables, VantusRuneCheck, summarize_consumables
from core import (
    Event,
    ParsedFight,
    PlayerRole,
    difficulty_color,
    difficulty_name,
    fight_relative_ms,
    format_timestamp,
    get_class_color,
    get_fight_roster,
    resolve_to_player,
)
from defensive_cooldowns import (
    CooldownUsage as DefensiveCooldownUsage,
    PlayerDamagePrevention,
    summarize_cooldown_usage as summarize_defensive_cooldown_usage,
)
from fight_analyzers import (
    DamageDoneSummary,
    DamageTakenSummary,
    DeathReport,
    HealerSummary,
    summarize_damage_done,
    summarize_damage_taken,
    summarize_healing,
    summarize_wipe,
)
from gear import (
    GearRequirements,
    PlayerGearCompliance,
    PlayerGearReport,
    quality_name,
    summarize_gear,
)
from raid_cooldowns import (
    CooldownUsage as RaidCooldownUsage,
    summarize_cooldown_usage as summarize_raid_cooldown_usage,
)
from tier_sets import (
    MISSING_TIER_PIECE_CHAR,
    PlayerTierSetReport,
    TOTAL_TIER_SLOTS,
    track_color_for_letter,
)


# =======================================================================
# SECTION 1 -- output filename builder
# (originally report_filename.py)
#
# Builds the output path for the HTML report:
#     reports/<SanitizedRaidName>-<DDMMYYYY><HHMM>.html
#
# The date/time used is ALWAYS "now" -- the moment the report is being
# GENERATED on your machine -- never the date the raid actually
# happened or anything read from the log itself. This lets you re-run
# the same report code multiple times (e.g. after a consumables config
# fix) and get a distinctly-named file each time rather than
# overwriting the previous one.
#
# Example: a raid zone named "The Venomous Abyss", report generated on
# 20 September 2026 at 11:47, produces:
#     reports/TheVenomousAbyss-200920261147.html
#
# Sanitization strips every character that isn't a letter or digit
# (spaces, apostrophes, colons, etc.) -- "The Venomous Abyss" becomes
# "TheVenomousAbyss". The FULL filename always ends with the literal
# ".html" extension -- build_report_filename()/build_report_path() both
# guarantee this unconditionally, so the output is never accidentally
# saved with no extension (which some file explorers display as a
# generic "File" type rather than recognizing it as HTML).
# =======================================================================

REPORTS_SUBFOLDER = "reports"
_FALLBACK_NAME = "Raid"
_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9]")
_EXTENSION = ".html"


def sanitize_for_filename(name: str) -> str:
    """Strip every non-alphanumeric character. Falls back to _FALLBACK_NAME if that leaves nothing."""
    cleaned = _SANITIZE_PATTERN.sub("", name or "")
    return cleaned or _FALLBACK_NAME


def build_report_filename(raid_name: str, generated_at: datetime.datetime | None = None) -> str:
    """
    Build just the filename (no directory), e.g.
    "TheVenomousAbyss-200920261147.html". `generated_at` defaults to
    datetime.datetime.now() -- pass an explicit value only for testing.
    """
    generated_at = generated_at or datetime.datetime.now()
    timestamp = generated_at.strftime("%d%m%Y%H%M")
    return f"{sanitize_for_filename(raid_name)}-{timestamp}{_EXTENSION}"


def build_report_path(
    raid_name: str,
    reports_dir: str = REPORTS_SUBFOLDER,
    generated_at: datetime.datetime | None = None,
) -> str:
    """Build the full path, e.g. "reports/TheVenomousAbyss-200920261147.html", relative to the current working directory."""
    filename = build_report_filename(raid_name, generated_at=generated_at)
    return os.path.join(reports_dir, filename)


def ensure_reports_dir(reports_dir: str = REPORTS_SUBFOLDER) -> str:
    """Create the reports subfolder if it doesn't exist yet (no-op if it already does). Returns the directory path."""
    os.makedirs(reports_dir, exist_ok=True)
    return reports_dir


def ensure_html_extension(path: str) -> str:
    """
    Defensive helper: guarantees `path` ends with ".html", regardless
    of what was passed in. Used as a final safety net for a
    user-supplied --output-path override in the HTML-report CLI, so an
    explicit override can never accidentally produce an extensionless
    (or wrongly-extensioned) file either.
    """
    if path.lower().endswith(_EXTENSION):
        return path
    return path + _EXTENSION


# =======================================================================
# SECTION 2 -- GitHub Pages report index
# (originally docs_index.py)
#
# Builds a simple index.html listing every generated report under
# docs/reports/, most recent first -- for GitHub Pages. Regenerated
# fully from scratch on every run (stateless: it just scans whatever
# .html files currently exist in the reports folder), so there's no
# separate "database" of past reports to keep in sync.
#
# Sorting is done by parsing the "-<DDMMYYYY><HHMM>.html" timestamp
# suffix that Section 1's build_report_filename() always appends --
# NOT by filename string order (which would sort incorrectly whenever
# two reports have different raid-name prefixes of different lengths)
# and NOT by filesystem mtime (which would be wrong if files are ever
# re-copied/checked out by git in a different order than they were
# originally created, e.g. after a fresh clone -- git does not
# preserve original file creation/modification timestamps).
# =======================================================================

_TIMESTAMP_SUFFIX_RE = re.compile(r"-(\d{2})(\d{2})(\d{4})(\d{2})(\d{2})\.html$", re.IGNORECASE)


@dataclass
class IndexedReport:
    filename: str
    raid_name: str  # the sanitized name portion before the timestamp -- NOT necessarily human-readable (see sanitize_for_filename)
    generated_at: datetime.datetime


def parse_report_filename(filename: str) -> IndexedReport | None:
    """
    Parse one Section-1-style filename into an IndexedReport. Returns
    None (rather than raising) for any .html file that doesn't match
    the expected "<Name>-<DDMMYYYY><HHMM>.html" pattern -- e.g. a
    hand-placed file that isn't one of this project's own generated
    reports -- so a stray file in the folder can't crash index
    generation; it's just silently excluded from the index instead.
    """
    match = _TIMESTAMP_SUFFIX_RE.search(filename)
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    try:
        generated_at = datetime.datetime(int(year), int(month), int(day), int(hour), int(minute))
    except ValueError:
        return None  # e.g. a filename that LOOKS like the pattern but has an impossible date (day=99, month=13, etc.)
    raid_name = filename[: match.start()]
    return IndexedReport(filename=filename, raid_name=raid_name, generated_at=generated_at)


def scan_reports_directory(reports_dir: str) -> list[IndexedReport]:
    """
    List every valid report in `reports_dir`, sorted NEWEST FIRST by
    their parsed generation timestamp. Returns an empty list (not an
    error) if the directory doesn't exist yet -- e.g. the very first
    time this ever runs, before any report has been generated.
    """
    if not os.path.isdir(reports_dir):
        return []
    indexed = []
    for filename in os.listdir(reports_dir):
        if not filename.lower().endswith(".html"):
            continue
        parsed = parse_report_filename(filename)
        if parsed is not None:
            indexed.append(parsed)
    indexed.sort(key=lambda r: r.generated_at, reverse=True)
    return indexed


def _esc_index(value) -> str:
    return html_module.escape(str(value))


def render_index_html(
    reports: list[IndexedReport],
    reports_subfolder_name: str = "reports",
    title: str = "Raid Reports",
) -> str:
    """
    Render a minimal, dependency-free index page listing every report,
    newest first, each linking to its file under `reports_subfolder_name/`.
    """
    redirect_tag = ""
    if not reports:
        body = '<p class="empty">No reports have been generated yet.</p>'
    else:
        items = []
        for r in reports:
            timestamp_str = r.generated_at.strftime("%A %d %B %Y, %H:%M")
            href = f"{reports_subfolder_name}/{r.filename}"
            items.append(
                f'<li><a href="{_esc_index(href)}">{_esc_index(r.raid_name)}</a>'
                f'<span class="ts"> &mdash; generated {_esc_index(timestamp_str)}</span></li>'
            )
        body = "<ul>" + "".join(items) + "</ul>"
        # Auto-redirect straight to the newest report (reports[0], since
        # scan_reports_directory sorts newest-first). A <meta refresh> is
        # used rather than JS so it still works with JS disabled. The list
        # above still renders underneath, as a fallback/history page for
        # anyone who lands here with the redirect blocked or wants to
        # browse older reports.
        newest_href = f"{reports_subfolder_name}/{reports[0].filename}"
        redirect_tag = f'<meta http-equiv="refresh" content="0; url={_esc_index(newest_href)}">\n'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
{redirect_tag}<title>{_esc_index(title)}</title>
<style>
body {{ background:#14151a; color:#e8e8ec; font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif; margin:0; padding:24px; }}
h1 {{ font-size:20px; margin:0 0 16px 0; }}
ul {{ list-style:none; padding:0; margin:0; max-width:700px; }}
li {{ padding:10px 14px; border-bottom:1px solid #2f3140; }}
a {{ color:#6c8cff; text-decoration:none; font-weight:600; }}
a:hover {{ text-decoration:underline; }}
.ts {{ color:#9a9db0; font-size:12.5px; }}
.empty {{ color:#9a9db0; font-style:italic; }}
</style>
</head>
<body>
<h1>{_esc_index(title)}</h1>
{body}
</body>
</html>"""


def build_index(reports_dir: str, index_path: str, title: str = "Raid Reports") -> list[IndexedReport]:
    """
    Scan `reports_dir` and write index.html to `index_path`. Returns
    the list of reports found, so the caller can log/print a summary
    without needing to re-scan the directory itself.
    """
    reports = scan_reports_directory(reports_dir)
    reports_subfolder_name = os.path.basename(os.path.normpath(reports_dir))
    html_content = render_index_html(reports, reports_subfolder_name=reports_subfolder_name, title=title)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return reports


# =======================================================================
# SECTION 3 -- FightReportData + plain-text/Markdown rendering
# (originally report.py)
#
# Pure rendering: combines already-computed analyzer results into
# text/Markdown. No analyzers called here.
#
# Gear Check's Markdown/text table drops "Lowest Quality" in favor of
# two columns -- "Tier Pieces" (X/5) and "Tier Set" (the 5-char track
# string, e.g. "MMHCC" -- see Section 5/tier_sets.py for the letter
# legend and slot order) -- joined against tier_set_reports by
# player_id. A player with no tier_set_reports entry at all (e.g. the
# tier-set analyzer wasn't run) falls back to "n/a"/"-----" rather than
# crashing.
#
# Timestamps are rendered as M:SS (e.g. 3:03) via
# core.format_timestamp(), rather than raw seconds (e.g. 183.0s),
# across every section of every output format (.txt, .md, and Section
# 5's .html) for consistency and easier reading during raid review.
# =======================================================================

@dataclass
class FightReportData:
    parsed_fight: ParsedFight
    death_reports: list[DeathReport] = field(default_factory=list)
    healer_summaries: list[HealerSummary] = field(default_factory=list)
    damage_taken_summaries: list[DamageTakenSummary] = field(default_factory=list)
    biggest_hits: list[Event] = field(default_factory=list)
    damage_done_summaries: list[DamageDoneSummary] = field(default_factory=list)
    cooldown_usages: list[RaidCooldownUsage] = field(default_factory=list)
    defensive_cooldown_usages: list[DefensiveCooldownUsage] = field(default_factory=list)
    defensive_damage_prevention: list[PlayerDamagePrevention] = field(default_factory=list)
    consumable_results: list[PlayerConsumables] = field(default_factory=list)
    consumable_categories: list[str] = field(default_factory=list)
    vantus_check: VantusRuneCheck | None = None
    gear_reports: list[PlayerGearReport] = field(default_factory=list)
    tier_set_reports: list[PlayerTierSetReport] = field(default_factory=list)
    avoidable_reports: list[PlayerAvoidableDamage] = field(default_factory=list)
    avoidable_config: EncounterConfig | None = None
    player_roles: dict[int, PlayerRole] = field(default_factory=dict)


def render_text(data: FightReportData) -> str:
    fight = data.parsed_fight.fight
    status = "KILL" if fight.kill else "wipe"
    difficulty_str = difficulty_name(fight.difficulty)
    sections = [
        f"{fight.name}  [{difficulty_str}]  ({status}, {format_timestamp(fight.duration_ms)})",
        "=" * 60,
    ]

    def section(title: str, body: str) -> None:
        sections.append(f"\n-- {title} " + "-" * max(0, 50 - len(title)))
        sections.append(body)

    if data.death_reports or data.parsed_fight.events:
        section("Deaths", summarize_wipe(data.parsed_fight, data.death_reports))
    if data.avoidable_config is not None:
        section("Avoidable Damage", summarize_avoidable_damage(data.parsed_fight, data.avoidable_reports, data.avoidable_config))
    if data.healer_summaries:
        section("Healing", summarize_healing(data.parsed_fight, data.healer_summaries))
    if data.damage_done_summaries:
        section("Damage Done (DPS)", summarize_damage_done(data.parsed_fight, data.damage_done_summaries))
    if data.damage_taken_summaries:
        section("Damage Taken", summarize_damage_taken(data.parsed_fight, data.damage_taken_summaries))
    if data.biggest_hits:
        note = " (tanks excluded)" if data.player_roles and any(r.role == "tank" for r in data.player_roles.values()) else ""
        hits_lines = [f"  {hit.amount:>10,}  {hit.ability_name or 'Unknown':<25} on {hit.target_name or 'Unknown'}" for hit in data.biggest_hits]
        section(f"Biggest Hits{note}", "\n".join(hits_lines))
    if data.cooldown_usages:
        section("Raid Cooldown Usage", summarize_raid_cooldown_usage(data.parsed_fight, data.cooldown_usages))
    if data.defensive_cooldown_usages:
        section("Defensive Cooldown Usage", summarize_defensive_cooldown_usage(data.parsed_fight, data.defensive_cooldown_usages))
    if data.consumable_results:
        section("Consumables", summarize_consumables(data.parsed_fight, data.consumable_results, data.consumable_categories, vantus_check=data.vantus_check))
    if data.gear_reports:
        section("Gear Check", summarize_gear(data.gear_reports))
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
    difficulty_str = difficulty_name(fight.difficulty)
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
                    'for these (see Prevented column above showing "n/a", not a measured zero)._'
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

    # 11. Gear Check -- "Lowest Quality" REMOVED, replaced with "Tier
    # Pieces" (X/5) and "Tier Set" (5-char track string), joined against
    # tier_set_reports by player_id. A player with no tier_set_reports
    # entry (e.g. the analyzer wasn't run this pass) falls back to
    # "n/a" / a string of dashes rather than a crash or a misleading 0/5.
    if data.gear_reports:
        lines.append("## Gear Check")
        tier_by_player = {t.player_id: t for t in data.tier_set_reports}
        rows = []
        for r in data.gear_reports:
            tier = tier_by_player.get(r.player_id)
            if tier is None:
                tier_pieces_cell = "n/a"
                tier_set_cell = MISSING_TIER_PIECE_CHAR * TOTAL_TIER_SLOTS
            elif not tier.has_data:
                tier_pieces_cell = "n/a"
                tier_set_cell = MISSING_TIER_PIECE_CHAR * TOTAL_TIER_SLOTS
            else:
                tier_pieces_cell = f"{tier.pieces_worn}/{TOTAL_TIER_SLOTS}"
                tier_set_cell = tier.track_string
            if not r.has_data:
                rows.append([r.player_name, "no data", tier_pieces_cell, tier_set_cell, "no data", "no data"])
                continue
            rows.append([
                r.player_name, f"{r.average_item_level:.1f}", tier_pieces_cell, tier_set_cell,
                str(r.total_gems), ", ".join(r.missing_enchant_slots) or "none",
            ])
        lines.append(_md_table(["Player", "Avg iLvl", "Tier Pieces", "Tier Set HSCGL", "Gems", "Missing Enchants"], rows))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report(data: FightReportData, output_dir: str, base_filename: str) -> tuple[str, str]:
    text_path = os.path.join(output_dir, f"{base_filename}.txt")
    markdown_path = os.path.join(output_dir, f"{base_filename}.md")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(render_text(data))
    with open(markdown_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(data))
    return text_path, markdown_path


# =======================================================================
# SECTION 4 -- unified per-player raid scorecard
# (originally raid_scorecard.py)
#
# A single, unified per-player "scorecard" that synthesizes results
# from every other player-facing check already built into this
# project -- consumables, gear compliance, and avoidable boss-mechanic
# damage -- into one ranked table.
#
# DESIGN NOTE -- why "number of distinct problems", not a blended
# number: each source analyzer reports on a different SCALE. If summed
# directly, one spammy mechanic would dwarf "forgot to flask" even
# though both are really just "one thing to go fix." The scorecard's
# headline number -- problem_count -- counts DISTINCT PROBLEM AREAS per
# category (one missing consumable category = 1, one failed gear
# check = 1, one avoidable MECHANIC that hit them at all = 1,
# regardless of how many times), not raw magnitudes. Raw magnitudes are
# still fully preserved on each entry.
#
# DESIGN NOTE -- why defensive cooldown usage is informational, not
# scored: whether a player "should" have used a defensive cooldown
# depends on context this project doesn't model -- sitting on Divine
# Shield through a clean pull isn't a mistake. Defensive cooldown data
# is attached for visibility but deliberately EXCLUDED from
# problem_count.
#
# DESIGN NOTE -- missing gear data is NOT the same as a gear failure: a
# player with no CombatantInfo snapshot (has_data=False) means "we
# can't tell", not "they failed a check". Tracked as its own
# gear_data_missing flag, NOT counted in problem_count.
#
# Every input to build_raid_scorecard() is OPTIONAL (None) -- the
# scorecard composes gracefully from however many of the underlying
# analyzers you've actually run.
#
# Pure function over already-computed analyzer outputs -- no network
# calls, and this section does not call any analyzer itself.
# =======================================================================

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
    defensive_usages: list[DefensiveCooldownUsage] = field(default_factory=list)

    @property
    def problem_count(self) -> int:
        """The scorecard's headline number -- see Section 4's docstring for the design rationale."""
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
        lowest_name = quality_name(compliance.lowest_quality) if compliance.lowest_quality is not None else "n/a"
        required_name = (
            quality_name(requirements.min_quality)
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
    defensive_cooldown_usages: list[DefensiveCooldownUsage] | None = None,
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
        for player_id, actor in get_fight_roster(parsed_fight).items():
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


# =======================================================================
# SECTION 5 -- the full collapsible/filterable HTML report
# (originally html_report.py)
#
# Renders a whole raid night (or a single fight) into ONE
# self-contained HTML file: collapsible per-boss, per-pull, and
# per-section details, with client-side filters, a direct link back to
# the original Warcraft Logs report, class-colored meter bars for
# Damage Done/Healing/Damage Taken/Damage Prevented, a difficulty badge
# on every pull, and consistent right-aligned/comma-formatted numeric
# columns everywhere.
#
# Every per-pull section starts COLLAPSED by default (DEFAULT_OPEN_SECTIONS
# is empty) -- the mini-scoreboard and timeline strip are the only
# exceptions, since they were never collapsible <details> sections to
# begin with.
#
# Section order within a pull: Damage Done (DPS) -> Healing -> Deaths
# -> Avoidable Damage -> Damage Taken -> Biggest Hits -> Defensive
# Cooldown Usage -> Damage Prevented by Defensives -> Raid Cooldown
# Usage -> Gear Check -> Consumables.
#
# Every pull's summary line shows "Pulled by <Player>" via
# _find_puller_name(): the first PLAYER-SOURCED Casts or DamageDone
# event in the fight, chronologically. Pet-sourced actions fold back to
# the OWNING PLAYER via core.resolve_to_player(). Omitted entirely if
# no such event exists.
#
# Gear Check's "Lowest Quality" column is REMOVED and replaced with
# "Tier Pieces" (X/5) and "Tier Set" (5-char color-coded track string),
# joined against tier_set_reports by player_id -- see tier_sets.py for
# the letter legend/slot order.
#
# The per-boss TREND VIEW shows three inline-SVG bar charts: "Raid DPS
# by Pull", "Raid HPS by Pull" (restricted to tank/healer-role players,
# same filter the mini-scoreboard's "Top HPS" stat and the Healing
# section already apply), and "Deaths by Pull".
#
# "Defensive Cooldown Usage" uses a meter-bar OVERVIEW (ranked by total
# casts per player, average-efficiency pill) with per-player detail
# blocks underneath showing the full per-ability breakdown. Raid
# Cooldown Usage remains a flat per-ability table.
#
# A DIFFICULTY filter (LFR / Normal / Heroic / Mythic) sits alongside
# Role/Boss/Player, operating at the per-pull level (a boss can be
# pulled at more than one difficulty in one night).
#
# TIMELINE STRIP (per-pull three-lane Deaths/Raid CDs/Defensive CDs
# strip), STICKY MINI-SCOREBOARD, COLOR-CODED EFFICIENCY pills, M:SS
# timestamps throughout, and the meter-bar treatment (incl. hatched "no
# estimate possible" style) for Damage Prevented by Defensives.
#
# Pure rendering: takes a list of already-built FightReportData and
# produces HTML. Does not call any analyzer and does not touch the
# network. _find_puller_name() is the one lightweight exception -- a
# direct read over parsed_fight.events/.actors, not a separate analyzer
# call, matching how _role_of()/_class_of() already read parsed_fight
# directly elsewhere in this section.
# =======================================================================

_BG = "#14151a"

# Efficiency-pill thresholds, shared by Raid Cooldown Usage, Defensive
# Cooldown Usage (both the overview's Avg Efficiency and the per-player
# detail blocks' per-ability Efficiency).
EFFICIENCY_LOW_MAX = 40.0
EFFICIENCY_MID_MAX = 70.0

# Neutral fallback color for timeline markers/meter bars when a
# player's class can't be resolved (e.g. missing CombatantInfo).
_FALLBACK_MARKER_COLOR = "#8a8d9c"

# Color for a "_" (not-a-tracked-tier-piece) character in the Tier Set
# track string -- deliberately dim/gray rather than any track color,
# since it represents an absence, not a low-quality track.
_TIER_SET_MISSING_COLOR = "#565968"

# Which Casts/DamageDone events count as "starting" a pull, for the
# "Pulled by" note -- deliberately just these two data types (not
# Healing/Buffs/Resources/CombatantInfo/etc.), since those are the only
# two that represent an actual OFFENSIVE/engaging action initiating combat.
_PULL_STARTING_DATA_TYPES = ("Casts", "DamageDone")

# Canonical raid-difficulty ordering for the filter chips.
_DIFFICULTY_ORDER = ["LFR", "Normal", "Heroic", "Mythic"]


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


# Every header string (across every table built in this section) that
# represents a NUMERIC value and should therefore be right-aligned.
NUMERIC_HEADERS = {
    "Dmg (window)", "Heal (window)",
    "HPS", "DPS", "DTPS",
    "Effective", "Overheal", "Overheal %",
    "Total",
    "Amount",
    "Casts/Max", "Efficiency",
    "Prevented", "Dmg Taken (windows)", "Windows", "Dmg Taken",
    "# Missing",
    "Avg iLvl", "Gems", "Tier Pieces",
    "Total Hits",
    "Total Casts", "Avg Efficiency",
}

_CSS = f"""
:root {{
    --bg: {_BG}; --panel: #1c1e26; --panel-alt: #22242e; --border: #2f3140;
    --text: #e8e8ec; --text-dim: #9a9db0; --accent: #6c8cff;
    --kill: #3ddc84; --wipe: #ff6b6b; --warn: #ffb84d;
    --sticky-top: 70px;
}}
* {{ box-sizing: border-box; }}
body {{
    background: var(--bg); color: var(--text); font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    margin: 0; padding: 0 0 60px 0; font-size: 14px;
}}
h1 {{ font-size: 20px; margin: 0; }}
.header {{ padding: 16px 24px; border-bottom: 1px solid var(--border); }}
.header .report-link {{ margin-top: 8px; font-size: 13px; }}
.header .report-link a {{ color: var(--accent); text-decoration: none; }}
.header .report-link a:hover {{ text-decoration: underline; }}
.header .subtitle {{ color: var(--text-dim); font-size: 12.5px; margin-top: 6px; }}
.filter-bar {{
    position: sticky; top: 0; z-index: 10; background: var(--panel);
    border-bottom: 1px solid var(--border); padding: 12px 24px;
    display: flex; flex-wrap: wrap; gap: 22px; align-items: flex-start;
    box-shadow: 0 2px 8px rgba(0,0,0,0.35);
}}
.filter-group {{ display: flex; flex-direction: column; gap: 6px; }}
.filter-group .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-dim); }}
.filter-chips {{ display: flex; flex-wrap: wrap; gap: 6px; max-width: 480px; }}
.chip {{ display: inline-flex; align-items: center; gap: 5px; background: var(--panel-alt);
    border: 1px solid var(--border); border-radius: 14px; padding: 3px 10px; cursor: pointer; font-size: 12.5px; }}
.chip input {{ margin: 0; accent-color: var(--accent); }}
#player-search {{
    background: var(--panel-alt); border: 1px solid var(--border); color: var(--text);
    padding: 6px 10px; border-radius: 6px; font-size: 13px; width: 200px;
}}
#reset-filters {{
    background: transparent; border: 1px solid var(--border); color: var(--text-dim);
    border-radius: 6px; padding: 6px 12px; cursor: pointer; font-size: 12.5px; align-self: flex-end;
}}
#reset-filters:hover {{ color: var(--text); border-color: var(--accent); }}
.content {{ padding: 20px 24px; max-width: 1100px; margin: 0 auto; }}
details.boss-section {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    margin-bottom: 14px; overflow: hidden;
}}
details.boss-section > summary {{
    padding: 12px 16px; cursor: pointer; font-size: 16px; font-weight: 600;
    list-style: none; display: flex; align-items: center; gap: 10px;
}}
details.boss-section > summary::-webkit-details-marker {{ display: none; }}
details.boss-section > summary .pull-count {{ color: var(--text-dim); font-weight: 400; font-size: 12.5px; }}
details.pull-section {{ background: var(--panel-alt); border-top: 1px solid var(--border); }}
details.pull-section > summary {{
    padding: 10px 16px 10px 28px; cursor: pointer; list-style: none;
    display: flex; align-items: center; gap: 10px; font-size: 13.5px;
}}
details.pull-section > summary::-webkit-details-marker {{ display: none; }}
.badge {{ border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700; letter-spacing: .03em; }}
.badge.kill {{ background: rgba(61,220,132,0.18); color: var(--kill); }}
.badge.wipe {{ background: rgba(255,107,107,0.18); color: var(--wipe); }}
.pulled-by {{
    color: var(--text-dim); font-weight: 400; font-size: 12px; font-style: italic;
}}
.pull-body {{ padding: 6px 16px 16px 28px; }}
details.section-details {{ margin: 0 0 10px 0; border: none; background: transparent; }}
details.section-details > summary {{
    cursor: pointer; font-size: 12.5px; text-transform: uppercase; letter-spacing: .04em;
    color: var(--text-dim); list-style: none; padding: 4px 0; user-select: none;
}}
details.section-details > summary::-webkit-details-marker {{ display: none; }}
details.section-details > summary::before {{ content: "\\25B8  "; }}
details.section-details[open] > summary::before {{ content: "\\25BE  "; }}
details.section-details > summary:hover {{ color: var(--text); }}
.section-body {{ padding: 2px 0 4px 4px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ text-align: left; padding: 5px 10px; border-bottom: 1px solid var(--border); }}
th {{ color: var(--text-dim); font-weight: 600; font-size: 11.5px; text-transform: uppercase; }}
th.num, td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
tr.hidden-row {{ display: none; }}
.empty-note {{ color: var(--text-dim); font-style: italic; font-size: 12.5px; }}
td.meter-cell {{ position: relative; padding: 0; }}
.meter-bar {{
    position: absolute; top: 3px; bottom: 3px; left: 0;
    border-radius: 3px; opacity: 0.75; z-index: 0;
    transition: width 0.2s ease;
}}
.meter-bar.no-estimate {{
    width: 28px !important;
    min-width: 28px;
    opacity: 0.35;
    background-image: repeating-linear-gradient(45deg, rgba(255,255,255,0.35) 0 4px, transparent 4px 8px);
}}
.meter-name {{
    position: relative; z-index: 1; display: block;
    padding: 5px 10px; font-weight: 700; color: {_BG};
    white-space: nowrap;
    text-shadow: 0 0 4px rgba(255,255,255,0.65), 0 0 2px rgba(255,255,255,0.65);
}}
.defensive-player-block {{ margin: 0 0 14px 0; }}
.defensive-player-block:last-child {{ margin-bottom: 0; }}
.defensive-player-heading {{
    margin: 10px 0 4px 0; font-weight: 600; font-size: 13px;
}}
.defensive-window-note {{
    font-size: 11.5px; color: var(--text-dim); margin: 0 0 6px 0;
}}
/* ---- Efficiency pills ---- */
.eff-pill {{
    display: inline-block; padding: 2px 9px; border-radius: 10px;
    font-weight: 700; font-variant-numeric: tabular-nums; font-size: 12px;
}}
.eff-low  {{ background: rgba(255,107,107,0.18); color: var(--wipe); }}
.eff-mid  {{ background: rgba(255,184,77,0.18);  color: var(--warn); }}
.eff-high {{ background: rgba(61,220,132,0.18);  color: var(--kill); }}
/* ---- Tier Set track string ---- */
.tier-set-string {{
    font-family: ui-monospace, "SF Mono", Consolas, monospace;
    font-weight: 700; font-size: 13.5px; letter-spacing: 0.06em;
}}
/* ---- Sticky mini-scoreboard ---- */
.mini-scoreboard {{
    position: sticky; top: var(--sticky-top); z-index: 5;
    display: flex; flex-wrap: wrap; gap: 20px; align-items: center;
    background: var(--panel-alt); border: 1px solid var(--border); border-radius: 6px;
    padding: 8px 16px; margin: 0 0 14px 0;
    box-shadow: 0 2px 6px rgba(0,0,0,0.3);
}}
.mini-scoreboard .stat {{ display: flex; flex-direction: column; gap: 1px; min-width: 64px; }}
.mini-scoreboard .stat-label {{
    font-size: 10px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-dim);
}}
.mini-scoreboard .stat-value {{ font-size: 13.5px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.mini-scoreboard .stat-value.kill {{ color: var(--kill); }}
.mini-scoreboard .stat-value.wipe {{ color: var(--wipe); }}
.mini-scoreboard .stat-sub {{ font-size: 10.5px; color: var(--text-dim); }}
/* ---- Timeline strip ---- */
.timeline-strip {{ margin: 0 0 16px 0; }}
.timeline-legend {{
    display: flex; gap: 16px; font-size: 10.5px; color: var(--text-dim); margin-bottom: 5px;
}}
.timeline-legend .legend-item {{ display: flex; align-items: center; gap: 4px; }}
.timeline-legend .legend-swatch {{ display: inline-block; width: 9px; height: 9px; }}
.timeline-legend .legend-swatch.death {{ background: var(--wipe); width: 2px; height: 11px; }}
.timeline-legend .legend-swatch.raidcd {{ background: var(--accent); border-radius: 50%; }}
.timeline-legend .legend-swatch.defcd {{ background: var(--accent); border-radius: 2px; }}
.tl-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
.tl-row:last-child {{ margin-bottom: 0; }}
.tl-label {{
    flex: 0 0 74px; width: 74px; text-align: right; font-size: 10.5px; color: var(--text-dim);
}}
.tl-track {{
    position: relative; flex: 1 1 auto; height: 18px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 3px;
}}
.tl-marker {{
    position: absolute; top: 50%; transform: translate(-50%, -50%);
    cursor: default; border: 1px solid rgba(0,0,0,0.4);
}}
.tl-marker.death {{
    width: 2px; height: 18px; top: 0; transform: translate(-50%, 0);
    background: var(--wipe); border: none;
}}
.tl-marker.raidcd {{ width: 9px; height: 9px; border-radius: 50%; }}
.tl-marker.defcd {{ width: 8px; height: 8px; border-radius: 2px; }}
/* ---- Trend view across pulls ---- */
.trend-view {{
    display: flex; flex-wrap: wrap; gap: 28px; padding: 4px 16px 14px 16px;
}}
.trend-chart-block {{ display: flex; flex-direction: column; gap: 4px; }}
.trend-chart-title {{
    font-size: 10.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-dim);
}}
.trend-chart-note {{ font-size: 11.5px; color: var(--text-dim); font-style: italic; padding: 4px 16px; }}
"""
_JS = """
function applyFilters() {
    var allRoleBoxes = Array.prototype.slice.call(document.querySelectorAll('.role-chip input'));
    var checkedRoles = allRoleBoxes.filter(function(el) { return el.checked; }).map(function(el) { return el.value; });
    var allRolesChecked = allRoleBoxes.length === 0 || checkedRoles.length === allRoleBoxes.length;
    var checkedBosses = Array.prototype.map.call(document.querySelectorAll('.boss-chip input:checked'), function(el) { return el.value; });
    var allDifficultyBoxes = Array.prototype.slice.call(document.querySelectorAll('.difficulty-chip input'));
    var checkedDifficulties = allDifficultyBoxes.filter(function(el) { return el.checked; }).map(function(el) { return el.value; });
    var allDifficultiesChecked = allDifficultyBoxes.length === 0 || checkedDifficulties.length === allDifficultyBoxes.length;
    var query = document.getElementById('player-search').value.trim().toLowerCase();
    document.querySelectorAll('.boss-section').forEach(function(section) {
        var bossMatches = checkedBosses.indexOf(section.dataset.boss) !== -1;
        section.style.display = bossMatches ? '' : 'none';
    });
    document.querySelectorAll('.boss-section').forEach(function(bossSection) {
        if (bossSection.style.display === 'none') { return; }
        var anyPullVisible = false;
        bossSection.querySelectorAll('.pull-section').forEach(function(pull) {
            var difficultyOk = allDifficultiesChecked || checkedDifficulties.indexOf(pull.dataset.difficulty) !== -1;
            pull.style.display = difficultyOk ? '' : 'none';
            if (difficultyOk) { anyPullVisible = true; }
        });
        if (!anyPullVisible) { bossSection.style.display = 'none'; }
    });
    document.querySelectorAll('tr[data-player]').forEach(function(row) {
        var role = row.dataset.role || '';
        var player = (row.dataset.player || '').toLowerCase();
        var roleOk = allRolesChecked || (role !== '' && checkedRoles.indexOf(role) !== -1);
        var playerOk = !query || player.indexOf(query) !== -1;
        row.classList.toggle('hidden-row', !(roleOk && playerOk));
    });
}
function resetFilters() {
    document.querySelectorAll('.role-chip input, .boss-chip input, .difficulty-chip input').forEach(function(el) { el.checked = true; });
    document.getElementById('player-search').value = '';
    applyFilters();
}
function updateStickyOffset() {
    var bar = document.querySelector('.filter-bar');
    if (bar) {
        document.documentElement.style.setProperty('--sticky-top', bar.offsetHeight + 'px');
    }
}
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.role-chip input, .boss-chip input, .difficulty-chip input').forEach(function(el) {
        el.addEventListener('change', applyFilters);
    });
    document.getElementById('player-search').addEventListener('input', applyFilters);
    document.getElementById('reset-filters').addEventListener('click', resetFilters);
    applyFilters();
    updateStickyOffset();
    window.addEventListener('resize', updateStickyOffset);
});
"""
ROLE_LABELS = {"tank": "Tank", "healer": "Healer", "melee": "Melee", "ranged": "Ranged"}

# ALL per-pull sections start collapsed, with zero exceptions. The
# mini-scoreboard/timeline strip are unaffected since they were never
# <details> sections to begin with.
DEFAULT_OPEN_SECTIONS: set[str] = set()


def _esc(value) -> str:
    return html_module.escape(str(value if value is not None else ""))


def _numeric_indices(headers: list[str]) -> frozenset[int]:
    return frozenset(i for i, h in enumerate(headers) if h in NUMERIC_HEADERS)


def _difficulty_badge_html(difficulty: int | None) -> str:
    name = difficulty_name(difficulty)
    color = difficulty_color(difficulty)
    r, g, b = _hex_to_rgb(color)
    return (
        f'<span class="badge" style="background-color:rgba({r},{g},{b},0.18);color:{color};">'
        f"{_esc(name.upper())}</span>"
    )


def _find_puller_name(parsed_fight) -> str | None:
    """
    Find who "pulled" the encounter: the first PLAYER-sourced Casts or
    DamageDone event in the fight, chronologically (parsed_fight.events
    is already sorted by timestamp upstream in core.py's log-parser
    section). A pet/guardian-sourced action resolves back to the OWNING
    PLAYER via core.resolve_to_player(). Returns None if no such event
    exists at all -- the caller omits the "Pulled by" note entirely in
    that case.
    """
    for event in parsed_fight.events:
        if event.data_type not in _PULL_STARTING_DATA_TYPES:
            continue
        player = resolve_to_player(event.source_id, parsed_fight.actors)
        if player is not None:
            return player.name
    return None


def _tier_set_string_html(track_string: str) -> str:
    """
    Render a 5-char tier-set track string (e.g. "MM_H_") as individually
    color-coded characters -- each letter gets its track's color, and
    each MISSING_TIER_PIECE_CHAR ("_") renders dim gray instead of any
    track color, so a missing slot never gets visually confused with a
    real (if low) track.
    """
    spans = []
    for ch in track_string:
        color = _TIER_SET_MISSING_COLOR if ch == MISSING_TIER_PIECE_CHAR else track_color_for_letter(ch)
        spans.append(f'<span style="color:{color};">{_esc(ch)}</span>')
    return f'<span class="tier-set-string">{"".join(spans)}</span>'


def _role_of_html(player_id, data: FightReportData) -> str:
    if player_id is None:
        return ""
    role_info = data.player_roles.get(player_id)
    return role_info.role if role_info else ""


def _class_of(player_id, data: FightReportData) -> str | None:
    if player_id is None:
        return None
    actor = data.parsed_fight.actors.get(player_id)
    return actor.subtype if actor is not None else None


def _marker_color(player_id, data: FightReportData) -> str:
    class_name = _class_of(player_id, data)
    if class_name is None:
        return _FALLBACK_MARKER_COLOR
    color = get_class_color(class_name)
    return color or _FALLBACK_MARKER_COLOR


def _efficiency_pill(efficiency_fraction: float) -> str:
    """
    efficiency_fraction is 0.0-1.0 (as returned by CooldownUsage.efficiency(),
    or a simple mean of several such values for the Defensive Cooldown
    Usage OVERVIEW's "Avg Efficiency" column).

    Buckets: < EFFICIENCY_LOW_MAX -> red, < EFFICIENCY_MID_MAX -> amber,
    else green.
    """
    pct = efficiency_fraction * 100.0
    if pct < EFFICIENCY_LOW_MAX:
        bucket = "eff-low"
    elif pct < EFFICIENCY_MID_MAX:
        bucket = "eff-mid"
    else:
        bucket = "eff-high"
    return f'<span class="eff-pill {bucket}">{pct:.1f}%</span>'


def _row(
    player_id, player_name, data: FightReportData, cells: list,
    numeric_indices: frozenset[int] = frozenset(), raw_indices: frozenset[int] = frozenset(),
) -> str:
    """
    raw_indices: cell indices whose content is ALREADY-BUILT, TRUSTED
    HTML (e.g. an efficiency pill span, or a colored tier-set string)
    that must NOT be passed through _esc() a second time -- every such
    cell is built exclusively by this section itself (never from raw
    log/user data), so this is safe.
    """
    role = _role_of_html(player_id, data)
    attrs = f'data-player="{_esc(player_name)}"'
    if role:
        attrs += f' data-role="{role}"'
    cell_html = "".join(
        (f'<td class="num">{c}</td>' if i in numeric_indices else f"<td>{c}</td>")
        if i in raw_indices else
        (f'<td class="num">{_esc(c)}</td>' if i in numeric_indices else f"<td>{_esc(c)}</td>")
        for i, c in enumerate(cells)
    )
    return f"<tr {attrs}>{cell_html}</tr>"


def _meter_row(
    player_id, player_name, data: FightReportData, value: float, max_value: float,
    other_cells: list, numeric_indices: frozenset[int] = frozenset(),
    no_estimate: bool = False, raw_indices: frozenset[int] = frozenset(),
) -> str:
    """
    raw_indices: same meaning as in _row() -- cell indices (within
    other_cells) whose content is already-built trusted HTML that must
    not be re-escaped.
    """
    role = _role_of_html(player_id, data)
    class_name = _class_of(player_id, data)
    bar_color = get_class_color(class_name)
    width_percent = 0.0
    if not no_estimate and max_value and max_value > 0:
        width_percent = max(0.0, min(100.0, (value / max_value) * 100.0))
    attrs = f'data-player="{_esc(player_name)}"'
    if role:
        attrs += f' data-role="{role}"'
    bar_class = "meter-bar no-estimate" if no_estimate else "meter-bar"
    name_cell = (
        f'<td class="meter-cell">'
        f'<div class="{bar_class}" style="width:{width_percent:.1f}%;background-color:{bar_color};"></div>'
        f'<span class="meter-name">{_esc(player_name)}</span>'
        f"</td>"
    )
    other_cells_html = "".join(
        (f'<td class="num">{c}</td>' if i in numeric_indices else f"<td>{c}</td>")
        if i in raw_indices else
        (f'<td class="num">{_esc(c)}</td>' if i in numeric_indices else f"<td>{_esc(c)}</td>")
        for i, c in enumerate(other_cells)
    )
    return f"<tr {attrs}>{name_cell}{other_cells_html}</tr>"


def _table(headers: list[str], rows: list[str], empty_message: str) -> str:
    if not rows:
        return f'<p class="empty-note">{_esc(empty_message)}</p>'
    head = "".join(
        f'<th class="num">{_esc(h)}</th>' if h in NUMERIC_HEADERS else f"<th>{_esc(h)}</th>"
        for h in headers
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _section(title: str, body_html: str) -> str:
    open_attr = " open" if title in DEFAULT_OPEN_SECTIONS else ""
    return (
        f'<details class="section-details"{open_attr}>'
        f'<summary>{_esc(title)}</summary>'
        f'<div class="section-body">{body_html}</div>'
        f"</details>"
    )


def _cooldown_usage_rows(usages, data: FightReportData, duration_ms: int) -> list[str]:
    """Flat, one-row-per-(player,ability) table -- still used as-is for Raid Cooldown Usage."""
    headers = ["Player", "Ability", "Casts/Max", "Efficiency"]
    numeric_indices = _numeric_indices(headers)
    return [
        _row(u.player_id, u.player_name, data, [
            u.player_name, u.ability_name,
            f"{u.num_casts}/{u.theoretical_max_casts(duration_ms)}",
            _efficiency_pill(u.efficiency(duration_ms)),
        ], numeric_indices, raw_indices=frozenset({3}))
        for u in usages
    ]


# ---------------------------------------------------------------------
# Defensive Cooldown Usage: per-player aggregation + detail blocks
# ---------------------------------------------------------------------

def _aggregate_cooldown_usage_by_player(usages, duration_ms: int) -> list[tuple]:
    order: list[int] = []
    agg: dict[int, dict] = {}
    for u in usages:
        key = u.player_id
        if key not in agg:
            agg[key] = {"player_name": u.player_name, "total_casts": 0, "efficiencies": []}
            order.append(key)
        agg[key]["total_casts"] += u.num_casts
        agg[key]["efficiencies"].append(u.efficiency(duration_ms))
    results = []
    for key in order:
        v = agg[key]
        avg_efficiency = sum(v["efficiencies"]) / len(v["efficiencies"]) if v["efficiencies"] else 0.0
        results.append((key, v["player_name"], v["total_casts"], avg_efficiency))
    results.sort(key=lambda r: r[2], reverse=True)
    return results


def _cooldown_usage_detail_blocks(usages, data: FightReportData, duration_ms: int) -> list[str]:
    order: list[int] = []
    by_player: dict[int, list] = {}
    for u in usages:
        if u.player_id not in by_player:
            by_player[u.player_id] = []
            order.append(u.player_id)
        by_player[u.player_id].append(u)
    headers = ["Ability", "Casts/Max", "Efficiency"]
    numeric_indices = _numeric_indices(headers)
    blocks = []
    for player_id in order:
        player_usages = by_player[player_id]
        player_name = player_usages[0].player_name
        rows = [
            _row(u.player_id, u.player_name, data, [
                u.ability_name, f"{u.num_casts}/{u.theoretical_max_casts(duration_ms)}",
                _efficiency_pill(u.efficiency(duration_ms)),
            ], numeric_indices, raw_indices=frozenset({2}))
            for u in player_usages
        ]
        heading = f'<p class="defensive-player-heading">{_esc(player_name)}</p>'
        blocks.append(f'<div class="defensive-player-block">{heading}' + _table(headers, rows, "") + "</div>")
    return blocks


# ---------------------------------------------------------------------
# Sticky mini-scoreboard
# ---------------------------------------------------------------------

def _mini_scoreboard_html(data: FightReportData) -> str:
    fight = data.parsed_fight.fight
    duration_ms = fight.duration_ms
    status_class = "kill" if fight.kill else "wipe"
    status_label = "KILL" if fight.kill else "WIPE"
    top_dps_html = '<span class="stat-sub">n/a</span>'
    if data.damage_done_summaries:
        top = data.damage_done_summaries[0]  # already sorted descending
        top_dps_html = (
            f'<span class="stat-value">{top.dps(duration_ms):,.0f}</span>'
            f'<span class="stat-sub">{_esc(top.player_name or "Unknown")}</span>'
        )
    top_hps_html = '<span class="stat-sub">n/a</span>'
    has_role_data = bool(data.player_roles)
    relevant_healers = [
        s for s in data.healer_summaries
        if not has_role_data or _role_of_html(s.healer_id, data) in ("tank", "healer")
    ]
    if relevant_healers:
        top = max(relevant_healers, key=lambda s: s.total_effective_healing)
        top_hps_html = (
            f'<span class="stat-value">{top.hps(duration_ms):,.0f}</span>'
            f'<span class="stat-sub">{_esc(top.healer_name or "Unknown")}</span>'
        )
    death_count = len(data.death_reports)
    death_class = "wipe" if death_count > 0 else "kill"
    return f"""
    <div class="mini-scoreboard">
        <div class="stat">
            <span class="stat-label">Result</span>
            <span class="stat-value {status_class}">{status_label}</span>
        </div>
        <div class="stat">
            <span class="stat-label">Duration</span>
            <span class="stat-value">{format_timestamp(duration_ms)}</span>
        </div>
        <div class="stat">
            <span class="stat-label">Top DPS</span>
            {top_dps_html}
        </div>
        <div class="stat">
            <span class="stat-label">Top HPS</span>
            {top_hps_html}
        </div>
        <div class="stat">
            <span class="stat-label">Deaths</span>
            <span class="stat-value {death_class}">{death_count}</span>
        </div>
    </div>
    """


# ---------------------------------------------------------------------
# Timeline strip
# ---------------------------------------------------------------------

def _position_percent(ms: int, duration_ms: int) -> float:
    if duration_ms <= 0:
        return 0.0
    return max(0.0, min(100.0, (ms / duration_ms) * 100.0))


def _timeline_strip_html(data: FightReportData) -> str:
    fight = data.parsed_fight.fight
    duration_ms = fight.duration_ms
    start_time = fight.start_time
    if duration_ms <= 0:
        return ""
    death_markers = []
    for d in data.death_reports:
        pct = _position_percent(d.time_into_fight_ms, duration_ms)
        title = f"{format_timestamp(d.time_into_fight_ms)} \u2014 {_esc(d.victim_name or 'Unknown')} died to {_esc(d.killing_ability_name or 'Unknown')}"
        death_markers.append(f'<div class="tl-marker death" style="left:{pct:.2f}%;" title="{title}"></div>')

    def _cooldown_markers(usages, marker_class: str) -> list[str]:
        markers = []
        for u in usages:
            color = _marker_color(u.player_id, data)
            for raw_ts in u.cast_timestamps:
                relative_ms = fight_relative_ms(raw_ts, start_time, duration_ms)
                pct = _position_percent(relative_ms, duration_ms)
                title = f"{format_timestamp(relative_ms)} \u2014 {_esc(u.player_name or 'Unknown')} \u2014 {_esc(u.ability_name)}"
                markers.append(
                    f'<div class="tl-marker {marker_class}" '
                    f'style="left:{pct:.2f}%;background-color:{color};" title="{title}"></div>'
                )
        return markers

    raid_cd_markers = _cooldown_markers(data.cooldown_usages, "raidcd")
    defensive_cd_markers = _cooldown_markers(data.defensive_cooldown_usages, "defcd")
    if not (death_markers or raid_cd_markers or defensive_cd_markers):
        return ""
    return f"""
    <div class="timeline-strip">
        <div class="timeline-legend">
            <span class="legend-item"><span class="legend-swatch death"></span>Death</span>
            <span class="legend-item"><span class="legend-swatch raidcd"></span>Raid CD</span>
            <span class="legend-item"><span class="legend-swatch defcd"></span>Defensive CD</span>
        </div>
        <div class="tl-row">
            <span class="tl-label">Deaths</span>
            <div class="tl-track">{''.join(death_markers)}</div>
        </div>
        <div class="tl-row">
            <span class="tl-label">Raid CDs</span>
            <div class="tl-track">{''.join(raid_cd_markers)}</div>
        </div>
        <div class="tl-row">
            <span class="tl-label">Defensive CDs</span>
            <div class="tl-track">{''.join(defensive_cd_markers)}</div>
        </div>
    </div>
    """


# ---------------------------------------------------------------------
# Trend view across pulls (per boss, 2+ pulls only)
# ---------------------------------------------------------------------

def _bar_chart_svg(
    values: list[float], is_kill: list[bool], value_labels: list[str], pull_labels: list[str],
    bar_width: int = 22, gap: int = 8, height: int = 52,
) -> str:
    n = len(values)
    if n == 0:
        return ""
    max_value = max(values) if max(values) > 0 else 1.0
    width = n * bar_width + (n - 1) * gap + 4
    bars = []
    for i, (value, kill, value_label, pull_label) in enumerate(zip(values, is_kill, value_labels, pull_labels)):
        bar_h = max(2.0, (value / max_value) * (height - 14))
        x = i * (bar_width + gap) + 2
        y = height - bar_h - 12
        color = "var(--kill)" if kill else "var(--wipe)"
        title = f"Pull {pull_label}: {value_label}"
        bars.append(
            f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" height="{bar_h:.1f}" rx="2" fill="{color}" opacity="0.85">'
            f'<title>{_esc(title)}</title></rect>'
            f'<text x="{x + bar_width / 2}" y="{height - 2}" font-size="9" fill="var(--text-dim)" '
            f'text-anchor="middle">{_esc(pull_label)}</text>'
        )
    return f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">{"".join(bars)}</svg>'


def _trend_view_html(pulls: list[FightReportData]) -> str:
    if len(pulls) < 2:
        return ""
    dps_values, dps_labels = [], []
    hps_values, hps_labels = [], []
    death_values, death_labels = [], []
    is_kill, pull_labels = [], []
    for i, data in enumerate(pulls, start=1):
        fight = data.parsed_fight.fight
        duration_s = fight.duration_ms / 1000 if fight.duration_ms > 0 else 0
        raid_dps = (
            sum(s.total_damage_done for s in data.damage_done_summaries) / duration_s
            if duration_s > 0 else 0.0
        )
        dps_values.append(raid_dps)
        dps_labels.append(f"{raid_dps:,.0f} raid DPS")
        has_role_data = bool(data.player_roles)
        relevant_healers = [
            s for s in data.healer_summaries
            if not has_role_data or _role_of_html(s.healer_id, data) in ("tank", "healer")
        ]
        raid_hps = (
            sum(s.total_effective_healing for s in relevant_healers) / duration_s
            if duration_s > 0 else 0.0
        )
        hps_values.append(raid_hps)
        hps_labels.append(f"{raid_hps:,.0f} raid HPS")
        death_count = len(data.death_reports)
        death_values.append(float(death_count))
        death_labels.append(f"{death_count} death(s)")
        is_kill.append(fight.kill)
        pull_labels.append(str(i))
    dps_chart = _bar_chart_svg(dps_values, is_kill, dps_labels, pull_labels)
    hps_chart = _bar_chart_svg(hps_values, is_kill, hps_labels, pull_labels)
    death_chart = _bar_chart_svg(death_values, is_kill, death_labels, pull_labels)
    return f"""
    <div class="trend-view">
        <div class="trend-chart-block">
            <div class="trend-chart-title">Raid DPS by Pull</div>
            {dps_chart}
        </div>
        <div class="trend-chart-block">
            <div class="trend-chart-title">Raid HPS by Pull</div>
            {hps_chart}
        </div>
        <div class="trend-chart-block">
            <div class="trend-chart-title">Deaths by Pull</div>
            {death_chart}
        </div>
    </div>
    """


def _render_pull_sections(data: FightReportData) -> str:
    """
    Section order: Damage Done (DPS) -> Healing -> Deaths -> Avoidable
    Damage -> Damage Taken -> Biggest Hits -> Defensive Cooldown Usage
    -> Damage Prevented by Defensives -> Raid Cooldown Usage -> Gear
    Check -> Consumables.
    """
    duration_ms = data.parsed_fight.fight.duration_ms
    blocks: list[str] = []
    blocks.append(_mini_scoreboard_html(data))
    blocks.append(_timeline_strip_html(data))

    # 1. Damage Done (DPS) -- METER bars.
    if data.damage_done_summaries:
        headers = ["Player", "DPS", "Total"]
        numeric_indices = _numeric_indices(headers[1:])
        max_value = data.damage_done_summaries[0].total_damage_done
        rows = [
            _meter_row(s.player_id, s.player_name, data, s.total_damage_done, max_value, [
                f"{s.dps(duration_ms):,.0f}", f"{s.total_damage_done:,}",
            ], numeric_indices)
            for s in data.damage_done_summaries
        ]
        blocks.append(_section("Damage Done (DPS)", _table(headers, rows, "No damage-done data.")))

    # 2. Healing -- restricted to tank/healer rows whenever role data
    # is available; METER bars, scaled to the top ROW WITHIN THIS
    # (already tank/healer-filtered) LIST.
    if data.healer_summaries:
        headers = ["Healer", "HPS", "Effective", "Overheal", "Overheal %"]
        numeric_indices = _numeric_indices(headers[1:])
        has_role_data = bool(data.player_roles)
        relevant = [
            s for s in data.healer_summaries
            if not has_role_data or _role_of_html(s.healer_id, data) in ("tank", "healer")
        ]
        max_value = relevant[0].total_effective_healing if relevant else 0
        rows = [
            _meter_row(s.healer_id, s.healer_name, data, s.total_effective_healing, max_value, [
                f"{s.hps(duration_ms):,.0f}", f"{s.total_effective_healing:,}",
                f"{s.total_overheal:,}", f"{s.overheal_percent:.1f}%",
            ], numeric_indices)
            for s in relevant
        ]
        empty_message = "No healing recorded from healers/tanks." if has_role_data else "No healing recorded."
        blocks.append(_section("Healing", _table(headers, rows, empty_message)))

    # 3. Deaths.
    if data.death_reports:
        headers = ["Time", "Victim", "Killed By", "Dmg (window)", "Heal (window)"]
        numeric_indices = _numeric_indices(headers)
        rows = [
            _row(r.victim_id, r.victim_name, data, [
                format_timestamp(r.time_into_fight_ms), r.victim_name, r.killing_ability_name,
                f"{r.total_damage_taken_in_window:,}", f"{r.total_effective_healing_in_window:,}",
            ], numeric_indices)
            for r in data.death_reports
        ]
        blocks.append(_section("Deaths", _table(headers, rows, "No deaths.")))

    # 4. Avoidable Damage.
    if data.avoidable_config is not None:
        headers = ["Player", "Total Hits", "Breakdown"]
        numeric_indices = _numeric_indices(headers)
        hit_reports = [r for r in data.avoidable_reports if r.total_avoidable_hits > 0]
        rows = [
            _row(r.player_id, r.player_name, data, [
                r.player_name, str(r.total_avoidable_hits),
                ", ".join(f"{n} x{c}" for n, c in r.hits_by_mechanic.items() if c > 0),
            ], numeric_indices)
            for r in hit_reports
        ]
        blocks.append(_section("Avoidable Damage", _table(headers, rows, "No avoidable hits taken. Clean pull!")))

    # 5. Damage Taken -- METER bars.
    if data.damage_taken_summaries:
        headers = ["Player", "DTPS", "Total"]
        numeric_indices = _numeric_indices(headers[1:])
        max_value = data.damage_taken_summaries[0].total_damage_taken
        rows = [
            _meter_row(s.target_id, s.target_name, data, s.total_damage_taken, max_value, [
                f"{s.dtps(duration_ms):,.0f}", f"{s.total_damage_taken:,}",
            ], numeric_indices)
            for s in data.damage_taken_summaries
        ]
        blocks.append(_section("Damage Taken", _table(headers, rows, "No damage-taken data.")))

    # 6. Biggest Hits.
    if data.biggest_hits:
        headers = ["Amount", "Ability", "Target"]
        numeric_indices = _numeric_indices(headers)
        rows = [
            _row(hit.target_id, hit.target_name, data, [
                f"{hit.amount:,}", hit.ability_name, hit.target_name,
            ], numeric_indices)
            for hit in data.biggest_hits
        ]
        blocks.append(_section("Biggest Hits", _table(headers, rows, "No hits recorded.")))

    # 7. Defensive Cooldown Usage -- OVERVIEW uses meter bars, ranked by
    # total casts summed per player. A per-player detail block
    # underneath still shows the full per-ability breakdown.
    if data.defensive_cooldown_usages:
        overview_headers = ["Player", "Total Casts", "Avg Efficiency"]
        overview_numeric_indices = _numeric_indices(overview_headers[1:])
        aggregated = _aggregate_cooldown_usage_by_player(data.defensive_cooldown_usages, duration_ms)
        max_casts = aggregated[0][2] if aggregated else 0
        overview_rows = [
            _meter_row(
                player_id, player_name, data, total_casts, max_casts,
                [str(total_casts), _efficiency_pill(avg_efficiency)],
                overview_numeric_indices, raw_indices=frozenset({1}),
            )
            for player_id, player_name, total_casts, avg_efficiency in aggregated
        ]
        overview_table = _table(overview_headers, overview_rows, "No tracked defensive cooldowns used.")
        detail_blocks = _cooldown_usage_detail_blocks(data.defensive_cooldown_usages, data, duration_ms)
        blocks.append(_section("Defensive Cooldown Usage", overview_table + "".join(detail_blocks)))

    # 8. Damage Prevented by Defensives.
    if data.defensive_damage_prevention:
        overview_headers = ["Player", "Prevented", "Dmg Taken (windows)", "Windows"]
        overview_numeric_indices = _numeric_indices(overview_headers[1:])
        max_prevented = data.defensive_damage_prevention[0].total_damage_prevented
        overview_rows = []
        for e in data.defensive_damage_prevention:
            has_any_estimate = bool(e.windows_with_estimate)
            prevented_cell = f"{e.total_damage_prevented:,}" if has_any_estimate else "n/a"
            overview_rows.append(_meter_row(
                e.player_id, e.player_name, data, e.total_damage_prevented, max_prevented,
                [
                    prevented_cell,
                    f"{e.total_actual_damage_taken_during_windows:,}",
                    str(len(e.windows)),
                ],
                overview_numeric_indices,
                no_estimate=not has_any_estimate,
            ))
        overview_table = _table(overview_headers, overview_rows, "No damage-prevention data available.")
        detail_headers = ["Time", "Ability", "Mitigation", "Dmg Taken", "Prevented"]
        detail_numeric_indices = _numeric_indices(detail_headers)
        detail_blocks = []
        for entry in data.defensive_damage_prevention:
            if not entry.windows:
                continue
            window_rows = []
            for w in entry.windows:
                if w.mitigation_type == "immunity":
                    window_rows.append(_row(entry.player_id, entry.player_name, data, [
                        format_timestamp(w.cast_timestamp), w.ability_name, "immunity",
                        f"{w.actual_damage_taken:,} (residual)", "n/a",
                    ], detail_numeric_indices))
                else:
                    window_rows.append(_row(entry.player_id, entry.player_name, data, [
                        format_timestamp(w.cast_timestamp), w.ability_name,
                        f"{w.damage_reduction_percent:.0f}% / {w.window_duration_seconds:.0f}s",
                        f"{w.actual_damage_taken:,}", f"{w.damage_prevented:,}",
                    ], detail_numeric_indices))
            heading = f'<p class="defensive-player-heading">{_esc(entry.player_name)}</p>'
            note = ""
            if not entry.windows_with_estimate:
                note = (
                    '<p class="defensive-window-note">Only immunity-type defensives used -- '
                    "no damage-prevented estimate is possible for these (see Damage Prevented "
                    "column above showing a hatched bar, not a measured zero).</p>"
                )
            detail_blocks.append(
                f'<div class="defensive-player-block">{heading}{note}'
                + _table(detail_headers, window_rows, "")
                + "</div>"
            )
        blocks.append(_section("Damage Prevented by Defensives", overview_table + "".join(detail_blocks)))

    # 9. Raid Cooldown Usage -- still a single flat per-ability table.
    if data.cooldown_usages:
        headers = ["Player", "Ability", "Casts/Max", "Efficiency"]
        blocks.append(_section("Raid Cooldown Usage", _table(
            headers, _cooldown_usage_rows(data.cooldown_usages, data, duration_ms),
            "No tracked raid cooldowns used.",
        )))

    # 10. Gear Check -- "Lowest Quality" REMOVED; replaced with "Tier
    # Pieces" (X/5) and "Tier Set" (colored track string), joined
    # against tier_set_reports by player_id.
    if data.gear_reports:
        headers = ["Player", "Avg iLvl", "Tier Pieces", "Tier Set", "Gems", "Missing Enchants"]
        numeric_indices = _numeric_indices(headers)
        tier_by_player = {t.player_id: t for t in data.tier_set_reports}
        rows = []
        for r in data.gear_reports:
            tier = tier_by_player.get(r.player_id)
            if tier is None or not tier.has_data:
                tier_pieces_cell = "n/a"
                tier_set_html = _tier_set_string_html(MISSING_TIER_PIECE_CHAR * TOTAL_TIER_SLOTS)
            else:
                tier_pieces_cell = f"{tier.pieces_worn}/{TOTAL_TIER_SLOTS}"
                tier_set_html = _tier_set_string_html(tier.track_string)
            if not r.has_data:
                rows.append(_row(r.player_id, r.player_name, data, [
                    r.player_name, "no data", tier_pieces_cell, tier_set_html, "no data", "no data",
                ], numeric_indices, raw_indices=frozenset({3})))
                continue
            rows.append(_row(r.player_id, r.player_name, data, [
                r.player_name, f"{r.average_item_level:.1f}", tier_pieces_cell, tier_set_html,
                str(r.total_gems), ", ".join(r.missing_enchant_slots) or "none",
            ], numeric_indices, raw_indices=frozenset({3})))
        blocks.append(_section("Gear Check", _table(headers, rows, "No gear data.")))

    # 11. Consumables.
    if data.consumable_results:
        headers = ["Player", "# Missing", "Missing"]
        numeric_indices = _numeric_indices(headers)
        ranked = sorted(
            data.consumable_results,
            key=lambda p: len(p.missing_categories(data.consumable_categories)), reverse=True,
        )
        rows = [
            _row(p.player_id, p.player_name, data, [
                p.player_name, str(len(p.missing_categories(data.consumable_categories))),
                ", ".join(p.missing_categories(data.consumable_categories)) or "all covered",
            ], numeric_indices)
            for p in ranked
        ]
        consumables_html = _table(headers, rows, "No consumable data.")
        if data.vantus_check is not None:
            vc = data.vantus_check
            if not vc.threshold_met:
                vantus_line = f"Vantus Rune: not majority-used ({len(vc.players_with)} had it)."
            elif vc.players_missing:
                vantus_line = f"Vantus Rune: majority using it -- missing: {', '.join(vc.players_missing)}"
            else:
                vantus_line = "Vantus Rune: everyone who should have it, has it."
            consumables_html += f'<p class="empty-note">{_esc(vantus_line)}</p>'
        blocks.append(_section("Consumables", consumables_html))

    return "".join(blocks)


def _difficulty_chip_value(difficulty: int | None) -> str:
    return difficulty_name(difficulty)


def render_html(fights: list[FightReportData], title: str = "Raid Report", report_code: str | None = None) -> str:
    groups: OrderedDict[str, list[FightReportData]] = OrderedDict()
    for data in fights:
        groups.setdefault(data.parsed_fight.fight.name, []).append(data)

    all_roles_seen = set()
    all_difficulties_seen: set[str] = set()
    for data in fights:
        for role_info in data.player_roles.values():
            all_roles_seen.add(role_info.role)
        all_difficulties_seen.add(_difficulty_chip_value(data.parsed_fight.fight.difficulty))

    roles_for_filter = [r for r in ("tank", "healer", "melee", "ranged") if r in all_roles_seen] or \
        ["tank", "healer", "melee", "ranged"]
    role_chips = "".join(
        f'<label class="chip role-chip"><input type="checkbox" value="{r}" checked>{ROLE_LABELS[r]}</label>'
        for r in roles_for_filter
    )
    boss_chips = "".join(
        f'<label class="chip boss-chip"><input type="checkbox" value="{_esc(boss)}" checked>{_esc(boss)}</label>'
        for boss in groups.keys()
    )
    difficulties_for_filter = [d for d in _DIFFICULTY_ORDER if d in all_difficulties_seen]
    difficulties_for_filter += sorted(all_difficulties_seen - set(difficulties_for_filter))
    difficulty_chips = "".join(
        f'<label class="chip difficulty-chip"><input type="checkbox" value="{_esc(d)}" checked>{_esc(d)}</label>'
        for d in difficulties_for_filter
    )

    boss_sections = []
    for boss_name, pulls in groups.items():
        kill_count = sum(1 for p in pulls if p.parsed_fight.fight.kill)
        trend_html = _trend_view_html(pulls)
        pull_summaries = []
        for i, data in enumerate(pulls, start=1):
            fight = data.parsed_fight.fight
            status = "kill" if fight.kill else "wipe"
            difficulty_badge = _difficulty_badge_html(fight.difficulty)
            difficulty_value = _difficulty_chip_value(fight.difficulty)
            puller_name = _find_puller_name(data.parsed_fight)
            puller_html = f'<span class="pulled-by">Pulled by {_esc(puller_name)}</span>' if puller_name else ""
            pull_summaries.append(f"""
            <details class="pull-section" data-difficulty="{_esc(difficulty_value)}">
                <summary>
                    {difficulty_badge}
                    <span class="badge {status}">{status.upper()}</span>
                    Pull {i} &mdash; {format_timestamp(fight.duration_ms)}
                    {puller_html}
                </summary>
                <div class="pull-body">{_render_pull_sections(data)}</div>
            </details>
            """)
        boss_sections.append(f"""
        <details class="boss-section" data-boss="{_esc(boss_name)}" open>
            <summary>{_esc(boss_name)} <span class="pull-count">({len(pulls)} pull(s), {kill_count} kill(s))</span></summary>
            {trend_html}
            {''.join(pull_summaries)}
        </details>
        """)

    report_link_html = ""
    if report_code:
        report_url = f"https://www.warcraftlogs.com/reports/{report_code}"
        report_link_html = (
            f'<div class="report-link">'
            f'<a href="{_esc(report_url)}" target="_blank" rel="noopener noreferrer">'
            f"View original report on Warcraft Logs &#8599;</a></div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="header">
    <h1>{_esc(title)}</h1>
    {report_link_html}
    <div class="subtitle">{len(groups)} boss(es), {len(fights)} pull(s) total</div>
</div>
<div class="filter-bar">
    <div class="filter-group">
        <div class="label">Role</div>
        <div class="filter-chips">{role_chips}</div>
    </div>
    <div class="filter-group">
        <div class="label">Boss</div>
        <div class="filter-chips">{boss_chips}</div>
    </div>
    <div class="filter-group">
        <div class="label">Difficulty</div>
        <div class="filter-chips">{difficulty_chips}</div>
    </div>
    <div class="filter-group">
        <div class="label">Player</div>
        <input type="text" id="player-search" placeholder="Search player...">
    </div>
    <button id="reset-filters">Reset filters</button>
</div>
<div class="content">
    {''.join(boss_sections)}
</div>
<script>{_JS}</script>
</body>
</html>"""


def write_html_report(
    fights: list[FightReportData], output_path: str, title: str = "Raid Report", report_code: str | None = None
) -> str:
    html_content = render_html(fights, title=title, report_code=report_code)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return output_path
