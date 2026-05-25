# Session Summary

Date: 2026-05-25  
Project: `imposter-finder` / 범인찾기 봇

## Goal

친구들의 게임 닉네임을 미리 등록해두고, 주기적으로 최근 전적을 확인한 뒤 새 게임이 발견되면 결과를 분석해서 Discord에 "범인" 후보를 알려주는 봇을 만든다.

초기 후보 게임:

- League of Legends, 특히 KR 증강 칼바람 / ARAM: Mayhem
- VALORANT
- PUBG
- FC Online

## Current Decision

기존 `buy-or-not` 레포에서 작업하지 않고, 새 프로젝트 폴더 `../imposter-finder`에서 진행한다.

현재 생성된 파일:

- `README.md`
- `session.md`
- `tools/verify_game_apis.py`
- `docs/api-keys.md`
- `docs/game-api-capabilities.md`
- `docs/implementation-plan.md`

## What Was Discussed

기존 `buy-or-not`는 algumon 핫딜을 매일 12시에 Discord 포럼 채널로 보내는 Python 봇이다. 새 봇은 크롤링이 아니라 게임사 API를 사용해 전적을 가져오는 쪽이 적합하다.

처음에는 게임 진행형 Discord 봇인지 논의했지만, 실제 목표는 친구들의 최근 게임 전적을 감지하고 분석하는 봇으로 정리됐다.

## Game Feasibility Summary

| Game | Feasibility | Notes |
|---|---|---|
| PUBG | High | 공식 API가 닉네임 조회, 최근 매치, 매치 상세, telemetry를 제공한다. 범인찾기 분석에 가장 적합하다. |
| FC Online | Medium | Nexon Open API로 매치 조회 가능. 단, 데이터가 약 2시간 지연된다. 커스텀 1:1은 실제 matchtype 검증 필요. 볼타 커스텀은 `234`로 명시되어 있다. |
| LoL KR ARAM Mayhem | Unknown | KR 계정과 일반 Match-V5 흐름은 가능. `queueId=2400`은 ARAM: Mayhem으로 확인됐지만, 실제 Match-V5에서 최근 증강 칼바람 매치가 반환되는지는 API key로 검증해야 한다. |
| VALORANT | Technically possible, operationally hard | 공식 API는 matchlist/match detail을 제공하지만 Production key, RSO, 사용자 opt-in, Riot 승인이 필요하다. 개인 토이봇 MVP에는 후순위. |

## Verification Script

검증 스크립트:

```bash
python3 tools/verify_game_apis.py all
python3 tools/verify_game_apis.py lol
python3 tools/verify_game_apis.py pubg
python3 tools/verify_game_apis.py fc
```

`.env` 예시:

```env
RIOT_API_KEY=...
LOL_RIOT_ID=닉네임#태그
LOL_QUEUE_ID=2400

PUBG_API_KEY=...
PUBG_PLATFORM=steam
PUBG_PLAYER_NAMES=닉네임1,닉네임2

NEXON_API_KEY=...
FC_NICKNAME=구단주닉네임
FC_MATCHTYPE=234
```

현재 `.env`가 없기 때문에 실행 결과는 `SKIP`이다.

## Verification Priorities

1. PUBG: API key와 친구 닉네임으로 최근 매치, participant stats, telemetry URL이 잡히는지 확인한다.
2. FC Online: `FC_MATCHTYPE=234`, 그리고 일반 커스텀/친선 후보인 `30`, `40`, `60`을 각각 확인한다.
3. LoL KR ARAM Mayhem: `LOL_QUEUE_ID=2400`으로 최근 match id가 반환되는지 확인한다.
4. VALORANT: 승인 절차가 필요하므로 PoC에서는 제외하거나 mock 데이터로만 설계한다.

## Important Notes

- API key는 채팅에 붙이지 말고 `.env`에 저장한다.
- `.env`는 git에 커밋하지 않는다.
- FC Online은 10분 polling을 하더라도 API 데이터 자체가 약 2시간 늦게 갱신될 수 있다.
- LoL ARAM: Mayhem은 match id가 잡히는지부터 확인해야 한다. 증강 선택 정보는 필요하지 않다고 정리됐다.
- VALORANT는 친구들이 각자 Riot Sign On으로 데이터 공유에 동의해야 하는 구조가 될 가능성이 높다.
