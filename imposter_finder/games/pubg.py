from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class PubgApiError(RuntimeError):
    pass


class PubgClient:
    def __init__(self, api_key: str, timeout: int = 20) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def _request_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body.decode("utf-8")) if body else None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise PubgApiError(f"HTTP {e.code}: {body[:500]}") from e

    def _headers(self, auth: bool = True) -> dict[str, str]:
        headers = {"Accept": "application/vnd.api+json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def lookup_players(self, platform: str, names: list[str]) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(names), 10):
            batch = names[offset : offset + 10]
            encoded = urllib.parse.quote(",".join(batch), safe=",")
            url = f"https://api.pubg.com/shards/{platform}/players?filter[playerNames]={encoded}"
            payload = self._request_json(url, self._headers(auth=True))
            for item in payload.get("data", []):
                name = item.get("attributes", {}).get("name")
                if name:
                    found[name.lower()] = item
        return found

    def get_match(self, platform: str, match_id: str) -> dict[str, Any]:
        url = f"https://api.pubg.com/shards/{platform}/matches/{match_id}"
        return self._request_json(url, self._headers(auth=False))

    def get_telemetry(self, url: str) -> list[dict[str, Any]]:
        payload = self._request_json(url, headers={})
        return payload if isinstance(payload, list) else []
