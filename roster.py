"""
roster.py

Identifies the actual PLAYERS in a fight, as distinct from NPCs, bosses,
and pets/summons -- and resolves a pet/summon actor back to the player
who owns it. Every analyzer that reports "who did X" should filter or
resolve through this module, so pets never show up as if they were
raid members in their own right.

Pure function over parsed data -- no network calls.
"""
from __future__ import annotations

from data_models import Actor, ParsedFight


def get_players(actors: dict[int, Actor]) -> dict[int, Actor]:
    """Return only the actors that are real players (excludes NPCs and pets)."""
    return {actor_id: actor for actor_id, actor in actors.items() if actor.type == "Player"}


def get_player_roster(parsed_fight: ParsedFight) -> dict[int, Actor]:
    """
    Convenience wrapper: get_players() straight from a ParsedFight.

    NOTE: this returns every player anywhere in the REPORT (masterData is
    report-wide), not necessarily everyone in THIS specific pull -- subs
    or people who left/joined partway through the raid will still show
    up. For an accurate per-fight roster, use get_fight_roster() instead.
    """
    return get_players(parsed_fight.actors)


def get_fight_roster(parsed_fight: ParsedFight) -> dict[int, Actor]:
    """
    Return only the players who were actually present in THIS specific
    fight/pull, using WCL's per-fight friendlyPlayers list.
    """
    present_ids = set(parsed_fight.fight.friendly_player_ids)
    return {
        actor_id: actor
        for actor_id, actor in parsed_fight.actors.items()
        if actor_id in present_ids and actor.type == "Player"
    }


def is_player(actor_id: int | None, actors: dict[int, Actor]) -> bool:
    """True if actor_id refers to a real player (not an NPC or pet)."""
    if actor_id is None:
        return False
    actor = actors.get(actor_id)
    return actor is not None and actor.type == "Player"


def resolve_to_player(
    actor_id: int | None, actors: dict[int, Actor]
) -> Actor | None:
    """
    Resolve any actor ID to the player it should be credited to:
      - a real player  -> returns that player's Actor
      - a pet/summon with a known owner -> returns the OWNER's Actor
      - an NPC, boss, or a pet with no resolvable owner -> returns None
    """
    if actor_id is None:
        return None
    actor = actors.get(actor_id)
    if actor is None:
        return None
    if actor.type == "Player":
        return actor
    if actor.owner_id is not None:
        owner = actors.get(actor.owner_id)
        if owner is not None and owner.type == "Player":
            return owner
    return None


def resolve_to_player_id_name(
    actor_id: int | None, fallback_name: str | None, actors: dict[int, Actor]
) -> tuple[int | None, str | None]:
    """
    Same resolution as resolve_to_player, but returns an (id, name) tuple
    instead of an Actor, falling back to (actor_id, fallback_name) when
    resolution fails.
    """
    player = resolve_to_player(actor_id, actors)
    if player is not None:
        return player.id, player.name
    return actor_id, fallback_name
