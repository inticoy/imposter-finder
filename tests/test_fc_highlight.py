from io import BytesIO
import unittest
from unittest.mock import patch

from PIL import Image

from imposter_finder.analysis.fc_highlight import _team_kit, best_goal, build_highlight, render
from imposter_finder.analysis.fc_report import Friend, Team


def _shot(minute: int, x: float, y: float, kind: int = 1, result: int = 3, **extra) -> dict:
    return {"goalTime": minute * 60, "x": x, "y": y, "type": kind, "result": result, "spId": 101, "assist": False,
            "assistX": 0.5, "assistY": 0.5, **extra}


def _team(name: str, color: tuple, shots: list[dict], won: bool) -> Team:
    side = {"ouid": name, "nickname": name, "shootDetail": shots, "player": [{"spId": 101, "spPosition": 25}],
            "matchDetail": {"matchResult": "승" if won else "패"},
            "shoot": {"goalTotal": sum(1 for s in shots if s["result"] == 3)}}
    return Team(side, Friend(name, None), None, None, color)


class FakeMeta:
    def player_name(self, spid: int) -> str:
        return {101: "웨인 루니", 102: "데이비드 베컴"}.get(spid, "선수")

    def season_image(self, spid: int) -> None:
        return None


class BestGoalTests(unittest.TestCase):
    def test_prefers_a_long_curler_over_a_tap_in(self):
        tap_in = _shot(10, 0.97, 0.5, assist=True, assistX=0.9, assistY=0.3, assistSpId=102)
        curler = _shot(86, 0.75, 0.4, kind=2)
        left = _team("a", (220, 40, 50), [tap_in, curler], True)
        right = _team("b", (240, 240, 240), [_shot(30, 0.9, 0.5, result=2)], False)
        team, shot = best_goal(left, right)
        self.assertIs(team, left)
        self.assertIs(shot, curler)

    def test_no_goal_means_no_highlight(self):
        left = _team("a", (220, 40, 50), [_shot(10, 0.9, 0.5, result=1)], False)
        right = _team("b", (240, 240, 240), [], False)
        self.assertIsNone(best_goal(left, right))
        self.assertIsNone(render(left, right, FakeMeta()))

    def test_failure_is_swallowed(self):
        match = {"matchInfo": [{"ouid": "x"}, {"ouid": "y"}]}  # 친구 정보가 없어 실패한다
        self.assertIsNone(build_highlight(match, {}, FakeMeta(), None))


class KitTests(unittest.TestCase):
    def test_kit_comes_from_the_team_name_not_the_crest(self):
        city = Team({}, Friend("a", None), None, "21-22 맨체스터 시티", (0, 75, 150))  # 엠블럼은 남색
        self.assertEqual(_team_kit(city)["shirt"], (108, 172, 222))  # 유니폼은 하늘색
        unknown = Team({}, Friend("b", None), None, "없는 팀", (10, 20, 30))
        self.assertEqual(_team_kit(unknown)["shirt"], (10, 20, 30))


@patch("imposter_finder.analysis.fc_highlight.face_image", lambda spid: "")  # 네트워크 없이
class RenderTests(unittest.TestCase):
    def test_every_shot_type_renders(self):
        cases = [(1, True), (1, False), (2, True), (3, True), (3, False), (6, False), (7, True), (8, False), (9, False), (13, True)]
        for kind, assisted in cases:
            with self.subTest(kind=kind, assisted=assisted):
                extra = {"assist": True, "assistX": 0.8, "assistY": 0.15, "assistSpId": 102} if assisted else {}
                x, y = (0.895, 0.5) if kind == 9 else (0.78, 0.35) if kind == 8 else (0.88, 0.6)
                left = _team("a", (220, 40, 50), [_shot(80, x, y, kind=kind, **extra)], True)
                right = _team("b", (250, 250, 250), [], False)
                data = render(left, right, FakeMeta(), size=0.25)
                with Image.open(BytesIO(data)) as gif:
                    self.assertGreater(gif.n_frames, 3)
                    gif.seek(gif.n_frames - 1)
                    self.assertEqual(gif.info["duration"], 3500)


if __name__ == "__main__":
    unittest.main()
