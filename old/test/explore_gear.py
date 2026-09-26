"""
explore_gear.py

Interactive menu for building a gear compliance profile. On start it
ONLY shows a menu -- it does NOT touch the Warcraft Logs API until you
choose "add". "remove" and "list" work entirely from the local
gear_requirements.generated.json file.

Menu:
  [a] Add / adjust gear compliance rules -- fetches the report/fight,
                                             shows what enchants/gems/
                                             item levels/quality the
                                             roster actually has, and
                                             lets you set per-slot
                                             enchant rules, a gem-ID
                                             quality whitelist, plus
                                             global thresholds
  [r] Remove a rule or reset a threshold -- reads the local config only
  [l] List the current compliance profile -- reads the local config only
  [d] Done -- exit

Usage:
    python explore_gear.py
    python explore_gear.py <report_code>
    python explore_gear.py <report_code> <fight_id>
    python explore_gear.py <report_code> <fight_id> --export my_gear.json
    python explore_gear.py <report_code> <fight_id> --replace

Scope note: this tool is about ENCHANTS, GEMS, ITEM LEVEL, and QUALITY
TIER -- not specific items/trinkets/set bonuses, since there's no item
database available.
"""
from __future__ import annotations

import argparse
import os

from wcl_api import WCLClient, WCLAPIError
import cli_helpers
import config
import gear_analyzer
import gear_explorer
import log_parser
import manage_gear
from gear_config_io import (
    DEFAULT_CONFIG_PATH,
    GearConfigError,
    load_generated_gear_requirements,
    save_gear_requirements,
)
from gear_schema import EnchantRequirement
from selection_utils import parse_selection


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _pause() -> None:
    input("\nPress Enter to return to the menu...")


