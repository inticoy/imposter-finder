from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid


class DiscordError(RuntimeError):
    pass


SUPPRESS_NOTIFICATIONS = 1 << 12


def _silent(payload: dict) -> dict:
    """봇 리포트는 모두 @silent: 태그는 남지만 푸시 알림은 가지 않는다."""
    return payload | {"flags": payload.get("flags", 0) | SUPPRESS_NOTIFICATIONS}


class DiscordClient:
    def __init__(self, bot_token: str, timeout: int = 20) -> None:
        self.bot_token = bot_token
        self.timeout = timeout

    def _request_json(self, method: str, url: str, payload: dict | None = None) -> dict | None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bot {self.bot_token}",
                "Content-Type": "application/json",
                "User-Agent": "imposter-finder/0.1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise DiscordError(f"Discord HTTP {exc.code}: {raw[:500]}") from exc

    def send_message(self, channel_id: str, payload: dict) -> None:
        """파일 없는 메시지 (FC 몰수 경기 한 줄 등)."""
        self._request_json("POST", f"https://discord.com/api/v10/channels/{channel_id}/messages", _silent(payload))

    def send_with_file(self, channel_id: str, payload: dict, filename: str, data: bytes,
                       content_type: str = "image/png") -> dict | None:
        """Post a message payload with one attached file."""
        return self.send_with_files(channel_id, payload, [(filename, data, content_type)])

    def send_with_files(self, channel_id: str, payload: dict, files: list[tuple]) -> dict | None:
        """Post a message payload with attached files (multipart/form-data).

        files: [(filename, bytes) | (filename, bytes, content_type)]. payload의 attachment://filename으로 참조한다.
        """
        payload = _silent(payload) | {"attachments": [{"id": n, "filename": f[0]} for n, f in enumerate(files)]}
        boundary = f"----imposterfinder{uuid.uuid4().hex}"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n".encode() + json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ]
        for n, (filename, data, *rest) in enumerate(files):
            content_type = rest[0] if rest else "image/png"
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[{n}]\"; filename=\"{filename}\"\r\n"
                f"Content-Type: {content_type}\r\n\r\n".encode() + data)
        body = b"\r\n".join(parts) + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bot {self.bot_token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "imposter-finder/0.1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise DiscordError(f"Discord HTTP {exc.code}: {raw[:500]}") from exc
