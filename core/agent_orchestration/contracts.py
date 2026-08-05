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


MULTI_AGENT_FEATURE_ID = "multi_agent_orchestration_v1"
ORCHESTRATION_SCHEMA_VERSION = 1
MAX_ROLE_COUNT = 32
MAX_TASK_COUNT = 64
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
class AgentTaskOutput:
    """所有 Worker 的稳定观察合同。"""

    status: AgentTaskOutputStatus
    summary: str
    next_actions: tuple[str, ...] = ()
    artifacts: tuple[RuntimeArtifactRef, ...] = ()
    data: Mapping[str, object] = field(default_factory=dict)
    usage: RuntimeUsage = field(default_factory=RuntimeUsage)
    model_calls: int = 1

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
        }
        for name, maximum in maxima.items():
            object.__setattr__(
                self,
                name,
                _positive_int(getattr(self, name), name, maximum),
            )
        if self.max_concurrency > self.max_tasks:
            raise ValueError("max_concurrency 不能超过 max_tasks")
        if self.max_checkpoints < self.max_tasks:
            raise ValueError("max_checkpoints 必须覆盖每个任务边界")
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
        self._validate_acyclic(task_by_id)
        digest = hashlib.sha256(canonical_json_bytes(self.to_dict(include_hash=False))).hexdigest()
        declared = str(self.content_sha256 or "").strip().lower()
        if declared and _sha256(declared, "plan content_sha256") != digest:
            raise ValueError("plan content_sha256 与内容不一致")
        object.__setattr__(self, "content_sha256", digest)

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

    def execution_batches(self) -> tuple[tuple[str, ...], ...]:
        """按依赖优先和 task_id 生成确定性并发批次。"""

        task_by_id = self.task_by_id
        remaining = set(task_by_id)
        completed: set[str] = set()
        batches: list[tuple[str, ...]] = []
        while remaining:
            ready = sorted(
                task_id
                for task_id in remaining
                if set(task_by_id[task_id].dependencies) <= completed
            )
            for offset in range(0, len(ready), self.budget.max_concurrency):
                batch = tuple(ready[offset:offset + self.budget.max_concurrency])
                batches.append(batch)
                completed.update(batch)
                remaining.difference_update(batch)
        return tuple(batches)

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
class AgentOrchestrationRequest:
    orchestration_id: str
    identity: RuntimeRunIdentity
    plan: AgentOrchestrationPlan
    approval: AgentOrchestrationApproval
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
        if self.attempt_no != 1:
            raise ValueError("8.1 尚不允许隐式任务重试")
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
class AgentOrchestrationCheckpoint:
    checkpoint_id: str
    orchestration_id: str
    identity: RuntimeRunIdentity
    plan_sha256: str
    sequence: int
    parent_checkpoint_id: str
    task_states: Mapping[str, AgentTaskState]
    outputs: Mapping[str, AgentTaskOutput]
    receipt_sha256s: tuple[str, ...]
    created_at: datetime
    state_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "checkpoint_id", _identifier(self.checkpoint_id, "checkpoint_id", max_chars=200))
        object.__setattr__(self, "orchestration_id", _identifier(self.orchestration_id, "orchestration_id", max_chars=160))
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("checkpoint identity 无效")
        object.__setattr__(self, "plan_sha256", _sha256(self.plan_sha256, "checkpoint plan_sha256"))
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
        receipts = tuple(self.receipt_sha256s)
        if len(receipts) != self.sequence or any(
            _sha256(item, "checkpoint receipt_sha256") != item for item in receipts
        ):
            raise ValueError("checkpoint receipt_sha256s 与 sequence 不一致")
        object.__setattr__(self, "receipt_sha256s", receipts)
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

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "checkpoint_id": self.checkpoint_id,
            "orchestration_id": self.orchestration_id,
            "run_id": self.identity.run_id,
            "owner": self.identity.owner.canonical_id,
            "plan_sha256": self.plan_sha256,
            "sequence": self.sequence,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "task_states": {
                task_id: state.value for task_id, state in self.task_states.items()
            },
            "outputs": {
                task_id: output.to_dict() for task_id, output in self.outputs.items()
            },
            "receipt_sha256s": list(self.receipt_sha256s),
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
        receipt_ids = [item.task_id for item in receipts]
        if len(receipt_ids) != len(set(receipt_ids)):
            raise ValueError("result task receipt 不能重复")
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
    "AgentOrchestrationPlan",
    "AgentOrchestrationRequest",
    "AgentOrchestrationResult",
    "AgentOrchestrationState",
    "AgentRoleDefinition",
    "AgentRoleKind",
    "AgentTaskCompletionCondition",
    "AgentTaskDefinition",
    "AgentTaskDependencyReceipt",
    "AgentTaskExecutionContext",
    "AgentTaskExecutionReceipt",
    "AgentTaskExecutor",
    "AgentTaskInputBinding",
    "AgentTaskOutput",
    "AgentTaskOutputStatus",
    "AgentTaskState",
    "JsonObjectContract",
    "MAX_CHECKPOINT_BYTES",
    "MULTI_AGENT_FEATURE_ID",
    "ORCHESTRATION_SCHEMA_VERSION",
    "canonical_json_bytes",
    "plain_json",
]
