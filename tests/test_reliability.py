import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from imposter_finder import collector, fc_collector, lol_collector, tft_collector
from imposter_finder.config import Settings
from imposter_finder.discord import DiscordClient
from imposter_finder.storage import LocalStore


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", Mock(side_effect=AssertionError("unexpected network call")))


@pytest.fixture(params=["pubg", "fc", "lol", "tft"])
def game(request, monkeypatch, tmp_path):
    name = request.param
    module = {"pubg": collector, "fc": fc_collector, "lol": lol_collector, "tft": tft_collector}[name]
    settings = Settings("dev", "test", 24, tmp_path / "players.json",
                        discord_pubg_thread_ids=("one", "two"), discord_fc_thread_ids=("one", "two"),
                        discord_lol_thread_ids=("one", "two"), discord_tft_thread_ids=("one", "two"))
    players = [SimpleNamespace(name=p, nickname=p, riot_id=p, platform="steam", discord_user_id=None)
               for p in ("a", "b")]
    client = Mock()
    now = int(time.time() * 1000)
    matches = {
        "pubg": lambda mid: {"data": {"id": mid}, "included": [
            {"type": "participant", "attributes": {"stats": {"name": p}}} for p in ("a", "b")]},
        "fc": lambda mid: {"matchId": mid, "matchType": 40, "matchDate": "2026-10-25T00:00:00",
                           "matchInfo": [{"ouid": p} for p in ("a", "b")]},
        "lol": lambda mid: {"metadata": {"matchId": mid}, "info": {"gameCreation": now, "queueId": 1700}},
        "tft": lambda mid: {"metadata": {"match_id": mid}, "info": {
            "game_datetime": now, "queue_id": 0, "participants": [{"puuid": p} for p in ("a", "b")]}}
    }
    if name == "pubg":
        loader, constructor, fetch = "load_pubg_players", "PubgClient", "get_match"
        client.get_recent_match_ids.return_value = ["old", "later"]
        client.get_match.side_effect = lambda platform, mid: matches[name](mid)
        cycle = module.run_collection_cycle
    elif name == "fc":
        loader, constructor, fetch = "load_fc_players", "FcOnlineClient", "match_detail"
        client.ouid.side_effect = lambda p: p
        client.match_ids.return_value = ["old", "later"]
        for attr in ("_meta", "_colors", "_prices"):
            monkeypatch.setattr(module, attr, Mock())
        monkeypatch.setattr(module, "_too_old", lambda m: False)
        monkeypatch.setattr(module, "is_forfeit", lambda m: False)
        monkeypatch.setattr(module, "_publish_league", Mock(return_value=0))
        cycle = module.run_fc_cycle
    else:
        loader, constructor = "load_lol_accounts", "RiotClient"
        fetch = "match" if name == "lol" else "tft_match"
        client.puuid.side_effect = lambda p: p
        getattr(client, "match_ids" if name == "lol" else "tft_match_ids").return_value = ["old", "later"]
        client.tft_rank.return_value = None
        cycle = module.run_lol_cycle if name == "lol" else module.run_tft_cycle
    if name != "pubg":
        getattr(client, fetch).side_effect = matches[name]
    monkeypatch.setattr(module, loader, lambda path: players)
    monkeypatch.setattr(module, constructor, lambda *args: client)
    monkeypatch.setattr(module, "build_report", Mock(return_value=({"content": "report"}, [("x.png", b"x")])))
    if name != "tft":
        monkeypatch.setattr(module, "_highlight", lambda *args: None)
    discord = Mock()
    monkeypatch.setattr(module, "DiscordClient", lambda token: discord)
    return SimpleNamespace(name=name, module=module, settings=settings, client=client, fetch=fetch,
                           matches=matches[name], cycle=cycle, discord=discord, path=tmp_path / "db.sqlite")


