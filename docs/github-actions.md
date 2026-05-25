# GitHub Actions

이 프로젝트는 GitHub Actions에서 주기적으로 PUBG 최근 매치를 확인하고, 새 매치가 있으면 Discord에 리포트를 보낸 뒤 `data/seen_matches.json`을 커밋하는 방식으로 운영할 수 있다.

PUBG 분석 대상은 `players.json`에 등록된 친구가 2명 이상 참여한 매치로 제한된다.

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

## dev / prod

`BOT_ENV`가 `dev`면 `DISCORD_THREAD_DEV`를 우선 사용하고, 없으면 `DISCORD_CHANNEL_DEV`로 보낸다.

`BOT_ENV`가 `prod`면 `DISCORD_THREAD_PROD`를 우선 사용하고, 없으면 `DISCORD_CHANNEL_PROD`로 보낸다.

수동 실행은 Actions 탭의 `PUBG Imposter Finder` workflow에서 가능하다.

- `bot_env=dev`: 테스트 채널로 전송
- `bot_env=prod`: 실제 채널로 전송
- `send_discord=false`: Discord 전송 없이 로그와 상태 파일만 확인
- `include_seen=true`: 이미 처리한 매치도 재분석

스케줄 실행은 기본적으로 `prod`로 동작한다.

## Local Test

로컬에서는 `.env`와 `players.json`을 사용한다.

Discord 전송 없이 분석만 확인:

```bash
python3 -B -m imposter_finder pubg --include-seen --max-matches 1 --no-save-state
```

dev 채널로 Discord 전송까지 확인:

```bash
BOT_ENV=dev python3 -B -m imposter_finder pubg --include-seen --max-matches 1 --send-discord --no-save-state
```

prod 채널로 전송:

```bash
BOT_ENV=prod python3 -B -m imposter_finder pubg --include-seen --max-matches 1 --send-discord --no-save-state
```

로컬에서 새 매치만 처리:

```bash
python3 -B -m imposter_finder pubg --max-matches 3 --send-discord
```

## Discord Channel Type

일반 텍스트 채널이면 메시지를 보낸다.

`DISCORD_THREAD_*`가 있으면 해당 고정 thread에 메시지를 계속 단다.

thread ID가 없고 대상이 포럼 채널이면 매치마다 새 thread를 생성한다. 봇에는 해당 채널에 메시지/스레드를 만들 권한이 있어야 한다.

Discord 전송은 embed 형식을 사용한다. embed에는 제목, 매치 요약, 친구별 요약, 한 줄 요약이 들어간다.
