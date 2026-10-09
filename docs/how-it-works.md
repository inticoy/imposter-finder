# 동작 방식

현재 버전 기준입니다. 흐름도는 [flow.html](flow.html)에 있어요.

## 구조

```
launchd (KeepAlive)
  └─ python -m imposter_finder serve        상주 프로세스 1개
       ├─ Discord 봇 (bot.py)               /user 슬래시 명령
       └─ 15분 수집 루프 (collector.py)       새 그룹 매치 → 범인찾기 리포트
            ├─ games/pubg.py                PUBG API (매치·텔레메트리)
            ├─ analysis/pubg.py             매치별 범인 판정
            ├─ storage.py                   SQLite data/imposter_finder.db
            └─ discord.py                   리포트 전송 (REST)
```

## 15분 수집 → 범인찾기 리포트

1. `players.json`의 친구마다 최근 30경기를 PUBG API에서 가져와 SQLite에 쌓습니다.
2. 아직 처리하지 않은 매치 중 다음을 걸러냅니다.
   - `PUBG_MAX_MATCH_AGE_HOURS`(기본 12시간)보다 오래된 매치
   - 등록된 친구가 2명 미만인 매치 (혼자 한 판)
3. 남은 매치는 텔레메트리까지 받아 친구마다 평점(0~5)을 매기고, 가장 낮은 사람을 "범인"으로 리포트를 보냅니다.
4. 처리한 매치는 `sent` / `too_old` / `not_a_group_match`로 기록해 다시 보내지 않습니다.

**평점 감점 예시** (`analysis/pubg.py`): 0딜 −0.8, 킬·기절 없이 교전 기여 낮음 −0.4, 5분 이상 살았는데 교전 거의 없음 −0.4, 친구 중 첫 기절 −0.6, 친구 중 첫 사망 −0.8.

**첫 실행과 재시작:** DB가 비어 있으면 첫 수집은 리포트 없이 기록만 합니다(지난 매치가 한꺼번에 가지 않게). DB가 있으면 꺼져 있던 동안의 최근 매치도 정상적으로 리포트합니다.

## `/user` 전적 감정서

`/user 이름 [판수]` (기본 30판, 5~100) — 이름은 자동완성됩니다.

- 로컬 DB에 쌓인 경기를 먼저 쓰고, 모자라면 PUBG API로 채웁니다.
- 6개 지표(0~100): 화력 28% · 마무리 20% · 생존 15% · 팀워크 15% · 안정성 12% · 순위 10%
- 가중 점수로 등급: S(85+) · A(70+) · B(55+) · C(40+) · E(25+) · F
- 방사형 그래프 이미지, 승률·평균 딜·킬·생존·순위, 재미용 성향 판정을 함께 보여줍니다.

## 운영

| 항목 | 내용 |
|---|---|
| 실행 | launchd `com.inticoy.imposter-finder`, Python 3.14 (`.venv`) |
| 로그 | `logs/imposter-finder.log` (수집 결과), `logs/imposter-finder-error.log` (봇) |
| 감시 | 수집 주기마다 `HEALTHCHECK_IMPOSTER_FINDER_URL` 핑, 실패 시 `/fail` (period 15분, grace 10분) |
| 데이터 | `data/imposter_finder.db` (커밋 안 함), `players.json` (커밋 안 함) |

- 인터넷이 끊긴 채로 시작하면 Discord 로그인에 실패해 종료되고, launchd가 10초 뒤 다시 띄웁니다.
- `pubg` 명령(`--list-matches` 등)은 디버깅용입니다. 이 경로는 예전 GitHub Actions용 `data/seen_matches.json`을 상태로 씁니다.
