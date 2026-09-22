"""player_roles.py -- converts WCL's tank/healer/dps playerDetails grouping into a per-player role lookup."""
from __future__ import annotations

from dataclasses import dataclass

import role_reference_data


@dataclass
class PlayerRole:
    player_id: int
    player_name: str
    role: str  # "tank", "healer", "melee", "ranged"
    spec_icon: str | None = None


def _classify(entry: dict, bucket_role: str) -> str:
    spec_icon = entry.get("icon")
    if spec_icon in role_reference_data.TANK_SPECS:
        return "tank"
    if spec_icon in role_reference_data.HEALER_SPECS:
        return "healer"
    if bucket_role == "tank":
        return "tank"
    if bucket_role == "healer":
        return "healer"
    return role_reference_data.classify_dps_spec(spec_icon)


def parse_player_roles(raw_player_details: dict) -> dict[int, PlayerRole]:
    roles: dict[int, PlayerRole] = {}
    for entry in raw_player_details.get("tanks", []) or []:
        spec_icon = entry.get("icon")
        roles[entry["id"]] = PlayerRole(
            player_id=entry["id"], player_name=entry.get("name", "Unknown"),
            role=_classify(entry, "tank"), spec_icon=spec_icon,
        )
    for entry in raw_player_details.get("healers", []) or []:
        spec_icon = entry.get("icon")
        roles[entry["id"]] = PlayerRole(
            player_id=entry["id"], player_name=entry.get("name", "Unknown"),
            role=_classify(entry, "healer"), spec_icon=spec_icon,
        )
    for entry in raw_player_details.get("dps", []) or []:
        spec_icon = entry.get("icon")
        roles[entry["id"]] = PlayerRole(
            player_id=entry["id"], player_name=entry.get("name", "Unknown"),
            role=_classify(entry, "dps"), spec_icon=spec_icon,
        )
    return roles
