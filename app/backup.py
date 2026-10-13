"""JSON 导入导出与格式校验。校验不通过时绝不改动本地数据。"""

import json

from app.storage import SCHEMA_VERSION, SEEDED_KEY, SCHEMA_VERSION_KEY, now_ms

BACKUP_APP_ID = "social-intuition-flashcard"
MAX_ISSUES = 20
STATES = ("learning", "review", "relearning")


def build_backup(db) -> dict:
    cards = db.list_cards()
    scheduling = db.list_scheduling()
    review_logs = [dict(row) for row in db.conn.execute("SELECT * FROM review_logs").fetchall()]
    attempts = db.list_attempts()
    return {
        "app": BACKUP_APP_ID,
        "schema_version": int(db.get_meta(SCHEMA_VERSION_KEY, SCHEMA_VERSION) or SCHEMA_VERSION),
        "exported_at": now_ms(),
        "counts": {
            "cards": len(cards),
            "scheduling": len(scheduling),
            "review_logs": len(review_logs),
            "attempts": len(attempts),
        },
        "cards": cards,
        "scheduling": scheduling,
        "review_logs": review_logs,
        "attempts": attempts,
    }


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_backup(raw) -> tuple[dict | None, list[str]]:
    """返回 (数据, 问题列表)。有问题时数据为 None。"""
    issues: list[str] = []

    def push(message: str) -> None:
        if len(issues) < MAX_ISSUES:
            issues.append(message)

    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as error:
            return None, [f"不是合法的 JSON：{error}"]

    if not isinstance(raw, dict):
        return None, ["导入内容必须是一个 JSON 对象"]

    cards_raw = raw.get("cards")
    scheduling_raw = raw.get("scheduling")
    review_logs_raw = raw.get("review_logs", [])
    attempts_raw = raw.get("attempts", [])

    if not isinstance(cards_raw, list):
        push("缺少 cards 数组")
    if not isinstance(scheduling_raw, list):
        push("缺少 scheduling 数组")
    if not isinstance(review_logs_raw, list):
        push("review_logs 必须是数组")
    if not isinstance(attempts_raw, list):
        push("attempts 必须是数组")
    if issues:
        return None, issues

    cards: list[dict] = []
    card_ids: set[str] = set()
    for index, item in enumerate(cards_raw):
        if not isinstance(item, dict):
            push(f"cards[{index}] 必须是对象")
            continue
        card_id = item.get("id")
        if not _is_text(card_id):
            push(f"cards[{index}].id 缺失或不是字符串")
            continue
        if card_id in card_ids:
            push(f"cards[{index}].id 重复：{card_id}")
            continue
        if not _is_text(item.get("front")):
            push(f"cards[{index}].front 不能为空")
        if not _is_text(item.get("back")):
            push(f"cards[{index}].back 不能为空")
        card_ids.add(card_id)
        cards.append(
            {
                "id": card_id,
                "front": str(item.get("front") or ""),
                "back": str(item.get("back") or ""),
                "created_at": int(item["created_at"]) if _is_int(item.get("created_at")) else now_ms(),
                "updated_at": int(item["updated_at"]) if _is_int(item.get("updated_at")) else now_ms(),
            }
        )

    scheduling: list[dict] = []
    seen_scheduling: set[str] = set()
    for index, item in enumerate(scheduling_raw):
        if not isinstance(item, dict):
            push(f"scheduling[{index}] 必须是对象")
            continue
        card_id = item.get("card_id")
        if not _is_text(card_id):
            push(f"scheduling[{index}].card_id 缺失")
            continue
        if card_id not in card_ids:
            push(f"scheduling[{index}].card_id 找不到对应的闪卡：{card_id}")
            continue
        if card_id in seen_scheduling:
            push(f"scheduling[{index}] 同一张卡出现多条调度记录：{card_id}")
            continue
        if not _is_int(item.get("due")):
            push(f"scheduling[{index}].due 必须是时间戳整数")
            continue
        if item.get("state") not in STATES:
            push(f"scheduling[{index}].state 必须是 {' / '.join(STATES)} 之一")
            continue
        seen_scheduling.add(card_id)
        scheduling.append(
            {
                "card_id": card_id,
                "fsrs_id": int(item["fsrs_id"]) if _is_int(item.get("fsrs_id")) else 0,
                "due": int(item["due"]),
                "state": item["state"],
                "step": int(item["step"]) if _is_int(item.get("step")) else None,
                "stability": float(item["stability"]) if isinstance(item.get("stability"), (int, float)) else None,
                "difficulty": float(item["difficulty"]) if isinstance(item.get("difficulty"), (int, float)) else None,
                "lapses": int(item["lapses"]) if _is_int(item.get("lapses")) else 0,
                "formal_reps": int(item["formal_reps"]) if _is_int(item.get("formal_reps")) else 0,
                "last_review": int(item["last_review"]) if _is_int(item.get("last_review")) else None,
                "last_formal_review_at": int(item["last_formal_review_at"])
                if _is_int(item.get("last_formal_review_at"))
                else None,
                "updated_at": int(item["updated_at"]) if _is_int(item.get("updated_at")) else now_ms(),
            }
        )

    review_logs: list[dict] = []
    for index, item in enumerate(review_logs_raw):
        if not isinstance(item, dict) or not _is_text(item.get("id")) or not _is_text(item.get("card_id")):
            push(f"review_logs[{index}] 缺少 id 或 card_id")
            continue
        if item["card_id"] not in card_ids:
            push(f"review_logs[{index}].card_id 找不到对应的闪卡：{item['card_id']}")
            continue
        review_logs.append(
            {
                "id": item["id"],
                "card_id": item["card_id"],
                "attempt_id": str(item.get("attempt_id") or ""),
                "rating": str(item.get("rating") or "good"),
                "match_score": int(item["match_score"]) if _is_int(item.get("match_score")) else 0,
                "state_before": str(item.get("state_before") or "learning"),
                "due_after": int(item["due_after"]) if _is_int(item.get("due_after")) else 0,
                "stability": float(item["stability"]) if isinstance(item.get("stability"), (int, float)) else None,
                "difficulty": float(item["difficulty"]) if isinstance(item.get("difficulty"), (int, float)) else None,
                "reviewed_at": int(item["reviewed_at"]) if _is_int(item.get("reviewed_at")) else now_ms(),
            }
        )

    attempts: list[dict] = []
    for index, item in enumerate(attempts_raw):
        if not isinstance(item, dict) or not _is_text(item.get("id")) or not _is_text(item.get("card_id")):
            push(f"attempts[{index}] 缺少 id 或 card_id")
            continue
        if item["card_id"] not in card_ids:
            push(f"attempts[{index}].card_id 找不到对应的闪卡：{item['card_id']}")
            continue
        if item.get("mode") not in ("free", "review"):
            push(f"attempts[{index}].mode 必须是 'free' 或 'review'")
            continue
        mode = item["mode"]
        grade = item.get("grade")
        grade_applied = bool(item.get("grade_applied")) and mode == "review"
        attempts.append(
            {
                "id": item["id"],
                "card_id": item["card_id"],
                "mode": mode,
                "user_answer": str(item.get("user_answer") or ""),
                "front": str(item.get("front") or ""),
                "back": str(item.get("back") or ""),
                "created_at": int(item["created_at"]) if _is_int(item.get("created_at")) else now_ms(),
                "analysis_json": item.get("analysis_json"),
                "analysis_error": item.get("analysis_error"),
                "analysis_model": item.get("analysis_model"),
                "analyzed_at": int(item["analyzed_at"]) if _is_int(item.get("analyzed_at")) else None,
                "grade": grade if mode == "review" else None,
                "grade_applied": 1 if grade_applied else 0,
                "review_log_id": item.get("review_log_id"),
                "applied_at": int(item["applied_at"]) if _is_int(item.get("applied_at")) else None,
            }
        )

    if issues:
        return None, issues

    data = {
        "app": raw.get("app") if isinstance(raw.get("app"), str) else BACKUP_APP_ID,
        "schema_version": int(raw["schema_version"]) if _is_int(raw.get("schema_version")) else SCHEMA_VERSION,
        "exported_at": int(raw["exported_at"]) if _is_int(raw.get("exported_at")) else now_ms(),
        "counts": {
            "cards": len(cards),
            "scheduling": len(scheduling),
            "review_logs": len(review_logs),
            "attempts": len(attempts),
        },
        "cards": cards,
        "scheduling": scheduling,
        "review_logs": review_logs,
        "attempts": attempts,
    }
    return data, []


