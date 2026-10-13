# FC 온라인 데이터 참고

FC 기능을 만들 때 쓰는 데이터 출처와 규칙입니다. 동작 방식은 [how-it-works.md](how-it-works.md)를 보세요.

## 데이터 출처 (2026-10-09 확인)

| 데이터 | 출처 | 비고 |
|---|---|---|
| 닉네임 → ouid, 경기 목록, 경기 상세 | NEXON Open API `open.api.nexon.com/fconline/v1/...` | 키 필요. `test_` 키는 하루 1,000회. 반영 약 2~3시간 지연 |
| 선수 이름, 시즌, 포지션, 등급 | 공개 메타데이터 `open.api.nexon.com/static/fconline/meta/{spid,seasonid,spposition,division}.json` | 키 불필요. 선수 88,359명, 시즌 153개 |
| 시즌 얼굴 (그 시즌 유니폼 상반신 128px) | `fco.dn.nexoncdn.co.kr/live/externalAssets/common/playersAction/p{spid}.png` | 공식 CDN. 친구가 설정한 미니페이스온은 API에 없음 |
| 시즌 아이콘 | 메타 `seasonid.json`의 `seasonImg` | |
| 오버롤(포지션별 28개)·급여·시세 | 데이터센터 내부 `GET /DataCenter/SquadMakerPlayerList?strPlayerName=` | 공식 API 아님, JSON |
| 세부 능력치 34개·특성·약발·팀컬러 | 데이터센터 내부 `POST /datacenter/PlayerAbility` (spid, n1Strong=강화) | 공식 API 아님, HTML. 팀컬러는 `selector_item tdefault{id}`, 90000000(단일팀)은 제외 |
| 팀컬러 목록·엠블럼 | 데이터센터 내부 `GET /datacenter/teamcolor` | 공식 API 아님, HTML. 801개 (클럽·국가·특수), 매주 갱신 |
| 선수 시세 | `SquadMakerPlayerList`의 `eachPrice` ("0\|+1\|…\|+13", BP) | 하루 한 번, 구단 가치 계산 |
| 강화 배지 | 데이터센터 CSS `.en_levelN` (0~1 회색, 2~4 동, 5~7 은, 8~10 금, 11+ 플래티넘 `bg_plt.png`) | 이미지 안에 그려서 재현 |

## 지켜야 할 것

- 스쿼드 조회 API는 없습니다. 최근 경기에 **실제로 뛴 선수만** 알 수 있습니다.
- API로 받아 저장한 데이터는 **30일 안에 갱신**해야 합니다 (넥슨 Open API 공지).
- 데이터센터 내부 요청은 바뀌거나 막힐 수 있어 간격을 두고, 받은 값은 DB에 저장해 재사용합니다.
- 경기 데이터 보관 기간이 짧아 조회할 때마다 DB에 쌓습니다.

## 친구 · thread

- 친구 FC 닉네임: `players.json`의 `accounts.fconline.nickname`
- 친구전 판별: 친구들의 **클래식 1on1(40)** 목록에 같은 경기 ID가 두 명 이상 있으면 친구전. 리그 친선(30)·공식 친선(60)은 친구전이 없었음. 2026-10-09 기준 6쌍 124경기
- 보관: 넥슨은 약 30일치만 보여줍니다 (2026-10-09 기준 가장 오래된 경기 9/10, 다음 페이지 없음)
- thread: 말말말-beta "⚽ FC Online BETA" (`DISCORD_THREAD_FC_DEV`), 말말말 "⚽ FC Online" (`DISCORD_THREAD_FC_PROD`). 봇이 만든 thread라 첫 글을 봇이 수정할 수 있음

## 경기 상세에 있는 것 (`match-detail`)

- 양쪽 닉네임, 결과, 골, 점유율, 파울·카드, 코너킥, 오프사이드, 평균 평점, 컨트롤러(키보드/패드)
- 슈팅(총·유효·골·종류), 패스(시도·성공), 수비(태클·블록)
- 출전 선수마다 spId·포지션·강화 등급과 개인 기록(골·도움·슈팅·패스·드리블·태클·평점)
- 슈팅 상세: 시간, 좌표(x, y 0~1, 공격 방향 기준 x=1이 상대 골대), 결과(1 유효, 2 빗나감, 3 골), 슈터·어시스트 선수와 어시스트 좌표, 페널티 박스 여부
- `goalTime`: 위 비트가 전후반(0/1/2/3/4 → 0/45/90/105/120분), 아래 24비트가 초. 표시 분 = 시작 분 + 초//60 + 1
- 없는 것: 경고·퇴장·교체 **시간** (선수별 경고 개수만 있음)
- `matchDate`는 **UTC**입니다 (2026-10-10 00:11 KST에 랭커 5명의 최신 경기가 10/9 12:44로 찍혀 있었음 = 21:44 KST). 한국 시간은 +9시간
- `matchEndType`: 0 정상, 1 몰수승, 2 몰수패(중도 이탈). 몰수패 쪽은 스탯이 비어 있을 수 있음
- 선수 `spPosition` 28은 벤치(SUB). 경기마다 선발 11 + 벤치 7 = 18명

## 슈팅 종류 (`shootDetail.type`)

공식 문서에 코드표가 없습니다 (2026-10-13 확인). 확인한 것만 이름을 붙이고 나머지는 "슈팅"으로 둡니다 (`analysis/fc_xg.py` `SHOT_TYPES`).

| 코드 | 이름 | 근거 |
|---|---|---|
| 8 | 프리킥 | 팀 기록 `shootFreekick`·`goalFreekick`과 256개 팀 기록 모두 일치 |
| 9 | 페널티킥 | 팀 기록 `shootPenaltyKick`·`goalPenaltyKick`과 256개 모두 일치. 어시스트 0%, 거리 약 11m |
| 3 | 헤더 | 어시스트 97%, 전부 박스 안. 팀 기록 `shootHeading`과는 80%만 일치 (차이 원인 미상) |
| 1·2·6·7 | 일반·감아차기·땅볼·발리 | 외부 분석([yim2412/fifa-match-tracker](https://github.com/yim2412/fifa-match-tracker))의 대조 결과. 우리 데이터의 거리·위치 분포와 모순 없음 |
| 4·10·12·13·14 | **보류** | 근거 없음. 10·12·13은 평균 20m 이상 중거리 (무회전·파워 슈팅 후보). 친구가 골 종류를 알려 주면 그 경기 기록과 대조해 채운다 |

## 기대 득점 (xG)

- API에 없어 친구전 슈팅으로 학습한 로지스틱 모델(`analysis/fc_xg.py`): 골문까지 거리·보이는 각도·헤더·어시스트·프리킥·페널티킥
- 친구전은 슈팅의 약 40%가 골이라 실제 축구 xG(골 약 10%)를 쓰면 맞지 않습니다. 2026-10-13 129경기·슈팅 1,722개로 학습, 합계 xG 695.1 vs 실제 695골
- 다시 학습: `PYTHONPATH=. .venv/bin/python tools/fc_xg_fit.py` → 출력된 `WEIGHTS`를 붙여 넣기

## 선수 이미지

- `playersAction/p{spid}.png`(시즌 얼굴)가 없으면 `players/p{pid}.png`(pid = spid % 1000000) 기본 얼굴. 받은 이미지는 `data/assets/fc/`에 영구 저장
- 예: 반페르시 UC(spid 877007826)는 시즌 이미지가 403, 기본 이미지는 200