def test_partial_bootstrap_restarts_quietly(game):
    store = LocalStore(game.path)
    fetch = getattr(game.client, game.fetch)
    fetch.side_effect = [game.matches("old"), RuntimeError("later lookup failed")]
    with pytest.raises(RuntimeError):
        game.cycle(game.settings, store, False)
    assert not store.bootstrap_complete(game.name)
    store.close()
    store = LocalStore(game.path)
    assert not store.bootstrap_complete(game.name)
    fetch.side_effect = (lambda platform, mid: game.matches(mid)) if game.name == "pubg" else game.matches
    result = game.cycle(game.settings, store, store.bootstrap_complete(game.name))
    assert result["published"] == 0
    assert store.bootstrap_complete(game.name)
    game.discord.send_with_files.assert_not_called()
    store.close()
    store = LocalStore(game.path)
    assert store.bootstrap_complete(game.name)
    store.close()


def test_timeout_retry_keeps_report_id_and_send_before_mark(game):
    store = LocalStore(game.path)
    store.mark_bootstrap_complete(game.name)
    platform = {"pubg": "steam", "fc": "fconline"}.get(game.name, game.name)
    accepted = []

    def send(channel, payload, files, *, report_id):
        assert not store.notification_done(platform, "old")
        accepted.append((channel, report_id))
        if len(accepted) == 2:
            raise TimeoutError("accepted but response lost")

    game.discord.send_with_files.side_effect = send
    with pytest.raises(TimeoutError):
        game.cycle(game.settings, store, True)
    assert not store.notification_done(platform, "old")
    game.discord.send_with_files.side_effect = lambda channel, payload, files, **kw: accepted.append((channel, kw["report_id"]))
    game.cycle(game.settings, store, True)
    assert accepted[:2] == accepted[2:4]
    assert store.notification_done(platform, "old")
    store.close()


@pytest.mark.parametrize("game,table,platform", [("pubg", "matches", "steam"), ("fc", "fc_matches", "fconline"),
                                                 ("lol", "lol_matches", "lol"), ("tft", "tft_matches", "tft")])
@pytest.mark.parametrize("notified", [False, True])
def test_legacy_migration_requires_matches_and_notifications(tmp_path, game, table, platform, notified):
    path = tmp_path / "legacy.sqlite"
    store = LocalStore(path)
    store.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE game_bootstraps")
        columns = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
        values = {c: "test" for c in columns}
        values.update(match_id="old", platform=platform)
        db.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})", [values[c] for c in columns])
        if notified:
            db.execute("INSERT INTO match_notifications VALUES (?, 'old', 'bootstrap', 'now')", (platform,))
    store = LocalStore(path)
    assert store.bootstrap_complete(game) == notified
    assert not store.bootstrap_complete("fc" if game != "fc" else "lol")
    store.close()


@pytest.mark.parametrize("multipart", [False, True])
def test_discord_nonce_survives_accepted_timeout(monkeypatch, multipart):
    requests = []
    messages = {}

    def urlopen(req, **kwargs):
        if multipart:
            raw = req.data.split(b"\r\n\r\n", 1)[1].split(b"\r\n", 1)[0]
        else:
            raw = req.data
        payload = json.loads(raw)
        requests.append(payload)
        assert payload["enforce_nonce"] is True
        assert len(payload["nonce"]) <= 25
        messages.setdefault(payload["nonce"], {"id": "accepted"})
        if len(requests) == 1:
            raise TimeoutError("response lost")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(messages[payload["nonce"]]).encode()
        return response

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = DiscordClient("fake")
    payload = {"content": "report"}

    def send(channel="thread", report_id="lol:match"):
        if multipart:
            return client.send_with_file(channel, payload, "x.png", b"x", report_id=report_id)
        return client.send_message(channel, payload, report_id=report_id)

    with pytest.raises(TimeoutError):
        send()
    send()
    assert len(messages) == 1
    assert requests[0]["nonce"] == requests[1]["nonce"]
    send(report_id="tft:match")
    send(channel="other")
    assert len(messages) == 3
    assert payload == {"content": "report"}


