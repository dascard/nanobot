"""出站状态机到兼容读模型的投影适配器。

领域状态机只传入当前事务和冻结实体；所有 ConversationTurn、ScheduledTask 与
ProactiveOutreachLog ORM 细节集中在此处，避免继续散落到编排代码。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import exists, or_
from sqlalchemy.orm import Session

from core.db.models.chat import ConversationTurn
from core.db.models.outbound import (
    OutboundDeliveryOutbox,
    OutboundRun,
)
from core.db.models.proactive import ProactiveOutreachLog
from core.db.models.scheduling import ScheduledTask
from core.message_envelope import envelope_to_message, is_html_reply
from core.outbound.contracts import (
    PROACTIVE_GENERATED_TASK_KIND,
    OutboundConflictError,
    OutboundFencingError,
)
from core.outbound.policy import (
    canonical_json,
    proactive_outreach_generated_source_snapshot,
    proactive_outreach_occurrence_key,
    proactive_outreach_source_snapshot,
    safe_summary,
    utc_naive,
)


_OUTBOUND_CONTEXT_TIMEZONE = ZoneInfo("Asia/Shanghai")
_OUTBOUND_CONTEXT_TEXT_LIMIT = 800


class OutboundSourceProjectionPort(Protocol):
    """状态机向兼容投影写入结果的端口。"""

    def append_delivered_context(
        self,
        db: Session,
        *,
        run: OutboundRun,
        outbox: OutboundDeliveryOutbox,
        delivered_at: datetime,
    ) -> None: ...

    def linkage_is_current(self, db: Session, *, run_id: int) -> bool: ...

    def project(
        self,
        db: Session,
        *,
        run: OutboundRun,
        status: str,
        current: datetime,
        error_summary: Any = "",
        bind_run: bool = False,
        attempted: bool = False,
        succeeded: bool = False,
    ) -> None: ...


def _outbound_context_local_time(value: datetime) -> datetime:
    utc_value = utc_naive(value).replace(tzinfo=timezone.utc)
    return utc_value.astimezone(_OUTBOUND_CONTEXT_TIMEZONE).replace(tzinfo=None)


def _compact_outbound_context_text(value: str) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= _OUTBOUND_CONTEXT_TEXT_LIMIT:
        return compact
    return compact[:_OUTBOUND_CONTEXT_TEXT_LIMIT] + "…"


def _json_object_or_empty(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        return {}
    return parsed if type(parsed) is dict else {}


def _scheduled_task_id(run: OutboundRun) -> int | None:
    if str(run.source_type) != "scheduled_task":
        return None
    try:
        task_id = int(str(run.source_id))
    except (TypeError, ValueError):
        return None
    return task_id if task_id > 0 else None


def validate_proactive_source(
    db: Session,
    run: OutboundRun,
) -> tuple[ProactiveOutreachLog, dict[str, Any]]:
    try:
        log_id = int(str(run.source_id))
    except (TypeError, ValueError) as exc:
        raise OutboundFencingError("主动外呼 run 的 source_id 无效") from exc
    row = db.get(ProactiveOutreachLog, log_id)
    if row is None:
        raise OutboundFencingError("主动外呼来源记录不存在")
    try:
        parsed = json.loads(str(run.source_snapshot_json or ""))
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise OutboundFencingError("主动外呼冻结快照无法解析") from exc
    if type(parsed) is not dict:
        raise OutboundFencingError("主动外呼冻结快照必须是 JSON object")
    canonical, digest = canonical_json(
        parsed,
        name="proactive_outreach_source_snapshot",
    )
    if (
        canonical != str(run.source_snapshot_json)
        or digest != str(run.source_snapshot_sha256)
        or digest != str(run.source_revision)
    ):
        raise OutboundFencingError("主动外呼冻结快照完整性校验失败")
    current_snapshot = (
        proactive_outreach_generated_source_snapshot(row)
        if str(run.task_kind) == PROACTIVE_GENERATED_TASK_KIND
        else proactive_outreach_source_snapshot(row)
    )
    current_json, current_revision = canonical_json(
        current_snapshot,
        name="proactive_outreach_source_snapshot",
    )
    expected_occurrence = proactive_outreach_occurrence_key(
        log_id=log_id,
        idempotency_key=str(row.idempotency_key or ""),
        source_revision=str(run.source_revision),
    )
    if (
        current_json != canonical
        or current_revision != str(run.source_revision)
        or parsed.get("log_id") != log_id
        or str(run.occurrence_key) != expected_occurrence
    ):
        raise OutboundFencingError("主动外呼来源 revision 已变化")
    return row, current_snapshot


def _proactive_projection_query(
    db: Session,
    *,
    row: ProactiveOutreachLog,
    run: OutboundRun,
):
    query = db.query(ProactiveOutreachLog).filter(
        ProactiveOutreachLog.id == int(row.id),
        ProactiveOutreachLog.user_id == row.user_id,
        ProactiveOutreachLog.idempotency_key == row.idempotency_key,
        ProactiveOutreachLog.grounding_json == row.grounding_json,
        ProactiveOutreachLog.judge_reason == row.judge_reason,
        ProactiveOutreachLog.next_check_at == row.next_check_at,
        ProactiveOutreachLog.next_intent == row.next_intent,
        ProactiveOutreachLog.message == row.message,
        ProactiveOutreachLog.forced.is_(bool(row.forced)),
        ProactiveOutreachLog.created_at == row.created_at,
    )
    if row.judge_should is None:
        query = query.filter(ProactiveOutreachLog.judge_should.is_(None))
    else:
        query = query.filter(
            ProactiveOutreachLog.judge_should.is_(bool(row.judge_should))
        )
    return query


class SqlAlchemyOutboundSourceProjection:
    """使用当前 SQLAlchemy Session 写兼容投影的生产适配器。"""

    def append_delivered_context(
        self,
        db: Session,
        *,
        run: OutboundRun,
        outbox: OutboundDeliveryOutbox,
        delivered_at: datetime,
    ) -> None:
        source_type = str(run.source_type or "")
        if source_type not in {"proactive_outreach", "scheduled_task"}:
            return
        if str(outbox.target_type or "") != "private":
            return
        destination = _json_object_or_empty(outbox.destination_snapshot_json)
        user_id = str(destination.get("target_id") or "").strip()
        if not user_id:
            return
        event_id = f"outbound-delivery:{source_type}:{int(outbox.id)}"
        source_ids_json = json.dumps([event_id], ensure_ascii=False)
        already_exists = db.query(
            exists().where(
                ConversationTurn.user_id == user_id,
                ConversationTurn.session_id == f"private_{user_id}",
                ConversationTurn.source_message_ids_json == source_ids_json,
            )
        ).scalar()
        if already_exists:
            return

        payload = _json_object_or_empty(outbox.payload_json)
        message = envelope_to_message(payload)
        if source_type == "proactive_outreach":
            if is_html_reply(message):
                content = f"[主动外呼已发送] HTML报告，{len(message)}字符"
            else:
                body = _compact_outbound_context_text(message)
                content = f"[主动外呼已发送] {body or '已投递'}"
        else:
            snapshot = _json_object_or_empty(run.source_snapshot_json)
            task_name = _compact_outbound_context_text(
                str(snapshot.get("name") or "定时任务")
            )
            if is_html_reply(message):
                content = (
                    f"[定时任务已发送] {task_name}"
                    f"（HTML报告，{len(message)}字符）"
                )
            else:
                body = _compact_outbound_context_text(message)
                content = f"[定时任务已发送] {task_name}：{body or '已投递'}"

        db.add(ConversationTurn(
            user_id=user_id,
            session_id=f"private_{user_id}",
            role="assistant",
            content=content,
            created_at=_outbound_context_local_time(delivered_at),
            source_message_ids_json=source_ids_json,
            meta_json=json.dumps(
                {
                    "kind": "outbound_delivery_summary",
                    "source_type": source_type,
                    "run_id": int(run.id),
                    "outbox_id": int(outbox.id),
                    "delivered_at_utc": utc_naive(delivered_at).isoformat(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        ))

    def linkage_is_current(self, db: Session, *, run_id: int) -> bool:
        run = db.get(OutboundRun, int(run_id))
        if run is None or str(run.source_type) != "proactive_outreach":
            return False
        try:
            row, _snapshot = validate_proactive_source(db, run)
        except (OutboundFencingError, OutboundConflictError):
            return False
        return row.outbound_run_id == int(run.id)

    def _project_scheduled_task(
        self,
        db: Session,
        *,
        run: OutboundRun,
        status: str,
        current: datetime,
        error_summary: Any = "",
        bind_run: bool = False,
        attempted: bool = False,
        succeeded: bool = False,
    ) -> None:
        task_id = _scheduled_task_id(run)
        if task_id is None:
            return
        normalized_status = str(status or "")[:48]
        normalized_summary = safe_summary(error_summary)
        if succeeded:
            task = db.get(ScheduledTask, task_id)
            if task is not None and (
                task.last_success_at is None or task.last_success_at < current
            ):
                task.last_success_at = current
                db.flush()

        values: dict[Any, Any] = {
            ScheduledTask.delivery_status: normalized_status,
            ScheduledTask.last_error_summary: normalized_summary,
        }
        if bind_run:
            values[ScheduledTask.last_run_id] = int(run.id)
        if attempted:
            values[ScheduledTask.last_attempt_at] = current
            values[ScheduledTask.last_run_at] = current
        query = db.query(ScheduledTask).filter(ScheduledTask.id == task_id)
        if not bind_run:
            query = query.filter(ScheduledTask.last_run_id == int(run.id))
        query.update(values, synchronize_session=False)

    def _project_proactive_outreach(
        self,
        db: Session,
        *,
        run: OutboundRun,
        status: str,
        current: datetime,
        error_summary: Any = "",
        bind_run: bool = False,
        attempted: bool = False,
        succeeded: bool = False,
    ) -> None:
        del current, error_summary, attempted
        if str(run.source_type) != "proactive_outreach":
            return
        row, _snapshot = validate_proactive_source(db, run)
        projection_status = {
            "claimed": "candidate",
            "generating": "candidate",
            "queued": "queued",
            "delivering": "delivering",
            "retry_wait": "retry_wait",
            "blocked": "blocked",
            "failed": "failed",
            "cancelled": "cancelled",
            "ambiguous": "ambiguous",
            "delivered": "sent",
            "succeeded": "sent",
            "succeeded_after_ambiguous_replay": "sent_after_ambiguous_replay",
        }.get(str(status or ""))
        if succeeded:
            projection_status = (
                "sent_after_ambiguous_replay"
                if bool(run.has_ambiguous_ancestor)
                else "sent"
            )
        if projection_status is None:
            raise OutboundFencingError("主动外呼来源投影状态无效")
        query = _proactive_projection_query(db, row=row, run=run)
        if bind_run:
            query = query.filter(
                ProactiveOutreachLog.status.in_(("pending", "candidate")),
                or_(
                    ProactiveOutreachLog.outbound_run_id.is_(None),
                    ProactiveOutreachLog.outbound_run_id == int(run.id),
                ),
            )
        else:
            query = query.filter(
                ProactiveOutreachLog.outbound_run_id == int(run.id)
            )
        values: dict[Any, Any] = {
            ProactiveOutreachLog.status: projection_status,
        }
        if bind_run:
            values[ProactiveOutreachLog.outbound_run_id] = int(run.id)
        updated = query.update(values, synchronize_session=False)
        if updated != 1:
            raise OutboundFencingError("主动外呼来源投影 CAS 失败")

    def project(
        self,
        db: Session,
        *,
        run: OutboundRun,
        status: str,
        current: datetime,
        error_summary: Any = "",
        bind_run: bool = False,
        attempted: bool = False,
        succeeded: bool = False,
    ) -> None:
        self._project_scheduled_task(
            db,
            run=run,
            status=status,
            current=current,
            error_summary=error_summary,
            bind_run=bind_run,
            attempted=attempted,
            succeeded=succeeded,
        )
        self._project_proactive_outreach(
            db,
            run=run,
            status=status,
            current=current,
            error_summary=error_summary,
            bind_run=bind_run,
            attempted=attempted,
            succeeded=succeeded,
        )


DEFAULT_OUTBOUND_SOURCE_PROJECTION: OutboundSourceProjectionPort = (
    SqlAlchemyOutboundSourceProjection()
)


def append_delivered_outbound_context(
    db: Session,
    *,
    run: OutboundRun,
    outbox: OutboundDeliveryOutbox,
    delivered_at: datetime,
) -> None:
    DEFAULT_OUTBOUND_SOURCE_PROJECTION.append_delivered_context(
        db,
        run=run,
        outbox=outbox,
        delivered_at=delivered_at,
    )


def proactive_outreach_linkage_is_current(
    db: Session,
    *,
    run_id: int,
) -> bool:
    return DEFAULT_OUTBOUND_SOURCE_PROJECTION.linkage_is_current(
        db,
        run_id=run_id,
    )


def project_outbound_source(
    db: Session,
    *,
    run: OutboundRun,
    status: str,
    current: datetime,
    error_summary: Any = "",
    bind_run: bool = False,
    attempted: bool = False,
    succeeded: bool = False,
) -> None:
    DEFAULT_OUTBOUND_SOURCE_PROJECTION.project(
        db,
        run=run,
        status=status,
        current=current,
        error_summary=error_summary,
        bind_run=bind_run,
        attempted=attempted,
        succeeded=succeeded,
    )


__all__ = [
    "DEFAULT_OUTBOUND_SOURCE_PROJECTION",
    "OutboundSourceProjectionPort",
    "SqlAlchemyOutboundSourceProjection",
    "append_delivered_outbound_context",
    "proactive_outreach_linkage_is_current",
    "project_outbound_source",
    "validate_proactive_source",
]
