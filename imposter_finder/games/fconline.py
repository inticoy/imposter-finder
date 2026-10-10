from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

API_BASE = "https://open.api.nexon.com/fconline/v1"
META_BASE = "https://open.api.nexon.com/static/fconline/meta"
IMAGE_BASE = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common"
KST = timezone(timedelta(hours=9))
MATCHTYPE_CLASSIC_1ON1 = 40  # 친구끼리 하는 친선전이 이 종류로 잡힌다


class NexonApiError(RuntimeError):
    pass


class NexonQuotaExceeded(NexonApiError):
    pass


class FcOnlineClient:
    """NEXON Open API (FC 온라인). 하루 호출 수를 세서 한도를 넘기지 않는다."""

    def __init__(self, api_key: str, count_call: Callable[[str], int], daily_limit: int, timeout: int = 15) -> None:
        self.api_key = api_key
        self.count_call = count_call
        self.daily_limit = daily_limit
        self.timeout = timeout

    def _get(self, path: str, **params: Any) -> Any:
        used = self.count_call(datetime.now(KST).strftime("%Y-%m-%d"))
        if used > self.daily_limit:
            raise NexonQuotaExceeded(f"NEXON API daily limit reached ({used - 1}/{self.daily_limit})")
        url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"x-nxopen-api-key": self.api_key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise NexonApiError(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}") from exc
        finally:
            time.sleep(0.2)  # 동시·연속 요청이 몰리면 403이 난 사례가 있다

    def ouid(self, nickname: str) -> str:
        return self._get("/id", nickname=nickname)["ouid"]

    def match_ids(self, ouid: str, matchtype: int = MATCHTYPE_CLASSIC_1ON1, limit: int = 20) -> list[str]:
        return self._get("/user/match", ouid=ouid, matchtype=matchtype, offset=0, limit=limit)

    def match_detail(self, match_id: str) -> dict[str, Any]:
        return self._get("/match-detail", matchid=match_id)


