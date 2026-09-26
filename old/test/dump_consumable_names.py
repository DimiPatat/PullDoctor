"""
dump_consumable_names.py

Diagnostic tool: fetches the RAW pre-pull aura names, mid-fight cast
names, AND weapon temporary-enchant IDs Warcraft Logs returns for one
fight, side by side with what's currently tracked.

Makes NO changes to any file.

Usage:
    python dump_consumable_names.py <report_code> <fight_id>
    python dump_consumable_names.py <report_code> <fight_id> --player Alice
"""
from __future__ import annotations

import argparse

from wcl_api import WCLClient, WCLAPIError
import config
import consumable_data
import consumables_analyzer
import log_parser
import roster

_WEAPON_SLOT_NAMES = {15: "Main Hand", 16: "Off Hand"}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dump raw aura/cast/weapon-enchant names for one fight.")
    parser.add_argument("report_code", help="Warcraft Logs report code")
    parser.add_argument("fight_id", type=int, help="Fight ID")
    parser.add_argument("--player", default=None, help="Only show this one player (case-insensitive).")
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
    print(f"Fight: #{raw_fight['id']} {raw_fight['name']}  ({'KILL' if raw_fight.get('kill') else 'wipe'})\n")

    try:
        raw_master_data = client.get_report_master_data(args.report_code)
        raw_events_by_type = client.get_report_events_multi(
            args.report_code, args.fight_id, event_types=["CombatantInfo", "Casts"],
        )
    except WCLAPIError as exc:
        print(f"Error fetching fight data: {exc}")
        raise SystemExit(1)

    parsed = log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)
    fight_roster = roster.get_fight_roster(parsed)

    if args.player:
        matches = [a for a in fight_roster.values() if a.name.lower() == args.player.lower()]
        if not matches:
            print(f"Player {args.player!r} not found in this fight's roster.")
            raise SystemExit(1)
        fight_roster = {a.id: a for a in matches}

    aura_name_players: dict[str, set[str]] = {}
    for player_id, actor in fight_roster.items():
        snapshot = parsed.combatant_info.get(player_id)
        if snapshot is None:
            continue
        for aura_name in snapshot.aura_names:
            aura_name_players.setdefault(aura_name, set()).add(actor.name)

    cast_name_players: dict[str, set[str]] = {}
    for event in parsed.events:
        if event.data_type != "Casts" or not event.ability_name:
            continue
        player = roster.resolve_to_player(event.source_id, parsed.actors)
        if player is None or player.id not in fight_roster:
            continue
        cast_name_players.setdefault(event.ability_name, set()).add(player.name)

    tracked_names = {c.name: c.category for c in consumable_data.ALL_TRACKED_CONSUMABLES}

    print("=" * 78)
    print("RAW PRE-PULL AURA NAMES (from CombatantInfo -- flasks/food/runes)")
    print("=" * 78)
    if not aura_name_players:
        print("  (none found)")
    else:
        for name in sorted(aura_name_players):
            players = sorted(aura_name_players[name])
            marker = f"  <- TRACKED as '{tracked_names[name]}'" if name in tracked_names else ""
            print(f"  {name!r:<45} ({len(players)}p: {', '.join(players)[:40]}){marker}")

    print()
    print("=" * 78)
    print("RAW MID-FIGHT CAST NAMES (from Casts -- potions, healthstone, etc.)")
    print("=" * 78)
    if not cast_name_players:
        print("  (none found)")
    else:
        for name in sorted(cast_name_players):
            players = sorted(cast_name_players[name])
            marker = f"  <- TRACKED as '{tracked_names[name]}'" if name in tracked_names else ""
            if name in tracked_names or any(k in name.lower() for k in ("potion", "stone", "rune", "elixir", "draught", "oil")):
                print(f"  {name!r:<45} ({len(players)}p: {', '.join(players)[:40]}){marker}")

    print()
    print("=" * 78)
    print("WEAPON TEMPORARY-ENCHANT IDs (this is what OILS actually are -- NOT an aura)")
    print("=" * 78)
    print("(Detection is now ID-AGNOSTIC: ANY non-empty value here counts as 'oil used',")
    print(" regardless of which specific ID it is. This section is purely informational --")
    print(" a blank row for a player means they genuinely have no weapon oil applied.)")
    any_enchants_found = False
    for player_id, actor in sorted(fight_roster.items(), key=lambda kv: kv[1].name):
        enchant_ids = consumables_analyzer.get_weapon_temporary_enchant_ids(player_id, parsed)
        if enchant_ids:
            any_enchants_found = True
            print(f"  {actor.name:<15} enchant id(s): {enchant_ids}  -> oil DETECTED (loose match)")
        else:
            print(f"  {actor.name:<15} (no weapon temporary enchant found)")
    if not any_enchants_found:
        print("\n  *** NO player in this fight has ANY weapon temporary enchant at all. ***")
        print("  This could mean: (a) genuinely nobody used an oil this pull, (b) this")
        print("  fight has no CombatantInfo snapshot at all (check with dump_combatant_info.py),")
        print("  or (c) this specific report/server doesn't populate temporaryEnchant data.")

    print()
    print("=" * 78)
    print("TRACKED CONSUMABLES (NON-OIL) THAT NEVER APPEARED IN THIS FIGHT'S RAW DATA")
    print("=" * 78)
    all_raw_names = set(aura_name_players) | set(cast_name_players)
    never_appeared = sorted(
        name for name, category in tracked_names.items()
        if category != "oil" and name not in all_raw_names
    )
    if not never_appeared:
        print("  (every non-oil tracked consumable was seen at least once)")
    else:
        for name in never_appeared:
            print(f"  {name!r}  (category: {tracked_names[name]})")


if __name__ == "__main__":
    main()
