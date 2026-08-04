"""Run Event Ledger 的 SQLAlchemy 事务 Adapter。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.models.run_ledger import RunLedgerEventRow, RunLedgerStreamHead
from core.run_ledger.contracts import (
    RUN_LEDGER_SCHEMA_NAME,
    RunEventLedgerPort,
    RunLedgerConflictError,
    RunLedgerEventDraft,
    RunLedgerEventRecord,
    RunLedgerHead,
    RunLedgerIdentity,
    RunLedgerIntegrityError,
    decode_run_ledger_payload,
    encode_run_ledger_payload,
    run_ledger_event_sha256,
    run_ledger_payload_sha256,
)
from core.sqlite_retry import run_sqlite_locked_retry
from core.telemetry.contracts import TelemetryCorrelation


logger = logging.getLogger("nanobot.run_ledger")


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=timezone.utc)


def _row_to_record(row: RunLedgerEventRow) -> RunLedgerEventRecord:
    if str(row.schema_name or "") != RUN_LEDGER_SCHEMA_NAME:
        raise RunLedgerIntegrityError(
            f"未知 Run Ledger schema：{row.schema_name!r}"
        )
    payload = decode_run_ledger_payload(
        str(row.payload_json or "{}"),
        schema_version=int(row.schema_version),
    )
    payload_sha256 = run_ledger_payload_sha256(payload)
    if payload_sha256 != str(row.payload_sha256 or ""):
        raise RunLedgerIntegrityError(
            f"Run Ledger payload 摘要不一致：{row.event_id}"
        )
    correlation = TelemetryCorrelation(
        request_id=row.request_id,
        session_id=row.session_id,
        turn_id=row.turn_id,
        trace_id=row.trace_id,
        run_id=row.run_id,
        task_id=row.task_id,
        task_run_id=row.task_run_id,
        job_id=row.job_id,
        tool_call_id=row.tool_call_id,
        delivery_id=row.delivery_id,
        parent_job_id=row.parent_job_id,
    )
    identity = RunLedgerIdentity(
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        parent_actor_id=row.parent_actor_id,
        owner_platform=row.owner_platform,
        owner_type=row.owner_type,
        owner_id=row.owner_id,
    )
    event = RunLedgerEventDraft(
        event_id=str(row.event_id),
        run_id=str(row.run_id),
        event_type=str(row.event_type),
        occurred_at=_utc_aware(row.occurred_at),
        source=str(row.source),
        correlation=correlation,
        identity=identity,
        status=str(row.status or ""),
        payload=payload,
        source_event_id=str(row.source_event_id or ""),
        source_sequence=int(row.source_sequence or 0),
        correction_of_event_id=str(row.correction_of_event_id or ""),
        schema_version=int(row.schema_version),
        dropped_field_count=int(row.dropped_field_count or 0),
    )
    expected_sha256 = run_ledger_event_sha256(
        event,
        sequence=int(row.sequence),
        previous_event_sha256=str(row.previous_event_sha256 or ""),
    )
    if expected_sha256 != str(row.event_sha256 or ""):
        raise RunLedgerIntegrityError(
            f"Run Ledger event 摘要不一致：{row.event_id}"
        )
    return RunLedgerEventRecord(
        position=int(row.position),
        sequence=int(row.sequence),
        event=event,
        recorded_at=_utc_aware(row.recorded_at),
        previous_event_sha256=str(row.previous_event_sha256 or ""),
        event_sha256=str(row.event_sha256),
    )


def _same_event(
    record: RunLedgerEventRecord,
    candidate: RunLedgerEventDraft,
) -> bool:
    return record.event_sha256 == run_ledger_event_sha256(
        candidate,
        sequence=record.sequence,
        previous_event_sha256=record.previous_event_sha256,
    )


class SqlAlchemyRunEventLedger(RunEventLedgerPort):
    """绑定现有 Session；append/投影更新可处于同一业务事务。"""

    def __init__(self, db: Session) -> None:
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        self._db = db

    def append(
        self,
        event: RunLedgerEventDraft,
        *,
        expected_sequence: int | None = None,
    ) -> RunLedgerEventRecord:
        if not isinstance(event, RunLedgerEventDraft):
            raise TypeError("event 必须是 RunLedgerEventDraft")
        if expected_sequence is not None and (
            type(expected_sequence) is not int or expected_sequence <= 0
        ):
            raise ValueError("expected_sequence 必须是正整数或空")

        existing_row = (
            self._db.query(RunLedgerEventRow)
            .filter(RunLedgerEventRow.event_id == event.event_id)
            .one_or_none()
        )
        if existing_row is not None:
            existing = _row_to_record(existing_row)
            if expected_sequence is not None and existing.sequence != expected_sequence:
                raise RunLedgerConflictError(
                    "event_id 已存在，但 sequence 与期望不一致"
                )
            if not _same_event(existing, event):
                raise RunLedgerConflictError(
                    f"event_id 已绑定不同事实：{event.event_id}"
                )
            return existing

        if event.correction_of_event_id:
            corrected = (
                self._db.query(RunLedgerEventRow.run_id)
                .filter(
                    RunLedgerEventRow.event_id
                    == event.correction_of_event_id
                )
                .one_or_none()
            )
            if corrected is None or str(corrected[0]) != event.run_id:
                raise RunLedgerConflictError(
                    "纠正事件只能引用同一 Run 中已存在的事件"
                )

        self._validate_owner(event)
        head_row = (
            self._db.query(RunLedgerStreamHead)
            .filter(RunLedgerStreamHead.run_id == event.run_id)
            .with_for_update()
            .one_or_none()
        )
        if head_row is None:
            if event.event_type != "run.accepted":
                raise RunLedgerConflictError(
                    "Run Ledger 首条事实必须是 run.accepted"
                )
            head_row = RunLedgerStreamHead(
                run_id=event.run_id,
                last_sequence=0,
                last_event_id="",
                last_event_sha256="",
                terminal_sequence=None,
                updated_at=_utc_naive(datetime.now(timezone.utc)),
            )
            self._db.add(head_row)
            self._db.flush()
        elif event.event_type == "run.accepted":
            raise RunLedgerConflictError(
                f"Run 已存在接纳事实：{event.run_id}"
            )
        if (
            head_row.terminal_sequence is not None
            and event.event_type != "run.event_corrected"
        ):
            raise RunLedgerConflictError(
                f"Run 已终止，不能继续追加执行事实：{event.run_id}"
            )

        sequence = int(head_row.last_sequence or 0) + 1
        if expected_sequence is not None and expected_sequence != sequence:
            raise RunLedgerConflictError(
                f"Run Ledger 期望 sequence={expected_sequence}，实际应为 {sequence}"
            )
        previous_sha256 = str(head_row.last_event_sha256 or "")
        payload_json = encode_run_ledger_payload(event.payload)
        payload_sha256 = run_ledger_payload_sha256(event.payload)
        event_sha256 = run_ledger_event_sha256(
            event,
            sequence=sequence,
            previous_event_sha256=previous_sha256,
        )
        now = _utc_naive(datetime.now(timezone.utc))
        context = event.correlation
        identity = event.identity
        row = RunLedgerEventRow(
            event_id=event.event_id,
            run_id=event.run_id,
            sequence=sequence,
            event_type=event.event_type,
            schema_name=RUN_LEDGER_SCHEMA_NAME,
            schema_version=event.schema_version,
            occurred_at=_utc_naive(event.occurred_at),
            recorded_at=now,
            source=event.source,
            source_event_id=event.source_event_id,
            source_sequence=event.source_sequence,
            request_id=context.request_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
            trace_id=context.trace_id,
            task_id=context.task_id,
            task_run_id=context.task_run_id,
            job_id=context.job_id,
            tool_call_id=context.tool_call_id,
            delivery_id=context.delivery_id,
            parent_job_id=context.parent_job_id,
            actor_type=identity.actor_type,
            actor_id=identity.actor_id,
            parent_actor_id=identity.parent_actor_id,
            owner_platform=identity.owner_platform,
            owner_type=identity.owner_type,
            owner_id=identity.owner_id,
            status=event.status,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
            dropped_field_count=event.dropped_field_count,
            correction_of_event_id=event.correction_of_event_id,
            previous_event_sha256=previous_sha256,
            event_sha256=event_sha256,
        )
        self._db.add(row)
        head_row.last_sequence = sequence
        head_row.last_event_id = event.event_id
        head_row.last_event_sha256 = event_sha256
        if event.terminal:
            head_row.terminal_sequence = sequence
        head_row.updated_at = now
        self._db.flush()
        return _row_to_record(row)

    def _validate_owner(self, event: RunLedgerEventDraft) -> None:
        identity = event.identity
        if not identity.owner_id:
            return
        existing = (
            self._db.query(
                RunLedgerEventRow.owner_platform,
                RunLedgerEventRow.owner_type,
                RunLedgerEventRow.owner_id,
            )
            .filter(
                RunLedgerEventRow.run_id == event.run_id,
                RunLedgerEventRow.owner_id != "",
            )
            .order_by(RunLedgerEventRow.sequence.asc())
            .first()
        )
        if existing is None:
            return
        declared = (
            identity.owner_platform,
            identity.owner_type,
            identity.owner_id,
        )
        if tuple(str(value or "") for value in existing) != declared:
            raise RunLedgerConflictError(
                f"同一 run_id 不能切换 owner：{event.run_id}"
            )

    def get(self, event_id: str) -> RunLedgerEventRecord | None:
        normalized = str(event_id or "").strip()
        if not normalized:
            raise ValueError("event_id 不能为空")
        selected = (
            self._db.query(RunLedgerEventRow)
            .filter(RunLedgerEventRow.event_id == normalized)
            .one_or_none()
        )
        return _row_to_record(selected) if selected is not None else None

    def read(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        limit: int = 1000,
    ) -> tuple[RunLedgerEventRecord, ...]:
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run_id 不能为空")
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence 必须是非负整数")
        if through_sequence is not None and (
            type(through_sequence) is not int
            or through_sequence <= after_sequence
        ):
            raise ValueError("through_sequence 必须大于 after_sequence")
        if type(limit) is not int or not 0 < limit <= 5000:
            raise ValueError("limit 必须在 1 到 5000 之间")
        query = self._db.query(RunLedgerEventRow).filter(
            RunLedgerEventRow.run_id == normalized,
            RunLedgerEventRow.sequence > after_sequence,
        )
        if through_sequence is not None:
            query = query.filter(
                RunLedgerEventRow.sequence <= through_sequence
            )
        rows = (
            query.order_by(RunLedgerEventRow.sequence.asc())
            .limit(limit)
            .all()
        )
        records = tuple(_row_to_record(row) for row in rows)
        self._verify_contiguous(records, after_sequence=after_sequence)
        return records

    @staticmethod
    def _verify_contiguous(
        records: tuple[RunLedgerEventRecord, ...],
        *,
        after_sequence: int,
    ) -> None:
        expected_sequence = after_sequence + 1
        previous_sha256 = ""
        for index, record in enumerate(records):
            if record.sequence != expected_sequence:
                raise RunLedgerIntegrityError(
                    "Run Ledger sequence 不连续："
                    f"期望 {expected_sequence}，实际 {record.sequence}"
                )
            if index > 0 and record.previous_event_sha256 != previous_sha256:
                raise RunLedgerIntegrityError(
                    f"Run Ledger 摘要链断裂：{record.event_id}"
                )
            previous_sha256 = record.event_sha256
            expected_sequence += 1

    def head(self, run_id: str) -> RunLedgerHead | None:
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run_id 不能为空")
        row = self._db.get(RunLedgerStreamHead, normalized)
        if row is None:
            return None
        return RunLedgerHead(
            run_id=str(row.run_id),
            last_sequence=int(row.last_sequence or 0),
            last_event_id=str(row.last_event_id or ""),
            last_event_sha256=str(row.last_event_sha256 or ""),
            terminal_sequence=(
                int(row.terminal_sequence)
                if row.terminal_sequence is not None
                else None
            ),
        )


class SqlAlchemyRunEventLedgerWriter:
    """自带短事务和提交不确定 read-back 的生产写入入口。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory 必须可调用")
        self._session_factory = session_factory

    def append(
        self,
        event: RunLedgerEventDraft,
        *,
        expected_sequence: int | None = None,
    ) -> RunLedgerEventRecord:
        return self.append_many(
            (event,),
            expected_sequences=(expected_sequence,),
        )[0]

    def append_many(
        self,
        events: tuple[RunLedgerEventDraft, ...],
        *,
        expected_sequences: tuple[int | None, ...] | None = None,
    ) -> tuple[RunLedgerEventRecord, ...]:
        """在一个短事务中追加同一 Run 的一组连续事实。"""

        drafts = tuple(events)
        if not drafts or any(
            not isinstance(event, RunLedgerEventDraft) for event in drafts
        ):
            raise TypeError("events 必须是非空 RunLedgerEventDraft tuple")
        run_ids = {event.run_id for event in drafts}
        if len(run_ids) != 1:
            raise ValueError("append_many 只能追加同一 run_id 的事实")
        sequences = (
            tuple(expected_sequences)
            if expected_sequences is not None
            else (None,) * len(drafts)
        )
        if len(sequences) != len(drafts):
            raise ValueError("expected_sequences 数量必须与 events 一致")
        db = self._session_factory()
        try:
            def operation() -> tuple[RunLedgerEventRecord, ...]:
                ledger = SqlAlchemyRunEventLedger(db)
                records = tuple(
                    ledger.append(event, expected_sequence=expected)
                    for event, expected in zip(drafts, sequences, strict=True)
                )
                db.commit()
                return records

            return run_sqlite_locked_retry(
                operation,
                rollback=db.rollback,
                label="run_ledger_append",
                logger=logger,
            )
        except BaseException as exc:
            db.rollback()
            recovered = self._read_back_many(drafts)
            if recovered is not None:
                for record, expected in zip(
                    recovered,
                    sequences,
                    strict=True,
                ):
                    if expected is not None and record.sequence != expected:
                        raise RunLedgerConflictError(
                            "提交后 read-back 的 sequence 与期望不一致"
                        ) from exc
                return recovered
            if isinstance(exc, IntegrityError):
                raise RunLedgerConflictError(
                    f"Run Ledger 并发追加冲突：{drafts[0].run_id}"
                ) from exc
            raise
        finally:
            db.close()

    def _read_back_many(
        self,
        events: tuple[RunLedgerEventDraft, ...],
    ) -> tuple[RunLedgerEventRecord, ...] | None:
        db = self._session_factory()
        try:
            ledger = SqlAlchemyRunEventLedger(db)
            records: list[RunLedgerEventRecord] = []
            for event in events:
                record = ledger.get(event.event_id)
                if record is None:
                    return None
                if not _same_event(record, event):
                    raise RunLedgerConflictError(
                        f"event_id 已绑定不同事实：{event.event_id}"
                    )
                records.append(record)
            return tuple(records)
        finally:
            db.close()

    def head(self, run_id: str) -> RunLedgerHead | None:
        db = self._session_factory()
        try:
            return SqlAlchemyRunEventLedger(db).head(run_id)
        finally:
            db.close()


__all__ = [
    "SqlAlchemyRunEventLedger",
    "SqlAlchemyRunEventLedgerWriter",
]
