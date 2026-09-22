"""
example_usage.py

Standalone smoke test wiring wcl_api.py + log_parser.py together --
not part of the app's real module chain, just a sanity check for
these two pieces while developing them.

Set your credentials as environment variables before running:
    set WCL_CLIENT_ID=...
    set WCL_CLIENT_SECRET=...
    python example_usage.py <report_code>
"""

import os
import sys

from wcl_api import WCLClient, WCLAPIError
import log_parser
import death_analyzer
import healing_analyzer
import damage_analyzer


def main():
    if len(sys.argv) < 2:
        print("Usage: python example_usage.py <report_code>")
        sys.exit(1)

    report_code = sys.argv[1]

    client_id = os.environ.get("WCL_CLIENT_ID")
    client_secret = os.environ.get("WCL_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("Set WCL_CLIENT_ID and WCL_CLIENT_SECRET environment variables.")
        sys.exit(1)

    client = WCLClient(client_id, client_secret)

    try:
        raw_report = client.get_report_fights(report_code)
    except WCLAPIError as exc:
        print(f"Error fetching report: {exc}")
        sys.exit(1)

    print(f"Report: {raw_report['title']}  ({raw_report['zone']['name']})")
    print(f"Fights: {len(raw_report['fights'])}")
    for fight in raw_report["fights"][:5]:
        status = "KILL" if fight["kill"] else "wipe"
        print(f"  #{fight['id']:>3}  {fight['name']:<30} {status}")

    if not raw_report["fights"]:
        return

    first_fight_id = raw_report["fights"][0]["id"]
    raw_fight = next(f for f in raw_report["fights"] if f["id"] == first_fight_id)

    raw_master_data = client.get_report_master_data(report_code)
    raw_events_by_type = client.get_report_events_multi(
        report_code,
        first_fight_id,
        event_types=["Casts", "Healing", "DamageTaken", "Deaths"],
    )

    parsed = log_parser.parse_fight_bundle(raw_fight, raw_master_data, raw_events_by_type)

    print(f"\nParsed fight: {parsed.fight.name} "
          f"({'kill' if parsed.fight.kill else 'wipe'}, "
          f"{parsed.fight.duration_ms / 1000:.1f}s)")
    print(f"Actors: {len(parsed.actors)}   Abilities: {len(parsed.abilities)}   "
          f"Events: {len(parsed.events)}")

    print("\nFirst 10 parsed events:")
    for event in parsed.events[:10]:
        print(
            f"  [{event.timestamp:>7}] {event.event_type:<12} "
            f"{event.source_name or '?':<20} -> {event.target_name or '?':<20} "
            f"{event.ability_name or '':<25} amount={event.amount}"
        )

    print(f"\nRate limit: {client.rate_limit.points_spent_this_hour} / "
          f"{client.rate_limit.limit_per_hour} points this hour")

    death_reports = death_analyzer.analyze_deaths(parsed)
    print("\n--- Death analysis ---")
    print(death_analyzer.summarize_wipe(parsed, death_reports))

    healer_summaries = healing_analyzer.analyze_healing(parsed)
    print("\n--- Healing analysis ---")
    print(healing_analyzer.summarize_healing(parsed, healer_summaries))

    damage_summaries = damage_analyzer.analyze_damage_taken(parsed)
    print("\n--- Damage taken analysis ---")
    print(damage_analyzer.summarize_damage_taken(parsed, damage_summaries))

    print("\n--- Biggest hits ---")
    for hit in damage_analyzer.get_biggest_hits(parsed, top_n=5):
        print(f"  {hit.amount:>10,}  {hit.ability_name or 'Unknown':<25} "
              f"on {hit.target_name or 'Unknown'}")


if __name__ == "__main__":
    main()
