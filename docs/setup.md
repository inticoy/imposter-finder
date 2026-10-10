# 설정

## 설치

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                    # 토큰·thread ID·API 키
cp players.example.json players.json    # 친구 목록 (커밋하지 않음)
```

글꼴은 레포의 `fonts/`를 쓰므로 따로 설치하지 않습니다.

## `.env`

| 변수 | 설명 |
|---|---|
| `BOT_ENV` | `dev` / `prod`: 게임별 thread 중 어느 쪽으로 보낼지 |
| `DISCORD_BOT_TOKEN` | Discord 봇 토큰 |
| `DISCORD_THREAD_PUBG_DEV` / `_PROD` | 배그 리포트 thread |
| `DISCORD_THREAD_LOL_DEV` / `_PROD` | 롤 리포트 thread |
| `DISCORD_THREAD_FC_DEV` / `_PROD` | FC 리포트 thread |
| `PUBG_API_KEY` | [PUBG Developer](https://developer.pubg.com) |
| `PUBG_MAX_MATCH_AGE_HOURS` | 이보다 오래된 배그 경기는 알리지 않음 (기본 12) |
| `RIOT_API_KEY` | [Riot Developer](https://developer.riotgames.com) 개인 키 (2분 100회) |
| `NEXON_API_KEY` | [NEXON Open API](https://openapi.nexon.com) (`test_` 키 하루 1,000회, 900회에서 멈춤) |
| `GEMINI_API_KEY` | 총평·평가. 없으면 평가 없이 보냄 |
| `HEALTHCHECK_IMPOSTER_FINDER_URL` | 선택: 실행마다 Healthchecks.io 핑, 한 게임이라도 실패하면 `/fail` |
| `DISCORD_GUILD_ID` | 선택: `/user` 슬래시 명령을 이 서버에 바로 반영 (상주 실행일 때) |
| `*_POLL_INTERVAL_MINUTES` | 선택: 상주 실행(`serve`)의 수집 주기. Actions는 cron-job.org 주기를 따름 |

thread를 비워 둔 게임은 수집하지 않습니다.

## `players.json`

사람마다 Discord ID와 게임 계정을 적습니다. 등록된 친구가 두 명 이상 함께한 경기만 리포트합니다.

```json
{"players": [{"name": "철수", "discord_user_id": "123456789012345678",
  "accounts": {"pubg": {"platform": "steam", "nickname": "PubgNickA"},
               "fconline": {"nickname": "FcNickA"},
               "lol": {"riot_ids": ["GameNameA#KR1", "SubAccount#KR1"]}}}]}
```

- `name`: 판독 문장에 쓰는 이름. 리포트에서 Discord로 태그합니다 (@silent라 알림은 가지 않음).
- 게임이 없는 사람은 그 계정을 빼면 됩니다. 롤은 한 사람이 여러 계정을 적을 수 있습니다.

## 실행

```bash
.venv/bin/python -m imposter_finder collect   # 배그·FC·롤을 한 번 수집·리포트하고 끝 (운영은 이걸 Actions에서)
.venv/bin/python -m imposter_finder serve     # 상주: /user 슬래시 명령 + 15분 수집 루프
.venv/bin/python -m imposter_finder bot       # /user 슬래시 명령만
```

`BOT_ENV=dev`로 돌리면 dev thread(베타)로 보냅니다. 상주 실행과 Actions를 함께 켜면 DB가 따로라 같은 경기가 두 번 올라가니 하나만 씁니다.

## GitHub Actions + cron-job.org

`.github/workflows/find-imposter.yml`은 `workflow_dispatch`로만 실행합니다. GitHub의 `schedule`은 늦거나 빠지고, 활동이 없는 공개 레포에서는 60일 뒤 꺼지기 때문에 cron-job.org가 15분마다 부릅니다.

**cron-job.org 작업**

| 항목 | 값 |
|---|---|
| URL | `https://api.github.com/repos/<owner>/imposter-finder/actions/workflows/find-imposter.yml/dispatches` |
| 방식 | `POST`, 본문 `{"ref":"main"}` |
| 헤더 | `Authorization: Bearer <GitHub 토큰>`, `Accept: application/vnd.github+json` |
| 주기 | `*/15 * * * *` |

토큰은 이 레포의 Actions 쓰기 권한(fine-grained: Actions Read and write)이 필요합니다. 정상 응답은 `204`입니다. 토큰이 만료되면 401로 실패하고, 실패가 이어지면 cron-job.org가 작업을 끕니다.

**시크릿**

```bash
gh secret set ENV_FILE < .env      # BOT_ENV는 workflow에서 prod로 고정
.venv/bin/python -c "import json,sys; sys.stdout.write(json.dumps(json.load(open('players.json')), ensure_ascii=False, separators=(',',':')))" \
  | gh secret set PLAYERS_JSON
```

`PLAYERS_JSON`은 한 줄로 넣습니다. 여러 줄이면 GitHub이 `{`·`]`만 있는 줄까지 가려 로그가 `***`투성이가 됩니다. 친구나 키가 바뀌면 같은 명령으로 다시 올립니다.

**상태**

- DB(`data/imposter_finder.db`)와 FC 메타(`data/fc_meta/`)는 매 실행 Actions 캐시 `state-<run id>`에 저장하고, 다음 실행이 가장 최근 것을 복원합니다.
- 이미지 캐시(`data/assets/`)는 내용이 바뀔 때만 `assets-<hash>`로 저장합니다. 둘 다 최근 2개만 남깁니다.
- 캐시가 없으면(처음, 또는 캐시가 사라졌을 때) 그때 보이는 경기는 기록만 하고 올리지 않습니다. 지난 경기가 한꺼번에 가거나 중복으로 가지 않습니다.
- `concurrency`로 한 번에 하나만 실행합니다. 평소 1~3분, 처음 약 4분, 10분이 넘으면 멈춥니다.

**로그** — 공개 레포라 Actions 로그도 공개입니다. `players.json`의 이름·닉네임·Riot ID·Discord ID는 `***`로 가리고, 게임별 건수와 에러만 남깁니다.

```
[pubg] matches=43 published=1 bootstrap=False
[fc] friend_matches=23 published=0 bootstrap=False
[lol] group_matches=19 published=0 bootstrap=False
```

```bash
gh run list --workflow find-imposter.yml --limit 5   # 최근 실행
gh run view <run id> --log                          # 로그
gh workflow run find-imposter.yml                   # 지금 한 번 실행
gh cache list                                        # 상태·이미지 캐시
```

## README 예시 이미지

실제 경기로 리포트를 만들되 이름·닉네임을 가명으로 바꾸고, 디스코드에 올라온 모습(봇 이름·멘션·색 띠 카드·총평/평가·버튼)으로 합성해 `docs/images/report-{pubg,lol,fc}`에 저장합니다. 리포트 원본은 `data/samples/`(커밋 안 함)에 남습니다.

```bash
PYTHONPATH=. .venv/bin/python tools/readme_samples.py --pubg <match id> --lol <match id> --fc <match id>
PYTHONPATH=. .venv/bin/python tools/readme_samples.py --compose-only   # API 호출 없이 합성만 다시
```
