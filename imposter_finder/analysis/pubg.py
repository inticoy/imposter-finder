from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
            "message": "[범인찾기] PUBG 매치 분석 실패\n등록된 친구가 match detail participant 목록에 없습니다.",
            "scores": {},
        }

    match_meta = _match_meta(platform, match, ranked=None)
    _score_match_stats(scores, match_meta["duration_minutes"])
    _score_telemetry(scores, telemetry)

    for score in scores.values():
        score.finalize()

    ranked = sorted(scores.values(), key=_ranking_key)
    lowest_rating = ranked[0].rating if ranked else 0
    culprits = [item for item in ranked if item.rating == lowest_rating]
    culprit = culprits[0] if culprits else None
    match_meta = _match_meta(platform, match, ranked)
    message = _format_report(platform, match, ranked, culprits)
    embed = _format_embed(match_meta, ranked, culprits)

    return {
        "culprit": culprit.player.name if culprit else None,
        "message": message,
        "thread_name": _thread_name(match_meta, culprits),
        "discord_payload": {
            "content": "",
            "embeds": [embed],
        },
        "scores": {
            item.player.name: {
                "pubg_name": item.pubg_name,
                "rating": item.rating,
                "reasons": item.reasons,
                "tags": item.tags,
                "stats": _compact_stats(item.stats),
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


def _match_meta(platform: str, match: dict[str, Any], ranked: list[PlayerScore] | None) -> dict[str, Any]:
    attrs = match.get("data", {}).get("attributes", {})
    duration_seconds = attrs.get("duration")
    duration_minutes = float(duration_seconds) / 60 if duration_seconds is not None else None
    started_at = attrs.get("createdAt")
    map_id = attrs.get("mapName") or "Unknown"
    match_id = match.get("data", {}).get("id")

    return {
        "match_id": match_id,
        "short_match_id": str(match_id or "")[:8],
        "platform": platform,
        "map_name": _pubg_map_name(map_id),
        "map_id": map_id,
        "game_mode": attrs.get("gameMode") or "Unknown",
        "created_at": started_at,
        "created_at_kst": _format_kst(started_at),
        "duration_minutes": duration_minutes,
        "duration_text": _format_minutes(duration_minutes),
        "registered_count": len(ranked or []),
    }


def pubg_match_label(match: dict[str, Any]) -> str:
    attrs = match.get("data", {}).get("attributes", {})
    map_id = attrs.get("mapName") or "Unknown"
    duration_seconds = attrs.get("duration")
    duration_minutes = float(duration_seconds) / 60 if duration_seconds is not None else None
    return (
        f"{_pubg_map_name(map_id)} · {attrs.get('gameMode') or 'Unknown'} · "
        f"{_format_minutes(duration_minutes)} · {_format_kst(attrs.get('createdAt'))}"
    )


def _format_embed(match_meta: dict[str, Any], ranked: list[PlayerScore], culprits: list[PlayerScore]) -> dict[str, Any]:
    culprit = culprits[0] if culprits else None
    rating = culprit.rating if culprit else 5.0
    color = 0xE74C3C if rating <= 1.5 else 0xE67E22 if rating <= 2.5 else 0xF1C40F if rating <= 3.5 else 0x2ECC71

    if culprit:
        title_name = ", ".join(item.player.name for item in culprits)
        summary_tags = _tag_summary(culprit)
        description = f"**{culprit.player.name}** · **{culprit.rating:.1f}/5** {stars(culprit.rating)}\n{summary_tags}"
    else:
        title_name = "후보 없음"
        description = "다들 무난했습니다."

    fields = [
        {
            "name": "매치",
            "value": (
                f"{match_meta['map_name']} · {match_meta['game_mode']} · "
                f"{match_meta['duration_text']} · #{match_meta['short_match_id']}\n"
                f"{match_meta['created_at_kst']}"
            ),
            "inline": False,
        },
        {
            "name": "친구별 요약",
            "value": _embed_scoreboard_lines(ranked),
            "inline": False,
        },
        {
            "name": "한 줄 요약",
            "value": _verdict(culprit),
            "inline": False,
        },
    ]

    return {
        "title": f"🕵🏻‍♂️ PUBG 범인찾기 🔎 {title_name}",
        "description": description,
        "color": color,
        "fields": fields,
    }


def _thread_name(match_meta: dict[str, Any], culprits: list[PlayerScore]) -> str:
    if culprits:
        names = ", ".join(item.player.name for item in culprits)
        return f"🕵🏻‍♂️ PUBG 범인찾기 🔎 {names}"
    return "🕵🏻‍♂️ PUBG 범인찾기 🔎 후보 없음"


def _embed_scoreboard_lines(ranked: list[PlayerScore]) -> str:
    lines = []
    for item in ranked[:8]:
        stats = _compact_stats(item.stats)
        extras = []
        if item.team_damage >= 30:
            extras.append(f"팀딜 {round(item.team_damage)}")
        if item.team_kills:
            extras.append(f"팀킬 {item.team_kills}")
        extra_text = f" · {' · '.join(extras)}" if extras else ""
        lines.append(
            f"**{item.player.name}** {item.rating:.1f} {stars(item.rating)} · "
            f"`{stats['kills']}K/{stats['assists']}A` · `{stats['damageText']}` · "
            f"`{stats['dbnoText']}` · `{stats['reviveText']}` · `{stats['survivalText']}`{extra_text}"
        )
    return _limit("\n".join(lines), 1000)


def _tag_summary(item: PlayerScore) -> str:
    tags = []
    tag_labels = [
        ("team_kill", "팀킬"),
        ("team_damage", "팀딜"),
        ("first_knock", "첫 기절"),
        ("first_death", "첫 사망"),
        ("zero_damage", "0딜"),
        ("lowest_damage", "딜 최저"),
        ("isolated_death", "고립 사망"),
        ("short_survival", "빠른 퇴장"),
        ("empty_presence", "존재감 없음"),
        ("low_impact", "교전 기여 낮음"),
        ("no_support", "팀플 없음"),
    ]
    for tag, label in tag_labels:
        if tag in item.tags:
            tags.append(label)
    return " · ".join(tags[:4]) if tags else "이번 판 최저 평점"


def _verdict(item: PlayerScore | None) -> str:
    if item is None:
        return "오늘은 딱히 잡아낼 사람이 없습니다."

    tags = set(item.tags)
    if "team_kill" in tags:
        return "팀킬까지 했습니다. 이건 변명의 여지가 없습니다."
    if "team_damage" in tags and "first_death" in tags:
        return "팀원도 때리고 먼저 죽었습니다. 오늘은 확실히 문제였습니다."
    if "team_damage" in tags:
        return "적보다 팀원을 더 아프게 했습니다."
    if {"first_knock", "first_death", "zero_damage"} <= tags:
        return "첫 기절, 첫 사망, 0딜. 오늘은 그냥 짐짝이었습니다."
    if {"first_knock", "first_death"} <= tags:
        return "시작부터 먼저 누워서 팀 흐름을 끊었습니다."
    if "isolated_death" in tags:
        return "혼자 떨어져 죽고 팀 흐름까지 망쳤습니다."
    if "zero_damage" in tags:
        return "살아있는 시간은 있었는데 딜이 없습니다. 흔적이 없습니다."
    if {"low_damage", "low_impact"} & tags:
        return "교전 기여가 바닥입니다. 숫자가 변명을 못 합니다."
    if "short_survival" in tags:
        return "팀보다 먼저 사라졌습니다. 존재감도 같이 사라졌습니다."
    if "no_support" in tags:
        return "킬도 없고 부활도 없습니다. 팀플 흔적이 없습니다."
    return "큰 사고는 없었지만, 이번 판 기여도는 제일 낮았습니다."


def stars(rating: float) -> str:
    filled = max(0, min(5, round(rating)))
    return "⭐" * filled + "🫥" * (5 - filled)


def _format_kst(raw: str | None) -> str:
    if not raw:
        return "알 수 없음"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    kst = dt.astimezone(timezone(timedelta(hours=9)))
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    return f"{kst.month}/{kst.day}({weekdays[kst.weekday()]}) {kst:%H:%M}"


def _format_minutes(minutes: float | None) -> str:
    if minutes is None:
        return "알 수 없음"
    return f"{round(minutes)}분"


def _format_short_minutes(minutes: float | None) -> str:
    if minutes is None:
        return "알 수 없음"
    return f"{round(minutes)}분"


def _limit(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"


def _pubg_map_name(map_id: str) -> str:
    maps = _load_pubg_maps()
    return maps.get(map_id, map_id)


def _load_pubg_maps() -> dict[str, str]:
    path = ROOT_DIR / "data" / "pubg_maps.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _format_report(
    platform: str,
    match: dict[str, Any],
    ranked: list[PlayerScore],
    culprits: list[PlayerScore],
) -> str:
    attrs = match.get("data", {}).get("attributes", {})
    match_id = match.get("data", {}).get("id")
    meta = _match_meta(platform, match, ranked)
    title = f"🕵🏻‍♂️ PUBG 범인찾기 🔎 {', '.join(item.player.name for item in culprits) if culprits else '후보 없음'}"
    lines = [
        title,
        "",
        f"매치: {match_id}",
        f"플랫폼: {platform}",
        f"맵/모드: {meta['map_name']} / {attrs.get('gameMode')}",
        f"시작: {meta['created_at_kst']}",
        f"게임 시간: {meta['duration_text']}",
        f"매치 ID: #{meta['short_match_id']}",
        "",
    ]

    if culprits:
        culprit_names = ", ".join(f"{item.player.name} ({item.pubg_name})" for item in culprits)
        lines.append(f"{culprit_names} · {culprits[0].rating:.1f}/5 {stars(culprits[0].rating)}")
        lines.append(_tag_summary(culprits[0]))
    else:
        lines.append("후보 없음")

    lines.append("")
    lines.append("친구별 요약:")
    for item in ranked:
        stats = _compact_stats(item.stats)
        extras = []
        if item.team_damage >= 30:
            extras.append(f"팀딜 {round(item.team_damage)}")
        if item.team_kills:
            extras.append(f"팀킬 {item.team_kills}")
        extra_text = f", {', '.join(extras)}" if extras else ""
        lines.append(
            "- "
            f"{item.player.name} ({item.pubg_name}): "
            f"{item.rating:.1f}/5 {stars(item.rating)}, "
            f"{stats['kills']}K/{stats['assists']}A, {stats['damageText']}, "
            f"{stats['dbnoText']}, {stats['reviveText']}, {stats['survivalText']}{extra_text}"
        )

    lines.append("")
    lines.append("한 줄 요약:")
    lines.append(_verdict(culprits[0] if culprits else None))
    return "\n".join(lines)


def _compact_stats(stats: dict[str, Any]) -> dict[str, Any]:
    survived_minutes = float(stats.get("timeSurvived") or 0) / 60
    kills = int(stats.get("kills") or 0)
    damage = round(float(stats.get("damageDealt") or 0))
    dbnos = int(stats.get("DBNOs") or 0)
    assists = int(stats.get("assists") or 0)
    revives = int(stats.get("revives") or 0)
    return {
        "kills": kills,
        "damageDealt": damage,
        "DBNOs": dbnos,
        "assists": assists,
        "revives": revives,
        "damageText": f"{damage:>3}딜",
        "dbnoText": f"{dbnos}기절",
        "reviveText": f"{revives}부활",
        "survivalText": f"{round(survived_minutes):>2}분",
        "timeSurvivedMinutes": survived_minutes,
        "timeSurvivedText": _format_minutes(survived_minutes),
        "timeSurvivedShort": _format_short_minutes(survived_minutes),
        "winPlace": stats.get("winPlace"),
    }
