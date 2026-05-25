# Player Registry

친구별 Discord ID와 게임 닉네임은 `players.json`에 저장한다.

실제 `players.json`은 개인 정보가 들어가므로 커밋하지 않는다. 저장소에는 `players.example.json`만 예시로 둔다.

## Shape

```json
{
  "players": [
    {
      "name": "준호",
      "discord_user_id": "123456789012345678",
      "accounts": {
        "pubg": {
          "platform": "steam",
          "nickname": "PubgNickA"
        },
        "fconline": {
          "nickname": "FcOwnerNickA"
        },
        "lol": {
          "riot_id": "GameNameA#KR1"
        }
      }
    }
  ]
}
```

## Fields

| Field | Meaning |
|---|---|
| `name` | Discord 리포트에 표시할 친구 이름 |
| `discord_user_id` | Discord 멘션용 사용자 ID |
| `accounts.pubg.platform` | `steam` 또는 `kakao` |
| `accounts.pubg.nickname` | PUBG 닉네임 |
| `accounts.fconline.nickname` | FC Online 구단주 닉네임 |
| `accounts.lol.riot_id` | Riot ID, 예: `닉네임#태그` |

## Runtime Use

앱은 `players.json`을 읽고 게임별 API 조회에 필요한 닉네임만 추출한다.

예를 들어 PUBG 조회 시에는 다음 값만 사용한다.

```text
name
discord_user_id
accounts.pubg.platform
accounts.pubg.nickname
```

PUBG API에서 account id를 얻으면 SQLite에 캐싱한다. `players.json`에는 사람이 직접 관리하는 값만 둔다.

```text
name: 준호
pubg nickname: PubgNickA
pubg account id: account.xxx...  -> DB에 캐싱
```

리포트에서는 `name`과 `discord_user_id`를 우선 사용한다.

```text
범인 후보: <@123456789012345678> (준호 / PubgNickA)
```
