# Game API Capabilities

이 문서는 범인찾기 봇이 게임별로 가져올 수 있는 정보와 구현 가능성을 정리한다.

## Common Bot Flow

공통 흐름은 다음과 같다.

1. Discord 사용자와 게임 계정을 연결한다.
2. 게임별 고유 계정 ID를 저장한다.
3. 10분마다 최근 매치 목록을 조회한다.
4. 이미 처리한 match id는 건너뛴다.
5. 새 match id가 있으면 상세 데이터를 가져온다.
6. 게임별 규칙으로 범인 후보를 계산한다.
7. Discord 채널이나 스레드에 결과를 보낸다.

## League of Legends

### Status

일반 Match-V5 흐름은 가능하다. KR 계정도 지원된다.  
증강 칼바람 / ARAM: Mayhem은 `queueId=2400`이 존재하지만, 실제 KR 최근 매치가 Match-V5에서 반환되는지는 API key로 검증해야 한다.

### Official References

- Riot LoL docs: https://developer.riotgames.com/docs/lol
- Riot API list: https://developer.riotgames.com/apis/
- Queue IDs: https://static.developer.riotgames.com/docs/lol/queues.json

### Required Inputs

- Riot API key
- Riot ID: `gameName#tagLine`
- Queue ID
  - ARAM: Mayhem: `2400`
  - Normal ARAM: `450`

### Useful Endpoints

Account lookup:

```text
GET https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}
```

Recent match ids:

```text
GET https://asia.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=5&queue=2400
```

Match detail:

```text
GET https://asia.api.riotgames.com/lol/match/v5/matches/{matchId}
```

### Data We Can Use

If Match-V5 returns the match, the bot can use:

- match id
- queue id
- game mode / game type
- champion
- win/loss
- kills, deaths, assists
- total damage dealt to champions
- total damage taken
- gold earned
- items
- participant list
- team-level win/loss

Potentially useful if timeline is added later:

- death timestamps
- kill events
- assists
- objective events
- first blood

### Analysis Ideas

- Most deaths
- Lowest damage share
- Lowest KDA
- Died earliest or repeatedly
- High gold but low contribution
- Lost game with unusually low champion damage
- Fun labels such as "다리 위 관광객", "골드 기부왕", "먼저 누운 사람"

### Current Risk

ARAM: Mayhem may not be exposed through official Match-V5 even though queue metadata exists. This is the first LoL validation target.

## VALORANT

### Status

Technically possible, but not a good first MVP target. VALORANT personal match history access usually requires Production API access, Riot Sign On, and user opt-in.

### Official References

- VALORANT docs: https://developer.riotgames.com/docs/valorant
- VALORANT Developer Relations: https://support-developer.riotgames.com/hc/en-us/articles/22698769097107-VALORANT
- VALORANT API launch policy: https://www.riotgames.com/en/DevRel/valorant-api-launch

### Required Inputs

- Riot Production API key with VALORANT access
- RSO client
- Each user's opt-in / Riot Sign On link
- PUUID

### Useful Endpoints

Matchlist:

```text
GET /val/match/v1/matchlists/by-puuid/{puuid}
```

Match detail:

```text
GET /val/match/v1/matches/{matchId}
```

### Data We Expect

Once approved and authorized, likely useful fields include:

- agent
- team
- rounds won/lost
- kills, deaths, assists
- score
- damage
- headshots/bodyshots/legshots
- economy and loadout data, depending on response
- round results

### Analysis Ideas

- Lowest ACS/score among friends
- Entry deaths without trade
- Low damage despite full buys
- High deaths in first contact
- Clutch failures or spike mistakes if round-level data supports it

### Current Risk

Personal/dev keys do not make this easy. Riot approval and opt-in flow are product-level work, so use mock data until the bot has value from PUBG/FC/LoL.

## PUBG

### Status

Best first target. Official API exposes player lookup, match ids, match detail, and telemetry. Telemetry gives the richest "범인찾기" data.

