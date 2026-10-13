import unittest
from unittest.mock import patch

from imposter_finder.analysis.pubg_highlight import pick
from imposter_finder.analysis.pubg_report import build_highlight
from imposter_finder.registry import PubgPlayer


def _char(name: str, team: int, z: float = 0, ai: bool = False) -> dict:
    return {"name": name, "teamId": team, "accountId": ("ai." if ai else "account.") + name,
            "location": {"x": 1000, "y": 1000, "z": z}, "health": 100}


def _at(sec: float) -> str:
    return f"2026-10-01T00:{int(sec // 60):02d}:{sec % 60:06.3f}Z"


START = {"_T": "LogMatchStart", "_D": _at(0),
         "characters": [{"character": _char(n, t)} for n, t in (("me", 1), ("mate", 1), ("foe", 2), ("bot", 3))]}


def _knock(sec: float, attacker: dict, victim: dict, distance: float, head: bool = False) -> dict:
    return {"_T": "LogPlayerMakeGroggy", "_D": _at(sec), "attacker": attacker, "victim": victim,
            "damageCauserName": "WeapM24_C", "distance": distance * 100,
            "damageReason": "HeadShot" if head else "TorsoShot"}


class PickTests(unittest.TestCase):
    def test_bots_and_teammates_are_not_highlights(self):
        telemetry = [START, _knock(60, _char("me", 1), _char("bot", 3, ai=True), 300, head=True),
                     _knock(70, _char("me", 1), _char("mate", 1), 200)]
        self.assertIsNone(pick(telemetry, {"me"}))

    def test_long_headshot_from_above_beats_close_kill(self):
        telemetry = [START, _knock(60, _char("me", 1), _char("foe", 2), 8),
                     _knock(400, _char("me", 1, z=2000), _char("foe", 2), 150, head=True)]
        hl = pick(telemetry, {"ME"})  # 닉네임 대소문자 무시
        self.assertEqual((hl.best.t, hl.best.headshot, round(hl.best.height)), (400, True, 20))
        self.assertEqual(len(hl.plays), 1)

    def test_kills_close_together_are_one_scene(self):
        telemetry = [START, _knock(100, _char("me", 1), _char("foe", 2), 30),
                     _knock(108, _char("me", 1), _char("foe", 2), 30),
                     _knock(400, _char("me", 1), _char("foe", 2), 40)]
        hl = pick(telemetry, {"me"})
        self.assertEqual([p.t for p in hl.plays], [100, 108])


class BuildHighlightTests(unittest.TestCase):
    def test_no_message_without_a_friend_kill(self):
        match = {"data": {"attributes": {"gameMode": "squad", "mapName": "Baltic_Main", "duration": 600}},
                 "included": [{"type": "participant", "attributes": {"stats": {"name": "me", "damageDealt": 0}}}]}
        with patch("imposter_finder.analysis.pubg_report.analyze_pubg_match",
                   return_value={"scores": {"나": {"pubg_name": "me", "rating": 3}}}):
            self.assertIsNone(build_highlight("steam", match, [START], [PubgPlayer("나", None, "steam", "me")]))

    def test_team_deathmatch_has_no_highlight(self):
        match = {"data": {"attributes": {"gameMode": "tdm"}}, "included": []}
        self.assertIsNone(build_highlight("steam", match, [START], []))


if __name__ == "__main__":
    unittest.main()
