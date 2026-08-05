"""主动任务共享的 Trigger、预算、权限与 Run 事实合同。

本模块只抽取跨入口的不可变合同和运行边界，不接管 Proactive、Scheduled
Workflow 或 Outbound 各自已经成熟的领域状态机。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from core.agent_runtime.contracts import (
    RuntimeActor,
    RuntimeActorType,
    RuntimeOwnerType,
    RuntimePrincipal,
    RuntimeRunIdentity,
)
from core.agent_runtime.governance import (
    RuntimeBudgetAccount,
    RuntimeBudgetDecision,
    RuntimeBudgetReservation,
)
from core.agent_runtime.governance_contracts import (
    RuntimeAccessEnvelope,
    RuntimeAccessGrant,
    RuntimeAccessKind,
    RuntimeBudgetEnvelope,
    RuntimeBudgetLimit,
    RuntimeBudgetScope,
    RuntimeGovernanceEnvelope,
)
from core.durable_tasks import RunTaskOwner, durable_cancel_status
from core.run_ledger.adapters import budget_decision_event
from core.run_ledger.contracts import (
    RunLedgerEventDraft,
    RunLedgerIdentity,
    RunTriggerBinding,
)
from core.run_ledger.persistence import SqlAlchemyRunEventLedger
from core.telemetry.contracts import TelemetryCorrelation
from core.tracing import RunHandle, RunTracer


TRIGGER_SCHEMA_VERSION = 1
TRIGGER_POLICY_ID = "trusted-trigger-v1"
_MAX_TRIGGER_TTL = timedelta(days=7)


class TriggerKind(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    EVENT = "event"
    HEARTBEAT = "heartbeat"


class TriggerPhase(StrEnum):
    RECEIVED = "received"
    EVALUATED = "evaluated"
    LEASE_ACQUIRED = "lease_acquired"
    RUNNING = "running"
    DELIVERY_COMMITTED = "delivery_committed"
    COMPLETED = "completed"


class TriggerContractError(ValueError):
    """Trigger 快照、TTL、主体或授权范围无效。"""


def _utc_aware(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise TriggerContractError("Trigger 时间必须包含时区")
    return current.astimezone(timezone.utc)


def _required(value: object, name: str, *, max_chars: int = 256) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > max_chars
        or any(ord(character) < 32 for character in normalized)
    ):
        raise TriggerContractError(f"{name} 无效")
    return normalized


def _sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TriggerContractError("Trigger 快照必须是有限 JSON") from exc


def _normalized_resources(values: Iterable[object], name: str) -> tuple[str, ...]:
    normalized = tuple(sorted({
        _required(value, name)
        for value in values
    }))
    if any("*" in value or "?" in value for value in normalized):
        raise TriggerContractError(f"{name} 不允许通配符")
    return normalized


def _limit_from_mapping(
    value: Mapping[str, Any],
    *,
    scope: RuntimeBudgetScope,
) -> RuntimeBudgetLimit:
    return RuntimeBudgetLimit(
        scope=scope,
        model_call_limit=int(value.get("model_call_limit", 0)),
        token_limit=int(value.get("token_limit", 0)),
        cost_limit_microunits=int(value.get("cost_limit_microunits", 0)),
        step_limit=int(value.get("step_limit", 0)),
        time_limit_ms=int(value.get("time_limit_ms", 0)),
        concurrency_limit=int(value.get("concurrency_limit", 0)),
        allowed_model_ids=tuple(value.get("allowed_model_ids") or ()),
    )


def _governance_from_mapping(value: Mapping[str, Any]) -> RuntimeGovernanceEnvelope:
    budgets_value = value.get("budgets")
    access_value = value.get("access")
    if not isinstance(budgets_value, Mapping) or not isinstance(access_value, Mapping):
        raise TriggerContractError("Trigger governance 快照无效")
    limits: dict[str, RuntimeBudgetLimit] = {}
    for name, scope in (
        ("run", RuntimeBudgetScope.RUN),
        ("turn", RuntimeBudgetScope.TURN),
        ("tool", RuntimeBudgetScope.TOOL),
        ("subagent", RuntimeBudgetScope.SUBAGENT),
    ):
        raw = budgets_value.get(name)
        if not isinstance(raw, Mapping):
            raise TriggerContractError(f"Trigger budget.{name} 快照无效")
        limits[name] = _limit_from_mapping(raw, scope=scope)
    raw_grants = access_value.get("grants")
    if not isinstance(raw_grants, list):
        raise TriggerContractError("Trigger access.grants 快照无效")
    grants: list[RuntimeAccessGrant] = []
    for raw in raw_grants:
        if not isinstance(raw, Mapping):
            raise TriggerContractError("Trigger access grant 快照无效")
        grants.append(RuntimeAccessGrant(
            kind=RuntimeAccessKind(str(raw.get("kind") or "")),
            resource=str(raw.get("resource") or ""),
            operations=tuple(raw.get("operations") or ()),
            authorization=str(raw.get("authorization") or ""),
        ))
    return RuntimeGovernanceEnvelope(
        policy_id=str(value.get("policy_id") or ""),
        budgets=RuntimeBudgetEnvelope(
            run=limits["run"],
            turn=limits["turn"],
            tool=limits["tool"],
            subagent=limits["subagent"],
        ),
        access=RuntimeAccessEnvelope(tuple(grants)),
        content_sha256=str(value.get("content_sha256") or ""),
    )


@dataclass(frozen=True, slots=True)
class TriggerEnvelope:
    """由受信入口生成、模型不可扩张的一次触发快照。"""

    trigger_id: str
    kind: TriggerKind
    source_type: str
    source_ref_sha256: str
    idempotency_sha256: str
    principal: RuntimePrincipal
    occurred_at: datetime
    expires_at: datetime
    governance: RuntimeGovernanceEnvelope
    schema_version: int = TRIGGER_SCHEMA_VERSION
    content_sha256: str = field(default="")

    def __post_init__(self) -> None:
        if self.schema_version != TRIGGER_SCHEMA_VERSION:
            raise TriggerContractError("Trigger schema_version 不受支持")
        object.__setattr__(
            self,
            "trigger_id",
            _required(self.trigger_id, "trigger_id", max_chars=160),
        )
        try:
            kind = TriggerKind(self.kind)
        except ValueError as exc:
            raise TriggerContractError("Trigger kind 无效") from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "source_type",
            _required(self.source_type, "source_type", max_chars=64),
        )
        for name in ("source_ref_sha256", "idempotency_sha256"):
            digest = str(getattr(self, name) or "").strip().lower()
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise TriggerContractError(f"Trigger {name} 无效")
            object.__setattr__(self, name, digest)
        if not isinstance(self.principal, RuntimePrincipal):
            raise TriggerContractError("Trigger principal 无效")
        occurred_at = _utc_aware(self.occurred_at)
        expires_at = _utc_aware(self.expires_at)
        if expires_at <= occurred_at or expires_at - occurred_at > _MAX_TRIGGER_TTL:
            raise TriggerContractError("Trigger TTL 无效")
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "expires_at", expires_at)
        if not isinstance(self.governance, RuntimeGovernanceEnvelope):
            raise TriggerContractError("Trigger governance 无效")
        payload = self._content_payload()
        digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        declared = str(self.content_sha256 or "").strip().lower()
        if declared and declared != digest:
            raise TriggerContractError("Trigger content_sha256 与内容不一致")
        object.__setattr__(self, "content_sha256", digest)

    def _content_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trigger_id": self.trigger_id,
            "kind": self.kind.value,
            "source_type": self.source_type,
            "source_ref_sha256": self.source_ref_sha256,
            "idempotency_sha256": self.idempotency_sha256,
            "principal": {
                "platform": self.principal.platform,
                "owner_type": self.principal.owner_type.value,
                "owner_id": self.principal.owner_id,
            },
            "occurred_at": self.occurred_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "governance": self.governance.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_payload()
        payload["content_sha256"] = self.content_sha256
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TriggerEnvelope":
        if not isinstance(value, Mapping):
            raise TriggerContractError("Trigger 快照必须是对象")
        principal_value = value.get("principal")
        governance_value = value.get("governance")
        if not isinstance(principal_value, Mapping) or not isinstance(
            governance_value,
            Mapping,
        ):
            raise TriggerContractError("Trigger principal/governance 快照缺失")
        try:
            occurred_at = datetime.fromisoformat(str(value.get("occurred_at") or ""))
            expires_at = datetime.fromisoformat(str(value.get("expires_at") or ""))
        except ValueError as exc:
            raise TriggerContractError("Trigger 时间快照无法解析") from exc
        return cls(
            schema_version=int(value.get("schema_version") or 0),
            trigger_id=str(value.get("trigger_id") or ""),
            kind=TriggerKind(str(value.get("kind") or "")),
            source_type=str(value.get("source_type") or ""),
            source_ref_sha256=str(value.get("source_ref_sha256") or ""),
            idempotency_sha256=str(value.get("idempotency_sha256") or ""),
            principal=RuntimePrincipal(
                platform=str(principal_value.get("platform") or ""),
                owner_type=RuntimeOwnerType(
                    str(principal_value.get("owner_type") or "")
                ),
                owner_id=str(principal_value.get("owner_id") or ""),
            ),
            occurred_at=occurred_at,
            expires_at=expires_at,
            governance=_governance_from_mapping(governance_value),
            content_sha256=str(value.get("content_sha256") or ""),
        )

    def assert_active(self, *, now: datetime | None = None) -> None:
        current = _utc_aware(now)
        if current < self.occurred_at - timedelta(minutes=5):
            raise TriggerContractError("Trigger 尚未生效")
        if current >= self.expires_at:
            raise TriggerContractError("Trigger 已过期")

    def assert_owner(self, principal: RuntimePrincipal) -> None:
        if principal != self.principal:
            raise TriggerContractError("主动任务不得切换资源 owner")

    def assert_tool(self, tool_name: str) -> None:
        normalized = _required(tool_name, "tool_name")
        if self.governance.access.find(
            RuntimeAccessKind.TOOL,
            f"tool:{normalized}",
            "execute",
        ) is None:
            raise TriggerContractError(f"Trigger 未授权工具：{normalized}")

    def assert_delivery(self, endpoint_key: str) -> None:
        normalized = _required(endpoint_key, "endpoint_key")
        if self.governance.access.find(
            RuntimeAccessKind.NETWORK,
            f"delivery:{normalized}",
            "deliver",
        ) is None:
            raise TriggerContractError(f"Trigger 未授权投递端点：{normalized}")

    def assert_subagent(self, agent_id: str) -> None:
        normalized = _required(agent_id, "agent_id")
        if self.governance.access.find(
            RuntimeAccessKind.TOOL,
            f"subagent:{normalized}",
            "spawn",
        ) is None:
            raise TriggerContractError(f"Trigger 未授权子 Agent：{normalized}")

    def safe_snapshot(self, *, run_id: str = "") -> dict[str, Any]:
        """返回可进入业务证据、但不包含 owner 或原始来源的快照。"""

        return {
            "schema_version": self.schema_version,
            "trigger_id": self.trigger_id,
            "trigger_type": self.kind.value,
            "source_type": self.source_type,
            "source_ref_sha256": self.source_ref_sha256,
            "idempotency_sha256": self.idempotency_sha256,
            "owner_sha256": _sha256_text(self.principal.canonical_id),
            "occurred_at": self.occurred_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "governance_sha256": self.governance.content_sha256,
            "trigger_sha256": self.content_sha256,
            "run_id": str(run_id or "")[:160],
        }

    def run_binding(self) -> RunTriggerBinding:
        """生成只能由受信入口继续传递的类型化 Run 绑定。"""

        return RunTriggerBinding(
            trigger_id=self.trigger_id,
            trigger_type=self.kind.value,
            trigger_sha256=self.content_sha256,
            governance_sha256=self.governance.content_sha256,
        )

    def tool_constraint(
        self,
        allowed_tool_names: Iterable[str],
    ) -> "TriggerToolConstraint":
        tools = frozenset(
            _normalized_resources(allowed_tool_names, "allowed_tool")
        )
        for tool_name in tools:
            self.assert_tool(tool_name)
        return TriggerToolConstraint(
            binding=self.run_binding(),
            principal=self.principal,
            allowed_tool_names=tools,
        )


@dataclass(frozen=True, slots=True)
class TriggerToolConstraint:
    """受信 Trigger 对一次模型 Run 的精确、只减不增工具约束。"""

    binding: RunTriggerBinding
    principal: RuntimePrincipal
    allowed_tool_names: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.binding, RunTriggerBinding):
            raise TriggerContractError("Trigger Run 绑定无效")
        if not isinstance(self.principal, RuntimePrincipal):
            raise TriggerContractError("Trigger 工具约束主体无效")
        normalized = frozenset(
            _normalized_resources(
                self.allowed_tool_names,
                "allowed_tool",
            )
        )
        object.__setattr__(self, "allowed_tool_names", normalized)

    def assert_owner(self, principal: RuntimePrincipal) -> None:
        if principal != self.principal:
            raise TriggerContractError("Trigger 模型 Run 不得切换资源 owner")


def build_trigger_envelope(
    *,
    kind: TriggerKind,
    source_type: str,
    source_ref: str,
    idempotency_key: str,
    principal: RuntimePrincipal,
    allowed_tools: Iterable[str] = (),
    delivery_endpoints: Iterable[str] = (),
    allowed_subagents: Iterable[str] = (),
    max_model_calls: int,
    max_steps: int,
    timeout_seconds: int,
    max_subagents: int = 0,
    occurred_at: datetime | None = None,
    ttl_seconds: int | None = None,
) -> TriggerEnvelope:
    """由受信入口生成 Trigger；所有资源均为精确白名单。"""

    normalized_kind = TriggerKind(kind)
    normalized_source_type = _required(source_type, "source_type", max_chars=64)
    normalized_source_ref = _required(source_ref, "source_ref", max_chars=1024)
    normalized_idempotency = _required(
        idempotency_key,
        "idempotency_key",
        max_chars=1024,
    )
    tools = _normalized_resources(allowed_tools, "allowed_tool")
    endpoints = _normalized_resources(delivery_endpoints, "delivery_endpoint")
    subagents = _normalized_resources(allowed_subagents, "allowed_subagent")
    if type(max_model_calls) is not int or max_model_calls < 0:
        raise TriggerContractError("max_model_calls 必须是非负整数")
    if type(max_steps) is not int or max_steps <= 0:
        raise TriggerContractError("max_steps 必须是正整数")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 86_400:
        raise TriggerContractError("timeout_seconds 必须在 1-86400 之间")
    if type(max_subagents) is not int or max_subagents < 0:
        raise TriggerContractError("max_subagents 必须是非负整数")
    if len(subagents) > max_subagents:
        raise TriggerContractError("子 Agent 白名单超过并发上限")

    access_grants: list[RuntimeAccessGrant] = []
    for tool_name in tools:
        access_grants.append(RuntimeAccessGrant(
            RuntimeAccessKind.TOOL,
            f"tool:{tool_name}",
            ("execute",),
            TRIGGER_POLICY_ID,
        ))
    for endpoint in endpoints:
        access_grants.extend((
            RuntimeAccessGrant(
                RuntimeAccessKind.NETWORK,
                f"delivery:{endpoint}",
                ("deliver",),
                TRIGGER_POLICY_ID,
            ),
            RuntimeAccessGrant(
                RuntimeAccessKind.TOOL,
                f"tool:delivery:{endpoint}",
                ("execute",),
                TRIGGER_POLICY_ID,
            ),
        ))
    for agent_id in subagents:
        access_grants.append(RuntimeAccessGrant(
            RuntimeAccessKind.TOOL,
            f"subagent:{agent_id}",
            ("spawn",),
            TRIGGER_POLICY_ID,
        ))

    time_limit_ms = timeout_seconds * 1000
    concurrency = max(1, len(tools) + len(endpoints) + max_subagents)
    token_limit = 1_000_000 if max_model_calls else 0
    cost_limit = 1_000_000_000 if max_model_calls else 0
    governance = RuntimeGovernanceEnvelope(
        policy_id=TRIGGER_POLICY_ID,
        budgets=RuntimeBudgetEnvelope(
            run=RuntimeBudgetLimit(
                RuntimeBudgetScope.RUN,
                model_call_limit=max_model_calls,
                token_limit=token_limit,
                cost_limit_microunits=cost_limit,
                step_limit=max_steps,
                time_limit_ms=time_limit_ms,
                concurrency_limit=concurrency,
            ),
            turn=RuntimeBudgetLimit(
                RuntimeBudgetScope.TURN,
                model_call_limit=max_model_calls,
                token_limit=token_limit,
                cost_limit_microunits=cost_limit,
                step_limit=max_steps,
                time_limit_ms=time_limit_ms,
                concurrency_limit=concurrency,
            ),
            tool=RuntimeBudgetLimit(
                RuntimeBudgetScope.TOOL,
                model_call_limit=0,
                token_limit=0,
                cost_limit_microunits=0,
                step_limit=max_steps,
                time_limit_ms=time_limit_ms,
                concurrency_limit=concurrency,
            ),
            subagent=RuntimeBudgetLimit(
                RuntimeBudgetScope.SUBAGENT,
                model_call_limit=max_model_calls if max_subagents else 0,
                token_limit=token_limit if max_subagents else 0,
                cost_limit_microunits=cost_limit if max_subagents else 0,
                step_limit=max_steps if max_subagents else 0,
                time_limit_ms=time_limit_ms if max_subagents else 0,
                concurrency_limit=max_subagents,
            ),
        ),
        access=RuntimeAccessEnvelope(tuple(access_grants)),
    )
    current = _utc_aware(occurred_at)
    effective_ttl = ttl_seconds if ttl_seconds is not None else timeout_seconds
    if type(effective_ttl) is not int or not 1 <= effective_ttl <= int(
        _MAX_TRIGGER_TTL.total_seconds()
    ):
        raise TriggerContractError("ttl_seconds 超出允许范围")
    identity_payload = {
        "kind": normalized_kind.value,
        "source_type": normalized_source_type,
        "source_ref_sha256": _sha256_text(normalized_source_ref),
        "idempotency_sha256": _sha256_text(normalized_idempotency),
        "owner": principal.canonical_id,
        "occurred_at": current.isoformat(),
        "governance_sha256": governance.content_sha256,
    }
    trigger_digest = hashlib.sha256(
        _canonical_json(identity_payload).encode("utf-8")
    ).hexdigest()
    return TriggerEnvelope(
        trigger_id=f"trigger-{trigger_digest[:32]}",
        kind=normalized_kind,
        source_type=normalized_source_type,
        source_ref_sha256=identity_payload["source_ref_sha256"],
        idempotency_sha256=identity_payload["idempotency_sha256"],
        principal=principal,
        occurred_at=current,
        expires_at=current + timedelta(seconds=effective_ttl),
        governance=governance,
    )


def _trigger_phase_event(
    *,
    handle: RunHandle,
    envelope: TriggerEnvelope,
    phase: TriggerPhase,
    outcome_status: str = "",
    occurred_at: datetime | None = None,
) -> RunLedgerEventDraft:
    event_digest = hashlib.sha256(
        f"{handle.run_id}|{phase.value}".encode("utf-8")
    ).hexdigest()[:24]
    principal = envelope.principal
    return RunLedgerEventDraft(
        event_id=f"trigger-phase-{event_digest}",
        run_id=handle.run_id,
        event_type="trigger.phase_changed",
        occurred_at=_utc_aware(occurred_at),
        source="trigger.runtime",
        correlation=TelemetryCorrelation(
            request_id=envelope.trigger_id,
            trace_id=handle.trace_id,
            run_id=handle.run_id,
        ),
        identity=RunLedgerIdentity(
            actor_type=RuntimeActorType.SYSTEM.value,
            actor_id=f"trigger:{envelope.kind.value}",
            owner_platform=principal.platform,
            owner_type=principal.owner_type.value,
            owner_id=principal.owner_id,
        ),
        payload={
            "phase": phase.value,
            "trigger_id": envelope.trigger_id,
            "trigger_type": envelope.kind.value,
            "trigger_sha256": envelope.content_sha256,
            "source_type": envelope.source_type,
            "outcome_status": str(outcome_status or "")[:64],
        },
    )


class BufferedTriggerLedgerSink:
    """业务事务结束后一次性写入 Trigger phase 与预算决定。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        handle: RunHandle,
        envelope: TriggerEnvelope,
    ) -> None:
        self._session_factory = session_factory
        self._handle = handle
        self._envelope = envelope
        self._events: list[RunLedgerEventDraft] = []
        self._flushed = False

    def emit(self, decision: RuntimeBudgetDecision) -> None:
        if self._flushed:
            raise RuntimeError("Trigger Ledger buffer 已提交")
        self._events.append(budget_decision_event(decision))

    def phase(
        self,
        phase: TriggerPhase,
        *,
        outcome_status: str = "",
        occurred_at: datetime | None = None,
    ) -> None:
        if self._flushed:
            raise RuntimeError("Trigger Ledger buffer 已提交")
        self._events.append(_trigger_phase_event(
            handle=self._handle,
            envelope=self._envelope,
            phase=phase,
            outcome_status=outcome_status,
            occurred_at=occurred_at,
        ))

    def flush(self) -> None:
        if self._flushed:
            return
        db = self._session_factory()
        try:
            ledger = SqlAlchemyRunEventLedger(db)
            for event in self._events:
                ledger.append(event)
            db.commit()
            self._flushed = True
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