### Official References

- PUBG docs: https://documentation.pubg.com/en/index.html
- Getting Started: https://documentation.pubg.com/en/getting-started.html
- Players: https://documentation.pubg.com/en/players-endpoint.html
- Matches: https://documentation.pubg.com/en/matches-endpoint.html
- Telemetry: https://documentation.pubg.com/en/telemetry.html
- Rate limits: https://documentation.pubg.com/en/rate-limits.html

### Required Inputs

- PUBG API key
- Platform shard
  - Steam: `steam`
  - Kakao PUBG: likely `kakao`
- Player names

### Useful Endpoints

Player lookup:

```text
GET https://api.pubg.com/shards/{platform}/players?filter[playerNames]={name1,name2}
```

Match detail:

```text
GET https://api.pubg.com/shards/{platform}/matches/{matchId}
```

Telemetry URL is found in the match detail `included` asset object.

### Data We Can Use

From match detail:

- match id
- game mode
- map
- participants
- roster/team
- kills
- damage dealt
- DBNOs
- assists
- boosts/heals
- revives
- time survived
- win placement

From telemetry:

- kill events
- damage events
- groggy/knock events
- revive events
- item pickup/drop/use events
- vehicle ride/leave/damage/destroy events
- player positions
- zone/phase changes
- throwable use
- friendly fire indicators if exposed in damage events

### Analysis Ideas

PUBG can support the richest "범인" scoring:

- First death
- First knock
- Died far from squad
- Did little damage before dying
- Took vehicle and caused disaster
- Team damage or team kill
- No revive attempt nearby
- Looted too long
- Over-peeked and triggered wipe
- High damage but no kills
- Last survivor who hid too long

### Current Risk

Default development rate limit is limited, so cache account IDs and match IDs. Match and telemetry requests are more forgiving according to PUBG docs, but player lookup should still be minimized.

## FC Online

### Status

Possible for match history, but not real-time. Nexon states data updates hourly and can lag by about two hours. 볼타 커스텀 is explicitly listed as `matchtype=234`. General custom/friendly 1:1 needs real account validation across `30`, `40`, and `60`.

### Official References

- Nexon FC Online API: https://openapi.nexon.com/game/fconline/
- FC Online match docs: https://openapi.nexon.com/game/fconline/?id=3
- FC Online metadata docs: https://openapi.nexon.com/ko/game/fconline/?id=5
- Matchtype metadata URL: https://open.api.nexon.com/static/fconline/meta/matchtype.json

### Required Inputs

- Nexon Open API key
- FC Online nickname
- Matchtype

### Match Types

Verified by fetching `matchtype.json`:

| matchtype | Description |
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

### Useful Endpoints

Nickname to OUID:

```text
GET https://open.api.nexon.com/fconline/v1/id?nickname={nickname}
```

User match list:

```text
GET https://open.api.nexon.com/fconline/v1/user/match?ouid={ouid}&matchtype={matchtype}&offset=0&limit=5
```

Match detail:

```text
GET https://open.api.nexon.com/fconline/v1/match-detail?matchid={matchId}
```

### Data We Can Use

Expected from match detail:

- match id
- match date
- match type
- player nicknames
- result
- shoot total
- effective shoot total
- possession
- average rating
- goals, assists, pass, defense, player-level details if present in response

### Analysis Ideas

- Lost with high possession but no shots
- Very low effective shots
- Low average rating
- Defensive mistakes if detailed data supports it
- Goalkeeper/player rating collapse
- For Volta, low contribution or repeated mistakes if detailed fields support it

### Current Risk

The user specifically cares about custom matches. 볼타 커스텀 is confirmed, but regular custom 1:1 may be represented by 친선/classic match types or may not be exposed as expected. Validate with a recent known custom match.

Also, FC Online is delayed. This bot can report after data appears, not immediately after the match ends.
