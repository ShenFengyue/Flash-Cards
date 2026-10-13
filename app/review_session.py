"""算法复习：只练 FSRS 判定当前到期的卡片，确认后才更新复习计划。"""

from app.grading import grade_from_score
from app.scheduling import commit_formal_review, plan_formal_review
from app.session_base import PracticeSession


class ReviewSession(PracticeSession):
    mode = "review"

    def __init__(self, db):
        super().__init__(db)
        self.queue: list[str] = []
        self.index = 0
        self.updated = 0
        self.skipped = 0

    def build_queue(self, when: int | None = None) -> list[str]:
        """进入「今日复习」时的固定队列；复习过程中不再动态变化。"""
        self.queue = self.db.list_due_card_ids(when)
        self.index = 0
        self.updated = 0
        self.skipped = 0
        return self.queue

    @property
    def total(self) -> int:
        return len(self.queue)

    @property
    def finished(self) -> bool:
        return self.index >= len(self.queue)

    def current_card_id(self) -> str | None:
        while not self.finished:
            card_id = self.queue[self.index]
            if self.db.get_card(card_id) is None:
                self.index += 1
                continue
            return card_id
        return None

    def advance(self, counted_as_updated: bool) -> None:
        if counted_as_updated:
            self.updated += 1
        self.index += 1

    def skip(self) -> None:
        self.skipped += 1
        self.index += 1

    def grade_for(self, analysis: dict) -> str | None:
        """正式复习：按固定区间把匹配分数映射成 FSRS 评分。"""
        return grade_from_score(analysis["match_score"])

    def preview(self, card_id: str, grade: str, analysis: dict, attempt_id: str, when: int | None = None):
        """只读预览：确认后复习计划会变成什么样。"""
        record = self.db.get_scheduling(card_id)
        if record is None:
            raise ValueError("卡片调度状态缺失，无法预览复习计划")
        return plan_formal_review(record, grade, analysis["match_score"], attempt_id, when)

    def confirm(self, attempt_id: str, grade: str, when: int | None = None) -> dict:
        """用户点击确认后，才真正写入 FSRS 调度数据。"""
        return commit_formal_review(self.db, attempt_id, grade, when)
