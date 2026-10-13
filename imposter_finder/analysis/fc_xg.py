"""FC 온라인 기대 득점(xG)과 슈팅 종류.

넥슨 API에는 xG가 없어 친구전 슈팅으로 직접 학습한 로지스틱 모델을 쓴다.
친구전은 슈팅의 약 40%가 골이라 실제 축구(약 10%) 모델을 그대로 쓰면 맞지 않는다.
다시 학습: PYTHONPATH=. .venv/bin/python tools/fc_xg_fit.py (결과를 WEIGHTS에 붙여 넣는다)
"""
from __future__ import annotations

import math
from typing import Any, Iterable

PITCH_LENGTH, PITCH_WIDTH, GOAL_WIDTH = 105.0, 68.0, 7.32

# shootDetail.type → 이름. 공식 코드표가 없어 확인한 것만 이름을 붙인다 (docs/fc-online.md "슈팅 종류")
# 8·9: 팀 기록(shootFreekick·shootPenaltyKick)과 256개 팀 기록 모두 일치
# 3: 박스 안·어시스트 97%로 헤더가 유력 (팀 기록 shootHeading과는 80%만 일치)
# 1·2·6·7: 외부 분석(yim2412/fifa-match-tracker)의 대조 결과. 4·10·12·13·14는 미상
SHOT_TYPES = {1: "일반 슈팅", 2: "감아차기", 3: "헤더", 6: "땅볼 슈팅", 7: "발리", 8: "프리킥", 9: "페널티킥"}
HEADER, FREE_KICK, PENALTY = 3, 8, 9

# 2026-10-13 친구전 129경기 · 슈팅 1,722개로 학습. 순서는 features()와 같다
WEIGHTS = (-0.428, -0.910, 3.014, -1.594, 0.114, -0.685, -0.438)


def geometry(x: float, y: float) -> tuple[float, float]:
    """(골문까지 거리 m, 골문이 보이는 각도 rad). x는 상대 골라인이 1, y는 경기장 폭 0~1."""
    dx = max((1 - x) * PITCH_LENGTH, 0.1)
    dy = (y - 0.5) * PITCH_WIDTH
    angle = math.atan2(GOAL_WIDTH * dx, dx * dx + dy * dy - (GOAL_WIDTH / 2) ** 2)
    return math.hypot(dx, dy), angle if angle >= 0 else angle + math.pi


def features(shot: dict[str, Any]) -> list[float]:
    distance, angle = geometry(shot["x"], shot["y"])
    kind = shot.get("type")
    return [1.0, distance / 10, angle, float(kind == HEADER), float(bool(shot.get("assist"))),
            float(kind == FREE_KICK), float(kind == PENALTY)]


def shot_xg(shot: dict[str, Any], weights: Iterable[float] = WEIGHTS) -> float:
    z = sum(w * f for w, f in zip(weights, features(shot)))
    return 1 / (1 + math.exp(-z))


def team_xg(side: dict[str, Any]) -> float:
    return sum(shot_xg(shot) for shot in side.get("shootDetail", []))


def shot_label(shot: dict[str, Any]) -> str | None:
    return SHOT_TYPES.get(shot.get("type"))


def fit(shots: list[dict[str, Any]], goal: int = 3, steps: int = 4000, rate: float = 0.5) -> tuple[list[float], float]:
    """경사 하강 로지스틱 회귀. 반환: (가중치, 평균 log loss)."""
    rows = [(features(s), 1.0 if s.get("result") == goal else 0.0) for s in shots]
    w = [0.0] * len(rows[0][0])
    for _ in range(steps):
        grad = [0.0] * len(w)
        for x, y in rows:
            p = 1 / (1 + math.exp(-sum(a * b for a, b in zip(w, x))))
            for k, v in enumerate(x):
                grad[k] += (p - y) * v
        w = [a - rate * g / len(rows) for a, g in zip(w, grad)]
    loss = 0.0
    for x, y in rows:
        p = min(max(1 / (1 + math.exp(-sum(a * b for a, b in zip(w, x)))), 1e-9), 1 - 1e-9)
        loss -= math.log(p) if y else math.log(1 - p)
    return w, loss / len(rows)
