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

    def get_channel(self, channel_id: str) -> dict:
        payload = self._request_json("GET", f"https://discord.com/api/v10/channels/{channel_id}")
        if not isinstance(payload, dict):
            raise DiscordError("Discord channel lookup returned an empty response")
        return payload

    def send_report(self, channel_id: str, report: dict | str, thread_name: str) -> None:
        payload = self._report_payload(report)
        channel = self.get_channel(channel_id)
        channel_type = channel.get("type")
        if channel_type in {15, 16}:
            self._request_json(
                "POST",
                f"https://discord.com/api/v10/channels/{channel_id}/threads",
                {
                    "name": thread_name[:100],
                    "message": payload,
                },
            )
            return

        self._request_json(
            "POST",
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            payload,
        )

    def send_message(self, channel_or_thread_id: str, report: dict | str) -> None:
        self._request_json(
            "POST",
            f"https://discord.com/api/v10/channels/{channel_or_thread_id}/messages",
            self._report_payload(report),
        )

    def _report_payload(self, report: dict | str) -> dict:
        if isinstance(report, str):
            return _silent({"content": report[:2000]})
        payload = report.get("discord_payload")
        if isinstance(payload, dict):
            return _silent(payload)
        return _silent({"content": str(report.get("message", ""))[:2000]})

    def send_with_file(self, channel_id: str, payload: dict, filename: str, data: bytes,
                       content_type: str = "image/png") -> dict | None:
        """Post a message payload with one attached file (multipart/form-data)."""
        payload = _silent(payload)
        boundary = f"----imposterfinder{uuid.uuid4().hex}"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n".encode() + json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n".encode() + data,
        ]
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
