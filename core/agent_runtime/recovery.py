"""Agent Runtime 的恢复、Checkpoint 与副作用回执稳定合同。

本模块只依赖 Python 标准库和 Runtime 值对象。数据库持久化、HTTP 与具体
Runtime Adapter 必须在外层实现，避免恢复语义反向依赖 SQLAlchemy 或 KT。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from core.agent_runtime.contracts import (
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimePlanRef,
    RuntimeRunIdentity,
    RuntimeToolCall,
    RuntimeToolExecutionResult,
)


def _required(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} 不能为空")
    return normalized


def _sha256(value: object, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} 必须是 64 位十六进制摘要")
    return normalized


class RuntimeCheckpointBoundary(str, Enum):
    """可审计的 Runtime 状态保存边界。"""

    TURN_STARTED = "turn_started"
    PLAN_RESOLVED = "plan_resolved"
    TOOL_READY = "tool_ready"
    TOOL_COMPLETED = "tool_completed"
    TOOL_AMBIGUOUS = "tool_ambiguous"
    TURN_COMPLETED = "turn_completed"
    RESTORED = "restored"


class RuntimeToolEffectClass(str, Enum):
    """工具失败后是否可能留下无法确认的副作用。"""

    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    EXTERNAL = "external"

    @property
    def requires_receipt(self) -> bool:
        return self is not RuntimeToolEffectClass.READ_ONLY


class RuntimeSideEffectState(str, Enum):
    PREPARED = "prepared"
    COMPLETED = "completed"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"

    @property
    def terminal(self) -> bool:
        return self is not RuntimeSideEffectState.PREPARED


class RuntimeRecoveryOperationKind(str, Enum):
    RESUME = "resume"
    FORK = "fork"
    REWIND = "rewind"


@dataclass(frozen=True, slots=True)
class RuntimeCheckpointCapture:
    """Runtime 在安全边界交给持久化 Adapter 的不可变状态。"""

    identity: RuntimeRunIdentity
    boundary: RuntimeCheckpointBoundary
    runtime_id: str
    runtime_protocol_version: str
    messages: tuple[RuntimeMessage, ...]
    plans: tuple[RuntimePlanRef, ...]
    model_route: RuntimeModelRoute | None = None
    model_step: int = 0
    tool_round: int = 0
    pending_tool: RuntimeToolCall | None = None
    last_tool_result: RuntimeToolExecutionResult | None = None
    side_effect_receipt_ids: tuple[str, ...] = ()
    resumable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("checkpoint capture.identity 无效")
        try:
            boundary = RuntimeCheckpointBoundary(self.boundary)
        except ValueError as exc:
            raise ValueError("checkpoint capture.boundary 无效") from exc
        object.__setattr__(self, "boundary", boundary)
        object.__setattr__(
            self,
            "runtime_id",
            _required(self.runtime_id, "checkpoint capture.runtime_id"),
        )
        object.__setattr__(
            self,
            "runtime_protocol_version",
            _required(
                self.runtime_protocol_version,
                "checkpoint capture.runtime_protocol_version",
            ),
        )
        object.__setattr__(self, "messages", tuple(self.messages))
        if any(not isinstance(item, RuntimeMessage) for item in self.messages):
            raise ValueError("checkpoint capture.messages 包含无效消息")
        plans = tuple(self.plans)
        if any(not isinstance(item, RuntimePlanRef) for item in plans):
            raise ValueError("checkpoint capture.plans 包含无效引用")
        if len({item.kind for item in plans}) != len(plans):
            raise ValueError("checkpoint capture.plans 中每种 kind 只能出现一次")
        object.__setattr__(self, "plans", plans)
        if self.model_route is not None and not isinstance(
            self.model_route,
            RuntimeModelRoute,
        ):
            raise ValueError("checkpoint capture.model_route 无效")
        for name in ("model_step", "tool_round"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"checkpoint capture.{name} 必须是非负整数")
        if self.pending_tool is not None and not isinstance(
            self.pending_tool,
            RuntimeToolCall,
        ):
            raise ValueError("checkpoint capture.pending_tool 无效")
        if self.last_tool_result is not None and not isinstance(
            self.last_tool_result,
            RuntimeToolExecutionResult,
        ):
            raise ValueError("checkpoint capture.last_tool_result 无效")
        receipt_ids = tuple(
            _required(item, "side_effect_receipt_id")
            for item in self.side_effect_receipt_ids
        )
        if len(receipt_ids) != len(set(receipt_ids)):
            raise ValueError("side_effect_receipt_ids 不能重复")
        object.__setattr__(self, "side_effect_receipt_ids", receipt_ids)
        if not isinstance(self.resumable, bool):
            raise ValueError("checkpoint capture.resumable 必须是 bool")


@dataclass(frozen=True, slots=True)
class RuntimeCheckpointReference:
    checkpoint_id: str
    run_id: str
    sequence: int
    boundary: RuntimeCheckpointBoundary
    payload_sha256: str
    resumable: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "checkpoint_id",
            _required(self.checkpoint_id, "checkpoint_id"),
        )
        object.__setattr__(self, "run_id", _required(self.run_id, "run_id"))
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("checkpoint sequence 必须是正整数")
        try:
            boundary = RuntimeCheckpointBoundary(self.boundary)
        except ValueError as exc:
            raise ValueError("checkpoint boundary 无效") from exc
        object.__setattr__(self, "boundary", boundary)
        object.__setattr__(
            self,
            "payload_sha256",
            _sha256(self.payload_sha256, "checkpoint payload_sha256"),
        )
        if not isinstance(self.resumable, bool):
            raise ValueError("checkpoint resumable 必须是 bool")


@dataclass(frozen=True, slots=True)
class RuntimeSideEffectGuard:
    receipt_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    effect_class: RuntimeToolEffectClass
    request_sha256: str

    def __post_init__(self) -> None:
        for name in ("receipt_id", "run_id", "tool_call_id", "tool_name"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        try:
            effect_class = RuntimeToolEffectClass(self.effect_class)
        except ValueError as exc:
            raise ValueError("side effect effect_class 无效") from exc
        if not effect_class.requires_receipt:
            raise ValueError("只读工具不能创建副作用回执")
        object.__setattr__(self, "effect_class", effect_class)
        object.__setattr__(
            self,
            "request_sha256",
            _sha256(self.request_sha256, "side effect request_sha256"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeSideEffectReceiptReference:
    receipt_id: str
    state: RuntimeSideEffectState
    result_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "receipt_id",
            _required(self.receipt_id, "receipt_id"),
        )
        try:
            state = RuntimeSideEffectState(self.state)
        except ValueError as exc:
            raise ValueError("side effect state 无效") from exc
        if not state.terminal:
            raise ValueError("副作用终结引用不能处于 prepared")
        object.__setattr__(self, "state", state)
        digest = str(self.result_sha256 or "").strip().lower()
        if digest:
            digest = _sha256(digest, "side effect result_sha256")
        object.__setattr__(self, "result_sha256", digest)


def runtime_model_route_sha256(route: RuntimeModelRoute) -> str:
    """计算不含凭据和连接对象的模型路由固定点。"""

    if not isinstance(route, RuntimeModelRoute):
        raise TypeError("route 必须是 RuntimeModelRoute")
    payload = {
        "route_id": route.route_id,
        "model_id": route.model_id,
        "provider_id": route.provider_id,
        "profile_id": route.profile_id,
        "temperature": route.temperature,
        "max_tokens": route.max_tokens,
        "timeout_seconds": route.timeout_seconds,
        "enable_thinking": route.enable_thinking,
    }
    if route.cost_input_1m != 0.0 or route.cost_output_1m != 0.0:
        payload["cost_input_1m"] = route.cost_input_1m
        payload["cost_output_1m"] = route.cost_output_1m
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@runtime_checkable
class RuntimeRecoveryPort(Protocol):
    async def save_checkpoint(
        self,
        capture: RuntimeCheckpointCapture,
    ) -> RuntimeCheckpointReference: ...

    async def prepare_tool_effect(
        self,
        *,
        identity: RuntimeRunIdentity,
        tool_call: RuntimeToolCall,
        execution_port_id: str,
        idempotency_key: str,
        effect_class: RuntimeToolEffectClass,
        checkpoint: RuntimeCheckpointReference,
    ) -> RuntimeSideEffectGuard | None: ...

    async def settle_tool_effect(
        self,
        guard: RuntimeSideEffectGuard,
        *,
        state: RuntimeSideEffectState,
        result: RuntimeToolExecutionResult | None = None,
        error_code: str = "",
    ) -> RuntimeSideEffectReceiptReference: ...


__all__ = [
    "RuntimeCheckpointBoundary",
    "RuntimeCheckpointCapture",
    "RuntimeCheckpointReference",
    "RuntimeRecoveryOperationKind",
    "RuntimeRecoveryPort",
    "RuntimeSideEffectGuard",
    "RuntimeSideEffectReceiptReference",
    "RuntimeSideEffectState",
    "RuntimeToolEffectClass",
    "runtime_model_route_sha256",
]
