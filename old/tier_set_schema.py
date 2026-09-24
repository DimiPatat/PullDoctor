"""
tier_set_schema.py
Just the TierSetPieceDefinition / TierTrackBreakpoint dataclasses --
kept in their own module with NO other project dependencies, same
philosophy as gear_schema.py.

Tier set pieces are keyed by (class_name, slot) -> item_id: exactly
FIVE armor slots make up a class's tier set (Head, Shoulder, Chest,
Gloves, Legs), same convention Blizzard has used every tier for years.

IMPORTANT LIMITATION (same one gear_analyzer.py already documents):
this app has no item database. tier_set_data.py does NOT ship with a
hardcoded item_id catalog -- it can't verify one, the same way it
can't verify enchant/gem IDs. Instead, TRACKED_TIER_SET_PIECES starts
EMPTY and gets populated by YOU, via tier_set_explorer.py's heuristic
"candidate" detection (multiple same-class players sharing an
identical item_id in a tier slot) + manage_tier_sets.py's add/remove
CLI -- exactly the same discover-then-confirm workflow already used
for defensive cooldowns and gear compliance.
"""
from __future__ import annotations
from dataclasses import dataclass

# The five armor slots that make up a tier set, and their Blizzard
# inventory slot indices (same indices gear_analyzer.py's
# ENCHANTABLE_SLOTS already uses for Head/Shoulder/Chest/Legs; Gloves
# -- slot 9 -- is the one additional slot tier sets cover that enchant
# tracking doesn't).
TIER_SET_SLOTS: dict[int, str] = {
    0: "Head", 2: "Shoulder", 4: "Chest", 9: "Gloves", 6: "Legs",
}

# Canonical DISPLAY order for the 5-character track string (e.g.
# "MMHCC"): Head, Shoulder, Chest, Gloves, Legs -- a plain dict's
# iteration order happens to already match this by insertion, but this
# tuple is the explicit, intentional contract other modules should
# import and rely on rather than assuming dict ordering.
TIER_SET_SLOT_ORDER: tuple[int, ...] = (0, 2, 4, 9, 6)

# Placeholder character shown for a tier slot the player has EQUIPPED
# something in, but that item is NOT one of the tracked tier-set piece
# IDs for their class -- i.e. "not wearing a tier piece in this slot",
# as distinct from having a slot literally empty (which normally can't
# happen for real players in a raid, but is handled the same way).
MISSING_TIER_PIECE_CHAR = "_"


@dataclass
class TierSetPieceDefinition:
    """
    One tracked tier-set item: for THIS class, wearing item_id in slot
    counts as a tier piece.
    """
    class_name: str
    slot: int  # must be a key in TIER_SET_SLOTS
    item_id: int
    notes: str | None = None

    @property
    def slot_name(self) -> str:
        return TIER_SET_SLOTS.get(self.slot, f"Slot {self.slot}")


@dataclass
class TierTrackBreakpoint:
    """
    One item-track's item-level floor: an item at or above
    min_item_level (and below the NEXT breakpoint's min_item_level) is
    considered to be on this track. track_letter is a single uppercase
    character used in the 5-char per-player track string (e.g. "M" for
    Myth); color_hex is the color that letter renders in, in the HTML
    report.
    """
    min_item_level: float
    track_letter: str
    track_name: str
    color_hex: str
