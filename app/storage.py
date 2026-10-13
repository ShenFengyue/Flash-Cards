"""SQLite 数据层：闪卡、FSRS 调度状态、正式复习日志、作答记录、元数据。

数据分表存放，隔离是结构性的：
- 闪卡内容只在 cards 表；
- FSRS 调度状态在 scheduling 表，创建时写入初始状态，之后只有
  app.scheduling.commit_formal_review 会更新它；
- 作答记录在 attempts 表，用 mode 字段区分 'free'（自由练习）与 'review'（正式复习）。
"""

import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA_VERSION = 1
SEEDED_KEY = "seeded"
SCHEMA_VERSION_KEY = "schema_version"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS scheduling (
    card_id TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
    fsrs_id INTEGER NOT NULL,
    due INTEGER NOT NULL,
    state TEXT NOT NULL,
    step INTEGER,
    stability REAL,
    difficulty REAL,
    lapses INTEGER NOT NULL DEFAULT 0,
    formal_reps INTEGER NOT NULL DEFAULT 0,
    last_review INTEGER,
    last_formal_review_at INTEGER,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS review_logs (
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    attempt_id TEXT NOT NULL UNIQUE,
    rating TEXT NOT NULL,
    match_score INTEGER NOT NULL,
    state_before TEXT,
    due_after INTEGER NOT NULL,
    stability REAL,
    difficulty REAL,
    reviewed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN ('free', 'review')),
    user_answer TEXT NOT NULL,
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    analysis_json TEXT,
    analysis_error TEXT,
    analysis_model TEXT,
    analyzed_at INTEGER,
    grade TEXT,
    grade_applied INTEGER NOT NULL DEFAULT 0,
    review_log_id TEXT,
    applied_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_attempts_card ON attempts(card_id);
CREATE INDEX IF NOT EXISTS idx_attempts_mode ON attempts(mode);
CREATE INDEX IF NOT EXISTS idx_review_logs_card ON review_logs(card_id);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return str(uuid.uuid4())


def _row_to_dict(row) -> dict | None:
    return dict(row) if row is not None else None


class Database:
    """一个连接的轻量封装。所有操作都在主线程调用，避免 sqlite 跨线程问题。"""

    def __init__(self, path):
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        # 万一同时开了两个窗口，让写入等待而不是立刻失败
        self.conn.execute("PRAGMA busy_timeout = 10000")

    def initialize(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA)
        if self.get_meta(SCHEMA_VERSION_KEY) is None:
            self.set_meta(SCHEMA_VERSION_KEY, SCHEMA_VERSION)

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # ---------- meta ----------
    def get_meta(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else row["value"]

    def set_meta(self, key: str, value) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    # ---------- 闪卡内容 ----------
    def create_card(self, front: str, back: str, when: int | None = None) -> dict:
        front = (front or "").strip()
        back = (back or "").strip()
        if not front:
            raise ValueError("卡片正面不能为空")
        if not back:
            raise ValueError("标准回复不能为空")
        timestamp = now_ms() if when is None else int(when)
        card_id = new_id()
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO cards(id, front, back, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
                (card_id, front, back, timestamp, timestamp),
            )
            fsrs_id = int(cursor.lastrowid)
            self._insert_initial_scheduling(card_id, fsrs_id, timestamp)
        return self.get_card(card_id)

    def update_card(self, card_id: str, front: str, back: str, when: int | None = None) -> dict:
        front = (front or "").strip()
        back = (back or "").strip()
        if not front:
            raise ValueError("卡片正面不能为空")
        if not back:
            raise ValueError("标准回复不能为空")
        if self.get_card(card_id) is None:
            raise ValueError("卡片不存在或已被删除")
        timestamp = now_ms() if when is None else int(when)
        with self.conn:
            # 只更新内容：绝不触碰 scheduling，复习历史与下次复习时间保持不变。
            self.conn.execute(
                "UPDATE cards SET front = ?, back = ?, updated_at = ? WHERE id = ?",
                (front, back, timestamp, card_id),
            )
        return self.get_card(card_id)

    def delete_card(self, card_id: str) -> dict:
        counts = self.count_related(card_id)
        with self.conn:
            self.conn.execute("DELETE FROM attempts WHERE card_id = ?", (card_id,))
            self.conn.execute("DELETE FROM review_logs WHERE card_id = ?", (card_id,))
            self.conn.execute("DELETE FROM scheduling WHERE card_id = ?", (card_id,))
            self.conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        return counts

    def count_related(self, card_id: str) -> dict:
        def count(sql: str) -> int:
            return int(self.conn.execute(sql, (card_id,)).fetchone()[0])

        return {
            "scheduling": count("SELECT COUNT(*) FROM scheduling WHERE card_id = ?"),
            "review_logs": count("SELECT COUNT(*) FROM review_logs WHERE card_id = ?"),
            "attempts": count("SELECT COUNT(*) FROM attempts WHERE card_id = ?"),
        }

    def get_card(self, card_id: str) -> dict | None:
        return _row_to_dict(self.conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone())

    def list_cards(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM cards ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def list_cards_with_schedule(self, when: int | None = None) -> list[dict]:
        timestamp = now_ms() if when is None else int(when)
        rows = self.conn.execute(
            """
            SELECT cards.*, scheduling.due AS due, scheduling.state AS state,
                   scheduling.stability AS stability, scheduling.difficulty AS difficulty,
                   scheduling.formal_reps AS formal_reps, scheduling.lapses AS lapses,
                   scheduling.last_formal_review_at AS last_formal_review_at
            FROM cards JOIN scheduling ON scheduling.card_id = cards.id
            ORDER BY cards.created_at DESC
            """
        ).fetchall()
        cards = []
        for row in rows:
            item = dict(row)
            item["status"] = (
                "new" if not item["formal_reps"] else ("due" if item["due"] <= timestamp else "scheduled")
            )
            cards.append(item)
        return cards

    # ---------- 调度状态：创建初始值 + 只读 ----------
    def _insert_initial_scheduling(self, card_id: str, fsrs_id: int, when: int) -> None:
        """新建卡片时的初始 FSRS 状态（学习阶段第一步，到期时间就是现在）。这不是一次复习。"""
        self.conn.execute(
            """
            INSERT INTO scheduling(card_id, fsrs_id, due, state, step, stability, difficulty,
                                   lapses, formal_reps, last_review, last_formal_review_at, updated_at)
            VALUES(?, ?, ?, 'learning', 0, NULL, NULL, 0, 0, NULL, NULL, ?)
            """,
            (card_id, int(fsrs_id), int(when), int(when)),
        )

    def get_scheduling(self, card_id: str) -> dict | None:
        return _row_to_dict(self.conn.execute("SELECT * FROM scheduling WHERE card_id = ?", (card_id,)).fetchone())

    def list_scheduling(self) -> list[dict]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM scheduling").fetchall()]

    def list_due_card_ids(self, when: int | None = None) -> list[str]:
        timestamp = now_ms() if when is None else int(when)
        rows = self.conn.execute(
            """
            SELECT scheduling.card_id AS card_id, scheduling.due AS due, scheduling.formal_reps AS formal_reps
            FROM scheduling JOIN cards ON cards.id = scheduling.card_id
            WHERE scheduling.due <= ?
            ORDER BY (scheduling.formal_reps = 0) DESC,
                     (CASE WHEN scheduling.formal_reps = 0 THEN 0 ELSE scheduling.due END) ASC,
                     cards.created_at DESC
            """,
            (timestamp,),
        ).fetchall()
        return [row["card_id"] for row in rows]

    # ---------- 作答记录 ----------
    def record_attempt(self, card: dict, mode: str, user_answer: str, when: int | None = None) -> dict:
        answer = (user_answer or "").strip()
        if not answer:
            raise ValueError("回答不能为空")
        if mode not in ("free", "review"):
            raise ValueError(f"未知的练习模式：{mode!r}")
        attempt_id = new_id()
        timestamp = now_ms() if when is None else int(when)
        with self.conn:
            # 只写 attempts：自由练习（mode='free'）在这里不会碰到任何调度数据。
            self.conn.execute(
                """
                INSERT INTO attempts(id, card_id, mode, user_answer, front, back, created_at,
                                     analysis_json, analysis_error, analysis_model, analyzed_at,
                                     grade, grade_applied, review_log_id, applied_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, 0, NULL, NULL)
                """,
                (attempt_id, card["id"], mode, answer, card["front"], card["back"], timestamp),
            )
        return self.get_attempt(attempt_id)

    def get_attempt(self, attempt_id: str) -> dict | None:
        return _row_to_dict(self.conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone())

    def list_attempts(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM attempts ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def list_attempts_for_card(self, card_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM attempts WHERE card_id = ? ORDER BY created_at DESC", (card_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def count_attempts(self, mode: str | None = None) -> int:
        if mode is None:
            return int(self.conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])
        return int(self.conn.execute("SELECT COUNT(*) FROM attempts WHERE mode = ?", (mode,)).fetchone()[0])

    def attach_analysis(self, attempt_id: str, analysis: dict, model: str, when: int | None = None) -> dict:
        attempt = self.get_attempt(attempt_id)
        if attempt is None:
            raise ValueError("作答记录不存在")
        timestamp = now_ms() if when is None else int(when)
        with self.conn:
            self.conn.execute(
                """
                UPDATE attempts
                SET analysis_json = ?, analysis_error = NULL, analysis_model = ?, analyzed_at = ?
                WHERE id = ?
                """,
                (json.dumps(analysis, ensure_ascii=False), model, timestamp, attempt_id),
            )
        return self.get_attempt(attempt_id)

    def attach_analysis_error(self, attempt_id: str, message: str) -> dict:
        if self.get_attempt(attempt_id) is None:
            raise ValueError("作答记录不存在")
        with self.conn:
            self.conn.execute(
                "UPDATE attempts SET analysis_json = NULL, analysis_error = ?, analyzed_at = NULL WHERE id = ?",
                (message, attempt_id),
            )
        return self.get_attempt(attempt_id)

    def attempt_analysis(self, attempt: dict) -> dict | None:
        raw = attempt.get("analysis_json")
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    # ---------- 初始化数据 ----------
    def seed_if_needed(self, seeds, when: int | None = None) -> int:
        """只在首次初始化（没有标记且库里没有卡片）时写入 5 张初始闪卡。"""
        if self.get_meta(SEEDED_KEY) is not None:
            return 0
        inserted = 0
        if int(self.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]) == 0:
            base = now_ms() if when is None else int(when)
            for index, seed in enumerate(seeds):
                # 依次写入，让第 1 张卡时间戳最大，列表默认按创建时间倒序时顺序自然。
                self.create_card(seed["front"], seed["back"], base - index * 1000)
                inserted += 1
        self.set_meta(SEEDED_KEY, True)
        return inserted

    def counts(self) -> dict:
        def count(table: str) -> int:
            return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        return {
            "cards": count("cards"),
            "scheduling": count("scheduling"),
            "review_logs": count("review_logs"),
            "attempts": count("attempts"),
        }


CardFilter = str  # 'all' | 'due' | 'new' | 'learned'
CardSort = str  # 'created_desc' | 'created_asc' | 'due_asc' | 'reps_desc'


def query_cards(cards: list[dict], search: str = "", card_filter: str = "all", sort: str = "created_desc") -> list[dict]:
    """搜索 + 筛选 + 排序（纯函数，方便直接测试）。"""
    keyword = (search or "").strip().lower()
    result = list(cards)
    if keyword:
        result = [
            card
            for card in result
            if keyword in card["front"].lower() or keyword in card["back"].lower()
        ]
    if card_filter == "due":
        result = [card for card in result if card["status"] != "scheduled"]
    elif card_filter == "new":
        result = [card for card in result if card["status"] == "new"]
    elif card_filter == "learned":
        result = [card for card in result if card["formal_reps"] > 0]

    if sort == "created_asc":
        result.sort(key=lambda card: card["created_at"])
    elif sort == "due_asc":
        result.sort(key=lambda card: card["due"])
    elif sort == "reps_desc":
        result.sort(key=lambda card: -card["formal_reps"])
    else:
        result.sort(key=lambda card: -card["created_at"])
    return result


def stats_of(cards: list[dict]) -> dict:
    due = sum(1 for card in cards if card["status"] != "scheduled")
    new_cards = sum(1 for card in cards if card["status"] == "new")
    return {
        "total": len(cards),
        "due": due,
        "new": new_cards,
        "scheduled": len(cards) - due,
    }
