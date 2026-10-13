"""匹配分数 → FSRS 评分的固定映射，集中配置在这一处。"""

from typing import NamedTuple


class GradeBand(NamedTuple):
    min_score: int
    max_score: int
    grade: str


GRADE_BANDS: tuple[GradeBand, ...] = (
    GradeBand(0, 39, "again"),
    GradeBand(40, 64, "hard"),
    GradeBand(65, 84, "good"),
    GradeBand(85, 100, "easy"),
)

GRADE_LABELS = {
    "again": "重来（Again）",
    "hard": "困难（Hard）",
    "good": "良好（Good）",
    "easy": "简单（Easy）",
}


def grade_from_score(score) -> str:
    """把 0-100 的匹配分数映射为 FSRS 评分；非法分数直接报错，不做任何猜测。"""
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError(f"匹配分数必须是数字，收到：{score!r}")
    value = float(score)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"匹配分数必须是有限数字，收到：{score!r}")
    if value < 0 or value > 100:
        raise ValueError(f"匹配分数必须在 0-100 之间，收到：{score!r}")
    rounded = int(round(value))
    for band in GRADE_BANDS:
        if band.min_score <= rounded <= band.max_score:
            return band.grade
    raise ValueError(f"没有匹配到评分区间，收到：{score!r}")


def grade_label(grade: str) -> str:
    return GRADE_LABELS.get(grade, grade)


def describe_bands() -> str:
    return "　|　".join(f"{band.min_score}-{band.max_score} → {GRADE_LABELS[band.grade]}" for band in GRADE_BANDS)
