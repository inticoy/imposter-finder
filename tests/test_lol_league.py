from dataclasses import replace
from unittest.mock import Mock

from imposter_finder import lol_collector
from imposter_finder.analysis.lol_league import FLEX_QUEUE, SOLO_DUO_QUEUE, _record_book, friend_game
from imposter_finder.analysis.lol_report import Friend
from imposter_finder.config import Settings
from imposter_finder.storage import LocalStore

FRIENDS = {"a": Friend("A", None), "b": Friend("B", None)}


def match(mid, created=1000, queue=FLEX_QUEUE, remake=False, duration=1800, kills=10):
    return {"metadata": {"matchId": mid}, "info": {
        "queueId": queue, "gameCreation": created, "gameDuration": duration, "participants": [
            {"puuid": "a", "teamId": 100, "championName": "Ahri", "win": True,
             "kills": kills, "deaths": 0, "assists": 3, "gameEndedInEarlySurrender": remake},
            {"puuid": "b", "teamId": 100, "championName": "Lux", "win": True,
             "kills": 2, "deaths": 1, "assists": 2, "teamPosition": "UTILITY", "visionScore": 60},
            {"puuid": "enemy", "teamId": 200, "kills": 1},
        ],
    }}


def test_rollout_combines_new_same_team_ranked_games(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "state.sqlite")
    settings = Settings("dev", "test", 24, tmp_path / "players.json", discord_lol_thread_ids=("thread",))
    riot, discord = Mock(), Mock()
    stored = {str(i): match(str(i), 2000 - i) for i in range(1, 7)}
    stored["remake"] = match("remake", remake=True)
    stored["2"] = match("2", 1998, queue=SOLO_DUO_QUEUE)
    stored["normal"] = match("normal", queue=400)
    stored["opponents"] = match("opponents")
    stored["opponents"]["info"]["participants"][1]["teamId"] = 200
    for i in range(6):
        old = match(f"old{i}", 2000 + i, queue=SOLO_DUO_QUEUE if i % 2 else FLEX_QUEUE)
        store.upsert_lol_match(old)
        store.mark_notification_done("lol", f"old{i}", "sent")

    for key in FRIENDS:
        store.set_lol_puuid(key, key)
    monkeypatch.setattr(lol_collector, "load_lol_accounts",
                        lambda _: [Mock(riot_id=key, name=p.name, discord_user_id=None) for key, p in FRIENDS.items()])
    monkeypatch.setattr(lol_collector, "RiotClient", lambda _: riot)
    monkeypatch.setattr(lol_collector, "DiscordClient", lambda _: discord)
    monkeypatch.setattr(lol_collector, "_highlight", lambda *args: None)
    monkeypatch.setattr(lol_collector, "MAX_AGE_S", float("inf"))
    monkeypatch.setattr(lol_collector, "build_report", Mock(return_value=({}, [])))
    render = Mock(return_value=({}, []))
    monkeypatch.setattr(lol_collector, "build_league_report", render)
    riot.match.side_effect = stored.__getitem__
    riot.match_ids.return_value = ["1", "2", "3", "4", "remake", "normal", "opponents"]
    assert lol_collector.run_lol_cycle(settings, store, True)["league"] == 0
    render.assert_not_called()
    riot.match_ids.return_value = ["5", "6"]
    assert lol_collector.run_lol_cycle(settings, store, True)["league"] == 1
    history, window, _ = render.call_args.args
    assert {g.id for g in window} == {"1", "2", "3", "4", "6"}
    assert [g.created for g in window] == sorted(g.created for g in window)
    assert len(history) == 11
    assert lol_collector.run_lol_cycle(settings, store, True)["league"] == 0
    render.assert_called_once()
    store.close()


def test_pending_batches_keep_checkpoint_and_skip_successful_destination(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "state.sqlite")
    store.mark_notification_done(lol_collector.LEAGUE, lol_collector.LEAGUE_START, "bootstrap")
    for i in range(11):
        store.upsert_lol_match(match(str(i), i))
        store.mark_notification_done("lol", str(i), "sent")
    settings = Settings("dev", "test", 24, tmp_path / "players.json", discord_lol_thread_ids=("one", "two"))
    discord = Mock()
    discord.send_with_files.side_effect = [None, TimeoutError("retry second destination")]
    render = Mock(return_value=({}, []))
    monkeypatch.setattr(lol_collector, "build_league_report", render)
    assert lol_collector._publish_league(settings, store, discord, FRIENDS) == 0
    assert store.last_notification(lol_collector.LEAGUE) == lol_collector.LEAGUE_START
    discord.send_with_files.side_effect = None
    assert lol_collector._publish_league(settings, store, discord, FRIENDS) == 2
    assert [c.args[0] for c in discord.send_with_files.call_args_list] == ["one", "two", "two", "one", "two"]
    assert [len(c.args[0]) for c in render.call_args_list] == [5, 5, 10]
    assert store.last_notification(lol_collector.LEAGUE) == "9"
    assert lol_collector._publish_league(settings, store, discord, FRIENDS) == 0
    store.close()


def test_record_book_detects_ties_and_new_records_without_stale_cards():
    prior = friend_game(match("prior", duration=1500, kills=14), FRIENDS)
    quiet = friend_game(match("quiet", duration=1450, kills=13), FRIENDS)
    # Increase past-best vision/assists too, so an unchanged week has no new or tied record.
    prior = replace(prior, players=(replace(prior.players[0], assists=9), replace(prior.players[1], vision=100)))
    assert not _record_book([prior, quiet], [quiet])

    tied = friend_game(match("tie", duration=1500, kills=14), FRIENDS)
    assert {r.label: badge for r, badge in _record_book([prior, tied], [tied]) if badge} == {
        "최장 경기": "타이기록", "최대 킬 격차 승리": "타이기록", "한 경기 최다 킬": "타이기록",
    }
    new = friend_game(match("new", duration=1600, kills=20), FRIENDS)
    assert {r.label: badge for r, badge in _record_book([prior, new], [new]) if badge} == {
        "최장 경기": "신기록", "최대 킬 격차 승리": "신기록", "한 경기 최다 킬": "신기록",
    }
