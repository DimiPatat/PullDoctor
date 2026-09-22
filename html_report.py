"""
html_report.py

Renders a whole raid night (or, via main.py's --html flag, a SINGLE
fight) into ONE self-contained HTML file: collapsible per-boss,
per-pull, and per-section details, with client-side filters, a direct
link back to the original Warcraft Logs report, class-colored meter
bars for Damage Done/Healing/Damage Taken/Damage Prevented, a
difficulty badge on every pull, and consistent right-aligned/comma-
formatted numeric columns everywhere.

CHANGED (this update) -- "Damage Prevented by Defensives" now uses the
SAME meter-bar treatment (class-colored relative-width bar behind the
player's name) as Damage Done/Healing/Damage Taken, instead of a plain
table. Specifically:
  - The OVERVIEW table (one row per player, ranked by total damage
    prevented) now has a meter bar, scaled the same way as every other
    meter section: the top row (highest total_damage_prevented) is
    100% width, everyone else is relative to that.
  - The per-player WINDOW DETAIL tables (individual defensive casts)
    remain plain tables underneath each player's row -- a meter bar
    doesn't make sense there, since each row is a single EVENT in
    time, not a competing entry among peers to rank against.
  - Players who only used "immunity" defensives (no damage_prevented
    value possible at all -- see defensive_damage_prevention_analyzer.py)
    still get a bar, but at 0% width and a distinct "no estimate
    possible" note instead of a plain 0, so it's clear this is a
    "can't be measured" case, not "measured and found to be zero".

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

_BG = "#14151a"


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
    /* A 0%-width bar has nothing for a background pattern to render
       ON -- so a "no estimate possible" bar needs a small fixed-width
       STUB to actually be visible at all, otherwise this looks
       IDENTICAL to a real measured-zero bar (confirmed visually via a
       real rendered screenshot before this fix -- an invisible 0px
       hatch pattern doesn't distinguish anything). min-width gives it
       a deliberately small, unmistakably-not-a-real-percentage sliver
       instead.
    */
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
"""

