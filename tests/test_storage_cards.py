import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.scheduling import commit_formal_review  # noqa: E402
from app.seeds import SEED_CARDS  # noqa: E402
from app.storage import Database, query_cards, stats_of  # noqa: E402

def temp_db_path(tag: str = "storage") -> Path:
    """测试数据库直接放在程序目录下（用唯一文件名），用完即删。"""
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
        "matched_points": ["保持轻松语气"],
        "missing_points": [],
        "differences": [],
        "explanation": f"匹配度 {score} 的说明",
    }


class StorageCardsTest(unittest.TestCase):
    def setUp(self):
        self.db_path = temp_db_path()
        self.db = Database(self.db_path)
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        remove_db(self.db_path)

    def test_create_edit_search_filter_delete(self):
        first = self.db.create_card("  女生说：「在吗？」  ", "  在的，怎么啦？  ", 1000)
        self.assertEqual(first["front"], "女生说：「在吗？」")
        self.assertEqual(first["back"], "在的，怎么啦？")
        self.db.create_card("点菜时女生说：「随便」", "那我按我的口味点了", 2000)

        cards = self.db.list_cards_with_schedule(5000)
        self.assertEqual(len(cards), 2)
        self.assertTrue(all(card["status"] == "new" for card in cards))
        self.assertEqual(stats_of(cards), {"total": 2, "due": 2, "new": 2, "scheduled": 0})

        self.assertEqual(len(query_cards(cards, "在吗")), 1)
        self.assertEqual(len(query_cards(cards, "随便")), 1)
        self.assertEqual(len(query_cards(cards, "不存在的关键词")), 0)
        ascending = query_cards(cards, sort="created_asc")
        self.assertLess(ascending[0]["created_at"], ascending[1]["created_at"])

        updated = self.db.update_card(first["id"], "新正面", "新背面", 3000)
        self.assertEqual(updated["front"], "新正面")
        self.assertEqual(updated["updated_at"], 3000)

        related = self.db.count_related(first["id"])
        self.assertEqual(related, {"scheduling": 1, "review_logs": 0, "attempts": 0})
        removed = self.db.delete_card(first["id"])
        self.assertEqual(removed["scheduling"], 1)
        self.assertEqual(self.db.counts()["cards"], 1)
        self.assertEqual(self.db.counts()["scheduling"], 1)

    def test_editing_standard_reply_keeps_fsrs_history(self):
        card = self.db.create_card("正面", "标准回复", 1000)
        attempt = self.db.record_attempt(card, "review", "我的回答", 1000)
        self.db.attach_analysis(attempt["id"], analysis(90), "deepseek-flash", 1000)
        commit_formal_review(self.db, attempt["id"], "easy", 1000)
        before = self.db.get_scheduling(card["id"])

        self.db.update_card(card["id"], "改过的正面", "改过的标准回复", 2000)

        self.assertEqual(self.db.get_scheduling(card["id"]), before)
        self.assertEqual(len(self.db.list_attempts()), 1)

    def test_delete_card_cleans_related_rows(self):
        card = self.db.create_card("正面", "标准回复", 1000)
        free = self.db.record_attempt(card, "free", "自由练习", 1000)
        review = self.db.record_attempt(card, "review", "正式复习", 1000)
        self.db.attach_analysis(review["id"], analysis(70), "deepseek-flash", 1000)
        commit_formal_review(self.db, review["id"], "good", 1000)
        self.assertTrue(free["id"])

        removed = self.db.delete_card(card["id"])
        self.assertEqual(removed, {"scheduling": 1, "review_logs": 1, "attempts": 2})
        self.assertEqual(self.db.counts(), {"cards": 0, "scheduling": 0, "review_logs": 0, "attempts": 0})

    def test_seed_runs_once_and_never_overwrites_edits(self):
        inserted = self.db.seed_if_needed(SEED_CARDS, 1_700_000_000_000)
        self.assertEqual(inserted, len(SEED_CARDS))
        self.assertEqual(self.db.counts()["cards"], len(SEED_CARDS))

        first = self.db.list_cards()[-1]
        self.db.update_card(first["id"], "我自己改的正面", "我自己改的回复", 2_000_000)

        again = self.db.seed_if_needed(SEED_CARDS, 1_800_000_000_000)
        self.assertEqual(again, 0)
        self.assertEqual(self.db.counts()["cards"], len(SEED_CARDS))
        self.assertEqual(self.db.get_card(first["id"])["front"], "我自己改的正面")

    def test_seed_content_matches_requirement(self):
        self.db.seed_if_needed(SEED_CARDS, 1000)
        fronts = [card["front"] for card in self.db.list_cards()]
        backs = [card["back"] for card in self.db.list_cards()]
        self.assertEqual(len(fronts), 5)
        self.assertTrue(any("你是不是对每个女生都这么热情啊" in text for text in fronts))
        self.assertIn("哈哈，那得看遇到谁了。", backs)
        self.assertTrue(any("你一个月赚多少钱" in text for text in fronts))
        self.assertTrue(any("单方面改变" in text for text in backs))


if __name__ == "__main__":
    unittest.main()
