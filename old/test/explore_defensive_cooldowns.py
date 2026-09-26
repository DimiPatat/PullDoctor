"""
explore_defensive_cooldowns.py

Interactive menu for managing tracked defensive cooldowns. On start it
ONLY shows a menu -- it does NOT touch the Warcraft Logs API until you
choose "add". "remove" and "list" work entirely from the local
defensive_cooldowns.generated.json file.

Menu:
  [a] Add defensive cooldowns to the tracked list -- fetches the
                                                       report/fight,
                                                       detects known
                                                       defensives, and
                                                       lets you pick
                                                       which to track,
                                                       INCLUDING their
                                                       damage-mitigation
                                                       profile
  [r] Remove defensive cooldowns from the tracked list -- local config only
  [l] List currently tracked defensive cooldowns       -- local config only
  [d] Done -- exit

Damage-mitigation profile: for each ability you add, you'll be asked
whether it's a flat % damage reduction, a near-100% immunity, or
something that doesn't fit either model. Detected abilities are pre-
filled with sensible defaults from the seed reference list
(defensive_cooldown_data.py) that you can accept or correct; press
Enter at any prompt to keep the suggested/default value.

Usage:
    python explore_defensive_cooldowns.py
    python explore_defensive_cooldowns.py <report_code>
    python explore_defensive_cooldowns.py <report_code> <fight_id>
    python explore_defensive_cooldowns.py <report_code> <fight_id> --export my_defensives.json
    python explore_defensive_cooldowns.py <report_code> <fight_id> --replace
"""
from __future__ import annotations

import argparse
import os

from wcl_api import WCLClient, WCLAPIError
import cli_helpers
import config
import defensive_cooldown_data
import defensive_cooldown_explorer
import log_parser
import manage_defensive_cooldowns
from defensive_cooldown_config_io import (
    DEFAULT_CONFIG_PATH,
    DefensiveCooldownConfigError,
    load_generated_defensive_cooldowns,
    save_defensive_cooldown_selection,
)
from defensive_cooldown_schema import MITIGATION_TYPES, DefensiveCooldownDefinition
from selection_utils import parse_selection


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _pause() -> None:
    input("\nPress Enter to return to the menu...")


def get_tracked_ability_names(path) -> set[str]:
    try:
        definitions = load_generated_defensive_cooldowns(path)
    except DefensiveCooldownConfigError as exc:
        print(
            f"\nWARNING: {path} has a problem and couldn't be read -- "
            f"skipping the 'already tracked' highlighting for this run.\n{exc}\n"
        )
        return set()
    return {d.ability_name for d in definitions}


def print_detected_table(detected, tracked_names: set[str] = frozenset()) -> None:
    if not detected:
        print("No known defensive cooldowns detected in this pull.")
        return
    if tracked_names:
        print("(* in the T column = already tracked)")
    header = f"{'#':>3} T {'Ability':<28} {'Class':<14} {'CD(s)':>6} {'Players':>8} {'TotalCasts':>11}"
    print(header)
    print("-" * len(header))
    for index, entry in enumerate(detected, start=1):
        marker = "*" if entry.definition.ability_name in tracked_names else " "
        print(
            f"{index:>3} {marker} {entry.definition.ability_name:<28.28} "
            f"{(entry.definition.class_name or ''):<14.14} {entry.definition.cooldown_seconds:>6.0f} "
            f"{entry.player_count:>8} {entry.total_casts:>11}"
        )


