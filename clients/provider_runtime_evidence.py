"""从脱敏 LLM Trace 汇总 Provider 运行证据的持久化适配器。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from core.db.models.observability import LLMApiRequestLog
from core.model_provider.contracts import ProviderCapability
from core.model_provider.provider_config import (
    BUILTIN_PROVIDER_DEFINITIONS,
    canonical_provider_instance_id,
)
from core.time_utils import db_now_naive


_SUCCESS_STATUSES = frozenset({"success", "stream_success"})
_FAILURE_STATUSES = frozenset({"failed", "error", "stream_error"})
_CAPABILITY_TRACE_LIMIT = 2_000


def _safe_json(value: object) -> object:
    try:
        return json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _contains_key_with_value(value: object, keys: frozenset[str]) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in keys and item not in (None, "", 0, [], {}):
                return True
            if _contains_key_with_value(item, keys):
                return True
    elif isinstance(value, list):
        return any(_contains_key_with_value(item, keys) for item in value)
    return False


def _contains_image_input(value: object) -> bool:
    if isinstance(value, Mapping):
        if any(key in value for key in ("image_url", "input_image")):
            return True
        item_type = str(value.get("type") or "").strip().lower()
        if item_type in {"image", "image_url", "input_image"}:
            return True
        return any(_contains_image_input(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_image_input(item) for item in value)
    return False


def _has_sent_tools(request_json: object, sent_tools_json: object) -> bool:
    sent_tools = _safe_json(sent_tools_json)
    if isinstance(sent_tools, list) and bool(sent_tools):
        return True
    request = _safe_json(request_json)
    return (
        isinstance(request, Mapping)
        and isinstance(request.get("tools"), list)
        and bool(request["tools"])
    )


def _provider_key(
    value: object,
    accepted: frozenset[str],
    raw_owner: Mapping[str, str],
) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw in raw_owner:
        return raw_owner[raw]
    canonical = canonical_provider_instance_id(raw)
    return canonical if canonical in accepted else ""


def _empty_evidence(window_days: int) -> dict[str, Any]:
    return {
        "window_days": window_days,
        "requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "incomplete_requests": 0,
        "success_rate": None,
        "avg_first_token_latency_ms": 0,
        "avg_total_latency_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "cache_write_tokens": 0,
        "cache_input_tokens": 0,
        "cache_denominator_unknown_requests": 0,
        "cache_hit_token_ratio": None,
        "cost_microusd": 0,
        "by_error_category": {},
        "observed_capabilities": [],
        "capability_evidence": {},
        "last_observed_at": None,
    }


def summarize_provider_runtime_evidence(
    db: Session,
    provider_ids: Iterable[str],
    *,
    window_days: int = 30,
    aliases_by_provider: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """按 Provider 汇总近期运行指标与成功请求中的能力证据。

    未观测到某项能力不代表不支持，因此这里只输出正向证据。请求与响应正文
    仅在进程内用于判定结构特征，不会进入返回值。
    """

    normalized_window = max(1, min(int(window_days), 365))
    accepted = frozenset(
        canonical_provider_instance_id(provider_id)
        for provider_id in provider_ids
        if str(provider_id or "").strip()
    )
    evidence = {
        provider_id: _empty_evidence(normalized_window)
        for provider_id in sorted(accepted)
    }
    if not accepted:
        return evidence
    raw_owner = {provider_id: provider_id for provider_id in accepted}
    for definition in BUILTIN_PROVIDER_DEFINITIONS.values():
        if definition.id in accepted:
            for alias in definition.aliases:
                raw_owner[alias] = definition.id
    for raw_provider_id, aliases in dict(aliases_by_provider or {}).items():
        provider_id = canonical_provider_instance_id(raw_provider_id)
        if provider_id not in accepted:
            continue
        for alias in aliases:
            raw_alias = str(alias or "").strip()
            if raw_alias:
                raw_owner[raw_alias] = provider_id

    cutoff = db_now_naive() - timedelta(days=normalized_window)
    cache_hit_expr = func.coalesce(LLMApiRequestLog.cache_hit_tokens, 0)
    cache_miss_expr = func.coalesce(LLMApiRequestLog.cache_miss_tokens, 0)
    cache_observed_expr = LLMApiRequestLog.cache_status.in_(("hit", "miss"))
    # 新记录优先使用明确分母；迁移前的旧记录没有 cache_input_tokens，但
    # 若同时保存了 miss_tokens，则 hit + miss 仍是可审计的兼容分母。
    effective_cache_input_expr = case(
        (
            cache_observed_expr & (LLMApiRequestLog.cache_input_tokens > 0),
            LLMApiRequestLog.cache_input_tokens,
        ),
        (
            cache_observed_expr
            & LLMApiRequestLog.cache_input_tokens.is_(None)
            & (cache_miss_expr > 0),
            cache_hit_expr + cache_miss_expr,
        ),
        else_=0,
    )
    effective_cache_hit_expr = case(
        (
            cache_observed_expr & (LLMApiRequestLog.cache_input_tokens > 0),
            cache_hit_expr,
        ),
        (
            cache_observed_expr
            & LLMApiRequestLog.cache_input_tokens.is_(None)
            & (cache_miss_expr > 0),
            cache_hit_expr,
        ),
        else_=0,
    )
    cache_unknown_expr = case(
        (
            cache_observed_expr
            & LLMApiRequestLog.cache_input_tokens.is_(None)
            & (cache_miss_expr <= 0),
            1,
        ),
        else_=0,
    )
    rows = (
        db.query(
            LLMApiRequestLog.provider,
            func.count(LLMApiRequestLog.id),
            func.sum(case((LLMApiRequestLog.status.in_(_SUCCESS_STATUSES), 1), else_=0)),
            func.sum(case((LLMApiRequestLog.status.in_(_FAILURE_STATUSES), 1), else_=0)),
            func.avg(func.nullif(LLMApiRequestLog.first_token_latency_ms, 0)),
            func.avg(func.nullif(LLMApiRequestLog.latency_ms, 0)),
            func.sum(case((LLMApiRequestLog.first_token_latency_ms > 0, 1), else_=0)),
            func.sum(case((LLMApiRequestLog.latency_ms > 0, 1), else_=0)),
            func.sum(LLMApiRequestLog.input_tokens),
            func.sum(LLMApiRequestLog.output_tokens),
            func.sum(LLMApiRequestLog.cache_hit_tokens),
            func.sum(LLMApiRequestLog.cache_miss_tokens),
            func.sum(LLMApiRequestLog.cache_write_tokens),
            func.sum(effective_cache_input_expr),
            func.sum(cache_unknown_expr),
            func.sum(effective_cache_hit_expr),
            func.sum(LLMApiRequestLog.cost_microusd),
            func.max(LLMApiRequestLog.created_at),
        )
        .filter(
            LLMApiRequestLog.created_at >= cutoff,
            LLMApiRequestLog.provider.in_(tuple(sorted(raw_owner))),
        )
        .group_by(LLMApiRequestLog.provider)
        .all()
    )
    latency_weights: dict[str, dict[str, int]] = {
        provider_id: {"first": 0, "total": 0}
        for provider_id in accepted
    }
    latency_sums: dict[str, dict[str, float]] = {
        provider_id: {"first": 0.0, "total": 0.0}
        for provider_id in accepted
    }
    for row in rows:
        provider_id = _provider_key(row[0], accepted, raw_owner)
        if not provider_id:
            continue
        item = evidence[provider_id]
        requests = int(row[1] or 0)
        first_latency = float(row[4] or 0)
        total_latency = float(row[5] or 0)
        first_count = int(row[6] or 0)
        total_count = int(row[7] or 0)
        item["requests"] += requests
        item["successful_requests"] += int(row[2] or 0)
        item["failed_requests"] += int(row[3] or 0)
        item["input_tokens"] += int(row[8] or 0)
        item["output_tokens"] += int(row[9] or 0)
        item["cache_hit_tokens"] += int(row[10] or 0)
        item["cache_miss_tokens"] += int(row[11] or 0)
        item["cache_write_tokens"] += int(row[12] or 0)
        item["cache_input_tokens"] += int(row[13] or 0)
        item["cache_denominator_unknown_requests"] += int(row[14] or 0)
        item.setdefault("_cache_ratio_hit_tokens", 0)
        item["_cache_ratio_hit_tokens"] += int(row[15] or 0)
        item["cost_microusd"] += int(row[16] or 0)
        latency_weights[provider_id]["first"] += first_count
        latency_weights[provider_id]["total"] += total_count
        latency_sums[provider_id]["first"] += first_latency * first_count
        latency_sums[provider_id]["total"] += total_latency * total_count
        observed_at = row[17]
        current_at = item["last_observed_at"]
        if observed_at is not None and (current_at is None or observed_at > current_at):
            item["last_observed_at"] = observed_at

    error_rows = (
        db.query(
            LLMApiRequestLog.provider,
            LLMApiRequestLog.error_category,
            func.count(LLMApiRequestLog.id),
        )
        .filter(
            LLMApiRequestLog.created_at >= cutoff,
            LLMApiRequestLog.provider.in_(tuple(sorted(raw_owner))),
        )
        .group_by(
            LLMApiRequestLog.provider,
            LLMApiRequestLog.error_category,
        )
        .all()
    )
    for raw_provider, category, count in error_rows:
        provider_id = _provider_key(raw_provider, accepted, raw_owner)
        if not provider_id:
            continue
        category_key = str(category or "none")
        by_category = evidence[provider_id]["by_error_category"]
        by_category[category_key] = by_category.get(category_key, 0) + int(count or 0)

    capability_rows = (
        db.query(
            LLMApiRequestLog.provider,
            LLMApiRequestLog.status,
            LLMApiRequestLog.request_json,
            LLMApiRequestLog.response_json,
            LLMApiRequestLog.actual_sent_tools_json,
            LLMApiRequestLog.cache_status,
            LLMApiRequestLog.cache_hit_tokens,
            LLMApiRequestLog.cache_miss_tokens,
            LLMApiRequestLog.cache_write_tokens,
            LLMApiRequestLog.created_at,
        )
        .filter(
            LLMApiRequestLog.created_at >= cutoff,
            LLMApiRequestLog.status.in_(_SUCCESS_STATUSES),
            LLMApiRequestLog.provider.in_(tuple(sorted(raw_owner))),
        )
        .order_by(LLMApiRequestLog.created_at.desc())
        .limit(_CAPABILITY_TRACE_LIMIT)
        .all()
    )
    capability_observed_at: dict[str, dict[str, object]] = {
        provider_id: {} for provider_id in accepted
    }
    for row in capability_rows:
        provider_id = _provider_key(row[0], accepted, raw_owner)
        if not provider_id:
            continue
        observed: set[ProviderCapability] = {
            ProviderCapability.CHAT_COMPLETION,
        }
        if row[1] == "stream_success":
            observed.add(ProviderCapability.STREAMING)
        if _has_sent_tools(row[2], row[4]):
            observed.add(ProviderCapability.TOOL_CALLING)
        request = _safe_json(row[2])
        if _contains_image_input(request):
            observed.add(ProviderCapability.VISION)
        response = _safe_json(row[3])
        if _contains_key_with_value(
            response,
            frozenset({"reasoning_content", "reasoning_tokens"}),
        ):
            observed.add(ProviderCapability.REASONING_CONTENT)
        if (
            str(row[5] or "") in {"hit", "miss"}
            or any(int(value or 0) > 0 for value in row[6:9])
        ):
            observed.add(ProviderCapability.CACHE_USAGE)
        for capability in observed:
            capability_observed_at[provider_id].setdefault(
                capability.value,
                row[9],
            )

    for provider_id, item in evidence.items():
        requests = int(item["requests"])
        successful = int(item["successful_requests"])
        failed = int(item["failed_requests"])
        item["incomplete_requests"] = max(0, requests - successful - failed)
        item["success_rate"] = (
            round(successful / requests, 6) if requests else None
        )
        hit_tokens = int(item.get("_cache_ratio_hit_tokens", 0))
        cache_input_tokens = int(item["cache_input_tokens"])
        item["cache_hit_token_ratio"] = (
            round(hit_tokens / cache_input_tokens, 6)
            if cache_input_tokens else None
        )
        for latency_key, result_key in (
            ("first", "avg_first_token_latency_ms"),
            ("total", "avg_total_latency_ms"),
        ):
            weight = latency_weights[provider_id][latency_key]
            item[result_key] = (
                int(latency_sums[provider_id][latency_key] / weight)
                if weight else 0
            )
        observed_at = capability_observed_at[provider_id]
        item["observed_capabilities"] = sorted(observed_at)
        item["capability_evidence"] = {
            capability: {
                "source": "successful_llm_trace",
                "last_observed_at": (
                    value.isoformat() if hasattr(value, "isoformat") else None
                ),
            }
            for capability, value in sorted(observed_at.items())
        }
        last_observed_at = item["last_observed_at"]
        item["last_observed_at"] = (
            last_observed_at.isoformat()
            if hasattr(last_observed_at, "isoformat") else None
        )
        item.pop("_cache_ratio_hit_tokens", None)
    return evidence


__all__ = ["summarize_provider_runtime_evidence"]
