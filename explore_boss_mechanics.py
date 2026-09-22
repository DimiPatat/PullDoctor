"""
explore_boss_mechanics.py

Interactive menu for managing avoidable-mechanic selections. On start it
ONLY shows a menu -- it does NOT touch the Warcraft Logs API until you
choose "add" (the only action that needs fresh log data). "remove" and
"list" work entirely from the local boss_mechanics.generated.json file.

Menu:
  [a] Add abilities to the tracked list   -- fetches the report/fight THEN
                                              shows ability tables to pick from
  [r] Remove abilities from the tracked list -- reads the local config only
  [l] List currently tracked abilities       -- reads the local config only
  [d] Done -- exit

Usage:
    python explore_boss_mechanics.py
    python explore_boss_mechanics.py <report_code>
    python explore_boss_mechanics.py <report_code> <fight_id>
    python explore_boss_mechanics.py <report_code> <fight_id> --export my_rules.json
    python explore_boss_mechanics.py <report_code> <fight_id> --replace
    python explore_boss_mechanics.py <report_code> <fight_id> --show-self-only
"""
from __future__ import annotations

import argparse
import os

from wcl_api import WCLClient, WCLAPIError
import ability_explorer
import cli_helpers
import config
import log_parser
import manage_boss_mechanics
from avoidable_damage_data import AvoidableMechanic
from mechanics_config_io import (
    DEFAULT_CONFIG_PATH,
    MechanicsConfigError,
    load_generated_encounters,
    save_encounter_selection,
)
from selection_utils import parse_selection


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
    """The ONLY function in this module that talks to the Warcraft Logs API."""
    if not report_code:
        report_code = input(
            "Warcraft Logs report code (the part of the URL after /reports/): "
        ).strip()
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
            report_code, resolved_fight_id,
            event_types=["Casts", "DamageTaken", "Debuffs", "Deaths"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        return None

    parsed = log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    print(f"\nEncounter ID: {parsed.fight.encounter_id}")

    npc_summaries = ability_explorer.analyze_boss_abilities(parsed)
    all_player_summaries = ability_explorer.analyze_player_sourced_abilities(parsed)
    if show_self_only:
        player_summaries = all_player_summaries
    else:
        player_summaries = ability_explorer.analyze_player_sourced_abilities(parsed, min_other_hits=1)
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
    """
    Ask whether this mechanic is a SHARED/SPLIT damage mechanic. Only
    asked when track_via includes "damage".
    """
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
            f"  python manage_boss_mechanics.py validate\n"
            f"  python manage_boss_mechanics.py repair"
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
    manage_boss_mechanics.interactive_remove_mechanics(encounter_id, path)


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
        manage_boss_mechanics.print_encounter_detail(next(iter(encounters.values())))
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
    manage_boss_mechanics.print_encounter_detail(encounters[chosen_id])


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
            print("\nDone. boss_mechanics_config.py will load any saved changes automatically.")
            return

        else:
            print("Enter a, r, l, or d.")
            _pause()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explore and manage avoidable boss mechanics (menu-driven)."
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
        help="Generated JSON config path (default: boss_mechanics.generated.json)",
    )
    parser.add_argument(
        "--replace", action="store_true",
        help="Wipe the encounter's previously saved mechanics on the FIRST successful 'add' this session.",
    )
    parser.add_argument(
        "--show-self-only", action="store_true",
        help="Also show player-sourced abilities that never hit anyone but their own caster.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    run_interactive_session(args)


if __name__ == "__main__":
    main()
