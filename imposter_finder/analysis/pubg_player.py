from __future__ import annotations

from math import sqrt
from typing import Any


DEFAULT_MATCH_COUNT = 30


def analyze_pubg_player(nickname: str, matches: list[dict[str, Any]], requested_count: int = DEFAULT_MATCH_COUNT) -> dict[str, Any]:
    """Build an explainable, recent-form report from PUBG match-detail data."""
    rows = [_participant_stats(match, nickname) for match in matches]
    rows = [row for row in rows if row is not None]
    count = len(rows)
    if not rows:
        raise ValueError(f"No participant statistics found for {nickname}")

    avg = lambda key: sum(float(row.get(key) or 0) for row in rows) / count
    avg_damage = avg("damageDealt")
    avg_kills = avg("kills")
    avg_assists = avg("assists")
    avg_revives = avg("revives")
    avg_survival = avg("timeSurvived") / 60
    avg_placement = avg("winPlace")
    wins = sum(1 for row in rows if int(row.get("winPlace") or 0) == 1)
    damage_values = [float(row.get("damageDealt") or 0) for row in rows]
    damage_std = _stddev(damage_values)

    axes = {
        "화력": _cap(avg_damage / 3.0),
        "마무리": _cap(avg_kills * 45 + avg("DBNOs") * 12),
        "생존": _cap(avg_survival / 18 * 100),
        "팀워크": _cap(avg_assists * 30 + avg_revives * 45),
        "안정성": _cap(100 - (damage_std / max(avg_damage, 80) * 55)),
        "순위": _cap(100 - ((max(avg_placement, 1) - 1) / 99 * 100)),
    }
    score = round(
        axes["화력"] * 0.28
        + axes["마무리"] * 0.20
        + axes["생존"] * 0.15
        + axes["팀워크"] * 0.15
        + axes["안정성"] * 0.12
        + axes["순위"] * 0.10
    )
    grade = _grade(score, count)
    traits, verdict = _traits(axes, avg_damage, avg_kills, avg_survival, avg_assists, avg_revives)

    return {
        "nickname": nickname,
        "requested_count": requested_count,
        "match_count": count,
        "sample_note": (
            f"최근 {count}판 기준" if count >= requested_count else f"API에서 확인된 {count}판 기준 (요청: {requested_count}판)"
        ),
        "grade": grade,
        "score": score,
        "axes": {key: round(value) for key, value in axes.items()},
        "stats": {
            "win_rate": round(wins / count * 100),
            "avg_damage": round(avg_damage),
            "avg_kills": round(avg_kills, 1),
            "avg_assists": round(avg_assists, 1),
            "avg_revives": round(avg_revives, 1),
            "avg_survival_minutes": round(avg_survival, 1),
            "avg_placement": round(avg_placement, 1),
        },
        "traits": traits,
        "verdict": verdict,
    }


def _participant_stats(match: dict[str, Any], nickname: str) -> dict[str, Any] | None:
    for item in match.get("included", []):
        if item.get("type") != "participant":
            continue
        stats = item.get("attributes", {}).get("stats", {})
        if str(stats.get("name") or "").lower() == nickname.lower():
            return stats
    return None


def _stddev(values: list[float]) -> float:
    if not values:
        return 0
    mean = sum(values) / len(values)
    return sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _cap(value: float) -> float:
    return max(0.0, min(100.0, value))


def _grade(score: int, sample_count: int) -> str:
    if sample_count < 8:
        return "판정 보류"
    for threshold, grade in ((85, "S"), (70, "A"), (55, "B"), (40, "C"), (25, "E")):
        if score >= threshold:
            return grade
    return "F"


def _traits(
    axes: dict[str, float], damage: float, kills: float, survival: float, assists: float, revives: float
) -> tuple[list[str], str]:
    traits: list[str] = []
    if axes["화력"] >= 70 and axes["마무리"] >= 60:
        traits.append("화력형 해결사")
    elif axes["화력"] >= 65:
        traits.append("후방 화력지원")
    if axes["팀워크"] >= 60:
        traits.append("구조대장")
    if axes["생존"] >= 70 and axes["화력"] < 45:
        traits.append("생존 장인")
    if axes["안정성"] >= 70:
        traits.append("기복 없는 직장인")
    if axes["순위"] >= 70:
        traits.append("막판 집중형")
    if axes["생존"] < 35 and axes["화력"] >= 55:
        traits.append("선봉 돌격대")
    if axes["생존"] >= 55 and axes["화력"] < 30:
        traits.append("파밍 관광객")
    if not traits:
        traits.append("밸런스형 관찰자")

    if "파밍 관광객" in traits:
        verdict = "오래 살아남지만 전장은 관광 코스가 아닙니다. 교전 기여를 조금만 올려봅시다."
    elif "선봉 돌격대" in traits:
        verdict = "총성에 누구보다 먼저 반응합니다. 아군의 발소리도 같이 기다리면 더 무섭습니다."
    elif "화력형 해결사" in traits:
        verdict = "필요할 때 숫자로 말하는 해결사입니다. 오늘의 면책 가능성이 높습니다."
    elif "구조대장" in traits:
        verdict = "팀원이 눕는 순간 출동합니다. 이 팀의 보험 상품입니다."
    elif damage < 100 and kills < 0.5:
        verdict = "총을 들고 있었던 기록은 확인됐습니다. 다음엔 적도 한 번 찾아봅시다."
    else:
        verdict = "한쪽으로 치우치지 않은 타입입니다. 레이더의 낮은 축이 다음 성장 포인트입니다."
    return traits[:3], verdict
