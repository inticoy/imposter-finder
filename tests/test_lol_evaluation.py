import json
import unittest
from unittest.mock import Mock, patch
from imposter_finder.analysis.lol_report import Friend, _evaluate, _format_evaluation, _timeline_facts, evaluation_text


class EvaluationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.players = [{"participantId": 1, "puuid": "a", "teamId": 100},
                        {"participantId": 2, "puuid": "b", "teamId": 100},
                        {"participantId": 3, "puuid": "c", "teamId": 200}]
        self.info = {"participants": self.players, "mapId": 11, "gameDuration": 180}
        self.friends = {"a": Friend("준서", None), "b": Friend("도윤", None)}

    def frame(self, ts, health, gold, events=()):
        return {"timestamp": ts, "events": list(events), "participantFrames": {
            str(p["participantId"]): {"championStats": {"health": health[i]}, "totalGold": gold[i]}
            for i, p in enumerate(self.players)}}

    def test_only_confirmed_death_interval_counts_objective_loss(self):
        death = {"type": "CHAMPION_KILL", "timestamp": 50000, "victimId": 1, "killerId": 3,
                 "position": {"x": 8000, "y": 6000}, "assistingParticipantIds": []}
        def dragon(ts):
            return {"type": "ELITE_MONSTER_KILL", "timestamp": ts, "monsterType": "DRAGON", "killerTeamId": 200}
        timeline = {"info": {"frames": [self.frame(0, [100, 100, 100], [100, 100, 100]),
            self.frame(60000, [0, 100, 100], [200, 200, 200], [death, dragon(55000)]),
            self.frame(120000, [100, 100, 100], [300, 300, 300], [dragon(90000)])]}}
        facts = _timeline_facts(self.info, timeline, self.players[:2], self.friends)
        a = facts["선수별 기록"][0]
        self.assertEqual(a["체력 0 관측으로 확인한 사망 대기 중 상대 오브젝트"], ["0:55 드래곤"])
        self.assertEqual(len(facts["선수별 기록"]), 2)
        self.assertIn("인과관계", facts["근거 한계"])

    def test_zero_gold_does_not_hide_reversal(self):
        timeline = {"info": {"frames": [self.frame(0, [100]*3, [200, 0, 100]),
            self.frame(60000, [100]*3, [100, 0, 100]), self.frame(120000, [100]*3, [100, 0, 200])]}}
        facts = _timeline_facts(self.info, timeline, self.players[:2], self.friends)
        self.assertEqual(facts["골드 우열 전환"], [{"시각": "2:00", "우리 골드 차이": -100, "전환": "열세로 전환"}])

    def test_all_players_bold_names_and_line_limits(self):
        result = {"summary": ["초반 주도권을 잡았어요. "*10, "후반에는 오브젝트를 챙겼어요."],
                  "players": {"준서": ["성장을 잘 이어 갔어요. "*10], "도윤": ["팀의 오브젝트 확보에 기여했어요."]},
                  "next": ["드래곤이 나오기 전 팀원과 함께 움직이세요. "*8]}
        text = evaluation_text(_format_evaluation(result, ["준서", "도윤"]))
        for expected in ("**총평**", "**평가**", "**다음 판엔**", "**준서**", "**도윤**"):
            self.assertIn(expected, text)
        self.assertTrue(all(len(line) <= 52 for line in text.splitlines()))
        self.assertLessEqual(len(text), 4000)

    def test_generation_retries_for_hidden_image_stats(self):
        good = {"summary": ["초반 골드 흐름이 팽팽했어요.", "전령을 확보하며 우세를 잡았어요.", "바론 이후 격차를 유지했어요."],
                "players": {"준서": ["포탑 압박에 기여했어요.", "미니언 성장을 이어 갔어요."],
                            "도윤": ["오브젝트 확보에 관여했어요.", "정글 성장을 꾸준히 이어 갔어요."]},
                "next": ["드래곤 전에 팀원과 시야 확보하기", "바론 버프가 있을 때 함께 포탑 압박하기"]}
        bad = json.loads(json.dumps(good))
        bad["players"]["준서"] = ["다이애나로 활약했어요.", "한 차례 사망했어요."]
        client = Mock()
        client.models.generate_content.side_effect = [Mock(text=json.dumps(bad)), Mock(text=json.dumps(good))]
        with patch("google.genai.Client", return_value=client):
            ai = _evaluate({"선수": [{"챔피언": "다이애나"}]}, ["준서", "도윤"], "test-key", "test-model")
        text = evaluation_text(ai)
        self.assertEqual(client.models.generate_content.call_count, 2)
        self.assertNotIn("다이애나", text)
        self.assertNotIn("한 차례 사망", text)
        self.assertIn("**도윤**", text)

    def test_missing_timeline_and_oversize_text_are_safe(self):
        self.assertIn("가용성", _timeline_facts(self.info, None, self.players[:2], self.friends))
        self.assertLessEqual(len(evaluation_text({"summary": "x"*5000})), 4000)


if __name__ == "__main__":
    unittest.main()
