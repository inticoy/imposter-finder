from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from math import sqrt
from pathlib import Path
from typing import Any

from imposter_finder.registry import PubgPlayer


ROOT_DIR = Path(__file__).resolve().parents[2]


@dataclass
class PlayerScore:
    player: PubgPlayer
    pubg_name: str
    stats: dict[str, Any]
    rating: float = 3.0
    reasons: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    team_damage: float = 0
    team_kills: int = 0

    def adjust(self, delta: float, reason: str, tag: str) -> None:
        self.rating += delta
        sign = "+" if delta > 0 else ""
        self.reasons.append(f"{sign}{delta:.1f} {reason}")
        self.tags.append(tag)

    def finalize(self) -> None:
        self.rating = max(0.0, min(5.0, round(self.rating, 1)))


def analyze_pubg_match(
    platform: str,
    match: dict[str, Any],
    telemetry: list[dict[str, Any]],
    registered_players: list[PubgPlayer],
) -> dict[str, Any]:
    participants = _participants_by_name(match)
    registered_by_nick = {p.nickname.lower(): p for p in registered_players if p.platform == platform}
    scores: dict[str, PlayerScore] = {}

    for nickname, player in registered_by_nick.items():
        stats = participants.get(nickname)
        if not stats:
            continue
        scores[nickname] = PlayerScore(player=player, pubg_name=stats.get("name") or player.nickname, stats=stats)

    if not scores:
        return {
            "culprit": None,
            "scores": {},
        }

    duration = match.get("data", {}).get("attributes", {}).get("duration")
    _score_match_stats(scores, float(duration) / 60 if duration is not None else None)
    _score_telemetry(scores, telemetry)

    for score in scores.values():
        score.finalize()

    ranked = sorted(scores.values(), key=_ranking_key)
    lowest_rating = ranked[0].rating if ranked else 0
    culprits = [item for item in ranked if item.rating == lowest_rating]
    culprit = culprits[0] if culprits else None
    return {
        "culprit": culprit.player.name if culprit else None,
        "scores": {
            item.player.name: {
                "pubg_name": item.pubg_name,
                "rating": item.rating,
                "reasons": item.reasons,
                "tags": item.tags,
                "team_damage": round(item.team_damage),
                "team_kills": item.team_kills,
            }
            for item in ranked
        },
    }


def _ranking_key(item: PlayerScore) -> tuple[float, int, float, float]:
    stats = item.stats
    first_death_penalty = 0 if "first_death" in item.tags else 1
    damage = float(stats.get("damageDealt") or 0)
    survived = float(stats.get("timeSurvived") or 0)
    return (item.rating, first_death_penalty, damage, survived)


def _participants_by_name(match: dict[str, Any]) -> dict[str, dict[str, Any]]:
    participants: dict[str, dict[str, Any]] = {}
    for item in match.get("included", []):
        if item.get("type") != "participant":
            continue
        stats = item.get("attributes", {}).get("stats", {})
        name = stats.get("name")
        if name:
            participants[name.lower()] = stats
    return participants


def _score_match_stats(scores: dict[str, PlayerScore], duration_minutes: float | None) -> None:
    damages = [float(score.stats.get("damageDealt") or 0) for score in scores.values()]
    avg_damage = sum(damages) / len(damages) if damages else 0
    max_damage = max(damages) if damages else 0
    survivals = [float(score.stats.get("timeSurvived") or 0) for score in scores.values()]
    avg_survival = sum(survivals) / len(survivals) if survivals else 0
    min_survival = min(survivals) if survivals else 0
    lowest_damage = min(damages) if damages else 0

    for score in scores.values():
        stats = score.stats
        damage = float(stats.get("damageDealt") or 0)
        kills = int(stats.get("kills") or 0)
        dbnos = int(stats.get("DBNOs") or 0)
        assists = int(stats.get("assists") or 0)
        revives = int(stats.get("revives") or 0)
        survived = float(stats.get("timeSurvived") or 0)
        survived_minutes = survived / 60

        contribution = kills + dbnos + assists

        if damage == 0 and survived_minutes >= 2:
            score.adjust(-0.8, "0딜", "zero_damage")
        elif damage == lowest_damage and len(scores) >= 2 and avg_damage >= 80 and damage <= avg_damage * 0.45:
            score.adjust(-0.6, f"친구 중 딜량 최저 ({damage:.0f} / 평균 {avg_damage:.0f})", "lowest_damage")
        elif survived_minutes >= 3 and damage < 75:
            score.adjust(-0.4, f"생존 시간 대비 딜량이 낮음 ({damage:.0f}딜, {survived_minutes:.1f}분)", "low_damage")

        if max_damage >= 200 and kills == 0 and dbnos == 0 and damage < avg_damage * 0.6:
            score.adjust(-0.4, "킬/기절 없이 교전 기여가 낮음", "low_impact")

        if assists == 0 and revives == 0 and len(scores) >= 3 and duration_minutes and duration_minutes >= 10:
            score.adjust(-0.2, "장기전에서 어시스트/부활 기록이 없음", "no_support")

        if survived == min_survival and len(scores) >= 2 and avg_survival >= 300 and survived < avg_survival * 0.65:
            score.adjust(-0.5, f"친구 중 가장 먼저 이탈 ({survived_minutes:.1f}분 / 평균 {avg_survival / 60:.1f}분)", "short_survival")

        if damage == max_damage and len(scores) >= 2 and max_damage >= 100:
            score.adjust(0.5, f"친구 중 딜량 1위 ({damage:.0f})", "top_damage")
        if damage >= 500:
            score.adjust(0.7, f"500딜 이상 ({damage:.0f})", "big_damage")
        elif damage >= 300:
            score.adjust(0.4, f"300딜 이상 ({damage:.0f})", "good_damage")

        kill_bonus = min(kills * 0.35, 1.0)
        if kill_bonus:
            score.adjust(kill_bonus, f"{kills}킬", "kills")
        dbno_bonus = min(dbnos * 0.25, 0.75)
        if dbno_bonus:
            score.adjust(dbno_bonus, f"{dbnos}기절", "dbnos")
        assist_bonus = min(assists * 0.15, 0.45)
        if assist_bonus:
            score.adjust(assist_bonus, f"{assists}어시스트", "assists")
        revive_bonus = min(revives * 0.35, 0.7)
        if revive_bonus:
            score.adjust(revive_bonus, f"{revives}부활", "revives")

        if contribution == 0 and damage < 100 and survived_minutes >= 5:
            score.adjust(-0.4, "5분 이상 생존했지만 교전 기록이 거의 없음", "empty_presence")


