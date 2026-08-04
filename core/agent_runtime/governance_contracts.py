"""Agent Runtime 的统一预算与资源访问声明合同。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
from collections.abc import Iterable
from typing import Any


def _required(value: object, name: str, *, max_length: int = 512) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} 不能为空")
    if len(normalized) > max_length:
        raise ValueError(f"{name} 不能超过 {max_length} 字符")
    return normalized


def _identifiers(values: object, name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ValueError(f"{name} 必须是标识符序列")
    normalized = tuple(
        sorted({_required(item, name, max_length=256) for item in values})
    )
    if any("*" in item or "?" in item for item in normalized):
        raise ValueError(f"{name} 不允许通配符")
    return normalized


class RuntimeBudgetScope(str, Enum):
    RUN = "run"
    TURN = "turn"
    TOOL = "tool"
    SUBAGENT = "subagent"


@dataclass(frozen=True, slots=True)
class RuntimeBudgetLimit:
    """一个执行层级的硬上限；0 明确表示该维度不可消费。"""

    scope: RuntimeBudgetScope
    model_call_limit: int
    token_limit: int
    cost_limit_microunits: int
    step_limit: int
    time_limit_ms: int
    concurrency_limit: int
    allowed_model_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            scope = RuntimeBudgetScope(self.scope)
        except ValueError as exc:
            raise ValueError("budget.scope 无效") from exc
        object.__setattr__(self, "scope", scope)
        maxima = {
            "model_call_limit": 100_000,
            "token_limit": 100_000_000,
            "cost_limit_microunits": 10_000_000_000,
            "step_limit": 1_000_000,
            "time_limit_ms": 86_400_000,
            "concurrency_limit": 1024,
        }
        for name, maximum in maxima.items():
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(
                    f"budget.{scope.value}.{name} 必须是 0 到 {maximum} 的整数"
                )
        object.__setattr__(
            self,
            "allowed_model_ids",
            _identifiers(
                self.allowed_model_ids,
                f"budget.{scope.value}.allowed_model_id",
            ),
        )
        if self.model_call_limit == 0 and self.allowed_model_ids:
            raise ValueError("禁止模型调用的预算不能声明 allowed_model_ids")

    def with_models(self, model_ids: tuple[str, ...]) -> "RuntimeBudgetLimit":
        if self.model_call_limit == 0:
            if model_ids:
                raise ValueError(f"{self.scope.value} 预算不允许模型调用")
            return self
        return replace(self, allowed_model_ids=model_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.value,
            "model_call_limit": self.model_call_limit,
            "token_limit": self.token_limit,
            "cost_limit_microunits": self.cost_limit_microunits,
            "step_limit": self.step_limit,
            "time_limit_ms": self.time_limit_ms,
            "concurrency_limit": self.concurrency_limit,
            "allowed_model_ids": list(self.allowed_model_ids),
        }


def _default_run_budget() -> RuntimeBudgetLimit:
    return RuntimeBudgetLimit(
        RuntimeBudgetScope.RUN,
        model_call_limit=16,
        token_limit=1_000_000,
        cost_limit_microunits=1_000_000_000,
        step_limit=64,
        time_limit_ms=120_000,
        concurrency_limit=8,
    )


def _default_turn_budget() -> RuntimeBudgetLimit:
    return RuntimeBudgetLimit(
        RuntimeBudgetScope.TURN,
        model_call_limit=8,
        token_limit=500_000,
        cost_limit_microunits=500_000_000,
        step_limit=32,
        time_limit_ms=120_000,
        concurrency_limit=8,
    )


def _default_tool_budget() -> RuntimeBudgetLimit:
    return RuntimeBudgetLimit(
        RuntimeBudgetScope.TOOL,
        model_call_limit=0,
        token_limit=0,
        cost_limit_microunits=0,
        step_limit=32,
        time_limit_ms=60_000,
        concurrency_limit=8,
    )


def _default_subagent_budget() -> RuntimeBudgetLimit:
    return RuntimeBudgetLimit(
        RuntimeBudgetScope.SUBAGENT,
        model_call_limit=0,
        token_limit=0,
        cost_limit_microunits=0,
        step_limit=0,
        time_limit_ms=0,
        concurrency_limit=0,
    )


@dataclass(frozen=True, slots=True)
class RuntimeBudgetEnvelope:
    """Run、Turn、Tool 与 Subagent 的单调收窄预算。"""

    run: RuntimeBudgetLimit = field(default_factory=_default_run_budget)
    turn: RuntimeBudgetLimit = field(default_factory=_default_turn_budget)
    tool: RuntimeBudgetLimit = field(default_factory=_default_tool_budget)
    subagent: RuntimeBudgetLimit = field(
        default_factory=_default_subagent_budget
    )

    def __post_init__(self) -> None:
        expected = {
            "run": RuntimeBudgetScope.RUN,
            "turn": RuntimeBudgetScope.TURN,
            "tool": RuntimeBudgetScope.TOOL,
            "subagent": RuntimeBudgetScope.SUBAGENT,
        }
        for name, scope in expected.items():
            value = getattr(self, name)
            if not isinstance(value, RuntimeBudgetLimit) or value.scope is not scope:
                raise ValueError(f"budget.{name} 必须声明 {scope.value} scope")
        for child_name in ("turn", "tool", "subagent"):
            child = getattr(self, child_name)
            for field_name in (
                "model_call_limit",
                "token_limit",
                "cost_limit_microunits",
                "step_limit",
                "time_limit_ms",
                "concurrency_limit",
            ):
                if getattr(child, field_name) > getattr(self.run, field_name):
                    raise ValueError(
                        f"budget.{child_name}.{field_name} 不能超过 run 上限"
                    )
        if self.run.allowed_model_ids:
            allowed = set(self.run.allowed_model_ids)
            for child_name in ("turn", "subagent"):
                child_models = set(getattr(self, child_name).allowed_model_ids)
                if not child_models <= allowed:
                    raise ValueError(
                        f"budget.{child_name}.allowed_model_ids 不能扩大 run 范围"
                    )

    def bind_model(self, model_id: str) -> "RuntimeBudgetEnvelope":
        normalized = _required(model_id, "model_id", max_length=256)
        return RuntimeBudgetEnvelope(
            run=self.run.with_models((normalized,)),
            turn=self.turn.with_models((normalized,)),
            tool=self.tool,
            subagent=(
                self.subagent.with_models((normalized,))
                if self.subagent.model_call_limit
                else self.subagent
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.to_dict(),
            "turn": self.turn.to_dict(),
            "tool": self.tool.to_dict(),
            "subagent": self.subagent.to_dict(),
        }


class RuntimeAccessKind(str, Enum):
    FILE = "file"
    NETWORK = "network"
    TOOL = "tool"
    SKILL = "skill"
    MCP = "mcp"
    MEMORY = "memory"


@dataclass(frozen=True, slots=True)
class RuntimeAccessGrant:
    """宿主解析后的精确资源范围；不接受模型提供的通配符。"""

    kind: RuntimeAccessKind
    resource: str
    operations: tuple[str, ...]
    authorization: str

    def __post_init__(self) -> None:
        try:
            kind = RuntimeAccessKind(self.kind)
        except ValueError as exc:
            raise ValueError("access.kind 无效") from exc
        object.__setattr__(self, "kind", kind)
        resource = _required(self.resource, "access.resource")
        if "*" in resource or "?" in resource:
            raise ValueError("access.resource 不允许通配符")
        object.__setattr__(self, "resource", resource)
        operations = _identifiers(
            self.operations,
            f"access.{kind.value}.operation",
        )
        if not operations:
            raise ValueError("access.operations 不能为空")
        object.__setattr__(self, "operations", operations)
        object.__setattr__(
            self,
            "authorization",
            _required(self.authorization, "access.authorization", max_length=128),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "resource": self.resource,
            "operations": list(self.operations),
            "authorization": self.authorization,
        }


def _compatibility_access_grants() -> tuple[RuntimeAccessGrant, ...]:
    return (
        RuntimeAccessGrant(
            RuntimeAccessKind.TOOL,
            "tool-plan:resolved",
            ("execute",),
            "request_tool_plan",
        ),
    )


@dataclass(frozen=True, slots=True)
class RuntimeAccessEnvelope:
    grants: tuple[RuntimeAccessGrant, ...] = field(
        default_factory=_compatibility_access_grants
    )

    def __post_init__(self) -> None:
        grants = tuple(
            sorted(
                self.grants,
                key=lambda item: (item.kind.value, item.resource),
            )
        )
        if any(not isinstance(item, RuntimeAccessGrant) for item in grants):
            raise ValueError("access.grants 包含无效声明")
        keys = [(item.kind, item.resource) for item in grants]
        if len(keys) != len(set(keys)):
            raise ValueError("同一 access kind/resource 只能声明一次")
        object.__setattr__(self, "grants", grants)

    def find(
        self,
        kind: RuntimeAccessKind,
        resource: str,
        operation: str,
    ) -> RuntimeAccessGrant | None:
        normalized_kind = RuntimeAccessKind(kind)
        normalized_resource = str(resource or "").strip()
        normalized_operation = str(operation or "").strip()
        for grant in self.grants:
            if (
                grant.kind is normalized_kind
                and grant.resource == normalized_resource
                and normalized_operation in grant.operations
            ):
                return grant
        if normalized_kind is RuntimeAccessKind.TOOL:
            for grant in self.grants:
                if (
                    grant.kind is RuntimeAccessKind.TOOL
                    and grant.resource == "tool-plan:resolved"
                    and normalized_operation in grant.operations
                ):
                    return grant
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"grants": [item.to_dict() for item in self.grants]}


@dataclass(frozen=True, slots=True)
class RuntimeGovernanceEnvelope:
    """一次 Runtime 请求不可变的预算和访问快照。"""

    policy_id: str = "runtime-governance-v1"
    budgets: RuntimeBudgetEnvelope = field(default_factory=RuntimeBudgetEnvelope)
    access: RuntimeAccessEnvelope = field(default_factory=RuntimeAccessEnvelope)
    content_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_id",
            _required(self.policy_id, "governance.policy_id", max_length=128),
        )
        if not isinstance(self.budgets, RuntimeBudgetEnvelope):
            raise ValueError("governance.budgets 无效")
        if not isinstance(self.access, RuntimeAccessEnvelope):
            raise ValueError("governance.access 无效")
        payload = {
            "policy_id": self.policy_id,
            "budgets": self.budgets.to_dict(),
            "access": self.access.to_dict(),
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        declared = str(self.content_sha256 or "").strip().lower()
        if declared and declared != digest:
            raise ValueError("governance.content_sha256 与声明不匹配")
        object.__setattr__(self, "content_sha256", digest)

    def bind_model(self, model_id: str) -> "RuntimeGovernanceEnvelope":
        return RuntimeGovernanceEnvelope(
            policy_id=self.policy_id,
            budgets=self.budgets.bind_model(model_id),
            access=self.access,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "budgets": self.budgets.to_dict(),
            "access": self.access.to_dict(),
            "content_sha256": self.content_sha256,
        }


__all__ = [
    "RuntimeAccessEnvelope",
    "RuntimeAccessGrant",
    "RuntimeAccessKind",
    "RuntimeBudgetEnvelope",
    "RuntimeBudgetLimit",
    "RuntimeBudgetScope",
    "RuntimeGovernanceEnvelope",
]
