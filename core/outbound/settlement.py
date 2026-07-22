"""主动出站投递结算、熔断与过期租约收敛适配器。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.db.models.outbound import (
    OutboundDeliveryAttempt,
    OutboundDeliveryCircuit,
    OutboundDeliveryOutbox,
    OutboundRun,
)
from core.outbound.contracts import (
    DeliverySettlementResult,
    LeaseExpirySummary,
    OutboundConflictError,
    OutboundFencingError,
    OutboundSafetyError,
)
from core.outbound.control import _locked_current
from core.outbound.delivery_claims import _require_live_delivery
from core.outbound.policy import (
    audit_datetime as _audit_datetime,
    audit_request_sha256 as _audit_request_sha256,
    destination_circuit_fingerprint,
    endpoint_circuit_fingerprint,
    payload_contract_circuit_fingerprint,
    require_text as _text,
    safe_summary as _summary,
    utc_naive as _utc_naive,
)
from core.outbound.projection import (
    append_delivered_outbound_context as _append_delivered_outbound_context,
    project_outbound_source as _project_outbound_source,
)


_CIRCUIT_SCOPE_TYPES = frozenset({"endpoint", "destination", "payload_contract"})
_TRANSIENT_CLIENT_HTTP_STATUSES = frozenset({408, 425, 429})
_STABLE_SERVER_HTTP_STATUSES = frozenset({501, 505})
_RESULT_CATEGORY_OUTCOMES = {
    "success": "succeeded",
    "transient": "transient_failure",
    "ambiguous": "ambiguous",
    "endpoint": "permanent_failure",
    "destination": "permanent_failure",
    "destination_missing": "permanent_failure",
    "destination_rejected": "permanent_failure",
    "destination_deleted": "permanent_failure",
    "payload": "permanent_failure",
    "payload_contract": "permanent_failure",
}
_SEMANTIC_2XX_FAILURE_CATEGORIES = frozenset(
    {
        "destination",
        "destination_missing",
        "destination_rejected",
        "destination_deleted",
        "payload",
        "payload_contract",
    }
)


def _scope_fingerprint(
    outbox: OutboundDeliveryOutbox,
    scope_type: str,
) -> str:
    if scope_type == "endpoint":
        return endpoint_circuit_fingerprint(str(outbox.endpoint_key))
    if scope_type == "destination":
        return destination_circuit_fingerprint(
            str(outbox.endpoint_key),
            str(outbox.destination_fingerprint),
        )
    if scope_type == "payload_contract":
        return payload_contract_circuit_fingerprint(
            str(outbox.endpoint_key),
            str(outbox.payload_contract_fingerprint),
        )
    raise ValueError("circuit_scope_type 非法")


def _classified_circuit_scope(
    *,
    outcome: str,
    http_status: int | None,
    result_category: str,
    error_type: str,
) -> str | None:
    if outcome != "permanent_failure":
        return None

    category = result_category.strip().lower()
    error = error_type.strip().lower()
    if http_status in {401, 403, 405, 501, 505}:
        return "endpoint"

    destination_signal = (
        category in {
            "destination",
            "destination_missing",
            "destination_rejected",
            "destination_deleted",
        }
        or error in {
            "destination_missing",
            "destination_rejected",
            "destination_deleted",
            "target_missing",
            "target_rejected",
            "target_deleted",
        }
    )
    if http_status in {404, 410}:
        return "destination" if destination_signal else "endpoint"
    if http_status == 415:
        return "payload_contract" if category == "payload_contract" else "endpoint"
    if http_status in {400, 422}:
        return "payload_contract" if category == "payload_contract" else None
    if http_status == 413 or category == "payload":
        return None
    if destination_signal:
        return "destination"
    if category == "payload_contract":
        return "payload_contract"
    if category == "endpoint":
        return "endpoint"
    return None


def _validate_settlement_classification(
    *,
    outcome: str,
    http_status: int | None,
    result_category: str,
) -> None:
    category = result_category.strip().lower()
    expected_outcome = _RESULT_CATEGORY_OUTCOMES.get(category)
    if expected_outcome is not None and outcome != expected_outcome:
        raise ValueError("result_category 与 outcome 分类不一致")

    if http_status is None:
        return
    expected_http_outcome = None
    if 400 <= http_status <= 499:
        expected_http_outcome = (
            "transient_failure"
            if http_status in _TRANSIENT_CLIENT_HTTP_STATUSES
            else "permanent_failure"
        )
    elif 500 <= http_status <= 599:
        expected_http_outcome = (
            "permanent_failure"
            if http_status in _STABLE_SERVER_HTTP_STATUSES
            else "transient_failure"
        )
    if expected_http_outcome is not None and outcome != expected_http_outcome:
        raise ValueError("HTTP 状态与 outcome 分类不一致")
    if 200 <= http_status <= 299:
        if outcome == "transient_failure":
            raise ValueError("2xx HTTP 状态不能按 transient_failure 结算")
        if (
            outcome == "permanent_failure"
            and category not in _SEMANTIC_2XX_FAILURE_CATEGORIES
        ):
            raise ValueError("2xx HTTP 永久失败缺少可验证的语义分类")


def _settlement_request_sha256(
    *,
    outcome: str,
    transport_phase: str,
    http_status: int | None,
    result_category: str,
    error_type: str,
    safe_summary: str,
    duration_ms: int | None,
    retry_at: datetime | None,
    circuit_scope_type: str | None,
) -> str:
    return _audit_request_sha256(
        "delivery_settlement",
        {
            "outcome": outcome,
            "transport_phase": transport_phase,
            "http_status": http_status,
            "result_category": result_category,
            "error_type": error_type,
            "safe_summary": safe_summary,
            "duration_ms": duration_ms,
            "retry_at": _audit_datetime(retry_at),
            "circuit_scope_type": circuit_scope_type,
        },
    )


def _open_delivery_circuit(
    db: Session,
    *,
    outbox: OutboundDeliveryOutbox,
    attempt: OutboundDeliveryAttempt,
    scope_type: str,
    reason_type: str,
    current: datetime,
) -> None:
    scope = _text(scope_type, name="circuit_scope_type", max_length=32)
    if scope not in _CIRCUIT_SCOPE_TYPES:
        raise ValueError("circuit_scope_type 非法")
    fingerprint = _scope_fingerprint(outbox, scope)
    db.execute(
        sqlite_insert(OutboundDeliveryCircuit)
        .values(
            scope_type=scope,
            scope_fingerprint=fingerprint,
            config_revision=str(attempt.endpoint_config_revision),
            status="open",
            reason_type=reason_type,
            opened_at=current,
            opened_by_attempt_id=int(attempt.id),
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_update(
            index_elements=[
                "scope_type",
                "scope_fingerprint",
                "config_revision",
            ],
            set_={
                "status": "open",
                "reason_type": reason_type,
                "opened_at": current,
                "opened_by_attempt_id": int(attempt.id),
                "updated_at": current,
            },
        )
    )


def settle_delivery_attempt(
    db: Session,
    *,
    outbox_id: int,
    attempt_id: int,
    worker_owner: str,
    lease_token: str,
    outcome: str,
    transport_phase: str,
    http_status: int | None,
    result_category: str,
    error_type: str,
    safe_summary: Any,
    duration_ms: int | None,
    retry_at: datetime | None = None,
    circuit_scope_type: str | None = None,
    now: datetime | None = None,
) -> DeliverySettlementResult:
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
    normalized_outcome = _text(outcome, name="outcome", max_length=32)
    if normalized_outcome not in {
        "succeeded",
        "transient_failure",
        "permanent_failure",
        "ambiguous",
    }:
        raise ValueError("outcome 非法")
    requested_scope = None
    if circuit_scope_type is not None:
        requested_scope = _text(
            circuit_scope_type,
            name="circuit_scope_type",
            max_length=32,
        )
        if requested_scope not in _CIRCUIT_SCOPE_TYPES:
            raise ValueError("circuit_scope_type 非法")
        if normalized_outcome != "permanent_failure":
            raise ValueError("只有 permanent_failure 可以打开 circuit")
    phase = _text(transport_phase, name="transport_phase", max_length=32)
    normalized_result_category = str(result_category or "")[:64]
    normalized_error_type = str(error_type or "")[:64]
    normalized_safe_summary = _summary(safe_summary)
    if http_status is not None and (
        type(http_status) is not int or not 100 <= http_status <= 599
    ):
        raise ValueError("http_status 必须是 100-599")
    _validate_settlement_classification(
        outcome=normalized_outcome,
        http_status=http_status,
        result_category=normalized_result_category,
    )
    if normalized_outcome == "succeeded" and (
        http_status is None or not 200 <= http_status <= 299
    ):
        raise ValueError("成功结算必须具有 2xx 状态码")
    if duration_ms is not None and (
        type(duration_ms) is not int or duration_ms < 0
    ):
        raise ValueError("duration_ms 必须是非负整数或 null")
    classified_scope = _classified_circuit_scope(
        outcome=normalized_outcome,
        http_status=http_status,
        result_category=normalized_result_category,
        error_type=normalized_error_type,
    )
    if requested_scope is not None and requested_scope != classified_scope:
        raise ValueError("circuit scope 与结构化结果分类不一致")
    circuit_scope_type = classified_scope
    normalized_retry_at = (
        _utc_naive(retry_at) if retry_at is not None else None
    )
    settlement_request_sha256 = _settlement_request_sha256(
        outcome=normalized_outcome,
        transport_phase=phase,
        http_status=http_status,
        result_category=normalized_result_category,
        error_type=normalized_error_type,
        safe_summary=normalized_safe_summary,
        duration_ms=duration_ms,
        retry_at=normalized_retry_at,
        circuit_scope_type=circuit_scope_type,
    )
    existing_outbox = db.get(OutboundDeliveryOutbox, int(outbox_id))
    existing_attempt = db.get(OutboundDeliveryAttempt, int(attempt_id))
    if existing_outbox is not None and existing_attempt is not None:
        existing_run = db.get(OutboundRun, int(existing_outbox.run_id))
        if existing_attempt.status != "started":
            identity_matches = (
                existing_run is not None
                and int(existing_attempt.outbox_id) == int(existing_outbox.id)
                and existing_attempt.worker_owner == owner
                and existing_attempt.lease_token == token
            )
            if not identity_matches:
                raise OutboundFencingError("delivery attempt 已结算或身份已变化")
            audit_fields_match = (
                existing_attempt.status == normalized_outcome
                and existing_attempt.transport_phase == phase
                and existing_attempt.http_status == http_status
                and existing_attempt.result_category == normalized_result_category
                and existing_attempt.error_type == normalized_error_type
                and existing_attempt.safe_summary == normalized_safe_summary
                and existing_attempt.duration_ms == duration_ms
                and existing_attempt.settlement_retry_at == normalized_retry_at
                and existing_attempt.settlement_circuit_scope_type
                == circuit_scope_type
            )
            fingerprint_matches = (
                existing_attempt.settlement_request_sha256
                == settlement_request_sha256
            )
            legacy_terminal_matches = (
                not existing_attempt.settlement_request_sha256
                and audit_fields_match
            )
            if audit_fields_match and (
                fingerprint_matches or legacy_terminal_matches
            ):
                return DeliverySettlementResult(
                    applied=False,
                    outbox_id=int(existing_outbox.id),
                    attempt_id=int(existing_attempt.id),
                    outbox_status=str(existing_outbox.status),
                    run_status=str(existing_run.status),
                )
            raise OutboundConflictError("重复结算的不可变审计事实不一致")
    if (
        normalized_outcome == "transient_failure"
        and normalized_retry_at is not None
        and normalized_retry_at <= current
    ):
        raise ValueError("retry_at 必须严格晚于 now")
    outbox, attempt, run = _require_live_delivery(
        db,
        outbox_id=outbox_id,
        attempt_id=attempt_id,
        worker_owner=owner,
        lease_token=token,
        current=current,
    )
    request_started = bool(attempt.request_started)
    if normalized_outcome in {"succeeded", "ambiguous"} and not request_started:
        raise OutboundSafetyError("成功或不确定结算必须已越过 request boundary")
    next_attempt_at = None
    delivered_at = None
    failure_type = ""
    failure_summary = ""
    if normalized_outcome == "succeeded":
        outbox_status = "delivered"
        run_status = (
            "succeeded_after_ambiguous_replay"
            if bool(run.has_ambiguous_ancestor) or int(outbox.replay_sequence) > 0
            else "succeeded"
        )
        delivered_at = current
    elif normalized_outcome == "ambiguous":
        outbox_status = "ambiguous"
        run_status = "ambiguous"
        failure_type = str(error_type or "ambiguous")[:64]
        failure_summary = _summary(safe_summary)
    elif normalized_outcome == "permanent_failure":
        outbox_status = "failed"
        run_status = "blocked" if circuit_scope_type else "failed"
        failure_type = str(error_type or "permanent_failure")[:64]
        failure_summary = _summary(safe_summary)
    else:
        failure_type = str(error_type or "transient_failure")[:64]
        failure_summary = _summary(safe_summary)
        exhausted = (
            int(outbox.request_started_count) >= int(outbox.max_attempts)
            or current >= outbox.retry_deadline_at
            or normalized_retry_at is None
            or normalized_retry_at >= outbox.retry_deadline_at
        )
        if exhausted:
            outbox_status = "failed"
            run_status = "failed"
            failure_type = "retry_exhausted"
        else:
            outbox_status = "retry_wait"
            run_status = "queued"
            next_attempt_at = normalized_retry_at

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
            OutboundDeliveryAttempt.request_started.is_(request_started),
        )
        .update(
            {
                OutboundDeliveryAttempt.status: normalized_outcome,
                OutboundDeliveryAttempt.transport_phase: phase,
                OutboundDeliveryAttempt.http_status: http_status,
                OutboundDeliveryAttempt.result_category: (
                    normalized_result_category
                ),
                OutboundDeliveryAttempt.error_type: normalized_error_type,
                OutboundDeliveryAttempt.safe_summary: normalized_safe_summary,
                OutboundDeliveryAttempt.duration_ms: duration_ms,
                OutboundDeliveryAttempt.settlement_retry_at: normalized_retry_at,
                OutboundDeliveryAttempt.settlement_circuit_scope_type: (
                    circuit_scope_type
                ),
                OutboundDeliveryAttempt.settlement_request_sha256: (
                    settlement_request_sha256
                ),
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
            OutboundDeliveryOutbox.allocated_attempt_count
            == int(attempt.attempt_no),
        )
        .update(
            {
                OutboundDeliveryOutbox.status: outbox_status,
                OutboundDeliveryOutbox.lease_owner: None,
                OutboundDeliveryOutbox.lease_token: None,
                OutboundDeliveryOutbox.lease_expires_at: None,
                OutboundDeliveryOutbox.next_attempt_at: next_attempt_at,
                OutboundDeliveryOutbox.last_error_type: failure_type,
                OutboundDeliveryOutbox.last_error_summary: failure_summary,
                OutboundDeliveryOutbox.delivered_at: delivered_at,
                OutboundDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    run_values: dict[Any, Any] = {
        OutboundRun.status: run_status,
        OutboundRun.failure_type: failure_type,
        OutboundRun.failure_summary: failure_summary,
        OutboundRun.succeeded_at: delivered_at,
        OutboundRun.updated_at: current,
    }
    if normalized_outcome == "ambiguous":
        run_values[OutboundRun.has_ambiguous_ancestor] = True
    run_updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.active_outbox_id == int(outbox.id),
            OutboundRun.status == "delivering",
            OutboundRun.cutover_epoch == int(outbox.cutover_epoch),
        )
        .update(run_values, synchronize_session=False)
    )
    if attempt_updated != 1 or outbox_updated != 1 or run_updated != 1:
        raise OutboundFencingError("delivery settlement CAS 失败")
    if circuit_scope_type is not None:
        _open_delivery_circuit(
            db,
            outbox=outbox,
            attempt=attempt,
            scope_type=circuit_scope_type,
            reason_type=failure_type,
            current=current,
        )
    projection_status = {
        "delivered": "delivered",
        "retry_wait": "retry_wait",
        "failed": "blocked" if circuit_scope_type else "failed",
        "ambiguous": "ambiguous",
    }[outbox_status]
    _project_outbound_source(
        db,
        run=run,
        status=projection_status,
        current=current,
        error_summary=failure_summary,
        succeeded=outbox_status == "delivered",
    )
    if outbox_status == "delivered" and delivered_at is not None:
        _append_delivered_outbound_context(
            db,
            run=run,
            outbox=outbox,
            delivered_at=delivered_at,
        )
    db.flush()
    return DeliverySettlementResult(
        applied=True,
        outbox_id=int(outbox.id),
        attempt_id=int(attempt.id),
        outbox_status=outbox_status,
        run_status=run_status,
    )


def expire_stale_delivery_leases(
    db: Session,
    *,
    endpoint_key: str = "qq_push",
    now: datetime | None = None,
) -> LeaseExpirySummary:
    endpoint = _text(endpoint_key, name="endpoint_key", max_length=64)
    observed_at = _utc_naive(now)
    candidates = (
        db.query(OutboundDeliveryOutbox)
        .filter(
            OutboundDeliveryOutbox.endpoint_key == endpoint,
            OutboundDeliveryOutbox.status == "leased",
            OutboundDeliveryOutbox.lease_expires_at.is_not(None),
            OutboundDeliveryOutbox.lease_expires_at <= observed_at,
        )
        .order_by(OutboundDeliveryOutbox.id.asc())
        .all()
    )
    abandoned = 0
    ambiguous = 0
    for outbox in candidates:
        outbox_id = int(outbox.id)
        run_probe = db.get(OutboundRun, int(outbox.run_id))
        if run_probe is None:
            current = _utc_naive(now)
        else:
            current = _locked_current(
                db,
                source_type=str(run_probe.source_type),
                now=now,
            )
        outbox = db.get(OutboundDeliveryOutbox, outbox_id)
        if (
            outbox is None
            or outbox.endpoint_key != endpoint
            or outbox.status != "leased"
            or outbox.lease_expires_at is None
            or outbox.lease_expires_at > current
        ):
            continue
        attempt = (
            db.query(OutboundDeliveryAttempt)
            .filter(
                OutboundDeliveryAttempt.outbox_id == int(outbox.id),
                OutboundDeliveryAttempt.attempt_no
                == int(outbox.allocated_attempt_count),
            )
            .first()
        )
        run = db.get(OutboundRun, int(outbox.run_id))
        attempt_consistent = bool(
            attempt is not None
            and attempt.status == "started"
            and attempt.worker_owner == outbox.lease_owner
            and attempt.lease_token == outbox.lease_token
        )
        active_leaf_consistent = bool(
            run is not None
            and run.active_outbox_id == outbox.id
            and run.status == "delivering"
        )
        request_started = bool(attempt.request_started) if attempt_consistent else True
        is_ambiguous = (
            request_started
            or not attempt_consistent
            or not active_leaf_consistent
        )
        deadline_exhausted = (
            not is_ambiguous and current >= outbox.retry_deadline_at
        )
        recovery_status = "failed" if deadline_exhausted else "pending"
        recovery_error_type = (
            "retry_exhausted" if deadline_exhausted else "lease_expired"
        )
        recovery_summary = (
            "发送前租约过期且投递重试期限已到期"
            if deadline_exhausted
            else "发送前租约过期，已安全放回队列"
        )
        previous_owner = str(outbox.lease_owner)
        previous_token = str(outbox.lease_token)
        previous_expiry = outbox.lease_expires_at
        updated = (
            db.query(OutboundDeliveryOutbox)
            .filter(
                OutboundDeliveryOutbox.id == int(outbox.id),
                OutboundDeliveryOutbox.endpoint_key == endpoint,
                OutboundDeliveryOutbox.status == "leased",
                OutboundDeliveryOutbox.lease_owner == previous_owner,
                OutboundDeliveryOutbox.lease_token == previous_token,
                OutboundDeliveryOutbox.lease_expires_at == previous_expiry,
                OutboundDeliveryOutbox.lease_expires_at <= current,
                OutboundDeliveryOutbox.request_started_count
                == int(outbox.request_started_count),
            )
            .update(
                {
                    OutboundDeliveryOutbox.status: (
                        "ambiguous" if is_ambiguous else recovery_status
                    ),
                    OutboundDeliveryOutbox.lease_owner: None,
                    OutboundDeliveryOutbox.lease_token: None,
                    OutboundDeliveryOutbox.lease_expires_at: None,
                    OutboundDeliveryOutbox.next_attempt_at: None,
                    OutboundDeliveryOutbox.last_error_type: (
                        "lease_expired" if is_ambiguous else recovery_error_type
                    ),
                    OutboundDeliveryOutbox.last_error_summary: (
                        "请求开始后租约过期，投递结果不确定"
                        if is_ambiguous
                        else recovery_summary
                    ),
                    OutboundDeliveryOutbox.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            continue
        if is_ambiguous:
            if attempt_consistent and attempt is not None:
                ambiguous_summary = "请求开始后租约过期，投递结果不确定"
                settlement_request_sha256 = _settlement_request_sha256(
                    outcome="ambiguous",
                    transport_phase=str(attempt.transport_phase),
                    http_status=attempt.http_status,
                    result_category="ambiguous",
                    error_type="lease_expired",
                    safe_summary=ambiguous_summary,
                    duration_ms=attempt.duration_ms,
                    retry_at=None,
                    circuit_scope_type=None,
                )
                attempt_updated = (
                    db.query(OutboundDeliveryAttempt)
                    .filter(
                        OutboundDeliveryAttempt.id == int(attempt.id),
                        OutboundDeliveryAttempt.outbox_id == int(outbox.id),
                        OutboundDeliveryAttempt.attempt_no
                        == int(outbox.allocated_attempt_count),
                        OutboundDeliveryAttempt.status == "started",
                        OutboundDeliveryAttempt.worker_owner == previous_owner,
                        OutboundDeliveryAttempt.lease_token == previous_token,
                        OutboundDeliveryAttempt.request_started.is_(
                            request_started
                        ),
                    )
                    .update(
                        {
                            OutboundDeliveryAttempt.status: "ambiguous",
                            OutboundDeliveryAttempt.result_category: "ambiguous",
                            OutboundDeliveryAttempt.error_type: "lease_expired",
                            OutboundDeliveryAttempt.safe_summary: ambiguous_summary,
                            OutboundDeliveryAttempt.settlement_retry_at: None,
                            OutboundDeliveryAttempt.settlement_circuit_scope_type: (
                                None
                            ),
                            OutboundDeliveryAttempt.settlement_request_sha256: (
                                settlement_request_sha256
                            ),
                            OutboundDeliveryAttempt.completed_at: current,
                        },
                        synchronize_session=False,
                    )
                )
                if attempt_updated != 1:
                    raise OutboundFencingError("过期 attempt ambiguous CAS 失败")
            if active_leaf_consistent and run is not None:
                run_updated = (
                    db.query(OutboundRun)
                    .filter(
                        OutboundRun.id == int(run.id),
                        OutboundRun.active_outbox_id == int(outbox.id),
                        OutboundRun.status == "delivering",
                    )
                    .update(
                        {
                            OutboundRun.status: "ambiguous",
                            OutboundRun.has_ambiguous_ancestor: True,
                            OutboundRun.failure_type: "lease_expired",
                            OutboundRun.failure_summary: (
                                "请求开始后租约过期，投递结果不确定"
                            ),
                            OutboundRun.updated_at: current,
                        },
                        synchronize_session=False,
                    )
                )
                if run_updated != 1:
                    raise OutboundFencingError("过期 run ambiguous CAS 失败")
                _project_outbound_source(
                    db,
                    run=run,
                    status="ambiguous",
                    current=current,
                    error_summary="请求开始后租约过期，投递结果不确定",
                )
            ambiguous += 1
        else:
            assert attempt is not None
            assert run is not None
            attempt_updated = (
                db.query(OutboundDeliveryAttempt)
                .filter(
                    OutboundDeliveryAttempt.id == int(attempt.id),
                    OutboundDeliveryAttempt.outbox_id == int(outbox.id),
                    OutboundDeliveryAttempt.attempt_no
                    == int(outbox.allocated_attempt_count),
                    OutboundDeliveryAttempt.status == "started",
                    OutboundDeliveryAttempt.worker_owner == previous_owner,
                    OutboundDeliveryAttempt.lease_token == previous_token,
                    OutboundDeliveryAttempt.request_started.is_(False),
                    OutboundDeliveryAttempt.transport_phase == "allocated",
                )
                .update(
                    {
                        OutboundDeliveryAttempt.status: "abandoned_before_send",
                        OutboundDeliveryAttempt.transport_phase: "allocated",
                        OutboundDeliveryAttempt.result_category: "before_send",
                        OutboundDeliveryAttempt.error_type: "lease_expired",
                        OutboundDeliveryAttempt.safe_summary: (
                            "发送前租约过期，未消耗网络预算"
                        ),
                        OutboundDeliveryAttempt.completed_at: current,
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
                        OutboundRun.status: (
                            "failed" if deadline_exhausted else "queued"
                        ),
                        OutboundRun.failure_type: (
                            "retry_exhausted" if deadline_exhausted else ""
                        ),
                        OutboundRun.failure_summary: (
                            recovery_summary if deadline_exhausted else ""
                        ),
                        OutboundRun.updated_at: current,
                    },
                    synchronize_session=False,
                )
            )
            if attempt_updated != 1 or run_updated != 1:
                raise OutboundFencingError("发送前租约过期 CAS 失败")
            _project_outbound_source(
                db,
                run=run,
                status="failed" if deadline_exhausted else "queued",
                current=current,
                error_summary=recovery_summary if deadline_exhausted else "",
            )
            abandoned += 1
    db.flush()
    result = LeaseExpirySummary(
        abandoned_before_send=abandoned,
        ambiguous=ambiguous,
    )
    db.expire_all()
    return result



__all__ = ["expire_stale_delivery_leases", "settle_delivery_attempt"]
