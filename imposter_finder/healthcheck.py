from __future__ import annotations

import urllib.request


def ping(url: str | None, failed: bool = False) -> None:
    """Best-effort Healthchecks.io signal; monitoring must never stop collection."""
    if not url:
        return
    target = f"{url.rstrip('/')}/fail" if failed else url
    try:
        with urllib.request.urlopen(target, timeout=10):
            pass
    except OSError as exc:
        print(f"[healthcheck] ERROR: {exc}")
