"""归一化不同模型供应商返回的 Prompt 缓存用量。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


CACHE_STATUS_PENDING = "pending"
CACHE_STATUS_HIT = "hit"
CACHE_STATUS_MISS = "miss"
CACHE_STATUS_NOT_REPORTED = "not_reported"
CACHE_STATUS_ERROR = "error"


@dataclass(frozen=True, slots=True)
class LLMCacheUsage:
    """单次模型调用的缓存结果。"""

    status: str
    hit: bool | None
    hit_tokens: int
    write_tokens: int
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _MetricPath:
    kind: str
    parts: tuple[str, ...]

    @property
    def source(self) -> str:
        return ".".join(self.parts)


_METRIC_PATHS = (
    # OpenAI Chat Completions、Responses、New-API 与 OpenRouter。
    _MetricPath("read", ("usage", "prompt_tokens_details", "cached_tokens")),
    _MetricPath("read", ("usage", "input_tokens_details", "cached_tokens")),
    # Anthropic Messages。
    _MetricPath("read", ("usage", "cache_read_input_tokens")),
    _MetricPath("write", ("usage", "cache_creation_input_tokens")),
    # DeepSeek。
    _MetricPath("read", ("usage", "prompt_cache_hit_tokens")),
    _MetricPath("miss", ("usage", "prompt_cache_miss_tokens")),
    # Gemini REST 与 SDK 的两种字段风格。
    _MetricPath(
        "read",
        ("usage_metadata", "cached_content_token_count"),
    ),
    _MetricPath(
        "read",
        ("usageMetadata", "cachedContentTokenCount"),
    ),
    # KT 及兼容网关的归一化/别名字段。
    _MetricPath("read", ("usage", "cached_tokens")),
    _MetricPath("read", ("usage", "tokens_cached")),
    _MetricPath("read", ("usage", "total_cached_tokens")),
    _MetricPath("write", ("usage", "cache_write_tokens")),
)


def _value_at_path(value: Any, parts: tuple[str, ...]) -> tuple[bool, Any]:
    current = value
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _token_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def normalize_llm_cache_usage(
    response: Any,
    *,
    successful: bool,
) -> LLMCacheUsage:
    """从供应商响应中提取单次调用的缓存命中事实。

    仅接受响应里的非负整数指标。多个兼容字段同时出现时取最大值，避免
    网关同时返回原始字段和归一化别名后发生重复累计。
    """

    if not successful:
        return LLMCacheUsage(
            status=CACHE_STATUS_ERROR,
            hit=None,
            hit_tokens=0,
            write_tokens=0,
            details={},
        )

    reported: list[dict[str, Any]] = []
    values_by_kind: dict[str, list[int]] = {
        "read": [],
        "write": [],
        "miss": [],
    }

    def collect(payload: Any, *, prefix: str = "") -> bool:
        before = len(reported)
        for metric_path in _METRIC_PATHS:
            present, raw_value = _value_at_path(payload, metric_path.parts)
            if not present:
                continue
            count = _token_count(raw_value)
            if count is None:
                continue
            values_by_kind[metric_path.kind].append(count)
            reported.append({
                "kind": metric_path.kind,
                "source": f"{prefix}{metric_path.source}",
                "count": count,
            })
        return len(reported) > before

    collect(response)
    # 流式聚合通常把 usage 提升到响应根部；兼容只在采样 chunk 中返回
    # usageMetadata 等供应商原生字段的出口。
    if not reported and isinstance(response, Mapping):
        chunks = response.get("chunks_sample")
        if isinstance(chunks, list):
            for index in range(len(chunks) - 1, -1, -1):
                if collect(chunks[index], prefix=f"chunks_sample[{index}]."):
                    break

    if not reported:
        return LLMCacheUsage(
            status=CACHE_STATUS_NOT_REPORTED,
            hit=None,
            hit_tokens=0,
            write_tokens=0,
            details={},
        )

    hit_tokens = max(values_by_kind["read"], default=0)
    write_tokens = max(values_by_kind["write"], default=0)
    cache_hit = hit_tokens > 0
    return LLMCacheUsage(
        status=CACHE_STATUS_HIT if cache_hit else CACHE_STATUS_MISS,
        hit=cache_hit,
        hit_tokens=hit_tokens,
        write_tokens=write_tokens,
        details={"reported_metrics": reported},
    )


__all__ = [
    "CACHE_STATUS_ERROR",
    "CACHE_STATUS_HIT",
    "CACHE_STATUS_MISS",
    "CACHE_STATUS_NOT_REPORTED",
    "CACHE_STATUS_PENDING",
    "LLMCacheUsage",
    "normalize_llm_cache_usage",
]
