"""상태 DB를 Cloudflare D1에 백업하고, Actions 캐시가 사라지면 되살린다.

경기 기록(배그·롤·TFT·FC)과 알림 처리 내역만 있으면 이미지·디스코드 메시지는 다시 만들 수 있다.
넥슨·라이엇·PUBG는 최근 기록만 다시 주므로 (FC 30일) 통산 기록은 여기서 지킨다.

push: 로컬에만 있는 행을 D1에 넣는다. 지우지 않으므로 빈 DB로 돌아도 백업이 망가지지 않는다.
      updated_at이 있는 작은 표(계정·랭크)와 api_usage는 매번 덮어쓴다.
pull: D1의 모든 행을 로컬 DB에 넣는다 (캐시가 없을 때 실행 전에).

Usage: python -m imposter_finder.backup push|pull
환경 변수: D1_API_TOKEN, D1_ACCOUNT_ID, D1_DATABASE_ID (없으면 아무것도 하지 않는다)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import Any

from imposter_finder.config import ROOT_DIR
from imposter_finder.storage import LocalStore

API = "https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{database}/query"
MAX_PARAMS = 100  # D1: 쿼리 하나에 묶는 값 최대 개수
MAX_BYTES = 700_000  # 요청 하나에 실을 값 크기 (행 하나는 롤 경기 약 140KB)
PAGE = 20  # pull 한 번에 받을 행


class D1:
    def __init__(self, token: str, account: str, database: str) -> None:
        self.url = API.format(account=account, database=database)
        self.token = token

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        body = json.dumps({"sql": sql, "params": params or []}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        if not data.get("success"):
            raise RuntimeError(f"D1 query failed: {data.get('errors')}")
        return data["result"][0].get("results", [])


def _tables(db: sqlite3.Connection) -> dict[str, tuple[str, list[str], list[str]]]:
    """표 이름 → (CREATE 문, 열, 기본 키 열)."""
    out = {}
    for name, sql in db.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
        info = db.execute(f"PRAGMA table_info({name})").fetchall()
        cols = [row[1] for row in info]
        keys = [row[1] for row in sorted(info, key=lambda r: r[5]) if row[5]] or cols
        out[name] = (sql, cols, keys)
    return out


def _mutable(cols: list[str], table: str) -> bool:
    return "updated_at" in cols or table == "api_usage"


def _insert(remote: D1, table: str, cols: list[str], rows: list[tuple]) -> None:
    """여러 행을 값 개수·크기 한도 안에서 묶어 넣는다."""
    batch: list[tuple] = []
    size = 0
    for row in rows + [None]:
        row_size = sum(len(str(v)) for v in row) if row else 0
        full = row is None or (len(batch) + 1) * len(cols) > MAX_PARAMS or size + row_size > MAX_BYTES
        if batch and full:
            values = ", ".join("(" + ", ".join("?" * len(cols)) + ")" for _ in batch)
            remote.query(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES {values}",
                         [v for r in batch for v in r])
            batch, size = [], 0
        if row is not None:
            batch.append(row)
            size += row_size


def push(db_path: Path, remote: D1) -> dict[str, int]:
    db = sqlite3.connect(db_path)
    sent = {}
    for table, (sql, cols, keys) in _tables(db).items():
        remote.query(sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
        rows = db.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        if not _mutable(cols, table):
            have = {tuple(r[k] for k in keys) for r in remote.query(f"SELECT {', '.join(keys)} FROM {table}")}
            index = [cols.index(k) for k in keys]
            rows = [r for r in rows if tuple(r[i] for i in index) not in have]
        _insert(remote, table, cols, rows)
        sent[table] = len(rows)
    db.close()
    return sent


def pull(db_path: Path, remote: D1) -> dict[str, int]:
    # 임시 파일에 다 받은 뒤에만 바꿔 넣는다: 중간에 끊긴 DB가 캐시되면 다음 실행이 그걸 진짜로 믿는다
    temp = db_path.with_name(db_path.name + ".restoring")
    for leftover in (temp, Path(f"{temp}-wal"), Path(f"{temp}-shm")):
        leftover.unlink(missing_ok=True)
    LocalStore(temp).close()  # 표를 만든다
    db = sqlite3.connect(temp)
    got = {}
    for table, (_, cols, keys) in _tables(db).items():
        try:
            total = remote.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        except RuntimeError:  # 아직 백업한 적 없는 표
            continue
        order = ", ".join(keys)
        for offset in range(0, total, PAGE):
            rows = remote.query(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order} LIMIT ? OFFSET ?",
                                [PAGE, offset])
            db.executemany(f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [tuple(r[c] for c in cols) for r in rows])
        db.commit()
        got[table] = total
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    os.replace(temp, db_path)
    return got


def main() -> None:
    token, account, database = (os.environ.get(k) for k in ("D1_API_TOKEN", "D1_ACCOUNT_ID", "D1_DATABASE_ID"))
    if not (token and account and database):
        print("[backup] D1 설정 없음 — 건너뜀")
        return
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    path = ROOT_DIR / os.environ.get("LOCAL_DATABASE_PATH", "data/imposter_finder.db")
    remote = D1(token, account, database)
    if command == "push":
        print("[backup] pushed", push(path, remote))
    elif command == "pull":
        print("[backup] pulled", pull(path, remote))
    else:
        raise SystemExit("usage: python -m imposter_finder.backup push|pull")


if __name__ == "__main__":
    main()
