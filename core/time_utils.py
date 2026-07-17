"""项目内 DateTime 字段使用的时间辅助函数。"""

from __future__ import annotations

from datetime import datetime, timezone


def db_now_naive() -> datetime:
    """返回适合写入当前 SQLite ORM DateTime 字段的本地 naive 时间。"""
    return datetime.now(timezone.utc).astimezone().replace(tzinfo=None)


def to_db_naive(value: datetime | None) -> datetime | None:
    """将可选 datetime 归一化为 ORM DateTime 字段使用的本地 naive 时间。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def db_naive_to_utc(value: datetime | None) -> datetime | None:
    """把数据库本地 naive 墙钟解释为服务器本地时间并转换为 UTC。"""

    if value is None:
        return None
    return value.astimezone(timezone.utc)


def db_datetime_to_utc_iso(value: datetime | None) -> str:
    """将数据库时间输出为明确的 UTC ISO-8601（Z）字符串。"""

    converted = db_naive_to_utc(value)
    if converted is None:
        return ""
    return converted.isoformat().replace("+00:00", "Z")
