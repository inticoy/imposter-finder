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


@dataclass(frozen=True)
class FcPlayer:
    name: str
    discord_user_id: str | None
    nickname: str


def load_fc_players(path: Path) -> list[FcPlayer]:
    """FC 온라인 닉네임이 등록된 친구만. 없으면 빈 목록."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        registry = json.load(f)
    return [
        FcPlayer(name=player["name"], discord_user_id=player.get("discord_user_id"),
                 nickname=player["accounts"]["fconline"]["nickname"])
        for player in registry.get("players", [])
        if player.get("accounts", {}).get("fconline", {}).get("nickname")
    ]


@dataclass(frozen=True)
class LolAccount:
    name: str
    discord_user_id: str | None
    riot_id: str  # "닉네임#태그"


def load_lol_accounts(path: Path) -> list[LolAccount]:
    """롤 계정 목록. 한 사람이 계정을 여러 개 가질 수 있다 (accounts.lol.riot_ids)."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        registry = json.load(f)
    return [
        LolAccount(name=player["name"], discord_user_id=player.get("discord_user_id"), riot_id=riot_id)
        for player in registry.get("players", [])
        for riot_id in player.get("accounts", {}).get("lol", {}).get("riot_ids", [])
    ]
