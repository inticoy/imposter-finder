import math
import unittest
from unittest.mock import Mock, patch

from imposter_finder.analysis import lol_highlight as H
from imposter_finder.analysis.lol_report import Friend
from imposter_finder.lol_collector import _send_highlight


def _match(map_id: int = 11) -> dict:
    players = [{"participantId": i, "puuid": str(i), "teamId": 100 if i <= 5 else 200, "win": i <= 5,
                "championName": f"Champ{i}", "riotIdGameName": f"Player{i}"} for i in range(1, 11)]
    return {"metadata": {"matchId": "KR_1"}, "info": {"mapId": map_id, "gameDuration": 1800, "participants": players}}


def _kill(t: int, killer: int, victim: int, assists=(), x: int = 7000, y: int = 7000) -> dict:
    return {"type": "CHAMPION_KILL", "timestamp": t, "killerId": killer, "victimId": victim,
            "assistingParticipantIds": list(assists), "position": {"x": x, "y": y}, "shutdownBounty": 0,
            "victimDamageReceived": [], "victimDamageDealt": []}


def _timeline(*events) -> dict:
    frames = [{"timestamp": n * 60000, "events": [e for e in events if n * 60000 <= e["timestamp"] < (n + 1) * 60000],
               "participantFrames": {str(i): {"position": {"x": 7000, "y": 7000}, "championStats": {"health": 100}}
                                     for i in range(1, 11)}} for n in range(31)]
    return {"info": {"frames": frames}}


class PickTests(unittest.TestCase):
    def test_no_friend_kill_means_no_highlight(self):
        timeline = _timeline(_kill(60000, 6, 1))  # 상대가 친구를 잡은 킬뿐
        self.assertIsNone(H.pick(_match(), timeline, {"1", "2"}))
        self.assertIsNone(H.build_highlight(_match(), timeline, {"1": Friend("친구", None)}, Mock(), 0))

    def test_multikill_beats_single_kill(self):
        double = [_kill(600000, 2, 6), _kill(604000, 2, 7),
                  {"type": "CHAMPION_SPECIAL_KILL", "killType": "KILL_MULTI", "multiKillLength": 2, "killerId": 2,
                   "timestamp": 604000}]
        timeline = _timeline(_kill(300000, 1, 8, assists=[3]), *double)
        hl = H.pick(_match(), timeline, {"1", "2"})
        self.assertEqual((hl.star, hl.multi, [k["victimId"] for k in hl.kills]), (2, 2, [6, 7]))
        self.assertEqual(H.headline(hl), "더블 킬")

    def test_other_maps_are_skipped(self):
        self.assertIsNone(H.render(_match(map_id=12), _timeline(_kill(60000, 1, 6)), {"1"}))


class DamageTests(unittest.TestCase):
    def test_damage_is_grouped_by_skill_slot(self):
        kill = _kill(60000, 1, 6)
        hit = lambda slot, dmg, basic=False, name="": {"type": "OTHER", "participantId": 1, "spellSlot": slot,
                                                       "basic": basic, "spellName": name, "physicalDamage": dmg,
                                                       "magicDamage": 0, "trueDamage": 0}
        kill["victimDamageReceived"] = [hit(0, 300), hit(0, 100), hit(3, 500), hit(64, 80, basic=True),
                                        hit(4, 50, name="summonerdot"), hit(7, 20), {**hit(2, 999), "participantId": 2}]
        data = {"spells": [{"name": k, "image": {"full": f"{k}.png"}} for k in ("큐", "더블유", "이", "궁")],
                "passive": {"name": "패시브", "image": {"full": "p.png"}}, "stats": {"attackrange": 550}}
        with patch.object(H, "_champion_data", return_value=data):
            rows, per_kill = H.damage_rows(_match()["info"]["participants"][0], [kill], "1.0")
        self.assertEqual([r[0] for r in rows], ["R", "Q", "평타", "S:SummonerDot", "기타"])
        self.assertEqual(per_kill[0]["Q"], 400)
        self.assertNotIn("E", per_kill[0])  # 다른 사람의 피해는 넣지 않는다


