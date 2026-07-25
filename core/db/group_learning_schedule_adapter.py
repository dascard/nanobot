"""群学习白名单、增量日志和 fenced 调度的 SQLAlchemy Adapter。"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json

from sqlalchemy import case, or_, update
from sqlalchemy.orm import Session

from core.chat_stream_identity import (
    identity_storage_aliases,
    parse_canonical_chat_stream_id,
)
from core.db.group_learning_schedule_contracts import (
    GroupLearningChatLogRecord,
    GroupLearningIncrementalLogs,
    GroupLearningScheduleClaim,
    GroupLearningScheduleLeaseLost,
    GroupLearningScheduleState,
    GroupLearningScheduleWrite,
)
from core.db.models import (
    ChatLog,
    GroupLearningSchedule,
    GroupLearningStreamState,
)
from core.fencing import lease_deadline, new_fencing_token
from core.jobs import JobLease


def _aspects(value: object) -> tuple[str, ...]:
    try:
        payload = json.loads(str(value or "[]"))
    except json.JSONDecodeError as exc:
        raise ValueError("群学习 schedule aspects_json 无效") from exc
    if not isinstance(payload, list):
        raise ValueError("群学习 schedule aspects_json 必须是数组")
    return tuple(str(item or "").strip() for item in payload)


def _state(row: GroupLearningSchedule) -> GroupLearningScheduleState:
    return GroupLearningScheduleState(
        chat_stream_id=str(row.chat_stream_id or ""),
        enabled=bool(row.enabled),
        aspects=_aspects(row.aspects_json),
        interval_minutes=int(row.interval_minutes or 0),
        window_hours=int(row.window_hours or 0),
        next_run_at=row.next_run_at,
        last_started_at=row.last_started_at,
        last_completed_at=row.last_completed_at,
        consecutive_failures=int(row.consecutive_failures or 0),
        last_error_code=str(row.last_error_code or ""),
        config_generation=int(row.config_generation or 1),
        lease_generation=int(row.lease_generation or 0),
        attempt_count=int(row.attempt_count or 0),
    )


def _job_id(chat_stream_id: str) -> str:
    digest = hashlib.sha256(chat_stream_id.encode("utf-8")).hexdigest()
    return f"group_learning:{digest}"


def _log_record(row: ChatLog) -> GroupLearningChatLogRecord:
    return GroupLearningChatLogRecord(
        chat_log_id=int(row.id or 0),
        role=str(row.role or ""),
        user_id=str(row.user_id or ""),
        sender_name=str(row.sender_name or ""),
        content=str(row.content or ""),
        meta_json=str(row.meta_json or "{}"),
        created_at=row.created_at,
    )


class SqlAlchemyGroupLearningScheduleRepository:
    """调度配置、领域租约和增量日志读取的单一 Adapter。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_schedule(
        self,
        chat_stream_id: str,
    ) -> GroupLearningScheduleState | None:
        row = self._session.get(
            GroupLearningSchedule,
            str(chat_stream_id or "").strip(),
        )
        return _state(row) if row is not None else None

    def put_schedule(
        self,
        write: GroupLearningScheduleWrite,
        *,
        now: datetime,
    ) -> GroupLearningScheduleState:
        row = self._session.get(
            GroupLearningSchedule,
            write.chat_stream_id,
        )
        if row is None:
            row = GroupLearningSchedule(
                chat_stream_id=write.chat_stream_id,
                config_generation=1,
                lease_generation=0,
                attempt_count=0,
                created_at=now,
            )
            self._session.add(row)
        else:
            row.config_generation = int(
                row.config_generation or 1
            ) + 1
        row.enabled = bool(write.enabled)
        row.aspects_json = json.dumps(
            write.aspects,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        row.interval_minutes = write.interval_minutes
        row.window_hours = write.window_hours
        row.next_run_at = write.next_run_at
        row.lease_owner = ""
        row.lease_token = ""
        row.lease_expires_at = None
        row.last_error_code = ""
        row.updated_at = now
        self._session.flush()
        return _state(row)

    def disable_schedule(
        self,
        chat_stream_id: str,
        *,
        now: datetime,
    ) -> GroupLearningScheduleState:
        row = self._session.get(
            GroupLearningSchedule,
            str(chat_stream_id or "").strip(),
        )
        if row is None:
            raise LookupError("group learning schedule not found")
        row.enabled = False
        row.config_generation = int(row.config_generation or 1) + 1
        row.lease_owner = ""
        row.lease_token = ""
        row.lease_expires_at = None
        row.updated_at = now
        self._session.flush()
        return _state(row)

    def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: float,
    ) -> GroupLearningScheduleClaim | None:
        candidates = (
            self._session.query(GroupLearningSchedule)
            .filter(
                GroupLearningSchedule.enabled.is_(True),
                or_(
                    GroupLearningSchedule.next_run_at.is_(None),
                    GroupLearningSchedule.next_run_at <= now,
                ),
                or_(
                    GroupLearningSchedule.lease_expires_at.is_(None),
                    GroupLearningSchedule.lease_expires_at <= now,
                ),
            )
            .order_by(
                case(
                    (
                        GroupLearningSchedule.next_run_at.is_(None),
                        0,
                    ),
                    else_=1,
                ),
                GroupLearningSchedule.next_run_at.asc(),
                GroupLearningSchedule.chat_stream_id.asc(),
            )
            .limit(20)
            .all()
        )
        for candidate in candidates:
            token = new_fencing_token()
            generation = int(candidate.lease_generation or 0) + 1
            attempt_no = int(candidate.attempt_count or 0) + 1
            expires_at = lease_deadline(now, lease_seconds)
            statement = (
                update(GroupLearningSchedule)
                .where(
                    GroupLearningSchedule.chat_stream_id
                    == candidate.chat_stream_id,
                    GroupLearningSchedule.enabled.is_(True),
                    GroupLearningSchedule.config_generation
                    == int(candidate.config_generation or 1),
                    GroupLearningSchedule.lease_generation
                    == int(candidate.lease_generation or 0),
                    or_(
                        GroupLearningSchedule.next_run_at.is_(None),
                        GroupLearningSchedule.next_run_at <= now,
                    ),
                    or_(
                        GroupLearningSchedule.lease_expires_at.is_(None),
                        GroupLearningSchedule.lease_expires_at <= now,
                    ),
                )
                .values(
                    lease_owner=worker_id,
                    lease_token=token,
                    lease_expires_at=expires_at,
                    lease_generation=generation,
                    attempt_count=attempt_no,
                    last_started_at=now,
                    updated_at=now,
                )
            )
            result = self._session.execute(statement)
            if int(result.rowcount or 0) != 1:
                self._session.expire_all()
                continue
            row = self._session.get(
                GroupLearningSchedule,
                candidate.chat_stream_id,
            )
            if row is None:
                raise RuntimeError("已 claim 的群学习 schedule 消失")
            return GroupLearningScheduleClaim(
                chat_stream_id=str(row.chat_stream_id),
                aspects=_aspects(row.aspects_json),
                interval_minutes=int(row.interval_minutes),
                window_hours=int(row.window_hours),
                config_generation=int(row.config_generation),
                lease=JobLease(
                    job_id=_job_id(str(row.chat_stream_id)),
                    worker_id=worker_id,
                    owner_token=token,
                    generation=generation,
                    attempt_no=attempt_no,
                    expires_at=expires_at,
                ),
            )
        return None

    def _active_claim_row(
        self,
        claim: GroupLearningScheduleClaim,
        *,
        now: datetime,
    ) -> GroupLearningSchedule:
        row = self._session.get(
            GroupLearningSchedule,
            claim.chat_stream_id,
        )
        lease = claim.lease
        if (
            row is None
            or not bool(row.enabled)
            or int(row.config_generation or 0)
            != claim.config_generation
            or str(row.lease_owner or "") != lease.worker_id
            or str(row.lease_token or "") != lease.owner_token
            or int(row.lease_generation or 0) != lease.generation
            or int(row.attempt_count or 0) != lease.attempt_no
            or row.lease_expires_at is None
            or row.lease_expires_at <= now
        ):
            raise GroupLearningScheduleLeaseLost(
                "group_learning_schedule_lease_lost"
            )
        return row

    def load_incremental_logs(
        self,
        claim: GroupLearningScheduleClaim,
        *,
        now: datetime,
        context_limit: int,
        max_new_messages: int,
    ) -> GroupLearningIncrementalLogs:
        self._active_claim_row(claim, now=now)
        identity = parse_canonical_chat_stream_id(
            claim.chat_stream_id
        )
        aliases = identity_storage_aliases(identity)
        state = self._session.get(
            GroupLearningStreamState,
            claim.chat_stream_id,
        )
        success_cursor = int(
            state.last_success_chat_log_id if state is not None else 0
        )
        base = self._session.query(ChatLog).filter(
            ChatLog.session_id.in_(aliases),
            ChatLog.role.in_(("ambient", "user")),
        )
        new_query = base.filter(ChatLog.id > success_cursor)
        if success_cursor == 0:
            new_query = new_query.filter(
                ChatLog.created_at
                >= now - timedelta(hours=claim.window_hours)
            )
        raw_new_limit = max(1, int(max_new_messages)) * 4
        new_rows = (
            new_query.order_by(ChatLog.id.asc())
            .limit(raw_new_limit)
            .all()
        )
        context_rows: list[ChatLog] = []
        if success_cursor > 0 and context_limit > 0:
            context_rows = (
                base.filter(ChatLog.id <= success_cursor)
                .order_by(ChatLog.id.desc())
                .limit(int(context_limit))
                .all()
            )
            context_rows.reverse()
        return GroupLearningIncrementalLogs(
            chat_stream_id=claim.chat_stream_id,
            success_cursor=success_cursor,
            context=tuple(_log_record(row) for row in context_rows),
            new=tuple(_log_record(row) for row in new_rows),
        )

    def _settle_statement(
        self,
        claim: GroupLearningScheduleClaim,
        *,
        now: datetime,
    ):
        lease = claim.lease
        return update(GroupLearningSchedule).where(
            GroupLearningSchedule.chat_stream_id
            == claim.chat_stream_id,
            GroupLearningSchedule.enabled.is_(True),
            GroupLearningSchedule.config_generation
            == claim.config_generation,
            GroupLearningSchedule.lease_owner == lease.worker_id,
            GroupLearningSchedule.lease_token == lease.owner_token,
            GroupLearningSchedule.lease_generation
            == lease.generation,
            GroupLearningSchedule.attempt_count == lease.attempt_no,
            GroupLearningSchedule.lease_expires_at > now,
        )

    def settle_success(
        self,
        claim: GroupLearningScheduleClaim,
        *,
        now: datetime,
    ) -> GroupLearningScheduleState:
        statement = self._settle_statement(
            claim,
            now=now,
        ).values(
            next_run_at=now + timedelta(
                minutes=claim.interval_minutes
            ),
            last_completed_at=now,
            lease_owner="",
            lease_token="",
            lease_expires_at=None,
            consecutive_failures=0,
            last_error_code="",
            updated_at=now,
        )
        result = self._session.execute(statement)
        if int(result.rowcount or 0) != 1:
            raise GroupLearningScheduleLeaseLost(
                "group_learning_schedule_lease_lost"
            )
        self._session.expire_all()
        row = self._session.get(
            GroupLearningSchedule,
            claim.chat_stream_id,
        )
        if row is None:
            raise RuntimeError("已 settle 的群学习 schedule 消失")
        return _state(row)

    def settle_failure(
        self,
        claim: GroupLearningScheduleClaim,
        *,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> GroupLearningScheduleState:
        statement = self._settle_statement(
            claim,
            now=now,
        ).values(
            next_run_at=retry_at,
            lease_owner="",
            lease_token="",
            lease_expires_at=None,
            consecutive_failures=(
                GroupLearningSchedule.consecutive_failures + 1
            ),
            last_error_code=error_code,
            updated_at=now,
        )
        result = self._session.execute(statement)
        if int(result.rowcount or 0) != 1:
            raise GroupLearningScheduleLeaseLost(
                "group_learning_schedule_lease_lost"
            )
        self._session.expire_all()
        row = self._session.get(
            GroupLearningSchedule,
            claim.chat_stream_id,
        )
        if row is None:
            raise RuntimeError("已 settle 的群学习 schedule 消失")
        return _state(row)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


__all__ = ["SqlAlchemyGroupLearningScheduleRepository"]
