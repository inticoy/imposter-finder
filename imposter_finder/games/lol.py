from __future__ import annotations

import json
import re
import time
from datetime import datetime
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

REGION = "https://asia.api.riotgames.com"  # account-v1, match-v5 (KR은 asia 라우팅)
PLATFORM = "https://kr.api.riotgames.com"  # 서버별 API (TFT 리그·티어)
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

    def _get(self, path: str, host: str = REGION, **params: Any) -> Any:
        url = f"{host}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
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

    # ── TFT (TFT용 키로 만든 클라이언트에서만) ──
    def tft_match_ids(self, puuid: str, count: int = 10) -> list[str]:
        return self._get(f"/tft/match/v1/matches/by-puuid/{puuid}/ids", start=0, count=count)

    def tft_match(self, match_id: str) -> dict[str, Any]:
        return self._get(f"/tft/match/v1/matches/{match_id}")

    def tft_rank(self, puuid: str) -> dict[str, Any] | None:
        """지금 랭크 티어·LP (경기 당시 값이 아니다). 랭크 기록이 없으면 None."""
        entries = self._get(f"/tft/league/v1/by-puuid/{puuid}", host=PLATFORM)
        return next((e for e in entries if e.get("queueType") == "RANKED_TFT"), None)


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

    def map_image(self, map_id: int = 11) -> str:
        """소환사의 협곡 지도 그림 (공식)."""
        self._refresh()
        return f"{DDRAGON}/cdn/{self.version}/img/map/map{map_id}.png"

    def champion_icon(self, champion_id: str) -> str:
        self._refresh()
        return f"{DDRAGON}/cdn/{self.version}/img/champion/{champion_id}.png"


OPGG = "https://op.gg/ko/lol/summoners/kr"
OPGG_TIME_SLACK_S = 300  # OP.GG의 경기 시각은 끝난 시각 근처라 우리 기록과 몇십 초 어긋난다


def opgg_summoner_url(game_name: str, tag: str) -> str:
    return f"{OPGG}/{urllib.parse.quote(game_name)}-{urllib.parse.quote(tag)}"


def opgg_match_url(participant: dict[str, Any], info: dict[str, Any]) -> str:
    """OP.GG 경기 상세 링크. 경기 ID는 OP.GG 자체 값이라 소환사 페이지의 최근 경기 목록(JSON-LD)에서 찾는다.
    못 찾으면 (아직 OP.GG에 갱신 전 등) 소환사 페이지 링크."""
    summoner = opgg_summoner_url(participant["riotIdGameName"], participant["riotIdTagline"])
    ended_s = (info["gameStartTimestamp"] + info["gameDuration"] * 1000) / 1000
    try:
        req = urllib.request.Request(summoner, headers={"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/141.0"})
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="replace")
        for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
            if "matchId" not in block:
                continue
            for item in _ld_items(json.loads(block)):
                props = {p.get("name"): p.get("value") for p in item.get("additionalProperty", [])}
                when = datetime.fromisoformat(item["startTime"]).timestamp()
                same = (props.get("kills"), props.get("deaths"), props.get("assists")) == (
                    participant["kills"], participant["deaths"], participant["assists"])
                if same and abs(when - ended_s) < OPGG_TIME_SLACK_S and props.get("matchId"):
                    return f"{summoner}/matches/{urllib.parse.quote(props['matchId'], safe='')}/{int(when) * 1000}"
    except Exception as exc:  # OP.GG 페이지가 바뀌거나 막혀도 리포트는 보낸다
        print(f"[lol] opgg lookup failed: {exc}")
    return summoner


def opgg_link(participants: list[dict[str, Any]], info: dict[str, Any]) -> str | None:
    """친구들 페이지를 차례로 보며 경기 상세 링크를 찾고, 없으면 첫 친구의 소환사 페이지."""
    fallback = None
    for participant in participants:
        url = opgg_match_url(participant, info)
        if "/matches/" in url:
            return url
        fallback = fallback or url
    return fallback


def _ld_items(node: Any) -> list[dict]:
    """JSON-LD 안의 PlayGameAction(경기)들을 모두 꺼낸다."""
    found = []
    if isinstance(node, dict):
        if node.get("@type") == "PlayGameAction":
            found.append(node)
        for value in node.values():
            found += _ld_items(value)
    elif isinstance(node, list):
        for value in node:
            found += _ld_items(value)
    return found