def summarize_backup(data: dict) -> str:
    counts = data.get("counts") or {}
    exported = data.get("exported_at")
    text = (
        f"卡片 {counts.get('cards', 0)} 张、调度状态 {counts.get('scheduling', 0)} 条、"
        f"正式复习日志 {counts.get('review_logs', 0)} 条、作答记录 {counts.get('attempts', 0)} 条"
    )
    if exported:
        from app.formatting import format_datetime

        text += f"，导出时间 {format_datetime(exported)}"
    return text


def apply_backup(db, data: dict, mode: str, when: int | None = None) -> dict:
    """写入导入数据。调用方必须已经取得用户对 合并 / 覆盖 的明确确认。"""
    if mode not in ("merge", "replace"):
        raise ValueError(f"未知的导入方式：{mode!r}")
    timestamp = now_ms() if when is None else int(when)

    with db.conn:
        if mode == "replace":
            db.conn.execute("DELETE FROM attempts")
            db.conn.execute("DELETE FROM review_logs")
            db.conn.execute("DELETE FROM scheduling")
            db.conn.execute("DELETE FROM cards")

        for card in data["cards"]:
            db.conn.execute(
                """
                INSERT INTO cards(id, front, back, created_at, updated_at) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET front = excluded.front, back = excluded.back,
                                              updated_at = excluded.updated_at
                """,
                (card["id"], card["front"], card["back"], card["created_at"], card["updated_at"]),
            )

        for record in data["scheduling"]:
            existing = db.get_scheduling(record["card_id"])
            if mode == "merge" and existing is not None:
                current_mark = existing["last_formal_review_at"] or existing["updated_at"]
                imported_mark = record["last_formal_review_at"] or record["updated_at"]
                if imported_mark < current_mark:
                    continue
            fsrs_id = record["fsrs_id"] or _rowid_of(db, record["card_id"])
            db.conn.execute(
                """
                INSERT INTO scheduling(card_id, fsrs_id, due, state, step, stability, difficulty,
                                       lapses, formal_reps, last_review, last_formal_review_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_id) DO UPDATE SET
                    fsrs_id = excluded.fsrs_id, due = excluded.due, state = excluded.state, step = excluded.step,
                    stability = excluded.stability, difficulty = excluded.difficulty, lapses = excluded.lapses,
                    formal_reps = excluded.formal_reps, last_review = excluded.last_review,
                    last_formal_review_at = excluded.last_formal_review_at, updated_at = excluded.updated_at
                """,
                (
                    record["card_id"],
                    fsrs_id,
                    record["due"],
                    record["state"],
                    record["step"],
                    record["stability"],
                    record["difficulty"],
                    record["lapses"],
                    record["formal_reps"],
                    record["last_review"],
                    record["last_formal_review_at"],
                    record["updated_at"],
                ),
            )

        for record in data["review_logs"]:
            db.conn.execute(
                """
                INSERT INTO review_logs(id, card_id, attempt_id, rating, match_score, state_before,
                                        due_after, stability, difficulty, reviewed_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET rating = excluded.rating, match_score = excluded.match_score,
                                              due_after = excluded.due_after
                """,
                (
                    record["id"],
                    record["card_id"],
                    record["attempt_id"],
                    record["rating"],
                    record["match_score"],
                    record["state_before"],
                    record["due_after"],
                    record["stability"],
                    record["difficulty"],
                    record["reviewed_at"],
                ),
            )

        for record in data["attempts"]:
            db.conn.execute(
                """
                INSERT INTO attempts(id, card_id, mode, user_answer, front, back, created_at,
                                     analysis_json, analysis_error, analysis_model, analyzed_at,
                                     grade, grade_applied, review_log_id, applied_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET analysis_json = excluded.analysis_json,
                                              analysis_error = excluded.analysis_error,
                                              grade = excluded.grade, grade_applied = excluded.grade_applied
                """,
                (
                    record["id"],
                    record["card_id"],
                    record["mode"],
                    record["user_answer"],
                    record["front"],
                    record["back"],
                    record["created_at"],
                    record["analysis_json"],
                    record["analysis_error"],
                    record["analysis_model"],
                    record["analyzed_at"],
                    record["grade"],
                    record["grade_applied"],
                    record["review_log_id"],
                    record["applied_at"],
                ),
            )

        # 缺少调度记录的卡片补一条初始状态，保证每张卡都有调度数据。
        for card in data["cards"]:
            if db.get_scheduling(card["id"]) is None:
                db._insert_initial_scheduling(card["id"], _rowid_of(db, card["id"]), timestamp)

        db.set_meta(SEEDED_KEY, True)
        db.set_meta(SCHEMA_VERSION_KEY, data.get("schema_version") or SCHEMA_VERSION)

    return {"mode": mode, **db.counts()}


def _rowid_of(db, card_id: str) -> int:
    row = db.conn.execute("SELECT rowid FROM cards WHERE id = ?", (card_id,)).fetchone()
    return int(row[0]) if row else 0