def test_account_failure_does_not_complete_bootstrap(game):
    if game.name == "pubg":
        game.client.get_recent_match_ids.side_effect = RuntimeError("account unavailable")
        error = RuntimeError
    else:
        error = game.module.NexonApiError if game.name == "fc" else game.module.RiotApiError
        ids = "tft_match_ids" if game.name == "tft" else "match_ids"
        getattr(game.client, ids).side_effect = error("account unavailable")
    store = LocalStore(game.path)
    with pytest.raises(error):
        game.cycle(game.settings, store, False)
    assert not store.bootstrap_complete(game.name)
    store.close()


def test_empty_successful_bootstrap_is_persistent(game):
    ids = {"pubg": "get_recent_match_ids", "fc": "match_ids", "lol": "match_ids", "tft": "tft_match_ids"}
    getattr(game.client, ids[game.name]).return_value = []
    store = LocalStore(game.path)
    game.cycle(game.settings, store, False)
    store.close()
    store = LocalStore(game.path)
    assert store.bootstrap_complete(game.name)
    store.close()


def test_once_uses_completion_marker(monkeypatch, tmp_path):
    from imposter_finder import once
    import sys

    path = tmp_path / "db.sqlite"
    settings = Settings("dev", "test", 24, tmp_path / "missing.json", local_database_path=path,
                        discord_pubg_thread_ids=("thread",))
    store = LocalStore(path)
    store.upsert_match("steam", {"data": {"id": "partial"}})
    store.close()
    cycle = Mock(return_value={"matches": 1, "published": 0})
    monkeypatch.setattr(once, "run_collection_cycle", cycle)
    monkeypatch.setattr(once, "monitoring", Mock())
    monkeypatch.setattr(once, "ping_healthcheck", Mock())
    monkeypatch.setenv("MONITOR_ENABLED_GAMES", "pubg")
    monkeypatch.delenv("MONITOR_ENDPOINT", raising=False)
    stdout, stderr = sys.stdout, sys.stderr
    try:
        assert once.run_once(settings) == 0
        assert cycle.call_args.args[2] is False
    finally:
        sys.stdout, sys.stderr = stdout, stderr


@pytest.mark.parametrize("game", ["fc"], indirect=True)
def test_forfeit_retry_keeps_report_id(game, monkeypatch):
    monkeypatch.setattr(game.module, "is_forfeit", lambda match: True)
    monkeypatch.setattr(game.module, "build_forfeit_report", Mock(return_value={"content": "forfeit"}))
    store = LocalStore(game.path)
    game.discord.send_message.side_effect = TimeoutError("response lost")
    with pytest.raises(TimeoutError):
        game.cycle(game.settings, store, True)
    first = game.discord.send_message.call_args
    assert not store.notification_done("fconline", "old")
    game.discord.send_message.side_effect = None
    game.cycle(game.settings, store, True)
    assert game.discord.send_message.call_args_list[1] == first
    assert first.kwargs["report_id"] == "fconline:old"
    assert store.notification_done("fconline", "old")
    store.close()


@pytest.mark.parametrize("complete", [False, True])
def test_service_loop_uses_completion_marker(game, monkeypatch, complete):
    import asyncio
    from imposter_finder import bot

    store = LocalStore(game.path)
    if complete:
        store.mark_bootstrap_complete(game.name)
    names = {"pubg": ("_collection_loop", "run_collection_cycle"), "fc": ("_fc_loop", "run_fc_cycle"),
             "lol": ("_lol_loop", "run_lol_cycle"), "tft": ("_tft_loop", "run_tft_cycle")}
    loop, cycle_name = names[game.name]
    cycle = Mock(return_value={"matches": 0, "friend_matches": 0, "group_matches": 0, "published": 0})
    monkeypatch.setattr(bot, cycle_name, cycle)
    monkeypatch.setattr(bot, "ping_healthcheck", Mock())

    async def stop_after_cycle(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(bot.asyncio, "sleep", stop_after_cycle)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(getattr(bot, loop)(game.settings, store))
    assert cycle.call_args.args[2] is complete
    store.close()
