# API Keys

이 문서는 각 게임 API 키를 어디서 발급받고, `.env`에 어떤 이름으로 넣을지 정리한다.

Do not commit `.env`.

## `.env` Template

```env
RIOT_API_KEY=
LOL_RIOT_ID=
LOL_QUEUE_ID=2400

PUBG_API_KEY=
PUBG_PLATFORM=steam
PUBG_PLAYER_NAMES=

NEXON_API_KEY=
FC_NICKNAME=
FC_MATCHTYPE=234
```

## Riot API Key

Used for:

- League of Legends
- Potentially VALORANT, but VALORANT requires additional approval and RSO for real player data.

### Where To Get It

1. Go to https://developer.riotgames.com/
2. Sign in with a Riot account.
3. Open the Developer Portal dashboard.
4. Create or open an application.
5. Use the development API key for local LoL testing.
6. Put it in `.env`:

```env
RIOT_API_KEY=RGAPI-...
```

### Important Notes

- Riot development keys expire regularly, commonly every 24 hours. Refresh the key when tests start failing with auth errors.
- Keep the key server-side only.
- For LoL KR, account lookup and Match-V5 use regional routing:
  - Account-V1: `asia.api.riotgames.com`
  - Match-V5: `asia.api.riotgames.com`
  - Some other LoL APIs use platform routing such as `kr.api.riotgames.com`.

### LoL Test Variables

```env
LOL_RIOT_ID=gameName#tagLine
LOL_QUEUE_ID=2400
```

Queue values:

- `2400`: ARAM: Mayhem
- `450`: Normal ARAM

### VALORANT Caveat

VALORANT cannot be treated like simple LoL development key testing. Riot docs state that VALORANT apps require user opt-in through Riot Sign On. Production-level access is required for RSO.

Useful links:

- https://developer.riotgames.com/docs/valorant
- https://www.riotgames.com/en/DevRel/valorant-api-launch
- https://support-developer.riotgames.com/hc/en-us/articles/22698769097107-VALORANT

For this project, defer VALORANT until PUBG/FC/LoL proof-of-concept is done.

## PUBG API Key

Used for:

- PUBG player lookup
- Recent match discovery
- Match detail
- Telemetry URL discovery

### Where To Get It

1. Go to https://developer.pubg.com/
2. Sign in or create an account.
3. Open `My Apps`.
4. Create an app.
5. Copy the API key.
6. Put it in `.env`:

```env
PUBG_API_KEY=...
```

### Test Variables

```env
PUBG_PLATFORM=steam
PUBG_PLAYER_NAMES=name1,name2
```

Common platform values:

- `steam`: PUBG on Steam
- `kakao`: Kakao PUBG, if the account is on Kakao

### Important Notes

- Cache player account IDs after the first lookup.
- The default development rate limit is limited, so avoid looking up the same player names every 10 minutes if account IDs are already known.
- PUBG documentation says match and telemetry endpoints are not rate-limited the same way as player lookup.
- Telemetry is the most valuable data source for 범인찾기 analysis.

Useful links:

- https://documentation.pubg.com/en/index.html
- https://documentation.pubg.com/en/getting-started.html
- https://documentation.pubg.com/en/rate-limits.html

## Nexon Open API Key

Used for:

- FC Online nickname to OUID lookup
- FC Online match list
- FC Online match detail

### Where To Get It

1. Go to https://openapi.nexon.com/
2. Sign in with a Nexon account.
3. Open `애플리케이션 등록` / `Register Application`.
4. Create an application.
5. Select or enable FC Online API access if required by the portal.
6. Copy the API key.
7. Put it in `.env`:

```env
NEXON_API_KEY=...
```

The verification script also accepts:

```env
NXOPEN_API_KEY=...
```

### Test Variables

```env
FC_NICKNAME=구단주닉네임
FC_MATCHTYPE=234
```

Test these match types for custom/friendly needs:

```text
30  리그 친선
40  클래식 1on1
60  공식 친선
234 볼타 커스텀
```

### Important Notes

- Nexon docs state FC Online data updates hourly and can reflect only data from about two hours earlier.
- 10-minute polling is still useful for detecting newly available records, but the bot should not promise instant post-game reports.
- Open API data pulled from Nexon must be refreshed within 30 days according to the notice on the Nexon API docs.

Useful links:

- https://openapi.nexon.com/game/fconline/
- https://openapi.nexon.com/game/fconline/?id=3
- https://openapi.nexon.com/ko/game/fconline/?id=5

## Discord Bot Token

Not needed for API feasibility validation, but needed once the reporting bot is implemented.

### Where To Get It

1. Go to https://discord.com/developers/applications
2. Create an application.
3. Add a bot under the Bot section.
4. Copy the bot token.
5. Invite the bot to the target server with permissions to send messages and create threads if needed.

Expected future variables:

```env
DISCORD_BOT_TOKEN=
DISCORD_CHANNEL_ID=
```

For MVP reporting, a Discord webhook may be simpler than a full gateway bot if slash commands are not required yet.
