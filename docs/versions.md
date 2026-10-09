# 버전별 스펙

| 버전 | 상태 | 한 줄 요약 |
|---|---|---|
| v1 | 종료 | GitHub Actions에서 PUBG 그룹 매치 범인찾기 |
| v2 | **운영 중** | 맥 상주 봇: `/user` 전적 감정서 + 15분 수집 + SQLite |
| v3 | 계획 | FC 온라인 (친구전 분석, 스쿼드, 선수 정보) + `@멘션` 자연어 |

## v1 — GitHub Actions (2026-05 ~ 08)

- cron-job.org가 30분마다 workflow를 실행, 새 매치를 분석해 Discord에 올리고 `data/seen_matches.json`을 커밋
- **종료 사유:** `/user` 같은 슬래시 명령은 봇이 계속 켜져 있어야 해서 맥 상주로 이전 (2026-08-11). PUBG API 차단 문제는 없었음
- 워크플로는 2026-10-09에 삭제. cron-job.org에 남은 작업이 있으면 꺼야 합니다

## v2 — 맥 상주 봇 (현재)

- `serve` 하나로 슬래시 명령 봇과 15분 수집 루프 실행, 매치·연결 기록은 SQLite
- `/user`: 6개 지표 + 등급 + 방사형 그래프
- 재시작 때 꺼져 있던 동안의 매치도 리포트 (2026-08-16)
- Python 3.9(Xcode) → 3.14(Homebrew), 로그 `logs/`로 이동 (2026-10-09)
- 자세한 내용: [how-it-works.md](how-it-works.md)

## v3 — FC 온라인 + 자연어 (계획)

같은 레포에 FC 모듈을 두고, 결과는 별도 thread로 보냅니다. 기능이 커지면 레포를 나눕니다.

### 하고 싶은 것
- **친구전 분석:** 친구끼리 한 경기의 상대 전적(승/무/패, 득실), 점유율·슈팅·패스, 선수별 평점, 슈팅 지도, Gemini 한 줄 총평
- **스쿼드 보기:** 닉네임 → 최근 경기에 뛴 선수로 스쿼드 재구성, 미니페이스온·오버롤·이름
- **선수 정보·추천:** 시즌·강화별 능력치, 특성 비교, 조건으로 추천
- **`@멘션` 자연어:** 살래말래처럼 "건우 스쿼드 보여줘", "나랑 진호 전적"

### 데이터 출처 (2026-10-09 확인)

| 데이터 | 출처 | 비고 |
|---|---|---|
| 닉네임 → ouid, 경기 목록, 경기 상세 | NEXON Open API `open.api.nexon.com/fconline/v1/...` | 키 필요. `test_` 키는 하루 1,000회. 반영 최대 약 2시간 지연 |
| 선수 이름, 시즌, 포지션, 등급 | 공개 메타데이터 `open.api.nexon.com/static/fconline/meta/{spid,seasonid,spposition,division}.json` | 키 불필요. 선수 88,359명, 시즌 153개 |
| 미니페이스온 | `fco.dn.nexoncdn.co.kr/live/externalAssets/common/playersAction/p{spid}.png` | 공식 CDN |
| 오버롤(포지션별 28개)·급여·시세 | 데이터센터 내부 `GET /DataCenter/SquadMakerPlayerList?strPlayerName=` | 공식 API 아님, JSON |
| 세부 능력치 34개·특성·약발·팀컬러 | 데이터센터 내부 `POST /datacenter/PlayerAbility` (spid, n1Strong=강화) | 공식 API 아님, HTML |

**지켜야 할 것**
- 스쿼드 조회 API는 없습니다. 최근 경기에 **실제로 뛴 선수만** 알 수 있습니다.
- API로 받아 저장한 데이터는 **30일 안에 갱신**해야 합니다 (넥슨 Open API 공지).
- 데이터센터 내부 요청은 바뀌거나 막힐 수 있어 간격을 두고, 받은 값은 DB에 저장해 재사용합니다.
- 경기 데이터 보관 기간이 짧아 조회할 때마다 DB에 쌓습니다.
- 친구전이 어느 경기 종류(`matchtype` 30 리그 친선 / 40 클래식 1on1 / 60 공식 친선)로 잡히는지 실제 계정으로 확인해야 합니다.

### 남은 준비
- [ ] 친구들의 FC 온라인 닉네임을 `players.json`의 `accounts.fconline.nickname`에 추가
- [ ] 친구전 경기 종류 확인
- [ ] FC 전용 thread 만들고 `DISCORD_THREAD_FC_*` 추가

참고 자료: [game-apis.md](game-apis.md) (PUBG·LoL·VALORANT·FC API 조사)
