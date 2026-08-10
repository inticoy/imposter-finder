# imposter-finder

범인찾기 봇 API 검증용 프로젝트.

## 문서

- [session.md](session.md): 현재까지 논의한 내용과 다음 세션 인수인계
- [docs/game-api-capabilities.md](docs/game-api-capabilities.md): 게임별 API 가능 여부와 가져올 수 있는 정보
- [docs/api-keys.md](docs/api-keys.md): API key 발급 위치와 `.env` 설정
- [docs/implementation-plan.md](docs/implementation-plan.md): 검증 이후 구현 계획
- [docs/player-registry.md](docs/player-registry.md): 친구별 Discord ID와 게임 닉네임 매핑 방식
- [docs/github-actions.md](docs/github-actions.md): GitHub Actions dev/prod 실행과 로컬 테스트 방법

## 친구 계정 등록

실제 친구 정보는 `players.json`에 저장하고 커밋하지 않습니다.

```bash
cp players.example.json players.json
```

`players.json`에는 사람 기준으로 Discord ID, PUBG 닉네임, FC Online 닉네임, LoL Riot ID를 함께 둡니다.

## API 검증

`.env`에 필요한 키와 테스트 닉네임을 넣고 실행합니다.

```env
RIOT_API_KEY=...
LOL_RIOT_ID=닉네임#태그
LOL_QUEUE_ID=2400

PUBG_API_KEY=...
PUBG_PLATFORM=steam
PUBG_PLAYER_NAMES=닉네임1,닉네임2 # players.json을 쓰면 생략 가능

NEXON_API_KEY=...
FC_NICKNAME=구단주닉네임
FC_MATCHTYPE=234
```

```bash
python3 tools/verify_game_apis.py all
python3 tools/verify_game_apis.py lol
python3 tools/verify_game_apis.py pubg
python3 tools/verify_game_apis.py fc
```

## PUBG MVP 실행

`players.json`과 `.env`의 `PUBG_API_KEY`를 준비한 뒤 실행합니다.

```bash
python3 -m imposter_finder pubg
```

최근 매치를 다시 분석하고 싶으면:

```bash
python3 -m imposter_finder pubg --include-seen
```

처리한 match id는 `data/seen_matches.json`에 저장됩니다. GitHub Actions에서 실행한 뒤 이 파일을 커밋하면 다음 실행에서 같은 매치를 건너뛸 수 있습니다.

분석 대상은 `players.json`에 등록된 친구가 2명 이상 참여한 PUBG 매치로 제한됩니다.
기본적으로 최근 12시간 이내 매치만 새 알림 후보로 봅니다. `PUBG_MAX_MATCH_AGE_HOURS`로 조정할 수 있습니다.

Discord 전송까지 테스트하려면:

```bash
BOT_ENV=dev python3 -B -m imposter_finder pubg --include-seen --ignore-age-limit --max-matches 1 --send-discord --no-save-state
```

Discord 메시지는 embed 형식으로 전송되며, 포럼 채널에서는 매치별 thread가 생성됩니다.
`DISCORD_THREAD_DEV` 또는 `DISCORD_THREAD_PROD`를 설정하면 고정 thread에 계속 댓글로 전송합니다.

## Discord에서 전적 조회하기

등록된 `players.json`의 이름 또는 PUBG 닉네임을 기준으로 최근 전적을 조회하는 로컬 슬래시 명령 봇입니다. 기본 분석 판수는 **30판**입니다.

```bash
python3 -m pip install -r requirements.txt
python3 -m imposter_finder bot
```

Discord에서 `/user`를 입력한 뒤 등록된 이름을 고릅니다. 이름 입력칸에는 `players.json`에 등록된 사용자 자동완성이 표시됩니다.

```text
/user 윤건우
/user 윤건우 판수:50
```

`.env`에 `DISCORD_BOT_TOKEN`을 설정해야 합니다. 개발 서버에서 슬래시 명령을 즉시 반영하려면 해당 서버 ID를 `DISCORD_GUILD_ID`로 추가합니다. 없으면 Discord 전역 명령 동기화를 사용합니다.

결과 카드는 화력·마무리·생존·팀워크·안정성·순위의 6개 지표를 방사형 그래프로 표시하고, 등급(S/A/B/C/E/F)과 재미용 성향 판정을 함께 제공합니다. PUBG API가 현재 노출한 최근 매치가 30판보다 적으면 실제 분석 판수를 카드에 표시합니다.

## 로컬 상시 운영 서버

GitHub Actions 대신 맥에서 Discord 명령과 15분 PUBG 수집을 함께 실행하려면 다음 명령을 사용합니다.

```bash
python3 -m imposter_finder serve
```

`serve`는 처음 실행할 때 기존 매치를 SQLite(`data/imposter_finder.db`)에 조용히 적재하고, 이후 `PUBG_POLL_INTERVAL_MINUTES`(기본 15분)마다 새 매치를 확인해 범인찾기 게시판으로 보냅니다. `/user`는 누적된 로컬 이력을 우선 사용하며 부족할 때만 PUBG API에서 최신 정보를 보충합니다.

```env
PUBG_POLL_INTERVAL_MINUTES=15
# 선택: 기본 data/imposter_finder.db 대신 다른 위치 사용
# LOCAL_DATABASE_PATH=data/imposter_finder.db
# 선택: 15분 수집 성공/실패를 외부 Healthchecks.io로 보고
# HEALTHCHECK_IMPOSTER_FINDER_URL=https://hc-ping.com/...
```

Healthchecks를 쓴다면 Check의 **Period는 15분, Grace Time은 10분**으로 설정합니다. 핑 URL은 `.env`에만 저장하고 커밋하지 않습니다. 성공한 수집 주기마다 정상 핑을, 수집 오류 때는 실패 핑을 보냅니다. 정상 핑은 Discord 알림을 만들지 않으며, 장애(Down)와 복구(Up) 상태 변화만 Healthchecks의 Discord 연동으로 알립니다.

macOS에서는 [launchd 설정](ops/launchd/com.inticoy.imposter-finder.plist)을 `~/Library/LaunchAgents/`에 설치하면 로그인·재부팅 뒤에도 자동 실행하고, 비정상 종료 시 다시 시작합니다.

현재 개발 환경에는 위 설정이 설치되어 있습니다. 상태와 로그는 다음으로 확인합니다.

```bash
launchctl print gui/$(id -u)/com.inticoy.imposter-finder
tail -f data/imposter-finder.log
tail -f data/imposter-finder-error.log
```

FC 온라인 matchtype 참고:

| matchtype | 설명 |
|---:|---|
| 30 | 리그 친선 |
| 40 | 클래식 1on1 |
| 50 | 공식경기 |
| 52 | 감독모드 |
| 60 | 공식 친선 |
| 204 | 볼타 친선 |
| 214 | 볼타 공식 |
| 224 | 볼타 AI대전 |
| 234 | 볼타 커스텀 |
