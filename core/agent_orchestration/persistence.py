"""多 Agent 计划治理与任务屏障 checkpoint 的 SQL 持久实现。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.agent_orchestration.contracts import (
    AgentOrchestrationCheckpoint,
    AgentOrchestrationError,
)
from core.agent_orchestration.plan_governance import (
    AgentPlanAuditEvent,
    AgentPlanEventKind,
    AgentPlanRepository,
    AgentPlanRepairSummary,
    AgentPlanRevisionRecord,
)
from core.agent_orchestration.serialization import (
    decode_agent_orchestration_checkpoint,
    decode_agent_orchestration_plan,
    encode_agent_orchestration_checkpoint,
    encode_agent_orchestration_plan,
)
from core.db.models.agent_orchestration import (
    AgentOrchestrationCheckpointRow,
    AgentOrchestrationPlanEventRow,
    AgentOrchestrationPlanRevisionRow,
)
from core.agent_runtime.contracts import RuntimeOwnerType, RuntimePrincipal
from core.sqlite_retry import run_sqlite_locked_retry


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("持久时间必须包含时区")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _owner_parts(owner_id: str) -> tuple[str, str, str]:
    parts = str(owner_id or "").split(":", 2)
    if len(parts) != 3 or not all(parts):
        raise ValueError("owner_id 必须是 canonical principal ID")
    RuntimeOwnerType(parts[1])
    return parts[0], parts[1], parts[2]


def _owner_from_row(row: object) -> RuntimePrincipal:
    return RuntimePrincipal(
        str(getattr(row, "owner_platform")),
        RuntimeOwnerType(str(getattr(row, "owner_type"))),
        str(getattr(row, "owner_id")),
    )


def _json_string_tuple(value: str, name: str) -> tuple[str, ...]:
    try:
        payload = json.loads(str(value or ""))
    except (TypeError, ValueError) as exc:
        raise AgentOrchestrationError(
            "plan_store_integrity_failed",
            f"{name} JSON 损坏",
        ) from exc
    if not isinstance(payload, list) or any(
        not isinstance(item, str) for item in payload
    ):
        raise AgentOrchestrationError(
            "plan_store_integrity_failed",
            f"{name} 必须是字符串数组",
        )
    return tuple(payload)


def _revision_from_row(
    row: AgentOrchestrationPlanRevisionRow,
) -> AgentPlanRevisionRecord:
    plan_json = str(row.plan_json or "")
    if len(plan_json.encode("utf-8")) != int(row.size_bytes or 0):
        raise AgentOrchestrationError(
            "plan_store_integrity_failed",
            "计划 JSON 大小与持久证明不一致",
        )
    try:
        plan = decode_agent_orchestration_plan(plan_json)
    except ValueError as exc:
        raise AgentOrchestrationError(
            "plan_store_integrity_failed",
            "计划 JSON 无法按当前 schema 重建",
        ) from exc
    if (
        plan.plan_id != str(row.plan_id)
        or plan.revision != int(row.revision)
        or plan.content_sha256 != str(row.plan_sha256)
    ):
        raise AgentOrchestrationError(
            "plan_store_integrity_failed",
            "计划 JSON 与索引证明不一致",
        )
    return AgentPlanRevisionRecord(
        preview_id=str(row.preview_id),
        plan=plan,
        owner=_owner_from_row(row),
        source_run_id=str(row.source_run_id),
        source_turn_id=str(row.source_turn_id),
        proposed_by=str(row.proposed_by),
        proposed_at=_utc_aware(row.proposed_at),
        repair=AgentPlanRepairSummary(
            reason_code=str(row.repair_reason_code or ""),
            parent_plan_sha256=str(row.parent_plan_sha256 or ""),
            added_task_ids=_json_string_tuple(
                row.added_task_ids_json,
                "added_task_ids",
            ),
            removed_task_ids=_json_string_tuple(
                row.removed_task_ids_json,
                "removed_task_ids",
            ),
            changed_task_ids=_json_string_tuple(
                row.changed_task_ids_json,
                "changed_task_ids",
            ),
        ),
    )


def _event_from_row(
    row: AgentOrchestrationPlanEventRow,
) -> AgentPlanAuditEvent:
    return AgentPlanAuditEvent(
        event_id=str(row.event_id),
        owner=_owner_from_row(row),
        plan_id=str(row.plan_id),
        plan_revision=int(row.plan_revision),
        plan_sha256=str(row.plan_sha256),
        sequence=int(row.sequence),
        kind=AgentPlanEventKind(str(row.event_kind)),
        actor_id=str(row.actor_id),
        proof_id=str(row.proof_id or ""),
        related_plan_revision=int(row.related_plan_revision or 0),
        related_plan_sha256=str(row.related_plan_sha256 or ""),
        occurred_at=_utc_aware(row.occurred_at),
        previous_event_sha256=str(row.previous_event_sha256 or ""),
        event_sha256=str(row.event_sha256),
    )


class SqlAlchemyAgentPlanRepository(AgentPlanRepository):
    """绑定现有事务的计划 Repository；调用方决定 commit／rollback。"""

    def __init__(self, db: Session) -> None:
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        self._db = db

    @staticmethod
    def _filters(plan_id: str, owner_id: str) -> tuple[object, ...]:
        platform, owner_type, raw_owner_id = _owner_parts(owner_id)
        return (
            AgentOrchestrationPlanRevisionRow.owner_platform == platform,
            AgentOrchestrationPlanRevisionRow.owner_type == owner_type,
            AgentOrchestrationPlanRevisionRow.owner_id == raw_owner_id,
            AgentOrchestrationPlanRevisionRow.plan_id == str(plan_id),
        )

    @staticmethod
    def _event_filters(plan_id: str, owner_id: str) -> tuple[object, ...]:
        platform, owner_type, raw_owner_id = _owner_parts(owner_id)
        return (
            AgentOrchestrationPlanEventRow.owner_platform == platform,
            AgentOrchestrationPlanEventRow.owner_type == owner_type,
            AgentOrchestrationPlanEventRow.owner_id == raw_owner_id,
            AgentOrchestrationPlanEventRow.plan_id == str(plan_id),
        )

    def _check_events(
        self,
        events: tuple[AgentPlanAuditEvent, ...],
        *,
        expected_sequence: int,
    ) -> None:
        if not events:
            raise ValueError("plan events 不能为空")
        first = events[0]
        last_row = (
            self._db.query(AgentOrchestrationPlanEventRow)
            .filter(*self._event_filters(
                first.plan_id,
                first.owner.canonical_id,
            ))
            .order_by(AgentOrchestrationPlanEventRow.sequence.desc())
            .with_for_update()
            .first()
        )
        actual_sequence = int(last_row.sequence) if last_row is not None else 0
        if actual_sequence != expected_sequence:
            raise AgentOrchestrationError(
                "plan_event_conflict",
                "计划事件序号发生并发冲突",
            )
        previous = str(last_row.event_sha256) if last_row is not None else ""
        for offset, event in enumerate(events, start=1):
            if (
                event.owner != first.owner
                or event.plan_id != first.plan_id
                or event.sequence != expected_sequence + offset
                or event.previous_event_sha256 != previous
            ):
                raise AgentOrchestrationError(
                    "plan_event_conflict",
                    "计划事件链不连续或 owner 不一致",
                )
            previous = event.event_sha256

    @staticmethod
    def _event_row(event: AgentPlanAuditEvent) -> AgentOrchestrationPlanEventRow:
        return AgentOrchestrationPlanEventRow(
            event_id=event.event_id,
            owner_platform=event.owner.platform,
            owner_type=event.owner.owner_type.value,
            owner_id=event.owner.owner_id,
            plan_id=event.plan_id,
            plan_revision=event.plan_revision,
            plan_sha256=event.plan_sha256,
            sequence=event.sequence,
            event_kind=event.kind.value,
            actor_id=event.actor_id,
            proof_id=event.proof_id,
            related_plan_revision=event.related_plan_revision,
            related_plan_sha256=event.related_plan_sha256,
            occurred_at=_utc_naive(event.occurred_at),
            previous_event_sha256=event.previous_event_sha256,
            event_sha256=event.event_sha256,
        )

    def add_revision(
        self,
        record: AgentPlanRevisionRecord,
        event: AgentPlanAuditEvent,
        *,
        expected_sequence: int,
    ) -> None:
        if event.kind is not AgentPlanEventKind.PREVIEWED:
            raise ValueError("新计划 revision 的首事件必须是 previewed")
        existing = (
            self._db.query(AgentOrchestrationPlanRevisionRow.id)
            .filter(
                *self._filters(
                    record.plan.plan_id,
                    record.owner.canonical_id,
                ),
                AgentOrchestrationPlanRevisionRow.revision
                == record.plan.revision,
            )
            .first()
        )
        if existing is not None:
            raise AgentOrchestrationError(
                "plan_revision_conflict",
                "计划 revision 已存在",
            )
        self._check_events((event,), expected_sequence=expected_sequence)
        plan_json = encode_agent_orchestration_plan(record.plan)
        revision_row = AgentOrchestrationPlanRevisionRow(
            preview_id=record.preview_id,
            owner_platform=record.owner.platform,
            owner_type=record.owner.owner_type.value,
            owner_id=record.owner.owner_id,
            plan_id=record.plan.plan_id,
            revision=record.plan.revision,
            plan_sha256=record.plan.content_sha256,
            plan_json=plan_json,
            size_bytes=len(plan_json.encode("utf-8")),
            source_run_id=record.source_run_id,
            source_turn_id=record.source_turn_id,
            proposed_by=record.proposed_by,
            proposed_at=_utc_naive(record.proposed_at),
            parent_plan_sha256=record.repair.parent_plan_sha256,
            repair_reason_code=record.repair.reason_code,
            added_task_ids_json=json.dumps(
                record.repair.added_task_ids,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            removed_task_ids_json=json.dumps(
                record.repair.removed_task_ids,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            changed_task_ids_json=json.dumps(
                record.repair.changed_task_ids,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        try:
            with self._db.begin_nested():
                self._db.add(revision_row)
                self._db.flush()
                self._db.add(self._event_row(event))
                self._db.flush()
        except IntegrityError as exc:
            raise AgentOrchestrationError(
                "plan_revision_conflict",
                "计划 revision 或事件发生并发冲突",
            ) from exc

    def append_events(
        self,
        events: tuple[AgentPlanAuditEvent, ...],
        *,
        expected_sequence: int,
    ) -> None:
        self._check_events(events, expected_sequence=expected_sequence)
        try:
            with self._db.begin_nested():
                self._db.add_all([
                    self._event_row(event) for event in events
                ])
                self._db.flush()
        except IntegrityError as exc:
            raise AgentOrchestrationError(
                "plan_event_conflict",
                "计划事件追加发生并发冲突",
            ) from exc

    def get_revision(
        self,
        plan_id: str,
        revision: int,
        *,
        owner_id: str,
    ) -> AgentPlanRevisionRecord | None:
        row = (
            self._db.query(AgentOrchestrationPlanRevisionRow)
            .filter(
                *self._filters(plan_id, owner_id),
                AgentOrchestrationPlanRevisionRow.revision == int(revision),
            )
            .one_or_none()
        )
        return _revision_from_row(row) if row is not None else None

    def latest_revision(
        self,
        plan_id: str,
        *,
        owner_id: str,
    ) -> AgentPlanRevisionRecord | None:
        row = (
            self._db.query(AgentOrchestrationPlanRevisionRow)
            .filter(*self._filters(plan_id, owner_id))
            .order_by(AgentOrchestrationPlanRevisionRow.revision.desc())
            .first()
        )
        return _revision_from_row(row) if row is not None else None

    def list_events(
        self,
        plan_id: str,
        *,
        owner_id: str,
    ) -> tuple[AgentPlanAuditEvent, ...]:
        rows = (
            self._db.query(AgentOrchestrationPlanEventRow)
            .filter(*self._event_filters(plan_id, owner_id))
            .order_by(AgentOrchestrationPlanEventRow.sequence.asc())
            .all()
        )
        events = tuple(_event_from_row(row) for row in rows)
        previous = ""
        for sequence, event in enumerate(events, start=1):
            if (
                event.sequence != sequence
                or event.previous_event_sha256 != previous
            ):
                raise AgentOrchestrationError(
                    "plan_store_integrity_failed",
                    "计划事件摘要链不连续",
                )
            previous = event.event_sha256
        return events


class SqlAlchemyAgentOrchestrationCheckpointStore:
    """每个任务屏障单独提交的生产 checkpoint Store。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory 必须可调用")
        self._session_factory = session_factory

    @staticmethod
    def _row_checkpoint(
        row: AgentOrchestrationCheckpointRow,
    ) -> AgentOrchestrationCheckpoint:
        state_json = str(row.state_json or "")
        if len(state_json.encode("utf-8")) != int(row.size_bytes or 0):
            raise AgentOrchestrationError(
                "checkpoint_store_integrity_failed",
                "checkpoint 大小证明不一致",
            )
        try:
            checkpoint = decode_agent_orchestration_checkpoint(state_json)
        except ValueError as exc:
            raise AgentOrchestrationError(
                "checkpoint_store_integrity_failed",
                "checkpoint JSON 无法按当前 schema 重建",
            ) from exc
        if (
            checkpoint.checkpoint_id != str(row.checkpoint_id)
            or checkpoint.orchestration_id != str(row.orchestration_id)
            or checkpoint.identity.run_id != str(row.run_id)
            or checkpoint.identity.owner.canonical_id != str(row.owner_id)
            or checkpoint.plan_id != str(row.plan_id)
            or checkpoint.plan_revision != int(row.plan_revision)
            or checkpoint.plan_sha256 != str(row.plan_sha256)
            or checkpoint.freeze_id != str(row.freeze_id)
            or checkpoint.sequence != int(row.sequence)
            or checkpoint.parent_checkpoint_id
            != str(row.parent_checkpoint_id or "")
            or checkpoint.barrier_id != str(row.barrier_id)
            or checkpoint.state_sha256 != str(row.state_sha256)
        ):
            raise AgentOrchestrationError(
                "checkpoint_store_integrity_failed",
                "checkpoint JSON 与索引证明不一致",
            )
        return checkpoint

    async def save(
        self,
        checkpoint: AgentOrchestrationCheckpoint,
    ) -> AgentOrchestrationCheckpoint:
        if not isinstance(checkpoint, AgentOrchestrationCheckpoint):
            raise TypeError("checkpoint 必须是 AgentOrchestrationCheckpoint")
        db = self._session_factory()
        try:
            def operation() -> AgentOrchestrationCheckpoint:
                existing = (
                    db.query(AgentOrchestrationCheckpointRow)
                    .filter(
                        AgentOrchestrationCheckpointRow.checkpoint_id
                        == checkpoint.checkpoint_id
                    )
                    .one_or_none()
                )
                if existing is not None:
                    stored = self._row_checkpoint(existing)
                    if stored != checkpoint:
                        raise AgentOrchestrationError(
                            "checkpoint_conflict",
                            "checkpoint_id 已绑定不同状态",
                        )
                    return stored
                latest = (
                    db.query(AgentOrchestrationCheckpointRow)
                    .filter(
                        AgentOrchestrationCheckpointRow.orchestration_id
                        == checkpoint.orchestration_id
                    )
                    .order_by(
                        AgentOrchestrationCheckpointRow.sequence.desc()
                    )
                    .with_for_update()
                    .first()
                )
                if latest is None:
                    if checkpoint.sequence != 1 or checkpoint.parent_checkpoint_id:
                        raise AgentOrchestrationError(
                            "checkpoint_conflict",
                            "首个 checkpoint 屏障无效",
                        )
                else:
                    if (
                        str(latest.owner_id)
                        != checkpoint.identity.owner.canonical_id
                        or str(latest.plan_id) != checkpoint.plan_id
                        or int(latest.plan_revision) != checkpoint.plan_revision
                        or str(latest.plan_sha256) != checkpoint.plan_sha256
                        or str(latest.freeze_id) != checkpoint.freeze_id
                        or checkpoint.sequence != int(latest.sequence) + 1
                        or checkpoint.parent_checkpoint_id
                        != str(latest.checkpoint_id)
                    ):
                        raise AgentOrchestrationError(
                            "checkpoint_conflict",
                            "checkpoint owner、计划或屏障链发生冲突",
                        )
                state_json = encode_agent_orchestration_checkpoint(checkpoint)
                db.add(AgentOrchestrationCheckpointRow(
                    checkpoint_id=checkpoint.checkpoint_id,
                    orchestration_id=checkpoint.orchestration_id,
                    run_id=checkpoint.identity.run_id,
                    owner_id=checkpoint.identity.owner.canonical_id,
                    plan_id=checkpoint.plan_id,
                    plan_revision=checkpoint.plan_revision,
                    plan_sha256=checkpoint.plan_sha256,
                    freeze_id=checkpoint.freeze_id,
                    sequence=checkpoint.sequence,
                    parent_checkpoint_id=checkpoint.parent_checkpoint_id,
                    barrier_id=checkpoint.barrier_id,
                    state_json=state_json,
                    state_sha256=checkpoint.state_sha256,
                    size_bytes=len(state_json.encode("utf-8")),
                    created_at=_utc_naive(checkpoint.created_at),
                ))
                db.commit()
                return checkpoint

            return run_sqlite_locked_retry(
                operation,
                rollback=db.rollback,
                label="agent_orchestration_checkpoint_save",
            )
        except IntegrityError as exc:
            db.rollback()
            raise AgentOrchestrationError(
                "checkpoint_conflict",
                "checkpoint 提交发生并发冲突",
            ) from exc
        finally:
            db.close()

    async def load_latest(
        self,
        orchestration_id: str,
        *,
        owner_id: str,
    ) -> AgentOrchestrationCheckpoint | None:
        normalized_orchestration = str(orchestration_id or "").strip()
        normalized_owner = str(owner_id or "").strip()
        if not normalized_orchestration or not normalized_owner:
            raise ValueError("orchestration_id 和 owner_id 不能为空")
        db = self._session_factory()
        try:
            row = (
                db.query(AgentOrchestrationCheckpointRow)
                .filter(
                    AgentOrchestrationCheckpointRow.orchestration_id
                    == normalized_orchestration,
                )
                .order_by(AgentOrchestrationCheckpointRow.sequence.desc())
                .first()
            )
            if row is None:
                return None
            if str(row.owner_id) != normalized_owner:
                raise AgentOrchestrationError(
                    "checkpoint_owner_conflict",
                    "orchestration_id 已绑定其他 owner",
                    stop_condition="owner 冲突时禁止执行或覆盖已有编排",
                )
            return self._row_checkpoint(row)
        finally:
            db.close()


__all__ = [
    "SqlAlchemyAgentOrchestrationCheckpointStore",
    "SqlAlchemyAgentPlanRepository",
]