def fetch_and_analyze_gear(report_code: str | None, fight_id: int | None) -> dict | None:
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
            report_code, resolved_fight_id, event_types=["CombatantInfo"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        return None

    parsed = log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    print(f"\nParsed: {parsed.fight.name}  ({'kill' if parsed.fight.kill else 'wipe'})")

    distribution = gear_explorer.analyze_gear_distribution(parsed)
    return {"parsed": parsed, "distribution": distribution}


def _print_slot_menu(distribution) -> None:
    print("\nEnchantable slots (pick a number to configure a rule for that slot):")
    for index, detection in enumerate(distribution.slot_detections, start=1):
        ids_summary = ", ".join(
            f"{eid} ({len(detection.player_names_by_enchant_id[eid])}p)"
            for eid in detection.distinct_enchant_ids
        ) or "none seen"
        missing_count = len(detection.players_missing_enchant)
        missing_note = f", {missing_count} missing enchant" if missing_count else ""
        print(f"  {index}. {detection.slot_name:<10} -- detected: {ids_summary}{missing_note}")


def _prompt_enchant_requirement_for_slot(detection) -> EnchantRequirement:
    print(f"\n--- {detection.slot_name} ---")
    for enchant_id in detection.distinct_enchant_ids:
        players = sorted(detection.player_names_by_enchant_id[enchant_id])
        print(f"  enchant {enchant_id}: {len(players)} player(s) -- {', '.join(players)}")
    missing = sorted(detection.players_missing_enchant)
    if missing:
        print(f"  (no enchant): {len(missing)} player(s) -- {', '.join(missing)}")

    skip = input(
        f"  Exclude {detection.slot_name} from compliance checking entirely? [y/N]: "
    ).strip().lower()
    if skip in {"y", "yes"}:
        return EnchantRequirement(slot=detection.slot, slot_name=detection.slot_name, skip_check=True)

    raw_ids = input(
        "  Which enchant ID(s) should count as compliant for this slot? "
        "(comma-separated, e.g. 7452,7453; press Enter to accept ANY enchant): "
    ).strip()
    required_ids: tuple[int, ...] = ()
    if raw_ids:
        try:
            required_ids = tuple(int(part.strip()) for part in raw_ids.split(",") if part.strip())
        except ValueError:
            print("  Couldn't parse that as numbers -- accepting ANY enchant instead.")
            required_ids = ()

    notes = input(f"  Notes for {detection.slot_name!r} (optional): ").strip() or None
    return EnchantRequirement(
        slot=detection.slot, slot_name=detection.slot_name,
        required_enchant_ids=required_ids, notes=notes,
    )


def _prompt_thresholds(distribution) -> dict:
    updates: dict = {}

    if distribution.player_average_item_levels:
        levels = distribution.player_average_item_levels
        print(
            f"\nItem level across roster -- min: {min(levels):.1f}  max: {max(levels):.1f}  "
            f"avg: {sum(levels) / len(levels):.1f}"
        )
    raw = input("Minimum average item level to require? (press Enter to skip): ").strip()
    if raw:
        try:
            updates["min_item_level"] = float(raw)
        except ValueError:
            print("  Couldn't parse that as a number -- skipping.")

    if distribution.player_lowest_qualities:
        qualities = distribution.player_lowest_qualities
        names = ", ".join(sorted({gear_analyzer.quality_name(q) for q in qualities}))
        print(f"\nLowest quality tiers seen on roster: {names}")
    raw = input(
        "Minimum quality tier to require? (0=Poor .. 4=Epic .. 5=Legendary; press Enter to skip): "
    ).strip()
    if raw:
        try:
            updates["min_quality"] = int(raw)
        except ValueError:
            print("  Couldn't parse that as a number -- skipping.")

    if distribution.gem_count_distribution:
        counts = ", ".join(
            f"{count} gem(s)={num_players}p"
            for count, num_players in sorted(distribution.gem_count_distribution.items())
        )
        print(f"\nGem count distribution: {counts}")
    raw = input("Minimum total gem count to require? (press Enter to skip): ").strip()
    if raw:
        try:
            updates["min_gem_count"] = int(raw)
        except ValueError:
            print("  Couldn't parse that as a number -- skipping.")

    return updates


def _prompt_gem_id_whitelist(distribution) -> tuple[int, ...]:
    """
    Ask which specific gem IDs should count as "correct quality" -- a
    socket is present, does it have a gem? If yes, does that gem's ID
    match one of the accepted IDs?
    """
    if distribution.gem_id_player_names:
        print("\nGem IDs observed across the roster (any equipped piece, any slot):")
        for gem_id in sorted(distribution.gem_id_player_names):
            players = sorted(distribution.gem_id_player_names[gem_id])
            print(f"  gem {gem_id}: {len(players)} player(s) -- {', '.join(players)}")
    else:
        print("\nNo gems detected in this pull.")

    raw = input(
        "Which gem ID(s) should count as the accepted quality (a whitelist -- any "
        "socketed gem outside this set fails)? (comma-separated, e.g. 1230459,1230460; "
        "press Enter to skip): "
    ).strip()
    if not raw:
        return ()
    try:
        return tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError:
        print("  Couldn't parse that as numbers -- skipping.")
        return ()


def _prompt_profile_notes() -> str | None:
    return input("\nAny notes for this whole gear compliance profile? (optional): ").strip() or None


def _do_add(session: dict, export_path, replace: bool) -> bool:
    distribution = session["distribution"]

    if not distribution.slot_detections and not distribution.player_average_item_levels:
        print("\nNo gear data found for this fight.")
        return False

    _print_slot_menu(distribution)
    raw = input(
        "\nWhich slot number(s) do you want to configure (e.g. 1,3-4)? "
        "Press Enter to skip enchant rules: "
    )
    enchant_updates: list[EnchantRequirement] = []
    try:
        indexes = parse_selection(raw, len(distribution.slot_detections))
    except ValueError as exc:
        print(f"Invalid selection: {exc}")
        indexes = []
    for index in indexes:
        detection = distribution.slot_detections[index]
        enchant_updates.append(_prompt_enchant_requirement_for_slot(detection))

    threshold_updates = _prompt_thresholds(distribution)
    gem_id_updates = _prompt_gem_id_whitelist(distribution)
    profile_notes = _prompt_profile_notes()

    if not enchant_updates and not threshold_updates and not gem_id_updates and not profile_notes:
        print("\nNothing configured -- nothing saved.")
        return False

    print()
    try:
        path = save_gear_requirements(
            enchant_requirement_updates=enchant_updates,
            min_item_level=threshold_updates.get("min_item_level"),
            min_quality=threshold_updates.get("min_quality"),
            min_gem_count=threshold_updates.get("min_gem_count"),
            required_gem_ids_updates=gem_id_updates,
            notes=profile_notes,
            path=export_path, replace=replace, verbose=True,
        )
    except GearConfigError as exc:
        print(
            f"Could not save -- {export_path} currently has a problem and wasn't "
            f"touched (nothing was overwritten):\n\n{exc}\n\n"
            f"Fix it, then try again:\n"
            f"  python manage_gear.py validate\n"
            f"  python manage_gear.py repair"
        )
        return False

    print(f"\nSaved to: {path}")
    return True


def _do_remove_menu(path) -> None:
    manage_gear.interactive_remove_gear_setting(path)


def _do_list_menu(path) -> None:
    try:
        requirements = load_generated_gear_requirements(path)
    except GearConfigError as exc:
        print(f"WARNING: could not read {path}:\n{exc}")
        return
    manage_gear.print_gear_requirements_detail(requirements)


def run_interactive_session(args: argparse.Namespace) -> None:
    session: dict = {"parsed": None, "distribution": None}
    replace_pending = args.replace

    while True:
        clear_screen()
        print("PullDoctor -- Gear Compliance Explorer\n")
        print(
            "What would you like to do?\n"
            "  [a] Add / adjust gear compliance rules\n"
            "  [r] Remove a rule or reset a threshold\n"
            "  [l] List the current compliance profile\n"
            "  [d] Done"
        )
        choice = input("> ").strip().lower()

        if choice in {"a", "add"}:
            if session["parsed"] is None:
                result = fetch_and_analyze_gear(args.report_code, args.fight_id)
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
            print("\nDone. gear_requirements_config.py will load any saved changes automatically.")
            return

        else:
            print("Enter a, r, l, or d.")
            _pause()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explore and manage gear compliance rules (menu-driven)."
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
        help="Generated JSON config path (default: gear_requirements.generated.json)",
    )
    parser.add_argument(
        "--replace", action="store_true",
        help="Wipe the ENTIRE gear compliance profile on the FIRST successful 'add' this session.",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    run_interactive_session(args)


if __name__ == "__main__":
    main()
