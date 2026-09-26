"""
damage_targets.py

MERGED MODULE -- combines what used to be four separate files into one,
as part of the PullDoctor file-count reduction pass:
    - target_label_schema.py       (TargetLabel / EncounterTargetLabels dataclasses)
    - target_label_config_io.py    (load/save/validate/repair .generated.json)
    - target_label_config.py       (MANUAL_ENCOUNTERS + merge -> ENCOUNTER_TARGET_LABELS)
    - damage_to_target_analyzer.py (the actual per-target/per-player damage analysis)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from core import Actor, ParsedFight, parse_fight_bundle, parse_selection, resolve_fight_id, resolve_to_player
from wcl import WCLClient, WCLAPIError, get_credentials

@dataclass
class TargetLabel:
    target_name: str
    display_name: str
    category: str | None = None
    notes: str | None = None


@dataclass
class EncounterTargetLabels:
    encounter_id: int
    encounter_name: str
    labels: list[TargetLabel] = field(default_factory=list)


DEFAULT_CONFIG_PATH = Path(__file__).with_name("damage_targets.generated.json")
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


class TargetLabelConfigError(Exception):
    """Raised when damage_targets.generated.json can't be parsed or doesn't have the expected shape."""


def _looks_like_trailing_comma(text: str, pos: int) -> bool:
    left = pos
    while left > 0 and text[left - 1].isspace():
        left -= 1
    left_char = text[left - 1] if left > 0 else ""
    right = pos
    while right < len(text) and text[right].isspace():
        right += 1
    right_char = text[right] if right < len(text) else ""
    if left_char == "," and right_char in "]}":
        return True
    if pos < len(text) and text[pos] == ",":
        after_comma = pos + 1
        while after_comma < len(text) and text[after_comma].isspace():
            after_comma += 1
        after_comma_char = text[after_comma] if after_comma < len(text) else ""
        if after_comma_char in "]}":
            return True
    return False


def _describe_json_error(text: str, error: json.JSONDecodeError) -> str:
    lines = text.splitlines()
    line_no = error.lineno
    col_no = error.colno
    start = max(1, line_no - 2)
    end = min(len(lines), line_no + 2) if lines else line_no
    context: list[str] = []
    for n in range(start, end + 1):
        if n - 1 >= len(lines):
            continue
        marker = ">>" if n == line_no else "  "
        label = f"{marker} {n:>4} | "
        context.append(f"{label}{lines[n - 1]}")
        if n == line_no:
            context.append(" " * (len(label) + max(0, col_no - 1)) + "^")
    message_lower = (error.msg or "").lower()
    if "trailing comma" in message_lower or _looks_like_trailing_comma(text, error.pos):
        hint = (
            "Likely cause: a trailing comma before a closing ] or } -- JSON "
            "(unlike Python) doesn't allow a comma right before the closing "
            "bracket. Delete that comma, or run:\n"
            "  python damage_targets_cli.py repair"
        )
    elif "expecting property name" in message_lower:
        hint = (
            "Likely cause: a trailing comma before a closing } -- delete it, or run:\n"
            "  python damage_targets_cli.py repair"
        )
    elif "expecting ',' delimiter" in message_lower:
        hint = "Likely cause: a comma is MISSING between two items."
    elif "expecting value" in message_lower:
        hint = "Likely cause: an empty spot where a value was expected -- check for a stray comma."
    else:
        hint = "Check the line pointed to above for a syntax mistake."
    return f"{error.msg} at line {line_no}, column {col_no}\n\n" + "\n".join(context) + "\n\n" + hint


def _load_raw_or_raise(config_path: Path) -> dict:
    text = config_path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TargetLabelConfigError(
            f"{config_path} is not valid JSON:\n\n"
            f"{_describe_json_error(text, exc)}\n\n"
            f"You can also check/fix it without touching a report:\n"
            f"  python damage_targets_cli.py validate\n"
            f"  python damage_targets_cli.py repair"
        ) from exc
    if not isinstance(raw, dict):
        raise TargetLabelConfigError(f"{config_path} does not contain a JSON object at the top level.")
    return raw


def backup_file(path: str | Path) -> Path | None:
    config_path = Path(path)
    if not config_path.exists():
        return None
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    return backup_path


def load_generated_target_labels(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> dict[int, EncounterTargetLabels]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    raw = _load_raw_or_raise(config_path)
    encounters: dict[int, EncounterTargetLabels] = {}
    for entry in raw.get("encounters", []):
        encounter_id = int(entry["encounter_id"])
        labels = [
            TargetLabel(
                target_name=item["target_name"],
                display_name=item["display_name"],
                category=item.get("category"),
                notes=item.get("notes"),
            )
            for item in entry.get("labels", [])
        ]
        encounters[encounter_id] = EncounterTargetLabels(
            encounter_id=encounter_id,
            encounter_name=entry.get("encounter_name", f"Encounter {encounter_id}"),
            labels=labels,
        )
    return encounters


def _label_to_json(label: TargetLabel) -> dict:
    return {
        "target_name": label.target_name,
        "display_name": label.display_name,
        "category": label.category,
        "notes": label.notes,
    }


def _write_json(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def save_target_labels(
    encounter_id: int,
    encounter_name: str,
    labels: list[TargetLabel],
    path: str | Path = DEFAULT_CONFIG_PATH,
    replace: bool = False,
    verbose: bool = False,
) -> Path:
    config_path = Path(path)
    existing = {"version": 1, "encounters": []}
    if config_path.exists():
        existing = _load_raw_or_raise(config_path)
        existing.setdefault("encounters", [])
    encounters = existing.setdefault("encounters", [])
    encounter_entry = None
    encounter_index = None
    for index, entry in enumerate(encounters):
        if int(entry.get("encounter_id", -1)) == encounter_id:
            encounter_entry = entry
            encounter_index = index
            break
    if replace or encounter_entry is None:
        if verbose and encounter_entry is not None:
            print(f"Replacing all previously stored target labels for encounter {encounter_id}.")
        merged_by_name: dict[str, dict] = {}
    else:
        merged_by_name = {item["target_name"]: dict(item) for item in encounter_entry.get("labels", [])}
    for label in labels:
        new_json = _label_to_json(label)
        prior = merged_by_name.get(label.target_name)
        if prior is None:
            if verbose:
                print(f"  + Added new label: {label.target_name} -> {label.display_name}")
        else:
            if new_json["category"] is None:
                new_json["category"] = prior.get("category")
            if new_json["notes"] is None:
                new_json["notes"] = prior.get("notes")
            if verbose:
                print(f"  ~ Updated label: {label.target_name} -> {label.display_name}")
        merged_by_name[label.target_name] = new_json
    replacement_entry = {
        "encounter_id": encounter_id,
        "encounter_name": encounter_name,
        "labels": list(merged_by_name.values()),
    }
    if encounter_index is not None:
        encounters[encounter_index] = replacement_entry
    else:
        encounters.append(replacement_entry)
    encounters.sort(key=lambda item: int(item["encounter_id"]))
    backup_file(config_path)
    _write_json(config_path, existing)
    return config_path


def remove_labels(
    encounter_id: int,
    target_names: list[str],
    path: str | Path = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
) -> list[str]:
    config_path = Path(path)
    if not config_path.exists():
        return []
    existing = _load_raw_or_raise(config_path)
    target_set = set(target_names)
    removed: list[str] = []
    for entry in existing.get("encounters", []):
        if int(entry.get("encounter_id", -1)) != encounter_id:
            continue
        kept = []
        for label_json in entry.get("labels", []):
            if label_json.get("target_name") in target_set:
                removed.append(label_json["target_name"])
                if verbose:
                    print(f"  - Removed label for: {label_json['target_name']}")
            else:
                kept.append(label_json)
        entry["labels"] = kept
        break
    if removed:
        backup_file(config_path)
        _write_json(config_path, existing)
    return removed


def remove_encounter(
    encounter_id: int,
    path: str | Path = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
) -> bool:
    config_path = Path(path)
    if not config_path.exists():
        return False
    existing = _load_raw_or_raise(config_path)
    encounters = existing.get("encounters", [])
    remaining = [e for e in encounters if int(e.get("encounter_id", -1)) != encounter_id]
    if len(remaining) == len(encounters):
        return False
    if verbose:
        removed_entry = next(e for e in encounters if int(e.get("encounter_id", -1)) == encounter_id)
        print(f"  - Removed encounter {encounter_id} ({removed_entry.get('encounter_name')}).")
    backup_file(config_path)
    existing["encounters"] = remaining
    _write_json(config_path, existing)
    return True


def validate_file(path: str | Path = DEFAULT_CONFIG_PATH) -> tuple[bool, str]:
    config_path = Path(path)
    if not config_path.exists():
        return True, f"{config_path} does not exist yet -- nothing to validate."
    text = config_path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return False, f"{config_path} is NOT valid JSON:\n\n{_describe_json_error(text, exc)}"
    if not isinstance(raw, dict) or "encounters" not in raw:
        return False, f"{config_path} is valid JSON but is missing the expected 'encounters' list."
    count = len(raw.get("encounters", []))
    return True, f"{config_path} is valid -- {count} encounter(s) found."


def repair_trailing_commas(
    path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False
) -> tuple[bool, str]:
    config_path = Path(path)
    if not config_path.exists():
        return True, f"{config_path} does not exist yet -- nothing to repair."
    text = config_path.read_text(encoding="utf-8")
    original_error: json.JSONDecodeError | None = None
    try:
        json.loads(text)
        return True, f"{config_path} is already valid JSON -- nothing to repair."
    except json.JSONDecodeError as exc:
        original_error = exc
    fixed_text = _TRAILING_COMMA_RE.sub(r"\1", text)
    if fixed_text == text:
        return False, f"No trailing comma found to auto-fix.\n\n{_describe_json_error(text, original_error)}"
    try:
        parsed = json.loads(fixed_text)
    except json.JSONDecodeError as still_broken:
        return False, (
            f"Removed a trailing comma, but the file still isn't valid JSON after "
            f"that -- there may be more than one problem.\n\n"
            f"{_describe_json_error(fixed_text, still_broken)}"
        )
    backup_path = backup_file(config_path)
    _write_json(config_path, parsed)
    if verbose:
        print(f"  - Removed a trailing comma from {config_path}")
    backup_note = f" A backup of the original was saved to {backup_path}." if backup_path else ""
    return True, f"Fixed {config_path}.{backup_note}"


MANUAL_ENCOUNTERS: dict[int, EncounterTargetLabels] = {
}

try:
    _generated_target_label_encounters = load_generated_target_labels()
except TargetLabelConfigError as exc:
    print(
        "WARNING: damage_targets.generated.json could not be read -- "
        "ignoring it for this run.\n"
        f"{exc}\n"
    )
    _generated_target_label_encounters = {}

ENCOUNTER_TARGET_LABELS: dict[int, EncounterTargetLabels] = dict(_generated_target_label_encounters)
ENCOUNTER_TARGET_LABELS.update(MANUAL_ENCOUNTERS)


def _is_enemy_npc(actor: Actor | None) -> bool:
    if actor is None:
        return False
    return actor.type == "NPC" and actor.owner_id is None


@dataclass
class TargetSummary:
    target_name: str
    target_ids: set[int] = field(default_factory=set)
    total_damage: int = 0
    damage_by_player_id: dict[int, int] = field(default_factory=dict)
    player_names: dict[int, str] = field(default_factory=dict)
    first_seen_ms: int | None = None
    last_seen_ms: int | None = None

    @property
    def instance_count(self) -> int:
        return len(self.target_ids)

    def damage_by(self, player_id: int) -> int:
        return self.damage_by_player_id.get(player_id, 0)

    def ranked_players(self) -> list[tuple[int, str, int]]:
        return sorted(
            ((pid, self.player_names.get(pid, "Unknown"), dmg) for pid, dmg in self.damage_by_player_id.items()),
            key=lambda row: row[2], reverse=True,
        )


@dataclass
class PlayerTargetBreakdown:
    player_id: int
    player_name: str
    damage_by_target_name: dict[str, int] = field(default_factory=dict)
    total_damage: int = 0

    def damage_to(self, target_name: str) -> int:
        return self.damage_by_target_name.get(target_name, 0)

    def ranked_targets(self) -> list[tuple[str, int]]:
        return sorted(self.damage_by_target_name.items(), key=lambda row: row[1], reverse=True)


def analyze_damage_by_target(parsed_fight: ParsedFight) -> list[TargetSummary]:
    summaries: dict[str, TargetSummary] = {}
    for event in parsed_fight.events:
        if event.data_type != "DamageDone":
            continue
        target_actor = parsed_fight.actors.get(event.target_id) if event.target_id is not None else None
        if not _is_enemy_npc(target_actor):
            continue
        player = resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue
        target_name = event.target_name or "Unknown"
        if target_name not in summaries:
            summaries[target_name] = TargetSummary(target_name=target_name)
        summary = summaries[target_name]
        summary.target_ids.add(event.target_id)
        amount = event.amount or 0
        summary.total_damage += amount
        summary.damage_by_player_id[player.id] = summary.damage_by_player_id.get(player.id, 0) + amount
        summary.player_names[player.id] = player.name
        if summary.first_seen_ms is None or event.timestamp < summary.first_seen_ms:
            summary.first_seen_ms = event.timestamp
        if summary.last_seen_ms is None or event.timestamp > summary.last_seen_ms:
            summary.last_seen_ms = event.timestamp
    return sorted(summaries.values(), key=lambda s: s.total_damage, reverse=True)


def analyze_player_damage_breakdown(parsed_fight: ParsedFight) -> list[PlayerTargetBreakdown]:
    breakdowns: dict[int, PlayerTargetBreakdown] = {}
    for event in parsed_fight.events:
        if event.data_type != "DamageDone":
            continue
        target_actor = parsed_fight.actors.get(event.target_id) if event.target_id is not None else None
        if not _is_enemy_npc(target_actor):
            continue
        player = resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue
        if player.id not in breakdowns:
            breakdowns[player.id] = PlayerTargetBreakdown(player_id=player.id, player_name=player.name)
        breakdown = breakdowns[player.id]
        target_name = event.target_name or "Unknown"
        amount = event.amount or 0
        breakdown.damage_by_target_name[target_name] = (
            breakdown.damage_by_target_name.get(target_name, 0) + amount
        )
        breakdown.total_damage += amount
    return sorted(breakdowns.values(), key=lambda b: b.total_damage, reverse=True)


def get_player_breakdown(
    breakdowns: list[PlayerTargetBreakdown], player_name: str
) -> PlayerTargetBreakdown | None:
    for breakdown in breakdowns:
        if breakdown.player_name and breakdown.player_name.lower() == player_name.lower():
            return breakdown
    return None


@dataclass
class PlayerTargetMatrix:
    player_names: list[str] = field(default_factory=list)
    target_names: list[str] = field(default_factory=list)
    damage: dict[tuple[str, str], int] = field(default_factory=dict)
    player_totals: dict[str, int] = field(default_factory=dict)
    target_totals: dict[str, int] = field(default_factory=dict)

    def get_cell(self, player_name: str, target_name: str) -> int:
        return self.damage.get((player_name, target_name), 0)

    def row_for(self, player_name: str) -> list[int]:
        return [self.get_cell(player_name, target_name) for target_name in self.target_names]

    def column_for(self, target_name: str) -> list[int]:
        return [self.get_cell(player_name, target_name) for player_name in self.player_names]


def build_player_target_matrix(target_summaries: list[TargetSummary]) -> PlayerTargetMatrix:
    matrix = PlayerTargetMatrix()
    matrix.target_names = [summary.target_name for summary in target_summaries]
    player_totals: dict[str, int] = {}
    for summary in target_summaries:
        matrix.target_totals[summary.target_name] = summary.total_damage
        for player_id, player_name, damage in summary.ranked_players():
            matrix.damage[(player_name, summary.target_name)] = damage
            player_totals[player_name] = player_totals.get(player_name, 0) + damage
    matrix.player_totals = player_totals
    matrix.player_names = sorted(player_totals, key=lambda name: player_totals[name], reverse=True)
    return matrix


def format_player_target_matrix_table(
    matrix: PlayerTargetMatrix,
    max_target_column_width: int = 12,
    max_player_name_width: int = 14,
) -> str:
    if not matrix.player_names or not matrix.target_names:
        return "No player-vs-target data available for this fight."
    name_col_width = max_player_name_width
    col_width = max(max_target_column_width, 8)
    header_cells = [t[:col_width].rjust(col_width) for t in matrix.target_names]
    header = f"{'Player':<{name_col_width}} " + " ".join(header_cells) + " " + "Total".rjust(col_width)
    lines = [header, "-" * len(header)]
    for player_name in matrix.player_names:
        row_cells = [f"{matrix.get_cell(player_name, t):,}".rjust(col_width) for t in matrix.target_names]
        total_cell = f"{matrix.player_totals.get(player_name, 0):,}".rjust(col_width)
        lines.append(f"{player_name[:name_col_width]:<{name_col_width}} " + " ".join(row_cells) + " " + total_cell)
    lines.append("-" * len(header))
    footer_cells = [f"{matrix.target_totals.get(t, 0):,}".rjust(col_width) for t in matrix.target_names]
    grand_total = sum(matrix.target_totals.values())
    lines.append(f"{'Total':<{name_col_width}} " + " ".join(footer_cells) + " " + f"{grand_total:,}".rjust(col_width))
    return "\n".join(lines)


def format_player_target_matrix_markdown(matrix: PlayerTargetMatrix) -> str:
    if not matrix.player_names or not matrix.target_names:
        return "_No player-vs-target data available for this fight._"
    headers = ["Player"] + matrix.target_names + ["Total"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for player_name in matrix.player_names:
        row = [player_name] + [f"{matrix.get_cell(player_name, t):,}" for t in matrix.target_names]
        row.append(f"{matrix.player_totals.get(player_name, 0):,}")
        lines.append("| " + " | ".join(row) + " |")
    footer = ["**Total**"] + [f"**{matrix.target_totals.get(t, 0):,}**" for t in matrix.target_names]
    footer.append(f"**{sum(matrix.target_totals.values()):,}**")
    lines.append("| " + " | ".join(footer) + " |")
    return "\n".join(lines)


def format_target_summary_table(target_summaries: list[TargetSummary], top_n_players: int = 5) -> str:
    if not target_summaries:
        return "No enemy targets took damage in this fight."
    lines = ["Damage by target:\n"]
    for summary in target_summaries:
        instance_note = f" ({summary.instance_count} instance(s))" if summary.instance_count > 1 else ""
        lines.append(f"{summary.target_name}{instance_note} -- {summary.total_damage:,} total damage")
        for player_id, player_name, damage in summary.ranked_players()[:top_n_players]:
            share = (damage / summary.total_damage * 100) if summary.total_damage else 0
            lines.append(f"  {player_name:<15} {damage:>12,}  ({share:>4.1f}%)")
        lines.append("")
    return "\n".join(lines)


def format_player_breakdown_table(breakdown: PlayerTargetBreakdown) -> str:
    if not breakdown.damage_by_target_name:
        return f"{breakdown.player_name}: no damage done to any enemy target this fight."
    lines = [f"{breakdown.player_name} -- damage by target ({breakdown.total_damage:,} total):"]
    for target_name, damage in breakdown.ranked_targets():
        share = (damage / breakdown.total_damage * 100) if breakdown.total_damage else 0
        lines.append(f"  {target_name:<28.28} {damage:>12,}  ({share:>4.1f}%)")
    return "\n".join(lines)


def summarize_damage_by_target(
    parsed_fight: ParsedFight, target_summaries: list[TargetSummary], top_n_players: int = 5
) -> str:
    if not target_summaries:
        return f"{parsed_fight.fight.name}: no enemy targets took damage this fight."
    header = f"{parsed_fight.fight.name} -- damage by target:"
    return header + "\n" + format_target_summary_table(target_summaries, top_n_players=top_n_players)


def apply_target_labels(
    target_summaries: list[TargetSummary],
    labels: list,
) -> list[TargetSummary]:
    display_name_by_raw = {label.target_name: label.display_name for label in labels}
    merged: dict[str, TargetSummary] = {}
    for summary in target_summaries:
        display_name = display_name_by_raw.get(summary.target_name, summary.target_name)
        if display_name not in merged:
            merged[display_name] = TargetSummary(target_name=display_name)
        combined = merged[display_name]
        combined.target_ids |= summary.target_ids
        combined.total_damage += summary.total_damage
        for player_id, damage in summary.damage_by_player_id.items():
            combined.damage_by_player_id[player_id] = combined.damage_by_player_id.get(player_id, 0) + damage
        combined.player_names.update(summary.player_names)
        if summary.first_seen_ms is not None:
            if combined.first_seen_ms is None or summary.first_seen_ms < combined.first_seen_ms:
                combined.first_seen_ms = summary.first_seen_ms
        if summary.last_seen_ms is not None:
            if combined.last_seen_ms is None or summary.last_seen_ms > combined.last_seen_ms:
                combined.last_seen_ms = summary.last_seen_ms
    return sorted(merged.values(), key=lambda s: s.total_damage, reverse=True)


def apply_target_labels_to_breakdowns(
    breakdowns: list[PlayerTargetBreakdown],
    labels: list,
) -> list[PlayerTargetBreakdown]:
    display_name_by_raw = {label.target_name: label.display_name for label in labels}
    relabeled: list[PlayerTargetBreakdown] = []
    for breakdown in breakdowns:
        new_breakdown = PlayerTargetBreakdown(
            player_id=breakdown.player_id, player_name=breakdown.player_name,
            total_damage=breakdown.total_damage,
        )
        for target_name, damage in breakdown.damage_by_target_name.items():
            display_name = display_name_by_raw.get(target_name, target_name)
            new_breakdown.damage_by_target_name[display_name] = (
                new_breakdown.damage_by_target_name.get(display_name, 0) + damage
            )
        relabeled.append(new_breakdown)
    return relabeled


def rollup_by_category(
    target_summaries: list[TargetSummary],
    labels: list,
) -> dict[str, int]:
    category_by_raw_name = {label.target_name: (label.category or "uncategorized") for label in labels}
    totals: dict[str, int] = {}
    for summary in target_summaries:
        category = category_by_raw_name.get(summary.target_name, "uncategorized")
        totals[category] = totals.get(category, 0) + summary.total_damage
    return totals


# =======================================================================
# SECTION 5 -- CLI (explore/list/remove/remove-encounter/validate/repair)
# (originally damage_targets_cli.py -- merged in so the whole
# damage-target-labeling system, engine + CLI, lives in one file.)
# =======================================================================

def print_encounter_detail(config_obj) -> None:
    print(
        f"{config_obj.encounter_name} (encounter_id={config_obj.encounter_id}) "
        f"-- {len(config_obj.labels)} label(s):\n"
    )
    if not config_obj.labels:
        print("  (none)")
        return
    for number, label in enumerate(config_obj.labels, start=1):
        category_text = f" ({label.category})" if label.category else ""
        print(f"  {number:>2}. {label.target_name!r} -> {label.display_name}{category_text}")
        if label.notes:
            print(f"      notes: {label.notes}")


def _load_or_exit(path):
    try:
        return load_generated_target_labels(path)
    except TargetLabelConfigError as exc:
        print(f"Could not read {path}:\n\n{exc}")
        raise SystemExit(1)


def interactive_remove_labels(
    encounter_id: int,
    path,
    encounters: dict | None = None,
) -> list[str]:
    if encounters is None:
        try:
            encounters = load_generated_target_labels(path)
        except TargetLabelConfigError as exc:
            print(f"WARNING: could not read {path}:\n{exc}")
            return []
    config_obj = encounters.get(encounter_id)
    if config_obj is None or not config_obj.labels:
        print("No labels configured yet for this encounter -- nothing to remove.")
        return []
    print_encounter_detail(config_obj)
    raw = input(
        "\nWhich label number(s) to remove (examples: 2 / 1,3 / 2-4)? "
        "Press Enter to cancel: "
    )
    if not raw.strip():
        print("Cancelled -- nothing removed.")
        return []
    try:
        indexes = parse_selection(raw, len(config_obj.labels))
    except ValueError as exc:
        print(f"Invalid selection: {exc}")
        return []
    if not indexes:
        print("Cancelled -- nothing removed.")
        return []
    names_to_remove = [config_obj.labels[i].target_name for i in indexes]
    print("\nAbout to remove:")
    for name in names_to_remove:
        print(f"  - {name}")
    confirm = input(
        f"\nA backup of {path} will be saved as {path}.bak first. Proceed? [y/N]: "
    ).strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled -- nothing removed.")
        return []
    removed = remove_labels(encounter_id, names_to_remove, path, verbose=True)
    if removed:
        print(f"\nRemoved {len(removed)} label(s) from encounter {encounter_id}.")
        print(f"Backup saved as: {path}.bak")
    else:
        print("\nNothing matched -- file left untouched.")
    return removed


def cmd_list(args: argparse.Namespace) -> None:
    encounters = _load_or_exit(args.path)
    if not encounters:
        print(f"No encounters configured yet in {args.path}.")
        return
    if args.encounter_id is not None:
        config_obj = encounters.get(args.encounter_id)
        if config_obj is None:
            print(f"Encounter {args.encounter_id} is not configured in {args.path}.")
            return
        print_encounter_detail(config_obj)
        return
    print(f"{len(encounters)} encounter(s) configured in {args.path}:\n")
    for encounter_id in sorted(encounters):
        config_obj = encounters[encounter_id]
        print(f"  {encounter_id:>6}  {config_obj.encounter_name}  ({len(config_obj.labels)} label(s))")
    print("\nUse 'list <encounter_id>' to see the labels for one boss.")


def cmd_remove(args: argparse.Namespace) -> None:
    encounters = _load_or_exit(args.path)
    if not encounters.get(args.encounter_id) or not encounters[args.encounter_id].labels:
        print(f"Encounter {args.encounter_id} has no labels configured -- nothing to remove.")
        return
    interactive_remove_labels(args.encounter_id, args.path, encounters=encounters)


def cmd_remove_encounter(args: argparse.Namespace) -> None:
    encounters = _load_or_exit(args.path)
    config_obj = encounters.get(args.encounter_id)
    if config_obj is None:
        print(f"Encounter {args.encounter_id} is not configured -- nothing to remove.")
        return
    print_encounter_detail(config_obj)
    if not args.yes:
        confirm = input(
            f"\nThis deletes ALL {len(config_obj.labels)} label(s) above for "
            f"'{config_obj.encounter_name}'. A backup will be saved as {args.path}.bak "
            f"first. Proceed? [y/N]: "
        ).strip().lower()
        if confirm not in {"y", "yes"}:
            print("Cancelled -- nothing removed.")
            return
    removed = remove_encounter(args.encounter_id, args.path, verbose=True)
    if removed:
        print(f"\nRemoved encounter {args.encounter_id}. Backup saved as: {args.path}.bak")
    else:
        print("Nothing removed (encounter not found).")


def cmd_validate(args: argparse.Namespace) -> None:
    ok, message = validate_file(args.path)
    print(message)
    if not ok:
        raise SystemExit(1)


def cmd_repair(args: argparse.Namespace) -> None:
    ok, message = repair_trailing_commas(args.path, verbose=True)
    print(message)
    if not ok:
        raise SystemExit(1)


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _pause() -> None:
    input("\nPress Enter to return to the menu...")


def get_labeled_target_names(encounter_id: int, path) -> set[str]:
    try:
        existing = load_generated_target_labels(path)
    except TargetLabelConfigError as exc:
        print(
            f"\nWARNING: {path} has a problem and couldn't be read -- "
            f"skipping the 'already labeled' highlighting for this run.\n{exc}\n"
        )
        return set()
    encounter_config = existing.get(encounter_id)
    if encounter_config is None:
        return set()
    return {label.target_name for label in encounter_config.labels}


def print_target_table(target_summaries, labeled_names: set[str] = frozenset()) -> None:
    if not target_summaries:
        print("No enemy targets took damage in this fight.")
        return
    if labeled_names:
        print("(* in the L column = already labeled)")
    header = f"{'#':>3} L {'Target Name':<28} {'Instances':>9} {'TotalDmg':>14} {'Players':>8}"
    print(header)
    print("-" * len(header))
    for index, summary in enumerate(target_summaries, start=1):
        marker = "*" if summary.target_name in labeled_names else " "
        print(
            f"{index:>3} {marker} {summary.target_name:<28.28} {summary.instance_count:>9} "
            f"{summary.total_damage:>14,} {len(summary.damage_by_player_id):>8}"
        )


def fetch_and_analyze(report_code: str | None, fight_id: int | None) -> dict | None:
    """The ONLY function in this section that talks to the Warcraft Logs API."""
    if not report_code:
        report_code = input(
            "Warcraft Logs report code (the part of the URL after /reports/): "
        ).strip()
        if not report_code:
            print("No report code given -- cancelled.")
            return None
    try:
        client_id, client_secret = get_credentials()
    except RuntimeError as exc:
        print(exc)
        return None
    client = WCLClient(client_id, client_secret)
    try:
        raw_report = client.get_report_fights(report_code)
    except WCLAPIError as exc:
        print(f"Error fetching report: {exc}")
        return None
    print(f"Report: {raw_report['title']}  ({raw_report['zone']['name']})")
    try:
        resolved_fight_id = resolve_fight_id(raw_report["fights"], fight_id)
    except SystemExit:
        print("No valid fight selected -- cancelled.")
        return None
    raw_fight = next(f for f in raw_report["fights"] if f["id"] == resolved_fight_id)
    try:
        raw_master_data = client.get_report_master_data(report_code)
        raw_events_by_type = client.get_report_events_multi(
            report_code, resolved_fight_id, event_types=["DamageDone"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        return None
    parsed = parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    print(f"\nEncounter ID: {parsed.fight.encounter_id}")
    target_summaries = analyze_damage_by_target(parsed)
    return {"parsed": parsed, "target_summaries": target_summaries}


def _prompt_label_for_target(summary) -> TargetLabel:
    print(f"\n--- {summary.target_name} ---")
    print(f"  {summary.total_damage:,} total damage from {len(summary.damage_by_player_id)} player(s), "
          f"{summary.instance_count} instance(s) seen")
    for player_id, player_name, damage in summary.ranked_players()[:5]:
        print(f"    {player_name:<15} {damage:>12,}")
    display_name = input(f"  Display name for {summary.target_name!r} (e.g. 'Boss A', 'Priority Add A'): ").strip()
    if not display_name:
        display_name = summary.target_name
        print(f"  (blank -- keeping raw name {summary.target_name!r})")
    category = input(
        "  Category (optional, e.g. boss/miniboss/priority_add/add -- used for rollups): "
    ).strip() or None
    notes = input(f"  Notes for {summary.target_name!r} (optional): ").strip() or None
    return TargetLabel(
        target_name=summary.target_name, display_name=display_name, category=category, notes=notes,
    )


def prompt_for_labels(target_summaries) -> list[TargetLabel]:
    while True:
        raw = input(
            "\nChoose targets to label by number (examples: 1,3,5-7). "
            "Press Enter to select none: "
        )
        try:
            indexes = parse_selection(raw, len(target_summaries))
            break
        except ValueError as exc:
            print(f"Invalid selection: {exc}")
    return [_prompt_label_for_target(target_summaries[index]) for index in indexes]


def _do_add(parsed, target_summaries, export_path, replace: bool) -> bool:
    labels = prompt_for_labels(target_summaries)
    if not labels:
        print("\nNo labels selected -- nothing saved.")
        return False
    print()
    try:
        path = save_target_labels(
            encounter_id=parsed.fight.encounter_id,
            encounter_name=parsed.fight.name,
            labels=labels,
            path=export_path,
            replace=replace,
            verbose=True,
        )
    except TargetLabelConfigError as exc:
        print(
            f"Could not save -- {export_path} currently has a problem and wasn't "
            f"touched (nothing was overwritten):\n\n{exc}\n\n"
            f"Fix it, then try again:\n"
            f"  python damage_targets_cli.py validate\n"
            f"  python damage_targets_cli.py repair"
        )
        return False
    print(f"\nSaved to: {path}")
    return True


def _resolve_target_encounter_id(path, session: dict, prompt_verb: str) -> int | None:
    try:
        encounters = load_generated_target_labels(path)
    except TargetLabelConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return None
    if not encounters:
        print(f"No encounters configured yet in {path}.")
        print("Choose 'add' first to explore a report and label some targets.")
        return None
    if len(encounters) == 1:
        return next(iter(encounters))
    last_encounter_id = session["parsed"].fight.encounter_id if session.get("parsed") else None
    print(f"{len(encounters)} encounter(s) configured:\n")
    for encounter_id in sorted(encounters):
        config_obj = encounters[encounter_id]
        marker = " (last used this session)" if encounter_id == last_encounter_id else ""
        print(f"  {encounter_id:>6}  {config_obj.encounter_name}  ({len(config_obj.labels)} label(s)){marker}")
    default_hint = f" (Enter for {last_encounter_id})" if last_encounter_id in encounters else ""
    raw = input(f"\nWhich encounter_id do you want to {prompt_verb}?{default_hint}: ").strip()
    if not raw and last_encounter_id in encounters:
        return last_encounter_id
    if not raw:
        print("No encounter_id given -- cancelled.")
        return None
    try:
        chosen_id = int(raw)
    except ValueError:
        print(f"'{raw}' isn't a valid encounter_id.")
        return None
    if chosen_id not in encounters:
        print(f"Encounter {chosen_id} is not configured.")
        return None
    return chosen_id


def _do_remove_menu(path, session: dict) -> None:
    encounter_id = _resolve_target_encounter_id(path, session, "remove from")
    if encounter_id is None:
        return
    interactive_remove_labels(encounter_id, path)


def _do_list_menu(path, session: dict) -> None:
    try:
        encounters = load_generated_target_labels(path)
    except TargetLabelConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return
    if not encounters:
        print(f"No encounters configured yet in {path}.")
        print("Choose 'add' first to explore a report and label some targets.")
        return
    if len(encounters) == 1:
        print_encounter_detail(next(iter(encounters.values())))
        return
    print(f"{len(encounters)} encounter(s) configured in {path}:\n")
    for encounter_id in sorted(encounters):
        config_obj = encounters[encounter_id]
        print(f"  {encounter_id:>6}  {config_obj.encounter_name}  ({len(config_obj.labels)} label(s))")
    raw = input("\nShow detail for which encounter_id? (Enter to skip): ").strip()
    if not raw:
        return
    try:
        chosen_id = int(raw)
    except ValueError:
        print(f"'{raw}' isn't a valid encounter_id.")
        return
    if chosen_id not in encounters:
        print(f"Encounter {chosen_id} is not configured.")
        return
    print_encounter_detail(encounters[chosen_id])


def run_interactive_session(args: argparse.Namespace) -> None:
    session: dict = {"parsed": None, "target_summaries": None}
    replace_pending = args.replace
    while True:
        clear_screen()
        print("PullDoctor -- Damage-by-Target Labeling Explorer\n")
        print(
            "What would you like to do?\n"
            "  [a] Add labels for targets seen in a fight\n"
            "  [r] Remove labels from the tracked list\n"
            "  [l] List currently tracked labels\n"
            "  [d] Done"
        )
        choice = input("> ").strip().lower()
        if choice in {"a", "add"}:
            if session["parsed"] is None:
                result = fetch_and_analyze(args.report_code, args.fight_id)
                if result is None:
                    _pause()
                    continue
                session.update(result)
            print()
            labeled_names = get_labeled_target_names(session["parsed"].fight.encounter_id, args.export)
            print_target_table(session["target_summaries"], labeled_names)
            saved = _do_add(session["parsed"], session["target_summaries"], args.export, replace_pending)
            if saved:
                replace_pending = False
            _pause()
        elif choice in {"r", "remove"}:
            print()
            _do_remove_menu(args.export, session)
            _pause()
        elif choice in {"l", "list"}:
            print()
            _do_list_menu(args.export, session)
            _pause()
        elif choice in {"d", "done", "q", "quit"}:
            print("\nDone. damage_targets.py will load any saved changes automatically.")
            return
        else:
            print("Enter a, r, l, or d.")
            _pause()


def cmd_explore(args: argparse.Namespace) -> None:
    run_interactive_session(args)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Label enemy targets (bosses/adds/priority adds) for cleaner damage-by-target reports (merged CLI)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    explore_parser = subparsers.add_parser(
        "explore", help="Interactive menu: add labels (via a report), remove, or list."
    )
    explore_parser.add_argument(
        "report_code", nargs="?", default=None,
        help="Warcraft Logs report code -- only needed for 'add'; prompted for if omitted.",
    )
    explore_parser.add_argument(
        "fight_id", nargs="?", type=int, default=None,
        help="Optional fight ID -- only needed for 'add'; prompted for if omitted.",
    )
    explore_parser.add_argument(
        "--export", default=str(DEFAULT_CONFIG_PATH),
        help="Generated JSON config path (default: damage_targets.generated.json)",
    )
    explore_parser.add_argument(
        "--replace", action="store_true",
        help="Wipe the encounter's previously saved labels on the FIRST successful 'add' this session.",
    )
    explore_parser.set_defaults(func=cmd_explore)

    list_parser = subparsers.add_parser("list", help="List configured encounters/labels")
    list_parser.add_argument("encounter_id", nargs="?", type=int, help="Show detail for one encounter")
    list_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    list_parser.set_defaults(func=cmd_list)

    remove_parser = subparsers.add_parser("remove", help="Remove specific labels from one encounter")
    remove_parser.add_argument("encounter_id", type=int)
    remove_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    remove_parser.set_defaults(func=cmd_remove)

    remove_encounter_parser = subparsers.add_parser(
        "remove-encounter", help="Remove an entire encounter's labels"
    )
    remove_encounter_parser.add_argument("encounter_id", type=int)
    remove_encounter_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    remove_encounter_parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt"
    )
    remove_encounter_parser.set_defaults(func=cmd_remove_encounter)

    validate_parser = subparsers.add_parser("validate", help="Check the JSON file for syntax errors")
    validate_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    validate_parser.set_defaults(func=cmd_validate)

    repair_parser = subparsers.add_parser(
        "repair", help="Attempt to auto-fix a common syntax error (a trailing comma)"
    )
    repair_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    repair_parser.set_defaults(func=cmd_repair)

    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    if args.command == "explore":
        cmd_explore(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
