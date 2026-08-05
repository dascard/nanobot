"""统一预算的原子计数、硬拒绝和运行期决策。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import time
from typing import Callable, Protocol

from core.agent_runtime.contracts import (
    RuntimeRunIdentity,
    RuntimeUsage,
)
from core.agent_runtime.errors import AgentRuntimeBudgetExceededError
from core.agent_runtime.governance_contracts import (
    RuntimeAccessKind,
    RuntimeBudgetLimit,
    RuntimeBudgetScope,
    RuntimeGovernanceEnvelope,
)


class RuntimeBudgetDecisionOutcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class RuntimeBudgetDecision:
    decision_id: str
    identity: RuntimeRunIdentity
    scope: RuntimeBudgetScope
    operation: str
    outcome: RuntimeBudgetDecisionOutcome
    reason: str
    resource: str
    occurred_at: datetime
    governance_sha256: str
    model_calls: int
    tokens: int
    cost_microunits: int
    steps: int
    concurrency: int
    limits: RuntimeBudgetLimit

    def __post_init__(self) -> None:
        if not self.decision_id:
            raise ValueError("budget decision_id 不能为空")
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("budget identity 无效")
        object.__setattr__(self, "scope", RuntimeBudgetScope(self.scope))
        object.__setattr__(
            self,
            "outcome",
            RuntimeBudgetDecisionOutcome(self.outcome),
        )
        if not str(self.operation or "").strip():
            raise ValueError("budget operation 不能为空")
        if not str(self.reason or "").strip():
            raise ValueError("budget reason 不能为空")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("budget occurred_at 必须包含时区")
        if len(str(self.governance_sha256 or "")) != 64:
            raise ValueError("budget governance_sha256 无效")
        for name in (
            "model_calls",
            "tokens",
            "cost_microunits",
            "steps",
            "concurrency",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"budget decision {name} 必须是非负整数")
        if not isinstance(self.limits, RuntimeBudgetLimit):
            raise ValueError("budget decision limits 无效")


class RuntimeBudgetDecisionSink(Protocol):
    def emit(self, decision: RuntimeBudgetDecision) -> None: ...


@dataclass(slots=True)
class _UsageState:
    model_calls: int = 0
    tokens: int = 0
    cost_microunits: int = 0
    steps: int = 0
    concurrency: int = 0
    started_monotonic: float = 0.0


@dataclass(frozen=True, slots=True)
class RuntimeBudgetReservation:
    reservation_id: str
    scope: RuntimeBudgetScope
    turn_id: str


@dataclass(frozen=True, slots=True)
class RuntimeBudgetConsumption:
    """预算账户的只读累计量，用于 Runtime 回传真实物理消费。"""

    scope: RuntimeBudgetScope
    model_calls: int
    tokens: int
    cost_microunits: int
    steps: int
    concurrency: int


def _numeric_policy(limit: RuntimeBudgetLimit) -> tuple[int, ...]:
    return (
        limit.model_call_limit,
        limit.token_limit,
        limit.cost_limit_microunits,
        limit.step_limit,
        limit.time_limit_ms,
        limit.concurrency_limit,
    )


class RuntimeBudgetAccount:
    """单个 Run 的预算账户；同一 Turn 的路由重试共享消费。"""

    def __init__(
        self,
        identity: RuntimeRunIdentity,
        governance: RuntimeGovernanceEnvelope,
        *,
        sink: RuntimeBudgetDecisionSink | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(identity, RuntimeRunIdentity):
            raise TypeError("identity 必须是 RuntimeRunIdentity")
        if not isinstance(governance, RuntimeGovernanceEnvelope):
            raise TypeError("governance 必须是 RuntimeGovernanceEnvelope")
        self._identity = identity
        self._governance = governance
        self._sink = sink
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(timezone.utc))
        started = self._monotonic()
        self._run = _UsageState(started_monotonic=started)
        self._turns: dict[str, _UsageState] = {
            identity.turn_id: _UsageState(started_monotonic=started)
        }
        self._turn_governance: dict[str, RuntimeGovernanceEnvelope] = {
            identity.turn_id: governance
        }
        self._tool = _UsageState(started_monotonic=started)
        self._subagent = _UsageState(started_monotonic=started)
        self._decision_sequence = 0
        self._reservation_sequence = 0
        self._reservations: dict[str, RuntimeBudgetReservation] = {}
        self._emit(
            identity,
            RuntimeBudgetScope.RUN,
            "declared",
            RuntimeBudgetDecisionOutcome.ALLOW,
            "governance_snapshot_bound",
            governance.policy_id,
            self._run,
            governance.budgets.run,
        )
        for scope, state in (
            (RuntimeBudgetScope.TURN, self._turns[identity.turn_id]),
            (RuntimeBudgetScope.TOOL, self._tool),
            (RuntimeBudgetScope.SUBAGENT, self._subagent),
        ):
            self._emit(
                identity,
                scope,
                "declared",
                RuntimeBudgetDecisionOutcome.ALLOW,
                "governance_snapshot_bound",
                governance.policy_id,
                state,
                getattr(governance.budgets, scope.value),
            )

    @property
    def run_id(self) -> str:
        return self._identity.run_id

    @property
    def identity(self) -> RuntimeRunIdentity:
        return self._identity

    @property
    def governance(self) -> RuntimeGovernanceEnvelope:
        return self._governance

    def consumption(
        self,
        scope: RuntimeBudgetScope,
    ) -> RuntimeBudgetConsumption:
        """返回当前 Run／Turn／Tool／Subagent 的不可变用量快照。"""

        normalized = RuntimeBudgetScope(scope)
        state = self._state(normalized)
        return RuntimeBudgetConsumption(
            scope=normalized,
            model_calls=state.model_calls,
            tokens=state.tokens,
            cost_microunits=state.cost_microunits,
            steps=state.steps,
            concurrency=state.concurrency,
        )

    def bind(
        self,
        identity: RuntimeRunIdentity,
        governance: RuntimeGovernanceEnvelope,
    ) -> None:
        if identity.run_id != self._identity.run_id:
            raise ValueError("budget account run_id 不匹配")
        if identity.owner != self._identity.owner:
            raise ValueError("同一 budget account 不能切换 owner")
        if governance.policy_id != self._governance.policy_id:
            self._deny(
                identity,
                RuntimeBudgetScope.RUN,
                "policy_bind",
                "governance_policy_changed",
                governance.policy_id,
                self._run,
                self._governance.budgets.run,
            )
        if _numeric_policy(governance.budgets.run) != _numeric_policy(
            self._governance.budgets.run
        ):
            self._deny(
                identity,
                RuntimeBudgetScope.RUN,
                "policy_bind",
                "run_budget_policy_changed",
                governance.policy_id,
                self._run,
                self._governance.budgets.run,
            )
        for scope in (RuntimeBudgetScope.TOOL, RuntimeBudgetScope.SUBAGENT):
            if _numeric_policy(getattr(governance.budgets, scope.value)) != (
                _numeric_policy(getattr(self._governance.budgets, scope.value))
            ):
                self._deny(
                    identity,
                    scope,
                    "policy_bind",
                    f"{scope.value}_budget_policy_changed",
                    governance.policy_id,
                    self._state(scope),
                    getattr(self._governance.budgets, scope.value),
                )
        if governance.access.to_dict() != self._governance.access.to_dict():
            self._deny(
                identity,
                RuntimeBudgetScope.RUN,
                "policy_bind",
                "access_scope_changed",
                governance.policy_id,
                self._run,
                self._governance.budgets.run,
            )
        existing = self._turn_governance.get(identity.turn_id)
        if existing is not None:
            if _numeric_policy(existing.budgets.turn) != _numeric_policy(
                governance.budgets.turn
            ):
                self._deny(
                    identity,
                    RuntimeBudgetScope.TURN,
                    "policy_bind",
                    "turn_budget_policy_changed",
                    governance.policy_id,
                    self._turns[identity.turn_id],
                    existing.budgets.turn,
                )
        else:
            started = self._monotonic()
            self._turns[identity.turn_id] = _UsageState(
                started_monotonic=started
            )
            self._turn_governance[identity.turn_id] = governance
            self._emit(
                identity,
                RuntimeBudgetScope.TURN,
                "declared",
                RuntimeBudgetDecisionOutcome.ALLOW,
                "turn_governance_bound",
                governance.policy_id,
                self._turns[identity.turn_id],
                governance.budgets.turn,
            )
        self._identity = identity
        self._governance = governance
        self._check_time(identity)

    def _state(self, scope: RuntimeBudgetScope) -> _UsageState:
        if scope is RuntimeBudgetScope.RUN:
            return self._run
        if scope is RuntimeBudgetScope.TURN:
            return self._turns[self._identity.turn_id]
        if scope is RuntimeBudgetScope.TOOL:
            return self._tool
        return self._subagent

    def _limit(self, scope: RuntimeBudgetScope) -> RuntimeBudgetLimit:
        return getattr(self._governance.budgets, scope.value)

    def _emit(
        self,
        identity: RuntimeRunIdentity,
        scope: RuntimeBudgetScope,
        operation: str,
        outcome: RuntimeBudgetDecisionOutcome,
        reason: str,
        resource: str,
        state: _UsageState,
        limit: RuntimeBudgetLimit,
    ) -> None:
        self._decision_sequence += 1
        decision = RuntimeBudgetDecision(
            decision_id=(
                f"budget:{identity.run_id}:{self._decision_sequence}"
            ),
            identity=identity,
            scope=scope,
            operation=operation,
            outcome=outcome,
            reason=reason,
            resource=str(resource or "")[:512],
            occurred_at=self._now(),
            governance_sha256=self._governance.content_sha256,
            model_calls=state.model_calls,
            tokens=state.tokens,
            cost_microunits=state.cost_microunits,
            steps=state.steps,
            concurrency=state.concurrency,
            limits=limit,
        )
        if self._sink is not None:
            self._sink.emit(decision)

    def _deny(
        self,
        identity: RuntimeRunIdentity,
        scope: RuntimeBudgetScope,
        operation: str,
        reason: str,
        resource: str,
        state: _UsageState,
        limit: RuntimeBudgetLimit,
    ) -> None:
        self._emit(
            identity,
            scope,
            operation,
            RuntimeBudgetDecisionOutcome.DENY,
            reason,
            resource,
            state,
            limit,
        )
        raise AgentRuntimeBudgetExceededError(
            f"Runtime {scope.value} 预算拒绝：{reason}",
            runtime_id="runtime-governance",
        )

    def _check_time(self, identity: RuntimeRunIdentity) -> None:
        current = self._monotonic()
        for scope in (RuntimeBudgetScope.RUN, RuntimeBudgetScope.TURN):
            state = self._state(scope)
            limit = self._limit(scope)
            elapsed_ms = max(0, int((current - state.started_monotonic) * 1000))
            if limit.time_limit_ms == 0 or elapsed_ms >= limit.time_limit_ms:
                self._deny(
                    identity,
                    scope,
                    "time_check",
                    "time_limit_exhausted",
                    "clock",
                    state,
                    limit,
                )

    def remaining_time_seconds(self) -> float:
        self._check_time(self._identity)
        current = self._monotonic()
        remaining = []
        for scope in (RuntimeBudgetScope.RUN, RuntimeBudgetScope.TURN):
            state = self._state(scope)
            limit = self._limit(scope)
            elapsed = current - state.started_monotonic
            remaining.append(max(0.001, limit.time_limit_ms / 1000 - elapsed))
        return min(remaining)

    def _check_subagent_time(
        self,
        identity: RuntimeRunIdentity,
        *,
        operation: str,
        resource: str,
    ) -> None:
        state = self._state(RuntimeBudgetScope.SUBAGENT)
        limit = self._limit(RuntimeBudgetScope.SUBAGENT)
        elapsed_ms = max(
            0,
            int((self._monotonic() - state.started_monotonic) * 1000),
        )
        if limit.time_limit_ms == 0 or elapsed_ms >= limit.time_limit_ms:
            self._deny(
                identity,
                RuntimeBudgetScope.SUBAGENT,
                operation,
                "time_limit_exhausted",
                resource,
                state,
                limit,
            )

    def subagent_remaining_time_seconds(self) -> float:
        self._check_time(self._identity)
        self._check_subagent_time(
            self._identity,
            operation="subagent_time_check",
            resource="clock",
        )
        state = self._state(RuntimeBudgetScope.SUBAGENT)
        limit = self._limit(RuntimeBudgetScope.SUBAGENT)
        elapsed = self._monotonic() - state.started_monotonic
        return min(
            self.remaining_time_seconds(),
            max(0.001, limit.time_limit_ms / 1000 - elapsed),
        )

    @staticmethod
    def _would_exceed(
        state: _UsageState,
        limit: RuntimeBudgetLimit,
        *,
        model_calls: int = 0,
        tokens: int = 0,
        cost_microunits: int = 0,
        steps: int = 0,
        concurrency: int = 0,
    ) -> str:
        checks = (
            ("model_call_limit", state.model_calls + model_calls, limit.model_call_limit),
            ("token_limit", state.tokens + tokens, limit.token_limit),
            (
                "cost_limit_microunits",
                state.cost_microunits + cost_microunits,
                limit.cost_limit_microunits,
            ),
            ("step_limit", state.steps + steps, limit.step_limit),
            (
                "concurrency_limit",
                state.concurrency + concurrency,
                limit.concurrency_limit,
            ),
        )
        return next((name for name, value, ceiling in checks if value > ceiling), "")

    @staticmethod
    def _consume(
        state: _UsageState,
        *,
        model_calls: int = 0,
        tokens: int = 0,
        cost_microunits: int = 0,
        steps: int = 0,
        concurrency: int = 0,
    ) -> None:
        state.model_calls += model_calls
        state.tokens += tokens
        state.cost_microunits += cost_microunits
        state.steps += steps
        state.concurrency += concurrency

    def reserve_model(self, model_id: str) -> None:
        identity = self._identity
        self._check_time(identity)
        normalized_model = str(model_id or "").strip() or "host-bound"
        for scope in (RuntimeBudgetScope.RUN, RuntimeBudgetScope.TURN):
            state = self._state(scope)
            limit = self._limit(scope)
            if (
                limit.allowed_model_ids
                and normalized_model not in limit.allowed_model_ids
            ):
                self._deny(
                    identity,
                    scope,
                    "model_reservation",
                    "model_scope_denied",
                    normalized_model,
                    state,
                    limit,
                )
            exceeded = self._would_exceed(
                state,
                limit,
                model_calls=1,
                steps=1,
            )
            if exceeded:
                self._deny(
                    identity,
                    scope,
                    "model_reservation",
                    exceeded,
                    normalized_model,
                    state,
                    limit,
                )
        for scope in (RuntimeBudgetScope.RUN, RuntimeBudgetScope.TURN):
            state = self._state(scope)
            self._consume(state, model_calls=1, steps=1)
            self._emit(
                identity,
                scope,
                "model_reservation",
                RuntimeBudgetDecisionOutcome.ALLOW,
                "within_limit",
                normalized_model,
                state,
                self._limit(scope),
            )

    def record_usage(self, usage: RuntimeUsage | None) -> None:
        if usage is None:
            return
        identity = self._identity
        for scope in (RuntimeBudgetScope.RUN, RuntimeBudgetScope.TURN):
            state = self._state(scope)
            limit = self._limit(scope)
            exceeded = self._would_exceed(
                state,
                limit,
                tokens=usage.total_tokens,
                cost_microunits=usage.cost_microunits,
            )
            if exceeded:
                self._deny(
                    identity,
                    scope,
                    "usage_recorded",
                    exceeded,
                    "model_usage",
                    state,
                    limit,
                )
        for scope in (RuntimeBudgetScope.RUN, RuntimeBudgetScope.TURN):
            state = self._state(scope)
            self._consume(
                state,
                tokens=usage.total_tokens,
                cost_microunits=usage.cost_microunits,
            )
            self._emit(
                identity,
                scope,
                "usage_recorded",
                RuntimeBudgetDecisionOutcome.ALLOW,
                "within_limit",
                "model_usage",
                state,
                self._limit(scope),
            )

    def reserve_tool(self, tool_name: str) -> RuntimeBudgetReservation:
        identity = self._identity
        self._check_time(identity)
        normalized = str(tool_name or "").strip()
        access = self._governance.access.find(
            RuntimeAccessKind.TOOL,
            f"tool:{normalized}",
            "execute",
        )
        if access is None:
            access = self._governance.access.find(
                RuntimeAccessKind.TOOL,
                normalized,
                "execute",
            )
        if access is None:
            self._deny(
                identity,
                RuntimeBudgetScope.TOOL,
                "tool_reservation",
                "tool_access_scope_denied",
                normalized,
                self._tool,
                self._governance.budgets.tool,
            )
        for scope in (
            RuntimeBudgetScope.RUN,
            RuntimeBudgetScope.TURN,
            RuntimeBudgetScope.TOOL,
        ):
            state = self._state(scope)
            limit = self._limit(scope)
            exceeded = self._would_exceed(
                state,
                limit,
                steps=1,
                concurrency=1,
            )
            if exceeded:
                self._deny(
                    identity,
                    scope,
                    "tool_reservation",
                    exceeded,
                    normalized,
                    state,
                    limit,
                )
        self._reservation_sequence += 1
        reservation_id = (
            f"budget-reservation:{identity.run_id}:{self._reservation_sequence}"
        )
        for scope in (
            RuntimeBudgetScope.RUN,
            RuntimeBudgetScope.TURN,
            RuntimeBudgetScope.TOOL,
        ):
            state = self._state(scope)
            self._consume(state, steps=1, concurrency=1)
            self._emit(
                identity,
                scope,
                "tool_reservation",
                RuntimeBudgetDecisionOutcome.ALLOW,
                "within_limit",
                normalized,
                state,
                self._limit(scope),
            )
        reservation = RuntimeBudgetReservation(
            reservation_id,
            RuntimeBudgetScope.TOOL,
            identity.turn_id,
        )
        self._reservations[reservation_id] = reservation
        return reservation

    def release(self, reservation: RuntimeBudgetReservation) -> None:
        existing = self._reservations.get(reservation.reservation_id)
        if existing is None:
            raise ValueError("budget reservation 不存在或已释放")
        if existing != reservation:
            raise ValueError("budget reservation 内容不匹配")
        if reservation.turn_id != self._identity.turn_id:
            raise ValueError("budget reservation 不属于当前 Turn")
        if reservation.scope is RuntimeBudgetScope.TOOL:
            scopes = (
                RuntimeBudgetScope.RUN,
                RuntimeBudgetScope.TURN,
                RuntimeBudgetScope.TOOL,
            )
        elif reservation.scope is RuntimeBudgetScope.SUBAGENT:
            scopes = (
                RuntimeBudgetScope.RUN,
                RuntimeBudgetScope.TURN,
                RuntimeBudgetScope.SUBAGENT,
            )
        else:
            raise ValueError("budget reservation scope 不支持释放")
        states = tuple(self._state(scope) for scope in scopes)
        if any(state.concurrency <= 0 for state in states):
            raise RuntimeError("budget concurrency 计数失衡")
        self._reservations.pop(reservation.reservation_id)
        for state in states:
            state.concurrency -= 1

    def tool_timeout_seconds(self) -> float:
        return min(
            self.remaining_time_seconds(),
            max(0.001, self._governance.budgets.tool.time_limit_ms / 1000),
        )

    def reserve_subagent(self, agent_id: str) -> RuntimeBudgetReservation:
        identity = self._identity
        self._check_time(identity)
        normalized = str(agent_id or "").strip() or "subagent"
        for scope in (
            RuntimeBudgetScope.RUN,
            RuntimeBudgetScope.TURN,
            RuntimeBudgetScope.SUBAGENT,
        ):
            state = self._state(scope)
            limit = self._limit(scope)
            exceeded = self._would_exceed(
                state,
                limit,
                steps=1,
                concurrency=1,
            )
            if exceeded:
                self._deny(
                    identity,
                    scope,
                    "subagent_reservation",
                    exceeded,
                    normalized,
                    state,
                    limit,
                )
        self._check_subagent_time(
            identity,
            operation="subagent_reservation",
            resource=normalized,
        )
        self._reservation_sequence += 1
        reservation_id = (
            f"budget-reservation:{identity.run_id}:{self._reservation_sequence}"
        )
        for scope in (
            RuntimeBudgetScope.RUN,
            RuntimeBudgetScope.TURN,
            RuntimeBudgetScope.SUBAGENT,
        ):
            state = self._state(scope)
            self._consume(state, steps=1, concurrency=1)
            self._emit(
                identity,
                scope,
                "subagent_reservation",
                RuntimeBudgetDecisionOutcome.ALLOW,
                "within_limit",
                normalized,
                state,
                self._limit(scope),
            )
        reservation = RuntimeBudgetReservation(
            reservation_id,
            RuntimeBudgetScope.SUBAGENT,
            identity.turn_id,
        )
        self._reservations[reservation_id] = reservation
        return reservation

    def record_subagent_usage(
        self,
        usage: RuntimeUsage,
        *,
        model_calls: int,
        tool_calls: int = 0,
    ) -> None:
        """把结构化 Worker 用量计入父 Run、Turn 与 Subagent 总预算。"""

        if not isinstance(usage, RuntimeUsage):
            raise TypeError("subagent usage 必须是 RuntimeUsage")
        if type(model_calls) is not int or model_calls < 0:
            raise ValueError("subagent model_calls 必须是非负整数")
        if type(tool_calls) is not int or tool_calls < 0:
            raise ValueError("subagent tool_calls 必须是非负整数")
        identity = self._identity
        self._check_time(identity)
        self._check_subagent_time(
            identity,
            operation="subagent_usage_recorded",
            resource="subagent_usage",
        )
        for scope in (
            RuntimeBudgetScope.RUN,
            RuntimeBudgetScope.TURN,
            RuntimeBudgetScope.SUBAGENT,
        ):
            state = self._state(scope)
            limit = self._limit(scope)
            exceeded = self._would_exceed(
                state,
                limit,
                model_calls=model_calls,
                tokens=usage.total_tokens,
                cost_microunits=usage.cost_microunits,
                steps=tool_calls,
            )
            if exceeded:
                self._deny(
                    identity,
                    scope,
                    "subagent_usage_recorded",
                    exceeded,
                    "subagent_usage",
                    state,
                    limit,
                )
        for scope in (
            RuntimeBudgetScope.RUN,
            RuntimeBudgetScope.TURN,
            RuntimeBudgetScope.SUBAGENT,
        ):
            state = self._state(scope)
            self._consume(
                state,
                model_calls=model_calls,
                tokens=usage.total_tokens,
                cost_microunits=usage.cost_microunits,
                steps=tool_calls,
            )
            self._emit(
                identity,
                scope,
                "subagent_usage_recorded",
                RuntimeBudgetDecisionOutcome.ALLOW,
                "within_limit",
                "subagent_usage",
                state,
                self._limit(scope),
            )


class RuntimeBudgetManager:
    """Runtime 实例内按 Run 复用账户，防止模型路由重试重置预算。"""

    def __init__(
        self,
        *,
        sink: RuntimeBudgetDecisionSink | None = None,
        max_accounts: int = 256,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if type(max_accounts) is not int or max_accounts <= 0:
            raise ValueError("max_accounts 必须是正整数")
        self._sink = sink
        self._max_accounts = max_accounts
        self._monotonic = monotonic
        self._now = now
        self._accounts: dict[str, RuntimeBudgetAccount] = {}
        self._order: list[str] = []

    def bind(
        self,
        identity: RuntimeRunIdentity,
        governance: RuntimeGovernanceEnvelope,
    ) -> RuntimeBudgetAccount:
        account = self._accounts.get(identity.run_id)
        if account is None:
            if len(self._accounts) >= self._max_accounts:
                stale = self._order.pop(0)
                self._accounts.pop(stale, None)
            account = RuntimeBudgetAccount(
                identity,
                governance,
                sink=self._sink,
                monotonic=self._monotonic,
                now=self._now,
            )
            self._accounts[identity.run_id] = account
            self._order.append(identity.run_id)
        else:
            account.bind(identity, governance)
        return account

    def discard(self, run_id: str) -> None:
        normalized = str(run_id or "").strip()
        self._accounts.pop(normalized, None)
        if normalized in self._order:
            self._order.remove(normalized)


__all__ = [
    "RuntimeBudgetAccount",
    "RuntimeBudgetConsumption",
    "RuntimeBudgetDecision",
    "RuntimeBudgetDecisionOutcome",
    "RuntimeBudgetDecisionSink",
    "RuntimeBudgetManager",
    "RuntimeBudgetReservation",
]
