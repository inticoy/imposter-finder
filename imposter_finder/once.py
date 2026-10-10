"""GitHub Actions용: 배그·FC·롤을 한 번씩 수집·리포트하고 끝낸다 (cron-job.org가 15분마다 workflow 실행).

공개 레포라 Actions 로그도 공개된다. players.json의 이름·닉네임·디스코드 ID는 로그에서 가리고,
건수·처리 결과·에러 내용만 남긴다.
"""
from __future__ import annotations

import json
import sys
import traceback
import urllib.parse
from pathlib import Path
from typing import TextIO

from imposter_finder.collector import run_collection_cycle
from imposter_finder.config import Settings
from imposter_finder.fc_collector import run_fc_cycle
from imposter_finder.healthcheck import ping as ping_healthcheck
from imposter_finder.lol_collector import run_lol_cycle
from imposter_finder.storage import LocalStore

MASK = "***"
NOT_PRIVATE = {"steam", "kakao", "psn", "xbox", "stadia", "console"}  # 플랫폼 이름은 가리지 않는다


def private_words(players_path: Path) -> list[str]:
    """players.json의 모든 글자 값 (이름·닉네임·Riot ID·디스코드 ID). Riot ID는 태그 앞 이름도."""
    try:
        data = json.loads(players_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    words: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str) and len(value.strip()) >= 2 and value.casefold() not in NOT_PRIVATE:
            for word in {value, value.split("#")[0]}:
                words.update({word, urllib.parse.quote(word), urllib.parse.quote_plus(word)})

    walk(data)
    return sorted((w for w in words if len(w) >= 2), key=len, reverse=True)  # 긴 것부터 가려야 일부만 남지 않는다


class Redacted:
    """print·traceback이 쓰는 stdout/stderr를 감싸 개인정보를 가린다 (대소문자 무시)."""

    def __init__(self, stream: TextIO, words: list[str]) -> None:
        self._stream, self._words = stream, words

    def write(self, text: str) -> int:
        for word in self._words:
            if word.casefold() in text.casefold():
                text = _replace_ci(text, word)
        return self._stream.write(text)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def _replace_ci(text: str, word: str) -> str:
    out, low, key, i = [], text.casefold(), word.casefold(), 0
    while (j := low.find(key, i)) >= 0:
        out += [text[i:j], MASK]
        i = j + len(key)
    return "".join(out) + text[i:]


def run_once(settings: Settings) -> int:
    words = private_words(settings.players_path)
    sys.stdout, sys.stderr = Redacted(sys.stdout, words), Redacted(sys.stderr, words)
    store = LocalStore(settings.local_database_path)
    jobs = [("pubg", run_collection_cycle, store.has_matches, True),
            ("fc", run_fc_cycle, store.has_fc_matches, bool(settings.nexon_api_key and settings.discord_fc_thread_ids)),
            ("lol", run_lol_cycle, store.has_lol_matches, bool(settings.riot_api_key and settings.discord_lol_thread_ids))]
    failed = False
    for name, cycle, has_data, enabled in jobs:
        if not enabled:
            print(f"[{name}] skipped: not configured")
            continue
        # 저장된 기록이 없으면(캐시가 사라졌을 때도) 이번 경기들은 올리지 않고 기록만 한다: 중복 리포트 방지
        bootstrap = not has_data()
        try:
            result = cycle(settings, store, not bootstrap)
            print(f"[{name}] " + " ".join(f"{k}={v}" for k, v in result.items()) + f" bootstrap={bootstrap}")
        except Exception:
            failed = True
            print(f"[{name}] ERROR", flush=True)
            traceback.print_exc()
    store.close()
    ping_healthcheck(settings.healthcheck_imposter_finder_url, failed)
    return 1 if failed else 0
