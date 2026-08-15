"""只记录哈希和尺寸的 LLM Prompt Cache 形状诊断。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


HISTORY_SAMPLE_BYTES = 4096
_TRACE_ONLY_OPTION_KEYS = frozenset({
    "metadata",
    "request_id",
    "run_id",
    "trace_id",
})


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: Any) -> str:
    return _sha256_bytes(_stable_json(value).encode("utf-8", errors="replace"))


def _prompt_sha256(value: Any) -> str:
    """与 canonical Prompt Runtime 的 stable_json 编码保持一致。"""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode("utf-8", errors="replace")
    return _sha256_bytes(encoded)


def _message_role(message: Any) -> str:
    if not isinstance(message, Mapping):
        return ""
    return str(message.get("role") or "").strip().lower()


def _split_prompt_messages(messages: list[Any]) -> tuple[list[Any], list[Any]]:
    """拆出稳定前置 system 与 canonical history 段。

    Nanobot canonical Prompt 把历史放在前置 system 和请求级 persona/runtime
    system 之间。普通两消息任务没有第二段 system，此时最后一条视为当前事件，
    不误记成历史。
    """

    leading_end = 0
    while leading_end < len(messages) and _message_role(messages[leading_end]) == "system":
        leading_end += 1
    leading_system = messages[:leading_end]

    history_end = leading_end
    while history_end < len(messages) and _message_role(messages[history_end]) != "system":
        history_end += 1
    if history_end < len(messages):
        history = messages[leading_end:history_end]
    else:
        trailing = messages[leading_end:]
        history = trailing[:-1] if len(trailing) > 1 else []
    return leading_system, history


def _sample_hashes(value: Any) -> tuple[str, str, int]:
    encoded = _stable_json(value).encode("utf-8", errors="replace")
    return (
        _sha256_bytes(encoded[:HISTORY_SAMPLE_BYTES]),
        _sha256_bytes(encoded[-HISTORY_SAMPLE_BYTES:]),
        len(encoded),
    )


def build_llm_cache_shape(
    request: Any,
    *,
    cache_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """生成不含 Prompt 正文、会话 ID 或密钥的缓存形状。"""

    payload = dict(request) if isinstance(request, Mapping) else {}
    raw_messages = payload.get("messages")
    messages = list(raw_messages) if isinstance(raw_messages, (list, tuple)) else []
    raw_tools = payload.get("tools")
    tools = list(raw_tools) if isinstance(raw_tools, (list, tuple)) else []
    context = dict(cache_context or {})
    leading_system, history = _split_prompt_messages(messages)
    explicit_history_start = context.get("history_message_start_index")
    explicit_history_end = context.get("history_message_end_index")
    if (
        type(explicit_history_start) is int
        and type(explicit_history_end) is int
        and 0 <= explicit_history_start <= explicit_history_end <= len(messages)
    ):
        history = messages[explicit_history_start:explicit_history_end]
        history_source = "manifest"
    else:
        history_source = "derived"
    explicit_prefix_count = context.get("stable_prefix_message_count")
    if (
        type(explicit_prefix_count) is int
        and 0 < explicit_prefix_count <= len(messages)
    ):
        stable_prefix = messages[:explicit_prefix_count]
        stable_prefix_source = "manifest"
    else:
        stable_prefix = leading_system
        stable_prefix_source = "derived"
    history_head, history_tail, history_bytes = _sample_hashes(history)

    options = {
        key: value
        for key, value in payload.items()
        if key not in {"messages", "tools", "input"}
        and key not in _TRACE_ONLY_OPTION_KEYS
    }
    leading_system_sha256 = _sha256(leading_system)
    stable_prefix_sha256 = _prompt_sha256(stable_prefix)
    tools_sha256 = _prompt_sha256(tools)
    explicit_epoch = str(context.get("prefix_epoch") or "").strip()[:96]
    derived_epoch = _sha256({
        "stable_prefix": stable_prefix_sha256,
        "tools": tools_sha256,
        "history_head": history_head,
    })[:24]
    scope_key = str(
        context.get("session_id")
        or context.get("scope_key")
        or ""
    )

    result: dict[str, Any] = {
        "schema_version": 2,
        "prefix_epoch": explicit_epoch or derived_epoch,
        "prefix_epoch_source": "runtime" if explicit_epoch else "derived",
        "scope_sha256": _sha256(scope_key) if scope_key else "",
        "leading_system_sha256": leading_system_sha256,
        "leading_system_messages": len(leading_system),
        "stable_prefix_sha256": stable_prefix_sha256,
        "stable_prefix_messages": len(stable_prefix),
        "stable_prefix_source": stable_prefix_source,
        "stable_prefix_contract_match": (
            not context.get("stable_prefix_sha256")
            or str(context.get("stable_prefix_sha256")) == stable_prefix_sha256
        ),
        "prefix_cache_key": str(context.get("prefix_cache_key") or "")[:64],
        "prefix_cache_manifest_sha256": str(
            context.get("prefix_cache_manifest_sha256") or ""
        )[:64],
        "canonical_order_sha256": str(
            context.get("canonical_order_sha256") or ""
        )[:64],
        "tools_sha256": tools_sha256,
        "tool_schema_contract_match": (
            not context.get("tool_schema_sha256")
            or str(context.get("tool_schema_sha256")) == tools_sha256
        ),
        "tool_count": len(tools),
        "history_sha256": _sha256(history),
        "history_source": history_source,
        "history_head_sha256": history_head,
        "history_tail_sha256": history_tail,
        "history_messages": len(history),
        "history_bytes": history_bytes,
        "request_options_sha256": _sha256(options),
    }
    for key in (
        "prefix_epoch_generation",
        "prefix_epoch_covered_until",
        "prefix_epoch_low_water_tokens",
        "prefix_epoch_high_water_tokens",
    ):
        value = context.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    return result


def infer_cache_miss_reason(
    current: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
) -> str:
    """按最先断裂的稳定前缀组成部分给出保守诊断。"""

    if not previous:
        return "cold_start"
    comparisons = (
        ("prefix_cache_key", "prefix_cache_key_changed"),
        ("prefix_epoch", "prefix_epoch_changed"),
        ("stable_prefix_sha256", "stable_prefix_changed"),
        ("tools_sha256", "tools_changed"),
        ("history_head_sha256", "history_head_changed"),
        ("request_options_sha256", "request_options_changed"),
    )
    for key, reason in comparisons:
        if str(current.get(key) or "") != str(previous.get(key) or ""):
            return reason
    return "upstream_or_cache_eviction"


__all__ = [
    "HISTORY_SAMPLE_BYTES",
    "build_llm_cache_shape",
    "infer_cache_miss_reason",
]
