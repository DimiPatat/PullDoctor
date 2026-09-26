"""
tier_set_explorer.py
Scans a REAL parsed fight's CombatantInfo snapshots to find likely
tier-set item candidates, since this app has no item database and
can't be handed a pre-built item_id catalog to verify against (see
tier_set_schema.py's docstring). Mirrors the discovery pattern already
used in defensive_cooldown_explorer.py: flag candidates for a human to
confirm/add, never auto-add anything.

HEURISTIC: for each of the 5 tier slots, group the item_id every player
of a given class is wearing in that slot. If 2+ players of the SAME
class share an IDENTICAL item_id in that slot, AND it isn't already in
TRACKED_TIER_SET_PIECES, flag it as a candidate -- tier-set items are
class-specific by design, so seeing the same item repeat across same-
class players in a tier slot is a strong (though not 100% certain)
signal.

CAVEAT: this can produce a false positive if two players of the same
class happen to share a non-tier drop in a tier slot by coincidence
(rare, but possible with only 2 players of a class in a small roster).
It can also MISS a real tier piece if your raid only ever had ONE
player of a given class equip it during the scanned fight(s) -- run
this across a few different fights/reports to build higher confidence
before committing an item_id via manage_tier_sets.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import roster
from tier_set_data import TRACKED_TIER_SET_PIECES, TIER_SET_NAMES_BY_CLASS
from tier_set_schema import TIER_SET_SLOT_ORDER, TIER_SET_SLOTS


@dataclass
class CandidateTierPiece:
    class_name: str
    slot: int
    item_id: int
    num_players_seen: int
    example_item_levels: list[int] = field(default_factory=list)

    @property
    def slot_name(self) -> str:
        return TIER_SET_SLOTS.get(self.slot, f"Slot {self.slot}")

    @property
    def likely_set_name(self) -> str | None:
        return TIER_SET_NAMES_BY_CLASS.get(self.class_name)


def discover_candidate_tier_pieces(parsed_fight, min_players_sharing: int = 2) -> list[CandidateTierPiece]:
    """
    Scan one parsed fight for candidate tier-set item IDs. Returns
    candidates sorted by (class_name, slot, -num_players_seen) so
    results group naturally by class when printed.
    min_players_sharing: how many same-class players must share an
    identical item_id in a slot before it's flagged (default 2 --
    lower this to 1 only if you're checking a single-player log and
    are willing to accept more false positives).
    """
    already_tracked = {(p.class_name, p.slot) for p in TRACKED_TIER_SET_PIECES}
    fight_roster = roster.get_fight_roster(parsed_fight)

    # (class_name, slot, item_id) -> list of item_levels seen
    seen: dict[tuple[str, int, int], list[int]] = {}
    for player_id, actor in fight_roster.items():
        class_name = getattr(actor, "subtype", None)
        if class_name is None:
            continue
        snapshot = parsed_fight.combatant_info.get(player_id)
        if snapshot is None:
            continue
        gear_by_slot = {g.slot: g for g in snapshot.gear}
        for slot in TIER_SET_SLOT_ORDER:
            gear_item = gear_by_slot.get(slot)
            if gear_item is None or not gear_item.item_id:
                continue
            key = (class_name, slot, gear_item.item_id)
            seen.setdefault(key, []).append(gear_item.item_level or 0)

    candidates: list[CandidateTierPiece] = []
    for (class_name, slot, item_id), item_levels in seen.items():
        if (class_name, slot) in already_tracked:
            continue
        if len(item_levels) < min_players_sharing:
            continue
        candidates.append(CandidateTierPiece(
            class_name=class_name, slot=slot, item_id=item_id,
            num_players_seen=len(item_levels), example_item_levels=sorted(set(item_levels)),
        ))

    return sorted(candidates, key=lambda c: (c.class_name, c.slot, -c.num_players_seen))


def format_candidates(candidates: list[CandidateTierPiece]) -> str:
    if not candidates:
        return "No tier-set candidates found in this fight (or everything found is already tracked)."
    lines = [f"Found {len(candidates)} candidate tier-set piece(s):"]
    for c in candidates:
        set_hint = f"  (likely: {c.likely_set_name})" if c.likely_set_name else ""
        lines.append(
            f"  {c.class_name:<14} {c.slot_name:<9} item_id={c.item_id:<10} "
            f"seen on {c.num_players_seen} player(s), ilvl {c.example_item_levels}{set_hint}"
        )
    lines.append("\nRun `python manage_tier_sets.py add` to confirm and start tracking any of these.")
    return "\n".join(lines)
