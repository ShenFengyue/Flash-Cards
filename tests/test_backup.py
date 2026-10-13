import json
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backup import apply_backup, build_backup, summarize_backup, validate_backup  # noqa: E402
from app.grading import grade_from_score  # noqa: E402
from app.scheduling import commit_formal_review  # noqa: E402
from app.storage import Database  # noqa: E402

BASE_TIME = 1_700_000_000_000


def temp_db_path(tag: str = "backup") -> Path:
    return ROOT / f"_test-{tag}-{uuid.uuid4().hex[:8]}.db"


def remove_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(path) + suffix).unlink()
        except OSError:
            pass


def cleanup_db(db, path: Path) -> None:
    """先关闭连接再删文件，否则 Windows 上文件仍被占用。"""
    db.close()
    remove_db(path)


def analysis(score):
    return {
        "match_score": score,
        "matched_points": ["a"],
        "missing_points": [],
        "differences": [],
        "explanation": "说明",
    }


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.db_path = temp_db_path("source")
        self.db = Database(self.db_path)
        self.db.initialize()
        self.card = self.db.create_card("正面", "标准回复", BASE_TIME - 1000)
        self.db.create_card("正面 2", "标准回复 2", BASE_TIME - 2000)
        attempt = self.db.record_attempt(self.card, "review", "我的回答", BASE_TIME)
        self.db.attach_analysis(attempt["id"], analysis(85), "deepseek-flash", BASE_TIME)
        commit_formal_review(self.db, attempt["id"], grade_from_score(85), BASE_TIME)
        self.db.record_attempt(self.card, "free", "自由练习回答", BASE_TIME + 1)

    def tearDown(self):
        self.db.close()
        remove_db(self.db_path)

    def new_database(self, name):
        path = temp_db_path(name)
        other = Database(path)
        other.initialize()
        self.addCleanup(cleanup_db, other, path)
        return other

    def test_export_and_restore_round_trip(self):
        backup = build_backup(self.db)
        self.assertEqual(backup["app"], "social-intuition-flashcard")
        self.assertEqual(backup["counts"]["cards"], 2)
        self.assertEqual(backup["counts"]["review_logs"], 1)
        self.assertEqual(backup["counts"]["attempts"], 2)

        data, issues = validate_backup(json.dumps(backup, ensure_ascii=False))
        self.assertEqual(issues, [])
        self.assertIsNotNone(data)

        restored = self.new_database("restored.db")
        outcome = apply_backup(restored, data, "replace", BASE_TIME + 5000)
        self.assertEqual(outcome["cards"], 2)
        self.assertEqual(restored.counts()["review_logs"], 1)
        self.assertEqual(restored.counts()["attempts"], 2)
        self.assertEqual(restored.get_scheduling(self.card["id"]), self.db.get_scheduling(self.card["id"]))
        self.assertIn("卡片 2 张", summarize_backup(data))

    def test_merge_keeps_local_only_cards(self):
        backup = build_backup(self.db)
        data, _ = validate_backup(json.dumps(backup, ensure_ascii=False))
        local = self.new_database("merge.db")
        extra = local.create_card("本地独有", "本地回复", BASE_TIME)

        apply_backup(local, data, "merge", BASE_TIME + 1000)
        self.assertEqual(local.counts()["cards"], 3)
        self.assertIsNotNone(local.get_card(extra["id"]))
        self.assertIsNotNone(local.get_card(self.card["id"]))

    def test_replace_clears_local_data(self):
        backup = build_backup(self.db)
        data, _ = validate_backup(json.dumps(backup, ensure_ascii=False))
        local = self.new_database("replace.db")
        local.create_card("本地独有", "本地回复", BASE_TIME)

        apply_backup(local, data, "replace", BASE_TIME + 1000)
        cards = local.list_cards()
        self.assertEqual(len(cards), 2)
        self.assertNotIn("本地独有", [card["front"] for card in cards])

    def test_missing_scheduling_gets_initial_state(self):
        backup = build_backup(self.db)
        backup["scheduling"] = []
        data, issues = validate_backup(json.dumps(backup, ensure_ascii=False))
        self.assertEqual(issues, [])

        local = self.new_database("missing.db")
        apply_backup(local, data, "replace", BASE_TIME + 1000)
        record = local.get_scheduling(self.card["id"])
        self.assertIsNotNone(record)
        self.assertEqual(record["formal_reps"], 0)
        self.assertEqual(record["state"], "learning")

    def test_free_attempt_tampering_is_corrected(self):
        backup = build_backup(self.db)
        for item in backup["attempts"]:
            item["grade_applied"] = 1
            item["grade"] = "easy"
        data, _ = validate_backup(json.dumps(backup, ensure_ascii=False))
        local = self.new_database("tampered.db")
        apply_backup(local, data, "replace", BASE_TIME + 1000)
        free_attempts = [item for item in local.list_attempts() if item["mode"] == "free"]
        self.assertEqual(len(free_attempts), 1)
        self.assertEqual(free_attempts[0]["grade_applied"], 0)
        self.assertIsNone(free_attempts[0]["grade"])

    def test_unknown_import_mode_is_rejected(self):
        data = build_backup(self.db)
        with self.assertRaises(ValueError):
            apply_backup(self.db, data, "something-else")


