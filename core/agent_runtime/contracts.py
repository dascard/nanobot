"""与 Agent 框架无关的运行时端口和不可变请求合同。

本模块只能依赖 Python 标准库。KT、FastAPI、SQLAlchemy 等框架类型必须在
外层 Adapter 中完成转换，不能泄漏进这些合同。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math
from typing import Protocol, runtime_checkable


class RuntimeLifecycleState(str, Enum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class RuntimeOwnerType(str, Enum):
    USER = "user"
    GROUP = "group"
    PROJECT = "project"
    SYSTEM = "system"


class RuntimeChatType(str, Enum):
    PRIVATE = "private"
    GROUP = "group"
    TASK = "task"
    SYSTEM = "system"


class RuntimePlanKind(str, Enum):
    PROMPT = "prompt"
    TOOL = "tool"
    MODEL = "model"
    MEMORY = "memory"
    BUDGET = "budget"


class RuntimeToolCallStatus(str, Enum):
    REQUESTED = "requested"
    COMPLETED = "completed"


class RuntimeTurnKind(str, Enum):
    USER_INPUT = "user_input"
    CONTINUE = "continue"


def _required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    return normalized


@dataclass(frozen=True, slots=True)
class RuntimePrincipal:
    """由受信入口派生的主体；模型不得构造或覆盖。"""

    platform: str
    owner_type: RuntimeOwnerType
    owner_id: str

    def __post_init__(self) -> None:
        owner_type = self.owner_type
        if not isinstance(owner_type, RuntimeOwnerType):
            try:
                owner_type = RuntimeOwnerType(str(owner_type))
            except ValueError as exc:
                raise ValueError("owner_type 无效") from exc
        object.__setattr__(
            self, "platform", _required(self.platform, "platform").lower()
        )
        object.__setattr__(self, "owner_type", owner_type)
        object.__setattr__(self, "owner_id", _required(self.owner_id, "owner_id"))

    @property
    def canonical_id(self) -> str:
        return f"{self.platform}:{self.owner_type.value}:{self.owner_id}"


@dataclass(frozen=True, slots=True)
class RuntimeFeature:
    name: str
    enabled: bool
    source: str = "default"

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("feature.enabled 必须是 bool")
        object.__setattr__(self, "name", _required(self.name, "feature.name"))
        object.__setattr__(self, "source", _required(self.source, "feature.source"))


@dataclass(frozen=True, slots=True)
class RuntimePlanRef:
    """请求所采用计划的稳定引用，不携带框架内可变对象。"""

    kind: RuntimePlanKind
    identity: str
    sha256: str

    def __post_init__(self) -> None:
        kind = self.kind
        if not isinstance(kind, RuntimePlanKind):
            try:
                kind = RuntimePlanKind(str(kind))
            except ValueError as exc:
                raise ValueError("plan.kind 无效") from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "identity", _required(self.identity, "plan.identity"))
        digest = _required(self.sha256, "plan.sha256").lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("plan.sha256 必须是 64 位十六进制摘要")
        object.__setattr__(self, "sha256", digest)


@dataclass(frozen=True, slots=True)
class RuntimeAttribute:
    """向底层事件传递的显式附加字段。"""

    key: str
    value: object

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _required(self.key, "attribute.key"))


@dataclass(frozen=True, slots=True)
class RequestRuntimeContext:
    """一次运行的不可变身份、追踪与策略快照。"""

    request_id: str
    principal: RuntimePrincipal
    session_id: str
    chat_type: RuntimeChatType
    trace_id: str = ""
    run_id: str = ""
    message_id: str = ""
    capabilities: frozenset[str] = field(default_factory=frozenset)
    features: tuple[RuntimeFeature, ...] = ()
    plans: tuple[RuntimePlanRef, ...] = ()
    deadline_at: datetime | None = None

    def __post_init__(self) -> None:
        chat_type = self.chat_type
        if not isinstance(chat_type, RuntimeChatType):
            try:
                chat_type = RuntimeChatType(str(chat_type))
            except ValueError as exc:
                raise ValueError("chat_type 无效") from exc
        object.__setattr__(self, "chat_type", chat_type)
        object.__setattr__(self, "request_id", _required(self.request_id, "request_id"))
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        normalized_capabilities = frozenset(
            _required(value, "capability") for value in self.capabilities
        )
        object.__setattr__(self, "capabilities", normalized_capabilities)
        object.__setattr__(self, "features", tuple(self.features))
        object.__setattr__(self, "plans", tuple(self.plans))

        feature_names = [feature.name for feature in self.features]
        if len(feature_names) != len(set(feature_names)):
            raise ValueError("features 不能包含重复名称")
        plan_kinds = [plan.kind for plan in self.plans]
        if len(plan_kinds) != len(set(plan_kinds)):
            raise ValueError("plans 中每种 kind 只能出现一次")
        if self.deadline_at is not None:
            if self.deadline_at.tzinfo is None or self.deadline_at.utcoffset() is None:
                raise ValueError("deadline_at 必须包含时区")

    def feature_enabled(self, name: str, *, default: bool = False) -> bool:
        target = str(name or "").strip()
        for feature in self.features:
            if feature.name == target:
                return feature.enabled
        return default

    def plan(self, kind: RuntimePlanKind) -> RuntimePlanRef | None:
        for plan in self.plans:
            if plan.kind is kind:
                return plan
        return None


@dataclass(frozen=True, slots=True)
class RuntimeToolCall:
    call_id: str
    name: str
    arguments: object = ""
    status: RuntimeToolCallStatus = RuntimeToolCallStatus.REQUESTED
    result: object | None = None

    def __post_init__(self) -> None:
        status = self.status
        if not isinstance(status, RuntimeToolCallStatus):
            try:
                status = RuntimeToolCallStatus(str(status))
            except ValueError as exc:
                raise ValueError("tool_call.status 无效") from exc
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self, "call_id", _required(self.call_id, "tool_call.call_id")
        )
        object.__setattr__(self, "name", _required(self.name, "tool_call.name"))


@dataclass(frozen=True, slots=True)
class RuntimeMessage:
    role: str
    content: object = ""
    name: str = ""
    tool_call_id: str = ""
    tool_calls: tuple[RuntimeToolCall, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _required(self.role, "message.role"))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))


@dataclass(frozen=True, slots=True)
class RuntimeModelRoute:
    """Agent Runtime 可见的模型路由；凭据与传输连接由 Provider Adapter 持有。"""

    route_id: str
    model_id: str
    provider_id: str
    profile_id: str = ""
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    enable_thinking: str | bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "route_id", _required(self.route_id, "route_id"))
        object.__setattr__(self, "model_id", _required(self.model_id, "model_id"))
        object.__setattr__(
            self, "provider_id", _required(self.provider_id, "provider_id")
        )
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("max_tokens 必须大于 0")
        if self.temperature is not None and not math.isfinite(self.temperature):
            raise ValueError("temperature 必须是有限数值")
        if self.timeout_seconds is not None:
            if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
                raise ValueError("timeout_seconds 必须是有限正数")
        thinking = self.enable_thinking
        if isinstance(thinking, bool):
            thinking = "true" if thinking else "false"
        elif thinking is not None:
            thinking = str(thinking).strip().lower()
            if thinking not in {"auto", "true", "false"}:
                raise ValueError("enable_thinking 必须是 auto/true/false")
        object.__setattr__(self, "enable_thinking", thinking)


@dataclass(frozen=True, slots=True)
class RuntimeToolPolicyStatus:
    ready: bool
    guard_installed: bool
    schema_filter_installed: bool
    missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "missing", tuple(self.missing))


@dataclass(frozen=True, slots=True)
class RuntimePendingStateReset:
    pending_events: int = 0
    queued_events: int = 0
    pending_injections: int = 0

    @property
    def total(self) -> int:
        return self.pending_events + self.queued_events + self.pending_injections


@dataclass(frozen=True, slots=True)
class AgentTurnRequest:
    context: RequestRuntimeContext
    content: object
    stream: bool = False
    kind: RuntimeTurnKind = RuntimeTurnKind.USER_INPUT
    event_attributes: tuple[RuntimeAttribute, ...] = ()

    def __post_init__(self) -> None:
        kind = self.kind
        if not isinstance(kind, RuntimeTurnKind):
            try:
                kind = RuntimeTurnKind(str(kind))
            except ValueError as exc:
                raise ValueError("turn.kind 无效") from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "event_attributes", tuple(self.event_attributes))
        keys = [attribute.key for attribute in self.event_attributes]
        if len(keys) != len(set(keys)):
            raise ValueError("event_attributes 不能包含重复 key")
        if "stream" in keys:
            raise ValueError("stream 必须通过 AgentTurnRequest.stream 设置")


@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    raw_result: object
    messages: tuple[RuntimeMessage, ...]
    tool_calls: tuple[RuntimeToolCall, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))


@dataclass(frozen=True, slots=True)
class RuntimeLifecycleEvent:
    sequence: int
    runtime_id: str
    previous_state: RuntimeLifecycleState
    current_state: RuntimeLifecycleState
    occurred_at: datetime
    reason: str = ""


class RuntimeLifecycleEventSink(Protocol):
    def __call__(self, event: RuntimeLifecycleEvent) -> None: ...


@runtime_checkable
class AgentRuntimePort(Protocol):
    """Nanobot 使用 Agent 框架的唯一稳定能力面。"""

    @property
    def runtime_id(self) -> str: ...

    @property
    def state(self) -> RuntimeLifecycleState: ...

    @property
    def lifecycle_events(self) -> tuple[RuntimeLifecycleEvent, ...]: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def execute_turn(self, request: AgentTurnRequest) -> AgentTurnResult: ...

    def replace_conversation(self, messages: tuple[RuntimeMessage, ...]) -> int: ...

    def read_conversation(self) -> tuple[RuntimeMessage, ...]: ...

    def clear_pending_events(self) -> RuntimePendingStateReset: ...

    def install_tool_policy(self) -> RuntimeToolPolicyStatus: ...

    def set_model_route(self, route: RuntimeModelRoute) -> None: ...

    def inspect_tool_calls(self) -> tuple[RuntimeToolCall, ...]: ...

    def list_tool_names(self) -> tuple[str, ...]: ...

    def interrupt(self, *, reason: str = "") -> bool: ...
