"""有界多 Agent DAG 的框架无关、不可变合同。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
import math
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from core.agent_runtime.contracts import (
    RuntimeArtifactRef,
    RuntimeRunIdentity,
    RuntimeUsage,
)
from core.agent_runtime.governance_contracts import RuntimeAccessKind


MULTI_AGENT_FEATURE_ID = "multi_agent_orchestration_v1"
ORCHESTRATION_SCHEMA_VERSION = 3
MAX_ROLE_COUNT = 32
MAX_TASK_COUNT = 64
MAX_TASK_RETRY_ATTEMPTS = 5
MAX_TASK_ATTEMPT_COUNT = MAX_TASK_COUNT * MAX_TASK_RETRY_ATTEMPTS
MAX_TASK_INPUT_BYTES = 256 * 1024
MAX_TASK_OUTPUT_BYTES = 512 * 1024
MAX_CHECKPOINT_BYTES = 4 * 1024 * 1024


class AgentOrchestrationError(RuntimeError):
    """带安全恢复建议的稳定编排错误。"""

    def __init__(
        self,
        code: str,
        summary: str,
        *,
        next_actions: Sequence[str] = (),
        stop_condition: str = "修复声明后使用新 orchestration_id 重试",
    ) -> None:
        self.code = _identifier(code, "orchestration error code")
        self.summary = _text(summary, "orchestration error summary", 500)
        self.next_actions = _text_tuple(
            next_actions,
            "orchestration error next_action",
            max_items=8,
            max_chars=300,
        )
        self.stop_condition = _text(
            stop_condition,
            "orchestration error stop_condition",
            500,
        )
        super().__init__(f"{self.code}: {self.summary}")


class AgentRoleKind(str, Enum):
    COORDINATOR = "coordinator"
    WORKER = "worker"
    REVIEWER = "reviewer"
    AGGREGATOR = "aggregator"


class AgentTaskPurpose(str, Enum):
    EXPLORE = "explore"
    RETRIEVE = "retrieve"
    EXECUTE = "execute"
    VERIFY = "verify"
    JUDGE = "judge"
    AGGREGATE = "aggregate"


class AgentModelClass(str, Enum):
    ECONOMY = "economy"
    QUALITY = "quality"


class AgentTaskOutputStatus(str, Enum):
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class AgentTaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def terminal(self) -> bool:
        return self not in {self.PENDING, self.RUNNING}


_NON_RETRYABLE_ERROR_CODES = frozenset({
    "budget_identity_mismatch",
    "checkpoint_store_failed",
    "child_access_scope_denied",
    "child_mcp_scope_denied",
    "child_mcp_server_access_missing",
    "child_model_catalog_mismatch",
    "child_model_route_drift",
    "child_model_scope_denied",
    "child_parent_identity_mismatch",
    "child_prompt_payload_too_large",
    "child_prompt_runtime_invalid",
    "child_reported_budget_exceeded",
    "child_runtime_capability_missing",
    "child_runtime_factory_invalid",
    "child_runtime_not_isolated",
    "child_runtime_stop_failed",
    "child_runtime_stop_unconfirmed",
    "child_scope_plan_missing",
    "child_skill_dependency_missing",
    "child_skill_permission_missing",
    "child_skill_scope_denied",
    "child_skill_tool_missing",
    "child_task_plan_mismatch",
    "child_tool_access_invalid",
    "child_tool_plan_missing",
    "child_tool_policy_unavailable",
    "child_tool_scope_denied",
    "dependency_not_succeeded",
    "dependency_output_missing",
    "orchestration_budget_exceeded",
    "recursive_spawn_denied",
    "runtime_budget_exceeded",
    "review_model_not_independent",
    "subagent_budget_denied",
    "task_cancelled",
    "task_cancel_unconfirmed",
    "task_input_missing",
    "task_runtime_policy_missing",
})


class AgentOrchestrationState(str, Enum):
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


def _text(value: object, name: str, max_chars: int) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > max_chars
        or any(ord(character) < 32 and character not in "\n\t" for character in normalized)
    ):
        raise ValueError(f"{name} 无效")
    return normalized


def _identifier(value: object, name: str, *, max_chars: int = 128) -> str:
    normalized = _text(value, name, max_chars)
    if any(character.isspace() for character in normalized):
        raise ValueError(f"{name} 不能包含空白")
    return normalized


def _sha256(value: object, name: str, *, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized and allow_empty:
        return ""
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} 必须是 SHA-256")
    return normalized


def _positive_int(value: object, name: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} 必须是 1..{maximum} 的整数")
    return int(value)


def _text_tuple(
    values: Sequence[object],
    name: str,
    *,
    max_items: int,
    max_chars: int,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} 必须是序列")
    normalized = tuple(_text(item, name, max_chars) for item in values)
    if len(normalized) > max_items or len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} 数量超限或重复")
    if not allow_empty and not normalized:
        raise ValueError(f"{name} 不能为空")
    return normalized


def _freeze_json(value: object, *, depth: int = 0) -> object:
    if depth > 16:
        raise ValueError("JSON 数据嵌套超过 16 层")
    if value is None or type(value) in {bool, int}:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON 浮点数必须有限")
        return value
    if isinstance(value, str):
        if len(value) > MAX_TASK_OUTPUT_BYTES or "\x00" in value:
            raise ValueError("JSON 字符串超出边界")
        return value
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise ValueError("JSON 对象字段超过 256 个")
        normalized: dict[str, object] = {}
        for raw_key, raw_value in sorted(
            value.items(),
            key=lambda item: str(item[0]),
        ):
            key = _identifier(raw_key, "JSON key", max_chars=128)
            if key in normalized:
                raise ValueError("JSON key 重复")
            normalized[key] = _freeze_json(raw_value, depth=depth + 1)
        return MappingProxyType(normalized)
    if isinstance(value, (list, tuple)):
        if len(value) > 1024:
            raise ValueError("JSON 数组超过 1024 项")
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    raise ValueError(f"JSON 数据包含不支持的类型：{type(value).__name__}")


def plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [plain_json(item) for item in value]
    return value


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        plain_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class JsonObjectContract:
    """窄 JSON 对象合同；不接受未声明字段。"""

    required_keys: tuple[str, ...] = ()
    optional_keys: tuple[str, ...] = ()
    max_bytes: int = MAX_TASK_INPUT_BYTES

    def __post_init__(self) -> None:
        required = _text_tuple(
            self.required_keys,
            "required key",
            max_items=128,
            max_chars=128,
        )
        optional = _text_tuple(
            self.optional_keys,
            "optional key",
            max_items=128,
            max_chars=128,
        )
        if set(required) & set(optional):
            raise ValueError("JSON 合同的 required/optional key 不能重叠")
        object.__setattr__(self, "required_keys", tuple(sorted(required)))
        object.__setattr__(self, "optional_keys", tuple(sorted(optional)))
        object.__setattr__(
            self,
            "max_bytes",
            _positive_int(self.max_bytes, "JSON max_bytes", MAX_CHECKPOINT_BYTES),
        )

    @property
    def allowed_keys(self) -> frozenset[str]:
        return frozenset((*self.required_keys, *self.optional_keys))

    def validate(self, value: object, *, name: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} 必须是 JSON 对象")
        frozen = _freeze_json(value)
        assert isinstance(frozen, Mapping)
        keys = frozenset(str(key) for key in frozen)
        missing = set(self.required_keys) - keys
        unknown = keys - self.allowed_keys
        if missing:
            raise ValueError(f"{name} 缺少字段：{','.join(sorted(missing))}")
        if unknown:
            raise ValueError(f"{name} 含未知字段：{','.join(sorted(unknown))}")
        if len(canonical_json_bytes(frozen)) > self.max_bytes:
            raise ValueError(f"{name} 超过 {self.max_bytes} bytes")
        return frozen

    def validate_partial(
        self,
        value: object,
        *,
        name: str,
        extra_keys: tuple[str, ...] = (),
    ) -> Mapping[str, object]:
        """验证失败态 data：允许缺少成功必填字段，但仍拒绝越界字段。"""

        if not isinstance(value, Mapping):
            raise ValueError(f"{name} 必须是 JSON 对象")
        extras = _text_tuple(
            extra_keys,
            "JSON partial extra key",
            max_items=16,
            max_chars=128,
        )
        frozen = _freeze_json(value)
        assert isinstance(frozen, Mapping)
        keys = frozenset(str(key) for key in frozen)
        extra_set = set(extras)
        unknown = keys - self.allowed_keys - extra_set
        if unknown:
            raise ValueError(f"{name} 含未知字段：{','.join(sorted(unknown))}")
        host_extra_keys = extra_set - self.allowed_keys
        contract_payload = {
            key: item
            for key, item in frozen.items()
            if key not in host_extra_keys
        }
        if len(canonical_json_bytes(contract_payload)) > self.max_bytes:
            raise ValueError(f"{name} 超过 {self.max_bytes} bytes")
        return frozen

    def to_dict(self) -> dict[str, object]:
        return {
            "required_keys": list(self.required_keys),
            "optional_keys": list(self.optional_keys),
            "max_bytes": self.max_bytes,
        }


@dataclass(frozen=True, slots=True)
class AgentRoleDefinition:
    role_id: str
    kind: AgentRoleKind
    description: str
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _identifier(self.role_id, "role_id"))
        object.__setattr__(self, "kind", AgentRoleKind(self.kind))
        object.__setattr__(
            self,
            "description",
            _text(self.description, "role description", 1_000),
        )
        object.__setattr__(
            self,
            "capabilities",
            tuple(sorted(_text_tuple(
                self.capabilities,
                "role capability",
                max_items=64,
                max_chars=128,
            ))),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "role_id": self.role_id,
            "kind": self.kind.value,
            "description": self.description,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True, slots=True)
class AgentTaskAccessRequirement:
    """计划声明的最小资源需求；授权来源只能由宿主从父信封派生。"""

    kind: RuntimeAccessKind
    resource: str
    operations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", RuntimeAccessKind(self.kind))
        resource = _identifier(
            self.resource,
            "task access resource",
            max_chars=512,
        )
        if "*" in resource or "?" in resource:
            raise ValueError("task access resource 不允许通配符")
        object.__setattr__(self, "resource", resource)
        object.__setattr__(
            self,
            "operations",
            tuple(sorted(_text_tuple(
                self.operations,
                "task access operation",
                max_items=32,
                max_chars=128,
                allow_empty=False,
            ))),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "resource": self.resource,
            "operations": list(self.operations),
        }


@dataclass(frozen=True, slots=True)
class AgentTaskAuthority:
    """任务可见资源、Skill 与 MCP 的显式最小集合。"""

    access: tuple[AgentTaskAccessRequirement, ...] = ()
    skill_ids: tuple[str, ...] = ()
    mcp_tool_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        raw_access = tuple(self.access)
        if any(
            not isinstance(item, AgentTaskAccessRequirement)
            for item in raw_access
        ):
            raise ValueError("task authority access 无效")
        access = tuple(sorted(
            raw_access,
            key=lambda item: (item.kind.value, item.resource),
        ))
        keys = [(item.kind, item.resource) for item in access]
        if len(keys) != len(set(keys)):
            raise ValueError("task authority 同一 kind/resource 只能声明一次")
        object.__setattr__(self, "access", access)
        skill_ids = tuple(sorted(_text_tuple(
            self.skill_ids,
            "task skill_id",
            max_items=64,
            max_chars=256,
        )))
        mcp_names = tuple(sorted(_text_tuple(
            self.mcp_tool_names,
            "task mcp_tool_name",
            max_items=64,
            max_chars=128,
        )))
        object.__setattr__(self, "skill_ids", skill_ids)
        object.__setattr__(self, "mcp_tool_names", mcp_names)
        if skill_ids and not any(
            item.kind is RuntimeAccessKind.SKILL for item in access
        ):
            raise ValueError("task skill_ids 必须绑定显式 Skill access")
        if not skill_ids and any(
            item.kind is RuntimeAccessKind.SKILL for item in access
        ):
            raise ValueError("task Skill access 必须收窄到具体 skill_ids")
        tool_names = {
            item.resource.removeprefix("tool:")
            for item in access
            if item.kind is RuntimeAccessKind.TOOL
            and item.resource.startswith("tool:")
            and "execute" in item.operations
        }
        if not set(mcp_names) <= tool_names:
            raise ValueError("task MCP 工具必须同时声明精确 Tool execute access")
        if mcp_names and not any(
            item.kind is RuntimeAccessKind.MCP for item in access
        ):
            raise ValueError("task MCP 工具必须绑定显式 MCP access")

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(
            item.resource.removeprefix("tool:")
            for item in self.access
            if item.kind is RuntimeAccessKind.TOOL
            and item.resource.startswith("tool:")
            and "execute" in item.operations
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "access": [item.to_dict() for item in self.access],
            "skill_ids": list(self.skill_ids),
            "mcp_tool_names": list(self.mcp_tool_names),
        }


@dataclass(frozen=True, slots=True)
class AgentTaskRuntimeBudget:
    """单个子 Runtime 的硬预算；Subagent 预算固定为零。"""

    model_call_limit: int
    token_limit: int
    cost_limit_microunits: int
    tool_call_limit: int
    time_limit_ms: int

    def __post_init__(self) -> None:
        maxima = {
            "model_call_limit": 10_000,
            "token_limit": 100_000_000,
            "cost_limit_microunits": 10_000_000_000,
            "tool_call_limit": 100_000,
            "time_limit_ms": 3_600_000,
        }
        for name, maximum in maxima.items():
            value = getattr(self, name)
            minimum = 1 if name in {
                "model_call_limit",
                "token_limit",
                "time_limit_ms",
            } else 0
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(
                    f"task runtime budget {name} 必须是 {minimum}..{maximum} 的整数"
                )

    @property
    def step_limit(self) -> int:
        return self.model_call_limit + self.tool_call_limit

    def to_dict(self) -> dict[str, int]:
        return {
            "model_call_limit": self.model_call_limit,
            "token_limit": self.token_limit,
            "cost_limit_microunits": self.cost_limit_microunits,
            "tool_call_limit": self.tool_call_limit,
            "time_limit_ms": self.time_limit_ms,
        }


@dataclass(frozen=True, slots=True)
class AgentTaskRuntimePolicy:
    """绑定任务用途、模型等级、固定路由与最小权限。"""

    purpose: AgentTaskPurpose
    model_class: AgentModelClass
    model_route_id: str
    model_route_sha256: str
    authority: AgentTaskAuthority
    budget: AgentTaskRuntimeBudget

    def __post_init__(self) -> None:
        purpose = AgentTaskPurpose(self.purpose)
        model_class = AgentModelClass(self.model_class)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "model_class", model_class)
        object.__setattr__(
            self,
            "model_route_id",
            _identifier(self.model_route_id, "task model_route_id", max_chars=256),
        )
        object.__setattr__(
            self,
            "model_route_sha256",
            _sha256(self.model_route_sha256, "task model_route_sha256"),
        )
        if not isinstance(self.authority, AgentTaskAuthority):
            raise ValueError("task runtime authority 无效")
        if not isinstance(self.budget, AgentTaskRuntimeBudget):
            raise ValueError("task runtime budget 无效")
        if model_class is AgentModelClass.ECONOMY and purpose not in {
            AgentTaskPurpose.EXPLORE,
            AgentTaskPurpose.RETRIEVE,
        }:
            raise ValueError("低成本模型只允许探索或检索任务")
        if purpose in {
            AgentTaskPurpose.VERIFY,
            AgentTaskPurpose.JUDGE,
            AgentTaskPurpose.AGGREGATE,
        } and model_class is not AgentModelClass.QUALITY:
            raise ValueError("验证、裁判和汇总必须使用高质量模型")

    def to_dict(self) -> dict[str, object]:
        return {
            "purpose": self.purpose.value,
            "model_class": self.model_class.value,
            "model_route_id": self.model_route_id,
            "model_route_sha256": self.model_route_sha256,
            "authority": self.authority.to_dict(),
            "budget": self.budget.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AgentTaskInputBinding:
    """由协调者把根输入或依赖任务 data 字段绑定到任务输入。"""

    target_key: str
    source_key: str
    source_task_id: str = ""
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_key",
            _identifier(self.target_key, "binding target_key"),
        )
        object.__setattr__(
            self,
            "source_key",
            _identifier(self.source_key, "binding source_key"),
        )
        source_task_id = str(self.source_task_id or "").strip()
        if source_task_id:
            source_task_id = _identifier(source_task_id, "binding source_task_id")
        object.__setattr__(self, "source_task_id", source_task_id)
        if type(self.required) is not bool:
            raise ValueError("binding.required 必须是 bool")

    @property
    def from_root(self) -> bool:
        return not self.source_task_id

    def to_dict(self) -> dict[str, object]:
        return {
            "target_key": self.target_key,
            "source_key": self.source_key,
            "source_task_id": self.source_task_id,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class AgentTaskCompletionCondition:
    accepted_statuses: tuple[AgentTaskOutputStatus, ...] = (
        AgentTaskOutputStatus.SUCCESS,
    )
    required_data_keys: tuple[str, ...] = ()
    minimum_artifacts: int = 0

    def __post_init__(self) -> None:
        statuses = tuple(
            sorted(
                {AgentTaskOutputStatus(status) for status in self.accepted_statuses},
                key=lambda item: item.value,
            )
        )
        if not statuses or AgentTaskOutputStatus.ERROR in statuses:
            raise ValueError("完成条件不能为空或接受 error")
        object.__setattr__(self, "accepted_statuses", statuses)
        object.__setattr__(
            self,
            "required_data_keys",
            tuple(sorted(_text_tuple(
                self.required_data_keys,
                "completion required_data_key",
                max_items=128,
                max_chars=128,
            ))),
        )
        if type(self.minimum_artifacts) is not int or not 0 <= self.minimum_artifacts <= 128:
            raise ValueError("minimum_artifacts 必须是 0..128 的整数")

    def matches(self, output: "AgentTaskOutput") -> bool:
        return bool(
            output.status in self.accepted_statuses
            and set(self.required_data_keys) <= set(output.data)
            and len(output.artifacts) >= self.minimum_artifacts
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted_statuses": [item.value for item in self.accepted_statuses],
            "required_data_keys": list(self.required_data_keys),
            "minimum_artifacts": self.minimum_artifacts,
        }


@dataclass(frozen=True, slots=True)
class AgentTaskRetryPolicy:
    """冻结在计划内的局部重试边界；不允许随机抖动或隐式扩容。"""

    max_attempts: int = 1
    retryable_error_codes: tuple[str, ...] = ()
    backoff_ms: tuple[int, ...] = ()
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        attempts = _positive_int(
            self.max_attempts,
            "retry max_attempts",
            MAX_TASK_RETRY_ATTEMPTS,
        )
        object.__setattr__(self, "max_attempts", attempts)
        codes = tuple(sorted(_text_tuple(
            self.retryable_error_codes,
            "retryable error_code",
            max_items=32,
            max_chars=128,
        )))
        if set(codes) & _NON_RETRYABLE_ERROR_CODES:
            raise ValueError("retry policy 包含不可重试的治理错误")
        object.__setattr__(self, "retryable_error_codes", codes)
        backoff = tuple(self.backoff_ms)
        if len(backoff) != max(0, attempts - 1) or any(
            type(value) is not int or not 0 <= value <= 60_000
            for value in backoff
        ):
            raise ValueError("retry backoff_ms 必须精确覆盖后续尝试且位于 0..60000")
        object.__setattr__(self, "backoff_ms", backoff)
        key = str(self.idempotency_key or "").strip()
        if attempts == 1:
            if codes or backoff or key:
                raise ValueError("单次任务不能声明重试字段")
        else:
            if not codes:
                raise ValueError("多次任务必须声明可重试错误码")
            key = _identifier(
                key,
                "retry idempotency_key",
                max_chars=200,
            )
        object.__setattr__(self, "idempotency_key", key)

    def permits(self, error_code: str, attempt_no: int) -> bool:
        return bool(
            attempt_no < self.max_attempts
            and error_code in self.retryable_error_codes
        )

    def delay_seconds(self, attempt_no: int) -> float:
        if not 1 <= attempt_no < self.max_attempts:
            raise ValueError("attempt_no 不存在后续重试")
        return self.backoff_ms[attempt_no - 1] / 1000

    def to_dict(self) -> dict[str, object]:
        return {
            "max_attempts": self.max_attempts,
            "retryable_error_codes": list(self.retryable_error_codes),
            "backoff_ms": list(self.backoff_ms),
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class AgentTaskOutput:
    """所有 Worker 的稳定观察合同。"""

    status: AgentTaskOutputStatus
    summary: str
    next_actions: tuple[str, ...] = ()
    artifacts: tuple[RuntimeArtifactRef, ...] = ()
    data: Mapping[str, object] = field(default_factory=dict)
    usage: RuntimeUsage = field(default_factory=RuntimeUsage)
    model_calls: int = 1
    tool_calls: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", AgentTaskOutputStatus(self.status))
        object.__setattr__(self, "summary", _text(self.summary, "task summary", 2_000))
        object.__setattr__(
            self,
            "next_actions",
            _text_tuple(
                self.next_actions,
                "task next_action",
                max_items=16,
                max_chars=500,
            ),
        )
        artifacts = tuple(self.artifacts)
        if len(artifacts) > 128 or any(
            not isinstance(item, RuntimeArtifactRef) for item in artifacts
        ):
            raise ValueError("task artifacts 无效或超过 128 项")
        artifact_ids = [item.artifact_id for item in artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("task artifacts 不能重复")
        object.__setattr__(self, "artifacts", artifacts)
        frozen_data = _freeze_json(self.data)
        if not isinstance(frozen_data, Mapping):
            raise ValueError("task data 必须是 JSON 对象")
        object.__setattr__(self, "data", frozen_data)
        if not isinstance(self.usage, RuntimeUsage):
            raise ValueError("task usage 必须是 RuntimeUsage")
        if type(self.model_calls) is not int or not 0 <= self.model_calls <= 100_000:
            raise ValueError("task model_calls 必须是 0..100000 的整数")
        if type(self.tool_calls) is not int or not 0 <= self.tool_calls <= 100_000:
            raise ValueError("task tool_calls 必须是 0..100000 的整数")
        if len(canonical_json_bytes(self.to_dict())) > MAX_TASK_OUTPUT_BYTES:
            raise ValueError("task output 超过 512 KiB")

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.to_dict())).hexdigest()

    @property
    def size_bytes(self) -> int:
        return len(canonical_json_bytes(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "summary": self.summary,
            "next_actions": list(self.next_actions),
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "uri": item.uri,
                    "sha256": item.sha256,
                    "media_type": item.media_type,
                    "size_bytes": item.size_bytes,
                    "version": item.version,
                    "source_run_id": item.source_run_id,
                }
                for item in self.artifacts
            ],
            "data": plain_json(self.data),
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "cached_input_tokens": self.usage.cached_input_tokens,
                "reasoning_tokens": self.usage.reasoning_tokens,
                "cost_microunits": self.usage.cost_microunits,
            },
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
        }


@dataclass(frozen=True, slots=True)
class AgentTaskDefinition:
    task_id: str
    role_id: str
    description: str
    dependencies: tuple[str, ...]
    input_contract: JsonObjectContract
    input_bindings: tuple[AgentTaskInputBinding, ...]
    output_contract: JsonObjectContract
    completion: AgentTaskCompletionCondition
    timeout_ms: int = 60_000
    runtime_policy: AgentTaskRuntimePolicy | None = None
    retry_policy: AgentTaskRetryPolicy = field(
        default_factory=AgentTaskRetryPolicy
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        object.__setattr__(self, "role_id", _identifier(self.role_id, "role_id"))
        object.__setattr__(
            self,
            "description",
            _text(self.description, "task description", 4_000),
        )
        dependencies = tuple(sorted(_text_tuple(
            self.dependencies,
            "task dependency",
            max_items=MAX_TASK_COUNT,
            max_chars=128,
        )))
        if self.task_id in dependencies:
            raise ValueError("任务不能依赖自身")
        object.__setattr__(self, "dependencies", dependencies)
        if not isinstance(self.input_contract, JsonObjectContract):
            raise ValueError("task input_contract 无效")
        if not isinstance(self.output_contract, JsonObjectContract):
            raise ValueError("task output_contract 无效")
        bindings = tuple(sorted(
            self.input_bindings,
            key=lambda item: item.target_key,
        ))
        if any(not isinstance(item, AgentTaskInputBinding) for item in bindings):
            raise ValueError("task input_bindings 无效")
        targets = [item.target_key for item in bindings]
        if len(targets) != len(set(targets)):
            raise ValueError("task input binding target 不能重复")
        if set(targets) != self.input_contract.allowed_keys:
            raise ValueError("task input binding 必须精确覆盖 input_contract")
        required_targets = {
            item.target_key for item in bindings if item.required
        }
        if required_targets != set(self.input_contract.required_keys):
            raise ValueError("binding.required 必须与 input_contract.required_keys 一致")
        source_tasks = {
            item.source_task_id for item in bindings if item.source_task_id
        }
        if not source_tasks <= set(dependencies):
            raise ValueError("task binding 只能引用显式依赖")
        object.__setattr__(self, "input_bindings", bindings)
        if not isinstance(self.completion, AgentTaskCompletionCondition):
            raise ValueError("task completion 无效")
        if not set(self.completion.required_data_keys) <= self.output_contract.allowed_keys:
            raise ValueError("完成条件引用了未声明输出字段")
        object.__setattr__(
            self,
            "timeout_ms",
            _positive_int(self.timeout_ms, "task timeout_ms", 3_600_000),
        )
        if self.runtime_policy is not None:
            if not isinstance(self.runtime_policy, AgentTaskRuntimePolicy):
                raise ValueError("task runtime_policy 无效")
            if self.runtime_policy.budget.time_limit_ms > self.timeout_ms:
                raise ValueError("task runtime budget 不能超过任务 timeout_ms")
        if not isinstance(self.retry_policy, AgentTaskRetryPolicy):
            raise ValueError("task retry_policy 无效")

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "role_id": self.role_id,
            "description": self.description,
            "dependencies": list(self.dependencies),
            "input_contract": self.input_contract.to_dict(),
            "input_bindings": [item.to_dict() for item in self.input_bindings],
            "output_contract": self.output_contract.to_dict(),
            "completion": self.completion.to_dict(),
            "timeout_ms": self.timeout_ms,
            "runtime_policy": (
                self.runtime_policy.to_dict()
                if self.runtime_policy is not None
                else None
            ),
            "retry_policy": self.retry_policy.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AgentOrchestrationBudget:
    max_tasks: int
    max_concurrency: int
    max_model_calls: int
    max_tokens: int
    max_cost_microunits: int
    max_elapsed_ms: int
    max_output_bytes: int
    max_checkpoints: int
    max_spawn_depth: int = 1
    max_tool_calls: int = 0

    def __post_init__(self) -> None:
        maxima = {
            "max_tasks": MAX_TASK_COUNT,
            "max_concurrency": 8,
            "max_model_calls": 100_000,
            "max_tokens": 100_000_000,
            "max_cost_microunits": 10_000_000_000,
            "max_elapsed_ms": 86_400_000,
            "max_output_bytes": MAX_CHECKPOINT_BYTES,
            "max_checkpoints": MAX_TASK_COUNT,
            "max_tool_calls": 100_000,
        }
        for name, maximum in maxima.items():
            value = getattr(self, name)
            if name == "max_tool_calls":
                if type(value) is not int or not 0 <= value <= maximum:
                    raise ValueError(
                        f"{name} 必须是 0..{maximum} 的整数"
                    )
                continue
            object.__setattr__(self, name, _positive_int(value, name, maximum))
        if self.max_concurrency > self.max_tasks:
            raise ValueError("max_concurrency 不能超过 max_tasks")
        if self.max_spawn_depth != 1:
            raise ValueError("首版多 Agent 只允许一层 spawn")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_tasks": self.max_tasks,
            "max_concurrency": self.max_concurrency,
            "max_model_calls": self.max_model_calls,
            "max_tokens": self.max_tokens,
            "max_cost_microunits": self.max_cost_microunits,
            "max_elapsed_ms": self.max_elapsed_ms,
            "max_output_bytes": self.max_output_bytes,
            "max_checkpoints": self.max_checkpoints,
            "max_spawn_depth": self.max_spawn_depth,
            "max_tool_calls": self.max_tool_calls,
        }


@dataclass(frozen=True, slots=True)
class AgentTaskBarrier:
    """由冻结 DAG 和并发上限确定性派生的任务屏障。"""

    barrier_id: str
    sequence: int
    task_ids: tuple[str, ...]
    completed_before: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "barrier_id",
            _identifier(self.barrier_id, "barrier_id", max_chars=200),
        )
        object.__setattr__(
            self,
            "sequence",
            _positive_int(self.sequence, "barrier sequence", MAX_TASK_COUNT),
        )
        task_ids = tuple(sorted(_text_tuple(
            self.task_ids,
            "barrier task_id",
            max_items=MAX_TASK_COUNT,
            max_chars=128,
            allow_empty=False,
        )))
        completed = tuple(sorted(_text_tuple(
            self.completed_before,
            "barrier completed task_id",
            max_items=MAX_TASK_COUNT,
            max_chars=128,
        )))
        if set(task_ids) & set(completed):
            raise ValueError("barrier 当前任务与已完成任务不能重叠")
        object.__setattr__(self, "task_ids", task_ids)
        object.__setattr__(self, "completed_before", completed)

    def to_dict(self) -> dict[str, object]:
        return {
            "barrier_id": self.barrier_id,
            "sequence": self.sequence,
            "task_ids": list(self.task_ids),
            "completed_before": list(self.completed_before),
        }


@dataclass(frozen=True, slots=True)
class AgentOrchestrationPlan:
    plan_id: str
    revision: int
    roles: tuple[AgentRoleDefinition, ...]
    tasks: tuple[AgentTaskDefinition, ...]
    root_input_contract: JsonObjectContract
    aggregation_task_id: str
    budget: AgentOrchestrationBudget
    content_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _identifier(self.plan_id, "plan_id", max_chars=160))
        object.__setattr__(
            self,
            "revision",
            _positive_int(self.revision, "plan revision", 1_000_000),
        )
        roles = tuple(sorted(self.roles, key=lambda item: item.role_id))
        if not roles or len(roles) > MAX_ROLE_COUNT or any(
            not isinstance(item, AgentRoleDefinition) for item in roles
        ):
            raise ValueError("plan roles 无效")
        role_ids = [item.role_id for item in roles]
        if len(role_ids) != len(set(role_ids)):
            raise ValueError("plan role_id 不能重复")
        coordinators = [
            item for item in roles if item.kind is AgentRoleKind.COORDINATOR
        ]
        if len(coordinators) != 1:
            raise ValueError("plan 必须且只能有一个 coordinator")
        object.__setattr__(self, "roles", roles)
        tasks = tuple(sorted(self.tasks, key=lambda item: item.task_id))
        if not tasks or any(not isinstance(item, AgentTaskDefinition) for item in tasks):
            raise ValueError("plan tasks 无效")
        if not isinstance(self.budget, AgentOrchestrationBudget):
            raise ValueError("plan budget 无效")
        if len(tasks) > self.budget.max_tasks:
            raise ValueError("任务数超过 plan budget")
        if not isinstance(self.root_input_contract, JsonObjectContract):
            raise ValueError("root_input_contract 无效")
        task_ids = [item.task_id for item in tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("plan task_id 不能重复")
        role_by_id = {item.role_id: item for item in roles}
        for task in tasks:
            role = role_by_id.get(task.role_id)
            if role is None or role.kind is AgentRoleKind.COORDINATOR:
                raise ValueError("task 必须绑定非 coordinator 角色")
            if not set(task.dependencies) <= set(task_ids):
                raise ValueError("task 含未知依赖")
            for binding in task.input_bindings:
                if binding.from_root and binding.source_key not in self.root_input_contract.allowed_keys:
                    raise ValueError("task binding 引用了未声明根输入")
        object.__setattr__(self, "tasks", tasks)
        aggregation_task_id = _identifier(
            self.aggregation_task_id,
            "aggregation_task_id",
        )
        task_by_id = {item.task_id: item for item in tasks}
        aggregation = task_by_id.get(aggregation_task_id)
        if aggregation is None:
            raise ValueError("aggregation_task_id 不存在")
        if role_by_id[aggregation.role_id].kind is not AgentRoleKind.AGGREGATOR:
            raise ValueError("aggregation task 必须绑定 aggregator 角色")
        other_task_ids = set(task_ids) - {aggregation_task_id}
        if set(aggregation.dependencies) != other_task_ids:
            raise ValueError("aggregation task 必须直接依赖所有其他任务")
        aggregate_sources = {
            item.source_task_id
            for item in aggregation.input_bindings
            if item.source_task_id
        }
        if aggregate_sources != other_task_ids:
            raise ValueError("aggregation task 必须显式绑定所有任务输出")
        if any(aggregation_task_id in task.dependencies for task in tasks):
            raise ValueError("其他任务不能依赖 aggregation task")
        object.__setattr__(self, "aggregation_task_id", aggregation_task_id)
        runtime_tasks = tuple(
            task for task in tasks if task.runtime_policy is not None
        )
        if runtime_tasks and len(runtime_tasks) != len(tasks):
            raise ValueError("同一计划不能混用 Runtime 任务和未绑定执行策略的任务")
        if runtime_tasks:
            totals = {
                "max_model_calls": sum(
                    task.runtime_policy.budget.model_call_limit
                    * task.retry_policy.max_attempts
                    for task in runtime_tasks
                ),
                "max_tokens": sum(
                    task.runtime_policy.budget.token_limit
                    * task.retry_policy.max_attempts
                    for task in runtime_tasks
                ),
                "max_cost_microunits": sum(
                    task.runtime_policy.budget.cost_limit_microunits
                    * task.retry_policy.max_attempts
                    for task in runtime_tasks
                ),
                "max_tool_calls": sum(
                    task.runtime_policy.budget.tool_call_limit
                    * task.retry_policy.max_attempts
                    for task in runtime_tasks
                ),
            }
            for budget_name, requested in totals.items():
                if requested > getattr(self.budget, budget_name):
                    raise ValueError(
                        f"task runtime budgets 合计超过 plan {budget_name}"
                    )
            for task in runtime_tasks:
                policy = task.runtime_policy
                assert policy is not None
                role_kind = role_by_id[task.role_id].kind
                if role_kind is AgentRoleKind.WORKER and policy.purpose not in {
                    AgentTaskPurpose.EXPLORE,
                    AgentTaskPurpose.RETRIEVE,
                    AgentTaskPurpose.EXECUTE,
                }:
                    raise ValueError("worker 任务用途必须是探索、检索或执行")
                if role_kind is AgentRoleKind.REVIEWER:
                    if policy.purpose not in {
                        AgentTaskPurpose.VERIFY,
                        AgentTaskPurpose.JUDGE,
                    } or not task.dependencies:
                        raise ValueError("reviewer 必须验证或裁判显式依赖")
                    dependency_routes = {
                        task_by_id[dependency].runtime_policy.model_route_id
                        for dependency in task.dependencies
                    }
                    if policy.model_route_id in dependency_routes:
                        raise ValueError("reviewer 必须使用独立模型路由")
                if (
                    role_kind is AgentRoleKind.AGGREGATOR
                    and policy.purpose is not AgentTaskPurpose.AGGREGATE
                ):
                    raise ValueError("aggregator 任务用途必须是 aggregate")
        self._validate_acyclic(task_by_id)
        if len(self.execution_barriers()) > self.budget.max_checkpoints:
            raise ValueError("max_checkpoints 必须覆盖每个确定性任务屏障")
        digest = hashlib.sha256(canonical_json_bytes(self.to_dict(include_hash=False))).hexdigest()
        declared = str(self.content_sha256 or "").strip().lower()
        if declared and _sha256(declared, "plan content_sha256") != digest:
            raise ValueError("plan content_sha256 与内容不一致")
        object.__setattr__(self, "content_sha256", digest)

    @property
    def maximum_attempts(self) -> int:
        return sum(task.retry_policy.max_attempts for task in self.tasks)

    @staticmethod
    def _validate_acyclic(task_by_id: Mapping[str, AgentTaskDefinition]) -> None:
        remaining = set(task_by_id)
        completed: set[str] = set()
        while remaining:
            ready = sorted(
                task_id
                for task_id in remaining
                if set(task_by_id[task_id].dependencies) <= completed
            )
            if not ready:
                raise ValueError("plan DAG 存在循环依赖")
            completed.update(ready)
            remaining.difference_update(ready)

    @property
    def task_by_id(self) -> Mapping[str, AgentTaskDefinition]:
        return MappingProxyType({item.task_id: item for item in self.tasks})

    @property
    def role_by_id(self) -> Mapping[str, AgentRoleDefinition]:
        return MappingProxyType({item.role_id: item for item in self.roles})

    def execution_barriers(self) -> tuple[AgentTaskBarrier, ...]:
        """按依赖优先、task_id 与并发上限生成确定性屏障。"""

        task_by_id = self.task_by_id
        remaining = set(task_by_id)
        completed: set[str] = set()
        barriers: list[AgentTaskBarrier] = []
        while remaining:
            ready = sorted(
                task_id
                for task_id in remaining
                if set(task_by_id[task_id].dependencies) <= completed
            )
            for offset in range(0, len(ready), self.budget.max_concurrency):
                task_ids = tuple(
                    ready[offset:offset + self.budget.max_concurrency]
                )
                sequence = len(barriers) + 1
                task_set_sha256 = hashlib.sha256(
                    canonical_json_bytes(task_ids)
                ).hexdigest()[:16]
                barriers.append(AgentTaskBarrier(
                    barrier_id=(
                        f"barrier-{sequence:04d}-{task_set_sha256}"
                    ),
                    sequence=sequence,
                    task_ids=task_ids,
                    completed_before=tuple(sorted(completed)),
                ))
                completed.update(task_ids)
                remaining.difference_update(task_ids)
        return tuple(barriers)

    def execution_batches(self) -> tuple[tuple[str, ...], ...]:
        """兼容旧调用方；批次内容来自同一确定性屏障计划。"""

        return tuple(barrier.task_ids for barrier in self.execution_barriers())

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "plan_id": self.plan_id,
            "revision": self.revision,
            "roles": [item.to_dict() for item in self.roles],
            "tasks": [item.to_dict() for item in self.tasks],
            "root_input_contract": self.root_input_contract.to_dict(),
            "aggregation_task_id": self.aggregation_task_id,
            "budget": self.budget.to_dict(),
            "communication_mode": "coordinator_mediated",
        }
        if include_hash:
            payload["content_sha256"] = self.content_sha256
        return payload


@dataclass(frozen=True, slots=True)
class AgentOrchestrationApproval:
    approval_id: str
    plan_id: str
    plan_revision: int
    plan_sha256: str
    approved_by: str
    approved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "approval_id", _identifier(self.approval_id, "approval_id", max_chars=160))
        object.__setattr__(self, "plan_id", _identifier(self.plan_id, "approval plan_id", max_chars=160))
        object.__setattr__(
            self,
            "plan_revision",
            _positive_int(self.plan_revision, "approval plan_revision", 1_000_000),
        )
        object.__setattr__(self, "plan_sha256", _sha256(self.plan_sha256, "approval plan_sha256"))
        object.__setattr__(self, "approved_by", _identifier(self.approved_by, "approved_by", max_chars=160))
        if self.approved_at.tzinfo is None or self.approved_at.utcoffset() is None:
            raise ValueError("approved_at 必须包含时区")

    def validates(self, plan: AgentOrchestrationPlan) -> bool:
        return bool(
            self.plan_id == plan.plan_id
            and self.plan_revision == plan.revision
            and self.plan_sha256 == plan.content_sha256
        )


@dataclass(frozen=True, slots=True)
class AgentOrchestrationFreeze:
    """批准后的独立冻结证明；执行入口必须同时校验两份证明。"""

    freeze_id: str
    approval_id: str
    plan_id: str
    plan_revision: int
    plan_sha256: str
    frozen_by: str
    frozen_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "freeze_id",
            _identifier(self.freeze_id, "freeze_id", max_chars=160),
        )
        object.__setattr__(
            self,
            "approval_id",
            _identifier(self.approval_id, "freeze approval_id", max_chars=160),
        )
        object.__setattr__(
            self,
            "plan_id",
            _identifier(self.plan_id, "freeze plan_id", max_chars=160),
        )
        object.__setattr__(
            self,
            "plan_revision",
            _positive_int(
                self.plan_revision,
                "freeze plan_revision",
                1_000_000,
            ),
        )
        object.__setattr__(
            self,
            "plan_sha256",
            _sha256(self.plan_sha256, "freeze plan_sha256"),
        )
        object.__setattr__(
            self,
            "frozen_by",
            _identifier(self.frozen_by, "frozen_by", max_chars=160),
        )
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise ValueError("frozen_at 必须包含时区")

    def validates(
        self,
        plan: AgentOrchestrationPlan,
        approval: AgentOrchestrationApproval,
    ) -> bool:
        return bool(
            approval.validates(plan)
            and self.approval_id == approval.approval_id
            and self.plan_id == plan.plan_id
            and self.plan_revision == plan.revision
            and self.plan_sha256 == plan.content_sha256
            and self.frozen_at >= approval.approved_at
        )


@dataclass(frozen=True, slots=True)
class AgentOrchestrationRequest:
    orchestration_id: str
    identity: RuntimeRunIdentity
    plan: AgentOrchestrationPlan
    approval: AgentOrchestrationApproval
    freeze: AgentOrchestrationFreeze
    root_input: Mapping[str, object]
    nesting_depth: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "orchestration_id",
            _identifier(self.orchestration_id, "orchestration_id", max_chars=160),
        )
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("orchestration identity 无效")
        if not isinstance(self.plan, AgentOrchestrationPlan):
            raise ValueError("orchestration plan 无效")
        if not isinstance(self.approval, AgentOrchestrationApproval):
            raise ValueError("orchestration approval 无效")
        if not self.approval.validates(self.plan):
            raise ValueError("approval 未绑定当前冻结计划")
        if not isinstance(self.freeze, AgentOrchestrationFreeze):
            raise ValueError("orchestration freeze 无效")
        if not self.freeze.validates(self.plan, self.approval):
            raise ValueError("freeze 未绑定当前批准计划")
        object.__setattr__(
            self,
            "root_input",
            self.plan.root_input_contract.validate(
                self.root_input,
                name="root_input",
            ),
        )
        if type(self.nesting_depth) is not int or self.nesting_depth < 0:
            raise ValueError("nesting_depth 必须是非负整数")


@dataclass(frozen=True, slots=True)
class AgentTaskDependencyReceipt:
    task_id: str
    state: AgentTaskState
    output_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _identifier(self.task_id, "dependency task_id"))
        object.__setattr__(self, "state", AgentTaskState(self.state))
        object.__setattr__(
            self,
            "output_sha256",
            _sha256(self.output_sha256, "dependency output_sha256", allow_empty=True),
        )


@dataclass(frozen=True, slots=True)
class AgentTaskExecutionContext:
    orchestration_id: str
    identity: RuntimeRunIdentity
    task: AgentTaskDefinition
    role: AgentRoleDefinition
    inputs: Mapping[str, object]
    dependencies: tuple[AgentTaskDependencyReceipt, ...]
    nesting_depth: int = 1
    spawn_allowed: bool = False
    attempt_no: int = 1
    previous_attempts: tuple["AgentTaskExecutionReceipt", ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "orchestration_id", _identifier(self.orchestration_id, "orchestration_id", max_chars=160))
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("task identity 无效")
        if not isinstance(self.task, AgentTaskDefinition):
            raise ValueError("task definition 无效")
        if not isinstance(self.role, AgentRoleDefinition):
            raise ValueError("task role 无效")
        if self.task.role_id != self.role.role_id:
            raise ValueError("task 与 role 不匹配")
        object.__setattr__(
            self,
            "inputs",
            self.task.input_contract.validate(self.inputs, name="task inputs"),
        )
        dependencies = tuple(sorted(self.dependencies, key=lambda item: item.task_id))
        if any(not isinstance(item, AgentTaskDependencyReceipt) for item in dependencies):
            raise ValueError("task dependency receipts 无效")
        if {item.task_id for item in dependencies} != set(self.task.dependencies):
            raise ValueError("task dependency receipts 不完整")
        object.__setattr__(self, "dependencies", dependencies)
        if self.nesting_depth != 1 or self.spawn_allowed is not False:
            raise ValueError("首版 Worker 必须位于第 1 层且禁止继续 spawn")
        attempt_no = _positive_int(
            self.attempt_no,
            "task attempt_no",
            self.task.retry_policy.max_attempts,
        )
        object.__setattr__(self, "attempt_no", attempt_no)
        previous = tuple(sorted(
            self.previous_attempts,
            key=lambda item: item.attempt_no,
        ))
        if any(
            not isinstance(item, AgentTaskExecutionReceipt)
            or item.task_id != self.task.task_id
            for item in previous
        ):
            raise ValueError("previous_attempts 无效")
        if tuple(item.attempt_no for item in previous) != tuple(
            range(1, attempt_no)
        ):
            raise ValueError("previous_attempts 必须连续覆盖此前尝试")
        if any(item.state is AgentTaskState.SUCCEEDED for item in previous):
            raise ValueError("成功任务不能继续重试")
        object.__setattr__(self, "previous_attempts", previous)


@runtime_checkable
class AgentTaskExecutor(Protocol):
    async def execute(self, context: AgentTaskExecutionContext) -> AgentTaskOutput: ...


@dataclass(frozen=True, slots=True)
class AgentTaskExecutionReceipt:
    task_id: str
    role_id: str
    state: AgentTaskState
    attempt_no: int
    dependency_ids: tuple[str, ...]
    output_sha256: str
    output_size_bytes: int
    error_code: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    reservation_id: str
    receipt_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _identifier(self.task_id, "receipt task_id"))
        object.__setattr__(self, "role_id", _identifier(self.role_id, "receipt role_id"))
        state = AgentTaskState(self.state)
        if not state.terminal:
            raise ValueError("receipt 必须是任务终态")
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self,
            "attempt_no",
            _positive_int(
                self.attempt_no,
                "receipt attempt_no",
                MAX_TASK_RETRY_ATTEMPTS,
            ),
        )
        object.__setattr__(
            self,
            "dependency_ids",
            tuple(sorted(_text_tuple(
                self.dependency_ids,
                "receipt dependency_id",
                max_items=MAX_TASK_COUNT,
                max_chars=128,
            ))),
        )
        output_sha = _sha256(
            self.output_sha256,
            "receipt output_sha256",
            allow_empty=True,
        )
        object.__setattr__(self, "output_sha256", output_sha)
        if type(self.output_size_bytes) is not int or self.output_size_bytes < 0:
            raise ValueError("output_size_bytes 必须是非负整数")
        error_code = str(self.error_code or "").strip()
        if error_code:
            error_code = _identifier(error_code, "receipt error_code")
        if state is AgentTaskState.SUCCEEDED and (not output_sha or error_code):
            raise ValueError("成功 receipt 必须有输出且不能有 error_code")
        if state is not AgentTaskState.SUCCEEDED and not error_code:
            raise ValueError("非成功 receipt 必须有 error_code")
        object.__setattr__(self, "error_code", error_code)
        for name in ("started_at", "finished_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} 必须包含时区")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at 不能早于 started_at")
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise ValueError("duration_ms 必须是非负整数")
        reservation_id = str(self.reservation_id or "").strip()
        if reservation_id:
            reservation_id = _identifier(
                reservation_id,
                "receipt reservation_id",
                max_chars=200,
            )
        object.__setattr__(self, "reservation_id", reservation_id)
        digest = hashlib.sha256(canonical_json_bytes(self.to_dict(include_hash=False))).hexdigest()
        declared = str(self.receipt_sha256 or "").strip().lower()
        if declared and _sha256(declared, "receipt_sha256") != digest:
            raise ValueError("receipt_sha256 与内容不一致")
        object.__setattr__(self, "receipt_sha256", digest)

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "task_id": self.task_id,
            "role_id": self.role_id,
            "state": self.state.value,
            "attempt_no": self.attempt_no,
            "dependency_ids": list(self.dependency_ids),
            "output_sha256": self.output_sha256,
            "output_size_bytes": self.output_size_bytes,
            "error_code": self.error_code,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "reservation_id": self.reservation_id,
        }
        if include_hash:
            payload["receipt_sha256"] = self.receipt_sha256
        return payload


@dataclass(frozen=True, slots=True)
class AgentOrchestrationUsage:
    """截至某个任务屏障的累计物理消费。"""

    usage: RuntimeUsage = field(default_factory=RuntimeUsage)
    model_calls: int = 0
    tool_calls: int = 0
    task_attempts: int = 0
    output_bytes: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.usage, RuntimeUsage):
            raise ValueError("orchestration usage 无效")
        maxima = {
            "model_calls": MAX_TASK_ATTEMPT_COUNT * 100_000,
            "tool_calls": MAX_TASK_ATTEMPT_COUNT * 100_000,
            "task_attempts": MAX_TASK_ATTEMPT_COUNT,
            "output_bytes": MAX_TASK_ATTEMPT_COUNT * MAX_TASK_OUTPUT_BYTES,
        }
        for name, maximum in maxima.items():
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"orchestration usage {name} 无效")

    @property
    def step_count(self) -> int:
        return self.task_attempts + self.tool_calls

    def to_dict(self) -> dict[str, object]:
        return {
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "cached_input_tokens": self.usage.cached_input_tokens,
                "reasoning_tokens": self.usage.reasoning_tokens,
                "cost_microunits": self.usage.cost_microunits,
            },
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "task_attempts": self.task_attempts,
            "output_bytes": self.output_bytes,
        }


@dataclass(frozen=True, slots=True)
class AgentOrchestrationCheckpoint:
    checkpoint_id: str
    orchestration_id: str
    identity: RuntimeRunIdentity
    plan_id: str
    plan_revision: int
    plan_sha256: str
    freeze_id: str
    sequence: int
    parent_checkpoint_id: str
    barrier_id: str
    task_states: Mapping[str, AgentTaskState]
    outputs: Mapping[str, AgentTaskOutput]
    receipts: tuple[AgentTaskExecutionReceipt, ...]
    cumulative_usage: AgentOrchestrationUsage
    created_at: datetime
    state_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "checkpoint_id", _identifier(self.checkpoint_id, "checkpoint_id", max_chars=200))
        object.__setattr__(self, "orchestration_id", _identifier(self.orchestration_id, "orchestration_id", max_chars=160))
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("checkpoint identity 无效")
        object.__setattr__(
            self,
            "plan_id",
            _identifier(self.plan_id, "checkpoint plan_id", max_chars=160),
        )
        object.__setattr__(
            self,
            "plan_revision",
            _positive_int(
                self.plan_revision,
                "checkpoint plan_revision",
                1_000_000,
            ),
        )
        object.__setattr__(self, "plan_sha256", _sha256(self.plan_sha256, "checkpoint plan_sha256"))
        object.__setattr__(
            self,
            "freeze_id",
            _identifier(self.freeze_id, "checkpoint freeze_id", max_chars=160),
        )
        object.__setattr__(
            self,
            "sequence",
            _positive_int(self.sequence, "checkpoint sequence", MAX_TASK_COUNT),
        )
        parent = str(self.parent_checkpoint_id or "").strip()
        if parent:
            parent = _identifier(parent, "parent_checkpoint_id", max_chars=200)
        if (self.sequence == 1) != (not parent):
            raise ValueError("checkpoint parent 与 sequence 不一致")
        object.__setattr__(self, "parent_checkpoint_id", parent)
        object.__setattr__(
            self,
            "barrier_id",
            _identifier(self.barrier_id, "checkpoint barrier_id", max_chars=200),
        )
        states = {
            _identifier(task_id, "checkpoint task_id"): AgentTaskState(state)
            for task_id, state in self.task_states.items()
        }
        if any(state is AgentTaskState.RUNNING for state in states.values()):
            raise ValueError("任务边界 checkpoint 不能包含 running")
        object.__setattr__(self, "task_states", MappingProxyType(dict(sorted(states.items()))))
        outputs = dict(sorted(self.outputs.items()))
        if any(
            task_id not in states or not isinstance(output, AgentTaskOutput)
            for task_id, output in outputs.items()
        ):
            raise ValueError("checkpoint outputs 无效")
        object.__setattr__(self, "outputs", MappingProxyType(outputs))
        receipts = tuple(self.receipts)
        if not receipts or any(
            not isinstance(item, AgentTaskExecutionReceipt)
            for item in receipts
        ):
            raise ValueError("checkpoint receipts 无效")
        receipt_keys = [
            (item.task_id, item.attempt_no) for item in receipts
        ]
        if len(receipt_keys) != len(set(receipt_keys)):
            raise ValueError("checkpoint receipt attempt 重复")
        receipt_task_ids = {item.task_id for item in receipts}
        terminal_task_ids = {
            task_id for task_id, state in states.items() if state.terminal
        }
        if receipt_task_ids != terminal_task_ids or set(outputs) != terminal_task_ids:
            raise ValueError("checkpoint 终态、输出与 receipt 任务集合不一致")
        for task_id in sorted(receipt_task_ids):
            attempts = tuple(
                item for item in receipts if item.task_id == task_id
            )
            if tuple(item.attempt_no for item in attempts) != tuple(
                range(1, len(attempts) + 1)
            ):
                raise ValueError("checkpoint 同一任务的 attempt 必须连续有序")
            if any(
                item.state is AgentTaskState.SUCCEEDED
                for item in attempts[:-1]
            ):
                raise ValueError("checkpoint 成功任务不能存在后续 attempt")
            final_receipt = attempts[-1]
            final_output = outputs[task_id]
            if (
                states[task_id] is not final_receipt.state
                or final_output.content_sha256 != final_receipt.output_sha256
                or final_output.size_bytes != final_receipt.output_size_bytes
            ):
                raise ValueError("checkpoint 最终 receipt 与状态或输出不一致")
        object.__setattr__(self, "receipts", receipts)
        if not isinstance(self.cumulative_usage, AgentOrchestrationUsage):
            raise ValueError("checkpoint cumulative_usage 无效")
        if self.cumulative_usage.task_attempts != len(receipts):
            raise ValueError("checkpoint 用量与 attempt receipt 数量不一致")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("checkpoint created_at 必须包含时区")
        digest = hashlib.sha256(canonical_json_bytes(self.to_dict(include_hash=False))).hexdigest()
        declared = str(self.state_sha256 or "").strip().lower()
        if declared and _sha256(declared, "checkpoint state_sha256") != digest:
            raise ValueError("checkpoint state_sha256 与内容不一致")
        object.__setattr__(self, "state_sha256", digest)
        if self.size_bytes > MAX_CHECKPOINT_BYTES:
            raise ValueError("checkpoint 超过 4 MiB")

    @property
    def size_bytes(self) -> int:
        return len(canonical_json_bytes(self.to_dict()))

    @property
    def receipt_sha256s(self) -> tuple[str, ...]:
        return tuple(item.receipt_sha256 for item in self.receipts)

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "checkpoint_id": self.checkpoint_id,
            "orchestration_id": self.orchestration_id,
            "run_id": self.identity.run_id,
            "owner": self.identity.owner.canonical_id,
            "identity": {
                "run_id": self.identity.run_id,
                "turn_id": self.identity.turn_id,
                "correlation_id": self.identity.correlation_id,
                "actor": {
                    "actor_type": self.identity.actor.actor_type.value,
                    "actor_id": self.identity.actor.actor_id,
                    "parent_actor_id": self.identity.actor.parent_actor_id,
                },
                "owner": {
                    "platform": self.identity.owner.platform,
                    "owner_type": self.identity.owner.owner_type.value,
                    "owner_id": self.identity.owner.owner_id,
                },
            },
            "plan_id": self.plan_id,
            "plan_revision": self.plan_revision,
            "plan_sha256": self.plan_sha256,
            "freeze_id": self.freeze_id,
            "sequence": self.sequence,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "barrier_id": self.barrier_id,
            "task_states": {
                task_id: state.value for task_id, state in self.task_states.items()
            },
            "outputs": {
                task_id: output.to_dict() for task_id, output in self.outputs.items()
            },
            "receipts": [item.to_dict() for item in self.receipts],
            "cumulative_usage": self.cumulative_usage.to_dict(),
            "created_at": self.created_at.isoformat(),
        }
        if include_hash:
            payload["state_sha256"] = self.state_sha256
        return payload


@runtime_checkable
class AgentOrchestrationCheckpointStore(Protocol):
    async def save(
        self,
        checkpoint: AgentOrchestrationCheckpoint,
    ) -> AgentOrchestrationCheckpoint: ...

    async def load_latest(
        self,
        orchestration_id: str,
        *,
        owner_id: str,
    ) -> AgentOrchestrationCheckpoint | None: ...


@dataclass(frozen=True, slots=True)
class AgentOrchestrationResult:
    orchestration_id: str
    state: AgentOrchestrationState
    plan_sha256: str
    receipts: tuple[AgentTaskExecutionReceipt, ...]
    outputs: Mapping[str, AgentTaskOutput]
    aggregate_output: AgentTaskOutput | None
    latest_checkpoint_id: str
    failure_code: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "orchestration_id", _identifier(self.orchestration_id, "orchestration_id", max_chars=160))
        state = AgentOrchestrationState(self.state)
        if not state.terminal:
            raise ValueError("orchestration result 必须是终态")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "plan_sha256", _sha256(self.plan_sha256, "result plan_sha256"))
        receipts = tuple(self.receipts)
        if any(not isinstance(item, AgentTaskExecutionReceipt) for item in receipts):
            raise ValueError("result receipts 无效")
        receipt_keys = [(item.task_id, item.attempt_no) for item in receipts]
        if len(receipt_keys) != len(set(receipt_keys)):
            raise ValueError("result task attempt receipt 不能重复")
        object.__setattr__(self, "receipts", receipts)
        outputs = dict(sorted(self.outputs.items()))
        if any(not isinstance(item, AgentTaskOutput) for item in outputs.values()):
            raise ValueError("result outputs 无效")
        object.__setattr__(self, "outputs", MappingProxyType(outputs))
        if self.aggregate_output is not None and not isinstance(
            self.aggregate_output,
            AgentTaskOutput,
        ):
            raise ValueError("aggregate_output 无效")
        checkpoint_id = str(self.latest_checkpoint_id or "").strip()
        if checkpoint_id:
            checkpoint_id = _identifier(checkpoint_id, "latest_checkpoint_id", max_chars=200)
        object.__setattr__(self, "latest_checkpoint_id", checkpoint_id)
        failure_code = str(self.failure_code or "").strip()
        if failure_code:
            failure_code = _identifier(failure_code, "failure_code")
        if state is AgentOrchestrationState.SUCCEEDED:
            if failure_code or self.aggregate_output is None:
                raise ValueError("成功结果必须有 aggregate_output 且无 failure_code")
        elif not failure_code:
            raise ValueError("非成功结果必须有 failure_code")
        object.__setattr__(self, "failure_code", failure_code)


__all__ = [
    "AgentOrchestrationApproval",
    "AgentOrchestrationBudget",
    "AgentOrchestrationCheckpoint",
    "AgentOrchestrationCheckpointStore",
    "AgentOrchestrationError",
    "AgentOrchestrationFreeze",
    "AgentOrchestrationPlan",
    "AgentOrchestrationRequest",
    "AgentOrchestrationResult",
    "AgentOrchestrationState",
    "AgentOrchestrationUsage",
    "AgentModelClass",
    "AgentRoleDefinition",
    "AgentRoleKind",
    "AgentTaskAccessRequirement",
    "AgentTaskAuthority",
    "AgentTaskBarrier",
    "AgentTaskCompletionCondition",
    "AgentTaskDefinition",
    "AgentTaskDependencyReceipt",
    "AgentTaskExecutionContext",
    "AgentTaskExecutionReceipt",
    "AgentTaskExecutor",
    "AgentTaskInputBinding",
    "AgentTaskOutput",
    "AgentTaskOutputStatus",
    "AgentTaskPurpose",
    "AgentTaskRetryPolicy",
    "AgentTaskRuntimeBudget",
    "AgentTaskRuntimePolicy",
    "AgentTaskState",
    "JsonObjectContract",
    "MAX_CHECKPOINT_BYTES",
    "MAX_TASK_ATTEMPT_COUNT",
    "MAX_TASK_RETRY_ATTEMPTS",
    "MULTI_AGENT_FEATURE_ID",
    "ORCHESTRATION_SCHEMA_VERSION",
    "canonical_json_bytes",
    "plain_json",
]
