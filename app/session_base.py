"""两种练习模式共用的流程骨架：记录作答、贴回分析结果、记录失败原因。

本模块不导入 app.scheduling，因此这里没有任何更新 FSRS 调度数据的路径。
"""


class PracticeSession:
    """练习会话基类。子类通过 mode 区分自由练习（free）与算法复习（review）。"""

    mode = "review"

    def __init__(self, db):
        self.db = db

    def start(self, card: dict, answer: str, when: int | None = None) -> dict:
        """保存本次作答文本（此时还没有 AI 结果）。"""
        return self.db.record_attempt(card, self.mode, answer, when)

    def attach_result(self, attempt_id: str, outcome: dict, when: int | None = None) -> dict:
        """保存 AI 返回、并已通过校验的分析结果。"""
        analysis = outcome["analysis"]
        return self.db.attach_analysis(attempt_id, analysis, outcome.get("model") or "unknown", when)

    def attach_error(self, attempt_id: str, message: str) -> dict:
        """记录失败原因：保留用户的回答，允许重试。"""
        return self.db.attach_analysis_error(attempt_id, message)

    def grade_for(self, analysis: dict) -> str | None:
        """把匹配分数换算成 FSRS 评分；自由练习模式恒为 None。"""
        return None
