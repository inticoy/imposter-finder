from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LocalStore:
    """SQLite-backed local history for the always-on service."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS matches (
                    platform TEXT NOT NULL,
                    match_id TEXT NOT NULL,
                    created_at TEXT,
                    payload_json TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    PRIMARY KEY (platform, match_id)
                );
                CREATE TABLE IF NOT EXISTS player_matches (
                    platform TEXT NOT NULL,
                    nickname TEXT NOT NULL COLLATE NOCASE,
                    match_id TEXT NOT NULL,
                    PRIMARY KEY (platform, nickname, match_id),
                    FOREIGN KEY (platform, match_id) REFERENCES matches(platform, match_id)
                );
                CREATE INDEX IF NOT EXISTS player_matches_recent
                    ON player_matches(platform, nickname, match_id);
                CREATE TABLE IF NOT EXISTS match_notifications (
                    platform TEXT NOT NULL,
                    match_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    PRIMARY KEY (platform, match_id)
                );
                """
            )
            self._connection.commit()

    def upsert_match(self, platform: str, match: dict[str, Any]) -> bool:
        match_id = str(match.get("data", {}).get("id") or "")
        if not match_id:
            raise ValueError("PUBG match payload has no match id")
        created_at = match.get("data", {}).get("attributes", {}).get("createdAt")
        with self._lock:
            exists = self._connection.execute(
                "SELECT 1 FROM matches WHERE platform = ? AND match_id = ?", (platform, match_id)
            ).fetchone()
            self._connection.execute(
                """INSERT INTO matches(platform, match_id, created_at, payload_json, collected_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(platform, match_id) DO UPDATE SET
                     created_at = excluded.created_at,
                     payload_json = excluded.payload_json,
                     collected_at = excluded.collected_at""",
                (platform, match_id, created_at, json.dumps(match, ensure_ascii=False), _now()),
            )
            self._connection.commit()
        return exists is None

    def link_player_match(self, platform: str, nickname: str, match_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR IGNORE INTO player_matches(platform, nickname, match_id) VALUES (?, ?, ?)",
                (platform, nickname, match_id),
            )
            self._connection.commit()

    def recent_player_matches(self, platform: str, nickname: str, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT m.payload_json FROM player_matches pm
                   JOIN matches m ON m.platform = pm.platform AND m.match_id = pm.match_id
                   WHERE pm.platform = ? AND pm.nickname = ?
                   ORDER BY m.created_at DESC, m.match_id DESC LIMIT ?""",
                (platform, nickname, limit),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def notification_done(self, platform: str, match_id: str) -> bool:
        with self._lock:
            return self._connection.execute(
                "SELECT 1 FROM match_notifications WHERE platform = ? AND match_id = ?", (platform, match_id)
            ).fetchone() is not None

    def mark_notification_done(self, platform: str, match_id: str, outcome: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR IGNORE INTO match_notifications(platform, match_id, outcome, processed_at) VALUES (?, ?, ?, ?)",
                (platform, match_id, outcome, _now()),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
