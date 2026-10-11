"""Versioned stdlib monitoring client, vendored into each Actions application.

No network calls outside an explicitly started production run. Errors never stop
the application's useful work. Only a durable relay acknowledgement counts as
reported; the workflow's final step withholds Healthchecks when reporting fails.
"""
from __future__ import annotations

import json
import os
import re
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

_events: dict[str, dict] = {}
_active = False
_words: list[str] = []
_severity = {"ok": 0, "disabled": 0, "degraded": 1, "failure": 2}


def begin(private_words: list[str] | None = None) -> None:
    global _active, _words
    _events.clear()
    _active = os.environ.get("BOT_ENV") == "prod" and bool(os.environ.get("MONITOR_ENDPOINT"))
    _words = private_words or []


def redact(value: str, limit: int = 850) -> str:
    for key, secret in os.environ.items():
        if any(part in key for part in ("TOKEN", "KEY", "SECRET", "PASSWORD", "URL")) and len(secret) >= 6:
            value = value.replace(secret, "***")
    for word in sorted(_words, key=len, reverse=True):
        if len(word) >= 2:
            value = re.sub(re.escape(word), "***", value, flags=re.I)
    value = re.sub(r"https?://\S+", "[URL 제거]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", "[이메일 제거]", value)
    value = re.sub(r"\b\d{17,20}\b", "[ID 제거]", value)
    return value[:limit]


def status(error: BaseException) -> int | None:
    current: BaseException | None = error
    for _ in range(5):
        if current is None:
            break
        code = getattr(current, "code", None)
        response = getattr(current, "response", None)
        code = code if isinstance(code, int) else getattr(response, "status_code", None)
        if isinstance(code, int) and 100 <= code <= 599:
            return code
        match = re.search(r"(?:HTTP\s*|\b)(401|403|404|429|5\d\d)\b", str(current))
        if match:
            return int(match[1])
        current = current.__cause__
    return None


def observe(component: str, severity: str = "ok", code: str = "ok", summary: str = "정상 확인", hint: str = "", log: str = "") -> None:
    if not _active:
        return
    previous = _events.get(component)
    # A later successful account cannot erase a failure in the same collection.
    if previous and _severity[previous["severity"]] > _severity[severity]:
        return
    _events[component] = dict(component=component, severity=severity, code=code,
        summary=redact(summary, 350), hint=hint, log=redact(log), observed_at=int(time.time()*1000))


def error(component: str, exc: BaseException, summary: str, hint: str = "실행 로그와 설정 확인", severity: str = "degraded") -> None:
    http = status(exc)
    code = f"http-{http}" if http else "exception-" + type(exc).__name__.lower()
    frames = traceback.extract_tb(exc.__traceback__)[-5:]
    # Source lines and exception messages may contain user prompts/calendar data.
    # Keep structural traceback and HTTP status, never raw provider payloads.
    log = "\n".join(f"{Path(f.filename).name}:{f.lineno} · {f.name}" for f in frames)
    log += "\n" + type(exc).__name__ + (f": HTTP {http}" if http else ": 처리 실패")
    if http in (401, 403):
        hint = "API 키 유효기간·승인 상태 또는 Discord 권한 확인"
    observe(component, severity, code, summary + (f" · HTTP {http}" if http else ""), hint, log)


def events() -> list[dict]:
    return list(_events.values())


def check_discord(token: str, targets: dict[str, tuple[str, ...]]) -> None:
    """Read-only destination check, including runs without any new reports."""
    if not _active or not token:
        return
    for game, threads in targets.items():
        for thread in threads:
            request = urllib.request.Request("https://discord.com/api/v10/channels/" + thread,
                headers={"Authorization": "Bot " + token, "User-Agent": "service-monitoring/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=10):
                    observe(game + ".discord")
            except OSError as exc:
                error(game + ".discord", exc, "게임 리포트 스레드 접근 실패", "스레드 ID·봇 접근 권한 확인")


def send(service: str, entries: list[dict], run_id: str, heartbeat: bool = False) -> bool:
    endpoint, token = os.environ.get("MONITOR_ENDPOINT"), os.environ.get("MONITOR_TOKEN")
    if not endpoint or not token:
        return False
    link = ""
    if os.environ.get("GITHUB_RUN_ID") and os.environ.get("GITHUB_REPOSITORY"):
        link = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    payload = dict(service=service, events=[entry | {"link": link} for entry in entries], run_id=run_id, heartbeat=heartbeat)
    for attempt in range(3):
        request = urllib.request.Request(endpoint.rstrip('/')+'/v1/events', data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "service-monitoring/1.0"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                if json.load(response).get("accepted") is True:
                    return True
        except (OSError, ValueError):
            if attempt < 2:
                time.sleep(1 + attempt)
    print("[monitoring] report failed; Healthchecks completion will be withheld", flush=True)
    return False


def finish(service: str) -> bool:
    if not _active:
        return False
    run = os.environ.get("GITHUB_RUN_ID", str(time.time_ns()))
    return send(service, events(), f"app-{run}-{os.environ.get('GITHUB_RUN_ATTEMPT','1')}")
