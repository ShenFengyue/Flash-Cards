"""自由练习：任何卡片随时可以练，随机抽卡、重复练同一张都可以。

自由练习不要求卡片已经到期，也不会改变 FSRS 的任何调度数据：
不写下次到期时间、稳定性、难度、正式复习次数，也不写入正式复习日志与复习队列。
本模块不导入 app.scheduling，代码层面就没有更新调度数据的路径。
"""

from app.session_base import PracticeSession


class FreePracticeSession(PracticeSession):
    mode = "free"

    def grade_for(self, analysis: dict) -> str | None:
        # 自由练习不做 FSRS 评分，AI 结果只用于展示对比。
        return None

    def start(self, card: dict, answer: str, when: int | None = None) -> dict:
        attempt = super().start(card, answer, when)
        # 结构性保证：自由练习产生的记录永远不带评分、也永远不会被标记为已写入。
        assert attempt["grade"] is None and not attempt["grade_applied"]
        return attempt
