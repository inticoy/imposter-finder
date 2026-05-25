# GitHub Actions

이 프로젝트는 GitHub Actions에서 주기적으로 PUBG 최근 매치를 확인하고, 새 매치가 있으면 Discord에 리포트를 보낸 뒤 `data/seen_matches.json`을 커밋하는 방식으로 운영할 수 있다.

PUBG 분석 대상은 `players.json`에 등록된 친구가 2명 이상 참여한 매치로 제한된다.

기본적으로 최근 12시간 이내 매치만 새 알림 후보로 본다. 이 제한은 오래된 backlog가 뒤늦게 전송되는 것을 막기 위한 것이다.

## Required Secrets

Repository Settings > Secrets and variables > Actions에 다음 secrets를 등록한다.

| Secret | Meaning |
|---|---|
| `PUBG_API_KEY` | PUBG Developer API key |
| `PLAYERS_JSON` | 로컬 `players.json` 전체 내용 |
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `DISCORD_CHANNEL_DEV` | dev 테스트 채널 또는 포럼 채널 ID |
| `DISCORD_CHANNEL_PROD` | prod 실제 알림 채널 또는 포럼 채널 ID |
| `DISCORD_THREAD_DEV` | 선택: dev 고정 thread ID |
| `DISCORD_THREAD_PROD` | 선택: prod 고정 thread ID |

## Optional Variables

Repository Settings > Secrets and variables > Actions > Variables에 다음 값을 등록할 수 있다.

| Variable | Default | Meaning |
|---|---:|---|
| `PUBG_MAX_MATCH_AGE_HOURS` | `12` | 이 시간보다 오래된 PUBG 매치는 알림 후보에서 제외 |

## dev / prod

`BOT_ENV`가 `dev`면 `DISCORD_THREAD_DEV`를 우선 사용하고, 없으면 `DISCORD_CHANNEL_DEV`로 보낸다.

`BOT_ENV`가 `prod`면 `DISCORD_THREAD_PROD`를 우선 사용하고, 없으면 `DISCORD_CHANNEL_PROD`로 보낸다.

수동 실행은 Actions 탭의 `PUBG Imposter Finder` workflow에서 가능하다.

- `bot_env=dev`: 테스트 채널로 전송
- `bot_env=prod`: 실제 채널로 전송
- `send_discord=false`: Discord 전송 없이 로그와 상태 파일만 확인
- `include_seen=true`: 이미 처리한 매치도 재분석

스케줄 실행은 기본적으로 `prod`로 동작한다.

## State File

처리한 match id는 `data/seen_matches.json`에 저장된다.

```json
{
  "version": 1,
  "seen_matches": [
    {
      "game": "pubg",
      "platform": "steam",
      "match_id": "match-id",
      "started_at": "2026-05-25T10:00:00Z",
      "processed_at": "2026-05-25T10:10:00+00:00"
    }
  ]
}
```

GitHub Actions는 새 매치를 처리한 뒤 이 파일을 커밋한다. 다음 cron 실행에서는 이 파일에 있는 match id를 건너뛴다.

처음 운영을 시작할 때 `seen_matches.json`이 비어 있더라도, 시간 제한 때문에 오래된 매치가 끝없이 거슬러 올라가 전송되지는 않는다.

## Cron Behavior

스케줄은 10분마다 실행된다.

1. `PLAYERS_JSON` secret으로 `players.json`을 임시 생성한다.
2. PUBG API에서 등록 친구들의 최근 match id를 조회한다.
3. 매치 상세를 가져와 등록 친구가 2명 이상인지 확인한다.
4. `PUBG_MAX_MATCH_AGE_HOURS`보다 오래된 매치는 건너뛴다.
5. `data/seen_matches.json`에 이미 있는 match id는 건너뛴다.
6. 새 매치만 분석하고 Discord에 embed로 전송한다.
7. 처리한 match id를 `data/seen_matches.json`에 저장하고 commit/push한다.

## Local Test

로컬에서는 `.env`와 `players.json`을 사용한다.

Discord 전송 없이 분석만 확인:

```bash
python3 -B -m imposter_finder pubg --include-seen --ignore-age-limit --max-matches 1 --no-save-state
```

dev 채널로 Discord 전송까지 확인:

```bash
BOT_ENV=dev python3 -B -m imposter_finder pubg --include-seen --ignore-age-limit --max-matches 1 --send-discord --no-save-state
```

prod 채널로 전송:

```bash
BOT_ENV=prod python3 -B -m imposter_finder pubg --include-seen --ignore-age-limit --max-matches 1 --send-discord --no-save-state
```

로컬에서 새 매치만 처리:

```bash
python3 -B -m imposter_finder pubg --max-matches 3 --send-discord
```

오래된 매치를 테스트하려면 `--ignore-age-limit`를 붙인다.

후보 목록 확인:

```bash
python3 -B -m imposter_finder pubg --include-seen --ignore-age-limit --list-matches --no-save-state
```

## Discord Channel Type

일반 텍스트 채널이면 메시지를 보낸다.

`DISCORD_THREAD_*`가 있으면 해당 고정 thread에 메시지를 계속 단다.

thread ID가 없고 대상이 포럼 채널이면 매치마다 새 thread를 생성한다. 봇에는 해당 채널에 메시지/스레드를 만들 권한이 있어야 한다.

Discord 전송은 embed 형식을 사용한다. embed에는 제목, 매치 요약, 친구별 요약, 한 줄 요약이 들어간다.
