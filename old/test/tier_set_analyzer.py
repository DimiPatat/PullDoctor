"""
tier_set_analyzer.py
Reports on tier-set completion per player, using the same CombatantInfo
snapshot gear_analyzer.py already reads. Independent of gear_analyzer.py
(reads raw combatant_info directly) so it can run standalone.

For each player, checks their five tier-set slots (Head, Shoulder,
Chest, Gloves, Legs -- see tier_set_schema.TIER_SET_SLOT_ORDER) against
TRACKED_TIER_SET_PIECES (keyed by the player's class + slot). Produces:
  - pieces_worn: how many of the 5 slots are a tracked tier item (0-5).
  - track_string: a 5-character string, ONE CHAR PER SLOT in
    Head/Shoulder/Chest/Gloves/Legs order. Each char is either the
    slot's item-level TRACK LETTER (A/V/C/H/M) if that slot IS a
    tracked tier piece, or "_" if it is not (either a non-tier item
    equipped there, or no CombatantInfo data for that slot at all).
    e.g. "MMHCC" = Myth Head, Myth Shoulder, Hero Chest, Champion
    Gloves, Champion Legs. "MM_H_" = Myth Head, Myth Shoulder, NOT a
    tier Chest, Hero Gloves, NOT a tier Legs.

IMPORTANT LIMITATION (same one gear_analyzer.py documents): this app
has no item database, so TRACKED_TIER_SET_PIECES is only ever what
YOU have explicitly confirmed via manage_tier_sets.py -- an empty
tracked list means every player shows 0/5 and "_____", not because
they aren't wearing tier gear, but because nothing has been configured
to recognize it yet.

Pure function over a ParsedFight -- no network calls.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import roster
from tier_set_data import TRACKED_TIER_SET_PIECES, track_letter_for_item_level
from tier_set_schema import MISSING_TIER_PIECE_CHAR, TIER_SET_SLOT_ORDER, TIER_SET_SLOTS

TOTAL_TIER_SLOTS = len(TIER_SET_SLOT_ORDER)


@dataclass
class PlayerTierSetReport:
    player_id: int | None
    player_name: str | None
    class_name: str | None
    has_data: bool = True
    # slot -> track_letter, only for slots that ARE a tracked tier piece.
    track_letters_by_slot: dict[int, str] = field(default_factory=dict)

    @property
    def pieces_worn(self) -> int:
        return len(self.track_letters_by_slot)

    @property
    def track_string(self) -> str:
        """5-char string in TIER_SET_SLOT_ORDER, '_' for any slot that isn't a tracked tier piece."""
        return "".join(
            self.track_letters_by_slot.get(slot, MISSING_TIER_PIECE_CHAR)
            for slot in TIER_SET_SLOT_ORDER
        )


def _pieces_by_class_and_slot() -> dict[tuple[str, int], int]:
    """(class_name, slot) -> item_id, built fresh each call so a live-reloaded TRACKED_TIER_SET_PIECES is always respected."""
    return {(p.class_name, p.slot): p.item_id for p in TRACKED_TIER_SET_PIECES}


def analyze_tier_sets(parsed_fight) -> list[PlayerTierSetReport]:
    """
    Build a PlayerTierSetReport for every player in the fight roster.
    Players with no CombatantInfo snapshot still get an entry, marked
    has_data=False, rather than being silently omitted (same convention
    as gear_analyzer.analyze_gear).
    SORTING: results are sorted by pieces_worn ASCENDING (fewest tier
    pieces first), so the player(s) most behind on tier gear surface at
    the top -- same "most worth a raid lead's attention first" ordering
    gear_analyzer.py already uses for item level. Players with no data
    at all sort to the very end (see gear_analyzer.py's identical
    reasoning: "unknown" isn't the same as "actually has 0 tier pieces").
    Ties broken by player name for stability.
    """
    fight_roster = roster.get_fight_roster(parsed_fight)
    pieces_by_key = _pieces_by_class_and_slot()
    reports: list[PlayerTierSetReport] = []

    for player_id, actor in fight_roster.items():
        class_name = getattr(actor, "subtype", None)
        snapshot = parsed_fight.combatant_info.get(player_id)
        if snapshot is None:
            reports.append(PlayerTierSetReport(
                player_id=player_id, player_name=actor.name, class_name=class_name, has_data=False,
            ))
            continue

        gear_by_slot = {g.slot: g for g in snapshot.gear}
        track_letters_by_slot: dict[int, str] = {}
        for slot in TIER_SET_SLOT_ORDER:
            gear_item = gear_by_slot.get(slot)
            if gear_item is None:
                continue
            tracked_item_id = pieces_by_key.get((class_name, slot))
            if tracked_item_id is None or gear_item.item_id != tracked_item_id:
                continue
            letter = track_letter_for_item_level(gear_item.item_level)
            if letter is not None:
                track_letters_by_slot[slot] = letter

        reports.append(PlayerTierSetReport(
            player_id=player_id, player_name=actor.name, class_name=class_name,
            track_letters_by_slot=track_letters_by_slot,
        ))

    return sorted(
        reports,
        key=lambda r: (not r.has_data, r.pieces_worn if r.has_data else 0, r.player_name or ""),
    )


def summarize_tier_sets(reports: list[PlayerTierSetReport]) -> str:
    """Return a short human-readable summary, in the same order as `reports` (fewest tier pieces first)."""
    if not reports:
        return "No tier-set data available (no CombatantInfo snapshots for this fight)."
    if not TRACKED_TIER_SET_PIECES:
        return (
            "Tier-set check: no tier pieces are configured yet -- run "
            "`python explore_tier_sets.py` and `python manage_tier_sets.py add` "
            "to start tracking your roster's tier sets."
        )
    lines = [f"Tier-set check ({TOTAL_TIER_SLOTS} slots: {', '.join(TIER_SET_SLOTS[s] for s in TIER_SET_SLOT_ORDER)}):"]
    for report in reports:
        name = (report.player_name or "Unknown")[:15]
        if not report.has_data:
            lines.append(f"  {name:<15} no gear data (no CombatantInfo snapshot)")
            continue
        lines.append(f"  {name:<15} {report.pieces_worn}/{TOTAL_TIER_SLOTS}  [{report.track_string}]")
    return "\n".join(lines)