class PathTests(unittest.TestCase):
    def setUp(self):
        H._route_cells.cache_clear()
        n = H.GRID
        grid = bytearray([255]) * (n * n)
        for y in range(40, n - 40):  # 가운데 세로 벽 (위아래로만 돌아갈 수 있다)
            for x in range(126, 130):
                grid[y * n + x] = 0
        self.patch = patch.object(H, "walk_grid", return_value=(bytes(grid), n))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        H._route_cells.cache_clear()

    def test_route_goes_around_walls(self):
        a, b = (6000.0, 7400.0), (8800.0, 7400.0)
        line = H.route(a, b)
        self.assertGreater(len(line), 2)
        for p, q in zip(line, line[1:]):  # 꺾은선의 어느 점도 벽 칸에 있지 않다
            for k in range(21):
                c = H._cell(p[0] + (q[0] - p[0]) * k / 20, p[1] + (q[1] - p[1]) * k / 20)
                self.assertTrue(H._walkable(c) or c in (H._cell(*a), H._cell(*b)))

    def test_points_inside_walls_snap_to_open_ground(self):
        wall = H._center((127, 128))
        self.assertFalse(H._walkable(H._cell(*wall)))
        self.assertTrue(H._walkable(H._cell(*H.snap(wall))))

    def test_track_walks_then_waits(self):
        track = H.Track()
        track.add(0, (1000.0, 1000.0), 20000, (2000.0, 1000.0))
        self.assertEqual(track.at(0), (1000.0, 1000.0))
        self.assertAlmostEqual(track.at(20000)[0], 2000.0)
        self.assertAlmostEqual(track.at(10000)[0], 2000.0)  # 1000을 걷고 남은 시간은 서 있다
        self.assertIsNone(track.at(-1))


class DeathTests(unittest.TestCase):
    def test_snapshot_cannot_revive_a_champion_within_seconds(self):
        t = 1_434_500
        timeline = _timeline(_kill(t, 1, 7))
        # 24:00 스냅샷에 우물 근처에서 살아 있다 → 그래도 대기 시간의 3/4 전에는 살아나지 않는다
        timeline["info"]["frames"][24]["participantFrames"]["7"]["position"] = {"x": 14000, "y": 14000}
        d0, d1, _ = H.deaths(_match()["info"], timeline)[7].spans[0]
        self.assertGreaterEqual(d1 - d0, 0.75 * H.death_seconds(1, t) * 1000 - 1)
        self.assertLess(d1 - d0, H.death_seconds(1, t) * 1000)


class CollectorTests(unittest.TestCase):
    def test_highlight_failure_never_raises(self):
        settings = Mock(discord_lol_thread_ids=("thread",))
        discord = Mock()
        with patch("imposter_finder.analysis.lol_highlight.build_highlight", side_effect=RuntimeError("boom")):
            _send_highlight(settings, discord, _match(), _timeline(), {"1": Friend("친구", None)})
        discord.send_with_files.assert_not_called()

    def test_highlight_is_sent_as_its_own_message(self):
        settings = Mock(discord_lol_thread_ids=("a", "b"))
        discord = Mock()
        message = ({"components": []}, [("highlight.gif", b"GIF", "image/gif")])
        with patch("imposter_finder.analysis.lol_highlight.build_highlight", return_value=message):
            _send_highlight(settings, discord, _match(), _timeline(), {"1": Friend("친구", None)})
        self.assertEqual(discord.send_with_files.call_count, 2)


class CameraTests(unittest.TestCase):
    def test_ground_homography_matches_projection(self):
        cam = H.Camera((7000, 7000), 4000)
        for p in ((6500, 6800), (7400, 7600)):
            a = cam.project(*p)[:2]
            b = H._apply(cam.ground(), *p)
            self.assertTrue(math.isclose(a[0], b[0], abs_tol=1e-6) and math.isclose(a[1], b[1], abs_tol=1e-6))


if __name__ == "__main__":
    unittest.main()
