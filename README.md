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
