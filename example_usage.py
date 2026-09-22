"""
example_usage.py

Standalone smoke test wiring wcl_api.py + log_parser.py together --
not part of the app's real module chain, just a sanity check for
these two pieces while developing/debugging. main.py is the real
entry point for normal use.

Set up credentials via a .env file, then run:
    python example_usage.py <report_code>
"""
import sys

from wcl_api import WCLClient, WCLAPIError
import config
import log_parser
import death_analyzer
import healing_analyzer
import damage_analyzer
import damage_done_analyzer
import cooldown_analyzer
import raid_cooldowns_config
import consumables_analyzer
import consumable_data
import gear_analyzer
import avoidable_damage_analyzer
import boss_mechanics_config
import report


def main():
    if len(sys.argv) < 2:
        print("Usage: python example_usage.py <report_code>")
        sys.exit(1)

    report_code = sys.argv[1]

    try:
        client_id, client_secret = config.get_credentials()
    except RuntimeError as exc:
        print(exc)
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
        event_types=["Casts", "Healing", "DamageTaken", "DamageDone", "Deaths", "CombatantInfo", "Debuffs"],
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
    healer_summaries = healing_analyzer.analyze_healing(parsed)
    damage_taken_summaries = damage_analyzer.analyze_damage_taken(parsed)
    biggest_hits = damage_analyzer.get_biggest_hits(parsed, top_n=5)
    damage_done_summaries = damage_done_analyzer.analyze_damage_done(parsed)
    cooldown_usages = cooldown_analyzer.analyze_cooldown_usage(
        parsed, raid_cooldowns_config.TRACKED_COOLDOWNS
    )
    consumable_results = consumables_analyzer.analyze_consumables(
        parsed, consumable_data.ALL_TRACKED_CONSUMABLES
    )
    mandatory_categories = consumable_data.MANDATORY_CATEGORIES
    vantus_check = consumables_analyzer.check_vantus_rune(
        parsed, consumable_data.VANTUS_RUNE_NAME
    )
    gear_reports = gear_analyzer.analyze_gear(parsed)
    avoidable_config = avoidable_damage_analyzer.get_encounter_config(
        parsed.fight.encounter_id, boss_mechanics_config.ENCOUNTERS
    )
    avoidable_reports = avoidable_damage_analyzer.analyze_avoidable_damage(
        parsed, boss_mechanics_config.ENCOUNTERS
    )

    report_data = report.FightReportData(
        parsed_fight=parsed,
        death_reports=death_reports,
        healer_summaries=healer_summaries,
        damage_taken_summaries=damage_taken_summaries,
        biggest_hits=biggest_hits,
        damage_done_summaries=damage_done_summaries,
        cooldown_usages=cooldown_usages,
        consumable_results=consumable_results,
        consumable_categories=mandatory_categories,
        vantus_check=vantus_check,
        gear_reports=gear_reports,
        avoidable_reports=avoidable_reports,
        avoidable_config=avoidable_config,
    )

    print("\n" + report.render_text(report_data))
    text_path, md_path = report.write_report(report_data, ".", f"fight_{first_fight_id}_report")
    print(f"\nReport written to {text_path} and {md_path}")


if __name__ == "__main__":
    main()
