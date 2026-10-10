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
                CREATE TABLE IF NOT EXISTS fc_users (
                    nickname TEXT PRIMARY KEY,
                    ouid TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fc_matches (
                    match_id TEXT PRIMARY KEY,
                    match_type INTEGER NOT NULL,
                    match_date TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    collected_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fc_match_users (
                    match_id TEXT NOT NULL,
                    ouid TEXT NOT NULL,
                    PRIMARY KEY (match_id, ouid)
                );
                CREATE TABLE IF NOT EXISTS lol_accounts (
                    riot_id TEXT PRIMARY KEY COLLATE NOCASE,
                    puuid TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lol_matches (
                    match_id TEXT PRIMARY KEY,
                    queue_id INTEGER NOT NULL,
                    game_creation INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    collected_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_usage (
                    api TEXT NOT NULL,
                    day TEXT NOT NULL,
                    calls INTEGER NOT NULL,
                    PRIMARY KEY (api, day)
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

    def has_matches(self) -> bool:
        """Whether this local service has completed an initial history import."""
        with self._lock:
            return self._connection.execute("SELECT 1 FROM matches LIMIT 1").fetchone() is not None

    def mark_notification_done(self, platform: str, match_id: str, outcome: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR IGNORE INTO match_notifications(platform, match_id, outcome, processed_at) VALUES (?, ?, ?, ?)",
                (platform, match_id, outcome, _now()),
            )
            self._connection.commit()

    # ── FC 온라인 ──────────────────────────────────────
    def fc_ouid(self, nickname: str) -> str | None:
        with self._lock:
            row = self._connection.execute("SELECT ouid FROM fc_users WHERE nickname = ?", (nickname,)).fetchone()
        return row["ouid"] if row else None

    def set_fc_ouid(self, nickname: str, ouid: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO fc_users (nickname, ouid, updated_at) VALUES (?, ?, ?)", (nickname, ouid, _now()))
            self._connection.commit()

    def has_fc_matches(self) -> bool:
        with self._lock:
            return self._connection.execute("SELECT 1 FROM fc_matches LIMIT 1").fetchone() is not None

    def fc_match(self, match_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM fc_matches WHERE match_id = ?", (match_id,)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def upsert_fc_match(self, match: dict[str, Any]) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO fc_matches (match_id, match_type, match_date, payload_json, collected_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (match["matchId"], match["matchType"], match["matchDate"],
                 json.dumps(match, ensure_ascii=False, separators=(",", ":")), _now()),
            )
            self._connection.executemany(
                "INSERT OR IGNORE INTO fc_match_users (match_id, ouid) VALUES (?, ?)",
                [(match["matchId"], info["ouid"]) for info in match.get("matchInfo", [])],
            )
            self._connection.commit()

    def fc_head_to_head(self, ouid_a: str, ouid_b: str, limit: int = 10) -> list[dict[str, Any]]:
        """두 사람이 함께 뛴 경기, 최신순."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT m.payload_json FROM fc_matches m
                JOIN fc_match_users a ON a.match_id = m.match_id AND a.ouid = ?
                JOIN fc_match_users b ON b.match_id = m.match_id AND b.ouid = ?
                ORDER BY m.match_date DESC LIMIT ?
                """,
                (ouid_a, ouid_b, limit),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    # ── 리그 오브 레전드 ──────────────────────────────
    def lol_puuid(self, riot_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute("SELECT puuid FROM lol_accounts WHERE riot_id = ?", (riot_id,)).fetchone()
        return row["puuid"] if row else None

    def set_lol_puuid(self, riot_id: str, puuid: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO lol_accounts (riot_id, puuid, updated_at) VALUES (?, ?, ?)", (riot_id, puuid, _now()))
            self._connection.commit()

    def has_lol_matches(self) -> bool:
        with self._lock:
            return self._connection.execute("SELECT 1 FROM lol_matches LIMIT 1").fetchone() is not None

    def lol_match(self, match_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT payload_json FROM lol_matches WHERE match_id = ?", (match_id,)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def upsert_lol_match(self, match: dict[str, Any]) -> None:
        info = match["info"]
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO lol_matches (match_id, queue_id, game_creation, payload_json, collected_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (match["metadata"]["matchId"], info["queueId"], info["gameCreation"],
                 json.dumps(match, ensure_ascii=False, separators=(",", ":")), _now()))
            self._connection.commit()

    # ── API 사용량 ────────────────────────────────────
    def add_api_call(self, api: str, day: str) -> int:
        with self._lock:
            self._connection.execute(
                "INSERT INTO api_usage (api, day, calls) VALUES (?, ?, 1)"
                " ON CONFLICT (api, day) DO UPDATE SET calls = calls + 1", (api, day))
            self._connection.commit()
            return self._connection.execute(
                "SELECT calls FROM api_usage WHERE api = ? AND day = ?", (api, day)).fetchone()["calls"]

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
