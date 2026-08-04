"""与 Agent 框架无关的运行时端口和不可变请求合同。

本模块只能依赖 Python 标准库。KT、FastAPI、SQLAlchemy 等框架类型必须在
外层 Adapter 中完成转换，不能泄漏进这些合同。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math
from types import MappingProxyType
from typing import AsyncIterator, Awaitable, Mapping, Protocol, runtime_checkable


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
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    AMBIGUOUS = "ambiguous"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RuntimeToolCallStatus.COMPLETED,
            RuntimeToolCallStatus.FAILED,
            RuntimeToolCallStatus.CANCELLED,
            RuntimeToolCallStatus.TIMED_OUT,
            RuntimeToolCallStatus.AMBIGUOUS,
        }


class RuntimeTurnKind(str, Enum):
    USER_INPUT = "user_input"
    CONTINUE = "continue"


class RuntimeCapability(str, Enum):
    RUN = "run"
    RUN_STREAM = "run_stream"
    RUN_EVENT = "run_event"
    CONVERSATION = "conversation"
    MODEL_ROUTE = "model_route"
    TOOL_POLICY = "tool_policy"
    TOOL_INSPECTION = "tool_inspection"
    INTERRUPT = "interrupt"


class RuntimeActorType(str, Enum):
    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    SYSTEM = "system"
    ADAPTER = "adapter"


class RuntimeRunStatus(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_RUN_STATUSES


class RuntimeRunEventKind(str, Enum):
    STATUS = "status"
    TEXT_DELTA = "text_delta"
    TOOL_ACTIVITY = "tool_activity"
    USAGE = "usage"
    ARTIFACT = "artifact"
    ERROR = "error"
    END = "end"


_TERMINAL_RUN_STATUSES = frozenset({
    RuntimeRunStatus.CANCELLED,
    RuntimeRunStatus.TIMED_OUT,
    RuntimeRunStatus.SUCCEEDED,
    RuntimeRunStatus.FAILED,
    RuntimeRunStatus.AMBIGUOUS,
})

_ALLOWED_RUN_STATUS_TRANSITIONS = {
    RuntimeRunStatus.ACCEPTED: frozenset({
        RuntimeRunStatus.RUNNING,
        RuntimeRunStatus.CANCELLED,
        RuntimeRunStatus.TIMED_OUT,
        RuntimeRunStatus.FAILED,
    }),
    RuntimeRunStatus.RUNNING: frozenset({
        RuntimeRunStatus.WAITING_APPROVAL,
        RuntimeRunStatus.WAITING_INPUT,
        RuntimeRunStatus.CANCELLED,
        RuntimeRunStatus.TIMED_OUT,
        RuntimeRunStatus.SUCCEEDED,
        RuntimeRunStatus.FAILED,
        RuntimeRunStatus.AMBIGUOUS,
    }),
    RuntimeRunStatus.WAITING_APPROVAL: frozenset({
        RuntimeRunStatus.RUNNING,
        RuntimeRunStatus.CANCELLED,
        RuntimeRunStatus.TIMED_OUT,
        RuntimeRunStatus.FAILED,
    }),
    RuntimeRunStatus.WAITING_INPUT: frozenset({
        RuntimeRunStatus.RUNNING,
        RuntimeRunStatus.CANCELLED,
        RuntimeRunStatus.TIMED_OUT,
        RuntimeRunStatus.FAILED,
    }),
    RuntimeRunStatus.CANCELLED: frozenset(),
    RuntimeRunStatus.TIMED_OUT: frozenset(),
    RuntimeRunStatus.SUCCEEDED: frozenset(),
    RuntimeRunStatus.FAILED: frozenset(),
    RuntimeRunStatus.AMBIGUOUS: frozenset(),
}


def _required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    return normalized


def validate_run_status_transition(
    previous: RuntimeRunStatus,
    target: RuntimeRunStatus,
) -> RuntimeRunStatus:
    """校验一次 Run 状态迁移；终态不可再次迁移。"""

    try:
        normalized_previous = (
            previous
            if isinstance(previous, RuntimeRunStatus)
            else RuntimeRunStatus(str(previous))
        )
        normalized_target = (
            target
            if isinstance(target, RuntimeRunStatus)
            else RuntimeRunStatus(str(target))
        )
    except ValueError as exc:
        raise ValueError("Run 状态无效") from exc
    if normalized_target not in _ALLOWED_RUN_STATUS_TRANSITIONS[
        normalized_previous
    ]:
        raise ValueError(
            "不允许 Run 从 "
            f"{normalized_previous.value} 转换为 {normalized_target.value}"
        )
    return normalized_target


@dataclass(frozen=True, slots=True)
class RuntimeActor:
    """发起本次动作的主体；与资源 owner 分开表达。"""

    actor_type: RuntimeActorType
    actor_id: str
    parent_actor_id: str = ""

    def __post_init__(self) -> None:
        actor_type = self.actor_type
        if not isinstance(actor_type, RuntimeActorType):
            try:
                actor_type = RuntimeActorType(str(actor_type))
            except ValueError as exc:
                raise ValueError("actor_type 无效") from exc
        object.__setattr__(self, "actor_type", actor_type)
        object.__setattr__(self, "actor_id", _required(self.actor_id, "actor_id"))
        object.__setattr__(
            self,
            "parent_actor_id",
            str(self.parent_actor_id or "").strip(),
        )


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
class RuntimeRunIdentity:
    """一个 Run 内不可变的 owner、actor 与关联 ID。"""

    run_id: str
    turn_id: str
    correlation_id: str
    actor: RuntimeActor
    owner: RuntimePrincipal

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _required(self.run_id, "run_id"))
        object.__setattr__(self, "turn_id", _required(self.turn_id, "turn_id"))
        object.__setattr__(
            self,
            "correlation_id",
            _required(self.correlation_id, "correlation_id"),
        )
        if not isinstance(self.actor, RuntimeActor):
            raise ValueError("actor 必须是 RuntimeActor")
        if not isinstance(self.owner, RuntimePrincipal):
            raise ValueError("owner 必须是 RuntimePrincipal")


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
class RuntimeCapabilities:
    """Runtime 实现实际支持的能力快照，不等同于请求所需能力。

    Skill、MCP 等组合 Provider 通过各自 Port 预检，不由 Runtime 冒充支持。
    """

    runtime_id: str
    supported: frozenset[RuntimeCapability]
    protocol_version: str = "1.0"

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_id", _required(self.runtime_id, "runtime_id"))
        normalized: set[RuntimeCapability] = set()
        for capability in self.supported:
            try:
                normalized.add(RuntimeCapability(capability))
            except ValueError as exc:
                raise ValueError(f"Runtime capability 无效：{capability}") from exc
        object.__setattr__(self, "supported", frozenset(normalized))
        object.__setattr__(
            self,
            "protocol_version",
            _required(self.protocol_version, "protocol_version"),
        )

    def supports(self, *capabilities: RuntimeCapability) -> bool:
        return all(RuntimeCapability(item) in self.supported for item in capabilities)

    def missing(
        self,
        capabilities: frozenset[RuntimeCapability],
    ) -> tuple[RuntimeCapability, ...]:
        return tuple(
            sorted(
                (RuntimeCapability(item) for item in capabilities - self.supported),
                key=lambda item: item.value,
            )
        )


@dataclass(frozen=True, slots=True)
class RequestRuntimeContext:
    """一次运行的不可变身份、追踪与策略快照。"""

    request_id: str
    principal: RuntimePrincipal
    session_id: str
    chat_type: RuntimeChatType
    trace_id: str = ""
    run_id: str = ""
    turn_id: str = ""
    correlation_id: str = ""
    actor: RuntimeActor | None = None
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
        if self.actor is not None and not isinstance(self.actor, RuntimeActor):
            raise ValueError("actor 必须是 RuntimeActor")

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

    @property
    def owner(self) -> RuntimePrincipal:
        return self.principal

    def execution_identity(self) -> RuntimeRunIdentity:
        """生成类型化 Run identity；旧入口未补齐 ID 时明确失败。"""

        if self.actor is None:
            raise ValueError("actor 不能为空")
        return RuntimeRunIdentity(
            run_id=self.run_id,
            turn_id=self.turn_id,
            correlation_id=self.correlation_id,
            actor=self.actor,
            owner=self.principal,
        )


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
class RuntimeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    cost_microunits: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "reasoning_tokens",
            "cost_microunits",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"usage.{field_name} 必须是非负整数")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class RuntimeArtifactRef:
    artifact_id: str
    uri: str
    sha256: str = ""
    media_type: str = "application/octet-stream"
    size_bytes: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_id",
            _required(self.artifact_id, "artifact_id"),
        )
        object.__setattr__(self, "uri", _required(self.uri, "artifact.uri"))
        digest = str(self.sha256 or "").strip().lower()
        if digest and (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("artifact.sha256 必须为空或 64 位十六进制摘要")
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(
            self,
            "media_type",
            _required(self.media_type, "artifact.media_type").lower(),
        )
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError("artifact.size_bytes 必须是非负整数")


@dataclass(frozen=True, slots=True)
class RuntimeRunError:
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required(self.code, "error.code"))
        object.__setattr__(
            self,
            "message",
            _required(self.message, "error.message"),
        )
        if not isinstance(self.retryable, bool):
            raise ValueError("error.retryable 必须是 bool")


@dataclass(frozen=True, slots=True)
class RuntimeToolExecutionRequest:
    """已完成策略决策后交给确定性工具执行 Port 的不可变请求。"""

    context: RequestRuntimeContext
    tool_call: RuntimeToolCall
    execution_port_id: str
    idempotency_key: str
    timeout_seconds: float
    attributes: tuple[RuntimeAttribute, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.context, RequestRuntimeContext):
            raise ValueError("tool execution context 无效")
        if not isinstance(self.tool_call, RuntimeToolCall):
            raise ValueError("tool_call 必须是 RuntimeToolCall")
        if self.tool_call.status is not RuntimeToolCallStatus.REQUESTED:
            raise ValueError("待执行 tool_call 必须处于 requested 状态")
        if not isinstance(self.tool_call.arguments, Mapping):
            raise ValueError("确定性工具参数必须是对象")
        frozen_call = RuntimeToolCall(
            call_id=self.tool_call.call_id,
            name=self.tool_call.name,
            arguments=MappingProxyType(dict(self.tool_call.arguments)),
            status=self.tool_call.status,
            result=self.tool_call.result,
        )
        object.__setattr__(self, "tool_call", frozen_call)
        object.__setattr__(
            self,
            "execution_port_id",
            _required(self.execution_port_id, "execution_port_id"),
        )
        object.__setattr__(
            self,
            "idempotency_key",
            _required(self.idempotency_key, "idempotency_key"),
        )
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(float(self.timeout_seconds))
            or float(self.timeout_seconds) <= 0
        ):
            raise ValueError("tool timeout_seconds 必须是有限正数")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        object.__setattr__(self, "attributes", tuple(self.attributes))
        keys = [attribute.key for attribute in self.attributes]
        if len(keys) != len(set(keys)):
            raise ValueError("tool execution attributes 不能包含重复 key")

    @property
    def arguments(self) -> Mapping[str, object]:
        return self.tool_call.arguments


@dataclass(frozen=True, slots=True)
class RuntimeToolExecutionResult:
    """工具执行的框架无关终态结果。"""

    tool_call: RuntimeToolCall
    error: RuntimeRunError | None = None
    exit_code: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.tool_call, RuntimeToolCall):
            raise ValueError("tool_call 必须是 RuntimeToolCall")
        if not self.tool_call.status.is_terminal:
            raise ValueError("工具执行结果必须处于终态")
        if self.tool_call.status is RuntimeToolCallStatus.COMPLETED:
            if self.error is not None:
                raise ValueError("completed 工具结果不能携带 error")
            if self.exit_code not in {None, 0}:
                raise ValueError("completed 工具结果 exit_code 必须为空或 0")
        elif self.error is None:
            raise ValueError("非 completed 工具结果必须携带 error")
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ValueError("tool result exit_code 必须是整数或空")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def success(self) -> bool:
        return self.tool_call.status is RuntimeToolCallStatus.COMPLETED

    @property
    def output(self) -> object | None:
        return self.tool_call.result

    @property
    def tool_call_id(self) -> str:
        return self.tool_call.call_id


@dataclass(frozen=True, slots=True)
class RuntimeRunEvent:
    """Runtime 对外输出的类型化事件；不承担数据库提交语义。"""

    event_id: str
    identity: RuntimeRunIdentity
    sequence: int
    kind: RuntimeRunEventKind
    status: RuntimeRunStatus
    occurred_at: datetime
    text_delta: str = ""
    tool_call: RuntimeToolCall | None = None
    usage: RuntimeUsage | None = None
    artifact: RuntimeArtifactRef | None = None
    error: RuntimeRunError | None = None
    attributes: tuple[RuntimeAttribute, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required(self.event_id, "event_id"))
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("identity 必须是 RuntimeRunIdentity")
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("event.sequence 必须是正整数")
        kind = self.kind
        if not isinstance(kind, RuntimeRunEventKind):
            try:
                kind = RuntimeRunEventKind(str(kind))
            except ValueError as exc:
                raise ValueError("event.kind 无效") from exc
        object.__setattr__(self, "kind", kind)
        status = self.status
        if not isinstance(status, RuntimeRunStatus):
            try:
                status = RuntimeRunStatus(str(status))
            except ValueError as exc:
                raise ValueError("event.status 无效") from exc
        object.__setattr__(self, "status", status)
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("event.occurred_at 必须包含时区")
        object.__setattr__(self, "text_delta", str(self.text_delta or ""))
        object.__setattr__(self, "attributes", tuple(self.attributes))
        attribute_keys = [attribute.key for attribute in self.attributes]
        if len(attribute_keys) != len(set(attribute_keys)):
            raise ValueError("event.attributes 不能包含重复 key")

        payloads = {
            RuntimeRunEventKind.TEXT_DELTA: bool(self.text_delta),
            RuntimeRunEventKind.TOOL_ACTIVITY: self.tool_call is not None,
            RuntimeRunEventKind.USAGE: self.usage is not None,
            RuntimeRunEventKind.ARTIFACT: self.artifact is not None,
            RuntimeRunEventKind.ERROR: self.error is not None,
        }
        expected_payload = payloads.get(kind)
        if expected_payload is False:
            raise ValueError(f"{kind.value} 事件缺少对应 payload")
        for payload_kind, present in payloads.items():
            if present and payload_kind is not kind:
                raise ValueError(
                    f"{kind.value} 事件不能携带 {payload_kind.value} payload"
                )
        if kind is RuntimeRunEventKind.END:
            if not status.is_terminal:
                raise ValueError("end 事件必须携带终态")
        elif status.is_terminal:
            raise ValueError("终态只能由 end 事件表达")

    @property
    def run_id(self) -> str:
        return self.identity.run_id

    @property
    def turn_id(self) -> str:
        return self.identity.turn_id

    @property
    def correlation_id(self) -> str:
        return self.identity.correlation_id

    @property
    def actor(self) -> RuntimeActor:
        return self.identity.actor

    @property
    def owner(self) -> RuntimePrincipal:
        return self.identity.owner


class RuntimeRunEventHandler(Protocol):
    """消费单次 Runtime 调用产生的瞬时事件，不代表持久化已提交。"""

    def __call__(
        self,
        event: RuntimeRunEvent,
    ) -> Awaitable[None] | None: ...


@runtime_checkable
class RunEventSink(Protocol):
    """接收类型化 Run 事件；是否持久化由具体实现声明。"""

    async def append(self, event: RuntimeRunEvent) -> None: ...


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
class ConversationPort(Protocol):
    """框架无关的单轮 conversation 快照读写能力。"""

    def replace_conversation(self, messages: tuple[RuntimeMessage, ...]) -> int: ...

    def read_conversation(self) -> tuple[RuntimeMessage, ...]: ...


@runtime_checkable
class ToolExecutionPort(Protocol):
    """不经模型、按冻结 binding 执行单个工具的 Port。"""

    @property
    def port_id(self) -> str: ...

    async def execute(
        self,
        request: RuntimeToolExecutionRequest,
    ) -> RuntimeToolExecutionResult: ...


@runtime_checkable
class AgentRuntimePort(ConversationPort, Protocol):
    """Nanobot 使用 Agent 框架的唯一稳定能力面。"""

    @property
    def runtime_id(self) -> str: ...

    @property
    def state(self) -> RuntimeLifecycleState: ...

    @property
    def lifecycle_events(self) -> tuple[RuntimeLifecycleEvent, ...]: ...

    @property
    def runtime_capabilities(self) -> RuntimeCapabilities: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def run(self, request: AgentTurnRequest) -> AgentTurnResult: ...

    def run_stream(
        self,
        request: AgentTurnRequest,
    ) -> AsyncIterator[RuntimeRunEvent]: ...

    async def run_event(
        self,
        request: AgentTurnRequest,
        handler: RuntimeRunEventHandler,
    ) -> AgentTurnResult: ...

    # 兼容旧调用面；新入口应使用 run/run_stream/run_event。
    async def execute_turn(self, request: AgentTurnRequest) -> AgentTurnResult: ...

    def clear_pending_events(self) -> RuntimePendingStateReset: ...

    def install_tool_policy(self) -> RuntimeToolPolicyStatus: ...

    def set_model_route(self, route: RuntimeModelRoute) -> None: ...

    def inspect_tool_calls(self) -> tuple[RuntimeToolCall, ...]: ...

    def list_tool_names(self) -> tuple[str, ...]: ...

    def interrupt(self, *, reason: str = "") -> bool: ...
