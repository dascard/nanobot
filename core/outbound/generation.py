"""主动出站生成结果落账与 outbox 原子物化适配器。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.db.models.outbound import (
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
)
from core.outbound.contracts import (
    OutboundConflictError,
    OutboundFencingError,
    OutboundSafetyError,
    OutboxCommitResult,
)
from core.outbound.control import (
    _locked_current,
    _open_circuit_row,
    _require_writer,
)
from core.outbound.policy import (
    assert_delivery_contract as _assert_delivery_contract,
    canonical_json as _canonical_json,
    circuit_facts as _circuit_facts,
    require_text as _text,
    safe_summary as _summary,
    utc_naive as _utc_naive,
)
from core.outbound.projection import project_outbound_source as _project_outbound_source
from core.outbound.run_claims import _assert_run_control, _require_generation_run


def _same_outbox_facts(
    row: OutboundDeliveryOutbox,
    *,
    run_id: int,
    idempotency_key: str,
    destination_snapshot_json: str,
    destination_fingerprint: str,
    target_type: str,
    endpoint_key: str,
    payload_json: str,
    payload_sha256: str,
    max_attempts: int,
    retry_deadline_at: datetime,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
) -> bool:
    return (
        int(row.run_id) == int(run_id)
        and row.idempotency_key == idempotency_key
        and row.destination_snapshot_json == destination_snapshot_json
        and row.destination_fingerprint == destination_fingerprint
        and row.target_type == target_type
        and row.endpoint_key == endpoint_key
        and row.payload_json == payload_json
        and row.payload_sha256 == payload_sha256
        and int(row.max_attempts) == int(max_attempts)
        and row.retry_deadline_at == retry_deadline_at
        and row.endpoint_config_revision == endpoint_config_revision
        and row.payload_contract_fingerprint == payload_contract_fingerprint
        and int(row.replay_sequence) == 0
        and row.replay_of_outbox_id is None
    )


def _assert_terminal_generation_settlement(
    db: Session,
    *,
    run_id: int,
    generation_attempt_id: int,
    owner: str,
    claim_token: str,
    outbox: OutboundDeliveryOutbox,
    payload_sha256: str,
    model_trace_id: str,
    generation_error_type: str,
    generation_error_summary: str,
) -> None:
    """验证已有 outbox 确由本次 generation attempt 按相同事实结算。"""

    db.expire_all()
    run = db.get(OutboundRun, int(run_id))
    attempt = db.get(OutboundGenerationAttempt, int(generation_attempt_id))
    if (
        run is None
        or int(outbox.run_id) != int(run_id)
        or run.active_outbox_id != int(outbox.id)
        or attempt is None
        or int(attempt.run_id) != int(run_id)
        or attempt.owner != owner
        or attempt.fencing_token != claim_token
    ):
        raise OutboundFencingError("已有 outbox 不属于当前 generation attempt")
    if attempt.status in {"started", "abandoned"}:
        raise OutboundFencingError("generation attempt 不是 outbox 的终态赢家")
    expected_status = "failed" if generation_error_type else "succeeded"
    expected_content_sha256 = "" if generation_error_type else payload_sha256
    if (
        attempt.status != expected_status
        or str(attempt.content_sha256 or "") != expected_content_sha256
        or str(attempt.error_type or "") != generation_error_type
        or str(attempt.error_summary or "") != generation_error_summary
        or str(attempt.model_trace_id or "") != model_trace_id
    ):
        raise OutboundConflictError("已有 outbox 的 generation 结算事实不一致")


def _block_prepared_outbound_run(
    db: Session,
    *,
    run: OutboundRun,
    owner: str,
    claim_token: str,
    reason_type: str,
    reason_summary: str,
    current: datetime,
) -> None:
    updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.status == "claimed",
            OutboundRun.claim_owner == owner,
            OutboundRun.claim_token == claim_token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.status: "blocked",
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.failure_type: reason_type[:64],
                OutboundRun.failure_summary: _summary(reason_summary),
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise OutboundFencingError("预生成候选阻断 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="blocked",
        current=current,
        error_summary=reason_summary,
    )
    db.flush()


def commit_prepared_outbox(
    db: Session,
    *,
    run_id: int,
    owner: str,
    claim_token: str,
    idempotency_key: str,
    destination_snapshot: Mapping[str, Any],
    destination_fingerprint: str,
    target_type: str,
    endpoint_key: str,
    payload: Mapping[str, Any],
    max_attempts: int,
    retry_deadline_at: datetime,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
    now: datetime | None = None,
) -> OutboxCommitResult:
    """把已完成业务评估的候选原子提交为 outbox，不伪造模型 attempt。"""

    normalized_owner = _text(owner, name="owner", max_length=128)
    token = _text(claim_token, name="claim_token", max_length=64)
    run_probe = db.get(OutboundRun, int(run_id))
    if run_probe is None:
        raise OutboundFencingError("prepared candidate claim 已失效")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    key = _text(idempotency_key, name="idempotency_key", max_length=255)
    destination_json, _destination_sha = _canonical_json(
        destination_snapshot,
        name="destination_snapshot",
    )
    destination = _text(
        destination_fingerprint,
        name="destination_fingerprint",
        max_length=64,
    )
    normalized_target_type = _text(target_type, name="target_type", max_length=16)
    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    payload_json, payload_sha256 = _canonical_json(payload, name="payload")
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts 必须是正整数")
    deadline = _utc_naive(retry_deadline_at)
    if deadline <= current:
        raise ValueError("retry_deadline_at 必须晚于 now")
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    contract = _text(
        payload_contract_fingerprint,
        name="payload_contract_fingerprint",
        max_length=64,
    )

    existing = (
        db.query(OutboundDeliveryOutbox)
        .filter(OutboundDeliveryOutbox.idempotency_key == key)
        .first()
    )
    if existing is not None:
        if not _same_outbox_facts(
            existing,
            run_id=run_id,
            idempotency_key=key,
            destination_snapshot_json=destination_json,
            destination_fingerprint=destination,
            target_type=normalized_target_type,
            endpoint_key=endpoint,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
            max_attempts=max_attempts,
            retry_deadline_at=deadline,
            endpoint_config_revision=revision,
            payload_contract_fingerprint=contract,
        ):
            raise OutboundConflictError("同一 idempotency_key 的不可变事实不一致")
        return OutboxCommitResult(
            outbox_id=int(existing.id),
            run_id=int(existing.run_id),
            created=False,
            payload_sha256=str(existing.payload_sha256),
            status=str(existing.status),
            reason_type="",
        )

    run = _require_generation_run(
        db,
        run_id=run_id,
        owner=normalized_owner,
        claim_token=token,
        current=current,
        allowed_statuses={"claimed"},
    )
    _require_writer(
        db,
        source_type=str(run.source_type),
        owner=str(run.writer_owner),
        token=str(run.writer_token),
        protocol_version=int(run.writer_protocol_version),
        current=current,
    )
    _assert_delivery_contract(
        run,
        destination_snapshot=destination_snapshot,
        destination_fingerprint=destination,
        target_type=normalized_target_type,
        endpoint_key=endpoint,
        payload_contract_fingerprint=contract,
    )
    try:
        _assert_run_control(db, run=run, current=current)
    except OutboundSafetyError as exc:
        _block_prepared_outbound_run(
            db,
            run=run,
            owner=normalized_owner,
            claim_token=token,
            reason_type="cutover_changed",
            reason_summary=str(exc),
            current=current,
        )
        return OutboxCommitResult(
            outbox_id=None,
            run_id=int(run.id),
            created=False,
            payload_sha256=payload_sha256,
            status="blocked",
            reason_type="cutover_changed",
        )
    open_circuit = _open_circuit_row(db, _circuit_facts(
        endpoint_key=endpoint,
        destination_fingerprint=destination,
        payload_contract_fingerprint=contract,
        config_revision=revision,
    ))
    if open_circuit is not None:
        _block_prepared_outbound_run(
            db,
            run=run,
            owner=normalized_owner,
            claim_token=token,
            reason_type="circuit_open",
            reason_summary=f"{open_circuit.scope_type} circuit 已打开",
            current=current,
        )
        return OutboxCommitResult(
            outbox_id=None,
            run_id=int(run.id),
            created=False,
            payload_sha256=payload_sha256,
            status="blocked",
            reason_type="circuit_open",
        )

    insert_result = db.execute(
        sqlite_insert(OutboundDeliveryOutbox)
        .values(
            run_id=int(run.id),
            idempotency_key=key,
            destination_snapshot_json=destination_json,
            destination_fingerprint=destination,
            target_type=normalized_target_type,
            endpoint_key=endpoint,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
            status="pending",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            next_attempt_at=None,
            allocated_attempt_count=0,
            request_started_count=0,
            max_attempts=max_attempts,
            retry_deadline_at=deadline,
            last_error_type="",
            last_error_summary="",
            delivered_at=None,
            cancelled_at=None,
            cancel_reason_type=None,
            replay_of_outbox_id=None,
            replay_sequence=0,
            replay_request_sha256="",
            cutover_epoch=int(run.cutover_epoch),
            endpoint_config_revision=revision,
            payload_contract_fingerprint=contract,
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_nothing()
    )
    created = insert_result.rowcount == 1
    outbox = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            or_(
                OutboundDeliveryOutbox.idempotency_key == key,
                (
                    (OutboundDeliveryOutbox.run_id == int(run.id))
                    & (
                        OutboundDeliveryOutbox.destination_fingerprint
                        == destination
                    )
                    & (OutboundDeliveryOutbox.replay_sequence == 0)
                ),
            )
        )
        .order_by(OutboundDeliveryOutbox.id.asc())
        .first()
    )
    if outbox is None or not _same_outbox_facts(
        outbox,
        run_id=run_id,
        idempotency_key=key,
        destination_snapshot_json=destination_json,
        destination_fingerprint=destination,
        target_type=normalized_target_type,
        endpoint_key=endpoint,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
        max_attempts=max_attempts,
        retry_deadline_at=deadline,
        endpoint_config_revision=revision,
        payload_contract_fingerprint=contract,
    ):
        raise OutboundConflictError("outbox 唯一叶已存在不同事实")
    if not created:
        return OutboxCommitResult(
            outbox_id=int(outbox.id),
            run_id=int(outbox.run_id),
            created=False,
            payload_sha256=str(outbox.payload_sha256),
            status=str(outbox.status),
            reason_type="",
        )

    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.status == "claimed",
            OutboundRun.claim_owner == normalized_owner,
            OutboundRun.claim_token == token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.status: "queued",
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.attempted_at: current,
                OutboundRun.generated_at: current,
                OutboundRun.active_outbox_id: int(outbox.id),
                OutboundRun.failure_type: "",
                OutboundRun.failure_summary: "",
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if run_updated != 1:
        raise OutboundFencingError("预生成候选提交 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="queued",
        current=current,
    )
    db.flush()
    result = OutboxCommitResult(
        outbox_id=int(outbox.id),
        run_id=int(run.id),
        created=True,
        payload_sha256=payload_sha256,
        status="pending",
        reason_type="",
    )
    db.expire_all()
    return result


def _abandon_generated_result(
    db: Session,
    *,
    run: OutboundRun,
    attempt: OutboundGenerationAttempt,
    owner: str,
    claim_token: str,
    reason_type: str,
    reason_summary: str,
    current: datetime,
) -> None:
    attempt_updated = (
        db.query(OutboundGenerationAttempt)
        .filter(
            OutboundGenerationAttempt.id == int(attempt.id),
            OutboundGenerationAttempt.run_id == int(run.id),
            OutboundGenerationAttempt.status == "started",
            OutboundGenerationAttempt.owner == owner,
            OutboundGenerationAttempt.fencing_token == claim_token,
        )
        .update(
            {
                OutboundGenerationAttempt.status: "abandoned",
                OutboundGenerationAttempt.completed_at: current,
                OutboundGenerationAttempt.error_type: reason_type[:64],
                OutboundGenerationAttempt.error_summary: _summary(reason_summary),
            },
            synchronize_session=False,
        )
    )
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.status == "generating",
            OutboundRun.claim_owner == owner,
            OutboundRun.claim_token == claim_token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.status: "blocked",
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.failure_type: reason_type[:64],
                OutboundRun.failure_summary: _summary(reason_summary),
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if attempt_updated != 1 or run_updated != 1:
        raise OutboundFencingError("生成结果废弃 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="blocked",
        current=current,
        error_summary=reason_summary,
    )
    db.flush()
    db.expire_all()


def commit_generated_outbox(
    db: Session,
    *,
    run_id: int,
    generation_attempt_id: int,
    owner: str,
    claim_token: str,
    idempotency_key: str,
    destination_snapshot: Mapping[str, Any],
    destination_fingerprint: str,
    target_type: str,
    endpoint_key: str,
    payload: Mapping[str, Any],
    max_attempts: int,
    retry_deadline_at: datetime,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
    model_trace_id: str = "",
    generation_error_type: str = "",
    generation_error_summary: Any = "",
    now: datetime | None = None,
) -> OutboxCommitResult:
    normalized_owner = _text(owner, name="owner", max_length=128)
    token = _text(claim_token, name="claim_token", max_length=64)
    run_probe = db.get(OutboundRun, int(run_id))
    if run_probe is None:
        raise OutboundFencingError("generation claim 已失效")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    key = _text(idempotency_key, name="idempotency_key", max_length=255)
    destination_json, _destination_sha = _canonical_json(
        destination_snapshot,
        name="destination_snapshot",
    )
    destination = _text(
        destination_fingerprint,
        name="destination_fingerprint",
        max_length=64,
    )
    normalized_target_type = _text(target_type, name="target_type", max_length=16)
    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    payload_json, payload_sha256 = _canonical_json(payload, name="payload")
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts 必须是正整数")
    deadline = _utc_naive(retry_deadline_at)
    if deadline <= current:
        raise ValueError("retry_deadline_at 必须晚于 now")
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    contract = _text(
        payload_contract_fingerprint,
        name="payload_contract_fingerprint",
        max_length=64,
    )
    trace_id = str(model_trace_id or "")[:128]
    fallback_error_type = str(generation_error_type or "").strip()
    if len(fallback_error_type) > 64:
        raise ValueError("generation_error_type 不能超过 64 字符")
    fallback_error_summary = (
        _summary(generation_error_summary) if fallback_error_type else ""
    )

    existing = (
        db.query(OutboundDeliveryOutbox)
        .filter(OutboundDeliveryOutbox.idempotency_key == key)
        .first()
    )
    if existing is not None:
        if not _same_outbox_facts(
            existing,
            run_id=run_id,
            idempotency_key=key,
            destination_snapshot_json=destination_json,
            destination_fingerprint=destination,
            target_type=normalized_target_type,
            endpoint_key=endpoint,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
            max_attempts=max_attempts,
            retry_deadline_at=deadline,
            endpoint_config_revision=revision,
            payload_contract_fingerprint=contract,
        ):
            raise OutboundConflictError("同一 idempotency_key 的不可变事实不一致")
        _assert_terminal_generation_settlement(
            db,
            run_id=run_id,
            generation_attempt_id=generation_attempt_id,
            owner=normalized_owner,
            claim_token=token,
            outbox=existing,
            payload_sha256=payload_sha256,
            model_trace_id=trace_id,
            generation_error_type=fallback_error_type,
            generation_error_summary=fallback_error_summary,
        )
        return OutboxCommitResult(
            outbox_id=int(existing.id),
            run_id=int(existing.run_id),
            created=False,
            payload_sha256=str(existing.payload_sha256),
            status=str(existing.status),
            reason_type="",
        )

    run = _require_generation_run(
        db,
        run_id=run_id,
        owner=normalized_owner,
        claim_token=token,
        current=current,
        allowed_statuses={"generating"},
    )
    _require_writer(
        db,
        source_type=str(run.source_type),
        owner=str(run.writer_owner),
        token=str(run.writer_token),
        protocol_version=int(run.writer_protocol_version),
        current=current,
    )
    _assert_delivery_contract(
        run,
        destination_snapshot=destination_snapshot,
        destination_fingerprint=destination,
        target_type=normalized_target_type,
        endpoint_key=endpoint,
        payload_contract_fingerprint=contract,
    )
    attempt = db.get(OutboundGenerationAttempt, int(generation_attempt_id))
    if (
        attempt is None
        or int(attempt.run_id) != int(run.id)
        or attempt.status != "started"
        or attempt.owner != normalized_owner
        or attempt.fencing_token != token
    ):
        raise OutboundFencingError("generation attempt 已失效")
    try:
        _assert_run_control(db, run=run, current=current)
    except OutboundSafetyError as exc:
        _abandon_generated_result(
            db,
            run=run,
            attempt=attempt,
            owner=normalized_owner,
            claim_token=token,
            reason_type="cutover_changed",
            reason_summary=str(exc),
            current=current,
        )
        return OutboxCommitResult(
            outbox_id=None,
            run_id=int(run.id),
            created=False,
            payload_sha256=payload_sha256,
            status="blocked",
            reason_type="cutover_changed",
        )
    open_circuit = _open_circuit_row(db, _circuit_facts(
        endpoint_key=endpoint,
        destination_fingerprint=destination,
        payload_contract_fingerprint=contract,
        config_revision=revision,
    ))
    if open_circuit is not None:
        _abandon_generated_result(
            db,
            run=run,
            attempt=attempt,
            owner=normalized_owner,
            claim_token=token,
            reason_type="circuit_open",
            reason_summary=f"{open_circuit.scope_type} circuit 已打开",
            current=current,
        )
        return OutboxCommitResult(
            outbox_id=None,
            run_id=int(run.id),
            created=False,
            payload_sha256=payload_sha256,
            status="blocked",
            reason_type="circuit_open",
        )

    insert_result = db.execute(
        sqlite_insert(OutboundDeliveryOutbox)
        .values(
            run_id=int(run.id),
            idempotency_key=key,
            destination_snapshot_json=destination_json,
            destination_fingerprint=destination,
            target_type=normalized_target_type,
            endpoint_key=endpoint,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
            status="pending",
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            next_attempt_at=None,
            allocated_attempt_count=0,
            request_started_count=0,
            max_attempts=max_attempts,
            retry_deadline_at=deadline,
            last_error_type="",
            last_error_summary="",
            delivered_at=None,
            cancelled_at=None,
            cancel_reason_type=None,
            replay_of_outbox_id=None,
            replay_sequence=0,
            replay_request_sha256="",
            cutover_epoch=int(run.cutover_epoch),
            endpoint_config_revision=revision,
            payload_contract_fingerprint=contract,
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_nothing()
    )
    created = insert_result.rowcount == 1
    outbox = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            or_(
                OutboundDeliveryOutbox.idempotency_key == key,
                (
                    (OutboundDeliveryOutbox.run_id == int(run.id))
                    & (
                        OutboundDeliveryOutbox.destination_fingerprint
                        == destination
                    )
                    & (OutboundDeliveryOutbox.replay_sequence == 0)
                ),
            )
        )
        .order_by(OutboundDeliveryOutbox.id.asc())
        .first()
    )
    if outbox is None or not _same_outbox_facts(
        outbox,
        run_id=run_id,
        idempotency_key=key,
        destination_snapshot_json=destination_json,
        destination_fingerprint=destination,
        target_type=normalized_target_type,
        endpoint_key=endpoint,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
        max_attempts=max_attempts,
        retry_deadline_at=deadline,
        endpoint_config_revision=revision,
        payload_contract_fingerprint=contract,
    ):
        raise OutboundConflictError("outbox 唯一叶已存在不同事实")
    if not created:
        _assert_terminal_generation_settlement(
            db,
            run_id=run_id,
            generation_attempt_id=generation_attempt_id,
            owner=normalized_owner,
            claim_token=token,
            outbox=outbox,
            payload_sha256=payload_sha256,
            model_trace_id=trace_id,
            generation_error_type=fallback_error_type,
            generation_error_summary=fallback_error_summary,
        )
        return OutboxCommitResult(
            outbox_id=int(outbox.id),
            run_id=int(outbox.run_id),
            created=False,
            payload_sha256=str(outbox.payload_sha256),
            status=str(outbox.status),
            reason_type="",
        )

    attempt_values = (
        {
            OutboundGenerationAttempt.status: "failed",
            OutboundGenerationAttempt.completed_at: current,
            OutboundGenerationAttempt.model_trace_id: trace_id,
            OutboundGenerationAttempt.content_sha256: "",
            OutboundGenerationAttempt.error_type: fallback_error_type,
            OutboundGenerationAttempt.error_summary: fallback_error_summary,
        }
        if fallback_error_type
        else {
            OutboundGenerationAttempt.status: "succeeded",
            OutboundGenerationAttempt.completed_at: current,
            OutboundGenerationAttempt.model_trace_id: trace_id,
            OutboundGenerationAttempt.content_sha256: payload_sha256,
            OutboundGenerationAttempt.error_type: "",
            OutboundGenerationAttempt.error_summary: "",
        }
    )
    attempt_updated = (
        db.query(OutboundGenerationAttempt)
        .filter(
            OutboundGenerationAttempt.id == int(attempt.id),
            OutboundGenerationAttempt.run_id == int(run.id),
            OutboundGenerationAttempt.status == "started",
            OutboundGenerationAttempt.owner == normalized_owner,
            OutboundGenerationAttempt.fencing_token == token,
        )
        .update(attempt_values, synchronize_session=False)
    )
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.status == "generating",
            OutboundRun.claim_owner == normalized_owner,
            OutboundRun.claim_token == token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.status: "queued",
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.generated_at: current,
                OutboundRun.active_outbox_id: int(outbox.id),
                OutboundRun.failure_type: "",
                OutboundRun.failure_summary: "",
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if attempt_updated != 1 or run_updated != 1:
        raise OutboundFencingError("生成提交 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="queued",
        current=current,
    )
    db.flush()
    result = OutboxCommitResult(
        outbox_id=int(outbox.id),
        run_id=int(run.id),
        created=True,
        payload_sha256=payload_sha256,
        status="pending",
        reason_type="",
    )
    db.expire_all()
    return result


def fail_outbound_generation(
    db: Session,
    *,
    run_id: int,
    generation_attempt_id: int,
    owner: str,
    claim_token: str,
    error_type: str,
    error_summary: Any,
    now: datetime | None = None,
) -> bool:
    normalized_owner = _text(owner, name="owner", max_length=128)
    token = _text(claim_token, name="claim_token", max_length=64)
    normalized_error_type = _text(
        error_type,
        name="error_type",
        max_length=64,
    )
    normalized_summary = _summary(error_summary)
    run_probe = db.get(OutboundRun, int(run_id))
    if run_probe is None:
        raise OutboundFencingError("generation claim 已失效")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    run = _require_generation_run(
        db,
        run_id=run_id,
        owner=normalized_owner,
        claim_token=token,
        current=current,
        allowed_statuses={"generating"},
    )
    _require_writer(
        db,
        source_type=str(run.source_type),
        owner=str(run.writer_owner),
        token=str(run.writer_token),
        protocol_version=int(run.writer_protocol_version),
        current=current,
    )
    attempt = db.get(OutboundGenerationAttempt, int(generation_attempt_id))
    if (
        attempt is None
        or int(attempt.run_id) != int(run.id)
        or attempt.status != "started"
        or attempt.owner != normalized_owner
        or attempt.fencing_token != token
    ):
        raise OutboundFencingError("generation attempt 已失效")
    attempt_updated = (
        db.query(OutboundGenerationAttempt)
        .filter(
            OutboundGenerationAttempt.id == int(attempt.id),
            OutboundGenerationAttempt.run_id == int(run.id),
            OutboundGenerationAttempt.status == "started",
            OutboundGenerationAttempt.owner == normalized_owner,
            OutboundGenerationAttempt.fencing_token == token,
        )
        .update(
            {
                OutboundGenerationAttempt.status: "failed",
                OutboundGenerationAttempt.completed_at: current,
                OutboundGenerationAttempt.error_type: normalized_error_type,
                OutboundGenerationAttempt.error_summary: normalized_summary,
            },
            synchronize_session=False,
        )
    )
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.status == "generating",
            OutboundRun.claim_owner == normalized_owner,
            OutboundRun.claim_token == token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
            OutboundRun.writer_owner == str(run.writer_owner),
            OutboundRun.writer_token == str(run.writer_token),
            OutboundRun.writer_protocol_version
            == int(run.writer_protocol_version),
        )
        .update(
            {
                OutboundRun.status: "failed",
                OutboundRun.claim_owner: None,
                OutboundRun.claim_token: None,
                OutboundRun.claim_expires_at: None,
                OutboundRun.failure_type: normalized_error_type,
                OutboundRun.failure_summary: normalized_summary,
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if attempt_updated != 1 or run_updated != 1:
        raise OutboundFencingError("生成失败结算 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="failed",
        current=current,
        error_summary=normalized_summary,
    )
    db.flush()
    return True



__all__ = [
    "commit_generated_outbox",
    "commit_prepared_outbox",
    "fail_outbound_generation",
]
