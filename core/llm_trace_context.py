"""LLM 请求链路上下文。

通过 ContextVar 向深层调用传递 trace_id、run_id、业务 source 和 Agent
执行阶段。业务 source 用于说明“谁发起调用”，phase 用于区分工具轮次、
最终动作、回复合同重试和真实模型路由重试。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

llm_trace_id: ContextVar[str] = ContextVar("llm_trace_id", default="")
llm_run_id: ContextVar[str] = ContextVar("llm_run_id", default="")
llm_source: ContextVar[str] = ContextVar("llm_source", default="")
llm_phase: ContextVar[str] = ContextVar("llm_phase", default="")
llm_route_attempt_index: ContextVar[int] = ContextVar(
    "llm_route_attempt_index",
    default=0,
)
llm_cache_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "llm_cache_context",
    default=None,
)

_CACHE_CONTEXT_KEYS = (
    "prefix_epoch",
    "prefix_epoch_generation",
    "prefix_epoch_covered_until",
    "prefix_epoch_low_water_tokens",
    "prefix_epoch_high_water_tokens",
)

_PREFIX_CACHE_MANIFEST_FIELDS = {
    "prefix_cache_key": "cache_key",
    "prefix_cache_manifest_sha256": "sha256",
    "stable_prefix_sha256": "stable_prefix_sha256",
    "stable_prefix_message_count": "stable_message_count",
    "stable_prefix_token_estimate": "stable_prefix_token_estimate",
    "tool_schema_sha256": "tool_schema_sha256",
    "canonical_order_sha256": "canonical_order_sha256",
}


def get_llm_trace_vars() -> tuple[str, str, str]:
    """返回当前上下文的 (trace_id, run_id, source)。"""
    return llm_trace_id.get(), llm_run_id.get(), llm_source.get()


def get_llm_trace_execution_vars() -> tuple[str, int]:
    """返回当前上下文的 (phase, route_attempt_index)。"""

    return llm_phase.get(), llm_route_attempt_index.get()


def get_llm_cache_context() -> dict[str, Any]:
    """返回当前请求的无正文 Prompt Cache epoch 上下文副本。"""

    return dict(llm_cache_context.get() or {})


def build_llm_cache_context(
    session_id: str,
    context_debug: Any,
    *,
    drop_none: bool = False,
) -> dict[str, Any]:
    """从请求调试信息提取允许进入链路追踪的缓存字段。"""

    debug = context_debug if isinstance(context_debug, dict) else {}
    return {
        "session_id": session_id,
        **{
            key: debug[key]
            for key in _CACHE_CONTEXT_KEYS
            if key in debug and (not drop_none or debug[key] is not None)
        },
    }


def attach_prompt_prefix_cache_context(
    cache_context: dict[str, Any],
    manifest: Any,
) -> dict[str, Any]:
    """把已签名的 Prefix Cache Manifest 收窄为链路上下文。"""

    from core.prompt_v2.prefix_cache import (
        validate_prompt_prefix_cache_manifest,
    )

    validate_prompt_prefix_cache_manifest(manifest)
    result = dict(cache_context or {})
    for target, source in _PREFIX_CACHE_MANIFEST_FIELDS.items():
        result[target] = manifest[source]
    return result


@contextmanager
def llm_trace_scope(
    *,
    trace_id: str = "",
    run_id: str = "",
    source: str = "",
    phase: str = "",
    route_attempt_index: int | None = None,
    cache_context: dict[str, Any] | None = None,
):
    """LLM 请求链路上下文管理器。

    内层 source/phase 覆盖外层，trace_id/run_id 默认继承外层；
    route_attempt_index 未提供时继承外层。
    支持嵌套使用。
    """
    prev_t = llm_trace_id.get()
    prev_r = llm_run_id.get()
    prev_s = llm_source.get()
    prev_p = llm_phase.get()
    prev_a = llm_route_attempt_index.get()
    prev_c = llm_cache_context.get()
    tok_t = llm_trace_id.set(trace_id or prev_t)
    tok_r = llm_run_id.set(run_id or prev_r)
    tok_s = llm_source.set(source or prev_s)
    tok_p = llm_phase.set(phase or prev_p)
    tok_a = llm_route_attempt_index.set(
        prev_a if route_attempt_index is None else max(0, int(route_attempt_index))
    )
    tok_c = llm_cache_context.set(
        dict(prev_c or {}) if cache_context is None else dict(cache_context)
    )
    try:
        yield
    finally:
        llm_cache_context.reset(tok_c)
        llm_route_attempt_index.reset(tok_a)
        llm_phase.reset(tok_p)
        llm_source.reset(tok_s)
        llm_run_id.reset(tok_r)
        llm_trace_id.reset(tok_t)
