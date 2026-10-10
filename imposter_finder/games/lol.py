from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

REGION = "https://asia.api.riotgames.com"  # account-v1, match-v5 (KR은 asia 라우팅)
DDRAGON = "https://ddragon.leagueoflegends.com"
CDRAGON_ICONS = "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-match-history/global/default"
QUEUE_NAMES = {400: "일반", 420: "솔로랭크", 430: "일반", 440: "자유랭크", 450: "칼바람", 490: "빠른 대전",
               1700: "아레나", 1750: "아레나", 2400: "증강 칼바람"}
ARENA_QUEUES = {1700, 1750}
# 개인 키 한도는 2분에 100회라 요청 사이를 넉넉히 띄운다
REQUEST_GAP_S = 1.3


class RiotApiError(RuntimeError):
    pass


class RiotClient:
    def __init__(self, api_key: str, timeout: int = 20) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def _get(self, path: str, **params: Any) -> Any:
        url = f"{REGION}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        # Python 기본 User-Agent는 403으로 막혀서 직접 지정한다
        req = urllib.request.Request(url, headers={"X-Riot-Token": self.api_key, "User-Agent": "imposter-finder/0.1"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 2:
                    time.sleep(int(exc.headers.get("Retry-After", "10")) + 1)
                    continue
                raise RiotApiError(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:200]}") from exc
            finally:
                time.sleep(REQUEST_GAP_S)
        raise RiotApiError("rate limited")

    def puuid(self, riot_id: str) -> str:
        name, tag = riot_id.rsplit("#", 1)
        return self._get(f"/riot/account/v1/accounts/by-riot-id/{urllib.parse.quote(name)}/{urllib.parse.quote(tag)}")["puuid"]

    def match_ids(self, puuid: str, count: int = 10) -> list[str]:
        return self._get(f"/lol/match/v5/matches/by-puuid/{puuid}/ids", start=0, count=count)

    def match(self, match_id: str) -> dict[str, Any]:
        return self._get(f"/lol/match/v5/matches/{match_id}")

    def timeline(self, match_id: str) -> dict[str, Any]:
        return self._get(f"/lol/match/v5/matches/{match_id}/timeline")


class DDragon:
    """Riot 공식 정적 데이터: 최신 버전, 챔피언 한글 이름과 아이콘. 하루에 한 번 갱신."""

    def __init__(self) -> None:
        self._loaded_at = 0.0
        self.version = ""
        self.ko_names: dict[str, str] = {}

    def _refresh(self) -> None:
        if time.time() - self._loaded_at < 24 * 3600:
            return
        get = lambda url: json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "imposter-finder/0.1"}), timeout=30).read())
        self.version = get(f"{DDRAGON}/api/versions.json")[0]
        champs = get(f"{DDRAGON}/cdn/{self.version}/data/ko_KR/champion.json")["data"]
        self.ko_names = {c["id"]: c["name"] for c in champs.values()}
        self._loaded_at = time.time()

    def champion_name(self, champion_id: str) -> str:
        self._refresh()
        return self.ko_names.get(champion_id, champion_id)

    def champion_icon(self, champion_id: str) -> str:
        self._refresh()
        return f"{DDRAGON}/cdn/{self.version}/img/champion/{champion_id}.png"
