from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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

    def season_name(self, spid: int) -> str:
        if self._seasons is None:
            self._seasons = {item["seasonId"]: item["className"].split(" (")[0] for item in self._load("seasonid")}
        return self._seasons.get(spid // 1000000, "")


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