def fetch_and_detect(report_code: str | None, fight_id: int | None) -> dict | None:
    """The ONLY function in this module that talks to the Warcraft Logs API."""
    if not report_code:
        report_code = input("Warcraft Logs report code (the part of the URL after /reports/): ").strip()
        if not report_code:
            print("No report code given -- cancelled.")
            return None

    try:
        client_id, client_secret = config.get_credentials()
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
        resolved_fight_id = cli_helpers.resolve_fight_id(raw_report["fights"], fight_id)
    except SystemExit:
        print("No valid fight selected -- cancelled.")
        return None

    raw_fight = next(f for f in raw_report["fights"] if f["id"] == resolved_fight_id)

    try:
        raw_master_data = client.get_report_master_data(report_code)
        raw_events_by_type = client.get_report_events_multi(
            report_code, resolved_fight_id, event_types=["Casts"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        return None

    parsed = log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    print(f"\nParsed: {parsed.fight.name}  ({'kill' if parsed.fight.kill else 'wipe'})")

    detected = defensive_cooldown_explorer.analyze_known_defensive_cooldown_matches(
        parsed, defensive_cooldown_data.SEED_REFERENCE_DEFENSIVE_COOLDOWNS
    )

    return {"parsed": parsed, "detected": detected}


def _prompt_float(prompt_text: str, default: float | None) -> float | None:
    default_label = f"{default:.0f}" if default is not None else "none"
    while True:
        raw = input(f"{prompt_text} (default {default_label}): ").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print("  Couldn't parse that as a number -- try again, or press Enter to keep the default.")


def _prompt_mitigation_profile(
    ability_name: str,
    default_mitigation_type: str = "unmodeled",
    default_damage_reduction_percent: float | None = None,
    default_duration_seconds: float | None = None,
    default_notes: str | None = None,
) -> tuple[str, float | None, float | None, str | None]:
    print(f"\n  Damage-mitigation profile for {ability_name!r}:")
    print(f"    (this feeds defensive_damage_prevention_analyzer.py's 'damage prevented' estimate -- optional)")
    if default_notes:
        print(f"    Seed list notes: {default_notes}")

    type_labels = {
        "percent_reduction": "flat % damage reduction (e.g. Shield Wall)",
        "immunity": "near-100% immunity (e.g. Ice Block)",
        "unmodeled": "doesn't fit either model -- absorb/heal/avoidance/redirect (skip damage-prevention estimate)",
    }
    print("    Mitigation type:")
    for key in MITIGATION_TYPES:
        marker = " <- detected default" if key == default_mitigation_type else ""
        print(f"      [{key[0]}] {key} -- {type_labels[key]}{marker}")

    default_letter = default_mitigation_type[0]
    while True:
        raw = input(f"    Choose p/i/u (default {default_letter}): ").strip().lower()
        if not raw:
            mitigation_type = default_mitigation_type
            break
        matches = [t for t in MITIGATION_TYPES if t.startswith(raw)]
        if len(matches) == 1:
            mitigation_type = matches[0]
            break
        print("    Enter p, i, or u (or press Enter to keep the default).")

    if mitigation_type == "unmodeled":
        return mitigation_type, None, None, default_notes

    damage_reduction_percent = default_damage_reduction_percent
    if mitigation_type == "percent_reduction":
        damage_reduction_percent = _prompt_float(
            "    Damage reduction percent (e.g. 30 for 30%)", default_damage_reduction_percent,
        )
        if damage_reduction_percent is not None and not (0 < damage_reduction_percent < 100):
            print("    Warning: reduction percent should be strictly between 0 and 100 for a back-calculation "
                  "to make sense -- 100% belongs under 'immunity' instead.")

    duration_seconds = _prompt_float(
        "    Effect duration in seconds (how long the mitigation window lasts once cast)", default_duration_seconds,
    )

    notes = input(f"    Notes for {ability_name!r} (optional, Enter to keep existing): ").strip()
    if not notes:
        notes = default_notes

    return mitigation_type, damage_reduction_percent, duration_seconds, notes


def _prompt_manual_additions() -> list[DefensiveCooldownDefinition]:
    manual: list[DefensiveCooldownDefinition] = []
    while True:
        answer = input("\nManually add a defensive cooldown not listed above? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            break
        name = input("  Ability name (must match the exact log name): ").strip()
        if not name:
            print("  Name cannot be blank -- skipping.")
            continue
        raw_cd = input("  Cooldown in seconds (e.g. 180): ").strip()
        try:
            cooldown_seconds = float(raw_cd)
        except ValueError:
            print("  Couldn't parse that as a number -- skipping this entry.")
            continue
        class_name = input("  Class (optional, e.g. Paladin): ").strip() or None
        category = input("  Category (optional, e.g. immunity/damage_reduction/external): ").strip() or None
        mitigation_type, damage_reduction_percent, duration_seconds, notes = _prompt_mitigation_profile(name)
        manual.append(DefensiveCooldownDefinition(
            ability_name=name, cooldown_seconds=cooldown_seconds,
            category=category, class_name=class_name, notes=notes,
            mitigation_type=mitigation_type, damage_reduction_percent=damage_reduction_percent,
            duration_seconds=duration_seconds,
        ))
    return manual


def _do_add(session: dict, export_path, replace: bool) -> bool:
    detected = session["detected"]
    tracked_names = get_tracked_ability_names(export_path)

    print()
    print_detected_table(detected, tracked_names)

    to_add: list[DefensiveCooldownDefinition] = []
    if detected:
        raw = input(
            "\nSelect detected defensive cooldowns to add by number (e.g. 1,3,5-7). "
            "Press Enter to select none: "
        )
        try:
            indexes = parse_selection(raw, len(detected))
        except ValueError as exc:
            print(f"Invalid selection: {exc}")
            indexes = []
        for index in indexes:
            entry = detected[index]
            raw_cd = input(
                f"\n  Cooldown in seconds for {entry.definition.ability_name!r} "
                f"(detected default {entry.definition.cooldown_seconds:.0f}s, press Enter to keep): "
            ).strip()
            if raw_cd:
                try:
                    cooldown_seconds = float(raw_cd)
                except ValueError:
                    print("  Couldn't parse that as a number -- keeping the detected default.")
                    cooldown_seconds = entry.definition.cooldown_seconds
            else:
                cooldown_seconds = entry.definition.cooldown_seconds
            category = input(
                f"  Category for {entry.definition.ability_name!r} "
                f"(detected as {entry.definition.category!r}, press Enter to keep): "
            ).strip() or entry.definition.category

            mitigation_type, damage_reduction_percent, duration_seconds, notes = _prompt_mitigation_profile(
                entry.definition.ability_name,
                default_mitigation_type=entry.definition.mitigation_type,
                default_damage_reduction_percent=entry.definition.damage_reduction_percent,
                default_duration_seconds=entry.definition.duration_seconds,
                default_notes=entry.definition.notes,
            )

            to_add.append(DefensiveCooldownDefinition(
                ability_name=entry.definition.ability_name,
                cooldown_seconds=cooldown_seconds,
                category=category,
                class_name=entry.definition.class_name,
                ability_ids=tuple(entry.ability_ids_seen),
                notes=notes,
                mitigation_type=mitigation_type,
                damage_reduction_percent=damage_reduction_percent,
                duration_seconds=duration_seconds,
            ))

    to_add.extend(_prompt_manual_additions())

    if not to_add:
        print("\nNo defensive cooldowns selected -- nothing saved.")
        return False

    print()
    try:
        path = save_defensive_cooldown_selection(to_add, path=export_path, replace=replace, verbose=True)
    except DefensiveCooldownConfigError as exc:
        print(
            f"Could not save -- {export_path} currently has a problem and wasn't "
            f"touched (nothing was overwritten):\n\n{exc}\n\n"
            f"Fix it, then try again:\n"
            f"  python manage_defensive_cooldowns.py validate\n"
            f"  python manage_defensive_cooldowns.py repair"
        )
        return False

    print(f"\nSaved to: {path}")
    return True


def _do_remove_menu(path) -> None:
    manage_defensive_cooldowns.interactive_remove_defensive_cooldowns(path)


def _do_list_menu(path) -> None:
    try:
        definitions = load_generated_defensive_cooldowns(path)
    except DefensiveCooldownConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return
    if not definitions:
        print(f"No defensive cooldowns configured yet in {path}.")
        print("Choose 'add' first to explore a report and select some.")
        return
    print(f"{len(definitions)} defensive cooldown(s) tracked in {path}:\n")
    manage_defensive_cooldowns.print_defensive_cooldowns_detail(definitions)


def run_interactive_session(args: argparse.Namespace) -> None:
    session: dict = {"parsed": None, "detected": None}
    replace_pending = args.replace

    while True:
        clear_screen()
        print("PullDoctor -- Defensive Cooldowns Explorer\n")
        print(
            "What would you like to do?\n"
            "  [a] Add defensive cooldowns to the tracked list\n"
            "  [r] Remove defensive cooldowns from the tracked list\n"
            "  [l] List currently tracked defensive cooldowns\n"
            "  [d] Done"
        )
        choice = input("> ").strip().lower()

        if choice in {"a", "add"}:
            if session["parsed"] is None:
                result = fetch_and_detect(args.report_code, args.fight_id)
                if result is None:
                    _pause()
                    continue
                session.update(result)

            saved = _do_add(session, args.export, replace_pending)
            if saved:
                replace_pending = False
            _pause()

        elif choice in {"r", "remove"}:
            print()
            _do_remove_menu(args.export)
            _pause()

        elif choice in {"l", "list"}:
            print()
            _do_list_menu(args.export)
            _pause()

        elif choice in {"d", "done", "q", "quit"}:
            print("\nDone. defensive_cooldown_data.py will load any saved changes automatically.")
            return

        else:
            print("Enter a, r, l, or d.")
            _pause()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explore and manage tracked defensive cooldowns (menu-driven)."
    )
    parser.add_argument(
        "report_code", nargs="?", default=None,
        help="Warcraft Logs report code -- only needed for 'add'; prompted for if omitted.",
    )
    parser.add_argument(
        "fight_id", nargs="?", type=int, default=None,
        help="Optional fight ID -- only needed for 'add'; prompted for if omitted.",
    )
    parser.add_argument(
        "--export", default=str(DEFAULT_CONFIG_PATH),
        help="Generated JSON config path (default: defensive_cooldowns.generated.json)",
    )
    parser.add_argument(
        "--replace", action="store_true",
        help="Wipe all previously tracked defensive cooldowns on the FIRST successful 'add' this session.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    run_interactive_session(args)


if __name__ == "__main__":
    main()
