"""请求级工具执行控制状态。

该模块只维护稳定的错误信封、调用指纹和失败抑制状态，不依赖 KT。具体的
执行拦截由 ``nanobot_kt.tool_runtime`` 适配。
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping


FINAL_ACTION_TOOLS = frozenset({"reply", "no_reply"})
_FAMILY_BLOCKING_ERROR_CODES = frozenset(
    {
        "authorization_failed",
        "sandbox_not_enabled",
        "asset_not_authorized",
    }
)


def tool_family(tool_name: str) -> str:
    """把需要共享失败抑制策略的工具归入同一族。"""

    name = str(tool_name or "").strip()
    if name.startswith("workspace_"):
        return "workspace"
    if name.startswith("asset_"):
        return "asset"
    if name.startswith("sandbox_"):
        return "sandbox_process"
    return name


def tool_call_fingerprint(tool_name: str, args: Any) -> str:
    """返回同名、同参数工具调用的稳定指纹。"""

    payload = {
        "tool_name": str(tool_name or "").strip(),
        "args": args,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolFailureEnvelope:
    """从工具稳定结果信封中提取的循环控制字段。"""

    code: str
    summary: str
    retryable: bool
    stop: bool


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _structured_result(value: Any) -> Mapping[str, Any] | None:
    metadata = _mapping(getattr(value, "metadata", None))
    if metadata is not None:
        structured = _mapping(metadata.get("structured_content"))
        if structured is not None:
            return structured

    candidates = (
        getattr(value, "output", value),
        getattr(value, "error", None),
    )
    for candidate in candidates:
        if isinstance(candidate, str):
            try:
                parsed = json.loads(candidate)
            except (TypeError, ValueError):
                continue
            structured = _mapping(parsed)
        else:
            structured = _mapping(candidate)
        if structured is not None:
            return structured
    return None


def extract_tool_failure(value: Any) -> ToolFailureEnvelope | None:
    """仅识别明确声明 ``status=error`` 的稳定结果信封。"""

    result = _structured_result(value)
    if result is None or result.get("status") != "error":
        return None
    error = _mapping(result.get("error"))
    if error is None:
        return None
    retryable = error.get("retryable")
    stop = error.get("stop")
    if not isinstance(retryable, bool) or not isinstance(stop, bool):
        return None
    return ToolFailureEnvelope(
        code=str(error.get("code") or "tool_error")[:128],
        summary=str(result.get("summary") or "工具执行失败")[:1000],
        retryable=retryable,
        stop=stop,
    )


@dataclass(slots=True)
class ToolExecutionState:
    """一次 Run 内跨多个 KT 工具轮次共享的执行状态。"""

    request_id: str
    blocked_failures: dict[str, ToolFailureEnvelope] = field(
        default_factory=dict
    )
    blocked_tool_families: dict[str, ToolFailureEnvelope] = field(
        default_factory=dict
    )
    duplicate_suppressed: int = 0
    family_suppressed: int = 0
    llm_round_index: int = 0
    final_action_only: bool = False

    def record_result(
        self,
        tool_name: str,
        args: Any,
        result: Any,
    ) -> ToolFailureEnvelope | None:
        failure = extract_tool_failure(result)
        if failure is None:
            return None
        if not failure.retryable or failure.stop:
            self.blocked_failures[
                tool_call_fingerprint(tool_name, args)
            ] = failure
        if failure.code in _FAMILY_BLOCKING_ERROR_CODES:
            self.blocked_tool_families[tool_family(tool_name)] = failure
        return failure

    def duplicate_failure(
        self,
        tool_name: str,
        args: Any,
    ) -> ToolFailureEnvelope | None:
        return self.blocked_failures.get(
            tool_call_fingerprint(tool_name, args)
        )

    def family_failure(self, tool_name: str) -> ToolFailureEnvelope | None:
        return self.blocked_tool_families.get(tool_family(tool_name))

    def next_llm_round(self) -> int:
        self.llm_round_index += 1
        return self.llm_round_index


_CURRENT_TOOL_EXECUTION_STATE: ContextVar[ToolExecutionState | None] = ContextVar(
    "nanobot_tool_execution_state",
    default=None,
)


def get_current_tool_execution_state() -> ToolExecutionState | None:
    return _CURRENT_TOOL_EXECUTION_STATE.get()


def set_current_tool_execution_state(
    state: ToolExecutionState | None,
) -> Token[ToolExecutionState | None]:
    return _CURRENT_TOOL_EXECUTION_STATE.set(state)


def reset_current_tool_execution_state(
    token: Token[ToolExecutionState | None],
) -> None:
    _CURRENT_TOOL_EXECUTION_STATE.reset(token)


@contextmanager
def tool_execution_scope(request_id: str) -> Iterator[ToolExecutionState]:
    state = ToolExecutionState(request_id=str(request_id or ""))
    token = set_current_tool_execution_state(state)
    try:
        yield state
    finally:
        reset_current_tool_execution_state(token)


@contextmanager
def final_action_only_scope() -> Iterator[None]:
    """限制回复合同重试阶段只能调用 ``reply/no_reply``。"""

    state = get_current_tool_execution_state()
    if state is None:
        yield
        return
    previous = state.final_action_only
    state.final_action_only = True
    try:
        yield
    finally:
        state.final_action_only = previous


__all__ = [
    "FINAL_ACTION_TOOLS",
    "ToolExecutionState",
    "ToolFailureEnvelope",
    "extract_tool_failure",
    "final_action_only_scope",
    "get_current_tool_execution_state",
    "reset_current_tool_execution_state",
    "set_current_tool_execution_state",
    "tool_call_fingerprint",
    "tool_execution_scope",
    "tool_family",
]
