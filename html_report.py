"""
html_report.py
Renders a whole raid night (or, via main.py's --html flag, a SINGLE
fight) into ONE self-contained HTML file: collapsible per-boss,
per-pull, and per-section details, with client-side filters, a direct
link back to the original Warcraft Logs report, class-colored meter
bars for Damage Done/Healing/Damage Taken/Damage Prevented, a
difficulty badge on every pull, and consistent right-aligned/comma-
formatted numeric columns everywhere.

CHANGED (this update) -- added a DIFFICULTY filter (LFR / Normal /
Heroic / Mythic) alongside the existing Role/Boss/Player filters.
Difficulty is a per-PULL property (the same boss can legitimately be
pulled at more than one difficulty in a single raid night, e.g. Heroic
farm + Mythic progression back to back), so this filters individual
pull-sections directly (via a new data-difficulty attribute), not
whole boss-sections the way the Boss filter does. As a bit of extra
polish: if every pull under a boss gets filtered out by the difficulty
selection, that boss-section is now ALSO hidden (via a boss-level
"any visible pull?" check re-run whenever filters change), rather than
showing an empty expanded boss card with nothing inside it. Only
difficulties actually PRESENT across the fights passed in are shown as
chips (same pattern already used for the Role filter).

CHANGED (prior update) -- four report-polish features, all pure
server-side rendering (no external JS libraries, no charting library --
everything is either plain CSS or inline SVG built as strings):

  1. TIMELINE STRIP -- a per-pull, three-lane horizontal strip (Deaths /
     Raid Cooldowns / Defensive Cooldowns) showing WHEN each event
     happened across the fight, positioned by percentage-of-duration.
     Native <title> tooltips on hover (no JS needed) show the exact
     M:SS, player, and ability. Sits at the top of every pull, ABOVE
     the collapsible sections, so it's visible without expanding
     anything else.
     NOTE ON TIMESTAMP CONVENTION: CooldownUsage.cast_timestamps (both
     raid and defensive) are RAW, report-relative milliseconds --
     confirmed directly in cooldown_analyzer.py's source, which never
     subtracts fight.start_time (the same reason death_analyzer.py has
     to do that subtraction itself for time_into_fight_ms). This file
     uses time_format.fight_relative_ms() to convert those raw values
     into fight-relative offsets before turning them into a percentage
     position on the strip. Deaths are unaffected --
     DeathReport.time_into_fight_ms is already fight-relative.

  2. STICKY MINI-SCOREBOARD -- a small non-collapsible KPI strip
     (Result, Duration, Top DPS, Top HPS, Deaths) at the top of every
     pull's body, using position:sticky so it stays visible under the
     filter bar while you scroll through a pull's expanded sections.
     The exact sticky offset is set at runtime via a tiny bit of JS
     that measures the actual rendered height of the filter bar -- a
     fixed guessed px value would break any time the filter bar wraps
     to a different number of lines (e.g. narrower browser window,
     more boss/difficulty chips).

  3. COLOR-CODED EFFICIENCY -- the Efficiency column in both Raid
     Cooldown Usage and Defensive Cooldown Usage tables renders as a
     colored pill (red / amber / green) instead of plain text, using
     the same threshold buckets in both places (see
     EFFICIENCY_LOW_MAX / EFFICIENCY_MID_MAX below).

  4. TREND VIEW ACROSS PULLS -- when a boss has 2+ pulls, a small pair
     of inline-SVG bar charts (raid DPS per pull, deaths per pull) is
     shown once per boss, right under the boss summary line and above
     the individual pull accordions -- kill pulls are colored green,
     wipes are colored the same muted "wipe" red used elsewhere.

CHANGED (prior update) -- every fight-relative timestamp (pull duration
in each pull's accordion header, death times in the Deaths table, and
defensive-cooldown cast times in the Damage Prevented by Defensives
detail tables) renders as M:SS (e.g. "3:03") via
time_format.format_timestamp(), instead of raw seconds (e.g. "183.0s").

CHANGED (prior update) -- "Damage Prevented by Defensives" uses the
SAME meter-bar treatment (class-colored relative-width bar behind the
player's name) as Damage Done/Healing/Damage Taken, instead of a plain
table, including a distinct hatched/dimmed "no estimate possible" bar
style for players whose only usage was an immunity-type defensive.

Pure rendering: takes a list of already-built FightReportData and
produces HTML. Does not call any analyzer and does not touch the
network.
"""
from __future__ import annotations
import html as html_module
from collections import OrderedDict
import class_colors
import difficulty_names
import gear_analyzer
from report import FightReportData
from time_format import format_timestamp, fight_relative_ms

_BG = "#14151a"