_JS = """
function applyFilters() {
    var allRoleBoxes = Array.prototype.slice.call(document.querySelectorAll('.role-chip input'));
    var checkedRoles = allRoleBoxes.filter(function(el) { return el.checked; }).map(function(el) { return el.value; });
    var allRolesChecked = allRoleBoxes.length === 0 || checkedRoles.length === allRoleBoxes.length;

    var checkedBosses = Array.prototype.map.call(document.querySelectorAll('.boss-chip input:checked'), function(el) { return el.value; });
    var query = document.getElementById('player-search').value.trim().toLowerCase();

    document.querySelectorAll('.boss-section').forEach(function(section) {
        section.style.display = checkedBosses.indexOf(section.dataset.boss) !== -1 ? '' : 'none';
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
    document.querySelectorAll('.role-chip input, .boss-chip input').forEach(function(el) { el.checked = true; });
    document.getElementById('player-search').value = '';
    applyFilters();
}
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.role-chip input, .boss-chip input').forEach(function(el) {
        el.addEventListener('change', applyFilters);
    });
    document.getElementById('player-search').addEventListener('input', applyFilters);
    document.getElementById('reset-filters').addEventListener('click', resetFilters);
    applyFilters();
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


def _row(player_id, player_name, data: FightReportData, cells: list, numeric_indices: frozenset[int] = frozenset()) -> str:
    role = _role_of(player_id, data)
    attrs = f'data-player="{_esc(player_name)}"'
    if role:
        attrs += f' data-role="{role}"'
    cell_html = "".join(
        f'<td class="num">{_esc(c)}</td>' if i in numeric_indices else f"<td>{_esc(c)}</td>"
        for i, c in enumerate(cells)
    )
    return f"<tr {attrs}>{cell_html}</tr>"


def _meter_row(
    player_id, player_name, data: FightReportData, value: float, max_value: float,
    other_cells: list, numeric_indices: frozenset[int] = frozenset(),
    no_estimate: bool = False,
) -> str:
    """
    Same as _row(), but the FIRST cell is a "meter cell" (name + class-
    colored relative bar) rather than plain text.

    no_estimate: when True, renders the bar at 0% width with a
    "no-estimate" hatched/dimmed style (see .meter-bar.no-estimate CSS)
    instead of a plain empty bar -- used for players whose ONLY
    defensive usage was an "immunity" type with no damage_prevented
    value at all, so it's visually distinct from "measured at zero".
    """
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
            f"{u.efficiency(duration_ms) * 100:.1f}%",
        ], numeric_indices)
        for u in usages
    ]


def _render_pull_sections(data: FightReportData) -> str:
    duration_ms = data.parsed_fight.fight.duration_ms
    blocks: list[str] = []

    # 1. Deaths -- open by default.
    if data.death_reports:
        headers = ["Time", "Victim", "Killed By", "Dmg (window)", "Heal (window)"]
        numeric_indices = _numeric_indices(headers)
        rows = [
            _row(r.victim_id, r.victim_name, data, [
                f"{r.time_into_fight_ms / 1000:.1f}s", r.victim_name, r.killing_ability_name,
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

    # 7. Raid Cooldown Usage -- collapsed by default.
    if data.cooldown_usages:
        headers = ["Player", "Ability", "Casts/Max", "Efficiency"]
        blocks.append(_section("Raid Cooldown Usage", _table(
            headers, _cooldown_usage_rows(data.cooldown_usages, data, duration_ms),
            "No tracked raid cooldowns used.",
        )))

    # 8. Defensive Cooldown Usage -- collapsed by default.
    if data.defensive_cooldown_usages:
        headers = ["Player", "Ability", "Casts/Max", "Efficiency"]
        blocks.append(_section("Defensive Cooldown Usage", _table(
            headers, _cooldown_usage_rows(data.defensive_cooldown_usages, data, duration_ms),
            "No tracked defensive cooldowns used.",
        )))

    # 9. Damage Prevented by Defensives -- collapsed by default.
    # CHANGED: overview table now uses meter bars, same as Damage Done/
    # Healing/Damage Taken. Scaling is against the TOP entry's
    # total_damage_prevented (entries are already sorted descending by
    # this same analyzer -- see defensive_damage_prevention_analyzer.py).
    # A player whose only tracked usage was "immunity" (no damage
    # prevented value is even POSSIBLE) gets a distinct hatched/dimmed
    # 0%-width bar rather than looking identical to "measured, and it
    # was zero".
    if data.defensive_damage_prevention:
        overview_headers = ["Player", "Prevented", "Dmg Taken (windows)", "Windows"]
        overview_numeric_indices = _numeric_indices(overview_headers[1:])
        max_prevented = data.defensive_damage_prevention[0].total_damage_prevented
        overview_rows = []
        for e in data.defensive_damage_prevention:
            has_any_estimate = bool(e.windows_with_estimate)
            # "n/a" (not "0") for the Prevented cell when NO window has
            # an estimate at all -- a real "0" would misleadingly imply
            # "measured, and it happened to prevent nothing", when the
            # true situation is "cannot be measured for this ability
            # type" (see defensive_damage_prevention_analyzer.py).
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
                        f"{w.cast_timestamp / 1000:.1f}s", w.ability_name, "immunity",
                        f"{w.actual_damage_taken:,} (residual)", "n/a",
                    ], detail_numeric_indices))
                else:
                    window_rows.append(_row(entry.player_id, entry.player_name, data, [
                        f"{w.cast_timestamp / 1000:.1f}s", w.ability_name,
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


def render_html(fights: list[FightReportData], title: str = "Raid Report", report_code: str | None = None) -> str:
    groups: OrderedDict[str, list[FightReportData]] = OrderedDict()
    for data in fights:
        groups.setdefault(data.parsed_fight.fight.name, []).append(data)

    all_roles_seen = set()
    for data in fights:
        for role_info in data.player_roles.values():
            all_roles_seen.add(role_info.role)
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

    boss_sections = []
    for boss_name, pulls in groups.items():
        kill_count = sum(1 for p in pulls if p.parsed_fight.fight.kill)
        pull_summaries = []
        for i, data in enumerate(pulls, start=1):
            fight = data.parsed_fight.fight
            status = "kill" if fight.kill else "wipe"
            difficulty_badge = _difficulty_badge_html(fight.difficulty)
            pull_summaries.append(f"""
            <details class="pull-section">
                <summary>
                    {difficulty_badge}
                    <span class="badge {status}">{status.upper()}</span>
                    Pull {i} &mdash; {fight.duration_ms / 1000:.1f}s
                </summary>
                <div class="pull-body">{_render_pull_sections(data)}</div>
            </details>
            """)
        boss_sections.append(f"""
        <details class="boss-section" data-boss="{_esc(boss_name)}" open>
            <summary>{_esc(boss_name)} <span class="pull-count">({len(pulls)} pull(s), {kill_count} kill(s))</span></summary>
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
