"""业务缓存——按 (group, window, instructions, latest_log_id) 缓存报告。"""

import hashlib
import time
import logging

logger = logging.getLogger("nanobot.tool.group_analysis.cache")

# key: (group_id, window_hours, instructions_hash, latest_log_id, raw_count) → (timestamp, report)
_REPORT_CACHE: dict[tuple, tuple[float, str]] = {}
_CACHE_TTL = 600  # 10 分钟


def _make_key(group_id: str, window_hours: int, instructions: str, latest_log_id: int | None, raw_count: int) -> tuple:
    h = hashlib.md5((instructions or "").encode()).hexdigest()[:8]
    return (group_id, window_hours or 0, h, latest_log_id or 0, raw_count)


def get_cached_report(group_id: str, window_hours: int, instructions: str, latest_log_id: int | None, raw_count: int) -> str | None:
    key = _make_key(group_id, window_hours, instructions, latest_log_id, raw_count)
    entry = _REPORT_CACHE.get(key)
    if not entry:
        return None
    ts, report = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _REPORT_CACHE[key]
        return None
    logger.info("[group_analysis.cache] HIT %s", key)
    return report


def set_cached_report(group_id: str, window_hours: int, instructions: str, latest_log_id: int | None, raw_count: int, report: str) -> None:
    key = _make_key(group_id, window_hours, instructions, latest_log_id, raw_count)
    _REPORT_CACHE[key] = (time.monotonic(), report)


# ── 向后兼容：保留旧 API ──

_LAST_GROUP_ANALYSIS_REPORT: tuple[float, str] = (0.0, "")


def remember_group_analysis_report(report: str) -> None:
    global _LAST_GROUP_ANALYSIS_REPORT
    if report and "group-analysis-report" in report:
        _LAST_GROUP_ANALYSIS_REPORT = (time.monotonic(), report)


def get_recent_group_analysis_report(max_age_seconds: float = 300.0) -> str:
    created_at, report = _LAST_GROUP_ANALYSIS_REPORT
    if not report:
        return ""
    if time.monotonic() - created_at > max_age_seconds:
        return ""
    return report