class BackupValidationTest(unittest.TestCase):
    def assert_invalid(self, raw, fragment):
        data, issues = validate_backup(raw)
        self.assertIsNone(data)
        self.assertTrue(any(fragment in issue for issue in issues), f"{fragment} not in {issues}")

    def test_rejects_broken_json(self):
        self.assert_invalid("{不是 json", "不是合法的 JSON")

    def test_rejects_missing_arrays(self):
        self.assert_invalid({}, "cards")
        self.assert_invalid({"cards": [], "scheduling": {}}, "scheduling")

    def test_rejects_bad_cards(self):
        self.assert_invalid({"cards": [{"id": ""}], "scheduling": []}, "cards[0].id")
        self.assert_invalid({"cards": [{"id": "a", "front": "", "back": "b"}], "scheduling": []}, "front")
        self.assert_invalid(
            {"cards": [{"id": "a", "front": "f", "back": "b"}, {"id": "a", "front": "f", "back": "b"}], "scheduling": []},
            "id 重复",
        )

    def test_rejects_bad_scheduling(self):
        cards = [{"id": "a", "front": "f", "back": "b"}]
        self.assert_invalid(
            {"cards": cards, "scheduling": [{"card_id": "ghost", "due": 1, "state": "learning"}]},
            "找不到对应的闪卡",
        )
        self.assert_invalid(
            {"cards": cards, "scheduling": [{"card_id": "a", "due": 1, "state": "乱写"}]},
            "state",
        )
        self.assert_invalid(
            {"cards": cards, "scheduling": [{"card_id": "a", "state": "learning"}]},
            "due",
        )

    def test_rejects_bad_attempts(self):
        cards = [{"id": "a", "front": "f", "back": "b"}]
        self.assert_invalid(
            {"cards": cards, "scheduling": [], "attempts": [{"id": "x", "card_id": "a", "mode": "formal"}]},
            "mode 必须是",
        )
        self.assert_invalid(
            {"cards": cards, "scheduling": [], "attempts": [{"id": "x", "card_id": "ghost", "mode": "free"}]},
            "找不到对应的闪卡",
        )

    def test_validation_failure_never_touches_local_data(self):
        path = temp_db_path("validation")
        db = Database(path)
        db.initialize()
        self.addCleanup(cleanup_db, db, path)
        card = db.create_card("正面", "标准回复", BASE_TIME)
        data, issues = validate_backup({"cards": [{"id": "x"}], "scheduling": []})
        self.assertIsNone(data)
        self.assertTrue(issues)
        # 校验失败时调用方不会执行导入，本地数据保持原样
        self.assertEqual(db.list_cards(), [card])
        self.assertEqual(db.counts()["scheduling"], 1)


if __name__ == "__main__":
    unittest.main()
