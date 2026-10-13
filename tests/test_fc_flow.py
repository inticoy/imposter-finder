from io import BytesIO
import unittest

from PIL import Image

from imposter_finder.analysis.fc_flow import render
from imposter_finder.analysis.fc_report import Friend, Team
from imposter_finder.analysis.fc_xg import shot_label, shot_xg


def _shot(minute: int, x: float, y: float, result: int, **extra) -> dict:
    return {"goalTime": minute * 60, "x": x, "y": y, "type": 1, "result": result, "spId": 1, "assist": False,
            "hitPost": False, "inPenalty": x > 0.84, **extra}


def _team(name: str, color: tuple, shots: list[dict], won: bool) -> Team:
    side = {"ouid": name, "nickname": name, "shootDetail": shots,
            "matchDetail": {"matchResult": "승" if won else "패"}, "shoot": {}}
    return Team(side, Friend(name, None), None, None, color)


class FcXgTests(unittest.TestCase):
    def test_close_central_shots_beat_long_range(self):
        close = shot_xg({"x": 0.95, "y": 0.5, "type": 1, "assist": True})
        far = shot_xg({"x": 0.72, "y": 0.2, "type": 1, "assist": False})
        self.assertGreater(close, 0.5)
        self.assertLess(far, 0.2)

    def test_only_verified_shot_types_are_named(self):
        self.assertEqual(shot_label({"type": 9}), "페널티킥")
        self.assertIsNone(shot_label({"type": 13}))


class FcFlowTests(unittest.TestCase):
    def test_replays_every_shot_and_ends_on_full_time(self):
        left = _team("a", (220, 40, 50), [_shot(10, 0.93, 0.45, 3, assist=True, assistX=0.8, assistY=0.2,
                                                assistSpId=2)], True)
        right = _team("b", (240, 240, 240), [_shot(30, 0.75, 0.6, 2), _shot(70, 0.9, 0.5, 1)], False)
        data = render(left, right, {1: "슈터", 2: "도우미"})
        with Image.open(BytesIO(data)) as gif:
            self.assertGreater(gif.n_frames, 20)
            gif.seek(gif.n_frames - 1)
            self.assertEqual(gif.info["duration"], 4500)


if __name__ == "__main__":
    unittest.main()
