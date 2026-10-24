import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from imposter_finder.backup import pull, push
from imposter_finder.storage import LocalStore


class FakeD1:
    """D1 /query와 같은 모양: SQL 하나 + 값 목록 → 행 dict 목록."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row

    def query(self, sql, params=None):
        rows = self.db.execute(sql, params or []).fetchall()
        self.db.commit()
        return [dict(r) for r in rows]


class BackupTests(unittest.TestCase):
    def test_round_trip_and_empty_database_never_erases_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, restored, empty = Path(tmp, "src.db"), Path(tmp, "restored.db"), Path(tmp, "empty.db")
            store = LocalStore(src)
            store.upsert_fc_match({"matchId": "m1", "matchType": 40, "matchDate": "2026-10-01T00:00:00",
                                   "matchInfo": [{"ouid": "a"}, {"ouid": "b"}]})
            store.mark_notification_done("fconline", "m1", "sent")
            store.set_fc_ouid("건우", "a")
            store.close()
            remote = FakeD1()
            first = push(src, remote)
            self.assertEqual(first["fc_matches"], 1)
            self.assertEqual(push(src, remote)["fc_matches"], 0)  # 이미 있는 기록은 다시 보내지 않는다

            LocalStore(empty).close()
            push(empty, remote)  # 캐시가 날아가 빈 DB로 돌아도 지우지 않는다
            pull(restored, remote)
            back = LocalStore(restored)
            self.assertEqual(back.fc_match("m1")["matchId"], "m1")
            self.assertTrue(back.notification_done("fconline", "m1"))
            self.assertEqual(back.fc_ouid("건우"), "a")
            back.close()

    def test_bootstrap_markers_round_trip_and_legacy_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, restored = Path(tmp, "src.db"), Path(tmp, "restored.db")
            store = LocalStore(src)
            store.mark_bootstrap_complete("pubg")  # 경기 없는 성공한 초기 수집도 보존
            store.upsert_fc_match({"matchId": "m1", "matchType": 40, "matchDate": "2026-10-01T00:00:00"})
            store.mark_notification_done("fconline", "m1", "bootstrap")
            store.close()
            remote = FakeD1()
            push(src, remote)
            pull(restored, remote)
            store = LocalStore(restored)
            self.assertTrue(store.bootstrap_complete("pubg"))
            self.assertFalse(store.bootstrap_complete("fc"))  # 부분 수집은 알림이 있어도 승격하지 않는다
            store.close()

            remote.query("DROP TABLE game_bootstraps")
            query = remote.query

            def legacy_query(sql, params=None):
                try:
                    return query(sql, params)
                except sqlite3.OperationalError as exc:
                    raise RuntimeError(str(exc)) from exc  # D1의 없는 표 오류

            remote.query = legacy_query
            pull(restored, remote)
            store = LocalStore(restored)
            self.assertFalse(store.bootstrap_complete("pubg"))
            self.assertTrue(store.bootstrap_complete("fc"))
            store.close()


if __name__ == "__main__":
    unittest.main()


class SqlErrorAsHttp400(unittest.TestCase):
    def test_missing_table_is_a_runtime_error(self):
        import io
        import urllib.error
        from unittest import mock
        from imposter_finder.backup import D1
        body = io.BytesIO(json.dumps({"success": False, "errors": [{"message": "no such table"}]}).encode())
        error = urllib.error.HTTPError("https://d1", 400, "Bad Request", {}, body)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError):
                D1("t", "a", "d").query("SELECT COUNT(*) AS n FROM game_bootstraps")
