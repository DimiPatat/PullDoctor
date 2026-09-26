"""
boss_mechanics.py

MERGED MODULE -- combines what used to be five separate files into one,
as part of the PullDoctor file-count reduction pass:
    - avoidable_damage_data.py    (AvoidableMechanic / EncounterConfig schema)
    - boss_mechanics_config.py    (MANUAL_ENCOUNTERS + merge -> ENCOUNTERS)
    - mechanics_config_io.py      (load/save/validate/repair .generated.json)
    - avoidable_damage_analyzer.py (per-player avoidable-hit analyzer)
    - ability_explorer.py         (NPC-ability + player-sourced-ability discovery)

Config keyed by Fight.encounter_id, since avoidable mechanics belong
to a specific boss (unlike raid/defensive cooldowns, which are
tracked globally across all encounters).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from core import Event, ParsedFight, get_fight_roster, parse_fight_bundle, parse_selection, resolve_fight_id
from wcl import WCLClient, WCLAPIError, get_credentials

@dataclass
class AvoidableMechanic:
    ability_name: str
    category: str | None = None
    track_via: tuple[str, ...] = ("damage",)
    require_source_differs_from_target: bool = False
    ability_ids: tuple[int, ...] = ()
    damage_multiplier_threshold: float | None = None
    notes: str | None = None


@dataclass
class EncounterConfig:
    encounter_id: int
    encounter_name: str
    mechanics: list[AvoidableMechanic] = field(default_factory=list)


DEFAULT_CONFIG_PATH = Path(__file__).with_name("boss_mechanics.generated.json")
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


class MechanicsConfigError(Exception):
    """Raised when boss_mechanics.generated.json can't be parsed or doesn't have the expected shape."""


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
            "Likely cause: a trailing comma before a closing ] or } -- delete it, or run:\n"
            "  python boss_mechanics_cli.py repair"
        )
    elif "expecting property name" in message_lower:
        hint = (
            "Likely cause: a trailing comma before a closing } -- delete it, or run:\n"
            "  python boss_mechanics_cli.py repair"
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
        raise MechanicsConfigError(
            f"{config_path} is not valid JSON:\n\n"
            f"{_describe_json_error(text, exc)}\n\n"
            f"You can also check/fix it without touching a report:\n"
            f"  python boss_mechanics_cli.py validate\n"
            f"  python boss_mechanics_cli.py repair"
        ) from exc
    if not isinstance(raw, dict):
        raise MechanicsConfigError(f"{config_path} does not contain a JSON object at the top level.")
    return raw


def backup_file(path: str | Path) -> Path | None:
    config_path = Path(path)
    if not config_path.exists():
        return None
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    return backup_path


def load_generated_encounters(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[int, EncounterConfig]:
    """Load generated encounter configurations. Missing file returns {}."""
    config_path = Path(path)
    if not config_path.exists():
        return {}
    raw = _load_raw_or_raise(config_path)
    encounters: dict[int, EncounterConfig] = {}
    for entry in raw.get("encounters", []):
        encounter_id = int(entry["encounter_id"])
        mechanics = [
            AvoidableMechanic(
                ability_name=item["ability_name"],
                category=item.get("category"),
                track_via=tuple(item.get("track_via") or ["damage"]),
                require_source_differs_from_target=bool(
                    item.get("require_source_differs_from_target", False)
                ),
                ability_ids=tuple(item.get("ability_ids") or []),
                damage_multiplier_threshold=item.get("damage_multiplier_threshold"),
                notes=item.get("notes"),
            )
            for item in entry.get("mechanics", [])
        ]
        encounters[encounter_id] = EncounterConfig(
            encounter_id=encounter_id,
            encounter_name=entry.get("encounter_name", f"Encounter {encounter_id}"),
            mechanics=mechanics,
        )
    return encounters


def _mechanic_to_json(mechanic: AvoidableMechanic) -> dict:
    return {
        "ability_name": mechanic.ability_name,
        "category": mechanic.category,
        "track_via": list(mechanic.track_via),
        "require_source_differs_from_target": mechanic.require_source_differs_from_target,
        "ability_ids": sorted(set(mechanic.ability_ids)),
        "damage_multiplier_threshold": mechanic.damage_multiplier_threshold,
        "notes": mechanic.notes,
    }


def _write_json(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def save_encounter_selection(
    encounter_id: int,
    encounter_name: str,
    mechanics: list[AvoidableMechanic],
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
            print(f"Replacing all previously stored mechanics for encounter {encounter_id}.")
        merged_by_name: dict[str, dict] = {}
    else:
        merged_by_name = {
            item["ability_name"]: dict(item) for item in encounter_entry.get("mechanics", [])
        }
    for mechanic in mechanics:
        new_json = _mechanic_to_json(mechanic)
        prior = merged_by_name.get(mechanic.ability_name)
        if prior is None:
            if verbose:
                print(f"  + Added new mechanic: {mechanic.ability_name}")
        else:
            prior_ids = set(prior.get("ability_ids", []))
            new_ids = set(new_json["ability_ids"])
            added_ids = sorted(new_ids - prior_ids)
            new_json["ability_ids"] = sorted(prior_ids | new_ids)
            if new_json["category"] is None:
                new_json["category"] = prior.get("category")
            if new_json["notes"] is None:
                new_json["notes"] = prior.get("notes")
            if new_json["damage_multiplier_threshold"] is None:
                new_json["damage_multiplier_threshold"] = prior.get("damage_multiplier_threshold")
            if verbose:
                if added_ids:
                    print(
                        f"  ~ {mechanic.ability_name}: updated, recorded new spell ID(s) "
                        f"{added_ids} (previously known: {sorted(prior_ids)})"
                    )
                else:
                    print(f"  ~ Updated existing mechanic: {mechanic.ability_name}")
        merged_by_name[mechanic.ability_name] = new_json
    replacement_entry = {
        "encounter_id": encounter_id,
        "encounter_name": encounter_name,
        "mechanics": list(merged_by_name.values()),
    }
    if encounter_index is not None:
        encounters[encounter_index] = replacement_entry
    else:
        encounters.append(replacement_entry)
    encounters.sort(key=lambda item: int(item["encounter_id"]))
    backup_file(config_path)
    _write_json(config_path, existing)
    return config_path


def remove_mechanics(
    encounter_id: int,
    ability_names: list[str],
    path: str | Path = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
) -> list[str]:
    config_path = Path(path)
    if not config_path.exists():
        return []
    existing = _load_raw_or_raise(config_path)
    encounters = existing.get("encounters", [])
    target_names = set(ability_names)
    removed: list[str] = []
    for entry in encounters:
        if int(entry.get("encounter_id", -1)) != encounter_id:
            continue
        kept_mechanics = []
        for mechanic_json in entry.get("mechanics", []):
            if mechanic_json.get("ability_name") in target_names:
                removed.append(mechanic_json["ability_name"])
                if verbose:
                    print(f"  - Removed: {mechanic_json['ability_name']}")
            else:
                kept_mechanics.append(mechanic_json)
        entry["mechanics"] = kept_mechanics
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
        print(
            f"  - Removed encounter {encounter_id} ({removed_entry.get('encounter_name')}) "
            f"and its {len(removed_entry.get('mechanics', []))} mechanic(s)."
        )
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


MANUAL_ENCOUNTERS: dict[int, EncounterConfig] = {
}

try:
    _generated_encounters = load_generated_encounters()
except MechanicsConfigError as exc:
    print(
        "WARNING: boss_mechanics.generated.json could not be read -- "
        "ignoring it for this run.\n"
        f"{exc}\n"
    )
    _generated_encounters = {}

ENCOUNTERS: dict[int, EncounterConfig] = dict(_generated_encounters)
ENCOUNTERS.update(MANUAL_ENCOUNTERS)


RELEVANT_DATA_TYPES = ("Casts", "DamageTaken", "Debuffs")


@dataclass
class AbilitySummary:
    ability_name: str
    ability_id: int | None = None
    occurrences_by_type: dict[str, int] = field(default_factory=dict)
    target_ids_hit: set[int] = field(default_factory=set)
    total_damage: int = 0
    max_hit: int = 0
    first_seen_ms: int | None = None
    last_seen_ms: int | None = None
    caused_death: bool = False

    @property
    def total_occurrences(self) -> int:
        return sum(self.occurrences_by_type.values())

    @property
    def unique_targets_hit(self) -> int:
        return len(self.target_ids_hit)

    @property
    def damage_hit_count(self) -> int:
        return self.occurrences_by_type.get("DamageTaken", 0)

    @property
    def avg_hit(self) -> float:
        if self.damage_hit_count == 0:
            return 0.0
        return self.total_damage / self.damage_hit_count

    def first_seen_pct(self, fight_duration_ms: int) -> float:
        if self.first_seen_ms is None or fight_duration_ms <= 0:
            return 0.0
        return (self.first_seen_ms / fight_duration_ms) * 100


def _is_npc_source(source_id: int | None, actors) -> bool:
    if source_id is None:
        return False
    actor = actors.get(source_id)
    return actor is not None and actor.type == "NPC"


def _is_player_source(source_id: int | None, actors) -> bool:
    if source_id is None:
        return False
    actor = actors.get(source_id)
    return actor is not None and actor.type == "Player"


def _is_player_target(target_id: int | None, actors) -> bool:
    if target_id is None:
        return False
    actor = actors.get(target_id)
    return actor is not None and actor.type == "Player"


def analyze_boss_abilities(parsed_fight: ParsedFight) -> list[AbilitySummary]:
    death_ability_names = {
        e.ability_name for e in parsed_fight.events if e.data_type == "Deaths" and e.ability_name
    }
    summaries: dict[str, AbilitySummary] = {}
    for event in parsed_fight.events:
        if event.data_type not in RELEVANT_DATA_TYPES:
            continue
        if not event.ability_name:
            continue
        if not _is_npc_source(event.source_id, parsed_fight.actors):
            continue

        name = event.ability_name
        if name not in summaries:
            summaries[name] = AbilitySummary(ability_name=name, ability_id=event.ability_id)
        summary = summaries[name]
        summary.occurrences_by_type[event.data_type] = summary.occurrences_by_type.get(event.data_type, 0) + 1

        if event.data_type == "DamageTaken":
            amount = event.amount or 0
            summary.total_damage += amount
            summary.max_hit = max(summary.max_hit, amount)

        if event.data_type in ("DamageTaken", "Debuffs") and event.target_id is not None:
            summary.target_ids_hit.add(event.target_id)

        if summary.first_seen_ms is None or event.timestamp < summary.first_seen_ms:
            summary.first_seen_ms = event.timestamp
        if summary.last_seen_ms is None or event.timestamp > summary.last_seen_ms:
            summary.last_seen_ms = event.timestamp

        if name in death_ability_names:
            summary.caused_death = True

    return sorted(summaries.values(), key=lambda s: s.total_occurrences, reverse=True)


@dataclass
class PlayerSourcedAbilitySummary:
    ability_name: str
    ability_id: int | None = None
    occurrences_by_type: dict[str, int] = field(default_factory=dict)
    self_hit_count: int = 0
    other_hit_count: int = 0
    self_damage: int = 0
    other_damage: int = 0
    other_target_ids: set[int] = field(default_factory=set)
    max_hit: int = 0
    first_seen_ms: int | None = None
    last_seen_ms: int | None = None
    caused_death: bool = False

    @property
    def total_occurrences(self) -> int:
        return self.self_hit_count + self.other_hit_count

    @property
    def unique_other_targets_hit(self) -> int:
        return len(self.other_target_ids)

    def first_seen_pct(self, fight_duration_ms: int) -> float:
        if self.first_seen_ms is None or fight_duration_ms <= 0:
            return 0.0
        return (self.first_seen_ms / fight_duration_ms) * 100


def analyze_player_sourced_abilities(
    parsed_fight: ParsedFight, min_other_hits: int = 0
) -> list[PlayerSourcedAbilitySummary]:
    death_ability_names = {
        e.ability_name for e in parsed_fight.events if e.data_type == "Deaths" and e.ability_name
    }

    summaries: dict[str, PlayerSourcedAbilitySummary] = {}
    for event in parsed_fight.events:
        if event.data_type not in ("DamageTaken", "Debuffs"):
            continue
        if not event.ability_name:
            continue
        if not _is_player_source(event.source_id, parsed_fight.actors):
            continue
        if not _is_player_target(event.target_id, parsed_fight.actors):
            continue
        if event.data_type == "Debuffs" and event.event_type != "applydebuff":
            continue

        name = event.ability_name
        if name not in summaries:
            summaries[name] = PlayerSourcedAbilitySummary(ability_name=name, ability_id=event.ability_id)
        summary = summaries[name]
        summary.occurrences_by_type[event.data_type] = summary.occurrences_by_type.get(event.data_type, 0) + 1

        is_self_hit = event.source_id == event.target_id
        amount = event.amount or 0 if event.data_type == "DamageTaken" else 0

        if is_self_hit:
            summary.self_hit_count += 1
            summary.self_damage += amount
        else:
            summary.other_hit_count += 1
            summary.other_damage += amount
            if event.target_id is not None:
                summary.other_target_ids.add(event.target_id)

        if event.data_type == "DamageTaken":
            summary.max_hit = max(summary.max_hit, amount)

        if summary.first_seen_ms is None or event.timestamp < summary.first_seen_ms:
            summary.first_seen_ms = event.timestamp
        if summary.last_seen_ms is None or event.timestamp > summary.last_seen_ms:
            summary.last_seen_ms = event.timestamp

        if name in death_ability_names:
            summary.caused_death = True

    results = sorted(summaries.values(), key=lambda s: s.total_occurrences, reverse=True)
    if min_other_hits > 0:
        results = [s for s in results if s.other_hit_count >= min_other_hits]
    return results


@dataclass
class PlayerAvoidableDamage:
    player_id: int
    player_name: str
    hits_by_mechanic: dict[str, int] = field(default_factory=dict)
    damage_by_mechanic: dict[str, int] = field(default_factory=dict)

    @property
    def total_avoidable_hits(self) -> int:
        return sum(self.hits_by_mechanic.values())

    @property
    def total_avoidable_damage(self) -> int:
        return sum(self.damage_by_mechanic.values())

    @property
    def distinct_mechanics_hit(self) -> int:
        return sum(1 for count in self.hits_by_mechanic.values() if count > 0)


def get_encounter_config(encounter_id: int, configs: dict[int, EncounterConfig]) -> EncounterConfig | None:
    return configs.get(encounter_id)


def _should_count(event: Event, mechanic: AvoidableMechanic) -> bool:
    if mechanic.require_source_differs_from_target and event.source_id == event.target_id:
        return False
    return True


def _candidate_damage_events_by_ability(
    parsed_fight: ParsedFight, damage_tracked: dict[str, AvoidableMechanic]
) -> dict[str, list[Event]]:
    events_by_ability: dict[str, list[Event]] = {}
    for event in parsed_fight.events:
        if event.data_type != "DamageTaken" or event.ability_name not in damage_tracked:
            continue
        mechanic = damage_tracked[event.ability_name]
        if not _should_count(event, mechanic):
            continue
        events_by_ability.setdefault(event.ability_name, []).append(event)
    return events_by_ability


def compute_mechanic_baselines(parsed_fight: ParsedFight, config: EncounterConfig) -> dict[str, float]:
    damage_tracked = {m.ability_name: m for m in config.mechanics if "damage" in m.track_via}
    events_by_ability = _candidate_damage_events_by_ability(parsed_fight, damage_tracked)
    return {
        name: statistics.median(e.amount or 0 for e in events)
        for name, events in events_by_ability.items()
    }


def analyze_avoidable_damage(
    parsed_fight: ParsedFight,
    configs: dict[int, EncounterConfig],
) -> list[PlayerAvoidableDamage]:
    config = get_encounter_config(parsed_fight.fight.encounter_id, configs)
    if config is None or not config.mechanics:
        return []

    fight_roster = get_fight_roster(parsed_fight)
    reports: dict[int, PlayerAvoidableDamage] = {
        player_id: PlayerAvoidableDamage(player_id=player_id, player_name=actor.name)
        for player_id, actor in fight_roster.items()
    }

    damage_tracked = {m.ability_name: m for m in config.mechanics if "damage" in m.track_via}
    debuff_tracked = {m.ability_name: m for m in config.mechanics if "debuff" in m.track_via}

    baselines = {
        name: statistics.median(e.amount or 0 for e in events)
        for name, events in _candidate_damage_events_by_ability(parsed_fight, damage_tracked).items()
    }

    for event in parsed_fight.events:
        if event.ability_name is None:
            continue
        if event.data_type == "DamageTaken" and event.ability_name in damage_tracked:
            mechanic = damage_tracked[event.ability_name]
            if not _should_count(event, mechanic):
                continue

            if mechanic.damage_multiplier_threshold is not None:
                median = baselines.get(event.ability_name, 0.0)
                amount = event.amount or 0
                if median <= 0 or amount < median * mechanic.damage_multiplier_threshold:
                    continue

            report = reports.get(event.target_id)
            if report is None:
                continue
            name = event.ability_name
            report.hits_by_mechanic[name] = report.hits_by_mechanic.get(name, 0) + 1
            report.damage_by_mechanic[name] = (
                report.damage_by_mechanic.get(name, 0) + (event.amount or 0)
            )
        elif (
            event.data_type == "Debuffs"
            and event.event_type == "applydebuff"
            and event.ability_name in debuff_tracked
        ):
            mechanic = debuff_tracked[event.ability_name]
            if not _should_count(event, mechanic):
                continue
            report = reports.get(event.target_id)
            if report is None:
                continue
            name = event.ability_name
            report.hits_by_mechanic[name] = report.hits_by_mechanic.get(name, 0) + 1
    return sorted(reports.values(), key=lambda r: r.total_avoidable_hits, reverse=True)


def summarize_avoidable_damage(
    parsed_fight: ParsedFight,
    reports: list[PlayerAvoidableDamage],
    config: EncounterConfig | None,
) -> str:
    if config is None:
        return (
            f"{parsed_fight.fight.name}: no avoidable-damage config for this "
            f"encounter yet (encounter_id={parsed_fight.fight.encounter_id})."
        )
    if not reports or all(r.total_avoidable_hits == 0 for r in reports):
        return f"{parsed_fight.fight.name}: no avoidable hits taken. Clean pull!"
    lines = [f"{parsed_fight.fight.name} -- avoidable damage:"]
    for report in reports:
        if report.total_avoidable_hits == 0:
            continue
        breakdown = ", ".join(
            f"{name} x{count}" for name, count in report.hits_by_mechanic.items() if count > 0
        )
        lines.append(
            f"  {(report.player_name or 'Unknown')[:15]:<15} {report.total_avoidable_hits} hit(s)  ({breakdown})"
        )

    thresholded = [m for m in config.mechanics if m.damage_multiplier_threshold is not None]
    if thresholded:
        baselines = compute_mechanic_baselines(parsed_fight, config)
        lines.append("")
        for mechanic in thresholded:
            median = baselines.get(mechanic.ability_name)
            if median is None:
                lines.append(
                    f"  ({mechanic.ability_name}: flagged at >= {mechanic.damage_multiplier_threshold}x "
                    f"median hit -- no hits recorded this pull to compute a median from)"
                )
            else:
                lines.append(
                    f"  ({mechanic.ability_name}: flagged at >= {mechanic.damage_multiplier_threshold}x "
                    f"median hit, median={median:,.0f})"
                )
    return "\n".join(lines)


# =======================================================================
# SECTION 6 -- CLI (explore/list/remove/remove-encounter/validate/repair)
# (originally boss_mechanics_cli.py -- merged in so the whole boss-
# mechanics system, engine + CLI, lives in one file.)
# =======================================================================

def print_encounter_detail(config_obj) -> None:
    print(
        f"{config_obj.encounter_name} (encounter_id={config_obj.encounter_id}) "
        f"-- {len(config_obj.mechanics)} mechanic(s):\n"
    )
    if not config_obj.mechanics:
        print("  (none)")
        return
    for number, mechanic in enumerate(config_obj.mechanics, start=1):
        ids_text = f" ids={list(mechanic.ability_ids)}" if mechanic.ability_ids else ""
        source_flag = " [source!=target]" if mechanic.require_source_differs_from_target else ""
        category_text = f" ({mechanic.category})" if mechanic.category else ""
        threshold_text = (
            f" [flag >= {mechanic.damage_multiplier_threshold}x median]"
            if mechanic.damage_multiplier_threshold is not None
            else ""
        )
        print(
            f"  {number:>2}. {mechanic.ability_name}{category_text} "
            f"[{'+'.join(mechanic.track_via)}]{source_flag}{threshold_text}{ids_text}"
        )
        if mechanic.notes:
            print(f"      notes: {mechanic.notes}")


def _load_or_exit(path):
    try:
        return load_generated_encounters(path)
    except MechanicsConfigError as exc:
        print(f"Could not read {path}:\n\n{exc}")
        raise SystemExit(1)


def interactive_remove_mechanics(
    encounter_id: int,
    path,
    encounters: dict | None = None,
) -> list[str]:
    if encounters is None:
        try:
            encounters = load_generated_encounters(path)
        except MechanicsConfigError as exc:
            print(f"WARNING: could not read {path}:\n{exc}")
            return []
    config_obj = encounters.get(encounter_id)
    if config_obj is None or not config_obj.mechanics:
        print("No mechanics configured yet for this encounter -- nothing to remove.")
        return []
    print_encounter_detail(config_obj)
    raw = input(
        "\nWhich mechanic number(s) to remove (examples: 2 / 1,3 / 2-4)? "
        "Press Enter to cancel: "
    )
    if not raw.strip():
        print("Cancelled -- nothing removed.")
        return []
    try:
        indexes = parse_selection(raw, len(config_obj.mechanics))
    except ValueError as exc:
        print(f"Invalid selection: {exc}")
        return []
    if not indexes:
        print("Cancelled -- nothing removed.")
        return []
    names_to_remove = [config_obj.mechanics[i].ability_name for i in indexes]
    print("\nAbout to remove:")
    for name in names_to_remove:
        print(f"  - {name}")
    confirm = input(
        f"\nA backup of {path} will be saved as {path}.bak first. Proceed? [y/N]: "
    ).strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled -- nothing removed.")
        return []
    removed = remove_mechanics(encounter_id, names_to_remove, path, verbose=True)
    if removed:
        print(f"\nRemoved {len(removed)} mechanic(s) from encounter {encounter_id}.")
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
        print(
            f"  {encounter_id:>6}  {config_obj.encounter_name}  "
            f"({len(config_obj.mechanics)} mechanic(s))"
        )
    print("\nUse 'list <encounter_id>' to see the mechanics for one boss.")


def cmd_remove(args: argparse.Namespace) -> None:
    encounters = _load_or_exit(args.path)
    if not encounters.get(args.encounter_id) or not encounters[args.encounter_id].mechanics:
        print(f"Encounter {args.encounter_id} has no mechanics configured -- nothing to remove.")
        return
    interactive_remove_mechanics(args.encounter_id, args.path, encounters=encounters)


def cmd_remove_encounter(args: argparse.Namespace) -> None:
    encounters = _load_or_exit(args.path)
    config_obj = encounters.get(args.encounter_id)
    if config_obj is None:
        print(f"Encounter {args.encounter_id} is not configured -- nothing to remove.")
        return
    print_encounter_detail(config_obj)
    if not args.yes:
        confirm = input(
            f"\nThis deletes ALL {len(config_obj.mechanics)} mechanic(s) above for "
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


def _relative_ms(parsed, timestamp_ms: int | None) -> int:
    return max(0, (timestamp_ms if timestamp_ms is not None else parsed.fight.start_time) - parsed.fight.start_time)


def get_tracked_ability_names(encounter_id: int, path) -> set[str]:
    try:
        existing = load_generated_encounters(path)
    except MechanicsConfigError as exc:
        print(
            f"\nWARNING: {path} has a problem and couldn't be read -- "
            f"skipping the 'already tracked' highlighting for this run.\n{exc}\n"
        )
        return set()
    encounter_config = existing.get(encounter_id)
    if encounter_config is None:
        return set()
    return {mechanic.ability_name for mechanic in encounter_config.mechanics}


def print_npc_table(parsed, summaries, start_number: int = 1, tracked_names: set[str] = frozenset()) -> None:
    print(f"{parsed.fight.name} -- boss/NPC ability summary:")
    if tracked_names:
        print("(* in the T column = already tracked as an avoidable mechanic for this encounter)")
    header = (
        f"{'#':>3} T {'Ability':<28} {'ID':>8} {'Types':<20} {'Count':>6} "
        f"{'Targets':>8} {'TotalDmg':>12} {'AvgDmg':>10} {'MaxDmg':>10} "
        f"{'First':>12} {'Death':>6}"
    )
    print(header)
    print("-" * len(header))
    duration_ms = parsed.fight.duration_ms
    for offset, summary in enumerate(summaries):
        number = start_number + offset
        is_tracked = summary.ability_name in tracked_names
        types_text = "/".join(sorted(summary.occurrences_by_type))
        relative_ms = _relative_ms(parsed, summary.first_seen_ms)
        first_seconds = relative_ms / 1000
        first_percent = (relative_ms / duration_ms * 100) if duration_ms > 0 else 0
        print(
            f"{number:>3} {'*' if is_tracked else ' '} {summary.ability_name:<28.28} "
            f"{summary.ability_id or 0:>8} "
            f"{types_text:<20.20} {summary.total_occurrences:>6} "
            f"{summary.unique_targets_hit:>8} {summary.total_damage:>12,} "
            f"{summary.avg_hit:>10,.0f} {summary.max_hit:>10,} "
            f"{first_seconds:>6.1f}s {first_percent:>4.0f}% "
            f"{'YES' if summary.caused_death else '':>6}"
        )


def print_player_sourced_table(parsed, summaries, start_number: int, tracked_names: set[str] = frozenset()) -> None:
    if not summaries:
        return
    print(
        "\nPlayer-sourced damage/debuffs (possible carried/soak mechanics):\n"
        "SelfHits = the carrier's own expected hit (usually NOT a mistake).\n"
        "OtherHits = a DIFFERENT player caught by it -- usually the real mistake."
    )
    if tracked_names:
        print("(* in the T column = already tracked as an avoidable mechanic for this encounter)")
    header = (
        f"{'#':>3} T {'Ability':<28} {'ID':>8} {'SelfHits':>9} {'OtherHits':>10} "
        f"{'OtherTgts':>10} {'MaxHit':>10} {'Death':>6}"
    )
    print(header)
    print("-" * len(header))
    for offset, summary in enumerate(summaries):
        number = start_number + offset
        is_tracked = summary.ability_name in tracked_names
        print(
            f"{number:>3} {'*' if is_tracked else ' '} {summary.ability_name:<28.28} "
            f"{summary.ability_id or 0:>8} "
            f"{summary.self_hit_count:>9} {summary.other_hit_count:>10} "
            f"{summary.unique_other_targets_hit:>10} {summary.max_hit:>10,} "
            f"{'YES' if summary.caused_death else '':>6}"
        )


def print_ability_tables(parsed, npc_summaries, player_summaries, hidden_self_only_count: int, export_path) -> None:
    tracked_names = get_tracked_ability_names(parsed.fight.encounter_id, export_path)
    if npc_summaries:
        print_npc_table(parsed, npc_summaries, start_number=1, tracked_names=tracked_names)
    else:
        print(f"{parsed.fight.name}: no NPC-sourced abilities found.")
    print_player_sourced_table(
        parsed, player_summaries, start_number=len(npc_summaries) + 1, tracked_names=tracked_names
    )
    if hidden_self_only_count > 0:
        plural = "y" if hidden_self_only_count == 1 else "ies"
        print(
            f"\n({hidden_self_only_count} player-sourced abilit{plural} hidden -- never hit "
            f"anyone but their own caster. Use --show-self-only to see "
            f"{'it' if hidden_self_only_count == 1 else 'them'}.)"
        )


def fetch_and_analyze(report_code: str | None, fight_id: int | None, show_self_only: bool) -> dict | None:
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
            report_code, resolved_fight_id,
            event_types=["Casts", "DamageTaken", "Debuffs", "Deaths"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        return None
    parsed = parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    print(f"\nEncounter ID: {parsed.fight.encounter_id}")
    npc_summaries = analyze_boss_abilities(parsed)
    all_player_summaries = analyze_player_sourced_abilities(parsed)
    if show_self_only:
        player_summaries = all_player_summaries
    else:
        player_summaries = analyze_player_sourced_abilities(parsed, min_other_hits=1)
    hidden_self_only_count = len(all_player_summaries) - len(player_summaries)
    return {
        "parsed": parsed,
        "npc_summaries": npc_summaries,
        "player_summaries": player_summaries,
        "hidden_self_only_count": hidden_self_only_count,
    }


def default_track_via(summary) -> tuple[str, ...]:
    has_damage = "DamageTaken" in summary.occurrences_by_type
    has_debuff = "Debuffs" in summary.occurrences_by_type
    if has_damage:
        return ("damage",)
    if has_debuff:
        return ("debuff",)
    return ("damage",)


def prompt_track_via(summary) -> tuple[str, ...]:
    default = default_track_via(summary)
    default_label = "+".join(default)
    while True:
        answer = input(
            f"  Track {summary.ability_name!r} via "
            f"[d]amage, de[b]uff, [a]ll (default {default_label}): "
        ).strip().lower()
        if not answer:
            return default
        if answer in {"d", "damage"}:
            return ("damage",)
        if answer in {"b", "debuff"}:
            return ("debuff",)
        if answer in {"a", "all", "both"}:
            return ("damage", "debuff")
        print("  Enter d, b, a, or press Enter for the default.")


def prompt_require_source_differs_from_target(summary, is_player_sourced: bool) -> bool:
    default = is_player_sourced
    default_label = "Y" if default else "N"
    if is_player_sourced:
        print(
            "  This ability was confirmed PLAYER-sourced (see the second table above) --\n"
            "  a real candidate for excluding the carrier's own expected hit."
        )
    else:
        print(
            "  This ability is BOSS-sourced -- this flag would have no effect here.\n"
            "  Answering No is almost always correct."
        )
    while True:
        answer = input(
            f"  Only count hits on players OTHER than the one carrying/causing it? "
            f"[y/n] (default {default_label}): "
        ).strip().lower()
        if not answer:
            return default
        if answer in {"n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("  Enter y or n, or press Enter for the default.")


def prompt_damage_multiplier_threshold(summary, track_via: tuple[str, ...]) -> float | None:
    if "damage" not in track_via:
        return None
    print(
        "  Optional: for SHARED/SPLIT damage mechanics (damage divides among\n"
        "  however many players are standing in range), you can flag hits that\n"
        "  are unusually large for that pull -- compares each hit to the MEDIAN\n"
        "  hit size for this ability in that specific pull."
    )
    while True:
        raw = input(
            f"  Flag hits >= how many times the median for {summary.ability_name!r}? "
            f"(e.g. 2 for 2x; press Enter to skip): "
        ).strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            print("  Enter a number (e.g. 2 or 1.5), or press Enter to skip.")
            continue
        if value <= 0:
            print("  Enter a number greater than 0.")
            continue
        return value


def prompt_for_mechanics(npc_summaries, player_summaries) -> list[AvoidableMechanic]:
    combined = list(npc_summaries) + list(player_summaries)
    player_sourced_start_index = len(npc_summaries)
    while True:
        raw = input(
            "\nChoose avoidable abilities by number, from EITHER table above "
            "(examples: 3,8,12-15). Press Enter to select none: "
        )
        try:
            indexes = parse_selection(raw, len(combined))
            break
        except ValueError as exc:
            print(f"Invalid selection: {exc}")
    mechanics: list[AvoidableMechanic] = []
    for index in indexes:
        summary = combined[index]
        is_player_sourced = index >= player_sourced_start_index
        print(f"\n--- {summary.ability_name} {'(player-sourced)' if is_player_sourced else '(boss-sourced)'} ---")
        track_via = prompt_track_via(summary)
        require_source_differs = prompt_require_source_differs_from_target(summary, is_player_sourced)
        damage_multiplier_threshold = prompt_damage_multiplier_threshold(summary, track_via)
        category = input(
            f"  Category for {summary.ability_name!r} "
            "(optional, e.g. ground_effect/cone/spread/split_damage): "
        ).strip() or None
        notes = input(f"  Notes for {summary.ability_name!r} (optional): ").strip() or None
        mechanics.append(
            AvoidableMechanic(
                ability_name=summary.ability_name,
                category=category,
                track_via=track_via,
                require_source_differs_from_target=require_source_differs,
                ability_ids=(summary.ability_id,) if summary.ability_id else (),
                damage_multiplier_threshold=damage_multiplier_threshold,
                notes=notes,
            )
        )
    return mechanics


def _do_add(parsed, npc_summaries, player_summaries, export_path, replace: bool) -> bool:
    mechanics = prompt_for_mechanics(npc_summaries, player_summaries)
    if not mechanics:
        print("\nNo mechanics selected -- nothing saved.")
        return False
    print()
    try:
        path = save_encounter_selection(
            encounter_id=parsed.fight.encounter_id,
            encounter_name=parsed.fight.name,
            mechanics=mechanics,
            path=export_path,
            replace=replace,
            verbose=True,
        )
    except MechanicsConfigError as exc:
        print(
            f"Could not save -- {export_path} currently has a problem and wasn't "
            f"touched (nothing was overwritten):\n\n{exc}\n\n"
            f"Fix it, then try again:\n"
            f"  python boss_mechanics_cli.py validate\n"
            f"  python boss_mechanics_cli.py repair"
        )
        return False
    print(f"\nSaved to: {path}")
    return True


def _resolve_target_encounter_id(path, session: dict, prompt_verb: str) -> int | None:
    try:
        encounters = load_generated_encounters(path)
    except MechanicsConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return None
    if not encounters:
        print(f"No encounters configured yet in {path}.")
        print("Choose 'add' first to explore a report and select some mechanics.")
        return None
    if len(encounters) == 1:
        return next(iter(encounters))
    last_encounter_id = session["parsed"].fight.encounter_id if session.get("parsed") else None
    print(f"{len(encounters)} encounter(s) configured:\n")
    for encounter_id in sorted(encounters):
        config_obj = encounters[encounter_id]
        marker = " (last used this session)" if encounter_id == last_encounter_id else ""
        print(
            f"  {encounter_id:>6}  {config_obj.encounter_name}  "
            f"({len(config_obj.mechanics)} mechanic(s)){marker}"
        )
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
    interactive_remove_mechanics(encounter_id, path)


def _do_list_menu(path, session: dict) -> None:
    try:
        encounters = load_generated_encounters(path)
    except MechanicsConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return
    if not encounters:
        print(f"No encounters configured yet in {path}.")
        print("Choose 'add' first to explore a report and select some mechanics.")
        return
    if len(encounters) == 1:
        print_encounter_detail(next(iter(encounters.values())))
        return
    print(f"{len(encounters)} encounter(s) configured in {path}:\n")
    for encounter_id in sorted(encounters):
        config_obj = encounters[encounter_id]
        print(
            f"  {encounter_id:>6}  {config_obj.encounter_name}  "
            f"({len(config_obj.mechanics)} mechanic(s))"
        )
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
    session: dict = {
        "parsed": None,
        "npc_summaries": None,
        "player_summaries": None,
        "hidden_self_only_count": None,
    }
    replace_pending = args.replace
    while True:
        clear_screen()
        print("PullDoctor -- Boss Mechanics Explorer\n")
        print(
            "What would you like to do?\n"
            "  [a] Add abilities to the tracked list\n"
            "  [r] Remove abilities from the tracked list\n"
            "  [l] List currently tracked abilities\n"
            "  [d] Done"
        )
        choice = input("> ").strip().lower()
        if choice in {"a", "add"}:
            if session["parsed"] is None:
                result = fetch_and_analyze(args.report_code, args.fight_id, args.show_self_only)
                if result is None:
                    _pause()
                    continue
                session.update(result)
            print()
            print_ability_tables(
                session["parsed"], session["npc_summaries"], session["player_summaries"],
                session["hidden_self_only_count"], args.export,
            )
            saved = _do_add(
                session["parsed"], session["npc_summaries"], session["player_summaries"],
                args.export, replace_pending,
            )
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
            print("\nDone. boss_mechanics.py will load any saved changes automatically.")
            return
        else:
            print("Enter a, r, l, or d.")
            _pause()


def cmd_explore(args: argparse.Namespace) -> None:
    run_interactive_session(args)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explore and manage avoidable boss mechanics (merged CLI)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    explore_parser = subparsers.add_parser(
        "explore", help="Interactive menu: add (via a report), remove, or list."
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
        help="Generated JSON config path (default: boss_mechanics.generated.json)",
    )
    explore_parser.add_argument(
        "--replace", action="store_true",
        help="Wipe the encounter's previously saved mechanics on the FIRST successful 'add' this session.",
    )
    explore_parser.add_argument(
        "--show-self-only", action="store_true",
        help="Also show player-sourced abilities that never hit anyone but their own caster.",
    )
    explore_parser.set_defaults(func=cmd_explore)

    list_parser = subparsers.add_parser("list", help="List configured encounters/mechanics")
    list_parser.add_argument("encounter_id", nargs="?", type=int, help="Show detail for one encounter")
    list_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    list_parser.set_defaults(func=cmd_list)

    remove_parser = subparsers.add_parser("remove", help="Remove specific mechanics from one encounter")
    remove_parser.add_argument("encounter_id", type=int)
    remove_parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH))
    remove_parser.set_defaults(func=cmd_remove)

    remove_encounter_parser = subparsers.add_parser(
        "remove-encounter", help="Remove an entire encounter's configuration"
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
