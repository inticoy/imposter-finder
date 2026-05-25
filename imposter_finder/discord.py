from __future__ import annotations

import json
import urllib.error
import urllib.request


class DiscordError(RuntimeError):
    pass


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
            return {"content": report[:2000]}
        payload = report.get("discord_payload")
        if isinstance(payload, dict):
            return payload
        return {"content": str(report.get("message", ""))[:2000]}
