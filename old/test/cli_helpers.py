"""
cli_helpers.py

Small shared helpers for CLI scripts that need a person to pick a fight
from a report. Not an analyzer -- plain I/O helpers.

print_fight_list()/prompt_for_fight_id()/resolve_fight_id() now accept
an optional `all_raw_fights` -- the FULL unfiltered fight list -- so
that when `fights` has already been filtered (via fight_filters.py),
the displayed numbering can still show a short note like "(3 trash/
short pulls hidden)" rather than silently renumbering fights in a way
that might confuse someone cross-referencing against the Warcraft Logs
website's own fight list. This is purely a display nicety; the
filtering decision itself always happens in fight_filters.py, before
these functions are ever called.
"""
from __future__ import annotations


def print_fight_list(fights: list[dict], all_raw_fights: list[dict] | None = None) -> None:
    for fight in fights:
        status = "KILL" if fight["kill"] else "wipe"
        duration_s = (fight["endTime"] - fight["startTime"]) / 1000
        print(f"  #{fight['id']:>3}  {fight['name']:<30} {status}  ({duration_s:.0f}s)")
    if all_raw_fights is not None and len(all_raw_fights) > len(fights):
        hidden = len(all_raw_fights) - len(fights)
        print(f"  ({hidden} trash/short pull(s) hidden -- see --include-trash / --min-duration to change this)")


def prompt_for_fight_id(fights: list[dict], all_raw_fights: list[dict] | None = None) -> int:
    print_fight_list(fights, all_raw_fights=all_raw_fights)
    valid_ids = {f["id"] for f in fights}
    while True:
        choice = input("\nWhich fight number? ").strip()
        if choice.isdigit() and int(choice) in valid_ids:
            return int(choice)
        print(f"'{choice}' isn't one of the fight numbers listed above -- try again.")


def resolve_fight_id(
    fights: list[dict],
    explicit_fight_id: int | None,
    all_raw_fights: list[dict] | None = None,
) -> int:
    """
    Return explicit_fight_id if it's valid for this (possibly filtered)
    fight list; otherwise prompt interactively. If explicit_fight_id
    was filtered OUT by fight_filters.py (e.g. it's a trash pull or a
    too-short pull) but genuinely exists in the report, this gives a
    clear explanation rather than a bare "not found" -- since that's a
    likely point of confusion once fight-filtering is involved.
    """
    valid_ids = {f["id"] for f in fights}
    if explicit_fight_id is not None:
        if explicit_fight_id not in valid_ids:
            if all_raw_fights is not None:
                raw_match = next((f for f in all_raw_fights if f["id"] == explicit_fight_id), None)
                if raw_match is not None:
                    duration_s = (raw_match["endTime"] - raw_match["startTime"]) / 1000
                    is_trash = (raw_match.get("encounterID", 0) or 0) == 0
                    reason = "it's a trash pull" if is_trash else f"it's only {duration_s:.0f}s long"
                    print(
                        f"Fight #{explicit_fight_id} exists in this report, but was filtered out "
                        f"because {reason}. Use --include-trash and/or --min-duration 0 to include it."
                    )
                    raise SystemExit(1)
            print(f"Fight #{explicit_fight_id} not found in this report. Available fights:")
            print_fight_list(fights, all_raw_fights=all_raw_fights)
            raise SystemExit(1)
        return explicit_fight_id
    return prompt_for_fight_id(fights, all_raw_fights=all_raw_fights)