class FcMeta:
    """선수 이름·시즌 메타데이터. 하루에 한 번 받아 파일로 둔다 (키 불필요)."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self._names: dict[int, str] | None = None
        self._seasons: dict[int, str] | None = None

    def _load(self, name: str) -> list[dict[str, Any]]:
        path = self.cache_dir / f"{name}.json"
        if not path.exists() or time.time() - path.stat().st_mtime > 24 * 3600:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(f"{META_BASE}/{name}.json", timeout=60) as resp:
                path.write_bytes(resp.read())
        return json.loads(path.read_text(encoding="utf-8"))

    def player_name(self, spid: int) -> str:
        if self._names is None:
            self._names = {item["id"]: item["name"] for item in self._load("spid")}
        return self._names.get(spid, str(spid))

    def season_image(self, spid: int) -> str | None:
        if getattr(self, "_season_images", None) is None:
            self._season_images = {item["seasonId"]: item.get("seasonImg") for item in self._load("seasonid")}
        return self._season_images.get(spid // 1000000)

    def season_name(self, spid: int) -> str:
        if self._seasons is None:
            self._seasons = {item["seasonId"]: item["className"].split(" (")[0] for item in self._load("seasonid")}
        return self._seasons.get(spid // 1000000, "")


DATACENTER = "https://fconline.nexon.com/datacenter"
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"}
TEAMCOLOR_SINGLE = 90000000  # '단일팀'은 모든 선수에게 붙어 팀컬러로 치지 않는다
TEAMCOLOR_MIN_PLAYERS = 3


class FcTeamColors:
    """팀컬러: 공식 API에 없어 FC 온라인 데이터센터 웹페이지를 읽는다. 선수별 결과는 파일에 캐시."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self._table: dict[int, tuple[str, str]] | None = None
        self._players: dict[str, list[int]] | None = None

    def _fetch(self, path: str, data: dict[str, Any] | None = None) -> str:
        body = urllib.parse.urlencode(data).encode() if data else None
        req = urllib.request.Request(f"{DATACENTER}/{path}", data=body, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def table(self) -> dict[int, tuple[str, str]]:
        """팀컬러 번호 → (이름, 엠블럼·국기 이미지 큰 것). 일주일에 한 번 새로 받는다."""
        if self._table is None:
            path = self.cache_dir / "teamcolors.json"
            if not path.exists() or time.time() - path.stat().st_mtime > 7 * 24 * 3600:
                table = {}
                for item in self._fetch("teamcolor").split('<div class="teamcolor_item">')[1:]:
                    tid = re.search(r"GetTeamColorDetail\((\d+)\)", item)
                    name = re.search(r'<div class="name">([^<]+)</div>', item)
                    img = re.search(r'<div class="crests[^"]*">\s*<img src="([^"]+)"', item)
                    if tid and name and img:
                        table[tid.group(1)] = (name.group(1).strip(), img.group(1).replace("/medium/", "/large/"))
                if table:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
            self._table = {int(k): tuple(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
        return self._table

    def of_player(self, spid: int) -> list[int]:
        """선수 카드가 가진 소속 팀컬러 번호들."""
        path = self.cache_dir / "player_teamcolors.json"
        if self._players is None:
            self._players = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        key = str(spid)
        if key not in self._players:
            html = self._fetch("PlayerAbility", {"spid": spid, "n1Strong": 1})
            self._players[key] = sorted({int(n) for n in re.findall(r'class="selector_item tdefault(\d+)"', html)} - {0})
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._players), encoding="utf-8")
            time.sleep(0.2)
        return self._players[key]

    def of_squad(self, spids: list[int]) -> tuple[str, str] | None:
        """선발 선수들이 가장 많이 공유하는 클럽·국가 팀컬러 (이름, 이미지). 3명 미만이면 없음."""
        table = self.table()
        counts: dict[int, int] = {}
        for spid in spids:
            try:
                ids = self.of_player(spid)
            except Exception as exc:  # 웹페이지가 바뀌거나 막혀도 리포트는 보낸다
                print(f"[fc] teamcolor failed {spid}: {exc}")
                continue
            for tid in ids:
                if tid != TEAMCOLOR_SINGLE and tid in table:
                    counts[tid] = counts.get(tid, 0) + 1
        if not counts:
            return None
        # 많이 겹치는 순, 같으면 클럽(1xxx) > 국가(2xxx) > 특수
        best = max(counts, key=lambda t: (counts[t], 1000 <= t < 2000, 2000 <= t < 3000))
        return table[best] if counts[best] >= TEAMCOLOR_MIN_PLAYERS else None


class FcPrices:
    """선수 시세 (강화 단계별). 데이터센터 선수 검색 결과를 하루 동안 파일에 캐시한다."""

    TTL_S = 24 * 3600

    def __init__(self, cache_dir: Path, meta: "FcMeta") -> None:
        self.path = cache_dir / "prices.json"
        self.meta = meta
        self._cache: dict[str, list] | None = None  # spid → [받은 시각, 강화별 가격 목록]

    def _load(self) -> dict[str, list]:
        if self._cache is None:
            self._cache = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        return self._cache

    def price(self, spid: int, grade: int) -> int | None:
        cache = self._load()
        entry = cache.get(str(spid))
        if entry is None or time.time() - entry[0] > self.TTL_S:
            name = self.meta.player_name(spid)
            url = f"https://fconline.nexon.com/DataCenter/SquadMakerPlayerList?strPlayerName={urllib.parse.quote(name)}"
            req = urllib.request.Request(url, headers=BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                players = json.loads(resp.read().decode("utf-8")).get("players", [])
            now = time.time()
            for p in players:  # 같은 이름의 다른 시즌도 함께 저장해 다음 조회를 줄인다
                cache[str(p["spid"])] = [now, [int(v.replace(",", "") or 0) for v in p["eachPrice"].split("|")]]
            cache.setdefault(str(spid), [now, []])
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(cache), encoding="utf-8")
            time.sleep(0.2)
            entry = cache[str(spid)]
        prices = entry[1]
        return prices[grade] if 0 <= grade < len(prices) else None

    def squad_value(self, players: list[dict[str, Any]]) -> int | None:
        """출전 명단(선발+교체) 선수 가격 합. 하나라도 못 구하면 그 선수는 빼고 더한다."""
        total, found = 0, 0
        for p in players:
            try:
                value = self.price(p["spId"], p["spGrade"])
            except Exception as exc:
                print(f"[fc] price failed {p['spId']}: {exc}")
                value = None
            if value:
                total, found = total + value, found + 1
        return total if found else None


def format_bp(value: int) -> str:
    """FC 온라인처럼: 12조 3,400억 / 2억 3,600만."""
    jo, rest = divmod(value, 10 ** 12)
    eok, rest = divmod(rest, 10 ** 8)
    man = rest // 10 ** 4
    if jo:
        return f"{jo:,}조 {eok:,}억" if eok else f"{jo:,}조"
    if eok:
        return f"{eok:,}억 {man:,}만" if man else f"{eok:,}억"
    return f"{man:,}만"


@lru_cache(maxsize=1024)
def face_image(spid: int) -> str:
    """시즌 얼굴(그 시즌 유니폼, 128px 상반신)이 있으면 그것, 없으면 기본 얼굴."""
    return player_image(spid) or f"{IMAGE_BASE}/players/p{spid % 1000000}.png"


@lru_cache(maxsize=1024)
def player_image(spid: int) -> str | None:
    """시즌 전용 이미지가 없는 선수가 있어, 있는 이미지를 순서대로 찾는다."""
    pid = spid % 1000000
    for url in (f"{IMAGE_BASE}/playersAction/p{spid}.png", f"{IMAGE_BASE}/playersAction/p{pid}.png",
                f"{IMAGE_BASE}/players/p{pid}.png"):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=10) as resp:
                if resp.status == 200:
                    return url
        except urllib.error.URLError:
            continue
    return None