def _score_telemetry(scores: dict[str, PlayerScore], telemetry: list[dict[str, Any]]) -> None:
    if not telemetry:
        return

    first_groggy = _first_registered_event_player(telemetry, {"LogPlayerMakeGroggy"}, scores, victim=True)
    if first_groggy and first_groggy in scores:
        scores[first_groggy].adjust(-0.6, "등록된 친구 중 첫 기절", "first_knock")

    first_death = _first_registered_event_player(telemetry, {"LogPlayerKill", "LogPlayerKillV2"}, scores, victim=True)
    if first_death and first_death in scores:
        scores[first_death].adjust(-0.8, "등록된 친구 중 첫 사망", "first_death")

    team_damage: dict[str, float] = {}
    team_kills: dict[str, int] = {}
    latest_positions: dict[str, tuple[float, float]] = {}
    death_positions: dict[str, tuple[float, float]] = {}

    for event in sorted(telemetry, key=_event_time):
        event_type = event.get("_T")
        for character in _event_characters(event):
            name = (character.get("name") or "").lower()
            location = character.get("location") or {}
            if name in scores and "x" in location and "y" in location:
                latest_positions[name] = (float(location["x"]), float(location["y"]))

        if event_type == "LogPlayerTakeDamage":
            attacker = _character_name(event.get("attacker"))
            victim = _character_name(event.get("victim"))
            damage = float(event.get("damage") or 0)
            if attacker in scores and victim in scores and attacker != victim:
                team_damage[attacker] = team_damage.get(attacker, 0) + damage

        if event_type in {"LogPlayerKill", "LogPlayerKillV2"}:
            killer = _character_name(event.get("killer"))
            victim = _character_name(event.get("victim"))
            victim_location = (event.get("victim") or {}).get("location") or {}
            if victim in scores and "x" in victim_location and "y" in victim_location:
                death_positions[victim] = (float(victim_location["x"]), float(victim_location["y"]))
            if killer in scores and victim in scores and killer != victim:
                team_kills[killer] = team_kills.get(killer, 0) + 1

    for name, damage in team_damage.items():
        if damage >= 30:
            scores[name].team_damage = damage
            scores[name].adjust(-1.0, f"팀원에게 피해를 줌 ({damage:.0f})", "team_damage")
    for name, kills in team_kills.items():
        scores[name].team_kills = kills
        scores[name].adjust(-2.0, f"팀킬 기록 ({kills}회)", "team_kill")

    for name, death_position in death_positions.items():
        teammate_positions = [pos for other, pos in latest_positions.items() if other != name]
        if not teammate_positions:
            continue
        nearest = min(_distance_m(death_position, pos) for pos in teammate_positions)
        if nearest >= 150:
            scores[name].adjust(-0.6, f"사망 시 팀과 떨어져 있었음 (가장 가까운 팀원 약 {nearest:.0f}m)", "isolated_death")


def _first_registered_event_player(
    telemetry: list[dict[str, Any]],
    event_types: set[str],
    scores: dict[str, PlayerScore],
    victim: bool,
) -> str | None:
    key = "victim" if victim else "character"
    for event in sorted(telemetry, key=_event_time):
        if event.get("_T") not in event_types:
            continue
        name = _character_name(event.get(key))
        if name in scores:
            return name
    return None


def _event_characters(event: dict[str, Any]) -> list[dict[str, Any]]:
    characters = []
    for key in ("character", "attacker", "victim", "killer", "assistant"):
        value = event.get(key)
        if isinstance(value, dict):
            characters.append(value)
    return characters


def _character_name(character: dict[str, Any] | None) -> str | None:
    if not character:
        return None
    name = character.get("name")
    return name.lower() if name else None


def _event_time(event: dict[str, Any]) -> datetime:
    raw = event.get("_D") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) / 100


def _pubg_map_name(map_id: str) -> str:
    maps = _load_pubg_maps()
    return maps.get(map_id, map_id)


def _load_pubg_maps() -> dict[str, str]:
    path = ROOT_DIR / "data" / "pubg_maps.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)
