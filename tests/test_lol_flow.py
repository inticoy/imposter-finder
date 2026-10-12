from io import BytesIO
import random
import unittest
from unittest.mock import Mock, patch
from PIL import Image
from imposter_finder.analysis.lol_flow import encode_bounded, schedule
from imposter_finder.analysis.lol_report import Friend, build_report


class FlowEncodingTests(unittest.TestCase):
    def test_reduces_bytes_without_losing_start_and_end(self):
        rng = random.Random(7)
        palette = [v for n in range(256) for v in (n, n, n)]
        frames = []
        for i in range(30):
            frame = Image.new("P", (80, 60))
            frame.putpalette(palette)
            frame.putdata([rng.randrange(256) for _ in range(80*60)])
            frame.putpixel((0, 0), i)
            frames.append(frame)
        data = encode_bounded(frames, max_bytes=22000)
        self.assertLessEqual(len(data), 22000)
        with Image.open(BytesIO(data)) as gif:
            self.assertLess(gif.n_frames, len(frames))
            self.assertEqual(gif.convert("RGB").getpixel((0, 0)), (0, 0, 0))
            gif.seek(gif.n_frames - 1)
            self.assertEqual(gif.convert("RGB").getpixel((0, 0)), (29, 29, 29))
            self.assertEqual(gif.info["duration"], 3500)

    def test_speed_changes_preserve_complete_match(self):
        for end in (34*60000, 70*60000):
            normal = schedule([end/2], end)
            faster = schedule([end/2], end, sec_per_min=.8)
            self.assertLess(len(faster), len(normal))
            self.assertEqual((faster[0], faster[-1]), (0, end))


class AutomaticReportFlowTests(unittest.TestCase):
    def setUp(self):
        self.players = []
        for i in range(1, 11):
            self.players.append({"participantId": i, "puuid": str(i), "teamId": 100 if i<=5 else 200,
                "win": i<=5, "championName": f"Champion{i}", "riotIdGameName": f"Player{i}",
                "kills": 2, "deaths": 1, "assists": 2, "teamPosition": "",
                "totalDamageDealtToChampions": 1000+i, "totalDamageTaken": 1000+i,
                "totalMinionsKilled": 20, "neutralMinionsKilled": 10, "goldEarned": 1000,
                "visionScore": 10})
        self.match = {"info": {"participants": self.players, "queueId": 420, "mapId": 11,
            "gameDuration": 600, "teams": [{"teamId": team, "objectives": {"champion": {"kills": 10}}}
                                         for team in (100, 200)]}}
        self.timeline = {"info": {"frames": [{"timestamp": 0, "events": [], "participantFrames": {
            str(i): {"totalGold": 1000, "championStats": {"health": 100}} for i in range(1, 11)}}]}}
        self.friends = {"1": Friend("친구1", None), "2": Friend("친구2", None)}
        self.dd = Mock()
        self.dd.champion_name.side_effect = lambda name: name
        self.dd.champion_icon.side_effect = lambda name: "https://example.test/"+name
        for fn in ("render_banner", "render_players", "render_objectives", "render_gold_gif", "opgg_link", "_evaluate"):
            p = patch("imposter_finder.analysis.lol_report."+fn, return_value=b"image" if fn.startswith("render") else {} if fn=="_evaluate" else None)
            p.start();self.addCleanup(p.stop)

    def test_flow_replaces_both_legacy_cards_and_is_in_payload(self):
        with patch("imposter_finder.analysis.lol_flow.render", return_value=(b"gif", None)):
            payload, files = build_report(self.match, self.timeline, self.friends, self.dd, None, "unused")
        self.assertEqual([f[0] for f in files], ["result.png", "players.png", "flow.gif"])
        galleries = [c for c in payload["components"][-1]["components"] if c["type"]==12]
        self.assertEqual(galleries[-1]["items"][0]["media"]["url"], "attachment://flow.gif")

    def test_render_failure_preserves_legacy_cards(self):
        with patch("imposter_finder.analysis.lol_flow.render", side_effect=RuntimeError("asset unavailable")), patch("builtins.print"):
            _, files = build_report(self.match, self.timeline, self.friends, self.dd, None, "unused")
        self.assertEqual([f[0] for f in files], ["result.png", "players.png", "objectives.png", "gold.gif"])

    def test_oversize_flow_is_not_uploaded(self):
        with patch("imposter_finder.analysis.lol_flow.render", return_value=(b"x"*(8*1024*1024+1), None)), patch("builtins.print"):
            _, files = build_report(self.match, self.timeline, self.friends, self.dd, None, "unused")
        self.assertNotIn("flow.gif", [f[0] for f in files])
        self.assertEqual(files[-1][0], "gold.gif")

    def test_missing_timeline_and_unsupported_map_skip_renderer(self):
        with patch("imposter_finder.analysis.lol_flow.render") as renderer:
            _, files = build_report(self.match, None, self.friends, self.dd, None, "unused")
            self.assertEqual([f[0] for f in files], ["result.png", "players.png", "objectives.png"])
            self.match["info"]["mapId"] = 12
            _, files = build_report(self.match, self.timeline, self.friends, self.dd, None, "unused")
            self.assertEqual(files[-1][0], "gold.gif")
            renderer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
