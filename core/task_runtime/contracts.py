"""统一语义 Task 的输入、输出、失败和模型执行 Port。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from core.resilience import FailureCategory
from core.runtime.events import RuntimeEventContext


class TaskFailureCode(StrEnum):
    INVALID_INVOCATION = "invalid_invocation"
    AUTHORIZATION_FAILED = "authorization_failed"
    CONTRACT_VERSION_MISMATCH = "contract_version_mismatch"
    TEMPLATE_UNAVAILABLE = "template_unavailable"
    ROUTE_UNAVAILABLE = "route_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    EXECUTION_TIMEOUT = "execution_timeout"
    RATE_LIMITED = "rate_limited"
    TRANSIENT_TRANSPORT = "transient_transport"
    PROVIDER_ERROR = "provider_error"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    EMPTY_OUTPUT = "empty_output"
    INVALID_JSON = "invalid_json"
    SCHEMA_INVALID = "schema_invalid"
    FIELD_OUT_OF_RANGE = "field_out_of_range"
    BUSINESS_VALIDATION_FAILED = "business_validation_failed"
    CONFLICT = "conflict"
    QUOTA_EXCEEDED = "quota_exceeded"
    CANCELLED = "cancelled"
    PERMANENT_FAILURE = "permanent_failure"


class TaskFailureStage(StrEnum):
    CONTRACT = "contract"
    RENDER = "render"
    ROUTE = "route"
    PROVIDER = "provider"
    OUTPUT_PARSE = "output_parse"
    BUSINESS_VALIDATION = "business_validation"


class TaskTerminalAction(StrEnum):
    BLOCK = "block"
    NORMAL_AGENT = "normal_agent"
    NO_REPLY = "no_reply"
    KEEP_UNPROCESSED = "keep_unprocessed"
    KEEP_CATALOG = "keep_catalog"
    PRESERVE_PENDING = "preserve_pending"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    BRANCH_FAILED = "branch_failed"
    CONSERVATIVE_DOWNRANK = "conservative_downrank"


_FAILURE_CATEGORY_BY_CODE = MappingProxyType({
    TaskFailureCode.INVALID_INVOCATION: FailureCategory.VALIDATION,
    TaskFailureCode.AUTHORIZATION_FAILED: FailureCategory.AUTHORIZATION,
    TaskFailureCode.CONTRACT_VERSION_MISMATCH: (
        FailureCategory.CONTRACT_VIOLATION
    ),
    TaskFailureCode.TEMPLATE_UNAVAILABLE: FailureCategory.UNAVAILABLE,
    TaskFailureCode.ROUTE_UNAVAILABLE: FailureCategory.UNAVAILABLE,
    TaskFailureCode.PROVIDER_UNAVAILABLE: FailureCategory.UNAVAILABLE,
    TaskFailureCode.EXECUTION_TIMEOUT: FailureCategory.TIMEOUT,
    TaskFailureCode.RATE_LIMITED: FailureCategory.RATE_LIMITED,
    TaskFailureCode.TRANSIENT_TRANSPORT: (
        FailureCategory.TRANSIENT_TRANSPORT
    ),
    TaskFailureCode.PROVIDER_ERROR: FailureCategory.TRANSIENT_TRANSPORT,
    TaskFailureCode.OUTPUT_LIMIT_EXCEEDED: FailureCategory.QUOTA,
    TaskFailureCode.EMPTY_OUTPUT: FailureCategory.CONTRACT_VIOLATION,
    TaskFailureCode.INVALID_JSON: FailureCategory.CONTRACT_VIOLATION,
    TaskFailureCode.SCHEMA_INVALID: FailureCategory.CONTRACT_VIOLATION,
    TaskFailureCode.FIELD_OUT_OF_RANGE: (
        FailureCategory.CONTRACT_VIOLATION
    ),
    TaskFailureCode.BUSINESS_VALIDATION_FAILED: (
        FailureCategory.CONTRACT_VIOLATION
    ),
    TaskFailureCode.CONFLICT: FailureCategory.CONFLICT,
    TaskFailureCode.QUOTA_EXCEEDED: FailureCategory.QUOTA,
    TaskFailureCode.CANCELLED: FailureCategory.CANCELLED,
    TaskFailureCode.PERMANENT_FAILURE: FailureCategory.PERMANENT,
})


def failure_category_for_code(
    code: TaskFailureCode | str,
) -> FailureCategory:
    """按稳定错误码返回分类，不读取异常 message 或 HTTP body。"""

    return _FAILURE_CATEGORY_BY_CODE[TaskFailureCode(code)]


def _safe_text(value: object, *, max_chars: int) -> str:
    text = str(value or "").strip()
    text = "".join(
        character if ord(character) >= 32 else " "
        for character in text
    )
    return text[:max_chars]


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_value(item)
            for key, item in value.items()
        })
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_value(item) for item in value)
    return value


def thaw_task_value(value: Any) -> Any:
    """把只读 TaskResult 值复制为业务层可拥有的普通容器。"""

    if isinstance(value, Mapping):
        return {
            str(key): thaw_task_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple | list):
        return [thaw_task_value(item) for item in value]
    if isinstance(value, frozenset | set):
        return {
            thaw_task_value(item)
            for item in value
        }
    return value

@dataclass(frozen=True, slots=True)
class TaskValidationDiagnostic:
    code: str
    path: str = ""
    rule: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        code = _safe_text(self.code, max_chars=64)
        if not code:
            raise ValueError("TaskValidationDiagnostic.code 不能为空")
        object.__setattr__(self, "code", code)
        object.__setattr__(
            self,
            "path",
            _safe_text(self.path, max_chars=160),
        )
        object.__setattr__(
            self,
            "rule",
            _safe_text(self.rule, max_chars=64),
        )
        object.__setattr__(
            self,
            "summary",
            _safe_text(self.summary, max_chars=240),
        )


@dataclass(frozen=True, slots=True)
class TaskTypedFailure:
    code: TaskFailureCode
    stage: TaskFailureStage
    retryable: bool
    summary: str
    terminal_action: TaskTerminalAction
    category: FailureCategory | None = None
    cause_type: str = ""
    trace_ref: str = ""

    def __post_init__(self) -> None:
        code = TaskFailureCode(self.code)
        expected_category = failure_category_for_code(code)
        category = (
            expected_category
            if self.category is None
            else FailureCategory(self.category)
        )
        if category is not expected_category:
            raise ValueError("TaskTypedFailure category 与 code 不一致")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "stage", TaskFailureStage(self.stage))
        object.__setattr__(
            self,
            "terminal_action",
            TaskTerminalAction(self.terminal_action),
        )
        object.__setattr__(
            self,
            "summary",
            _safe_text(self.summary, max_chars=240),
        )
        object.__setattr__(
            self,
            "cause_type",
            _safe_text(self.cause_type, max_chars=128),
        )
        object.__setattr__(
            self,
            "trace_ref",
            _safe_text(self.trace_ref, max_chars=128),
        )


class TaskModelExecutionError(RuntimeError):
    """模型 Adapter 可安全归类的运行时失败；编程错误不得包装进本异常。"""

    def __init__(
        self,
        *,
        code: TaskFailureCode | str,
        summary: str,
        retryable: bool,
        cause_type: str = "",
    ) -> None:
        self.code = TaskFailureCode(code)
        self.category = failure_category_for_code(self.code)
        self.summary = _safe_text(summary, max_chars=240)
        self.retryable = bool(retryable)
        self.cause_type = _safe_text(cause_type, max_chars=128)
        super().__init__(self.summary)


@dataclass(frozen=True, slots=True)
class TaskInvocation:
    invocation_id: str
    route_key: str
    input_values: Mapping[str, Any]
    contract_version: str = ""
    template_refs: tuple[str, ...] = ()
    rendered_messages: tuple[Mapping[str, Any], ...] = ()
    request_context: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    timeout_budget_seconds: float = 30.0
    trace_context: RuntimeEventContext | None = None
    max_tokens: int | None = None
    temperature: float | None = None

    def __post_init__(self) -> None:
        invocation_id = _safe_text(self.invocation_id, max_chars=128)
        route_key = _safe_text(self.route_key, max_chars=128)
        if not invocation_id or not route_key:
            raise ValueError("TaskInvocation 必须声明 invocation_id 和 route_key")
        timeout = float(self.timeout_budget_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("TaskInvocation.timeout_budget_seconds 必须为正数")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("TaskInvocation.max_tokens 必须为正数")
        if self.temperature is not None and not math.isfinite(
            float(self.temperature)
        ):
            raise ValueError("TaskInvocation.temperature 必须为有限数")
        object.__setattr__(self, "invocation_id", invocation_id)
        object.__setattr__(self, "route_key", route_key)
        object.__setattr__(
            self,
            "contract_version",
            _safe_text(self.contract_version, max_chars=128),
        )
        object.__setattr__(
            self,
            "template_refs",
            tuple(
                _safe_text(item, max_chars=192)
                for item in self.template_refs
                if _safe_text(item, max_chars=192)
            ),
        )
        object.__setattr__(
            self,
            "rendered_messages",
            tuple(
                _freeze_mapping(message)
                for message in self.rendered_messages
            ),
        )
        object.__setattr__(
            self,
            "input_values",
            _freeze_mapping(self.input_values),
        )
        object.__setattr__(
            self,
            "request_context",
            _freeze_mapping(self.request_context),
        )
        object.__setattr__(
            self,
            "idempotency_key",
            _safe_text(self.idempotency_key, max_chars=256),
        )
        object.__setattr__(self, "timeout_budget_seconds", timeout)

    @property
    def task_id(self) -> str:
        return self.invocation_id


@dataclass(frozen=True, slots=True)
class TaskModelRequest:
    task_id: str
    contract_version: str
    route_key: str
    messages: tuple[Mapping[str, Any], ...]
    run_id: str
    attempt_no: int
    timeout_seconds: float
    max_tokens: int | None = None
    temperature: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.attempt_no <= 0:
            raise ValueError("TaskModelRequest.attempt_no 必须为正数")
        if self.timeout_seconds <= 0:
            raise ValueError("TaskModelRequest.timeout_seconds 必须为正数")
        if not self.messages:
            raise ValueError("TaskModelRequest.messages 不能为空")
        object.__setattr__(
            self,
            "messages",
            tuple(_freeze_mapping(message) for message in self.messages),
        )
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class TaskModelCompletion:
    content: str
    route_key: str
    provider: str = ""
    model: str = ""
    usage: Mapping[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", str(self.content or ""))
        object.__setattr__(
            self,
            "route_key",
            _safe_text(self.route_key, max_chars=128),
        )
        object.__setattr__(
            self,
            "provider",
            _safe_text(self.provider, max_chars=128),
        )
        object.__setattr__(
            self,
            "model",
            _safe_text(self.model, max_chars=192),
        )
        object.__setattr__(self, "usage", _freeze_mapping(self.usage))
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(self.metadata),
        )


@runtime_checkable
class TaskModelExecutionPort(Protocol):
    @property
    def adapter_id(self) -> str: ...

    def complete_task(
        self,
        request: TaskModelRequest,
    ) -> TaskModelCompletion: ...


@dataclass(frozen=True, slots=True)
class TaskResult:
    parsed_value: Any
    contract_version: str
    route_key: str
    provider: str
    model: str
    attempt_count: int
    latency_ms: float
    failure: TaskTypedFailure | None
    raw_output_sha256: str
    raw_output_bytes: int
    validation_diagnostics: tuple[TaskValidationDiagnostic, ...]
    run_id: str
    usage: Mapping[str, Any] = field(default_factory=dict)
    execution_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parsed_value",
            _freeze_value(self.parsed_value),
        )
        object.__setattr__(
            self,
            "validation_diagnostics",
            tuple(self.validation_diagnostics),
        )
        object.__setattr__(self, "usage", _freeze_mapping(self.usage))
        object.__setattr__(
            self,
            "execution_metadata",
            _freeze_mapping(self.execution_metadata),
        )

    @property
    def ok(self) -> bool:
        return self.failure is None