# Efficiency-pill thresholds, shared by both Raid Cooldown Usage and
# Defensive Cooldown Usage tables: below EFFICIENCY_LOW_MAX -> red,
# between LOW_MAX and MID_MAX -> amber, at or above MID_MAX -> green.
EFFICIENCY_LOW_MAX = 40.0
EFFICIENCY_MID_MAX = 70.0

# Neutral fallback color for timeline markers/meter bars when a
# player's class can't be resolved (e.g. missing CombatantInfo) --
# avoids passing None straight into an inline CSS color value.
_FALLBACK_MARKER_COLOR = "#8a8d9c"

# Canonical raid-difficulty ordering for the filter chips (LFR is the
# lowest-effort/loosest tuning, Mythic the highest) -- chips are shown
# in this order whenever more than one is present, rather than in
# whatever order fights happen to appear in.
_DIFFICULTY_ORDER = ["LFR", "Normal", "Heroic", "Mythic"]


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


# Every header string (across every table built in this file) that
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
    "Avg iLvl", "Gems",
    "Total Hits",
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

/* ---- Efficiency pills (feature 3) ---- */
.eff-pill {{
    display: inline-block; padding: 2px 9px; border-radius: 10px;
    font-weight: 700; font-variant-numeric: tabular-nums; font-size: 12px;
}}
.eff-low  {{ background: rgba(255,107,107,0.18); color: var(--wipe); }}
.eff-mid  {{ background: rgba(255,184,77,0.18);  color: var(--warn); }}
.eff-high {{ background: rgba(61,220,132,0.18);  color: var(--kill); }}

/* ---- Sticky mini-scoreboard (feature 2) ---- */
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

/* ---- Timeline strip (feature 1) ---- */
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

/* ---- Trend view across pulls (feature 4) ---- */
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

    // Difficulty filter operates at the PULL level (a boss can be
    // pulled at more than one difficulty in the same raid night), then
    // hides the whole boss-section if none of its pulls remain visible.
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
DEFAULT_OPEN_SECTIONS = {"Deaths", "Healing", "Damage Done (DPS)"}


def _esc(value) -> str:
    return html_module.escape(str(value if value is not None else ""))


def _numeric_indices(headers: list[str]) -> frozenset[int]:
    return frozenset(i for i, h in enumerate(headers) if h in NUMERIC_HEADERS)


def _difficulty_badge_html(difficulty: int | None) -> str:
    name = difficulty_names.difficulty_name(difficulty)
    color = difficulty_names.difficulty_color(difficulty)
    r, g, b = _hex_to_rgb(color)
    return (
        f'<span class="badge" style="background-color:rgba({r},{g},{b},0.18);color:{color};">'
        f"{_esc(name.upper())}</span>"
    )


def _role_of(player_id, data: FightReportData) -> str:
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
    color = class_colors.get_class_color(class_name)
    return color or _FALLBACK_MARKER_COLOR


def _efficiency_pill(efficiency_fraction: float) -> str:
    """
    efficiency_fraction is 0.0-1.0 (as returned by CooldownUsage.efficiency()).
    Buckets: < EFFICIENCY_LOW_MAX -> red, < EFFICIENCY_MID_MAX -> amber,
    else green -- same thresholds for both Raid and Defensive Cooldown
    Usage tables.
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
    HTML (e.g. an efficiency pill span) that must NOT be passed through
    _esc() a second time -- every such cell is built exclusively by this
    module itself (never from raw log/user data), so this is safe.
    """
    role = _role_of(player_id, data)
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
    no_estimate: bool = False,
) -> str:
    role = _role_of(player_id, data)
    class_name = _class_of(player_id, data)
    bar_color = class_colors.get_class_color(class_name)
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
        f'<td class="num">{_esc(c)}</td>' if i in numeric_indices else f"<td>{_esc(c)}</td>"
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
# Feature 2: sticky mini-scoreboard
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
        if not has_role_data or _role_of(s.healer_id, data) in ("tank", "healer")
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
# Feature 1: timeline strip
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
# Feature 4: trend view across pulls (per boss, 2+ pulls only)
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

    dps_values, dps_labels, death_values, death_labels, is_kill, pull_labels = [], [], [], [], [], []
    for i, data in enumerate(pulls, start=1):
        fight = data.parsed_fight.fight
        duration_s = fight.duration_ms / 1000 if fight.duration_ms > 0 else 0
        raid_dps = (
            sum(s.total_damage_done for s in data.damage_done_summaries) / duration_s
            if duration_s > 0 else 0.0
        )
        dps_values.append(raid_dps)
        dps_labels.append(f"{raid_dps:,.0f} raid DPS")
        death_count = len(data.death_reports)
        death_values.append(float(death_count))
        death_labels.append(f"{death_count} death(s)")
        is_kill.append(fight.kill)
        pull_labels.append(str(i))

    dps_chart = _bar_chart_svg(dps_values, is_kill, dps_labels, pull_labels)
    death_chart = _bar_chart_svg(death_values, is_kill, death_labels, pull_labels)

    return f"""
    <div class="trend-view">
        <div class="trend-chart-block">
            <div class="trend-chart-title">Raid DPS by Pull</div>
            {dps_chart}
        </div>
        <div class="trend-chart-block">
            <div class="trend-chart-title">Deaths by Pull</div>
            {death_chart}
        </div>
    </div>
    """


