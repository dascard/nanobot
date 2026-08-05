"""冻结计划驱动的任务板、Agent handoff 与人工审批服务。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from core.agent_collaboration.contracts import (
    AgentCollaborationAccessDenied,
    AgentCollaborationBoard,
    AgentCollaborationClaim,
    AgentCollaborationConflict,
    AgentCollaborationError,
    AgentCollaborationEvent,
    AgentCollaborationEventKind,
    AgentCollaborationNotFound,
)
from core.agent_collaboration.feature import require_agent_collaboration_enabled
from core.agent_orchestration import (
    AgentOrchestrationCheckpoint,
    AgentOrchestrationRequest,
    AgentOrchestrationUsage,
    AgentPlanGovernanceService,
    AgentPlanRevisionState,
    AgentTaskExecutionReceipt,
    AgentTaskOutput,
    AgentTaskState,
    SqlAlchemyAgentOrchestrationCheckpointStore,
    SqlAlchemyAgentPlanRepository,
)
from core.agent_orchestration.contracts import canonical_json_bytes, plain_json
from core.agent_orchestration.serialization import agent_task_output_from_dict
from core.agent_runtime.contracts import (
    RuntimeActor,
    RuntimeActorType,
    RuntimeOwnerType,
    RuntimePrincipal,
    RuntimeRunIdentity,
    RuntimeUsage,
)
from core.agent_runtime.service_ports import RuntimeArtifactResolveRequest
from core.artifact_port import SqlAlchemyArtifactPort
from core.db.models.agent_collaboration import (
    AgentCollaborationBoardRow,
    AgentCollaborationEventRow,
)
from core.db.models.agent_orchestration import AgentOrchestrationCheckpointRow
from core.db.models.durable_task import RunTaskControl
from core.durable_tasks import (
    RunTaskConflict,
    RunTaskKind,
    RunTaskLease,
    RunTaskLeaseLost,
    RunTaskStatus,
    SqlAlchemyRunTaskService,
)
from core.lifecycle import FeatureScope


_SOURCE_TYPE = "agent_collaboration_task"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("持久时间必须包含时区")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _required(value: object, name: str, *, max_chars: int = 160) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > max_chars
        or any(character.isspace() for character in normalized)
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError(f"{name} 无效")
    return normalized


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: object) -> str:
    return _sha256_bytes(str(value or "").encode("utf-8"))


def _json_text(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _task_run_id(board_id: str, task_id: str) -> str:
    board_digest = _sha256_text(board_id)[:24]
    return f"collab-{board_digest}-{task_id}"[:160]


class SqlAlchemyAgentCollaborationService:
    """绑定现有事务的协作服务；调用方负责普通 mutation 的提交。"""

    def __init__(
        self,
        db: Session,
        *,
        session_factory: Callable[[], Session] | None = None,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
        enforce_feature: bool = True,
    ) -> None:
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        self._db = db
        self._session_factory = session_factory
        self._now = now or _utc_now
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")
        self._enforce_feature = bool(enforce_feature)
        self._tasks = SqlAlchemyRunTaskService(db)
        self._plans = AgentPlanGovernanceService(SqlAlchemyAgentPlanRepository(db))

    def _require_feature(self, scope: FeatureScope) -> None:
        if self._enforce_feature:
            require_agent_collaboration_enabled(scope)

    @staticmethod
    def _owner_filters(owner: RuntimePrincipal) -> tuple[object, ...]:
        return (
            AgentCollaborationBoardRow.owner_platform == owner.platform,
            AgentCollaborationBoardRow.owner_type == owner.owner_type.value,
            AgentCollaborationBoardRow.owner_id == owner.owner_id,
        )

    @staticmethod
    def _identity_from_row(row: AgentCollaborationBoardRow) -> RuntimeRunIdentity:
        return RuntimeRunIdentity(
            run_id=str(row.run_id),
            turn_id=str(row.turn_id),
            correlation_id=str(row.correlation_id),
            actor=RuntimeActor(
                RuntimeActorType(str(row.actor_type)),
                str(row.actor_id),
                str(row.parent_actor_id or ""),
            ),
            owner=RuntimePrincipal(
                str(row.owner_platform),
                RuntimeOwnerType(str(row.owner_type)),
                str(row.owner_id),
            ),
        )

    @classmethod
    def _board_from_row(
        cls,
        row: AgentCollaborationBoardRow,
    ) -> AgentCollaborationBoard:
        root_json = str(row.root_input_json or "")
        encoded = root_json.encode("utf-8")
        if (
            len(encoded) != int(row.root_input_size_bytes or 0)
            or _sha256_bytes(encoded) != str(row.root_input_sha256)
        ):
            raise AgentCollaborationError(
                "collaboration_board_integrity_failed",
                "任务板根输入证明不一致",
            )
        try:
            root_input = json.loads(root_json)
        except (TypeError, ValueError) as exc:
            raise AgentCollaborationError(
                "collaboration_board_integrity_failed",
                "任务板根输入无法解析",
            ) from exc
        if not isinstance(root_input, dict):
            raise AgentCollaborationError(
                "collaboration_board_integrity_failed",
                "任务板根输入不是 JSON 对象",
            )
        return AgentCollaborationBoard(
            board_id=str(row.board_id),
            identity=cls._identity_from_row(row),
            plan_id=str(row.plan_id),
            plan_revision=int(row.plan_revision),
            plan_sha256=str(row.plan_sha256),
            approval_id=str(row.approval_id),
            freeze_id=str(row.freeze_id),
            root_input=root_input,
            source_type=str(row.source_type),
            source_id=str(row.source_id),
            created_by=str(row.created_by),
            created_at=_utc_aware(row.created_at),
            expires_at=_utc_aware(row.expires_at),
        )

    def _load_board(
        self,
        board_id: str,
        *,
        owner: RuntimePrincipal | None = None,
        for_update: bool = False,
    ) -> AgentCollaborationBoard:
        query = self._db.query(AgentCollaborationBoardRow).filter(
            AgentCollaborationBoardRow.board_id == _required(board_id, "board_id")
        )
        if owner is not None:
            query = query.filter(*self._owner_filters(owner))
        if for_update:
            query = query.with_for_update()
        row = query.one_or_none()
        if row is None:
            raise AgentCollaborationNotFound(
                "collaboration_board_not_found",
                "任务板不存在或 owner 不匹配",
            )
        return self._board_from_row(row)

    def _plan_context(
        self,
        board: AgentCollaborationBoard,
    ) -> tuple[Any, Any]:
        view = self._plans.get_revision(
            board.plan_id,
            board.plan_revision,
            owner=board.identity.owner,
        )
        if (
            view is None
            or view.state not in {
                AgentPlanRevisionState.FROZEN,
                AgentPlanRevisionState.SUPERSEDED,
            }
            or view.approval is None
            or view.freeze is None
            or view.record.plan.content_sha256 != board.plan_sha256
            or view.approval.approval_id != board.approval_id
            or view.freeze.freeze_id != board.freeze_id
        ):
            raise AgentCollaborationError(
                "collaboration_plan_integrity_failed",
                "任务板未绑定有效的批准与冻结计划",
            )
        return view.record.plan, view

    @staticmethod
    def _event_from_row(
        row: AgentCollaborationEventRow,
    ) -> AgentCollaborationEvent:
        payload_json = str(row.payload_json or "")
        encoded = payload_json.encode("utf-8")
        if (
            len(encoded) != int(row.payload_size_bytes or 0)
            or _sha256_bytes(encoded) != str(row.payload_sha256)
        ):
            raise AgentCollaborationError(
                "collaboration_event_integrity_failed",
                "协作事件 payload 证明不一致",
            )
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError) as exc:
            raise AgentCollaborationError(
                "collaboration_event_integrity_failed",
                "协作事件 payload 无法解析",
            ) from exc
        try:
            event = AgentCollaborationEvent(
                event_id=str(row.event_id),
                board_id=str(row.board_id),
                sequence=int(row.sequence),
                kind=AgentCollaborationEventKind(str(row.event_kind)),
                actor_id=str(row.actor_id),
                target_actor_id=str(row.target_actor_id or ""),
                task_id=str(row.task_id or ""),
                delivery_id=str(row.delivery_id or ""),
                payload=payload,
                idempotency_key_sha256=str(row.idempotency_key_sha256),
                request_sha256=str(row.request_sha256),
                occurred_at=_utc_aware(row.occurred_at),
                expires_at=(
                    _utc_aware(row.expires_at)
                    if row.expires_at is not None
                    else None
                ),
                previous_event_sha256=str(row.previous_event_sha256 or ""),
                event_sha256=str(row.event_sha256),
            )
        except ValueError as exc:
            raise AgentCollaborationError(
                "collaboration_event_integrity_failed",
                "协作事件合同校验失败",
            ) from exc
        if event.payload_sha256 != str(row.payload_sha256):
            raise AgentCollaborationError(
                "collaboration_event_integrity_failed",
                "协作事件 canonical payload 摘要不一致",
            )
        return event

    def _events(self, board_id: str) -> tuple[AgentCollaborationEvent, ...]:
        rows = (
            self._db.query(AgentCollaborationEventRow)
            .filter(AgentCollaborationEventRow.board_id == str(board_id))
            .order_by(AgentCollaborationEventRow.sequence.asc())
            .all()
        )
        events = tuple(self._event_from_row(row) for row in rows)
        previous = ""
        for sequence, event in enumerate(events, start=1):
            if event.sequence != sequence or event.previous_event_sha256 != previous:
                raise AgentCollaborationError(
                    "collaboration_event_integrity_failed",
                    "协作事件哈希链不连续",
                )
            previous = event.event_sha256
        return events

    @staticmethod
    def _request_sha256(
        *,
        kind: AgentCollaborationEventKind,
        actor_id: str,
        target_actor_id: str,
        task_id: str,
        delivery_id: str,
        payload: Mapping[str, object],
    ) -> str:
        return _sha256_bytes(canonical_json_bytes({
            "kind": kind.value,
            "actor_id": actor_id,
            "target_actor_id": target_actor_id,
            "task_id": task_id,
            "delivery_id": delivery_id,
            "payload": plain_json(payload),
        }))

    def _existing_idempotent_event(
        self,
        board_id: str,
        *,
        idempotency_key: str,
        request_sha256: str,
        kind: AgentCollaborationEventKind,
    ) -> AgentCollaborationEvent | None:
        key_sha = _sha256_text(_required(
            idempotency_key,
            "idempotency_key",
            max_chars=256,
        ))
        row = (
            self._db.query(AgentCollaborationEventRow)
            .filter(
                AgentCollaborationEventRow.board_id == board_id,
                AgentCollaborationEventRow.idempotency_key_sha256 == key_sha,
            )
            .one_or_none()
        )
        if row is None:
            return None
        event = self._event_from_row(row)
        if event.kind is not kind or event.request_sha256 != request_sha256:
            raise AgentCollaborationConflict(
                "collaboration_idempotency_conflict",
                "相同幂等键已绑定不同协作操作",
            )
        return event

    def _append_event(
        self,
        board: AgentCollaborationBoard,
        *,
        kind: AgentCollaborationEventKind,
        actor_id: str,
        idempotency_key: str,
        payload: Mapping[str, object],
        target_actor_id: str = "",
        task_id: str = "",
        delivery_id: str = "",
        expires_at: datetime | None = None,
        request_sha256: str = "",
    ) -> tuple[AgentCollaborationEvent, bool]:
        actor = _required(actor_id, "actor_id")
        target = (
            _required(target_actor_id, "target_actor_id")
            if str(target_actor_id or "").strip()
            else ""
        )
        task = (
            _required(task_id, "task_id", max_chars=128)
            if str(task_id or "").strip()
            else ""
        )
        delivery = (
            _required(delivery_id, "delivery_id")
            if str(delivery_id or "").strip()
            else ""
        )
        calculated_request = request_sha256 or self._request_sha256(
            kind=kind,
            actor_id=actor,
            target_actor_id=target,
            task_id=task,
            delivery_id=delivery,
            payload=payload,
        )
        existing = self._existing_idempotent_event(
            board.board_id,
            idempotency_key=idempotency_key,
            request_sha256=calculated_request,
            kind=kind,
        )
        if existing is not None:
            return existing, True
        latest = (
            self._db.query(AgentCollaborationEventRow)
            .filter(AgentCollaborationEventRow.board_id == board.board_id)
            .order_by(AgentCollaborationEventRow.sequence.desc())
            .with_for_update()
            .first()
        )
        sequence = int(latest.sequence) + 1 if latest is not None else 1
        previous = str(latest.event_sha256) if latest is not None else ""
        event = AgentCollaborationEvent(
            event_id=self._id_factory("collaboration-event"),
            board_id=board.board_id,
            sequence=sequence,
            kind=kind,
            actor_id=actor,
            target_actor_id=target,
            task_id=task,
            delivery_id=delivery,
            payload=payload,
            idempotency_key_sha256=_sha256_text(idempotency_key),
            request_sha256=calculated_request,
            occurred_at=self._now(),
            expires_at=expires_at,
            previous_event_sha256=previous,
        )
        payload_json = _json_text(event.payload)
        self._db.add(AgentCollaborationEventRow(
            event_id=event.event_id,
            board_id=event.board_id,
            sequence=event.sequence,
            event_kind=event.kind.value,
            actor_id=event.actor_id,
            target_actor_id=event.target_actor_id,
            task_id=event.task_id,
            delivery_id=event.delivery_id,
            payload_json=payload_json,
            payload_sha256=event.payload_sha256,
            payload_size_bytes=len(payload_json.encode("utf-8")),
            idempotency_key_sha256=event.idempotency_key_sha256,
            request_sha256=event.request_sha256,
            previous_event_sha256=event.previous_event_sha256,
            event_sha256=event.event_sha256,
            occurred_at=_utc_naive(event.occurred_at),
            expires_at=(
                _utc_naive(event.expires_at)
                if event.expires_at is not None
                else None
            ),
        ))
        self._db.flush()
        return event, False

    def create_board(
        self,
        *,
        identity: RuntimeRunIdentity,
        plan_id: str,
        plan_revision: int,
        root_input: Mapping[str, object],
        source_type: str,
        source_id: str,
        created_by: str,
        idempotency_key: str,
        scope: FeatureScope = FeatureScope.ADMIN,
    ) -> AgentCollaborationBoard:
        self._require_feature(scope)
        if not isinstance(identity, RuntimeRunIdentity):
            raise ValueError("identity 无效")
        idempotency = _required(
            idempotency_key,
            "idempotency_key",
            max_chars=256,
        )
        view = self._plans.get_revision(
            _required(plan_id, "plan_id"),
            int(plan_revision),
            owner=identity.owner,
        )
        if (
            view is None
            or view.state is not AgentPlanRevisionState.FROZEN
            or view.approval is None
            or view.freeze is None
        ):
            raise AgentCollaborationConflict(
                "collaboration_plan_not_frozen",
                "只能从已批准且冻结的最新计划创建任务板",
            )
        plan = view.record.plan
        if any(task.runtime_policy is None for task in plan.tasks):
            raise AgentCollaborationConflict(
                "collaboration_runtime_policy_missing",
                "协作任务必须全部声明预算、权限和结束条件",
            )
        validated_root = plan.root_input_contract.validate(
            root_input,
            name="collaboration root_input",
        )
        now = self._now()
        board_id = self._id_factory("collaboration-board")
        # 复用执行请求合同再次绑定 owner、计划证明与根输入。
        AgentOrchestrationRequest(
            orchestration_id=board_id,
            identity=identity,
            plan=plan,
            approval=view.approval,
            freeze=view.freeze,
            root_input=validated_root,
        )
        source_kind = _required(source_type, "source_type", max_chars=64)
        source_identity = _required(source_id, "source_id")
        creator = _required(created_by, "created_by")
        request_payload = {
            "owner": identity.owner.canonical_id,
            "identity": {
                "run_id": identity.run_id,
                "turn_id": identity.turn_id,
                "correlation_id": identity.correlation_id,
                "actor_type": identity.actor.actor_type.value,
                "actor_id": identity.actor.actor_id,
                "parent_actor_id": identity.actor.parent_actor_id,
            },
            "plan_id": plan.plan_id,
            "plan_revision": plan.revision,
            "plan_sha256": plan.content_sha256,
            "root_input": plain_json(validated_root),
            "source_type": source_kind,
            "source_id": source_identity,
            "created_by": creator,
        }
        request_sha = _sha256_bytes(canonical_json_bytes(request_payload))
        existing_row = (
            self._db.query(AgentCollaborationBoardRow)
            .filter(
                *self._owner_filters(identity.owner),
                AgentCollaborationBoardRow.idempotency_key_sha256
                == _sha256_text(idempotency),
            )
            .one_or_none()
        )
        if existing_row is not None:
            if str(existing_row.request_sha256) != request_sha:
                raise AgentCollaborationConflict(
                    "collaboration_idempotency_conflict",
                    "相同幂等键已绑定不同任务板请求",
                )
            return self._board_from_row(existing_row)
        expires_at = now + timedelta(milliseconds=plan.budget.max_elapsed_ms)
        board = AgentCollaborationBoard(
            board_id=board_id,
            identity=identity,
            plan_id=plan.plan_id,
            plan_revision=plan.revision,
            plan_sha256=plan.content_sha256,
            approval_id=view.approval.approval_id,
            freeze_id=view.freeze.freeze_id,
            root_input=validated_root,
            source_type=source_kind,
            source_id=source_identity,
            created_by=creator,
            created_at=now,
            expires_at=expires_at,
        )
        root_json = _json_text(board.root_input)
        self._db.add(AgentCollaborationBoardRow(
            board_id=board.board_id,
            owner_platform=identity.owner.platform,
            owner_type=identity.owner.owner_type.value,
            owner_id=identity.owner.owner_id,
            plan_id=board.plan_id,
            plan_revision=board.plan_revision,
            plan_sha256=board.plan_sha256,
            approval_id=board.approval_id,
            freeze_id=board.freeze_id,
            run_id=identity.run_id,
            turn_id=identity.turn_id,
            correlation_id=identity.correlation_id,
            actor_type=identity.actor.actor_type.value,
            actor_id=identity.actor.actor_id,
            parent_actor_id=identity.actor.parent_actor_id,
            root_input_json=root_json,
            root_input_sha256=board.root_input_sha256,
            root_input_size_bytes=len(root_json.encode("utf-8")),
            source_type=board.source_type,
            source_id=board.source_id,
            created_by=board.created_by,
            idempotency_key_sha256=_sha256_text(idempotency),
            request_sha256=request_sha,
            created_at=_utc_naive(board.created_at),
            expires_at=_utc_naive(board.expires_at),
        ))
        self._db.flush()
        timeout_seconds = max(
            0.001,
            (board.expires_at - now).total_seconds(),
        )
        for task in plan.tasks:
            self._tasks.admit_prepared(
                run_id=_task_run_id(board.board_id, task.task_id),
                task_kind=RunTaskKind.BACKGROUND,
                source_type=_SOURCE_TYPE,
                source_id=board.board_id,
                request_id=f"{board.board_id}:{task.task_id}",
                idempotency_key=f"{idempotency}:{task.task_id}",
                timeout_seconds=timeout_seconds,
                now=now,
            )
        self._append_event(
            board,
            kind=AgentCollaborationEventKind.BOARD_CREATED,
            actor_id=creator,
            idempotency_key=f"board-created:{_sha256_text(idempotency)}",
            request_sha256=_sha256_bytes(canonical_json_bytes({
                "request_sha256": request_sha,
                "board_id": board.board_id,
            })),
            payload={
                "plan_id": board.plan_id,
                "plan_revision": board.plan_revision,
                "plan_sha256": board.plan_sha256,
                "freeze_id": board.freeze_id,
                "root_input_sha256": board.root_input_sha256,
                "task_ids": [task.task_id for task in plan.tasks],
                "expires_at": board.expires_at.isoformat(),
            },
        )
        return board

    @staticmethod
    def _event_by_delivery(
        events: tuple[AgentCollaborationEvent, ...],
        delivery_id: str,
    ) -> AgentCollaborationEvent | None:
        return next(
            (
                event
                for event in events
                if event.kind is AgentCollaborationEventKind.DELIVERABLE_SUBMITTED
                and event.delivery_id == delivery_id
            ),
            None,
        )

    @staticmethod
    def _task_deliveries(
        events: tuple[AgentCollaborationEvent, ...],
    ) -> dict[str, AgentCollaborationEvent]:
        return {
            event.task_id: event
            for event in events
            if event.kind is AgentCollaborationEventKind.DELIVERABLE_SUBMITTED
        }

    @staticmethod
    def _reviews(
        events: tuple[AgentCollaborationEvent, ...],
    ) -> dict[str, AgentCollaborationEvent]:
        return {
            event.delivery_id: event
            for event in events
            if event.kind in {
                AgentCollaborationEventKind.DELIVERABLE_APPROVED,
                AgentCollaborationEventKind.DELIVERABLE_REJECTED,
            }
        }

    @classmethod
    def _approved_task_ids(
        cls,
        events: tuple[AgentCollaborationEvent, ...],
    ) -> frozenset[str]:
        deliveries = cls._task_deliveries(events)
        reviews = cls._reviews(events)
        return frozenset(
            task_id
            for task_id, delivery in deliveries.items()
            if (
                (review := reviews.get(delivery.delivery_id)) is not None
                and review.kind is AgentCollaborationEventKind.DELIVERABLE_APPROVED
            )
        )

    @staticmethod
    def _output_from_delivery(event: AgentCollaborationEvent) -> AgentTaskOutput:
        try:
            output = agent_task_output_from_dict(event.payload.get("output"))
        except ValueError as exc:
            raise AgentCollaborationError(
                "collaboration_event_integrity_failed",
                "协作交付输出无法重建",
            ) from exc
        if output.content_sha256 != str(event.payload.get("output_sha256") or ""):
            raise AgentCollaborationError(
                "collaboration_event_integrity_failed",
                "协作交付输出摘要不一致",
            )
        return output

    def _resolved_task_input(
        self,
        board: AgentCollaborationBoard,
        task: Any,
        events: tuple[AgentCollaborationEvent, ...],
    ) -> Mapping[str, object]:
        approved = self._approved_task_ids(events)
        deliveries = self._task_deliveries(events)
        values: dict[str, object] = {}
        for binding in task.input_bindings:
            if binding.from_root:
                source: Mapping[str, object] = board.root_input
            else:
                if binding.source_task_id not in approved:
                    if binding.required:
                        raise AgentCollaborationConflict(
                            "collaboration_dependency_not_approved",
                            "任务依赖尚未完成人工审批",
                        )
                    continue
                delivery = deliveries[binding.source_task_id]
                source = self._output_from_delivery(delivery).data
            if binding.source_key in source:
                values[binding.target_key] = source[binding.source_key]
            elif binding.required:
                raise AgentCollaborationError(
                    "collaboration_input_binding_failed",
                    "冻结计划的任务输入绑定缺少必填值",
                )
        return task.input_contract.validate(values, name=f"task {task.task_id} input")

    def _task_payload(
        self,
        board: AgentCollaborationBoard,
        task: Any,
        events: tuple[AgentCollaborationEvent, ...],
    ) -> dict[str, object]:
        return {
            "task_id": task.task_id,
            "role_id": task.role_id,
            "description": task.description,
            "dependencies": list(task.dependencies),
            "input": plain_json(self._resolved_task_input(board, task, events)),
            "output_contract": task.output_contract.to_dict(),
            "completion": task.completion.to_dict(),
            "runtime_policy": (
                task.runtime_policy.to_dict()
                if task.runtime_policy is not None
                else None
            ),
            "timeout_ms": task.timeout_ms,
            "board_expires_at": board.expires_at.isoformat(),
        }

    @staticmethod
    def _ensure_board_active(
        board: AgentCollaborationBoard,
        events: tuple[AgentCollaborationEvent, ...],
        now: datetime,
    ) -> None:
        if now >= board.expires_at:
            raise AgentCollaborationConflict(
                "collaboration_board_expired",
                "任务板已经超过冻结的结束时间",
            )
        if any(
            event.kind is AgentCollaborationEventKind.DELIVERABLE_REJECTED
            for event in events
        ):
            raise AgentCollaborationConflict(
                "collaboration_board_blocked",
                "任务板已因人工拒绝而停止，需创建局部修复计划",
            )

    def _ensure_task_ready(
        self,
        task: Any,
        events: tuple[AgentCollaborationEvent, ...],
    ) -> None:
        approved = self._approved_task_ids(events)
        if not set(task.dependencies) <= approved:
            raise AgentCollaborationConflict(
                "collaboration_dependency_not_approved",
                "任务依赖尚未全部交付并通过人工审批",
            )
        deliveries = self._task_deliveries(events)
        if task.task_id in deliveries:
            raise AgentCollaborationConflict(
                "collaboration_task_already_delivered",
                "任务已经提交交付物",
            )

    def invite_agent(
        self,
        *,
        board_id: str,
        task_id: str,
        owner: RuntimePrincipal,
        invited_by: str,
        target_actor_id: str,
        idempotency_key: str,
        ttl_seconds: int = 900,
        scope: FeatureScope = FeatureScope.GROUP_SESSION,
    ) -> dict[str, object]:
        self._require_feature(scope)
        board = self._load_board(board_id, owner=owner, for_update=True)
        plan, _view = self._plan_context(board)
        events = self._events(board.board_id)
        now = self._now()
        self._ensure_board_active(board, events, now)
        task = plan.task_by_id.get(_required(task_id, "task_id", max_chars=128))
        if task is None:
            raise AgentCollaborationNotFound(
                "collaboration_task_not_found",
                "协作任务不存在",
            )
        self._ensure_task_ready(task, events)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("ttl_seconds 必须是 1..3600 的整数")
        expires_at = min(board.expires_at, now + timedelta(seconds=ttl_seconds))
        payload = {
            "board_id": board.board_id,
            "task_id": task.task_id,
            "target_actor_id": _required(target_actor_id, "target_actor_id"),
            "plan_sha256": board.plan_sha256,
            "expires_at": expires_at.isoformat(),
        }
        event, duplicate = self._append_event(
            board,
            kind=AgentCollaborationEventKind.AGENT_INVITED,
            actor_id=invited_by,
            target_actor_id=target_actor_id,
            task_id=task.task_id,
            idempotency_key=idempotency_key,
            payload=payload,
            expires_at=expires_at,
        )
        return {
            "event_id": event.event_id,
            "duplicate": duplicate,
            "target_actor_id": event.target_actor_id,
            "expires_at": event.expires_at.isoformat() if event.expires_at else None,
            "task": self._task_payload(board, task, events),
        }

    @staticmethod
    def _active_invitation(
        events: tuple[AgentCollaborationEvent, ...],
        *,
        task_id: str,
        actor_id: str,
        now: datetime,
    ) -> AgentCollaborationEvent | None:
        return next(
            (
                event
                for event in reversed(events)
                if event.kind is AgentCollaborationEventKind.AGENT_INVITED
                and event.task_id == task_id
                and event.target_actor_id == actor_id
                and event.expires_at is not None
                and event.expires_at > now
            ),
            None,
        )

    def claim_task(
        self,
        *,
        board_id: str,
        task_id: str,
        actor_id: str,
        idempotency_key: str,
        require_invitation: bool = True,
        scope: FeatureScope = FeatureScope.PRIVATE_SESSION,
    ) -> AgentCollaborationClaim:
        self._require_feature(scope)
        board = self._load_board(board_id, for_update=True)
        plan, _view = self._plan_context(board)
        events = self._events(board.board_id)
        now = self._now()
        self._ensure_board_active(board, events, now)
        actor = _required(actor_id, "actor_id")
        task = plan.task_by_id.get(_required(task_id, "task_id", max_chars=128))
        if task is None:
            raise AgentCollaborationNotFound(
                "collaboration_task_not_found",
                "协作任务不存在",
            )
        self._ensure_task_ready(task, events)
        if require_invitation and self._active_invitation(
            events,
            task_id=task.task_id,
            actor_id=actor,
            now=now,
        ) is None:
            raise AgentCollaborationAccessDenied(
                "collaboration_invitation_required",
                "当前 Agent 没有该任务的有效邀请",
            )
        request_payload = {
            "board_id": board.board_id,
            "task_id": task.task_id,
            "actor_id": actor,
        }
        request_sha = self._request_sha256(
            kind=AgentCollaborationEventKind.TASK_CLAIMED,
            actor_id=actor,
            target_actor_id="",
            task_id=task.task_id,
            delivery_id="",
            payload=request_payload,
        )
        existing = self._existing_idempotent_event(
            board.board_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha,
            kind=AgentCollaborationEventKind.TASK_CLAIMED,
        )
        run_id = _task_run_id(board.board_id, task.task_id)
        if existing is not None:
            row = self._db.get(RunTaskControl, run_id)
            if (
                row is None
                or str(row.status) != RunTaskStatus.RUNNING.value
                or str(row.lease_owner) != actor
                or int(row.lease_generation) != int(existing.payload.get("generation") or 0)
                or row.lease_expires_at is None
                or row.lease_expires_at <= _utc_naive(now)
            ):
                raise AgentCollaborationConflict(
                    "collaboration_claim_not_replayable",
                    "认领已结算或租约已失效，不能再次返回旧 secret",
                )
            lease = RunTaskLease(
                run_id=run_id,
                owner=actor,
                token=str(row.lease_token),
                generation=int(row.lease_generation),
                attempt_no=int(row.attempt_count),
                expires_at=row.lease_expires_at,
                timeout_at=row.timeout_at,
            )
            return AgentCollaborationClaim(
                board.board_id,
                task.task_id,
                actor,
                lease,
                self._task_payload(board, task, events),
            )
        running_count = (
            self._db.query(RunTaskControl)
            .filter(
                RunTaskControl.source_type == _SOURCE_TYPE,
                RunTaskControl.source_id == board.board_id,
                RunTaskControl.status == RunTaskStatus.RUNNING.value,
            )
            .count()
        )
        if running_count >= plan.budget.max_concurrency:
            raise AgentCollaborationConflict(
                "collaboration_concurrency_exhausted",
                "任务板已达到冻结的并发上限",
            )
        policy = task.runtime_policy
        if policy is None:
            raise AgentCollaborationError(
                "collaboration_runtime_policy_missing",
                "任务缺少冻结的运行预算与权限",
            )
        lease_seconds = min(
            task.timeout_ms / 1000,
            policy.budget.time_limit_ms / 1000,
            (board.expires_at - now).total_seconds(),
        )
        try:
            lease = self._tasks.claim_prepared(
                run_id,
                owner=actor,
                lease_seconds=lease_seconds,
                now=now,
            )
        except RunTaskConflict as exc:
            raise AgentCollaborationConflict(
                "collaboration_task_claim_conflict",
                "任务已被其他 Agent 认领或不可执行",
            ) from exc
        self._append_event(
            board,
            kind=AgentCollaborationEventKind.TASK_CLAIMED,
            actor_id=actor,
            task_id=task.task_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha,
            payload={
                **request_payload,
                "generation": lease.generation,
                "attempt_no": lease.attempt_no,
                "expires_at": lease.expires_at.isoformat(),
            },
        )
        return AgentCollaborationClaim(
            board.board_id,
            task.task_id,
            actor,
            lease,
            self._task_payload(board, task, events),
        )

    @staticmethod
    def _conservative_output(
        output: AgentTaskOutput,
        task: Any,
    ) -> AgentTaskOutput:
        policy = task.runtime_policy
        if policy is None:
            raise AgentCollaborationError(
                "collaboration_runtime_policy_missing",
                "任务缺少冻结的运行预算与权限",
            )
        budget = policy.budget
        reported = output.usage
        if (
            reported.total_tokens > budget.token_limit
            or reported.cost_microunits > budget.cost_limit_microunits
            or output.model_calls > budget.model_call_limit
            or output.tool_calls > budget.tool_call_limit
        ):
            raise AgentCollaborationConflict(
                "collaboration_task_budget_exceeded",
                "交付声明的用量超过冻结任务预算",
            )
        return AgentTaskOutput(
            status=output.status,
            summary=output.summary,
            next_actions=output.next_actions,
            artifacts=output.artifacts,
            data=output.data,
            usage=RuntimeUsage(
                input_tokens=budget.token_limit,
                output_tokens=0,
                cached_input_tokens=0,
                reasoning_tokens=0,
                cost_microunits=budget.cost_limit_microunits,
            ),
            model_calls=budget.model_call_limit,
            tool_calls=budget.tool_call_limit,
        )

    def _validate_artifacts(
        self,
        output: AgentTaskOutput,
        owner: RuntimePrincipal,
    ) -> None:
        port = SqlAlchemyArtifactPort.for_metadata(self._db)
        for artifact in output.artifacts:
            try:
                resolved = port.resolve_sync(RuntimeArtifactResolveRequest(
                    artifact_id=artifact.artifact_id,
                    owner=owner,
                ))
            except PermissionError as exc:
                raise AgentCollaborationAccessDenied(
                    "collaboration_artifact_access_denied",
                    "交付物引用的 Artifact 不存在或 owner 未授权",
                ) from exc
            if resolved != artifact:
                raise AgentCollaborationConflict(
                    "collaboration_artifact_proof_conflict",
                    "交付物 Artifact 引用与持久元数据不一致",
                )

    def submit_delivery(
        self,
        *,
        board_id: str,
        task_id: str,
        actor_id: str,
        lease_token: str,
        lease_generation: int,
        attempt_no: int,
        output_payload: Mapping[str, object],
        idempotency_key: str,
        scope: FeatureScope = FeatureScope.PRIVATE_SESSION,
    ) -> dict[str, object]:
        self._require_feature(scope)
        board = self._load_board(board_id, for_update=True)
        plan, _view = self._plan_context(board)
        events = self._events(board.board_id)
        now = self._now()
        self._ensure_board_active(board, events, now)
        actor = _required(actor_id, "actor_id")
        task = plan.task_by_id.get(_required(task_id, "task_id", max_chars=128))
        if task is None:
            raise AgentCollaborationNotFound(
                "collaboration_task_not_found",
                "协作任务不存在",
            )
        raw_output = dict(output_payload) if isinstance(output_payload, Mapping) else output_payload
        request_payload = {
            "board_id": board.board_id,
            "task_id": task.task_id,
            "actor_id": actor,
            "lease_token_sha256": _sha256_text(lease_token),
            "lease_generation": lease_generation,
            "attempt_no": attempt_no,
            "output": raw_output,
        }
        request_sha = self._request_sha256(
            kind=AgentCollaborationEventKind.DELIVERABLE_SUBMITTED,
            actor_id=actor,
            target_actor_id="",
            task_id=task.task_id,
            delivery_id="",
            payload=request_payload,
        )
        existing = self._existing_idempotent_event(
            board.board_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha,
            kind=AgentCollaborationEventKind.DELIVERABLE_SUBMITTED,
        )
        if existing is not None:
            return {
                "board_id": board.board_id,
                "task_id": task.task_id,
                "delivery_id": existing.delivery_id,
                "delivery_sha256": existing.event_sha256,
                "output_sha256": existing.payload.get("output_sha256"),
                "duplicate": True,
                "status": "waiting_approval",
            }
        self._ensure_task_ready(task, events)
        try:
            parsed = agent_task_output_from_dict(output_payload)
        except (TypeError, ValueError) as exc:
            raise AgentCollaborationConflict(
                "collaboration_output_contract_invalid",
                "交付物不符合严格任务输出合同",
            ) from exc
        try:
            task.output_contract.validate(parsed.data, name=f"task {task.task_id} output")
        except ValueError as exc:
            raise AgentCollaborationConflict(
                "collaboration_output_contract_invalid",
                "交付数据不符合冻结输出合同",
            ) from exc
        if not task.completion.matches(parsed):
            raise AgentCollaborationConflict(
                "collaboration_completion_condition_failed",
                "交付物未满足冻结的任务结束条件",
            )
        output = self._conservative_output(parsed, task)
        self._validate_artifacts(output, board.identity.owner)
        delivered_bytes = sum(
            self._output_from_delivery(event).size_bytes
            for event in self._task_deliveries(events).values()
        )
        if delivered_bytes + output.size_bytes > plan.budget.max_output_bytes:
            raise AgentCollaborationConflict(
                "collaboration_output_budget_exceeded",
                "任务板累计输出超过冻结预算",
            )
        run_id = _task_run_id(board.board_id, task.task_id)
        row = self._db.get(RunTaskControl, run_id)
        if row is None or row.lease_expires_at is None:
            raise AgentCollaborationConflict(
                "collaboration_lease_lost",
                "任务执行租约不存在或已经失效",
            )
        lease = RunTaskLease(
            run_id=run_id,
            owner=actor,
            token=_required(lease_token, "lease_token", max_chars=64),
            generation=int(lease_generation),
            attempt_no=int(attempt_no),
            expires_at=row.lease_expires_at,
            timeout_at=row.timeout_at,
        )
        delivery_id = self._id_factory("collaboration-delivery")
        claim_event = next(
            (
                event
                for event in reversed(events)
                if event.kind is AgentCollaborationEventKind.TASK_CLAIMED
                and event.task_id == task.task_id
                and event.actor_id == actor
                and int(event.payload.get("generation") or 0) == lease.generation
            ),
            None,
        )
        if claim_event is None:
            raise AgentCollaborationError(
                "collaboration_event_integrity_failed",
                "执行租约缺少对应认领事件",
            )
        try:
            self._tasks.settle(
                lease,
                status=RunTaskStatus.SUCCEEDED,
                terminal_reason="delivery_submitted",
                result_ref=(
                    f"collaboration://{board.board_id}/deliveries/{delivery_id}"
                ),
                now=now,
            )
        except RunTaskLeaseLost as exc:
            raise AgentCollaborationConflict(
                "collaboration_lease_lost",
                "任务租约 token、generation 或有效期已经失效",
            ) from exc
        event, _duplicate = self._append_event(
            board,
            kind=AgentCollaborationEventKind.DELIVERABLE_SUBMITTED,
            actor_id=actor,
            task_id=task.task_id,
            delivery_id=delivery_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha,
            payload={
                "board_id": board.board_id,
                "task_id": task.task_id,
                "role_id": task.role_id,
                "actor_id": actor,
                "run_id": run_id,
                "generation": lease.generation,
                "attempt_no": lease.attempt_no,
                "started_at": claim_event.occurred_at.isoformat(),
                "submitted_at": now.isoformat(),
                "output": output.to_dict(),
                "output_sha256": output.content_sha256,
                "output_size_bytes": output.size_bytes,
            },
        )
        return {
            "board_id": board.board_id,
            "task_id": task.task_id,
            "delivery_id": delivery_id,
            "delivery_sha256": event.event_sha256,
            "output_sha256": output.content_sha256,
            "duplicate": False,
            "status": "waiting_approval",
        }

    def review_delivery(
        self,
        *,
        board_id: str,
        delivery_id: str,
        expected_delivery_sha256: str,
        owner: RuntimePrincipal,
        reviewer_id: str,
        approved: bool,
        reason_code: str,
        idempotency_key: str,
        scope: FeatureScope = FeatureScope.ADMIN,
    ) -> dict[str, object]:
        self._require_feature(scope)
        board = self._load_board(board_id, owner=owner, for_update=True)
        self._plan_context(board)
        events = self._events(board.board_id)
        now = self._now()
        self._ensure_board_active(board, events, now)
        delivery = self._event_by_delivery(
            events,
            _required(delivery_id, "delivery_id"),
        )
        if delivery is None:
            raise AgentCollaborationNotFound(
                "collaboration_delivery_not_found",
                "交付物不存在或不属于该任务板",
            )
        expected = str(expected_delivery_sha256 or "").strip().lower()
        if expected != delivery.event_sha256:
            raise AgentCollaborationConflict(
                "collaboration_delivery_digest_conflict",
                "人工复核摘要与不可变交付事件不一致",
            )
        existing_review = self._reviews(events).get(delivery.delivery_id)
        kind = (
            AgentCollaborationEventKind.DELIVERABLE_APPROVED
            if approved
            else AgentCollaborationEventKind.DELIVERABLE_REJECTED
        )
        normalized_reason = str(reason_code or "").strip()
        if approved:
            normalized_reason = normalized_reason or "human_approved"
        else:
            normalized_reason = _required(
                normalized_reason,
                "reason_code",
                max_chars=128,
            )
        payload = {
            "board_id": board.board_id,
            "task_id": delivery.task_id,
            "delivery_id": delivery.delivery_id,
            "delivery_sha256": delivery.event_sha256,
            "approved": bool(approved),
            "reason_code": normalized_reason,
        }
        request_sha = self._request_sha256(
            kind=kind,
            actor_id=_required(reviewer_id, "reviewer_id"),
            target_actor_id="",
            task_id=delivery.task_id,
            delivery_id=delivery.delivery_id,
            payload=payload,
        )
        duplicate = self._existing_idempotent_event(
            board.board_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha,
            kind=kind,
        )
        if duplicate is not None:
            return {
                "board_id": board.board_id,
                "task_id": delivery.task_id,
                "delivery_id": delivery.delivery_id,
                "review_event_id": duplicate.event_id,
                "approved": approved,
                "duplicate": True,
            }
        if existing_review is not None:
            raise AgentCollaborationConflict(
                "collaboration_delivery_already_reviewed",
                "交付物已经完成人工复核",
            )
        event, _ = self._append_event(
            board,
            kind=kind,
            actor_id=reviewer_id,
            task_id=delivery.task_id,
            delivery_id=delivery.delivery_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha,
            payload=payload,
        )
        return {
            "board_id": board.board_id,
            "task_id": delivery.task_id,
            "delivery_id": delivery.delivery_id,
            "review_event_id": event.event_id,
            "approved": approved,
            "duplicate": False,
        }

    def _checkpoint_receipts_outputs(
        self,
        plan: Any,
        events: tuple[AgentCollaborationEvent, ...],
        approved_task_ids: set[str],
    ) -> tuple[
        tuple[AgentTaskExecutionReceipt, ...],
        dict[str, AgentTaskOutput],
    ]:
        deliveries = self._task_deliveries(events)
        outputs: dict[str, AgentTaskOutput] = {}
        receipts: list[AgentTaskExecutionReceipt] = []
        for task_id in sorted(approved_task_ids):
            task = plan.task_by_id[task_id]
            delivery = deliveries[task_id]
            output = self._output_from_delivery(delivery)
            started_at = datetime.fromisoformat(
                str(delivery.payload.get("started_at") or "")
            )
            finished_at = datetime.fromisoformat(
                str(delivery.payload.get("submitted_at") or "")
            )
            duration_ms = max(
                0,
                int((finished_at - started_at).total_seconds() * 1000),
            )
            outputs[task_id] = output
            receipts.append(AgentTaskExecutionReceipt(
                task_id=task_id,
                role_id=task.role_id,
                state=AgentTaskState.SUCCEEDED,
                attempt_no=int(delivery.payload.get("attempt_no") or 1),
                dependency_ids=task.dependencies,
                output_sha256=output.content_sha256,
                output_size_bytes=output.size_bytes,
                error_code="",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                reservation_id=(
                    f"collab:{_sha256_text(delivery.board_id)[:24]}:"
                    f"{task_id}:{delivery.payload.get('generation')}"
                ),
            ))
        return tuple(receipts), outputs

    @staticmethod
    def _cumulative_usage(
        receipts: tuple[AgentTaskExecutionReceipt, ...],
        outputs: Mapping[str, AgentTaskOutput],
    ) -> AgentOrchestrationUsage:
        return AgentOrchestrationUsage(
            usage=RuntimeUsage(
                input_tokens=sum(item.usage.input_tokens for item in outputs.values()),
                output_tokens=sum(item.usage.output_tokens for item in outputs.values()),
                cached_input_tokens=sum(
                    item.usage.cached_input_tokens for item in outputs.values()
                ),
                reasoning_tokens=sum(
                    item.usage.reasoning_tokens for item in outputs.values()
                ),
                cost_microunits=sum(
                    item.usage.cost_microunits for item in outputs.values()
                ),
            ),
            model_calls=sum(item.model_calls for item in outputs.values()),
            tool_calls=sum(item.tool_calls for item in outputs.values()),
            task_attempts=len(receipts),
            output_bytes=sum(item.size_bytes for item in outputs.values()),
        )

    async def advance_checkpoints(
        self,
        *,
        board_id: str,
        owner: RuntimePrincipal,
    ) -> AgentOrchestrationCheckpoint | None:
        """人工审批提交后，幂等推进所有连续完成的确定性屏障。"""

        if self._session_factory is None:
            raise RuntimeError("advance_checkpoints 需要独立 session_factory")
        board = self._load_board(board_id, owner=owner)
        plan, _view = self._plan_context(board)
        events = self._events(board.board_id)
        if any(
            event.kind is AgentCollaborationEventKind.DELIVERABLE_REJECTED
            for event in events
        ):
            return None
        approved = set(self._approved_task_ids(events))
        store = SqlAlchemyAgentOrchestrationCheckpointStore(self._session_factory)
        latest = await store.load_latest(
            board.board_id,
            owner_id=owner.canonical_id,
        )
        next_sequence = latest.sequence + 1 if latest is not None else 1
        completed: set[str] = set()
        if latest is not None:
            completed = {
                task_id
                for task_id, state in latest.task_states.items()
                if state is AgentTaskState.SUCCEEDED
            }
        for barrier in plan.execution_barriers():
            if barrier.sequence < next_sequence:
                continue
            required = set(barrier.completed_before) | set(barrier.task_ids)
            if not required <= approved:
                break
            receipts, outputs = self._checkpoint_receipts_outputs(
                plan,
                events,
                required,
            )
            states = {
                task.task_id: (
                    AgentTaskState.SUCCEEDED
                    if task.task_id in required
                    else AgentTaskState.PENDING
                )
                for task in plan.tasks
            }
            checkpoint = AgentOrchestrationCheckpoint(
                checkpoint_id=f"collab-checkpoint-{board.board_id}-{barrier.sequence}",
                orchestration_id=board.board_id,
                identity=board.identity,
                plan_id=board.plan_id,
                plan_revision=board.plan_revision,
                plan_sha256=board.plan_sha256,
                freeze_id=board.freeze_id,
                sequence=barrier.sequence,
                parent_checkpoint_id=(latest.checkpoint_id if latest else ""),
                barrier_id=barrier.barrier_id,
                task_states=states,
                outputs=outputs,
                receipts=receipts,
                cumulative_usage=self._cumulative_usage(receipts, outputs),
                created_at=self._now(),
            )
            latest = await store.save(checkpoint)
            next_sequence = barrier.sequence + 1
            completed.update(barrier.task_ids)
        return latest

    def board_view(
        self,
        *,
        board_id: str,
        owner: RuntimePrincipal,
        scope: FeatureScope = FeatureScope.ADMIN,
    ) -> dict[str, object]:
        self._require_feature(scope)
        board = self._load_board(board_id, owner=owner)
        plan, _view = self._plan_context(board)
        events = self._events(board.board_id)
        deliveries = self._task_deliveries(events)
        reviews = self._reviews(events)
        claims = {
            event.task_id: event
            for event in events
            if event.kind is AgentCollaborationEventKind.TASK_CLAIMED
        }
        approved = self._approved_task_ids(events)
        now = self._now()
        task_views: list[dict[str, object]] = []
        any_running = False
        any_waiting = False
        blocked = any(
            event.kind is AgentCollaborationEventKind.DELIVERABLE_REJECTED
            for event in events
        )
        for task in plan.tasks:
            run = self._tasks.get(_task_run_id(board.board_id, task.task_id))
            delivery = deliveries.get(task.task_id)
            claim = claims.get(task.task_id)
            review = reviews.get(delivery.delivery_id) if delivery is not None else None
            if review is not None:
                state = (
                    "approved"
                    if review.kind is AgentCollaborationEventKind.DELIVERABLE_APPROVED
                    else "rejected"
                )
            elif delivery is not None:
                state = "waiting_approval"
                any_waiting = True
            elif run is None:
                state = "missing"
                blocked = True
            elif run.status is RunTaskStatus.RUNNING:
                state = "running"
                any_running = True
            elif run.status is RunTaskStatus.ACCEPTED:
                state = (
                    "ready"
                    if set(task.dependencies) <= approved
                    else "blocked_by_dependencies"
                )
            else:
                state = run.status.value
                blocked = True
            task_views.append({
                "task_id": task.task_id,
                "role_id": task.role_id,
                "description": task.description,
                "dependencies": list(task.dependencies),
                "state": state,
                "responsible_actor_id": (
                    delivery.actor_id
                    if delivery is not None
                    else claim.actor_id
                    if claim is not None
                    else run.lease_owner
                    if run is not None
                    else ""
                ),
                "lease_generation": (
                    int(delivery.payload.get("generation") or 0)
                    if delivery is not None
                    else int(claim.payload.get("generation") or 0)
                    if claim is not None
                    else run.lease_generation
                    if run is not None
                    else 0
                ),
                "lease_expires_at": (
                    run.lease_expires_at.isoformat()
                    if run is not None and run.lease_expires_at is not None
                    else None
                ),
                "delivery_id": delivery.delivery_id if delivery else "",
                "delivery_sha256": delivery.event_sha256 if delivery else "",
                "review_event_id": review.event_id if review else "",
            })
        latest_checkpoint = (
            self._db.query(AgentOrchestrationCheckpointRow)
            .filter(
                AgentOrchestrationCheckpointRow.orchestration_id == board.board_id
            )
            .order_by(AgentOrchestrationCheckpointRow.sequence.desc())
            .first()
        )
        all_approved = len(approved) == len(plan.tasks)
        if now >= board.expires_at:
            status = "expired"
        elif blocked:
            status = "blocked"
        elif all_approved:
            status = "succeeded"
        elif any_waiting:
            status = "waiting_approval"
        elif any_running:
            status = "running"
        else:
            status = "ready"
        aggregate_delivery = deliveries.get(plan.aggregation_task_id)
        aggregate_output = (
            self._output_from_delivery(aggregate_delivery).to_dict()
            if all_approved and aggregate_delivery is not None
            else None
        )
        return {
            "board_id": board.board_id,
            "owner": owner.canonical_id,
            "status": status,
            "plan": {
                "plan_id": plan.plan_id,
                "revision": plan.revision,
                "sha256": plan.content_sha256,
                "freeze_id": board.freeze_id,
                "budget": plan.budget.to_dict(),
            },
            "source": {"type": board.source_type, "id": board.source_id},
            "created_by": board.created_by,
            "created_at": board.created_at.isoformat(),
            "expires_at": board.expires_at.isoformat(),
            "tasks": task_views,
            "latest_checkpoint_id": (
                str(latest_checkpoint.checkpoint_id)
                if latest_checkpoint is not None
                else ""
            ),
            "latest_checkpoint_sequence": (
                int(latest_checkpoint.sequence)
                if latest_checkpoint is not None
                else 0
            ),
            "aggregate_output": aggregate_output,
        }

    def agent_status(
        self,
        *,
        board_id: str,
        actor_id: str,
        scope: FeatureScope = FeatureScope.PRIVATE_SESSION,
    ) -> dict[str, object]:
        self._require_feature(scope)
        board = self._load_board(board_id)
        plan, _view = self._plan_context(board)
        events = self._events(board.board_id)
        actor = _required(actor_id, "actor_id")
        now = self._now()
        task_ids = {
            event.task_id
            for event in events
            if (
                event.target_actor_id == actor
                and event.kind is AgentCollaborationEventKind.AGENT_INVITED
                and event.expires_at is not None
                and event.expires_at > now
            )
            or (
                event.actor_id == actor
                and event.kind in {
                    AgentCollaborationEventKind.TASK_CLAIMED,
                    AgentCollaborationEventKind.DELIVERABLE_SUBMITTED,
                }
            )
        }
        if not task_ids:
            raise AgentCollaborationAccessDenied(
                "collaboration_invitation_required",
                "当前 Agent 没有该任务板的协作记录",
            )
        deliveries = self._task_deliveries(events)
        reviews = self._reviews(events)
        tasks: list[dict[str, object]] = []
        for task_id in sorted(task_ids):
            task = plan.task_by_id.get(task_id)
            if task is None:
                raise AgentCollaborationError(
                    "collaboration_event_integrity_failed",
                    "协作事件引用未知任务",
                )
            delivery = deliveries.get(task_id)
            review = reviews.get(delivery.delivery_id) if delivery else None
            run = self._tasks.get(_task_run_id(board.board_id, task_id))
            tasks.append({
                "task": self._task_payload(board, task, events),
                "state": (
                    "approved"
                    if review is not None
                    and review.kind is AgentCollaborationEventKind.DELIVERABLE_APPROVED
                    else "rejected"
                    if review is not None
                    else "waiting_approval"
                    if delivery is not None
                    else run.status.value
                    if run is not None
                    else "missing"
                ),
                "delivery_id": delivery.delivery_id if delivery else "",
                "delivery_sha256": delivery.event_sha256 if delivery else "",
            })
        return {
            "board_id": board.board_id,
            "expires_at": board.expires_at.isoformat(),
            "tasks": tasks,
        }


__all__ = ["SqlAlchemyAgentCollaborationService"]
