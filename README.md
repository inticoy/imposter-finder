# 범인찾기 (imposter-finder)

친구들끼리 한 PUBG·FC 온라인·롤 경기를 찾아 Discord의 게임별 스레드에 이미지 리포트를 올리는 봇입니다. GitHub Actions에서 15분마다 돕니다 (cron-job.org가 실행). PUBG `/user` 전적 감정서는 맥 상주(`serve`)일 때만 쓸 수 있고, 지금은 꺼 두었습니다.

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
| `HEALTHCHECK_IMPOSTER_FINDER_URL` | 선택: 수집 주기마다 Healthchecks.io 핑 (한 게임이라도 실패하면 `/fail`) |
| `*_POLL_INTERVAL_MINUTES` | 맥 `serve`의 수집 주기. Actions는 cron-job.org 주기를 따름 |

**`players.json`** — 사람마다 Discord ID와 게임 계정을 적습니다. PUBG 분석은 여기 등록된 친구가 2명 이상 함께 한 매치만 대상으로 합니다.

```json
{"players": [{"name": "준호", "discord_user_id": "123456789012345678",
  "accounts": {"pubg": {"platform": "steam", "nickname": "PubgNickA"},
               "fconline": {"nickname": "FcNickA"},
               "lol": {"riot_ids": ["GameNameA#KR1", "SubAccount#KR1"]}}}]}
```

## 실행

```bash
.venv/bin/python -m imposter_finder collect # PUBG·FC·롤을 한 번 수집·리포트하고 끝 (GitHub Actions 운영)
.venv/bin/python -m imposter_finder serve   # 슬래시 명령 봇 + 15분 PUBG·FC·롤 수집 (맥 상주, 지금 꺼 둠)
.venv/bin/python -m imposter_finder pubg --list-matches   # 지금 분석할 매치 후보만 보기
BOT_ENV=dev .venv/bin/python -m imposter_finder pubg --include-seen --ignore-age-limit \
  --max-matches 1 --send-discord --no-save-state         # 예전 CLI 글 리포트 확인용; 운영 이미지 경로와 별개
```

## 자동 실행 (GitHub Actions + cron-job.org) — 운영 중

`.github/workflows/find-imposter.yml`: cron-job.org가 15분마다 `workflow_dispatch`로 부르면 `collect`가 배그 → FC → 롤을 한 번씩 수집·리포트하고 끝납니다. 2026-10-10부터 운영합니다.

**cron-job.org 작업**

| 항목 | 값 |
|---|---|
| URL | `https://api.github.com/repos/inticoy/imposter-finder/actions/workflows/find-imposter.yml/dispatches` |
| 방식 | `POST`, 본문 `{"ref":"main"}` |
| 헤더 | `Authorization: Bearer <GitHub 토큰>`, `Accept: application/vnd.github+json` |
| 주기 | `*/15 * * * *` (Asia/Seoul) |

토큰은 이 레포의 Actions 쓰기 권한(fine-grained: Actions Read and write)이 필요합니다. 만료되면 401로 실패하고, 실패가 이어지면 cron-job.org가 작업을 끕니다. 응답 `204`가 정상입니다.

**시크릿** (Settings → Secrets → Actions)

```bash
gh secret set ENV_FILE < .env      # .env 그대로. BOT_ENV는 workflow에서 prod로 고정
.venv/bin/python -c "import json,sys; sys.stdout.write(json.dumps(json.load(open('players.json')), ensure_ascii=False, separators=(',',':')))" \
  | gh secret set PLAYERS_JSON      # players.json을 한 줄로
```

`PLAYERS_JSON`은 한 줄로 넣습니다. 여러 줄이면 GitHub이 `{`·`]`만 있는 줄까지 가려 로그가 `***`투성이가 됩니다. 친구나 키가 바뀌면 같은 명령으로 다시 올립니다.

**상태와 중복 방지**

- `data/imposter_finder.db`·`data/fc_meta/`는 매 실행 Actions 캐시(`state-<run id>`)에 저장하고 다음 실행이 가장 최근 것을 복원합니다. 이미지 캐시 `data/assets/`는 내용이 바뀔 때만 저장합니다(`assets-<hash>`). 오래된 캐시는 최근 2개만 남기고 지웁니다.
- 캐시가 없으면(첫 실행·캐시 삭제) 그때 보이는 경기는 기록만 하고 올리지 않습니다. 지난 경기가 한꺼번에 가거나 중복으로 가지 않습니다.
- `concurrency`로 한 번에 하나만 실행합니다. 실행 시간은 10분까지(평소 1~3분, 첫 실행 약 4분).
- 맥 `serve`와 동시에 켜면 같은 경기가 두 번 올라갑니다. 하나만 켭니다.

**로그** — 공개 레포라 Actions 로그도 공개입니다. `players.json`의 이름·닉네임·Riot ID·디스코드 ID는 `***`로 가리고, 게임별 건수와 에러만 남깁니다.

```
[pubg] matches=43 published=1 bootstrap=False
[fc] friend_matches=129 published=0 bootstrap=False
[lol] group_matches=66 published=0 bootstrap=False
```

```bash
gh run list --workflow find-imposter.yml --limit 5   # 최근 실행
gh run view <run id> --log                          # 로그
gh workflow run find-imposter.yml                   # 지금 한 번 실행
gh cache list                                        # 상태·이미지 캐시
```

**글꼴** — 레포의 `fonts/`(Pretendard·Teko, OFL)를 써서 맥과 리눅스 러너의 이미지가 같습니다.

## 맥 상주 (launchd) — 꺼 둠

`/user` 슬래시 명령이 필요할 때만 씁니다. Actions와 동시에 켜지 않습니다.

```bash
cp ops/launchd/com.inticoy.imposter-finder.plist ~/Library/LaunchAgents/
launchctl enable gui/$(id -u)/com.inticoy.imposter-finder      # 지금은 disable 상태
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.inticoy.imposter-finder.plist
launchctl print gui/$(id -u)/com.inticoy.imposter-finder        # 상태
tail -f logs/imposter-finder.log logs/imposter-finder-error.log
```

로그인하면 시작하고, 죽으면 10초 뒤 다시 띄웁니다(`KeepAlive`). `.env` 변경은 `launchctl kickstart -k gui/$(id -u)/com.inticoy.imposter-finder`로 재시작해 반영합니다. 끌 때는 `launchctl bootout` 후 `launchctl disable`(재부팅·로그인 때 다시 켜지지 않게).

맥 DB(`data/imposter_finder.db`)와 Actions 캐시의 DB는 서로 따로입니다. 경기 원본과 전송 상태는 Git에 올리지 않습니다.
