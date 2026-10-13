import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from imposter_finder.analysis.fc_league import ELO_START, LEAGUE_EVERY, elo, standings
from imposter_finder.analysis.fc_report import Friend
from imposter_finder.fc_collector import LEAGUE, PLATFORM, _publish_league
from imposter_finder.storage import LocalStore


def _side(ouid: str, goals: int) -> dict:
    return {"ouid": ouid, "nickname": ouid, "player": [{"spId": 1}],
            "matchDetail": {"matchResult": "승", "matchEndType": 0, "possession": 50, "dribble": 0},
            "shoot": {"goalTotal": goals, "shootTotal": 1, "shootOutPenalty": 0}, "shootDetail": [],
            "pass": {"passTry": 1, "passSuccess": 1, "throughPassTry": 0}, "defence": {"tackleTry": 0, "tackleSuccess": 0}}


def _match(n: int, a: str, ga: int, b: str, gb: int) -> dict:
    return {"matchId": f"m{n:03d}", "matchType": 40, "matchDate": f"2026-10-01T00:{n // 60:02d}:{n % 60:02d}",
            "matchInfo": [_side(a, ga), _side(b, gb)]}


FRIENDS = {"a": Friend("가", None), "b": Friend("나", None), "c": Friend("다", None)}


class EloTests(unittest.TestCase):
    def test_winner_gains_and_bigger_margin_moves_more(self):
        close = elo([_match(1, "a", 1, "b", 0)])
        rout = elo([_match(1, "a", 5, "b", 0)])
        self.assertGreater(close["a"], ELO_START)
        self.assertAlmostEqual(close["a"] + close["b"], 2 * ELO_START)
        self.assertGreater(rout["a"], close["a"])

    def test_standings_count_only_the_window(self):
        history = [_match(1, "a", 2, "b", 0), _match(2, "b", 1, "c", 1), _match(3, "c", 3, "a", 0)]
        rows = {r.ouid: r for r in standings(history, history[1:], FRIENDS)}
        self.assertEqual((rows["a"].w, rows["a"].l), (0, 1))
        self.assertEqual(rows["b"].d, 1)
        self.assertEqual(rows["c"].games, 2)


class LeagueTriggerTests(unittest.TestCase):
    def test_bootstraps_then_sends_after_enough_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStore(Path(tmp) / "db.sqlite")
            settings = Mock(discord_fc_thread_ids=("thread",))
            discord = Mock()
            n = 0

            def play(count: int) -> None:
                nonlocal n
                for _ in range(count):
                    n += 1
                    match = _match(n, "a", n % 3, "b", 1)
                    store.upsert_fc_match(match)
                    store.mark_notification_done(PLATFORM, match["matchId"], "sent")

            play(3)
            self.assertEqual(_publish_league(settings, store, discord, FRIENDS), 0)  # 처음엔 기준점만
            self.assertEqual(store.last_notification(LEAGUE), "m003")
            play(LEAGUE_EVERY - 1)
            self.assertEqual(_publish_league(settings, store, discord, FRIENDS), 0)
            play(1)
            self.assertEqual(_publish_league(settings, store, discord, FRIENDS), 1)
            discord.send_with_files.assert_called_once()
            self.assertEqual(store.last_notification(LEAGUE), f"m{3 + LEAGUE_EVERY:03d}")
            store.close()


if __name__ == "__main__":
    unittest.main()