@dataclass(slots=True)
class TriggerRunContext:
    envelope: TriggerEnvelope
    handle: RunHandle
    identity: RuntimeRunIdentity
    budget: RuntimeBudgetAccount
    ledger: BufferedTriggerLedgerSink
    owner: RunTaskOwner
    session_factory: Callable[[], Any]
    _owner_stopped: bool = False
    _completion_status: str = ""
    _completion_queued: bool = False
    _ledger_finished: bool = False
    _finished: bool = False

    @classmethod
    async def start(
        cls,
        envelope: TriggerEnvelope,
        *,
        session_factory: Callable[[], Any],
        evaluated_status: str = "allowed",
    ) -> "TriggerRunContext":
        envelope.assert_active()
        principal = envelope.principal
        timeout_seconds = max(
            1,
            int((envelope.expires_at - datetime.now(timezone.utc)).total_seconds()),
        )
        handle = RunTracer.start_run(
            session_id=f"trigger:{envelope.source_ref_sha256[:24]}",
            user_id=(
                principal.owner_id
                if principal.owner_type is RuntimeOwnerType.USER
                else ""
            ),
            chat_type=(
                "group"
                if principal.owner_type is RuntimeOwnerType.GROUP
                else "private"
            ),
            group_id=(
                principal.owner_id
                if principal.owner_type is RuntimeOwnerType.GROUP
                else ""
            ),
            run_type=f"{envelope.source_type}_trigger",
            prompt_mode="trigger",
            input_preview=envelope.trigger_id,
            meta={
                "source": envelope.source_type,
                "message_id": envelope.trigger_id,
                "workflow_idempotency_key": envelope.idempotency_sha256,
                "run_timeout_seconds": timeout_seconds,
                "platform": principal.platform,
                "_trigger_run_binding": envelope.run_binding(),
            },
            session_factory=session_factory,
        )
        identity = RuntimeRunIdentity(
            run_id=handle.run_id,
            turn_id=f"turn:{handle.run_id}",
            correlation_id=handle.trace_id,
            actor=RuntimeActor(
                RuntimeActorType.SYSTEM,
                f"trigger:{envelope.kind.value}",
            ),
            owner=principal,
        )
        ledger = BufferedTriggerLedgerSink(
            session_factory=session_factory,
            handle=handle,
            envelope=envelope,
        )
        ledger.phase(TriggerPhase.RECEIVED, occurred_at=envelope.occurred_at)
        ledger.phase(
            TriggerPhase.EVALUATED,
            outcome_status=evaluated_status,
        )
        ledger.phase(TriggerPhase.LEASE_ACQUIRED)
        ledger.phase(TriggerPhase.RUNNING)
        budget = RuntimeBudgetAccount(
            identity,
            envelope.governance,
            sink=ledger,
        )
        owner = RunTaskOwner(
            handle.task_lease,
            session_factory=session_factory,
        )
        await owner.start()
        return cls(
            envelope=envelope,
            handle=handle,
            identity=identity,
            budget=budget,
            ledger=ledger,
            owner=owner,
            session_factory=session_factory,
        )

    @property
    def run_id(self) -> str:
        return self.handle.run_id

    def reserve_model(self, operation: str) -> None:
        self.envelope.assert_active()
        self.budget.reserve_model(_required(operation, "model_operation"))

    def reserve_tool(self, tool_name: str) -> RuntimeBudgetReservation:
        self.envelope.assert_active()
        self.envelope.assert_tool(tool_name)
        return self.budget.reserve_tool(tool_name)

    def reserve_delivery(self, endpoint_key: str) -> RuntimeBudgetReservation:
        self.envelope.assert_active()
        self.envelope.assert_delivery(endpoint_key)
        return self.budget.reserve_tool(f"delivery:{endpoint_key}")

    def reserve_subagent(self, agent_id: str) -> RuntimeBudgetReservation:
        self.envelope.assert_active()
        self.envelope.assert_subagent(agent_id)
        return self.budget.reserve_subagent(agent_id)

    def release(self, reservation: RuntimeBudgetReservation) -> None:
        self.budget.release(reservation)

    def mark_delivery_committed(self, status: str) -> None:
        self.ledger.phase(
            TriggerPhase.DELIVERY_COMMITTED,
            outcome_status=str(status or "")[:64],
        )

    async def finish(
        self,
        *,
        status: str,
        output: Mapping[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        if self._finished:
            return
        normalized_status = str(status or "success")
        if (
            self._completion_status
            and self._completion_status != normalized_status
        ):
            raise TriggerContractError("Trigger Run 收尾重试不得改变终态")
        self._completion_status = normalized_status
        if not self._owner_stopped:
            await self.owner.stop()
            self._owner_stopped = True
        if not self._ledger_finished:
            if not self._completion_queued:
                self.ledger.phase(
                    TriggerPhase.COMPLETED,
                    outcome_status=normalized_status,
                )
                self._completion_queued = True
            self.ledger.flush()
            self._ledger_finished = True
        safe_output = {
            key: value
            for key, value in dict(output or {}).items()
            if key in {
                "status",
                "error_type",
                "reason_code",
                "forced",
                "deduplicated",
                "log_id",
                "run_id",
                "outbox_id",
            }
            and isinstance(value, (str, int, bool, type(None)))
        }
        RunTracer.finish_run(
            self.run_id,
            task_lease=self.owner.lease,
            status=normalized_status,
            output_preview=safe_output,
            error=type(error).__name__ if error is not None else "",
            meta={
                "source": self.envelope.source_type,
                "trigger_type": self.envelope.kind.value,
                "trigger_sha256": self.envelope.content_sha256,
                "governance_sha256": self.envelope.governance.content_sha256,
            },
            session_factory=self.session_factory,
        )
        self._finished = True


def trigger_run_status(
    result: Mapping[str, Any] | None,
    error: BaseException | None = None,
) -> str:
    if error is not None:
        if isinstance(error, TimeoutError):
            return "timed_out"
        if isinstance(error, BaseException) and type(error).__name__ == "CancelledError":
            return durable_cancel_status(error)
        return "failed"
    status = str((result or {}).get("status") or "")
    if status in {"ambiguous", "lease_lost", "skipped_ambiguous"}:
        return "ambiguous"
    if status in {
        "state_error",
        "judge_error",
        "generation_error",
        "research_blocked",
    }:
        return "failed"
    return "success"


__all__ = [
    "BufferedTriggerLedgerSink",
    "TRIGGER_POLICY_ID",
    "TRIGGER_SCHEMA_VERSION",
    "TriggerContractError",
    "TriggerEnvelope",
    "TriggerKind",
    "TriggerPhase",
    "TriggerRunContext",
    "TriggerToolConstraint",
    "build_trigger_envelope",
    "trigger_run_status",
]
