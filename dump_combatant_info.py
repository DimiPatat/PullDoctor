"""
dump_combatant_info.py

Diagnostic tool: prints the RAW CombatantInfo data Warcraft Logs returns for
a fight, alongside how log_parser.py/gear_analyzer.py interpret it -- side
by side, per player, per gear piece. Use this when gear_analyzer.py's or
explore_gear.py's numbers look wrong (e.g. everyone missing enchants,
item level way too low/high) and you want to see whether the problem is
in the raw API data itself or in how it's being parsed downstream.

This makes NO changes to any file and does not require any other tool
to have been run first.

Usage:
    python dump_combatant_info.py <report_code> <fight_id>
    python dump_combatant_info.py <report_code> <fight_id> --raw-only
    python dump_combatant_info.py <report_code> <fight_id> --player Alpha
"""
from __future__ import annotations

import argparse
import json

from wcl_api import WCLClient, WCLAPIError
import config
import log_parser


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dump raw + parsed CombatantInfo gear data for one fight."
    )
    parser.add_argument("report_code", help="Warcraft Logs report code")
    parser.add_argument("fight_id", type=int, help="Fight ID")
    parser.add_argument(
        "--raw-only", action="store_true",
        help="Only print the raw, unparsed JSON from WCL (skip the parsed/interpreted view).",
    )
    parser.add_argument(
        "--player", default=None,
        help="Only show this one player's gear (matches actor name, case-insensitive).",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()

    try:
        client_id, client_secret = config.get_credentials()
    except RuntimeError as exc:
        print(exc)
        raise SystemExit(1)

    client = WCLClient(client_id, client_secret)

    try:
        raw_report = client.get_report_fights(args.report_code)
    except WCLAPIError as exc:
        print(f"Error fetching report: {exc}")
        raise SystemExit(1)

    raw_fight = next((f for f in raw_report["fights"] if f["id"] == args.fight_id), None)
    if raw_fight is None:
        print(f"Fight #{args.fight_id} not found in this report.")
        raise SystemExit(1)

    print(f"Report: {raw_report['title']}  ({raw_report['zone']['name']})")
    print(f"Fight: #{raw_fight['id']} {raw_fight['name']}  "
          f"({'KILL' if raw_fight.get('kill') else 'wipe'})\n")

    try:
        raw_master_data = client.get_report_master_data(args.report_code)
        raw_events_by_type = client.get_report_events_multi(
            args.report_code, args.fight_id, event_types=["CombatantInfo"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        raise SystemExit(1)

    raw_combatant_info_events = raw_events_by_type.get("CombatantInfo", [])

    print(f"Raw CombatantInfo events returned by WCL: {len(raw_combatant_info_events)}")
    if not raw_combatant_info_events:
        print(
            "\n*** ZERO CombatantInfo events were returned for this fight. ***\n"
            "This means WCL has NO gear snapshot at all for this pull -- every\n"
            "downstream tool will correctly show 'no gear data' / everything\n"
            "missing, because there is nothing to parse. This is NOT a parsing bug.\n\n"
            "Common reasons this happens:\n"
            "  - This fight_id is a trash pull, not a boss pull.\n"
            "  - The pull was extremely short.\n"
            "  - This specific report has anonymization/privacy settings.\n"
            "Try a different, longer boss pull from the same report and re-run this tool."
        )
        return

    print()

    actor_name_by_id = {a["id"]: a.get("name", "Unknown") for a in raw_master_data.get("actors", [])}
    ability_name_by_id = {a["gameID"]: a.get("name", "Unknown") for a in raw_master_data.get("abilities", [])}

    for raw_event in raw_combatant_info_events:
        player_id = raw_event.get("sourceID")
        player_name = actor_name_by_id.get(player_id, f"Unknown(id={player_id})")

        if args.player and args.player.lower() != player_name.lower():
            continue

        print("=" * 70)
        print(f"Player: {player_name}  (actor id {player_id})")
        print("=" * 70)

        raw_gear = raw_event.get("gear", [])
        print(f"Raw 'gear' array length: {len(raw_gear)}")

        if not raw_gear:
            print("  *** 'gear' array is EMPTY for this player in the raw API response. ***")
        elif args.raw_only:
            print(json.dumps(raw_gear, indent=2))
        else:
            print(f"{'Index':>5} {'ItemID':>8} {'Quality':>8} {'ItemLevel':>10} "
                  f"{'PermEnchant':>12} {'TempEnchant':>12} {'Gems':<20}")
            print("-" * 90)
            for slot_index, raw_item in enumerate(raw_gear):
                gems = raw_item.get("gems", [])
                gem_ids = [g.get("id") for g in gems if g.get("id")]
                gem_text = str(gem_ids) if gem_ids else "none"
                print(
                    f"{slot_index:>5} "
                    f"{str(raw_item.get('id', '?')):>8} "
                    f"{str(raw_item.get('quality', '?')):>8} "
                    f"{str(raw_item.get('itemLevel', 'MISSING')):>10} "
                    f"{str(raw_item.get('permanentEnchant', 'none')):>12} "
                    f"{str(raw_item.get('temporaryEnchant', 'none')):>12} "
                    f"{gem_text:<20}"
                )
            print(
                "\nNOTE: 'Index' above IS the slot number (array position) -- WCL's raw\n"
                "gear entries have NO 'slot' field; slot is always implied by position\n"
                "(0=Head,1=Neck,2=Shoulder,3=Shirt,4=Chest,5=Waist,6=Legs,7=Feet,\n"
                "8=Wrist,9=Hands,10=Ring1,11=Ring2,12=Trinket1,13=Trinket2,14=Back,\n"
                "15=MainHand,16=OffHand,17=Ranged/Relic)."
            )

            item_levels = [item.get("itemLevel") for item in raw_gear if item.get("itemLevel") is not None]
            missing_count = len(raw_gear) - len(item_levels)
            print()
            if item_levels:
                print(
                    f"itemLevel values present: {len(item_levels)}/{len(raw_gear)} pieces  "
                    f"(min={min(item_levels)}, max={max(item_levels)}, "
                    f"avg={sum(item_levels)/len(item_levels):.1f})"
                )
            if missing_count:
                print(f"*** {missing_count} piece(s) have NO 'itemLevel' field at all in the raw data. ***")

        auras = raw_event.get("auras", [])
        aura_names = sorted({ability_name_by_id.get(a.get("ability"), f"id={a.get('ability')}") for a in auras})
        print(f"\nPre-pull auras detected ({len(auras)} total): {', '.join(aura_names) if aura_names else '(none)'}")
        print()

    if not args.raw_only:
        print("\n" + "=" * 70)
        print("PARSED VIEW (what log_parser.py / gear_analyzer.py compute from the above)")
        print("=" * 70)
        parsed = log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
        import gear_analyzer
        reports = gear_analyzer.analyze_gear(parsed)
        for report in reports:
            if args.player and args.player.lower() != (report.player_name or "").lower():
                continue
            if not report.has_data:
                print(f"{report.player_name}: NO PARSED GEAR DATA (has_data=False)")
                continue
            print(
                f"{report.player_name}: avg ilvl {report.average_item_level:.1f}  "
                f"lowest quality {gear_analyzer.quality_name(report.lowest_quality) if report.lowest_quality is not None else 'n/a'}  "
                f"gems {report.total_gems}  missing enchants: {report.missing_enchant_slots or 'none'}"
            )


if __name__ == "__main__":
    main()