def _render_pull_sections(data: FightReportData) -> str:
    duration_ms = data.parsed_fight.fight.duration_ms
    blocks: list[str] = []

    # Mini-scoreboard (feature 2) and timeline strip (feature 1) always
    # go first, above every collapsible section.
    blocks.append(_mini_scoreboard_html(data))
    blocks.append(_timeline_strip_html(data))

    # 1. Deaths -- open by default.
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
    # 2. Avoidable Damage.
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
    # 3. Healing -- open by default; restricted to tank/healer rows
    # whenever role data is available; METER bars, scaled to the top
    # ROW WITHIN THIS (already tank/healer-filtered) LIST.
    if data.healer_summaries:
        headers = ["Healer", "HPS", "Effective", "Overheal", "Overheal %"]
        numeric_indices = _numeric_indices(headers[1:])
        has_role_data = bool(data.player_roles)
        relevant = [
            s for s in data.healer_summaries
            if not has_role_data or _role_of(s.healer_id, data) in ("tank", "healer")
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
    # 4. Damage Done (DPS) -- open by default; METER bars.
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
    # 5. Damage Taken -- collapsed by default; METER bars.
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
    # 6. Biggest Hits -- collapsed by default.
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
    # 7. Raid Cooldown Usage -- collapsed by default. Efficiency column
    # is now a color-coded pill (feature 3), built in _cooldown_usage_rows.
    if data.cooldown_usages:
        headers = ["Player", "Ability", "Casts/Max", "Efficiency"]
        blocks.append(_section("Raid Cooldown Usage", _table(
            headers, _cooldown_usage_rows(data.cooldown_usages, data, duration_ms),
            "No tracked raid cooldowns used.",
        )))
    # 8. Defensive Cooldown Usage -- collapsed by default. Same pill treatment.
    if data.defensive_cooldown_usages:
        headers = ["Player", "Ability", "Casts/Max", "Efficiency"]
        blocks.append(_section("Defensive Cooldown Usage", _table(
            headers, _cooldown_usage_rows(data.defensive_cooldown_usages, data, duration_ms),
            "No tracked defensive cooldowns used.",
        )))
    # 9. Damage Prevented by Defensives -- collapsed by default.
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
    # 10. Consumables -- collapsed by default.
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
    # 11. Gear Check -- collapsed by default.
    if data.gear_reports:
        headers = ["Player", "Avg iLvl", "Lowest Quality", "Gems", "Missing Enchants"]
        numeric_indices = _numeric_indices(headers)
        rows = []
        for r in data.gear_reports:
            if not r.has_data:
                rows.append(_row(r.player_id, r.player_name, data,
                                  [r.player_name, "no data", "no data", "no data", "no data"], numeric_indices))
                continue
            lowest = gear_analyzer.quality_name(r.lowest_quality) if r.lowest_quality is not None else "n/a"
            rows.append(_row(r.player_id, r.player_name, data, [
                r.player_name, f"{r.average_item_level:.1f}", lowest,
                str(r.total_gems), ", ".join(r.missing_enchant_slots) or "none",
            ], numeric_indices))
        blocks.append(_section("Gear Check", _table(headers, rows, "No gear data.")))
    return "".join(blocks)


def _difficulty_chip_value(difficulty: int | None) -> str:
    """The chip 'value' and pull-section data-difficulty attribute -- the
    human-readable name itself (e.g. "Heroic"), since difficulty_names
    is the single source of truth for id -> name and reusing its output
    directly avoids maintaining a second id/name mapping just for the
    filter."""
    return difficulty_names.difficulty_name(difficulty)


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
    # Show difficulty chips in canonical LFR->Mythic order, but only for
    # difficulties actually present across the fights passed in; any
    # unrecognized/custom difficulty name still shows up, appended after
    # the canonical ones, rather than being silently dropped.
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
            pull_summaries.append(f"""
            <details class="pull-section" data-difficulty="{_esc(difficulty_value)}">
                <summary>
                    {difficulty_badge}
                    <span class="badge {status}">{status.upper()}</span>
                    Pull {i} &mdash; {format_timestamp(fight.duration_ms)}
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
