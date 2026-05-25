# Implementation Plan

This plan assumes we first prove API access, then build the Discord bot.

## Phase 0: API Verification

Goal: prove which games expose the exact data needed for 범인찾기.

Use:

```bash
python3 tools/verify_game_apis.py all
```

### PUBG

Verify:

- Player names resolve on the selected platform.
- Recent match IDs are returned.
- Match detail includes participants and stats.
- Telemetry URL exists.

Decision:

- If OK, make PUBG the first MVP integration.

### FC Online

Verify:

- Nickname resolves to OUID.
- Match list returns data for the desired match type.
- Match detail has useful per-player fields.

Run several match types:

```env
FC_MATCHTYPE=30
FC_MATCHTYPE=40
FC_MATCHTYPE=60
FC_MATCHTYPE=234
```

Decision:

- If regular custom/friendly matches appear in `30`, `40`, or `60`, support FC Online custom 1:1.
- If only `234` works for custom, support Volta custom first.

### LoL

Verify:

- `LOL_RIOT_ID` resolves to PUUID.
- `queue=2400` returns recent match IDs.
- Match detail includes standard participant stats.

Decision:

- If `queue=2400` returns no matches even after a known recent ARAM: Mayhem game, defer LoL Mayhem.
- If it works, support basic post-game stats without augment selection.

### VALORANT

Do not block MVP on this.

Decision:

- Use mock data or defer until Riot Production API / RSO path is worth doing.

## Phase 1: Project Skeleton

Create a Python package:

```text
imposter_finder/
  __main__.py
  config.py
  db.py
  discord.py
  scheduler.py
  games/
    pubg.py
    fconline.py
    lol.py
  analysis/
    pubg.py
    fconline.py
    lol.py
```

Use SQLite:

```text
data/imposter_finder.db
```

Core tables:

```sql
players(
  id INTEGER PRIMARY KEY,
  discord_user_id TEXT,
  display_name TEXT,
  game TEXT NOT NULL,
  platform TEXT,
  external_name TEXT NOT NULL,
  external_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)

matches(
  id INTEGER PRIMARY KEY,
  game TEXT NOT NULL,
  platform TEXT,
  match_id TEXT NOT NULL,
  match_started_at TEXT,
  raw_summary_json TEXT,
  analyzed_at TEXT,
  notified_at TEXT,
  UNIQUE(game, platform, match_id)
)

match_players(
  id INTEGER PRIMARY KEY,
  match_id INTEGER NOT NULL,
  player_id INTEGER,
  external_name TEXT,
  stats_json TEXT,
  FOREIGN KEY(match_id) REFERENCES matches(id),
  FOREIGN KEY(player_id) REFERENCES players(id)
)

reports(
  id INTEGER PRIMARY KEY,
  match_id INTEGER NOT NULL,
  culprit_name TEXT,
  score_json TEXT,
  message_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(match_id) REFERENCES matches(id)
)
```

## Phase 2: First MVP With PUBG

Implement:

- Register PUBG player names in config or DB.
- Poll recent matches.
- Deduplicate by match id.
- Fetch match detail and telemetry.
- Analyze simple culprit score.
- Print dry-run report.

Initial scoring:

| Signal | Weight |
|---|---:|
| First death among registered squad | +30 |
| Very low damage before death | +20 |
| Died far from nearest teammate | +15 |
| Team damage or team kill | +40 |
| No revive attempt while nearby | +10 |
| Vehicle-related team disaster | +25 |

Keep scoring explainable. The report should show why someone was selected.

## Phase 3: Discord Reporting

Start simple:

- Webhook or REST message first.
- Full Discord gateway/slash commands later.

Report format:

```text
[범인찾기] PUBG 새 매치 분석

범인 후보: playerA
이유:
- 첫 번째로 기절/사망
- 73 데미지 후 사망
- 팀과 180m 떨어져 교전 시작

면책:
- 이 결과는 재미용 점수입니다.
```

## Phase 4: FC Online

After matchtype validation:

- Store FC OUIDs.
- Poll selected match types.
- Analyze match detail.
- Account for delayed availability.

Possible report:

```text
[범인찾기] FC 온라인 커스텀 분석

범인 후보: playerB
이유:
- 점유율 62%인데 유효슈팅 1개
- 평균 평점 최저
- 패배
```

## Phase 5: LoL If ARAM Mayhem Works

If `queue=2400` returns matches:

- Store Riot PUUID.
- Poll queue `2400`.
- Analyze standard participant stats.

If `queue=2400` does not return matches:

- Support normal ARAM `queue=450` or defer LoL.

## Phase 6: VALORANT Later

Only start after:

- Riot production approval is realistic.
- RSO flow is acceptable for friends.
- Users are willing to opt in.

Until then, keep the architecture game-adapter based so VALORANT can be added later.

## Immediate Next Steps

1. Add `.env` locally with API keys and test nicknames.
2. Run `python3 tools/verify_game_apis.py pubg`.
3. Run FC Online validation for match types `30`, `40`, `60`, `234`.
4. Run LoL validation with a KR Riot ID that recently played ARAM: Mayhem.
5. Pick MVP game based on successful verification results.
