"""主动出站投递 claim、请求边界与安全取消适配器。"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, or_
from sqlalchemy.orm import Session

from core.db.models.chat import User
from core.db.models.outbound import (
    OutboundDeliveryAttempt,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
)
from core.db.models.proactive import ProactiveOutreachLog
from core.outbound.contracts import (
    DeliveryClaimHandle,
    DeliverySettlementResult,
    LegacyOutreachResolutionResult,
    OutboundConflictError,
    OutboundFencingError,
    OutboundSafetyError,
    OutboxCancellationResult,
    RequestStartResult,
    SourceCancellationSummary,
)
from core.outbound.control import (
    _control,
    _locked_current,
    _mode_for_occurrence,
    _open_circuit_row,
    _require_writer,
    acquire_or_renew_delivery_writer,
)
from core.outbound.policy import (
    circuit_facts as _circuit_facts,
    local_naive_to_utc_naive as _local_naive_to_utc_naive,
    proactive_outreach_source_revision,
    require_positive_seconds as _positive_seconds,
    require_text as _text,
    safe_summary as _summary,
    utc_naive as _utc_naive,
)
from core.outbound.projection import (
    project_outbound_source as _project_outbound_source,
    validate_proactive_source as _validated_proactive_source,
)


_DUE_OUTBOX_STATUSES = frozenset({"pending", "retry_wait", "blocked"})


def _outbox_circuit_facts(
    outbox: OutboundDeliveryOutbox,
    *,
    actual_config_revision: str,
) -> tuple[tuple[str, str, str], ...]:
    return _circuit_facts(
        endpoint_key=str(outbox.endpoint_key),
        destination_fingerprint=str(outbox.destination_fingerprint),
        payload_contract_fingerprint=str(outbox.payload_contract_fingerprint),
        config_revision=actual_config_revision,
    )


def _worker_control_allows(
    db: Session,
    *,
    run: OutboundRun,
    outbox: OutboundDeliveryOutbox,
) -> bool:
    control = _control(db, str(run.source_type))
    return (
        run.delivery_mode == "outbox"
        and control.mode in {"outbox_active", "outbox_draining"}
        and int(control.cutover_epoch) == int(run.cutover_epoch)
        and int(outbox.cutover_epoch) == int(run.cutover_epoch)
    )


def _live_delivery_control_allows(
    db: Session,
    *,
    run: OutboundRun,
    outbox: OutboundDeliveryOutbox,
    current: datetime,
) -> bool:
    if _worker_control_allows(db, run=run, outbox=outbox):
        return True
    if run.delivery_mode != "legacy_direct":
        return False
    try:
        mode, epoch = _mode_for_occurrence(
            _control(db, str(run.source_type)),
            occurrence_at=run.scheduled_for or current,
        )
    except OutboundSafetyError:
        return False
    return (
        mode == "legacy_direct"
        and epoch == int(run.cutover_epoch)
        and int(outbox.cutover_epoch) == int(run.cutover_epoch)
    )


def claim_legacy_direct_outbox(
    db: Session,
    *,
    outbox_id: int,
    worker_owner: str,
    lease_seconds: int | float,
    writer_owner: str,
    writer_token: str,
    writer_protocol_version: int,
    writer_lease_seconds: int | float,
    endpoint_key: str,
    endpoint_config_revision: str,
    expected_writer_version: int | None = None,
    now: datetime | None = None,
) -> DeliveryClaimHandle | None:
    """由 source-specific 兼容入口领取指定 legacy leaf。"""

    owner = _text(worker_owner, name="worker_owner", max_length=128)
    seconds = _positive_seconds(lease_seconds, name="lease_seconds")
    normalized_writer_owner = _text(
        writer_owner,
        name="writer_owner",
        max_length=128,
    )
    normalized_writer_token = _text(
        writer_token,
        name="writer_token",
        max_length=64,
    )
    normalized_endpoint = _text(
        endpoint_key,
        name="endpoint_key",
        max_length=64,
    )
    normalized_writer_lease_seconds = _positive_seconds(
        writer_lease_seconds,
        name="writer_lease_seconds",
    )
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    if (
        expected_writer_version is not None
        and (
            type(expected_writer_version) is not int
            or expected_writer_version < 0
        )
    ):
        raise ValueError("expected_writer_version 必须是非负整数")
    outbox_probe = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run_probe = (
        db.get(OutboundRun, int(outbox_probe.run_id))
        if outbox_probe is not None
        else None
    )
    if outbox_probe is None or run_probe is None:
        raise OutboundFencingError("legacy direct leaf 不存在")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run = db.get(OutboundRun, int(run_probe.id))
    if (
        outbox is None
        or run is None
        or run.delivery_mode != "legacy_direct"
        or run.active_outbox_id != outbox.id
        or outbox.endpoint_key != normalized_endpoint
        or outbox.status not in _DUE_OUTBOX_STATUSES
        or int(outbox.request_started_count) >= int(outbox.max_attempts)
        or outbox.retry_deadline_at <= current
        or (
            outbox.status == "retry_wait"
            and (
                outbox.next_attempt_at is None
                or outbox.next_attempt_at > current
            )
        )
    ):
        return None
    if expected_writer_version is None:
        writer = acquire_or_renew_delivery_writer(
            db,
            source_type=str(run.source_type),
            owner=normalized_writer_owner,
            token=normalized_writer_token,
            protocol_version=writer_protocol_version,
            lease_seconds=normalized_writer_lease_seconds,
            now=current,
        )
        if not writer.acquired:
            return None
    else:
        try:
            writer_control = _require_writer(
                db,
                source_type=str(run.source_type),
                owner=normalized_writer_owner,
                token=normalized_writer_token,
                protocol_version=writer_protocol_version,
                current=current,
            )
        except OutboundFencingError:
            return None
        if int(writer_control.writer_version) != expected_writer_version:
            return None
    outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run = db.get(OutboundRun, int(run_probe.id))
    if (
        outbox is None
        or run is None
        or run.delivery_mode != "legacy_direct"
        or run.active_outbox_id != outbox.id
        or outbox.endpoint_key != normalized_endpoint
        or outbox.status not in _DUE_OUTBOX_STATUSES
        or int(outbox.request_started_count) >= int(outbox.max_attempts)
        or outbox.retry_deadline_at <= current
        or (
            outbox.status == "retry_wait"
            and (
                outbox.next_attempt_at is None
                or outbox.next_attempt_at > current
            )
        )
    ):
        return None
    if (
        run.writer_owner != normalized_writer_owner
        or run.writer_token != normalized_writer_token
        or int(run.writer_protocol_version) != int(writer_protocol_version)
    ):
        rebound = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.active_outbox_id == int(outbox.id),
                OutboundRun.delivery_mode == "legacy_direct",
                OutboundRun.writer_owner == run.writer_owner,
                OutboundRun.writer_token == run.writer_token,
                OutboundRun.writer_protocol_version
                == int(run.writer_protocol_version),
                OutboundRun.status.in_(("queued", "blocked")),
            )
            .update(
                {
                    OutboundRun.writer_owner: normalized_writer_owner,
                    OutboundRun.writer_token: normalized_writer_token,
                    OutboundRun.writer_protocol_version: writer_protocol_version,
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if rebound != 1:
            db.expire_all()
            return None
        db.flush()
        db.expire_all()
        outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
        run = db.get(OutboundRun, int(run_probe.id))
        if outbox is None or run is None:
            return None
    _require_writer(
        db,
        source_type=str(run.source_type),
        owner=normalized_writer_owner,
        token=normalized_writer_token,
        protocol_version=writer_protocol_version,
        current=current,
    )
    if not _live_delivery_control_allows(
        db,
        run=run,
        outbox=outbox,
        current=current,
    ):
        raise OutboundSafetyError("legacy direct leaf 与当前 control 不一致")
    open_circuit = _open_circuit_row(
        db,
        _outbox_circuit_facts(outbox, actual_config_revision=revision),
    )
    if open_circuit is not None:
        if outbox.status != "blocked":
            outbox.status = "blocked"
            outbox.next_attempt_at = None
            outbox.last_error_type = "circuit_open"
            outbox.last_error_summary = (
                f"{open_circuit.scope_type} circuit 已打开"
            )
            outbox.updated_at = current
            run.status = "blocked"
            run.failure_type = "circuit_open"
            run.failure_summary = outbox.last_error_summary
            run.updated_at = current
            _project_outbound_source(
                db,
                run=run,
                status="blocked",
                current=current,
                error_summary=outbox.last_error_summary,
            )
            db.flush()
        return None

    previous_status = str(outbox.status)
    attempt_no = int(outbox.allocated_attempt_count) + 1
    lease_token = secrets.token_hex(32)
    lease_expires_at = current + timedelta(seconds=seconds)
    updated = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.id == int(outbox.id),
            OutboundDeliveryOutbox.status == previous_status,
            OutboundDeliveryOutbox.request_started_count
            < OutboundDeliveryOutbox.max_attempts,
            OutboundDeliveryOutbox.retry_deadline_at > current,
        )
        .update(
            {
                OutboundDeliveryOutbox.status: "leased",
                OutboundDeliveryOutbox.lease_owner: owner,
                OutboundDeliveryOutbox.lease_token: lease_token,
                OutboundDeliveryOutbox.lease_expires_at: lease_expires_at,
                OutboundDeliveryOutbox.next_attempt_at: None,
                OutboundDeliveryOutbox.allocated_attempt_count: attempt_no,
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.expire_all()
        return None
    attempt = OutboundDeliveryAttempt(
        outbox_id=int(outbox.id),
        attempt_no=attempt_no,
        worker_owner=owner,
        lease_token=lease_token,
        status="started",
        transport_phase="allocated",
        request_started=False,
        endpoint_config_revision=revision,
        http_status=None,
        result_category="",
        error_type="",
        safe_summary="",
        duration_ms=None,
        settlement_retry_at=None,
        settlement_circuit_scope_type=None,
        settlement_request_sha256="",
        started_at=current,
        request_started_at=None,
        completed_at=None,
        created_at=current,
    )
    db.add(attempt)
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.active_outbox_id == int(outbox.id),
            OutboundRun.status.in_(("queued", "blocked")),
            OutboundRun.delivery_mode == "legacy_direct",
        )
        .update(
            {
                OutboundRun.status: "delivering",
                OutboundRun.failure_type: "",
                OutboundRun.failure_summary: "",
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if run_updated != 1:
        raise OutboundFencingError("legacy direct active leaf 已变化")
    db.flush()
    _project_outbound_source(
        db,
        run=run,
        status="delivering",
        current=current,
    )
    db.flush()
    result = DeliveryClaimHandle(
        outbox_id=int(outbox.id),
        run_id=int(run.id),
        attempt_id=int(attempt.id),
        attempt_no=attempt_no,
        worker_owner=owner,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
        endpoint_key=str(outbox.endpoint_key),
        target_type=str(outbox.target_type),
        endpoint_config_revision=revision,
        destination_snapshot_json=str(outbox.destination_snapshot_json),
        payload_json=str(outbox.payload_json),
        payload_sha256=str(outbox.payload_sha256),
        payload_contract_fingerprint=str(outbox.payload_contract_fingerprint),
    )
    db.expire_all()
    return result


def _terminalize_expired_outbox_leaf(
    db: Session,
    *,
    outbox_id: int,
    endpoint_key: str,
    now: datetime | None,
) -> bool:
    outbox_probe = db.get(OutboundDeliveryOutbox, outbox_id)
    if outbox_probe is None:
        return False
    run_probe = db.get(OutboundRun, int(outbox_probe.run_id))
    if run_probe is None:
        return False
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    outbox = db.get(OutboundDeliveryOutbox, outbox_id)
    if (
        outbox is None
        or outbox.endpoint_key != endpoint_key
        or outbox.status not in {"pending", "retry_wait", "blocked"}
        or outbox.retry_deadline_at > current
    ):
        return False
    previous_status = str(outbox.status)
    updated = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.id == outbox_id,
            OutboundDeliveryOutbox.endpoint_key == endpoint_key,
            OutboundDeliveryOutbox.status == previous_status,
            OutboundDeliveryOutbox.retry_deadline_at <= current,
        )
        .update(
            {
                OutboundDeliveryOutbox.status: "failed",
                OutboundDeliveryOutbox.next_attempt_at: None,
                OutboundDeliveryOutbox.last_error_type: "retry_exhausted",
                OutboundDeliveryOutbox.last_error_summary: (
                    "投递重试期限已到期"
                ),
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        return False
    run = db.get(OutboundRun, int(outbox.run_id))
    if run is not None and run.active_outbox_id == outbox.id:
        run_updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.active_outbox_id == int(outbox.id),
                OutboundRun.status.in_(("queued", "blocked")),
            )
            .update(
                {
                    OutboundRun.status: "failed",
                    OutboundRun.failure_type: "retry_exhausted",
                    OutboundRun.failure_summary: "投递重试期限已到期",
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if run_updated != 1:
            raise OutboundFencingError("过期 outbox 的 run CAS 失败")
        try:
            with db.begin_nested():
                _project_outbound_source(
                    db,
                    run=run,
                    status="failed",
                    current=current,
                    error_summary="投递重试期限已到期",
                )
        except (OutboundFencingError, OutboundConflictError):
            # 来源 revision 已变化时保留新来源，只终结冻结的投递账本。
            db.expire_all()
    db.flush()
    return True


def terminalize_expired_outboxes(
    db: Session,
    *,
    endpoint_key: str,
    source_type: str | None = None,
    delivery_mode: str | None = None,
    now: datetime | None,
) -> int:
    """将超过重试期限的安全 leaf 收敛为失败；调用方负责提交。"""

    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    source = (
        _text(source_type, name="source_type", max_length=32)
        if source_type is not None
        else None
    )
    mode = (
        _text(delivery_mode, name="delivery_mode", max_length=24)
        if delivery_mode is not None
        else None
    )
    if mode is not None and mode not in {"legacy_direct", "outbox"}:
        raise ValueError("delivery_mode 只支持 legacy_direct/outbox")
    observed_at = _utc_naive(now)
    candidate_query = (
        db.query(OutboundDeliveryOutbox)
        .join(OutboundRun, OutboundRun.id == OutboundDeliveryOutbox.run_id)
        .filter(
            OutboundDeliveryOutbox.endpoint_key == endpoint,
            OutboundDeliveryOutbox.status.in_((
                "pending",
                "retry_wait",
                "blocked",
            )),
            OutboundDeliveryOutbox.retry_deadline_at <= observed_at,
        )
    )
    if source is not None:
        candidate_query = candidate_query.filter(
            OutboundRun.source_type == source
        )
    if mode is not None:
        candidate_query = candidate_query.filter(
            OutboundRun.delivery_mode == mode
        )
    candidates = (
        candidate_query.order_by(OutboundDeliveryOutbox.id.asc())
        .limit(100)
        .all()
    )
    terminalized = 0
    for probe in candidates:
        try:
            with db.begin_nested():
                applied = _terminalize_expired_outbox_leaf(
                    db,
                    outbox_id=int(probe.id),
                    endpoint_key=endpoint,
                    now=now,
                )
        except (OutboundFencingError, OutboundConflictError):
            db.expire_all()
            continue
        if applied:
            terminalized += 1
    db.flush()
    return terminalized


def claim_due_outbox(
    db: Session,
    *,
    worker_owner: str,
    lease_seconds: int | float,
    endpoint_config_revision: str,
    endpoint_key: str = "qq_push",
    now: datetime | None = None,
) -> DeliveryClaimHandle | None:
    owner = _text(worker_owner, name="worker_owner", max_length=128)
    seconds = _positive_seconds(lease_seconds, name="lease_seconds")
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    terminalize_expired_outboxes(db, endpoint_key=endpoint, now=now)
    observed_at = _utc_naive(now)
    candidates = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.endpoint_key == endpoint,
            OutboundDeliveryOutbox.status.in_(tuple(_DUE_OUTBOX_STATUSES)),
            OutboundDeliveryOutbox.request_started_count
            < OutboundDeliveryOutbox.max_attempts,
            OutboundDeliveryOutbox.retry_deadline_at > observed_at,
        )
        .filter(
            or_(
                OutboundDeliveryOutbox.status.in_(("pending", "blocked")),
                (
                    (OutboundDeliveryOutbox.status == "retry_wait")
                    & (OutboundDeliveryOutbox.next_attempt_at <= observed_at)
                ),
            )
        )
        .order_by(
            OutboundDeliveryOutbox.next_attempt_at.asc(),
            OutboundDeliveryOutbox.id.asc(),
        )
        .all()
    )
    for candidate in candidates:
        candidate_id = int(candidate.id)
        run_probe = db.get(OutboundRun, int(candidate.run_id))
        if run_probe is None:
            continue
        current = _locked_current(
            db,
            source_type=str(run_probe.source_type),
            now=now,
        )
        lease_expires_at = current + timedelta(seconds=seconds)
        candidate = db.get(OutboundDeliveryOutbox, candidate_id)
        if candidate is None:
            continue
        run = db.get(OutboundRun, int(candidate.run_id))
        if (
            run is None
            or run.active_outbox_id != candidate.id
            or candidate.endpoint_key != endpoint
            or not _worker_control_allows(db, run=run, outbox=candidate)
            or candidate.status not in _DUE_OUTBOX_STATUSES
            or int(candidate.request_started_count) >= int(candidate.max_attempts)
            or candidate.retry_deadline_at <= current
            or (
                candidate.status == "retry_wait"
                and (
                    candidate.next_attempt_at is None
                    or candidate.next_attempt_at > current
                )
            )
        ):
            continue
        open_circuit = _open_circuit_row(
            db,
            _outbox_circuit_facts(
                candidate,
                actual_config_revision=revision,
            ),
        )
        previous_status = str(candidate.status)
        if open_circuit is not None:
            if previous_status != "blocked":
                (
                    db.query(OutboundDeliveryOutbox)
                    .filter(
                        OutboundDeliveryOutbox.id == candidate.id,
                        OutboundDeliveryOutbox.status == previous_status,
                    )
                    .update(
                        {
                            OutboundDeliveryOutbox.status: "blocked",
                            OutboundDeliveryOutbox.next_attempt_at: None,
                            OutboundDeliveryOutbox.last_error_type: "circuit_open",
                            OutboundDeliveryOutbox.last_error_summary: (
                                f"{open_circuit.scope_type} circuit 已打开"
                            ),
                            OutboundDeliveryOutbox.updated_at: current,
                        },
                        synchronize_session=False,
                    )
                )
                (
                    db.query(OutboundRun)
                    .filter(
                        OutboundRun.id == run.id,
                        OutboundRun.active_outbox_id == candidate.id,
                    )
                    .update(
                        {
                            OutboundRun.status: "blocked",
                            OutboundRun.failure_type: "circuit_open",
                            OutboundRun.failure_summary: (
                                f"{open_circuit.scope_type} circuit 已打开"
                            ),
                            OutboundRun.updated_at: current,
                        },
                        synchronize_session=False,
                    )
                )
                _project_outbound_source(
                    db,
                    run=run,
                    status="blocked",
                    current=current,
                    error_summary=f"{open_circuit.scope_type} circuit 已打开",
                )
            continue

        attempt_no = int(candidate.allocated_attempt_count) + 1
        lease_token = secrets.token_hex(32)
        updated = (
            db.query(OutboundDeliveryOutbox)
            .filter(
                OutboundDeliveryOutbox.id == int(candidate.id),
                OutboundDeliveryOutbox.status == previous_status,
                OutboundDeliveryOutbox.request_started_count
                < OutboundDeliveryOutbox.max_attempts,
                OutboundDeliveryOutbox.retry_deadline_at > current,
                or_(
                    OutboundDeliveryOutbox.status.in_(("pending", "blocked")),
                    (
                        (OutboundDeliveryOutbox.status == "retry_wait")
                        & (OutboundDeliveryOutbox.next_attempt_at <= current)
                    ),
                ),
            )
            .update(
                {
                    OutboundDeliveryOutbox.status: "leased",
                    OutboundDeliveryOutbox.lease_owner: owner,
                    OutboundDeliveryOutbox.lease_token: lease_token,
                    OutboundDeliveryOutbox.lease_expires_at: lease_expires_at,
                    OutboundDeliveryOutbox.next_attempt_at: None,
                    OutboundDeliveryOutbox.allocated_attempt_count: attempt_no,
                    OutboundDeliveryOutbox.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            db.expire_all()
            continue
        attempt = OutboundDeliveryAttempt(
            outbox_id=int(candidate.id),
            attempt_no=attempt_no,
            worker_owner=owner,
            lease_token=lease_token,
            status="started",
            transport_phase="allocated",
            request_started=False,
            endpoint_config_revision=revision,
            http_status=None,
            result_category="",
            error_type="",
            safe_summary="",
            duration_ms=None,
            settlement_retry_at=None,
            settlement_circuit_scope_type=None,
            settlement_request_sha256="",
            started_at=current,
            request_started_at=None,
            completed_at=None,
            created_at=current,
        )
        db.add(attempt)
        run_updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.active_outbox_id == int(candidate.id),
                OutboundRun.status.in_(("queued", "delivering", "blocked")),
            )
            .update(
                {
                    OutboundRun.status: "delivering",
                    OutboundRun.failure_type: "",
                    OutboundRun.failure_summary: "",
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if run_updated != 1:
            raise OutboundFencingError("active outbox leaf 已变化")
        _project_outbound_source(
            db,
            run=run,
            status="delivering",
            current=current,
        )
        db.flush()
        result = DeliveryClaimHandle(
            outbox_id=int(candidate.id),
            run_id=int(run.id),
            attempt_id=int(attempt.id),
            attempt_no=attempt_no,
            worker_owner=owner,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            endpoint_key=str(candidate.endpoint_key),
            target_type=str(candidate.target_type),
            endpoint_config_revision=revision,
            destination_snapshot_json=str(candidate.destination_snapshot_json),
            payload_json=str(candidate.payload_json),
            payload_sha256=str(candidate.payload_sha256),
            payload_contract_fingerprint=str(
                candidate.payload_contract_fingerprint
            ),
        )
        db.expire_all()
        return result
    db.flush()
    return None


def _require_live_delivery(
    db: Session,
    *,
    outbox_id: int,
    attempt_id: int,
    worker_owner: str,
    lease_token: str,
    current: datetime,
) -> tuple[OutboundDeliveryOutbox, OutboundDeliveryAttempt, OutboundRun]:
    outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    attempt = db.get(OutboundDeliveryAttempt, int(attempt_id))
    if outbox is None or attempt is None:
        raise OutboundFencingError("delivery attempt 不存在")
    run = db.get(OutboundRun, int(outbox.run_id))
    if (
        run is None
        or run.active_outbox_id != outbox.id
        or outbox.status != "leased"
        or outbox.lease_owner != worker_owner
        or outbox.lease_token != lease_token
        or outbox.lease_expires_at is None
        or outbox.lease_expires_at <= current
        or int(attempt.outbox_id) != int(outbox.id)
        or attempt.worker_owner != worker_owner
        or attempt.lease_token != lease_token
        or attempt.status != "started"
        or int(attempt.attempt_no) != int(outbox.allocated_attempt_count)
        or not _live_delivery_control_allows(
            db,
            run=run,
            outbox=outbox,
            current=current,
        )
    ):
        raise OutboundFencingError("delivery lease 或 active leaf 已失效")
    return outbox, attempt, run


def mark_delivery_request_started(
    db: Session,
    *,
    outbox_id: int,
    attempt_id: int,
    worker_owner: str,
    lease_token: str,
    now: datetime | None = None,
) -> RequestStartResult:
    owner = _text(worker_owner, name="worker_owner", max_length=128)
    token = _text(lease_token, name="lease_token", max_length=64)
    outbox_probe = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run_probe = (
        db.get(OutboundRun, int(outbox_probe.run_id))
        if outbox_probe is not None
        else None
    )
    if outbox_probe is None or run_probe is None:
        raise OutboundFencingError("delivery attempt 不存在")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    attempt = db.get(OutboundDeliveryAttempt, int(attempt_id))
    if (
        outbox is not None
        and attempt is not None
        and outbox.status == "leased"
        and outbox.lease_owner == owner
        and outbox.lease_token == token
        and outbox.lease_expires_at is not None
        and outbox.lease_expires_at > current
        and attempt.status == "started"
        and attempt.worker_owner == owner
        and attempt.lease_token == token
        and bool(attempt.request_started)
    ):
        return RequestStartResult(
            applied=False,
            outbox_id=int(outbox.id),
            attempt_id=int(attempt.id),
            request_started_count=int(outbox.request_started_count),
        )
    outbox, attempt, _run = _require_live_delivery(
        db,
        outbox_id=outbox_id,
        attempt_id=attempt_id,
        worker_owner=owner,
        lease_token=token,
        current=current,
    )
    open_circuit = _open_circuit_row(
        db,
        _outbox_circuit_facts(
            outbox,
            actual_config_revision=str(attempt.endpoint_config_revision),
        ),
    )
    if open_circuit is not None:
        raise OutboundSafetyError(
            f"{open_circuit.scope_type} circuit 已打开，禁止越过 request boundary"
        )
    if (
        bool(attempt.request_started)
        or attempt.transport_phase != "allocated"
        or int(outbox.request_started_count) >= int(outbox.max_attempts)
        or outbox.retry_deadline_at <= current
    ):
        raise OutboundSafetyError("网络请求预算或 request boundary 状态无效")
    previous_count = int(outbox.request_started_count)
    attempt_updated = (
        db.query(OutboundDeliveryAttempt)
        .filter(
            OutboundDeliveryAttempt.id == int(attempt.id),
            OutboundDeliveryAttempt.status == "started",
            OutboundDeliveryAttempt.worker_owner == owner,
            OutboundDeliveryAttempt.lease_token == token,
            OutboundDeliveryAttempt.request_started.is_(False),
            OutboundDeliveryAttempt.transport_phase == "allocated",
        )
        .update(
            {
                OutboundDeliveryAttempt.request_started: True,
                OutboundDeliveryAttempt.transport_phase: "request_started",
                OutboundDeliveryAttempt.request_started_at: current,
            },
            synchronize_session=False,
        )
    )
    outbox_updated = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.id == int(outbox.id),
            OutboundDeliveryOutbox.status == "leased",
            OutboundDeliveryOutbox.lease_owner == owner,
            OutboundDeliveryOutbox.lease_token == token,
            OutboundDeliveryOutbox.lease_expires_at > current,
            OutboundDeliveryOutbox.request_started_count == previous_count,
            OutboundDeliveryOutbox.request_started_count
            < OutboundDeliveryOutbox.max_attempts,
            OutboundDeliveryOutbox.retry_deadline_at > current,
        )
        .update(
            {
                OutboundDeliveryOutbox.request_started_count: previous_count + 1,
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if attempt_updated != 1 or outbox_updated != 1:
        raise OutboundFencingError("request boundary CAS 失败")
    db.flush()
    result = RequestStartResult(
        applied=True,
        outbox_id=int(outbox.id),
        attempt_id=int(attempt.id),
        request_started_count=previous_count + 1,
    )
    db.expire_all()
    return result


def cancel_delivery_before_send(
    db: Session,
    *,
    outbox_id: int,
    attempt_id: int,
    worker_owner: str,
    lease_token: str,
    reason_type: str,
    safe_summary: Any,
    now: datetime | None = None,
    _project_source: bool = True,
) -> DeliverySettlementResult:
    owner = _text(worker_owner, name="worker_owner", max_length=128)
    token = _text(lease_token, name="lease_token", max_length=64)
    reason = _text(reason_type, name="reason_type", max_length=64)
    summary = _summary(safe_summary)
    outbox_probe = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run_probe = (
        db.get(OutboundRun, int(outbox_probe.run_id))
        if outbox_probe is not None
        else None
    )
    if outbox_probe is None or run_probe is None:
        raise OutboundFencingError("delivery attempt 不存在")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    outbox, attempt, run = _require_live_delivery(
        db,
        outbox_id=outbox_id,
        attempt_id=attempt_id,
        worker_owner=owner,
        lease_token=token,
        current=current,
    )
    if bool(attempt.request_started) or attempt.transport_phase != "allocated":
        raise OutboundSafetyError("越过 request boundary 后不能安全取消")

    attempt_updated = (
        db.query(OutboundDeliveryAttempt)
        .filter(
            OutboundDeliveryAttempt.id == int(attempt.id),
            OutboundDeliveryAttempt.outbox_id == int(outbox.id),
            OutboundDeliveryAttempt.attempt_no
            == int(outbox.allocated_attempt_count),
            OutboundDeliveryAttempt.status == "started",
            OutboundDeliveryAttempt.worker_owner == owner,
            OutboundDeliveryAttempt.lease_token == token,
            OutboundDeliveryAttempt.request_started.is_(False),
            OutboundDeliveryAttempt.transport_phase == "allocated",
        )
        .update(
            {
                OutboundDeliveryAttempt.status: "cancelled_before_send",
                OutboundDeliveryAttempt.result_category: "before_send",
                OutboundDeliveryAttempt.error_type: reason,
                OutboundDeliveryAttempt.safe_summary: summary,
                OutboundDeliveryAttempt.completed_at: current,
            },
            synchronize_session=False,
        )
    )
    outbox_updated = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.id == int(outbox.id),
            OutboundDeliveryOutbox.status == "leased",
            OutboundDeliveryOutbox.lease_owner == owner,
            OutboundDeliveryOutbox.lease_token == token,
            OutboundDeliveryOutbox.lease_expires_at > current,
            OutboundDeliveryOutbox.request_started_count
            == int(outbox.request_started_count),
        )
        .update(
            {
                OutboundDeliveryOutbox.status: "cancelled",
                OutboundDeliveryOutbox.lease_owner: None,
                OutboundDeliveryOutbox.lease_token: None,
                OutboundDeliveryOutbox.lease_expires_at: None,
                OutboundDeliveryOutbox.next_attempt_at: None,
                OutboundDeliveryOutbox.last_error_type: reason,
                OutboundDeliveryOutbox.last_error_summary: summary,
                OutboundDeliveryOutbox.cancelled_at: current,
                OutboundDeliveryOutbox.cancel_reason_type: reason,
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.active_outbox_id == int(outbox.id),
            OutboundRun.status == "delivering",
        )
        .update(
            {
                OutboundRun.status: "failed",
                OutboundRun.failure_type: reason,
                OutboundRun.failure_summary: summary,
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if attempt_updated != 1 or outbox_updated != 1 or run_updated != 1:
        raise OutboundFencingError("发送前取消 CAS 失败")
    if _project_source:
        _project_outbound_source(
            db,
            run=run,
            status="cancelled",
            current=current,
            error_summary=summary,
        )
    db.flush()
    result = DeliverySettlementResult(
        applied=True,
        outbox_id=int(outbox.id),
        attempt_id=int(attempt.id),
        outbox_status="cancelled",
        run_status="failed",
    )
    db.expire_all()
    return result


def cancel_invalid_delivery_before_send(
    db: Session,
    *,
    outbox_id: int,
    attempt_id: int,
    worker_owner: str,
    lease_token: str,
    now: datetime | None = None,
) -> DeliverySettlementResult | None:
    """由当前 leaf owner 在 request boundary 前执行来源与清除点复检。"""

    owner = _text(worker_owner, name="worker_owner", max_length=128)
    token = _text(lease_token, name="lease_token", max_length=64)
    outbox_probe = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run_probe = (
        db.get(OutboundRun, int(outbox_probe.run_id))
        if outbox_probe is not None
        else None
    )
    if outbox_probe is None or run_probe is None:
        raise OutboundFencingError("delivery attempt 不存在")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    _outbox, _attempt, run = _require_live_delivery(
        db,
        outbox_id=outbox_id,
        attempt_id=attempt_id,
        worker_owner=owner,
        lease_token=token,
        current=current,
    )
    if str(run.source_type) != "proactive_outreach":
        return None

    project_source = True
    try:
        row, snapshot = _validated_proactive_source(db, run)
    except OutboundFencingError:
        reason_type = "source_fenced"
        safe_summary = "主动外呼来源 revision 已变化"
        project_source = False
    else:
        if row.outbound_run_id != int(run.id):
            reason_type = "source_fenced"
            safe_summary = "主动外呼来源 run 已变化"
            project_source = False
        else:
            clear_at = (
                db.query(User.history_clear_at)
                .filter(User.id == str(snapshot["user_id"]))
                .scalar()
            )
            clear_at_utc = (
                _local_naive_to_utc_naive(clear_at)
                if clear_at is not None
                else None
            )
            run_created_at = run.created_at
            if (
                clear_at_utc is not None
                and (
                    run_created_at is None
                    or run_created_at <= clear_at_utc
                )
            ):
                reason_type = "history_cleared"
                safe_summary = "用户历史已在投递前清除"
            elif str(row.status or "") in {
                "cancelled",
                "legacy_ambiguous_hold",
            }:
                reason_type = "source_cancelled"
                safe_summary = "主动外呼来源已取消"
            else:
                return None
    return cancel_delivery_before_send(
        db,
        outbox_id=outbox_id,
        attempt_id=attempt_id,
        worker_owner=owner,
        lease_token=token,
        reason_type=reason_type,
        safe_summary=safe_summary,
        now=current,
        _project_source=project_source,
    )


def cancel_safe_outbox(
    db: Session,
    *,
    outbox_id: int,
    expected_status: str,
    expected_updated_at: datetime,
    reason_type: str,
    safe_summary: Any,
    now: datetime | None = None,
) -> OutboxCancellationResult:
    """按活动 leaf 的显式版本安全取消尚未发送的投递。"""

    expected = _text(
        expected_status,
        name="expected_status",
        max_length=24,
    )
    reason = _text(reason_type, name="reason_type", max_length=64)
    summary = _summary(safe_summary)
    expected_updated = _utc_naive(expected_updated_at)
    outbox_probe = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run_probe = (
        db.get(OutboundRun, int(outbox_probe.run_id))
        if outbox_probe is not None
        else None
    )
    if outbox_probe is None or run_probe is None:
        raise OutboundFencingError("待取消 outbox 或 run 不存在")
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    run = db.get(OutboundRun, int(run_probe.id))
    if outbox is None or run is None:
        raise OutboundFencingError("待取消 outbox 或 run 不存在")
    if outbox.status != expected or outbox.updated_at != expected_updated:
        raise OutboundFencingError("outbox 状态或 updated_at CAS 已失效")
    if expected not in {"pending", "retry_wait", "blocked"}:
        raise OutboundSafetyError("只有未租用的安全 leaf 可以取消")
    if (
        outbox.lease_owner is not None
        or outbox.lease_token is not None
        or outbox.lease_expires_at is not None
    ):
        raise OutboundSafetyError("已租用 leaf 不能由管理员直接取消")
    if run.active_outbox_id != outbox.id:
        raise OutboundFencingError("outbox 已不是 run 的活动 leaf")
    if run.status not in {"queued", "blocked"}:
        raise OutboundFencingError("run 状态与可取消 leaf 不一致")

    outbox_updated = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.id == int(outbox.id),
            OutboundDeliveryOutbox.run_id == int(run.id),
            OutboundDeliveryOutbox.status == expected,
            OutboundDeliveryOutbox.updated_at == expected_updated,
            OutboundDeliveryOutbox.lease_owner.is_(None),
            OutboundDeliveryOutbox.lease_token.is_(None),
            OutboundDeliveryOutbox.lease_expires_at.is_(None),
        )
        .update(
            {
                OutboundDeliveryOutbox.status: "cancelled",
                OutboundDeliveryOutbox.next_attempt_at: None,
                OutboundDeliveryOutbox.last_error_type: reason,
                OutboundDeliveryOutbox.last_error_summary: summary,
                OutboundDeliveryOutbox.cancelled_at: current,
                OutboundDeliveryOutbox.cancel_reason_type: reason,
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.active_outbox_id == int(outbox.id),
            OutboundRun.status == str(run.status),
        )
        .update(
            {
                OutboundRun.status: "failed",
                OutboundRun.failure_type: reason,
                OutboundRun.failure_summary: summary,
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if outbox_updated != 1 or run_updated != 1:
        raise OutboundFencingError("安全取消 CAS 失败")
    _project_outbound_source(
        db,
        run=run,
        status="cancelled",
        current=current,
        error_summary=summary,
    )
    db.flush()
    result = OutboxCancellationResult(
        applied=True,
        outbox_id=int(outbox.id),
        run_id=int(run.id),
        status="cancelled",
    )
    db.expire_all()
    return result


def resolve_legacy_ambiguous_outreach(
    db: Session,
    *,
    outreach_log_id: int,
    expected_created_at: datetime,
    expected_source_revision: str,
    resolution: str,
    reason: str,
    now: datetime | None = None,
) -> LegacyOutreachResolutionResult:
    """显式取消无法证明投递结果的旧外呼记录，绝不创建成功事实。"""

    if resolution != "cancel_without_replay":
        raise OutboundSafetyError("legacy ambiguous hold 只能取消且不重放")
    normalized_reason = _text(reason, name="reason", max_length=1000)
    del normalized_reason
    expected_created = _utc_naive(expected_created_at)
    expected_revision = _text(
        expected_source_revision,
        name="expected_source_revision",
        max_length=128,
    )
    _utc_naive(now)
    row = db.get(ProactiveOutreachLog, int(outreach_log_id))
    if row is None:
        raise OutboundFencingError("legacy ambiguous outreach 不存在")
    if row.created_at != expected_created:
        raise OutboundFencingError("legacy outreach created_at CAS 已失效")
    if proactive_outreach_source_revision(row) != expected_revision:
        raise OutboundFencingError("legacy outreach source revision 已失效")
    if row.outbound_run_id is not None:
        raise OutboundSafetyError("已有出站 run 的记录不能按 legacy hold 解析")
    if row.status == "cancelled":
        return LegacyOutreachResolutionResult(
            applied=False,
            outreach_log_id=int(row.id),
            status="cancelled",
        )
    if row.status != "legacy_ambiguous_hold":
        raise OutboundSafetyError("只有 legacy ambiguous hold 可以显式解析")

    query = db.query(ProactiveOutreachLog).filter(
        ProactiveOutreachLog.id == int(row.id),
        ProactiveOutreachLog.status == "legacy_ambiguous_hold",
        ProactiveOutreachLog.outbound_run_id.is_(None),
        ProactiveOutreachLog.user_id == row.user_id,
        ProactiveOutreachLog.idempotency_key == row.idempotency_key,
        ProactiveOutreachLog.grounding_json == row.grounding_json,
        ProactiveOutreachLog.judge_reason == row.judge_reason,
        ProactiveOutreachLog.next_check_at == row.next_check_at,
        ProactiveOutreachLog.next_intent == row.next_intent,
        ProactiveOutreachLog.message == row.message,
        ProactiveOutreachLog.forced.is_(bool(row.forced)),
        ProactiveOutreachLog.created_at == expected_created,
    )
    if row.judge_should is None:
        query = query.filter(ProactiveOutreachLog.judge_should.is_(None))
    else:
        query = query.filter(
            ProactiveOutreachLog.judge_should.is_(bool(row.judge_should))
        )
    updated = query.update(
        {ProactiveOutreachLog.status: "cancelled"},
        synchronize_session=False,
    )
    if updated != 1:
        raise OutboundFencingError("legacy ambiguous outreach CAS 失败")
    db.flush()
    result = LegacyOutreachResolutionResult(
        applied=True,
        outreach_log_id=int(row.id),
        status="cancelled",
    )
    db.expire_all()
    return result


def cancel_safe_deliveries_for_source(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    expected_source_revision: str,
    reason_type: str,
    safe_summary: Any,
    now: datetime | None = None,
) -> SourceCancellationSummary:
    """取消尚未产生外部副作用的来源 leaf；投递中或不确定记录只报告风险。"""

    source = _text(source_type, name="source_type", max_length=32)
    source_identity = _text(source_id, name="source_id", max_length=255)
    revision = _text(
        expected_source_revision,
        name="expected_source_revision",
        max_length=128,
    )
    reason = _text(reason_type, name="reason_type", max_length=64)
    summary = _summary(safe_summary)
    current = _locked_current(db, source_type=source, now=now)
    cancelled = 0
    generation_runs = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.source_type == source,
            OutboundRun.source_id == source_identity,
            OutboundRun.source_revision == revision,
            OutboundRun.active_outbox_id.is_(None),
            OutboundRun.status.in_(("claimed", "generating")),
            ~exists().where(
                OutboundDeliveryOutbox.run_id == OutboundRun.id
            ),
        )
        .order_by(OutboundRun.id.asc())
        .all()
    )
    for run in generation_runs:
        previous_status = str(run.status)
        claim_owner = str(run.claim_owner or "")
        claim_token = str(run.claim_token or "")
        run_updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.source_type == source,
                OutboundRun.source_id == source_identity,
                OutboundRun.source_revision == revision,
                OutboundRun.status == previous_status,
                OutboundRun.claim_owner == claim_owner,
                OutboundRun.claim_token == claim_token,
                OutboundRun.active_outbox_id.is_(None),
                ~exists().where(
                    OutboundDeliveryOutbox.run_id == OutboundRun.id
                ),
            )
            .update(
                {
                    OutboundRun.status: "failed",
                    OutboundRun.claim_owner: None,
                    OutboundRun.claim_token: None,
                    OutboundRun.claim_expires_at: None,
                    OutboundRun.failure_type: reason,
                    OutboundRun.failure_summary: summary,
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if run_updated != 1:
            continue
        attempt_updated = (
            db.query(OutboundGenerationAttempt)
            .filter(
                OutboundGenerationAttempt.run_id == int(run.id),
                OutboundGenerationAttempt.status == "started",
                OutboundGenerationAttempt.owner == claim_owner,
                OutboundGenerationAttempt.fencing_token == claim_token,
            )
            .update(
                {
                    OutboundGenerationAttempt.status: "abandoned",
                    OutboundGenerationAttempt.completed_at: current,
                    OutboundGenerationAttempt.error_type: reason,
                    OutboundGenerationAttempt.error_summary: summary,
                },
                synchronize_session=False,
            )
        )
        expected_attempts = 1 if previous_status == "generating" else 0
        if attempt_updated != expected_attempts:
            raise OutboundFencingError(
                "来源安全取消的 generation attempt CAS 失败"
            )
        _project_outbound_source(
            db,
            run=run,
            status="cancelled",
            current=current,
            error_summary=summary,
        )
        cancelled += 1
    unsafe = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.source_type == source,
            OutboundRun.source_id == source_identity,
            OutboundRun.source_revision == revision,
            OutboundRun.status.in_(("claimed", "generating")),
        )
        .count()
    )
    blocked_runs = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.source_type == source,
            OutboundRun.source_id == source_identity,
            OutboundRun.source_revision == revision,
            OutboundRun.status == "blocked",
            OutboundRun.active_outbox_id.is_(None),
            ~exists().where(
                OutboundDeliveryOutbox.run_id == OutboundRun.id
            ),
        )
        .order_by(OutboundRun.id.asc())
        .all()
    )
    for run in blocked_runs:
        updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.status == "blocked",
                OutboundRun.active_outbox_id.is_(None),
                ~exists().where(
                    OutboundDeliveryOutbox.run_id == OutboundRun.id
                ),
            )
            .update(
                {
                    OutboundRun.status: "failed",
                    OutboundRun.failure_type: reason,
                    OutboundRun.failure_summary: summary,
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            unsafe += 1
            continue
        _project_outbound_source(
            db,
            run=run,
            status="cancelled",
            current=current,
            error_summary=summary,
        )
        cancelled += 1
    rows = (
        db.query(OutboundRun, OutboundDeliveryOutbox)
        .join(
            OutboundDeliveryOutbox,
            OutboundDeliveryOutbox.id == OutboundRun.active_outbox_id,
        )
        .filter(
            OutboundRun.source_type == source,
            OutboundRun.source_id == source_identity,
            OutboundRun.source_revision == revision,
        )
        .order_by(OutboundDeliveryOutbox.id.asc())
        .all()
    )
    for run, outbox in rows:
        previous_status = str(outbox.status)
        if previous_status in {"leased", "ambiguous"}:
            unsafe += 1
            continue
        if previous_status not in {"pending", "retry_wait", "blocked"}:
            continue
        outbox_updated = (
            db.query(OutboundDeliveryOutbox)
            .filter(
                OutboundDeliveryOutbox.id == int(outbox.id),
                OutboundDeliveryOutbox.status == previous_status,
                OutboundDeliveryOutbox.lease_owner.is_(None),
                OutboundDeliveryOutbox.lease_token.is_(None),
                OutboundDeliveryOutbox.lease_expires_at.is_(None),
            )
            .update(
                {
                    OutboundDeliveryOutbox.status: "cancelled",
                    OutboundDeliveryOutbox.next_attempt_at: None,
                    OutboundDeliveryOutbox.last_error_type: reason,
                    OutboundDeliveryOutbox.last_error_summary: summary,
                    OutboundDeliveryOutbox.cancelled_at: current,
                    OutboundDeliveryOutbox.cancel_reason_type: reason,
                    OutboundDeliveryOutbox.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if outbox_updated != 1:
            unsafe += 1
            continue
        run_updated = (
            db.query(OutboundRun)
            .filter(
                OutboundRun.id == int(run.id),
                OutboundRun.active_outbox_id == int(outbox.id),
                OutboundRun.status.in_(("queued", "blocked")),
            )
            .update(
                {
                    OutboundRun.status: "failed",
                    OutboundRun.failure_type: reason,
                    OutboundRun.failure_summary: summary,
                    OutboundRun.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if run_updated != 1:
            raise OutboundFencingError("来源安全取消的 run CAS 失败")
        _project_outbound_source(
            db,
            run=run,
            status="cancelled",
            current=current,
            error_summary=summary,
        )
        cancelled += 1
    db.flush()
    result = SourceCancellationSummary(cancelled=cancelled, unsafe=unsafe)
    db.expire_all()
    return result



__all__ = [
    "cancel_delivery_before_send",
    "cancel_invalid_delivery_before_send",
    "cancel_safe_deliveries_for_source",
    "cancel_safe_outbox",
    "claim_due_outbox",
    "claim_legacy_direct_outbox",
    "mark_delivery_request_started",
    "resolve_legacy_ambiguous_outreach",
    "terminalize_expired_outboxes",
]
