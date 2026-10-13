import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.free_session import FreePracticeSession  # noqa: E402
from app.grading import grade_from_score  # noqa: E402
from app.scheduling import commit_formal_review  # noqa: E402
from app.storage import Database  # noqa: E402

BASE_TIME = 1_700_000_000_000


def temp_db_path(tag: str = "free") -> Path:
    return ROOT / f"_test-{tag}-{uuid.uuid4().hex[:8]}.db"


def remove_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink()
        except OSError:
            pass


def analysis(score, **overrides):
    data = {
        "match_score": score,
        "matched_points": ["保持轻松语气"],
        "missing_points": [],
        "differences": [],
        "explanation": f"匹配度 {score} 的说明",
    }
    data.update(overrides)
    return data


class FreePracticeIsolationTest(unittest.TestCase):
    def setUp(self):
        self.db_path = temp_db_path()
        self.db = Database(self.db_path)
        self.db.initialize()
        self.card_a = self.db.create_card("正面 A", "标准回复 A", BASE_TIME - 1000)
        self.card_b = self.db.create_card("正面 B", "标准回复 B", BASE_TIME - 2000)

    def tearDown(self):
        self.db.close()
        remove_db(self.db_path)

    def run_formal_review(self, card_id, score, when=BASE_TIME):
        card = self.db.get_card(card_id)
        attempt = self.db.record_attempt(card, "review", "我的回答", when)
        self.db.attach_analysis(attempt["id"], analysis(score), "deepseek-flash", when)
        commit_formal_review(self.db, attempt["id"], grade_from_score(score), when)

    def run_free(self, card_id, score=70, when=BASE_TIME + 1000):
        session = FreePracticeSession(self.db)
        card = self.db.get_card(card_id)
        attempt = session.start(card, "自由练习回答", when)
        session.attach_result(attempt["id"], {"analysis": analysis(score), "model": "deepseek-flash"}, when)
        return attempt

    def snapshot(self):
        logs = [dict(row) for row in self.db.conn.execute("SELECT * FROM review_logs").fetchall()]
        return {
            "scheduling": self.db.list_scheduling(),
            "review_logs": logs,
            "cards": self.db.list_cards(),
        }

    def test_repeated_free_practice_keeps_every_fsrs_field_identical(self):
        self.run_formal_review(self.card_a["id"], 90)
        before = self.snapshot()

        for round_index in range(3):
            self.run_free(self.card_a["id"], 30, BASE_TIME + 2000 + round_index)
            self.run_free(self.card_b["id"], 95, BASE_TIME + 3000 + round_index)

        after = self.snapshot()
        self.assertEqual(after["scheduling"], before["scheduling"])
        self.assertEqual(after["review_logs"], before["review_logs"])
        self.assertEqual(after["cards"], before["cards"])

        self.assertEqual(self.db.count_attempts("free"), 6)
        for attempt in self.db.list_attempts():
            if attempt["mode"] == "free":
                self.assertIsNone(attempt["grade"])
                self.assertEqual(attempt["grade_applied"], 0)
                self.assertIsNone(attempt["review_log_id"])

        card_b_record = self.db.get_scheduling(self.card_b["id"])
        self.assertEqual(card_b_record["formal_reps"], 0)
        self.assertIsNone(card_b_record["last_formal_review_at"])

    def test_free_practice_keeps_due_and_reps(self):
        self.run_formal_review(self.card_a["id"], 70)
        before = dict(self.db.get_scheduling(self.card_a["id"]))

        self.run_free(self.card_a["id"], 95, BASE_TIME + 9_000_000)
        self.run_free(self.card_a["id"], 10, BASE_TIME + 9_500_000)

        after = dict(self.db.get_scheduling(self.card_a["id"]))
        self.assertEqual(after, before)
        self.assertEqual(after["formal_reps"], 1)
        self.assertEqual(after["due"], before["due"])
        self.assertEqual(after["stability"], before["stability"])
        self.assertEqual(after["difficulty"], before["difficulty"])

    def test_free_practice_after_ai_failure_keeps_fsrs_and_answer(self):
        before = self.snapshot()
        session = FreePracticeSession(self.db)
        card = self.db.get_card(self.card_a["id"])
        attempt = session.start(card, "随便写一句", BASE_TIME + 100)
        session.attach_error(attempt["id"], "DeepSeek 返回错误（HTTP 500）")

        self.assertEqual(self.snapshot(), before)
        stored = self.db.get_attempt(attempt["id"])
        self.assertEqual(stored["user_answer"], "随便写一句")
        self.assertIsNone(stored["analysis_json"])
        self.assertIn("HTTP 500", stored["analysis_error"])

    def test_free_session_never_produces_a_grade(self):
        session = FreePracticeSession(self.db)
        self.assertIsNone(session.grade_for(analysis(95)))

    def test_free_practice_on_new_card_stays_due(self):
        before = dict(self.db.get_scheduling(self.card_b["id"]))
        self.run_free(self.card_b["id"], 88)
        self.assertEqual(dict(self.db.get_scheduling(self.card_b["id"])), before)
        self.assertIn(self.card_b["id"], self.db.list_due_card_ids(BASE_TIME + 10_000))

    def test_deleting_card_cleans_free_attempts(self):
        self.run_free(self.card_a["id"], 60, BASE_TIME + 100)
        removed = self.db.delete_card(self.card_a["id"])
        self.assertEqual(removed, {"scheduling": 1, "review_logs": 0, "attempts": 1})


class SourceLevelIsolationTest(unittest.TestCase):
    """从源码层面确认自由练习没有更新调度数据的路径。"""

    def setUp(self):
        self.app_dir = ROOT / "app"

    def test_free_practice_modules_do_not_reference_scheduling_writes(self):
        for name in ("free_session.py", "session_base.py"):
            source = (self.app_dir / name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertNotIn("from app.scheduling import", source)
                self.assertNotIn("import app.scheduling", source)
                self.assertNotIn("UPDATE scheduling", source)
                self.assertNotIn("INSERT INTO scheduling", source)
                self.assertNotIn("DELETE FROM scheduling", source)

    def test_only_scheduling_module_updates_scheduling_table(self):
        updates = []
        inserts = []
        for path in sorted(self.app_dir.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "UPDATE scheduling" in source:
                updates.append(path.name)
            if "INSERT INTO scheduling" in source:
                inserts.append(path.name)
        # 更新调度状态的地方只能有一个；插入只允许出现在「新建卡片」和「导入备份」两条明确路径里
        self.assertEqual(updates, ["scheduling.py"])
        self.assertEqual(inserts, ["backup.py", "storage.py"])
        self.assertNotIn("free_session.py", updates + inserts)


if __name__ == "__main__":
    unittest.main()
