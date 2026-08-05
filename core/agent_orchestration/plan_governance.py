"""动态多 Agent 计划的预览、批准、冻结与追加式修订。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Protocol, runtime_checkable
from uuid import uuid4

from core.agent_orchestration.contracts import (
    AgentOrchestrationApproval,
    AgentOrchestrationError,
    AgentOrchestrationFreeze,
    AgentOrchestrationPlan,
    AgentOrchestrationRequest,
    canonical_json_bytes,
)
from core.agent_runtime.contracts import RuntimePrincipal, RuntimeRunIdentity


class AgentPlanRevisionState(str, Enum):
    PREVIEWED = "previewed"
    APPROVED = "approved"
    FROZEN = "frozen"
    SUPERSEDED = "superseded"


class AgentPlanEventKind(str, Enum):
    PREVIEWED = "previewed"
    APPROVED = "approved"
    FROZEN = "frozen"
    REVISION_SUPERSEDED = "revision_superseded"


def _identifier(value: object, name: str, *, max_chars: int = 160) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > max_chars
        or any(character.isspace() for character in normalized)
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError(f"{name} 无效")
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


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} 必须包含时区")
    return value


@dataclass(frozen=True, slots=True)
class AgentPlanRepairSummary:
    """两份不可变计划之间的可审计结构差异，不保存任务正文。"""

    reason_code: str
    parent_plan_sha256: str
    added_task_ids: tuple[str, ...]
    removed_task_ids: tuple[str, ...]
    changed_task_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        reason = str(self.reason_code or "").strip()
        parent = str(self.parent_plan_sha256 or "").strip()
        groups: list[set[str]] = []
        for name in (
            "added_task_ids",
            "removed_task_ids",
            "changed_task_ids",
        ):
            values = tuple(sorted(
                _identifier(item, f"repair {name} task_id", max_chars=128)
                for item in tuple(getattr(self, name))
            ))
            if len(values) != len(set(values)):
                raise ValueError(f"repair {name} 含重复 task_id")
            object.__setattr__(self, name, values)
            groups.append(set(values))
        if any(
            groups[left] & groups[right]
            for left in range(3)
            for right in range(left + 1, 3)
        ):
            raise ValueError("repair task diff 分类不能重叠")
        if not reason and not parent:
            if any(groups):
                raise ValueError("首版计划不能声明 repair task diff")
            object.__setattr__(self, "reason_code", "")
            object.__setattr__(self, "parent_plan_sha256", "")
            return
        object.__setattr__(
            self,
            "reason_code",
            _identifier(reason, "repair reason_code", max_chars=128),
        )
        object.__setattr__(
            self,
            "parent_plan_sha256",
            _sha256(parent, "repair parent_plan_sha256"),
        )

    @property
    def changed(self) -> bool:
        return bool(self.parent_plan_sha256)

    def to_dict(self) -> dict[str, object]:
        return {
            "reason_code": self.reason_code,
            "parent_plan_sha256": self.parent_plan_sha256,
            "added_task_ids": list(self.added_task_ids),
            "removed_task_ids": list(self.removed_task_ids),
            "changed_task_ids": list(self.changed_task_ids),
        }


@dataclass(frozen=True, slots=True)
class AgentPlanRevisionRecord:
    preview_id: str
    plan: AgentOrchestrationPlan
    owner: RuntimePrincipal
    source_run_id: str
    source_turn_id: str
    proposed_by: str
    proposed_at: datetime
    repair: AgentPlanRepairSummary

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "preview_id",
            _identifier(self.preview_id, "preview_id"),
        )
        if not isinstance(self.plan, AgentOrchestrationPlan):
            raise ValueError("plan revision plan 无效")
        if not isinstance(self.owner, RuntimePrincipal):
            raise ValueError("plan revision owner 无效")
        object.__setattr__(
            self,
            "source_run_id",
            _identifier(self.source_run_id, "source_run_id"),
        )
        object.__setattr__(
            self,
            "source_turn_id",
            _identifier(self.source_turn_id, "source_turn_id"),
        )
        object.__setattr__(
            self,
            "proposed_by",
            _identifier(self.proposed_by, "proposed_by"),
        )
        _aware(self.proposed_at, "proposed_at")
        if not isinstance(self.repair, AgentPlanRepairSummary):
            raise ValueError("plan revision repair 无效")
        if self.plan.revision == 1:
            if self.repair.parent_plan_sha256 or self.repair.reason_code:
                raise ValueError("首版计划不能引用父修订")
        elif not self.repair.parent_plan_sha256 or not self.repair.reason_code:
            raise ValueError("后续计划修订必须声明 repair 原因和父摘要")


@dataclass(frozen=True, slots=True)
class AgentPlanAuditEvent:
    event_id: str
    owner: RuntimePrincipal
    plan_id: str
    plan_revision: int
    plan_sha256: str
    sequence: int
    kind: AgentPlanEventKind
    actor_id: str
    proof_id: str
    related_plan_revision: int
    related_plan_sha256: str
    occurred_at: datetime
    previous_event_sha256: str = ""
    event_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _identifier(self.event_id, "plan event_id"))
        if not isinstance(self.owner, RuntimePrincipal):
            raise ValueError("plan event owner 无效")
        object.__setattr__(self, "plan_id", _identifier(self.plan_id, "plan event plan_id"))
        if type(self.plan_revision) is not int or self.plan_revision < 1:
            raise ValueError("plan event revision 必须是正整数")
        object.__setattr__(self, "plan_sha256", _sha256(self.plan_sha256, "plan event plan_sha256"))
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("plan event sequence 必须是正整数")
        kind = AgentPlanEventKind(self.kind)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "actor_id", _identifier(self.actor_id, "plan event actor_id"))
        proof = str(self.proof_id or "").strip()
        if kind is AgentPlanEventKind.PREVIEWED:
            if proof:
                raise ValueError("preview event 不能携带 proof_id")
        else:
            proof = _identifier(proof, "plan event proof_id")
        object.__setattr__(self, "proof_id", proof)
        if type(self.related_plan_revision) is not int or self.related_plan_revision < 0:
            raise ValueError("related_plan_revision 必须是非负整数")
        related_sha = _sha256(
            self.related_plan_sha256,
            "related_plan_sha256",
            allow_empty=True,
        )
        if (self.related_plan_revision == 0) != (not related_sha):
            raise ValueError("related plan revision 与摘要必须同时存在或同时为空")
        object.__setattr__(self, "related_plan_sha256", related_sha)
        _aware(self.occurred_at, "plan event occurred_at")
        previous = _sha256(
            self.previous_event_sha256,
            "previous_event_sha256",
            allow_empty=True,
        )
        if (self.sequence == 1) != (not previous):
            raise ValueError("plan event previous digest 与 sequence 不一致")
        object.__setattr__(self, "previous_event_sha256", previous)
        digest = hashlib.sha256(canonical_json_bytes(
            self.to_dict(include_hash=False)
        )).hexdigest()
        declared = str(self.event_sha256 or "").strip().lower()
        if declared and _sha256(declared, "plan event_sha256") != digest:
            raise ValueError("plan event_sha256 与内容不一致")
        object.__setattr__(self, "event_sha256", digest)

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "event_id": self.event_id,
            "owner": self.owner.canonical_id,
            "plan_id": self.plan_id,
            "plan_revision": self.plan_revision,
            "plan_sha256": self.plan_sha256,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "actor_id": self.actor_id,
            "proof_id": self.proof_id,
            "related_plan_revision": self.related_plan_revision,
            "related_plan_sha256": self.related_plan_sha256,
            "occurred_at": self.occurred_at.isoformat(),
            "previous_event_sha256": self.previous_event_sha256,
        }
        if include_hash:
            payload["event_sha256"] = self.event_sha256
        return payload


@dataclass(frozen=True, slots=True)
class AgentPlanRevisionView:
    record: AgentPlanRevisionRecord
    state: AgentPlanRevisionState
    approval: AgentOrchestrationApproval | None
    freeze: AgentOrchestrationFreeze | None
    latest_event_sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.record, AgentPlanRevisionRecord):
            raise ValueError("plan revision view record 无效")
        state = AgentPlanRevisionState(self.state)
        object.__setattr__(self, "state", state)
        if type(self.latest_event_sequence) is not int or self.latest_event_sequence < 1:
            raise ValueError("latest_event_sequence 必须是正整数")
        if state is AgentPlanRevisionState.PREVIEWED:
            if self.approval is not None or self.freeze is not None:
                raise ValueError("previewed revision 不能有批准或冻结证明")
        elif self.approval is None:
            raise ValueError("批准后的 revision 必须有 approval")
        if state is AgentPlanRevisionState.FROZEN and self.freeze is None:
            raise ValueError("frozen revision 必须有 freeze")
        if self.freeze is not None and state not in {
            AgentPlanRevisionState.FROZEN,
            AgentPlanRevisionState.SUPERSEDED,
        }:
            raise ValueError("freeze 只能属于 frozen 或 superseded revision")


@runtime_checkable
class AgentPlanRepository(Protocol):
    def add_revision(
        self,
        record: AgentPlanRevisionRecord,
        event: AgentPlanAuditEvent,
        *,
        expected_sequence: int,
    ) -> None: ...

    def append_events(
        self,
        events: tuple[AgentPlanAuditEvent, ...],
        *,
        expected_sequence: int,
    ) -> None: ...

    def get_revision(
        self,
        plan_id: str,
        revision: int,
        *,
        owner_id: str,
    ) -> AgentPlanRevisionRecord | None: ...

    def latest_revision(
        self,
        plan_id: str,
        *,
        owner_id: str,
    ) -> AgentPlanRevisionRecord | None: ...

    def list_events(
        self,
        plan_id: str,
        *,
        owner_id: str,
    ) -> tuple[AgentPlanAuditEvent, ...]: ...


class InMemoryAgentPlanRepository:
    """严格单调的测试 Repository；生产使用 SQL 实现。"""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, int], AgentPlanRevisionRecord] = {}
        self._events: dict[tuple[str, str], list[AgentPlanAuditEvent]] = {}

    @staticmethod
    def _family(owner_id: str, plan_id: str) -> tuple[str, str]:
        return (str(owner_id), str(plan_id))

    def add_revision(
        self,
        record: AgentPlanRevisionRecord,
        event: AgentPlanAuditEvent,
        *,
        expected_sequence: int,
    ) -> None:
        family = self._family(record.owner.canonical_id, record.plan.plan_id)
        key = (*family, record.plan.revision)
        if key in self._records:
            raise AgentOrchestrationError(
                "plan_revision_conflict",
                "计划 revision 已绑定不同或重复内容",
            )
        self._append_checked(family, (event,), expected_sequence)
        self._records[key] = record

    def append_events(
        self,
        events: tuple[AgentPlanAuditEvent, ...],
        *,
        expected_sequence: int,
    ) -> None:
        if not events:
            raise ValueError("plan events 不能为空")
        first = events[0]
        family = self._family(first.owner.canonical_id, first.plan_id)
        self._append_checked(family, events, expected_sequence)

    def _append_checked(
        self,
        family: tuple[str, str],
        events: tuple[AgentPlanAuditEvent, ...],
        expected_sequence: int,
    ) -> None:
        existing = self._events.setdefault(family, [])
        if len(existing) != expected_sequence:
            raise AgentOrchestrationError(
                "plan_event_conflict",
                "计划事件序号发生并发冲突",
            )
        previous = existing[-1].event_sha256 if existing else ""
        event_ids = {item.event_id for item in existing}
        for offset, event in enumerate(events, start=1):
            if (
                event.owner.canonical_id != family[0]
                or event.plan_id != family[1]
                or event.sequence != expected_sequence + offset
                or event.previous_event_sha256 != previous
                or event.event_id in event_ids
            ):
                raise AgentOrchestrationError(
                    "plan_event_conflict",
                    "计划事件不连续或身份不一致",
                )
            event_ids.add(event.event_id)
            previous = event.event_sha256
        existing.extend(events)

    def get_revision(
        self,
        plan_id: str,
        revision: int,
        *,
        owner_id: str,
    ) -> AgentPlanRevisionRecord | None:
        return self._records.get((str(owner_id), str(plan_id), int(revision)))

    def latest_revision(
        self,
        plan_id: str,
        *,
        owner_id: str,
    ) -> AgentPlanRevisionRecord | None:
        matches = [
            record
            for (owner, stored_plan_id, _), record in self._records.items()
            if owner == owner_id and stored_plan_id == plan_id
        ]
        return max(matches, key=lambda item: item.plan.revision) if matches else None

    def list_events(
        self,
        plan_id: str,
        *,
        owner_id: str,
    ) -> tuple[AgentPlanAuditEvent, ...]:
        return tuple(self._events.get(self._family(owner_id, plan_id), ()))


class AgentPlanGovernanceService:
    """宿主控制面；模型只可提交候选计划，不能批准或冻结。"""

    def __init__(
        self,
        repository: AgentPlanRepository,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        if not isinstance(repository, AgentPlanRepository):
            raise TypeError("repository 必须实现 AgentPlanRepository")
        self._repository = repository
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (
            lambda prefix: f"{prefix}-{uuid4().hex}"
        )

    @staticmethod
    def _repair_summary(
        parent: AgentOrchestrationPlan | None,
        candidate: AgentOrchestrationPlan,
        *,
        reason_code: str,
    ) -> AgentPlanRepairSummary:
        if parent is None:
            return AgentPlanRepairSummary("", "", (), (), ())
        parent_payload = parent.to_dict()
        candidate_payload = candidate.to_dict()
        for payload in (parent_payload, candidate_payload):
            payload.pop("revision", None)
            payload.pop("content_sha256", None)
        if parent_payload == candidate_payload:
            raise AgentOrchestrationError(
                "plan_repair_no_change",
                "计划修订没有改变任何执行语义",
                next_actions=("继续使用当前 revision，或提交实际变更后再预览",),
            )
        parent_budget = parent.budget.to_dict()
        candidate_budget = candidate.budget.to_dict()
        expanded = sorted(
            name
            for name, value in candidate_budget.items()
            if value > parent_budget[name]
        )
        if expanded:
            raise AgentOrchestrationError(
                "plan_repair_budget_expanded",
                "计划修订不能扩大已存在的预算上限",
                next_actions=("保持或收窄父计划预算后重新预览",),
            )
        parent_tasks = parent.task_by_id
        candidate_tasks = candidate.task_by_id
        parent_ids = set(parent_tasks)
        candidate_ids = set(candidate_tasks)
        changed = tuple(sorted(
            task_id
            for task_id in parent_ids & candidate_ids
            if parent_tasks[task_id].to_dict()
            != candidate_tasks[task_id].to_dict()
        ))
        return AgentPlanRepairSummary(
            reason_code=reason_code,
            parent_plan_sha256=parent.content_sha256,
            added_task_ids=tuple(sorted(candidate_ids - parent_ids)),
            removed_task_ids=tuple(sorted(parent_ids - candidate_ids)),
            changed_task_ids=changed,
        )

    def _event(
        self,
        *,
        owner: RuntimePrincipal,
        plan: AgentOrchestrationPlan,
        sequence: int,
        kind: AgentPlanEventKind,
        actor_id: str,
        proof_id: str = "",
        related_plan: AgentOrchestrationPlan | None = None,
        previous_event_sha256: str,
        occurred_at: datetime,
    ) -> AgentPlanAuditEvent:
        return AgentPlanAuditEvent(
            event_id=self._id_factory("plan-event"),
            owner=owner,
            plan_id=plan.plan_id,
            plan_revision=plan.revision,
            plan_sha256=plan.content_sha256,
            sequence=sequence,
            kind=kind,
            actor_id=actor_id,
            proof_id=proof_id,
            related_plan_revision=(
                related_plan.revision if related_plan is not None else 0
            ),
            related_plan_sha256=(
                related_plan.content_sha256 if related_plan is not None else ""
            ),
            occurred_at=occurred_at,
            previous_event_sha256=previous_event_sha256,
        )

    def preview(
        self,
        plan: AgentOrchestrationPlan,
        *,
        identity: RuntimeRunIdentity,
        proposed_by: str,
        repair_reason_code: str = "",
    ) -> AgentPlanRevisionView:
        if not isinstance(plan, AgentOrchestrationPlan):
            raise TypeError("plan 必须是 AgentOrchestrationPlan")
        if not isinstance(identity, RuntimeRunIdentity):
            raise TypeError("identity 必须是 RuntimeRunIdentity")
        owner_id = identity.owner.canonical_id
        latest = self._repository.latest_revision(
            plan.plan_id,
            owner_id=owner_id,
        )
        expected_revision = 1 if latest is None else latest.plan.revision + 1
        if plan.revision != expected_revision:
            raise AgentOrchestrationError(
                "plan_revision_conflict",
                f"计划 revision 必须连续递增为 {expected_revision}",
                next_actions=("从最新不可变 revision 创建新计划",),
            )
        events = self._repository.list_events(plan.plan_id, owner_id=owner_id)
        expected_sequence = len(events)
        repair = self._repair_summary(
            latest.plan if latest is not None else None,
            plan,
            reason_code=repair_reason_code,
        )
        now = _aware(self._now(), "preview time")
        record = AgentPlanRevisionRecord(
            preview_id=self._id_factory("plan-preview"),
            plan=plan,
            owner=identity.owner,
            source_run_id=identity.run_id,
            source_turn_id=identity.turn_id,
            proposed_by=proposed_by,
            proposed_at=now,
            repair=repair,
        )
        event = self._event(
            owner=identity.owner,
            plan=plan,
            sequence=expected_sequence + 1,
            kind=AgentPlanEventKind.PREVIEWED,
            actor_id=proposed_by,
            related_plan=(latest.plan if latest is not None else None),
            previous_event_sha256=(events[-1].event_sha256 if events else ""),
            occurred_at=now,
        )
        self._repository.add_revision(
            record,
            event,
            expected_sequence=expected_sequence,
        )
        return AgentPlanRevisionView(
            record=record,
            state=AgentPlanRevisionState.PREVIEWED,
            approval=None,
            freeze=None,
            latest_event_sequence=event.sequence,
        )

    def get_revision(
        self,
        plan_id: str,
        revision: int,
        *,
        owner: RuntimePrincipal,
    ) -> AgentPlanRevisionView | None:
        record = self._repository.get_revision(
            plan_id,
            revision,
            owner_id=owner.canonical_id,
        )
        if record is None:
            return None
        events = self._repository.list_events(
            plan_id,
            owner_id=owner.canonical_id,
        )
        relevant = [
            event for event in events if event.plan_revision == revision
        ]
        if (
            not relevant
            or relevant[0].kind is not AgentPlanEventKind.PREVIEWED
            or any(
                event.plan_sha256 != record.plan.content_sha256
                for event in relevant
            )
        ):
            raise AgentOrchestrationError(
                "plan_event_integrity_failed",
                "计划 revision 的 preview 或摘要证明不完整",
            )
        approval_events = tuple(
            event
            for event in relevant
            if event.kind is AgentPlanEventKind.APPROVED
        )
        freeze_events = tuple(
            event
            for event in relevant
            if event.kind is AgentPlanEventKind.FROZEN
        )
        supersede_events = tuple(
            event
            for event in relevant
            if event.kind is AgentPlanEventKind.REVISION_SUPERSEDED
        )
        if any(len(group) > 1 for group in (
            approval_events,
            freeze_events,
            supersede_events,
        )):
            raise AgentOrchestrationError(
                "plan_event_integrity_failed",
                "计划 revision 含重复生命周期事件",
            )
        approval_event = approval_events[0] if approval_events else None
        freeze_event = freeze_events[0] if freeze_events else None
        supersede_event = supersede_events[0] if supersede_events else None
        if (
            freeze_event is not None
            and (
                approval_event is None
                or freeze_event.sequence <= approval_event.sequence
            )
        ) or (
            supersede_event is not None
            and (
                approval_event is None
                or supersede_event.sequence <= approval_event.sequence
            )
        ):
            raise AgentOrchestrationError(
                "plan_event_integrity_failed",
                "计划 revision 的生命周期事件顺序无效",
            )
        approval = (
            AgentOrchestrationApproval(
                approval_id=approval_event.proof_id,
                plan_id=plan_id,
                plan_revision=revision,
                plan_sha256=record.plan.content_sha256,
                approved_by=approval_event.actor_id,
                approved_at=approval_event.occurred_at,
            )
            if approval_event is not None
            else None
        )
        freeze = (
            AgentOrchestrationFreeze(
                freeze_id=freeze_event.proof_id,
                approval_id=approval.approval_id,
                plan_id=plan_id,
                plan_revision=revision,
                plan_sha256=record.plan.content_sha256,
                frozen_by=freeze_event.actor_id,
                frozen_at=freeze_event.occurred_at,
            )
            if freeze_event is not None and approval is not None
            else None
        )
        if supersede_event is not None:
            state = AgentPlanRevisionState.SUPERSEDED
        elif freeze is not None:
            state = AgentPlanRevisionState.FROZEN
        elif approval is not None:
            state = AgentPlanRevisionState.APPROVED
        else:
            state = AgentPlanRevisionState.PREVIEWED
        return AgentPlanRevisionView(
            record=record,
            state=state,
            approval=approval,
            freeze=freeze,
            latest_event_sequence=events[-1].sequence,
        )

    def _current_view(
        self,
        plan_id: str,
        revision: int,
        owner: RuntimePrincipal,
    ) -> tuple[AgentPlanRevisionView, AgentPlanRevisionRecord]:
        view = self.get_revision(plan_id, revision, owner=owner)
        latest = self._repository.latest_revision(
            plan_id,
            owner_id=owner.canonical_id,
        )
        if view is None or latest is None:
            raise AgentOrchestrationError(
                "plan_revision_not_found",
                "计划 revision 不存在或 owner 不匹配",
            )
        if latest.plan.revision != revision:
            raise AgentOrchestrationError(
                "plan_revision_stale",
                "只能批准或冻结最新计划 revision",
                next_actions=("读取最新 preview 后重新确认",),
            )
        return view, latest

    def approve(
        self,
        *,
        plan_id: str,
        revision: int,
        plan_sha256: str,
        owner: RuntimePrincipal,
        approved_by: str,
        expected_event_sequence: int,
    ) -> AgentPlanRevisionView:
        view, record = self._current_view(plan_id, revision, owner)
        if view.state is not AgentPlanRevisionState.PREVIEWED:
            raise AgentOrchestrationError(
                "plan_state_conflict",
                "只有 previewed 计划可以批准",
            )
        if record.plan.content_sha256 != plan_sha256:
            raise AgentOrchestrationError(
                "plan_digest_conflict",
                "批准摘要与不可变计划不一致",
            )
        if view.latest_event_sequence != expected_event_sequence:
            raise AgentOrchestrationError(
                "plan_event_conflict",
                "批准使用了过期的计划事件序号",
            )
        events = self._repository.list_events(plan_id, owner_id=owner.canonical_id)
        now = _aware(self._now(), "approval time")
        approval_id = self._id_factory("plan-approval")
        event = self._event(
            owner=owner,
            plan=record.plan,
            sequence=expected_event_sequence + 1,
            kind=AgentPlanEventKind.APPROVED,
            actor_id=approved_by,
            proof_id=approval_id,
            previous_event_sha256=events[-1].event_sha256,
            occurred_at=now,
        )
        self._repository.append_events(
            (event,),
            expected_sequence=expected_event_sequence,
        )
        approved = self.get_revision(plan_id, revision, owner=owner)
        if approved is None:
            raise AgentOrchestrationError(
                "plan_store_integrity_failed",
                "批准事件已追加但计划 revision 无法读取",
            )
        return approved

    def freeze(
        self,
        *,
        plan_id: str,
        revision: int,
        plan_sha256: str,
        approval_id: str,
        owner: RuntimePrincipal,
        frozen_by: str,
        expected_event_sequence: int,
    ) -> AgentPlanRevisionView:
        view, record = self._current_view(plan_id, revision, owner)
        if view.state is not AgentPlanRevisionState.APPROVED or view.approval is None:
            raise AgentOrchestrationError(
                "plan_state_conflict",
                "只有 approved 计划可以冻结",
            )
        if (
            record.plan.content_sha256 != plan_sha256
            or view.approval.approval_id != approval_id
        ):
            raise AgentOrchestrationError(
                "plan_digest_conflict",
                "冻结证明与批准的不可变计划不一致",
            )
        if view.latest_event_sequence != expected_event_sequence:
            raise AgentOrchestrationError(
                "plan_event_conflict",
                "冻结使用了过期的计划事件序号",
            )
        events = self._repository.list_events(plan_id, owner_id=owner.canonical_id)
        now = _aware(self._now(), "freeze time")
        freeze_id = self._id_factory("plan-freeze")
        to_append: list[AgentPlanAuditEvent] = []
        previous_digest = events[-1].event_sha256
        sequence = expected_event_sequence
        parent_record = None
        if revision > 1:
            parent_record = self._repository.get_revision(
                plan_id,
                revision - 1,
                owner_id=owner.canonical_id,
            )
            if parent_record is None:
                raise AgentOrchestrationError(
                    "plan_revision_conflict",
                    "冻结修订缺少直接父 revision",
                )
            active_parent = None
            for candidate_revision in range(revision - 1, 0, -1):
                candidate_view = self.get_revision(
                    plan_id,
                    candidate_revision,
                    owner=owner,
                )
                if (
                    candidate_view is not None
                    and candidate_view.state in {
                        AgentPlanRevisionState.APPROVED,
                        AgentPlanRevisionState.FROZEN,
                    }
                ):
                    active_parent = candidate_view
                    break
            if active_parent is not None:
                sequence += 1
                supersede = self._event(
                    owner=owner,
                    plan=active_parent.record.plan,
                    sequence=sequence,
                    kind=AgentPlanEventKind.REVISION_SUPERSEDED,
                    actor_id=frozen_by,
                    proof_id=freeze_id,
                    related_plan=record.plan,
                    previous_event_sha256=previous_digest,
                    occurred_at=now,
                )
                to_append.append(supersede)
                previous_digest = supersede.event_sha256
        sequence += 1
        frozen = self._event(
            owner=owner,
            plan=record.plan,
            sequence=sequence,
            kind=AgentPlanEventKind.FROZEN,
            actor_id=frozen_by,
            proof_id=freeze_id,
            related_plan=(parent_record.plan if parent_record is not None else None),
            previous_event_sha256=previous_digest,
            occurred_at=now,
        )
        to_append.append(frozen)
        self._repository.append_events(
            tuple(to_append),
            expected_sequence=expected_event_sequence,
        )
        frozen_view = self.get_revision(plan_id, revision, owner=owner)
        if frozen_view is None:
            raise AgentOrchestrationError(
                "plan_store_integrity_failed",
                "冻结事件已追加但计划 revision 无法读取",
            )
        return frozen_view

    def require_frozen(self, request: AgentOrchestrationRequest) -> AgentPlanRevisionView:
        if not isinstance(request, AgentOrchestrationRequest):
            raise TypeError("request 必须是 AgentOrchestrationRequest")
        view = self.get_revision(
            request.plan.plan_id,
            request.plan.revision,
            owner=request.identity.owner,
        )
        if (
            view is None
            or view.state is not AgentPlanRevisionState.FROZEN
            or view.approval != request.approval
            or view.freeze != request.freeze
        ):
            raise AgentOrchestrationError(
                "plan_not_frozen",
                "执行请求没有绑定持久化的批准与冻结证明",
                next_actions=("从计划治理 Store 重新加载 frozen revision",),
            )
        return view


__all__ = [
    "AgentPlanAuditEvent",
    "AgentPlanEventKind",
    "AgentPlanGovernanceService",
    "AgentPlanRepairSummary",
    "AgentPlanRepository",
    "AgentPlanRevisionRecord",
    "AgentPlanRevisionState",
    "AgentPlanRevisionView",
    "InMemoryAgentPlanRepository",
]
