"""时间与文案格式化。"""

from datetime import datetime

DAY_MS = 24 * 60 * 60 * 1000


def format_datetime(timestamp_ms) -> str:
    if not timestamp_ms:
        return "—"
    moment = datetime.fromtimestamp(int(timestamp_ms) / 1000)
    return moment.strftime("%Y-%m-%d %H:%M")


def format_date(timestamp_ms) -> str:
    if not timestamp_ms:
        return "—"
    return datetime.fromtimestamp(int(timestamp_ms) / 1000).strftime("%Y-%m-%d")


def describe_due(due_ms: int, now: int) -> str:
    diff = int(due_ms) - int(now)
    if diff <= 0:
        return "现在到期"
    if diff < DAY_MS:
        minutes = max(1, round(diff / 60000))
        if minutes < 60:
            return f"{minutes} 分钟后"
        hours = max(1, round(diff / 3600000))
        return f"{hours} 小时后"
    days = round(diff / DAY_MS)
    if days == 1:
        return "明天"
    if days <= 30:
        return f"{days} 天后"
    return format_date(due_ms)


