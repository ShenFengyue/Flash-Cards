import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.review_session import ReviewSession  # noqa: E402
from app.storage import Database  # noqa: E402

BASE_TIME = 1_700_000_000_000


def temp_db_path(tag: str = "review") -> Path:
    return ROOT / f"_test-{tag}-{uuid.uuid4().hex[:8]}.db"


def remove_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink()
        except OSError:
            pass


def analysis(score):
    return {
        "match_score": score,
        "matched_points": ["a"],
        "missing_points": [],
        "differences": [],
        "explanation": "说明",
    }


class FormalReviewTest(unittest.TestCase):
    def setUp(self):
        self.db_path = temp_db_path()
        self.db = Database(self.db_path)
        self.db.initialize()
        self.card = self.db.create_card("正面", "标准回复", BASE_TIME - 1000)
        self.session = ReviewSession(self.db)

    def tearDown(self):
        self.db.close()
        remove_db(self.db_path)

    def start_attempt(self, score, card_id=None, mode="review", when=BASE_TIME):
        card = self.db.get_card(card_id or self.card["id"])
        attempt = self.db.record_attempt(card, mode, "我的回答", when)
        self.db.attach_analysis(attempt["id"], analysis(score), "deepseek-flash", when)
        return attempt

    def test_confirm_updates_due_stability_and_reps(self):
        before = dict(self.db.get_scheduling(self.card["id"]))
        self.assertEqual(before["formal_reps"], 0)

        attempt = self.start_attempt(90)
        result = self.session.confirm(attempt["id"], "easy", BASE_TIME)

        self.assertTrue(result["applied"])
        after = dict(self.db.get_scheduling(self.card["id"]))
        self.assertEqual(after["formal_reps"], 1)
        self.assertEqual(after["last_formal_review_at"], BASE_TIME)
        self.assertGreater(after["due"], before["due"])
        self.assertIsNotNone(after["stability"])
        self.assertEqual(self.db.counts()["review_logs"], 1)

    def test_preview_matches_what_gets_written(self):
        attempt = self.start_attempt(70)
        preview_record, _ = self.session.preview(self.card["id"], "good", analysis(70), attempt["id"], BASE_TIME)
        result = self.session.confirm(attempt["id"], "good", BASE_TIME)
        self.assertEqual(result["scheduling"]["due"], preview_record["due"])
        self.assertEqual(result["scheduling"]["stability"], preview_record["stability"])

    def test_double_confirm_is_idempotent(self):
        attempt = self.start_attempt(70)
        first = self.session.confirm(attempt["id"], "good", BASE_TIME)
        second = self.session.confirm(attempt["id"], "good", BASE_TIME + 1000)

        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        self.assertEqual(second["reason"], "already_applied")
        self.assertEqual(second["scheduling"], first["scheduling"])
        self.assertEqual(self.db.counts()["review_logs"], 1)
        self.assertEqual(self.db.get_scheduling(self.card["id"])["formal_reps"], 1)

    def test_confirm_rejects_attempt_without_analysis(self):
        attempt = self.db.record_attempt(self.card, "review", "我的回答", BASE_TIME)
        with self.assertRaises(ValueError) as context:
            self.session.confirm(attempt["id"], "good", BASE_TIME)
        self.assertIn("没有有效的匹配分析结果", str(context.exception))
        self.assertEqual(self.db.counts()["review_logs"], 0)
        self.assertEqual(self.db.get_scheduling(self.card["id"])["formal_reps"], 0)

    def test_confirm_rejects_free_practice_attempt(self):
        attempt = self.start_attempt(95, mode="free")
        with self.assertRaises(ValueError) as context:
            self.session.confirm(attempt["id"], "easy", BASE_TIME)
        self.assertIn("自由练习不允许写入", str(context.exception))
        self.assertEqual(self.db.counts()["review_logs"], 0)

    def test_confirm_rejects_grade_that_breaks_the_fixed_mapping(self):
        attempt = self.start_attempt(20)
        with self.assertRaises(ValueError) as context:
            self.session.confirm(attempt["id"], "easy", BASE_TIME)
        self.assertIn("评分与固定映射规则不一致", str(context.exception))
        self.assertEqual(self.db.counts()["review_logs"], 0)

    def test_low_score_schedules_sooner_than_high_score(self):
        second = self.db.create_card("正面 2", "标准回复 2", BASE_TIME - 2000)
        low = self.start_attempt(10, card_id=self.card["id"])
        high = self.start_attempt(95, card_id=second["id"])
        self.session.confirm(low["id"], "again", BASE_TIME)
        self.session.confirm(high["id"], "easy", BASE_TIME)

        low_due = self.db.get_scheduling(self.card["id"])["due"]
        high_due = self.db.get_scheduling(second["id"])["due"]
        self.assertLess(low_due, high_due)

    def test_reviewed_card_leaves_the_due_queue(self):
        attempt = self.start_attempt(95)
        self.session.confirm(attempt["id"], "easy", BASE_TIME)
        due_ids = self.db.list_due_card_ids(BASE_TIME + 1)
        self.assertNotIn(self.card["id"], due_ids)

    def test_review_session_queue_and_progress(self):
        second = self.db.create_card("正面 2", "标准回复 2", BASE_TIME - 2000)
        queue = self.session.build_queue(BASE_TIME)
        self.assertEqual(len(queue), 2)
        self.assertEqual(self.session.total, 2)
        self.assertFalse(self.session.finished)

        first_id = self.session.current_card_id()
        self.assertIn(first_id, [self.card["id"], second["id"]])
        self.session.advance(counted_as_updated=True)
        self.assertEqual(self.session.updated, 1)
        self.session.skip()
        self.assertEqual(self.session.skipped, 1)
        self.assertTrue(self.session.finished)

    def test_deleted_card_is_skipped_in_queue(self):
        second = self.db.create_card("正面 2", "标准回复 2", BASE_TIME - 2000)
        self.session.build_queue(BASE_TIME)
        self.db.delete_card(second["id"])
        remaining = []
        while not self.session.finished:
            card_id = self.session.current_card_id()
            if card_id is None:
                break
            remaining.append(card_id)
            self.session.advance(True)
        self.assertEqual(remaining, [self.card["id"]])


if __name__ == "__main__":
    unittest.main()
