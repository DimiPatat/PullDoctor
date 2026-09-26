"""
raid_cooldowns.py

MERGED MODULE -- combines what used to be five separate files into one,
as part of the PullDoctor file-count reduction pass:
    - cooldown_analyzer.py        (shared generic engine -- see NOTE below)
    - raid_cooldown_schema.py     (RaidCooldownDefinition dataclass)
    - raid_cooldown_config_io.py  (load/save/validate/repair .generated.json)
    - raid_cooldowns_config.py    (MANUAL_COOLDOWNS + merge -> TRACKED_COOLDOWNS)
    - raid_cooldown_explorer.py   (heuristic discovery of raid-CD candidates)

NOTE on cooldown_analyzer.py: this generic usage/efficiency engine
(CooldownDefinition / CooldownUsage / analyze_cooldown_usage /
summarize_cooldown_usage) is ALSO used by defensive_cooldowns.py -- it
is intentionally duplicated into BOTH merged files rather than kept as
a 13th standalone shared module, per an explicit request to minimize
file count above all else.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from core import (
    FightFilterCriteria,
    filter_fights,
    format_filter_summary,
    format_timestamp,
    parse_fight_bundle,
    resolve_fight_id,
    resolve_to_player,
)
from wcl import WCLClient, WCLAPIError, get_credentials

@dataclass
class CooldownDefinition:
    ability_name: str
    cooldown_seconds: float
    category: str | None = None


@dataclass
class CooldownUsage:
    player_id: int | None
    player_name: str | None
    ability_name: str
    cooldown_seconds: float
    category: str | None
    cast_timestamps: list[int] = field(default_factory=list)

    @property
    def num_casts(self) -> int:
        return len(self.cast_timestamps)

    def theoretical_max_casts(self, fight_duration_ms: int) -> int:
        if self.cooldown_seconds <= 0 or fight_duration_ms <= 0:
            return 0
        return int((fight_duration_ms / 1000) // self.cooldown_seconds) + 1

    def efficiency(self, fight_duration_ms: int) -> float:
        max_casts = self.theoretical_max_casts(fight_duration_ms)
        return min(self.num_casts / max_casts, 1.0) if max_casts else 0.0


def analyze_cooldown_usage(parsed_fight, cooldown_definitions: list[CooldownDefinition]) -> list[CooldownUsage]:
    definitions_by_name = {d.ability_name: d for d in cooldown_definitions}
    usages: dict[tuple[int, str], CooldownUsage] = {}
    for event in parsed_fight.events:
        if event.data_type != "Casts" or event.ability_name not in definitions_by_name:
            continue
        player = resolve_to_player(event.source_id, parsed_fight.actors)
        if player is None:
            continue
        definition = definitions_by_name[event.ability_name]
        key = (player.id, definition.ability_name)
        if key not in usages:
            usages[key] = CooldownUsage(player_id=player.id, player_name=player.name, ability_name=definition.ability_name, cooldown_seconds=definition.cooldown_seconds, category=definition.category)
        usages[key].cast_timestamps.append(event.timestamp)
    for usage in usages.values():
        usage.cast_timestamps.sort()
    return sorted(usages.values(), key=lambda u: (u.player_name or "", u.ability_name))


def summarize_cooldown_usage(parsed_fight, usages: list[CooldownUsage]) -> str:
    if not usages:
        return f"{parsed_fight.fight.name}: no tracked cooldown usage recorded."
    duration_ms = parsed_fight.fight.duration_ms
    lines = [f"{parsed_fight.fight.name} -- cooldown usage ({format_timestamp(duration_ms)}):"]
    for usage in usages:
        max_casts = usage.theoretical_max_casts(duration_ms)
        lines.append(f"  {(usage.player_name or 'Unknown')[:15]:<15} {usage.ability_name[:25]:<25} {usage.num_casts}/{max_casts} casts  ({usage.efficiency(duration_ms) * 100:>5.1f}% efficiency)")
    return "\n".join(lines)


@dataclass
class RaidCooldownDefinition:
    ability_name: str
    cooldown_seconds: float
    category: str | None = None
    class_name: str | None = None
    notes: str | None = None


def as_cooldown_definitions(definitions: list["RaidCooldownDefinition"]) -> list[CooldownDefinition]:
    return [
        CooldownDefinition(ability_name=d.ability_name, cooldown_seconds=d.cooldown_seconds, category=d.category)
        for d in definitions
    ]


DEFAULT_CONFIG_PATH = Path(__file__).with_name("raid_cooldowns.generated.json")
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")


class RaidCooldownConfigError(Exception):
    """Raised when raid_cooldowns.generated.json can't be parsed or doesn't have the expected shape."""


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
        hint = "Likely cause: a trailing comma before a closing ] or } -- delete it, or run:\n  python raid_cooldowns_cli.py repair"
    elif "expecting property name" in message_lower:
        hint = "Likely cause: a trailing comma before a closing } -- delete it, or run:\n  python raid_cooldowns_cli.py repair"
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
        raise RaidCooldownConfigError(
            f"{config_path} is not valid JSON:\n\n"
            f"{_describe_json_error(text, exc)}\n\n"
            f"You can also check/fix it without touching a report:\n"
            f"  python raid_cooldowns_cli.py validate\n"
            f"  python raid_cooldowns_cli.py repair"
        ) from exc
    if not isinstance(raw, dict):
        raise RaidCooldownConfigError(f"{config_path} does not contain a JSON object at the top level.")
    return raw


def backup_file(path: str | Path) -> Path | None:
    config_path = Path(path)
    if not config_path.exists():
        return None
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup_path)
    return backup_path


def load_generated_raid_cooldowns(path: str | Path = DEFAULT_CONFIG_PATH) -> list[RaidCooldownDefinition]:
    config_path = Path(path)
    if not config_path.exists():
        return []
    raw = _load_raw_or_raise(config_path)
    return [
        RaidCooldownDefinition(
            ability_name=item["ability_name"], cooldown_seconds=float(item["cooldown_seconds"]),
            category=item.get("category"), class_name=item.get("class_name"), notes=item.get("notes"),
        )
        for item in raw.get("cooldowns", [])
    ]


def _cooldown_to_json(cd: RaidCooldownDefinition) -> dict:
    return {
        "ability_name": cd.ability_name, "cooldown_seconds": cd.cooldown_seconds,
        "category": cd.category, "class_name": cd.class_name, "notes": cd.notes,
    }


def _write_json(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _read_existing(config_path: Path) -> dict:
    if config_path.exists():
        existing = _load_raw_or_raise(config_path)
        existing.setdefault("cooldowns", [])
        return existing
    return {"version": 1, "cooldowns": []}


def save_raid_cooldowns(
    cooldown_updates: list[RaidCooldownDefinition] | None = None,
    path: str | Path = DEFAULT_CONFIG_PATH,
    verbose: bool = False,
) -> Path:
    cooldown_updates = cooldown_updates or []
    config_path = Path(path)
    existing = _read_existing(config_path)
    by_name: dict[str, dict] = {item["ability_name"]: item for item in existing["cooldowns"]}
    for cd in cooldown_updates:
        is_new = cd.ability_name not in by_name
        by_name[cd.ability_name] = _cooldown_to_json(cd)
        if verbose:
            verb = "Added" if is_new else "Updated"
            print(f"  {'+' if is_new else '~'} {verb} {cd.ability_name}: {cd.cooldown_seconds:.0f}s cooldown")
    existing["cooldowns"] = sorted(by_name.values(), key=lambda item: item["ability_name"])
    backup_file(config_path)
    _write_json(config_path, existing)
    return config_path


def remove_raid_cooldown(
    ability_name: str, path: str | Path = DEFAULT_CONFIG_PATH, verbose: bool = False
) -> bool:
    config_path = Path(path)
    if not config_path.exists():
        return False
    existing = _read_existing(config_path)
    remaining = [item for item in existing["cooldowns"] if item["ability_name"] != ability_name]
    if len(remaining) == len(existing["cooldowns"]):
        return False
    if verbose:
        print(f"  - Removed {ability_name}")
    existing["cooldowns"] = remaining
    backup_file(config_path)
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
    if not isinstance(raw, dict):
        return False, f"{config_path} is valid JSON but is not an object at the top level."
    count = len(raw.get("cooldowns", []))
    return True, f"{config_path} is valid -- {count} tracked raid cooldown(s)."


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


MANUAL_COOLDOWNS: list[CooldownDefinition] = [
    CooldownDefinition("Revival", 180, category="raid_cd"),
    CooldownDefinition("Divine Hymn", 120, category="raid_cd"),
    CooldownDefinition("Tranquility", 180, category="raid_cd"),
    CooldownDefinition("Spirit Link Totem", 180, category="raid_cd"),
]

try:
    _generated_raid_cooldowns = load_generated_raid_cooldowns()
except RaidCooldownConfigError as exc:
    print(f"WARNING: raid_cooldowns.generated.json could not be read -- ignoring it.\n{exc}\n")
    _generated_raid_cooldowns = []

_by_name: dict[str, CooldownDefinition] = {
    cd.ability_name: cd for cd in as_cooldown_definitions(_generated_raid_cooldowns)
}
for _manual in MANUAL_COOLDOWNS:
    _by_name[_manual.ability_name] = _manual
TRACKED_COOLDOWNS: list[CooldownDefinition] = list(_by_name.values())


_RAID_COOLDOWN_KEYWORDS = re.compile(
    r"(bloodlust|heroism|time warp|ancient hysteria|fury of the aspects|"
    r"tranquility|revival|rapture|hymn|barrier|aura mastery|spirit link|"
    r"darkness|anti-magic zone|rallying cry|reincarnation|stampeding roar|"
    r"ancestral protection|guardian spirit|pain suppression|"
    r"power word|devouring plague|life cocoon|zephyr|vast oblivion|"
    r"mass barrier|blessing of|hand of|aegis|bulwark|sanctuary)",
    re.IGNORECASE,
)


@dataclass
class CandidateRaidCooldown:
    ability_name: str
    total_casts: int
    caster_names: set[str] = field(default_factory=set)
    caster_classes: set[str] = field(default_factory=set)
    observed_min_gap_seconds: float | None = None


def discover_candidate_raid_cooldowns(
    parsed_fight, already_tracked_names: set[str] | None = None
) -> list[CandidateRaidCooldown]:
    already_tracked_names = already_tracked_names or set()
    by_name: dict[str, CandidateRaidCooldown] = {}
    timestamps_by_name_caster: dict[str, dict[str, list[int]]] = {}
    for event in parsed_fight.events:
        if event.data_type != "Casts":
            continue
        ability_name = event.ability_name
        if not ability_name or ability_name in already_tracked_names:
            continue
        if not _RAID_COOLDOWN_KEYWORDS.search(ability_name):
            continue
        actor = parsed_fight.actors.get(event.source_id)
        caster_name = actor.name if actor is not None else (event.source_name or "Unknown")
        caster_class = getattr(actor, "subtype", None) if actor is not None else None
        if ability_name not in by_name:
            by_name[ability_name] = CandidateRaidCooldown(ability_name=ability_name, total_casts=0)
        candidate = by_name[ability_name]
        candidate.total_casts += 1
        candidate.caster_names.add(caster_name)
        if caster_class:
            candidate.caster_classes.add(caster_class)
        timestamps_by_name_caster.setdefault(ability_name, {}).setdefault(caster_name, []).append(event.timestamp)
    for ability_name, candidate in by_name.items():
        min_gap = None
        for caster_name, timestamps in timestamps_by_name_caster[ability_name].items():
            timestamps.sort()
            for a, b in zip(timestamps, timestamps[1:]):
                gap_seconds = (b - a) / 1000.0
                if min_gap is None or gap_seconds < min_gap:
                    min_gap = gap_seconds
        candidate.observed_min_gap_seconds = min_gap
    return sorted(by_name.values(), key=lambda c: -c.total_casts)


def format_candidates(candidates: list[CandidateRaidCooldown]) -> str:
    if not candidates:
        return "No raid-cooldown candidates found in this fight (or everything found is already tracked)."
    lines = [f"Found {len(candidates)} candidate raid cooldown(s):"]
    for c in candidates:
        classes = ", ".join(sorted(c.caster_classes)) or "unknown class"
        casters = ", ".join(sorted(c.caster_names))
        gap_str = f"{c.observed_min_gap_seconds:.0f}s (OBSERVED minimum gap -- a lower bound, not a confirmed cooldown)" \
            if c.observed_min_gap_seconds is not None \
            else "n/a (only cast once in this fight -- can't infer a gap)"
        lines.append(
            f"  {c.ability_name:<28} casts={c.total_casts:<3} class(es)=[{classes}] "
            f"cast by: {casters}\n"
            f"    observed min gap: {gap_str}"
        )
    lines.append(
        "\nVerify the TRUE base cooldown against a real spell reference before adding --\n"
        "the observed gap above can only ever UNDERSTATE the real cooldown (talents/haste\n"
        "shorten it, never lengthen it).\n"
        "Run `python raid_cooldowns_cli.py explore` to confirm and start tracking any of these."
    )
    return "\n".join(lines)


# =======================================================================
# SECTION 6 -- CLI (explore/list/add/remove/validate/repair)
# (originally raid_cooldowns_cli.py -- merged in so the whole raid-
# cooldowns system, engine + CLI, lives in one file.)
# =======================================================================

EVENT_TYPES_NEEDED = ["Casts"]


def cmd_list(_args) -> None:
    tracked = load_generated_raid_cooldowns()
    if not tracked:
        print(
            "No raid cooldowns tracked yet. Run "
            "`python raid_cooldowns_cli.py explore <report_code> [fight_id]` to find "
            "candidates from a real log, then `add` them here."
        )
        return
    print(f"Tracked raid cooldowns ({len(tracked)}):")
    for cd in sorted(tracked, key=lambda c: (c.category or "", c.ability_name)):
        class_str = f" ({cd.class_name})" if cd.class_name else ""
        category_str = f"[{cd.category}]" if cd.category else "[uncategorized]"
        note_str = f"\n      note: {cd.notes}" if cd.notes else ""
        print(f"  {cd.ability_name:<28} {cd.cooldown_seconds:>5.0f}s  {category_str}{class_str}{note_str}")


def cmd_add(args) -> None:
    if args.name and args.cooldown is not None:
        ability_name, cooldown_seconds = args.name, args.cooldown
        category, class_name, notes = args.category, args.class_name, args.notes
    else:
        print("Interactive add (press Ctrl+C to cancel):")
        ability_name = input("  Ability name (exact, as it appears in the log): ").strip()
        cooldown_seconds = float(input("  Cooldown, in seconds: ").strip())
        category = input("  Category (e.g. healing_cd, damage_reduction_cd, utility -- optional): ").strip() or None
        class_name = input("  Class (informational only, optional): ").strip() or None
        notes = input("  Notes (optional): ").strip() or None

    cd = RaidCooldownDefinition(
        ability_name=ability_name, cooldown_seconds=cooldown_seconds,
        category=category, class_name=class_name, notes=notes,
    )
    path = save_raid_cooldowns([cd], verbose=True)
    print(f"\nSaved to {path}")
    print(
        "\nNOTE: this only updates raid_cooldowns.generated.json. For it to actually "
        "affect reports, raid_cooldowns.py's TRACKED_COOLDOWNS must load from "
        "this file -- see that module's Section 4 docstring if it doesn't yet."
    )


def cmd_remove(args) -> None:
    removed = remove_raid_cooldown(args.name, verbose=True)
    if not removed:
        print(f"No tracked raid cooldown found named '{args.name}'.")


def cmd_validate(_args) -> None:
    ok, message = validate_file()
    print(message)
    sys.exit(0 if ok else 1)


def cmd_repair(_args) -> None:
    ok, message = repair_trailing_commas(verbose=True)
    print(message)
    sys.exit(0 if ok else 1)


def cmd_explore(args) -> None:
    try:
        client_id, client_secret = get_credentials()
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)

    client = WCLClient(client_id, client_secret)

    try:
        raw_report = client.get_report_fights(args.report_code)
    except WCLAPIError as exc:
        print(f"Error fetching report: {exc}")
        sys.exit(1)

    print(f"Report: {raw_report['title']}  ({raw_report['zone']['name']})")

    all_raw_fights = raw_report["fights"]
    criteria = FightFilterCriteria(only_boss_fights=not args.include_trash, min_duration_seconds=args.min_duration)
    filter_result = filter_fights(all_raw_fights, criteria)
    if not criteria.is_a_no_op:
        print(format_filter_summary(filter_result, criteria))

    fight_id = resolve_fight_id(filter_result.kept, args.fight_id, all_raw_fights=all_raw_fights)
    raw_fight = next(f for f in all_raw_fights if f["id"] == fight_id)

    try:
        raw_master_data = client.get_report_master_data(args.report_code)
        raw_events_by_type = client.get_report_events_multi(
            args.report_code, fight_id, event_types=EVENT_TYPES_NEEDED
        )
        parsed = parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        sys.exit(1)

    already_tracked_names = {cd.ability_name for cd in load_generated_raid_cooldowns()}
    print(f"\nScanning {parsed.fight.name} (fight {fight_id}) for raid-cooldown candidates...\n")
    candidates = discover_candidate_raid_cooldowns(parsed, already_tracked_names)
    print(format_candidates(candidates))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explore and manage tracked raid cooldowns (merged CLI).")
    subparsers = parser.add_subparsers(dest="command", required=True)

    explore_parser = subparsers.add_parser(
        "explore", help="Scan one fight's Casts events for likely raid-cooldown candidates."
    )
    explore_parser.add_argument("report_code")
    explore_parser.add_argument("fight_id", nargs="?", type=int, default=None)
    explore_parser.add_argument("--include-trash", action="store_true")
    explore_parser.add_argument("--min-duration", type=float, default=15.0)
    explore_parser.set_defaults(func=cmd_explore)

    subparsers.add_parser("list", help="List all tracked raid cooldowns.").set_defaults(func=cmd_list)

    p_add = subparsers.add_parser("add", help="Track a new raid cooldown (or update an existing one).")
    p_add.add_argument("--name", help="Exact ability name as it appears in the log")
    p_add.add_argument("--cooldown", type=float, help="Cooldown, in seconds")
    p_add.add_argument("--category", default=None, help="e.g. healing_cd, damage_reduction_cd, utility")
    p_add.add_argument("--class", dest="class_name", default=None, help="Informational only")
    p_add.add_argument("--notes", default=None)
    p_add.set_defaults(func=cmd_add)

    p_remove = subparsers.add_parser("remove", help="Stop tracking an ability.")
    p_remove.add_argument("--name", required=True)
    p_remove.set_defaults(func=cmd_remove)

    subparsers.add_parser("validate", help="Check raid_cooldowns.generated.json is valid JSON.").set_defaults(func=cmd_validate)
    subparsers.add_parser("repair", help="Attempt to auto-fix a trailing-comma JSON error.").set_defaults(func=cmd_repair)

    return parser


def main():
    args = build_argument_parser().parse_args()
    try:
        args.func(args)
    except RaidCooldownConfigError as exc:
        print(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
