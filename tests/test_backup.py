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


if __name__ == "__main__":
    unittest.main()
