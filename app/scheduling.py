"""基于成熟 FSRS 实现（py-fsrs）的调度层。

commit_formal_review 是整个程序里唯一会更新 scheduling 表的函数：
- 只接受「算法复习」模式下、AI 分析成功且通过校验的作答记录；
- 自由练习记录会被直接拒绝；
- 评分必须与 grading.py 的固定区间一致；
- 用 attempt_id 做幂等保护，重复点击不会重复写入。
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 让随程序分发的 FSRS 库可以被导入（无论从 .pyw 还是测试脚本启动）
_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

from fsrs import Card, Rating, Scheduler, State

from app.grading import grade_from_score
from app.storage import now_ms

# FSRS 参数集中配置：目标保持率 90%，最长间隔 100 年，开启少量随机抖动。
SCHEDULER = Scheduler(
    desired_retention=0.9,
    maximum_interval=36500,
    enable_fuzzing=True,
    learning_steps=(timedelta(minutes=1), timedelta(minutes=10)),
    relearning_steps=(timedelta(minutes=10),),
)

RATING_BY_GRADE = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}

STATE_NAME_BY_ENUM = {
    State.Learning: "learning",
    State.Review: "review",
    State.Relearning: "relearning",
}
STATE_ENUM_BY_NAME = {name: state for state, name in STATE_NAME_BY_ENUM.items()}

STATE_LABELS = {
    "learning": "学习中",
    "review": "复习中",
    "relearning": "重新学习",
}


def to_datetime(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc)


def to_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def card_from_record(record: dict) -> Card:
    return Card(
        card_id=int(record["fsrs_id"]),
        state=STATE_ENUM_BY_NAME.get(record["state"], State.Learning),
        step=record["step"],
        stability=record["stability"],
        difficulty=record["difficulty"],
        due=to_datetime(record["due"]),
        last_review=to_datetime(record["last_review"]) if record["last_review"] else None,
    )


def plan_formal_review(record: dict, grade: str, match_score: int, attempt_id: str, when: int | None = None) -> tuple[dict, dict]:
    """纯函数：算出新的调度状态与复习日志。预览与真正写入都调用它，保证界面上展示的下次复习时间与实际写入一致。"""
    timestamp = now_ms() if when is None else int(when)
    if grade not in RATING_BY_GRADE:
        raise ValueError(f"未知的评分：{grade!r}")

    reviewed_card, _ = SCHEDULER.review_card(
        card_from_record(record),
        RATING_BY_GRADE[grade],
        review_datetime=to_datetime(timestamp),
    )

    next_record = {
        "card_id": record["card_id"],
        "fsrs_id": record["fsrs_id"],
        "due": to_ms(reviewed_card.due),
        "state": STATE_NAME_BY_ENUM.get(reviewed_card.state, record["state"]),
        "step": reviewed_card.step,
        "stability": reviewed_card.stability,
        "difficulty": reviewed_card.difficulty,
        "lapses": int(record["lapses"]) + (1 if grade == "again" else 0),
        "formal_reps": int(record["formal_reps"]) + 1,
        "last_review": to_ms(reviewed_card.last_review) if reviewed_card.last_review else None,
        "last_formal_review_at": timestamp,
        "updated_at": timestamp,
    }

    log_record = {
        "id": f"{record['card_id']}:{attempt_id}",
        "card_id": record["card_id"],
        "attempt_id": attempt_id,
        "rating": grade,
        "match_score": int(match_score),
        "state_before": record["state"],
        "due_after": next_record["due"],
        "stability": next_record["stability"],
        "difficulty": next_record["difficulty"],
        "reviewed_at": timestamp,
    }
    return next_record, log_record


def commit_formal_review(db, attempt_id: str, grade: str, when: int | None = None) -> dict:
    """确认一次正式复习并写入 FSRS。这是 scheduling 表唯一的更新入口。"""
    timestamp = now_ms() if when is None else int(when)

    attempt = db.get_attempt(attempt_id)
    if attempt is None:
        raise ValueError("作答记录不存在，无法更新复习计划")
    if attempt["mode"] != "review":
        raise ValueError("自由练习不允许写入 FSRS 调度状态")
    analysis = db.attempt_analysis(attempt)
    if analysis is None:
        raise ValueError("本次作答没有有效的匹配分析结果，不能推进复习计划")

    expected_grade = grade_from_score(analysis["match_score"])
    if expected_grade != grade:
        raise ValueError(
            f"评分与固定映射规则不一致：匹配分数 {analysis['match_score']} 对应 {expected_grade}"
        )

    record = db.get_scheduling(attempt["card_id"])
    if record is None:
        raise ValueError("卡片调度状态缺失，无法更新复习计划")
    if attempt["grade_applied"]:
        return {"applied": False, "reason": "already_applied", "scheduling": record}

    next_record, log_record = plan_formal_review(record, grade, analysis["match_score"], attempt_id, timestamp)

    with db.conn:
        db.conn.execute(
            """
            UPDATE scheduling
            SET due = ?, state = ?, step = ?, stability = ?, difficulty = ?,
                lapses = ?, formal_reps = ?, last_review = ?, last_formal_review_at = ?, updated_at = ?
            WHERE card_id = ?
            """,
            (
                next_record["due"],
                next_record["state"],
                next_record["step"],
                next_record["stability"],
                next_record["difficulty"],
                next_record["lapses"],
                next_record["formal_reps"],
                next_record["last_review"],
                next_record["last_formal_review_at"],
                next_record["updated_at"],
                next_record["card_id"],
            ),
        )
        db.conn.execute(
            """
            INSERT INTO review_logs(id, card_id, attempt_id, rating, match_score, state_before,
                                    due_after, stability, difficulty, reviewed_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_record["id"],
                log_record["card_id"],
                log_record["attempt_id"],
                log_record["rating"],
                log_record["match_score"],
                log_record["state_before"],
                log_record["due_after"],
                log_record["stability"],
                log_record["difficulty"],
                log_record["reviewed_at"],
            ),
        )
        cursor = db.conn.execute(
            """
            UPDATE attempts
            SET grade = ?, grade_applied = 1, review_log_id = ?, applied_at = ?
            WHERE id = ? AND grade_applied = 0
            """,
            (grade, log_record["id"], timestamp, attempt_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("这次复习已经被写入过，未重复更新")

    return {"applied": True, "scheduling": db.get_scheduling(attempt["card_id"]), "review_log": log_record}


def retrievability(record: dict, when: int | None = None) -> float | None:
    """记忆保持率，仅用于界面展示，不参与任何写入。"""
    if record is None or not record.get("last_review"):
        return None
    timestamp = now_ms() if when is None else int(when)
    try:
        return float(SCHEDULER.get_card_retrievability(card_from_record(record), to_datetime(timestamp)))
    except Exception:
        return None
