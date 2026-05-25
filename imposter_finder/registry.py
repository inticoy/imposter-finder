from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PubgPlayer:
    name: str
    discord_user_id: str | None
    platform: str
    nickname: str


def load_pubg_players(path: Path) -> list[PubgPlayer]:
    if not path.exists():
        raise RuntimeError(f"{path} does not exist. Create it from players.example.json first.")

    with path.open("r", encoding="utf-8") as f:
        registry = json.load(f)

    players: list[PubgPlayer] = []
    for player in registry.get("players", []):
        account = player.get("accounts", {}).get("pubg")
        if not account or not account.get("nickname"):
            continue

        players.append(
            PubgPlayer(
                name=player["name"],
                discord_user_id=player.get("discord_user_id"),
                platform=account.get("platform") or "steam",
                nickname=account["nickname"],
            )
        )

    if not players:
        raise RuntimeError("No PUBG players found in players.json")
    return players
