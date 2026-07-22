"""主动出站 occurrence claim 与生成尝试租约适配器。"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.db.models.outbound import (
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
)
from core.outbound.contracts import (
    GenerationAttemptHandle,
    OutboundConflictError,
    OutboundFencingError,
    OutboundSafetyError,
    RunClaimDecision,
    RunClaimRenewal,
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
    canonical_json as _canonical_json,
    circuit_facts as _circuit_facts,
    delivery_contract as _delivery_contract,
    load_delivery_contract as _load_delivery_contract,
    require_positive_seconds as _positive_seconds,
    require_text as _text,
    safe_summary as _summary,
    utc_naive as _utc_naive,
)
from core.outbound.projection import project_outbound_source as _project_outbound_source


_RUN_CLAIM_STATUSES = frozenset({"claimed", "generating"})


def _run_decision(
    row: OutboundRun,
    *,
    acquired: bool,
    owner: str = "",
    claim_token: str = "",
) -> RunClaimDecision:
    return RunClaimDecision(
        acquired=acquired,
        run_id=int(row.id),
        status=str(row.status),
        owner=owner if acquired else "",
        claim_token=claim_token if acquired else "",
        claim_expires_at=row.claim_expires_at if acquired else None,
        delivery_mode=str(row.delivery_mode),
        cutover_epoch=int(row.cutover_epoch),
        source_snapshot_json=str(row.source_snapshot_json),
        source_snapshot_sha256=str(row.source_snapshot_sha256),
        delivery_contract_json=str(row.delivery_contract_json),
        delivery_contract_sha256=str(row.delivery_contract_sha256),
    )


def _find_run(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    occurrence_key: str,
) -> OutboundRun | None:
    return (
        db.query(OutboundRun)
        .filter(
            OutboundRun.source_type == source_type,
            OutboundRun.source_id == source_id,
            OutboundRun.occurrence_key == occurrence_key,
        )
        .first()
    )


def quarantine_expired_generation_run(
    db: Session,
    *,
    run_id: int,
    expected_source_type: str,
    target_status: str,
    reason_type: str,
    safe_summary: Any,
    now: datetime | None = None,
) -> bool:
    """终结无投递 leaf 的过期生成租约，不提交调用方事务。"""

    source = _text(
        expected_source_type,
        name="expected_source_type",
        max_length=32,
    )
    status = str(target_status or "").strip()
    if status not in {"failed", "blocked"}:
        raise ValueError("target_status 只支持 failed/blocked")
    reason = _text(reason_type, name="reason_type", max_length=64)
    summary = _summary(safe_summary)
    current = _locked_current(db, source_type=source, now=now)
    run = db.get(OutboundRun, int(run_id))
    if run is None or str(run.source_type) != source:
        return False
    no_outbox = ~exists().where(
        OutboundDeliveryOutbox.run_id == OutboundRun.id
    )
    updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run.id),
            OutboundRun.source_type == source,
            OutboundRun.status.in_(tuple(_RUN_CLAIM_STATUSES)),
            OutboundRun.claim_expires_at.is_not(None),
            OutboundRun.claim_expires_at <= current,
            OutboundRun.active_outbox_id.is_(None),
            no_outbox,
        )
        .update(
            {
                OutboundRun.status: status,
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
    if updated != 1:
        db.flush()
        db.expire_all()
        return False
    (
        db.query(OutboundGenerationAttempt)
        .filter(
            OutboundGenerationAttempt.run_id == int(run.id),
            OutboundGenerationAttempt.status == "started",
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
    _project_outbound_source(
        db,
        run=run,
        status=status,
        current=current,
        error_summary=summary,
    )
    db.flush()
    db.expire_all()
    return True


def claim_outbound_run(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    occurrence_key: str,
    source_revision: str,
    source_snapshot: Mapping[str, Any],
    destination_snapshot: Mapping[str, Any],
    target_type: str,
    task_kind: str,
    scheduled_for: datetime | None,
    trigger_type: str,
    owner: str,
    claim_lease_seconds: int | float,
    writer_owner: str,
    writer_token: str,
    writer_protocol_version: int,
    writer_lease_seconds: int | float,
    endpoint_key: str,
    destination_fingerprint: str,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
    now: datetime | None = None,
) -> RunClaimDecision:
    source = _text(source_type, name="source_type", max_length=32)
    source_identity = _text(source_id, name="source_id", max_length=255)
    occurrence = _text(occurrence_key, name="occurrence_key", max_length=255)
    revision = _text(source_revision, name="source_revision", max_length=128)
    kind = _text(task_kind, name="task_kind", max_length=64)
    trigger = _text(trigger_type, name="trigger_type", max_length=32)
    normalized_owner = _text(owner, name="owner", max_length=128)
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
    claim_seconds = _positive_seconds(
        claim_lease_seconds,
        name="claim_lease_seconds",
    )
    observed_at = _utc_naive(now)
    logical_occurrence = (
        _utc_naive(scheduled_for) if scheduled_for else observed_at
    )
    snapshot_json, snapshot_sha256 = _canonical_json(
        source_snapshot,
        name="source_snapshot",
    )
    delivery_contract_json, delivery_contract_sha256 = _delivery_contract(
        destination_snapshot=destination_snapshot,
        destination_fingerprint=destination_fingerprint,
        target_type=target_type,
        endpoint_key=endpoint_key,
        payload_contract_fingerprint=payload_contract_fingerprint,
    )
    circuit_facts = _circuit_facts(
        endpoint_key=endpoint_key,
        destination_fingerprint=destination_fingerprint,
        payload_contract_fingerprint=payload_contract_fingerprint,
        config_revision=endpoint_config_revision,
    )

    existing = _find_run(
        db,
        source_type=source,
        source_id=source_identity,
        occurrence_key=occurrence,
    )
    if existing is not None and not (
        existing.active_outbox_id is None
        and (
            (
                existing.status in _RUN_CLAIM_STATUSES
                and existing.claim_expires_at is not None
                and existing.claim_expires_at <= observed_at
            )
            or existing.status == "blocked"
        )
    ):
        return _run_decision(existing, acquired=False)

    writer = acquire_or_renew_delivery_writer(
        db,
        source_type=source,
        owner=normalized_writer_owner,
        token=normalized_writer_token,
        protocol_version=writer_protocol_version,
        lease_seconds=writer_lease_seconds,
        now=now,
    )
    if not writer.acquired:
        existing = _find_run(
            db,
            source_type=source,
            source_id=source_identity,
            occurrence_key=occurrence,
        )
        if existing is not None:
            return _run_decision(existing, acquired=False)
        raise OutboundSafetyError("其他 writer 仍持有 source lease")

    current = _utc_naive(now)

    control = _require_writer(
        db,
        source_type=source,
        owner=normalized_writer_owner,
        token=normalized_writer_token,
        protocol_version=writer_protocol_version,
        current=current,
    )
    delivery_mode, cutover_epoch = _mode_for_occurrence(
        control,
        occurrence_at=logical_occurrence,
    )
    open_circuit = _open_circuit_row(db, circuit_facts)
    claim_token = secrets.token_hex(32)
    claim_expires_at = current + timedelta(seconds=claim_seconds)

    existing = _find_run(
        db,
        source_type=source,
        source_id=source_identity,
        occurrence_key=occurrence,
    )
    if existing is not None:
        if existing.active_outbox_id is not None:
            return _run_decision(existing, acquired=False)
        if open_circuit is not None:
            if existing.status == "blocked":
                return _run_decision(existing, acquired=False)
            if (
                existing.status in _RUN_CLAIM_STATUSES
                and existing.claim_expires_at is not None
                and existing.claim_expires_at <= current
                and quarantine_expired_generation_run(
                    db,
                    run_id=int(existing.id),
                    expected_source_type=source,
                    target_status="blocked",
                    reason_type="circuit_open",
                    safe_summary=(
                        f"{open_circuit.scope_type} circuit 已打开"
                    ),
                    now=current,
                )
            ):
                blocked = db.get(OutboundRun, int(existing.id))
                if blocked is None:
                    raise RuntimeError("熔断终结后未找到 run")
                return _run_decision(blocked, acquired=False)
            return _run_decision(existing, acquired=False)
        if (
            existing.status == "blocked"
            or (
                existing.status in _RUN_CLAIM_STATUSES
                and existing.claim_expires_at is not None
                and existing.claim_expires_at <= current
            )
        ):
            updated = (
                db.query(OutboundRun)
                .filter(
                    OutboundRun.id == existing.id,
                    OutboundRun.active_outbox_id.is_(None),
                    or_(
                        OutboundRun.status == "blocked",
                        (
                            OutboundRun.status.in_(tuple(_RUN_CLAIM_STATUSES))
                            & (OutboundRun.claim_expires_at <= current)
                        ),
                    ),
                )
                .update(
                    {
                        OutboundRun.status: "claimed",
                        OutboundRun.claim_owner: normalized_owner,
                        OutboundRun.claim_token: claim_token,
                        OutboundRun.claim_expires_at: claim_expires_at,
                        OutboundRun.failure_type: "",
                        OutboundRun.failure_summary: "",
                        OutboundRun.writer_owner: normalized_writer_owner,
                        OutboundRun.writer_token: normalized_writer_token,
                        OutboundRun.writer_protocol_version: writer_protocol_version,
                        OutboundRun.updated_at: current,
                    },
                    synchronize_session=False,
                )
            )
            db.flush()
            row = db.get(OutboundRun, int(existing.id))
            if updated == 1 and row is not None:
                _project_outbound_source(
                    db,
                    run=row,
                    status="claimed",
                    current=current,
                )
                db.flush()
                db.expire_all()
                return _run_decision(
                    row,
                    acquired=True,
                    owner=normalized_owner,
                    claim_token=claim_token,
                )
        return _run_decision(existing, acquired=False)

    status = "blocked" if open_circuit is not None else "claimed"
    db.execute(
        sqlite_insert(OutboundRun)
        .values(
            source_type=source,
            source_id=source_identity,
            occurrence_key=occurrence,
            source_revision=revision,
            source_snapshot_json=snapshot_json,
            source_snapshot_sha256=snapshot_sha256,
            delivery_contract_json=delivery_contract_json,
            delivery_contract_sha256=delivery_contract_sha256,
            writer_owner=normalized_writer_owner,
            writer_token=normalized_writer_token,
            writer_protocol_version=writer_protocol_version,
            task_kind=kind,
            scheduled_for=logical_occurrence if scheduled_for is not None else None,
            trigger_type=trigger,
            status=status,
            claim_owner=normalized_owner if status == "claimed" else None,
            claim_token=claim_token if status == "claimed" else None,
            claim_expires_at=claim_expires_at if status == "claimed" else None,
            attempted_at=None,
            generated_at=None,
            succeeded_at=None,
            failure_type=("circuit_open" if open_circuit is not None else ""),
            failure_summary=(
                f"{open_circuit.scope_type} circuit 已打开"
                if open_circuit is not None
                else ""
            ),
            active_outbox_id=None,
            has_ambiguous_ancestor=False,
            delivery_mode=delivery_mode,
            cutover_epoch=cutover_epoch,
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_nothing(
            index_elements=["source_type", "source_id", "occurrence_key"]
        )
    )
    db.flush()
    db.expire_all()
    row = _find_run(
        db,
        source_type=source,
        source_id=source_identity,
        occurrence_key=occurrence,
    )
    if row is None:
        raise RuntimeError("原子登记 occurrence 后未找到 run")
    inserted_by_caller = row.claim_token == claim_token and row.status == "claimed"
    if inserted_by_caller or row.status == "blocked":
        _project_outbound_source(
            db,
            run=row,
            status=str(row.status),
            current=current,
            error_summary=str(row.failure_summary or ""),
            bind_run=True,
        )
        db.flush()
        db.expire_all()
    return _run_decision(
        row,
        acquired=inserted_by_caller,
        owner=normalized_owner,
        claim_token=claim_token,
    )


def renew_outbound_run_claim(
    db: Session,
    *,
    run_id: int,
    owner: str,
    claim_token: str,
    lease_seconds: int | float,
    now: datetime | None = None,
) -> RunClaimRenewal:
    normalized_owner = _text(owner, name="owner", max_length=128)
    token = _text(claim_token, name="claim_token", max_length=64)
    seconds = _positive_seconds(lease_seconds, name="lease_seconds")
    run_probe = db.get(OutboundRun, int(run_id))
    if run_probe is None:
        return RunClaimRenewal(
            applied=False,
            run_id=int(run_id),
            claim_expires_at=None,
        )
    current = _locked_current(
        db,
        source_type=str(run_probe.source_type),
        now=now,
    )
    expires_at = current + timedelta(seconds=seconds)
    run = db.get(OutboundRun, int(run_id))
    if run is None:
        return RunClaimRenewal(
            applied=False,
            run_id=int(run_id),
            claim_expires_at=None,
        )
    try:
        _require_writer(
            db,
            source_type=str(run.source_type),
            owner=str(run.writer_owner),
            token=str(run.writer_token),
            protocol_version=int(run.writer_protocol_version),
            current=current,
        )
    except OutboundFencingError:
        return RunClaimRenewal(
            applied=False,
            run_id=int(run_id),
            claim_expires_at=None,
        )
    updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == int(run_id),
            OutboundRun.status.in_(tuple(_RUN_CLAIM_STATUSES)),
            OutboundRun.claim_owner == normalized_owner,
            OutboundRun.claim_token == token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.claim_expires_at: expires_at,
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    result = RunClaimRenewal(
        applied=updated == 1,
        run_id=int(run_id),
        claim_expires_at=expires_at if updated == 1 else None,
    )
    db.expire_all()
    return result


def _require_generation_run(
    db: Session,
    *,
    run_id: int,
    owner: str,
    claim_token: str,
    current: datetime,
    allowed_statuses: set[str],
) -> OutboundRun:
    row = db.get(OutboundRun, int(run_id))
    if (
        row is None
        or row.status not in allowed_statuses
        or row.claim_owner != owner
        or row.claim_token != claim_token
        or row.claim_expires_at is None
        or row.claim_expires_at <= current
        or row.active_outbox_id is not None
    ):
        raise OutboundFencingError("generation claim 已失效")
    return row


def _assert_run_control(
    db: Session,
    *,
    run: OutboundRun,
    current: datetime,
) -> OutboundDeliveryControl:
    control = _control(db, str(run.source_type))
    mode, epoch = _mode_for_occurrence(
        control,
        occurrence_at=run.scheduled_for or current,
    )
    if mode != run.delivery_mode or epoch != int(run.cutover_epoch):
        raise OutboundSafetyError("run 与当前 cutover control 不一致")
    return control


def _block_generation_claim(
    db: Session,
    *,
    run: OutboundRun,
    owner: str,
    claim_token: str,
    reason_type: str,
    reason_summary: str,
    current: datetime,
) -> bool:
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
    if updated == 1:
        _project_outbound_source(
            db,
            run=run,
            status="blocked",
            current=current,
            error_summary=reason_summary,
        )
    db.flush()
    db.expire_all()
    return updated == 1


def start_generation_attempt(
    db: Session,
    *,
    run_id: int,
    owner: str,
    claim_token: str,
    writer_owner: str,
    writer_token: str,
    writer_protocol_version: int,
    endpoint_key: str,
    destination_fingerprint: str,
    endpoint_config_revision: str,
    payload_contract_fingerprint: str,
    now: datetime | None = None,
) -> GenerationAttemptHandle:
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
    run = _require_generation_run(
        db,
        run_id=run_id,
        owner=normalized_owner,
        claim_token=token,
        current=current,
        allowed_statuses={"claimed"},
    )
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
    if (
        run.writer_owner != normalized_writer_owner
        or run.writer_token != normalized_writer_token
        or int(run.writer_protocol_version) != int(writer_protocol_version)
    ):
        raise OutboundFencingError("generation writer fence 与 occurrence 不一致")
    _require_writer(
        db,
        source_type=str(run.source_type),
        owner=normalized_writer_owner,
        token=normalized_writer_token,
        protocol_version=writer_protocol_version,
        current=current,
    )
    revision = _text(
        endpoint_config_revision,
        name="endpoint_config_revision",
        max_length=128,
    )
    frozen_contract = _load_delivery_contract(run)
    normalized_frozen_contract = dict(frozen_contract)
    normalized_frozen_contract.pop("endpoint_config_revision", None)
    expected_circuit_facts = {
        "endpoint_key": _text(endpoint_key, name="endpoint_key", max_length=64),
        "destination_fingerprint": _text(
            destination_fingerprint,
            name="destination_fingerprint",
            max_length=64,
        ),
        "payload_contract_fingerprint": _text(
            payload_contract_fingerprint,
            name="payload_contract_fingerprint",
            max_length=64,
        ),
    }
    if any(
        normalized_frozen_contract.get(name) != value
        for name, value in expected_circuit_facts.items()
    ):
        raise OutboundConflictError("生成事实与 occurrence 冻结投递合同不一致")
    try:
        _assert_run_control(db, run=run, current=current)
    except OutboundSafetyError as exc:
        _block_generation_claim(
            db,
            run=run,
            owner=normalized_owner,
            claim_token=token,
            reason_type="cutover_changed",
            reason_summary=str(exc),
            current=current,
        )
        return GenerationAttemptHandle(
            run_id=int(run.id),
            attempt_id=None,
            attempt_no=None,
            owner="",
            fencing_token="",
            status="blocked",
            reason_type="cutover_changed",
        )
    circuit = _open_circuit_row(db, _circuit_facts(
        endpoint_key=endpoint_key,
        destination_fingerprint=destination_fingerprint,
        payload_contract_fingerprint=payload_contract_fingerprint,
        config_revision=revision,
    ))
    if circuit is not None:
        _block_generation_claim(
            db,
            run=run,
            owner=normalized_owner,
            claim_token=token,
            reason_type="circuit_open",
            reason_summary=f"{circuit.scope_type} circuit 已打开",
            current=current,
        )
        return GenerationAttemptHandle(
            run_id=int(run.id),
            attempt_id=None,
            attempt_no=None,
            owner="",
            fencing_token="",
            status="blocked",
            reason_type="circuit_open",
        )

    (
        db.query(OutboundGenerationAttempt)
        .filter(
            OutboundGenerationAttempt.run_id == run.id,
            OutboundGenerationAttempt.status == "started",
            OutboundGenerationAttempt.fencing_token != token,
        )
        .update(
            {
                OutboundGenerationAttempt.status: "abandoned",
                OutboundGenerationAttempt.completed_at: current,
                OutboundGenerationAttempt.error_type: "claim_expired",
                OutboundGenerationAttempt.error_summary: "生成租约已被新 owner 接管",
            },
            synchronize_session=False,
        )
    )
    next_attempt_no = int(
        db.query(func.max(OutboundGenerationAttempt.attempt_no))
        .filter(OutboundGenerationAttempt.run_id == run.id)
        .scalar()
        or 0
    ) + 1
    updated = (
        db.query(OutboundRun)
        .filter(
            OutboundRun.id == run.id,
            OutboundRun.status == "claimed",
            OutboundRun.claim_owner == normalized_owner,
            OutboundRun.claim_token == token,
            OutboundRun.claim_expires_at > current,
            OutboundRun.active_outbox_id.is_(None),
        )
        .update(
            {
                OutboundRun.status: "generating",
                OutboundRun.attempted_at: func.coalesce(
                    OutboundRun.attempted_at,
                    current,
                ),
                OutboundRun.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise OutboundFencingError("generation claim CAS 失败")
    attempt = OutboundGenerationAttempt(
        run_id=int(run.id),
        attempt_no=next_attempt_no,
        owner=normalized_owner,
        fencing_token=token,
        status="started",
        started_at=current,
        completed_at=None,
        model_trace_id="",
        content_sha256="",
        error_type="",
        error_summary="",
        created_at=current,
    )
    db.add(attempt)
    db.flush()
    _project_outbound_source(
        db,
        run=run,
        status="generating",
        current=current,
        attempted=True,
    )
    db.flush()
    result = GenerationAttemptHandle(
        run_id=int(run.id),
        attempt_id=int(attempt.id),
        attempt_no=next_attempt_no,
        owner=normalized_owner,
        fencing_token=token,
        status="started",
        reason_type="",
    )
    db.expire_all()
    return result



__all__ = [
    "claim_outbound_run",
    "quarantine_expired_generation_run",
    "renew_outbound_run_claim",
    "start_generation_attempt",
]
