"""AI 日报工具的公开参数、时间窗口与缓存身份契约。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo


AiDailyFreshness = Literal["today", "latest", "week", "custom"]

AI_DAILY_TIMEZONE = ZoneInfo("Asia/Shanghai")
AI_DAILY_FRESHNESS_VALUES = ("today", "latest", "week", "custom")
AI_DAILY_MAX_RESULTS_DEFAULT = 8
AI_DAILY_MAX_RESULTS_LIMIT = 50
AI_DAILY_CACHE_VERSION = "ai_daily_v3_20260713"
AI_DAILY_SERVER_BOUND_FIELDS = frozenset(
    {
        "mode",
        "timezone",
        "now",
        "pipeline_version",
        "output_format",
        "user_id",
        "session_id",
    }
)

_DAILY_QUERY_MARKERS = (
    "日报",
    "早报",
    "简报",
    "每日",
    "今日",
    "今天",
    "新闻",
    "资讯",
    "AI 新闻",
    "AI新闻",
    "最新新闻",
    "最新资讯",
)
_PUBLIC_FIELDS = frozenset(
    {"query", "max_results", "freshness", "target_date", "no_cache", "refresh"}
)


class AiDailyRequestError(ValueError):
    """AI 日报公开参数无法通过服务端输入契约。"""


@dataclass(frozen=True)
class AiDailyRequest:
    query: str
    max_results: int
    freshness: AiDailyFreshness
    target_date: str | None
    no_cache: bool
    refresh: bool
    window_start: datetime
    window_end: datetime
    reference_time: datetime

    @property
    def bypass_cache(self) -> bool:
        return self.no_cache or self.refresh

    @property
    def cache_date(self) -> str:
        if self.target_date is not None:
            return self.target_date
        return self.reference_time.astimezone(AI_DAILY_TIMEZONE).date().isoformat()

    @property
    def max_age_hours(self) -> int:
        hours = (self.window_end - self.window_start).total_seconds() / 3600
        return max(1, math.ceil(hours))

    @property
    def window_start_naive(self) -> datetime:
        return self.window_start.astimezone(AI_DAILY_TIMEZONE).replace(tzinfo=None)

    @property
    def window_end_naive(self) -> datetime:
        return self.window_end.astimezone(AI_DAILY_TIMEZONE).replace(tzinfo=None)

    @property
    def reference_time_naive(self) -> datetime:
        return self.reference_time.astimezone(AI_DAILY_TIMEZONE).replace(tzinfo=None)


def ai_daily_parameters_schema() -> dict[str, Any]:
    """返回模型可见参数 schema；不包含任何服务端字段。"""
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1000,
                "description": (
                    "日报主题或自然语言请求；今天/最新类请求必须基于 "
                    "runtime_context.current_time，不要自行编造年份。"
                ),
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": AI_DAILY_MAX_RESULTS_LIMIT,
                "description": (
                    "候选新闻数量（默认 8）；日报/最新资讯类请求会至少使用 8 条候选。"
                ),
                "default": AI_DAILY_MAX_RESULTS_DEFAULT,
            },
            "freshness": {
                "type": "string",
                "description": (
                    "时效范围：today/latest/week/custom。今天、最新、日报、早报优先使用 "
                    "today 或 latest。"
                ),
                "enum": list(AI_DAILY_FRESHNESS_VALUES),
                "default": "latest",
            },
            "target_date": {
                "type": "string",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
                "description": "目标日期，YYYY-MM-DD；freshness=custom 时必填。",
            },
            "no_cache": {
                "type": "boolean",
                "description": "跳过缓存读取并强制重新检索",
                "default": False,
            },
            "refresh": {
                "type": "boolean",
                "description": "强制刷新；只影响缓存读取，不改变检索时间窗口",
                "default": False,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(AI_DAILY_TIMEZONE)
    if now.tzinfo is None:
        return now.replace(tzinfo=AI_DAILY_TIMEZONE)
    return now.astimezone(AI_DAILY_TIMEZONE)


def _parse_target_date(raw: Any) -> date:
    value = str(raw or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise AiDailyRequestError("target_date must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise AiDailyRequestError("target_date is not a valid calendar date") from exc
    if parsed.isoformat() != value:
        raise AiDailyRequestError("target_date must use canonical YYYY-MM-DD")
    return parsed


def _parse_bool(args: dict[str, Any], name: str) -> bool:
    value = args.get(name, False)
    if not isinstance(value, bool):
        raise AiDailyRequestError(f"{name} must be a boolean")
    return value


def _parse_max_results(args: dict[str, Any]) -> int:
    value = args.get("max_results", AI_DAILY_MAX_RESULTS_DEFAULT)
    if isinstance(value, bool) or not isinstance(value, int):
        raise AiDailyRequestError("max_results must be an integer")
    if not 1 <= value <= AI_DAILY_MAX_RESULTS_LIMIT:
        raise AiDailyRequestError(
            f"max_results must be between 1 and {AI_DAILY_MAX_RESULTS_LIMIT}"
        )
    return value


def parse_ai_daily_request(
    args: dict[str, Any],
    *,
    now: datetime | None = None,
) -> AiDailyRequest:
    """严格解析模型参数，并生成显式北京时间半开时间窗口。"""
    if not isinstance(args, dict):
        raise AiDailyRequestError("arguments must be an object")
    unknown = sorted(set(args) - _PUBLIC_FIELDS)
    if unknown:
        raise AiDailyRequestError(f"unsupported arguments: {', '.join(unknown)}")

    raw_query = args.get("query")
    if not isinstance(raw_query, str):
        raise AiDailyRequestError("query must be a string")
    query = re.sub(r"\s+", " ", raw_query).strip()
    if not query:
        raise AiDailyRequestError("query must not be empty")
    if len(query) > 1000:
        raise AiDailyRequestError("query is too long")

    freshness_value = args.get("freshness", "latest")
    if not isinstance(freshness_value, str):
        raise AiDailyRequestError("freshness must be a string")
    freshness = freshness_value.strip().lower()
    if freshness not in AI_DAILY_FRESHNESS_VALUES:
        raise AiDailyRequestError("freshness is not supported")

    local_now = _normalize_now(now)
    raw_target_date = args.get("target_date")
    target: date | None = None
    if freshness == "custom":
        if raw_target_date is None or not str(raw_target_date).strip():
            raise AiDailyRequestError("target_date is required for custom freshness")
        target = _parse_target_date(raw_target_date)
    elif raw_target_date is not None and str(raw_target_date).strip():
        raise AiDailyRequestError("target_date is only allowed for custom freshness")

    if freshness == "today":
        window_start = datetime.combine(local_now.date(), time.min, AI_DAILY_TIMEZONE)
        window_end = local_now
        reference_time = local_now
    elif freshness == "latest":
        window_end = local_now
        window_start = window_end - timedelta(hours=72)
        reference_time = local_now
    elif freshness == "week":
        window_end = local_now
        window_start = window_end - timedelta(days=7)
        reference_time = local_now
    else:
        assert target is not None
        window_start = datetime.combine(target, time.min, AI_DAILY_TIMEZONE)
        window_end = window_start + timedelta(days=1)
        reference_time = window_end - timedelta(microseconds=1)

    max_results = _parse_max_results(args)
    if freshness in {"today", "latest"} or any(
        marker in query for marker in _DAILY_QUERY_MARKERS
    ):
        max_results = max(max_results, AI_DAILY_MAX_RESULTS_DEFAULT)

    return AiDailyRequest(
        query=query,
        max_results=max_results,
        freshness=freshness,  # type: ignore[arg-type]
        target_date=target.isoformat() if target is not None else None,
        no_cache=_parse_bool(args, "no_cache"),
        refresh=_parse_bool(args, "refresh"),
        window_start=window_start,
        window_end=window_end,
        reference_time=reference_time,
    )
