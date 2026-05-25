from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "seen_matches": []}
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("version", 1)
        data.setdefault("seen_matches", [])
        return data

    def seen_match(self, game: str, platform: str, match_id: str) -> bool:
        return any(
            item.get("game") == game and item.get("platform") == platform and item.get("match_id") == match_id
            for item in self.data["seen_matches"]
        )

    def mark_match_seen(
        self,
        game: str,
        platform: str,
        match_id: str,
        started_at: str | None,
        report: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = None
        for item in self.data["seen_matches"]:
            if item.get("game") == game and item.get("platform") == platform and item.get("match_id") == match_id:
                existing = item
                break

        payload = {
            "game": game,
            "platform": platform,
            "match_id": match_id,
            "started_at": started_at,
            "processed_at": now,
        }
        if existing is None:
            self.data["seen_matches"].append(payload)
        else:
            existing.update(payload)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.write("\n")
