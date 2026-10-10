# 범인찾기 (imposter-finder)

친구들끼리 한 PUBG·FC 온라인·롤 경기를 찾아 Discord의 게임별 스레드에 이미지 리포트를 올리는 봇입니다. PUBG `/user` 전적 감정서도 제공합니다. 이 Mac에서 상주 프로세스로 돕니다.

동작 방식은 [docs/how-it-works.md](docs/how-it-works.md), 버전과 계획은 [docs/versions.md](docs/versions.md), FC 데이터는 [docs/fc-online.md](docs/fc-online.md), 흐름도는 [docs/flow.html](docs/flow.html)에 있습니다.

## 설정

```bash
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env              # 토큰·채널 ID·API 키 채우기
cp players.example.json players.json   # 친구 목록 (커밋 안 함)
```

| 변수 | 설명 |
|---|---|
| `BOT_ENV` | `dev` / `prod` — 보낼 채널 선택 |
| `DISCORD_BOT_TOKEN` | Discord 봇 토큰 |
| `DISCORD_GUILD_ID` | 선택: 슬래시 명령을 이 서버에 바로 반영 |
| `DISCORD_CHANNEL_*` / `DISCORD_THREAD_*` | 보낼 포럼 채널 / 고정 thread |
| `PUBG_API_KEY`, `PUBG_PLATFORM` | [PUBG Developer](https://developer.pubg.com)에서 발급 |
| `PUBG_MAX_MATCH_AGE_HOURS` | 이보다 오래된 매치는 알리지 않음 (기본 12) |
| `NEXON_API_KEY` | FC 온라인, [NEXON Open API](https://openapi.nexon.com)에서 발급 (`test_` 키 하루 1,000회, 900회에서 멈춤) |
| `DISCORD_THREAD_FC_DEV` / `_PROD` | FC 리포트를 보낼 thread (`BOT_ENV`에 맞는 쪽) |
| `RIOT_API_KEY` | 롤, [Riot Developer](https://developer.riotgames.com) 개인 키 (2분 100회) |
| `DISCORD_THREAD_LOL_DEV` / `_PROD` | 롤 리포트를 보낼 thread |
| `DISCORD_THREAD_PUBG_DEV` / `_PROD` | 배그 이미지 리포트를 보낼 전용 thread. 미설정 시 기존 글 리포트 경로 사용 |
| `GEMINI_API_KEY` | 배그·FC·롤 총평/평가 (없으면 평가 없이 보냄) |
| `HEALTHCHECK_IMPOSTER_FINDER_URL` | 선택: 수집 주기마다 Healthchecks.io 핑 |

**`players.json`** — 사람마다 Discord ID와 게임 계정을 적습니다. PUBG 분석은 여기 등록된 친구가 2명 이상 함께 한 매치만 대상으로 합니다.

```json
{"players": [{"name": "준호", "discord_user_id": "123456789012345678",
  "accounts": {"pubg": {"platform": "steam", "nickname": "PubgNickA"},
               "fconline": {"nickname": "FcNickA"},
               "lol": {"riot_ids": ["GameNameA#KR1", "SubAccount#KR1"]}}}]}
```

## 실행

```bash
.venv/bin/python -m imposter_finder serve   # 슬래시 명령 봇 + 15분 PUBG·FC·롤 수집 (운영)
.venv/bin/python -m imposter_finder pubg --list-matches   # 지금 분석할 매치 후보만 보기
BOT_ENV=dev .venv/bin/python -m imposter_finder pubg --include-seen --ignore-age-limit \
  --max-matches 1 --send-discord --no-save-state         # 예전 CLI 글 리포트 확인용; 운영 이미지 경로와 별개
```

## 자동 실행 (launchd)

```bash
cp ops/launchd/com.inticoy.imposter-finder.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.inticoy.imposter-finder.plist
launchctl print gui/$(id -u)/com.inticoy.imposter-finder     # 상태
tail -f logs/imposter-finder.log logs/imposter-finder-error.log
```

로그인하면 시작하고, 죽으면 10초 뒤 다시 띄웁니다(`KeepAlive`).

`BOT_ENV=prod`면 게임별 `_PROD` 스레드로 보냅니다. `.env` 변경을 적용할 때는 `launchctl kickstart -k gui/$(id -u)/com.inticoy.imposter-finder`로 재시작합니다. 경기 원본과 전송 상태는 로컬 `data/imposter_finder.db`에 저장하며 Git에는 올리지 않습니다.
