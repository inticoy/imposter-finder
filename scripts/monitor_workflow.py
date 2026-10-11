"""Final Actions step: setup/runtime failure fallback + execution heartbeat.

Uses only stdlib and is executable even when application dependencies failed to
install. Workflow passes job.status, never parses potentially private raw logs.
"""
import os
import sys
import time
import urllib.request

from monitoring import send


def main() -> int:
    status = os.environ.get("JOB_STATUS", "failure")
    now = int(time.time()*1000)
    event = dict(component="workflow", severity="ok" if status == "success" else "failure",
        code="ok" if status == "success" else "workflow-failed",
        summary="Actions 실행 정상 완료" if status == "success" else "Actions 실행 실패 — 설치·설정·앱·캐시 단계 확인",
        hint="연결된 Actions 실행 로그에서 실패한 단계 확인", log=f"job.status={status}", observed_at=now)
    run = f"workflow-{os.environ.get('GITHUB_RUN_ID',time.time_ns())}-{os.environ.get('GITHUB_RUN_ATTEMPT','1')}"
    if not send(os.environ["MONITOR_SERVICE"], [event], run, heartbeat=True):
        return 1
    url = os.environ.get("HEALTHCHECK_URL")
    if not url:
        print("[monitoring] Healthchecks URL missing")
        return 1
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=b'', method='POST'), timeout=10):
                return 0
        except OSError:
            if attempt < 2:
                time.sleep(1+attempt)
    print("[monitoring] Healthchecks ping failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
