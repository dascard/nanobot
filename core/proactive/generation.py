"""主动外呼生成 occurrence 的恢复、模型工作与原子落账编排。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from core.db.models.outbound import (
    OutboundDeliveryOutbox,
    OutboundRun,
)
from core.db.models.proactive import ProactiveOutreachLog
from core.message_envelope import is_html_reply
from core.message_transport_adapters import render_chat_json
from core.model_provider.response_normalization import strip_think_blocks
from core.outbound.contracts import (
    OUTBOUND_PROTOCOL_VERSION,
    PROACTIVE_GENERATED_TASK_KIND,
    PROACTIVE_GENERATION_METADATA_KEY,
    OutboundConflictError,
    OutboundFencingError,
)
from core.outbound.control import lock_outbound_source_control
from core.outbound.generation import commit_generated_outbox, fail_outbound_generation
from core.outbound.policy import (
    prepare_proactive_generation_grounding,
    proactive_generation_metadata,
    proactive_outreach_delivery_key,
    proactive_outreach_destination_fingerprint,
    proactive_outreach_generated_source_revision,
    proactive_outreach_generated_source_snapshot,
    proactive_outreach_occurrence_key,
)
from core.outbound.projection import proactive_outreach_linkage_is_current
from core.outbound.run_claims import claim_outbound_run, start_generation_attempt
from core.proactive.config import (
    OUTREACH_CLAIM_LEASE_SECONDS,
    OUTREACH_DEFAULT_MAX_ATTEMPTS,
    OUTREACH_DEFAULT_RETRY_DEADLINE_SECONDS,
    OUTREACH_ENDPOINT_KEY,
    OUTREACH_PAYLOAD_CONTRACT,
    OUTREACH_SOURCE_TYPE,
    OUTREACH_WRITER_LEASE_SECONDS,
)
from core.proactive.runtime_identity import get_proactive_process_identity
from core.proactive.delivery_runtime import _deliver_legacy_outreach_leaf
from core.proactive.identity_policy import outreach_key as _outreach_key
from core.proactive.lease import fence_evaluation_write as _fence_evaluation_write
from core.proactive.model_policy import parse_iso_datetime as _parse_iso_datetime
from core.proactive.repository import (
    cancelled_row_is_reusable as _cancelled_row_is_reusable,
    history_clear_at as _history_clear_at,
)
from core.proactive.runtime_support import (
    _outbound_occurrence_utc,
    _outreach_endpoint_revision,
    _positive_env_integer,
    _positive_env_number,
)
from core.proactive.schedule_repository import (
    pending_schedule_conflict_result as _pending_schedule_conflict_result,
    upsert_pending_schedule as _upsert_pending_schedule,
)
from core.proactive.serialization import (
    grounding_json as _grounding_json,
    grounding_json_for_model as _grounding_json_for_model,
)
from foundation.identity import RecipientIdentity
from foundation.message_contract import (
    MessageAction,
    OutboundMessageContract,
    TextContent,
    TextFormat,
)

@dataclass(frozen=True, slots=True)
class _GeneratedOutreachWork:
    log_id: int
    run_id: int
    generation_attempt_id: int
    owner: str
    claim_token: str
    delivery_mode: str
    idempotency_key: str
    input_grounding: dict[str, Any]
    persisted_grounding: dict[str, Any]
    judge: dict[str, Any]
    kind: str
    forced: bool
    created_at: datetime
    source_revision: str
    destination_snapshot: dict[str, Any]
    destination_fingerprint: str
    endpoint_revision: str


def _outreach_result(
    session: Session,
    *,
    log_id: int,
    run_id: int,
    outbox_id: int | None,
    forced: bool,
    deduplicated: bool,
) -> dict[str, Any]:
    session.expire_all()
    row = session.get(ProactiveOutreachLog, int(log_id))
    outbox = (
        session.get(OutboundDeliveryOutbox, int(outbox_id))
        if outbox_id is not None
        else None
    )
    status = str(row.status) if row is not None else "state_error"
    if outbox is not None and outbox.status == "pending":
        status = "queued"
    result = {
        "status": status,
        "log_id": int(log_id),
        "run_id": int(run_id),
        "outbox_id": int(outbox_id) if outbox_id is not None else None,
        "forced": bool(forced),
        "deduplicated": bool(deduplicated),
    }
    if outbox is not None and str(outbox.last_error_type or "").strip():
        result["error_type"] = str(outbox.last_error_type)
    return result


def _blocked_outreach_without_outbox_is_current(
    session: Session,
    row: ProactiveOutreachLog | None,
) -> bool:
    if (
        row is None
        or str(row.status or "") != "blocked"
        or row.outbound_run_id is None
    ):
        return False
    run = session.get(OutboundRun, int(row.outbound_run_id))
    return bool(
        run is not None
        and str(run.status or "") == "blocked"
        and run.active_outbox_id is None
        and proactive_outreach_linkage_is_current(
            session,
            run_id=int(run.id),
        )
    )


def _generated_row_facts(
    row: ProactiveOutreachLog,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    try:
        grounding = json.loads(str(row.grounding_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise OutboundConflictError(
            "主动外呼 generated grounding 无法解析"
        ) from exc
    if type(grounding) is not dict:
        raise OutboundConflictError(
            "主动外呼 generated grounding 必须是 JSON object"
        )
    if PROACTIVE_GENERATION_METADATA_KEY not in grounding:
        return None
    metadata = proactive_generation_metadata(grounding)
    return grounding, metadata


def _unacquired_generated_result(
    session: Session,
    *,
    row: ProactiveOutreachLog,
    run_id: int,
) -> dict[str, Any]:
    session.expire_all()
    current_row = session.get(ProactiveOutreachLog, int(row.id))
    run = session.get(OutboundRun, int(run_id))
    if current_row is None or run is None:
        return {
            "status": "state_error",
            "error_type": "generated_run_missing",
            "log_id": int(row.id),
            "forced": bool(row.forced),
        }
    if run.active_outbox_id is not None:
        return _outreach_result(
            session,
            log_id=int(current_row.id),
            run_id=int(run.id),
            outbox_id=int(run.active_outbox_id),
            forced=bool(current_row.forced),
            deduplicated=True,
        )
    if str(run.status or "") in {"claimed", "generating"}:
        return {
            "status": "skipped_in_progress",
            "log_id": int(current_row.id),
            "run_id": int(run.id),
            "forced": bool(current_row.forced),
        }
    if str(run.status or "") == "failed":
        return {
            "status": "generation_error",
            "error_type": str(run.failure_type or "generation_failed"),
            "reason": str(run.failure_summary or ""),
            "log_id": int(current_row.id),
            "run_id": int(run.id),
            "forced": bool(current_row.forced),
        }
    return _outreach_result(
        session,
        log_id=int(current_row.id),
        run_id=int(run.id),
        outbox_id=None,
        forced=bool(current_row.forced),
        deduplicated=True,
    )


def _claim_generated_row_locked(
    session: Session,
    *,
    row: ProactiveOutreachLog,
    runtime_at: datetime,
) -> tuple[_GeneratedOutreachWork | None, dict[str, Any] | None]:
    process_identity = get_proactive_process_identity()
    facts = _generated_row_facts(row)
    if facts is None:
        raise OutboundConflictError(
            "主动外呼 generated 候选缺少冻结标记"
        )
    persisted_grounding, metadata = facts
    if row.id is None or row.created_at is None:
        raise OutboundConflictError(
            "主动外呼 generated 候选缺少持久身份"
        )
    log_id = int(row.id)
    idempotency_key = str(row.idempotency_key or "").strip()
    if not idempotency_key:
        raise OutboundConflictError(
            "主动外呼 generated 候选缺少幂等键"
        )
    created_at = row.created_at
    kind = str(metadata["kind"])
    forced = kind == "forced"
    input_grounding = dict(metadata["input_grounding"])
    judge = dict(metadata["judge"])
    snapshot = proactive_outreach_generated_source_snapshot(row)
    source_revision = (
        proactive_outreach_generated_source_revision(row)
    )
    occurrence_key = proactive_outreach_occurrence_key(
        log_id=log_id,
        idempotency_key=idempotency_key,
        source_revision=source_revision,
    )
    destination_snapshot = {
        "target_type": "private",
        "target_id": str(row.user_id),
    }
    destination_fingerprint = (
        proactive_outreach_destination_fingerprint(
            str(row.user_id)
        )
    )
    endpoint_revision = _outreach_endpoint_revision()
    claim = claim_outbound_run(
        session,
        source_type=OUTREACH_SOURCE_TYPE,
        source_id=str(log_id),
        occurrence_key=occurrence_key,
        source_revision=source_revision,
        source_snapshot=snapshot,
        destination_snapshot=destination_snapshot,
        target_type="private",
        task_kind=PROACTIVE_GENERATED_TASK_KIND,
        scheduled_for=_outbound_occurrence_utc(created_at),
        trigger_type="forced" if forced else "judge",
        owner=process_identity.owner,
        claim_lease_seconds=OUTREACH_CLAIM_LEASE_SECONDS,
        writer_owner=process_identity.owner,
        writer_token=process_identity.writer_token,
        writer_protocol_version=OUTBOUND_PROTOCOL_VERSION,
        writer_lease_seconds=OUTREACH_WRITER_LEASE_SECONDS,
        endpoint_key=OUTREACH_ENDPOINT_KEY,
        destination_fingerprint=destination_fingerprint,
        endpoint_config_revision=endpoint_revision,
        payload_contract_fingerprint=OUTREACH_PAYLOAD_CONTRACT,
        now=runtime_at,
    )
    if not claim.acquired:
        return None, _unacquired_generated_result(
            session,
            row=row,
            run_id=claim.run_id,
        )
    generation = start_generation_attempt(
        session,
        run_id=claim.run_id,
        owner=claim.owner,
        claim_token=claim.claim_token,
        writer_owner=process_identity.owner,
        writer_token=process_identity.writer_token,
        writer_protocol_version=OUTBOUND_PROTOCOL_VERSION,
        endpoint_key=OUTREACH_ENDPOINT_KEY,
        destination_fingerprint=destination_fingerprint,
        endpoint_config_revision=endpoint_revision,
        payload_contract_fingerprint=OUTREACH_PAYLOAD_CONTRACT,
        now=runtime_at,
    )
    if generation.status != "started" or generation.attempt_id is None:
        return None, _outreach_result(
            session,
            log_id=log_id,
            run_id=claim.run_id,
            outbox_id=None,
            forced=forced,
            deduplicated=False,
        )
    return _GeneratedOutreachWork(
        log_id=log_id,
        run_id=claim.run_id,
        generation_attempt_id=int(generation.attempt_id),
        owner=claim.owner,
        claim_token=claim.claim_token,
        delivery_mode=claim.delivery_mode,
        idempotency_key=idempotency_key,
        input_grounding=input_grounding,
        persisted_grounding=persisted_grounding,
        judge=judge,
        kind=kind,
        forced=forced,
        created_at=created_at,
        source_revision=source_revision,
        destination_snapshot=destination_snapshot,
        destination_fingerprint=destination_fingerprint,
        endpoint_revision=endpoint_revision,
    ), None


def _resume_generated_outreach(
    session: Session,
    *,
    row: ProactiveOutreachLog,
    user_id: str,
    evaluation_owner_token: str | None,
) -> tuple[_GeneratedOutreachWork | None, dict[str, Any] | None]:
    try:
        runtime_at = lock_outbound_source_control(
            session,
            source_type=OUTREACH_SOURCE_TYPE,
            now=None,
        )
        if not _fence_evaluation_write(
            session,
            user_id=user_id,
            owner_token=evaluation_owner_token,
        ):
            session.rollback()
            return None, {
                "status": "lease_lost",
                "log_id": int(row.id),
                "forced": bool(row.forced),
            }
        current = session.get(ProactiveOutreachLog, int(row.id))
        if current is None or current.user_id != user_id:
            session.rollback()
            return None, {
                "status": "stale_schedule",
                "log_id": int(row.id),
                "forced": bool(row.forced),
            }
        clear_at = _history_clear_at(session, user_id)
        if (
            clear_at is not None
            and current.created_at is not None
            and current.created_at <= clear_at
        ):
            session.rollback()
            return None, {
                "status": "cancelled_history_clear",
                "log_id": int(current.id),
                "forced": bool(current.forced),
            }
        work, result = _claim_generated_row_locked(
            session,
            row=current,
            runtime_at=runtime_at,
        )
        session.commit()
        return work, result
    except BaseException:
        session.rollback()
        raise


def _prepare_generated_outreach(
    session: Session,
    *,
    user_id: str,
    idempotency_key: str,
    grounding: dict[str, Any],
    judge: dict[str, Any],
    kind: str,
    next_check_at: datetime | None,
    next_intent: str,
    created_at: datetime,
    schedule_row_id: int | None,
    evaluation_owner_token: str | None,
) -> tuple[_GeneratedOutreachWork | None, dict[str, Any] | None]:
    try:
        runtime_at = lock_outbound_source_control(
            session,
            source_type=OUTREACH_SOURCE_TYPE,
            now=None,
        )
        if not _fence_evaluation_write(
            session,
            user_id=user_id,
            owner_token=evaluation_owner_token,
        ):
            session.rollback()
            return None, {
                "status": "lease_lost",
                "log_id": schedule_row_id,
                "forced": kind == "forced",
            }
        clear_at = _history_clear_at(session, user_id)
        if clear_at is not None and created_at <= clear_at:
            session.rollback()
            return None, {
                "status": "cancelled_history_clear",
                "log_id": schedule_row_id,
                "forced": kind == "forced",
            }
        existing = (
            session.query(ProactiveOutreachLog)
            .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            existing_facts = _generated_row_facts(existing)
            if existing_facts is not None and existing.outbound_run_id is not None:
                if existing.status not in {"candidate", "blocked"}:
                    conflict_result = _pending_schedule_conflict_result(
                        session,
                        row=existing,
                        user_id=user_id,
                    )
                    session.rollback()
                    return None, conflict_result
                work, result = _claim_generated_row_locked(
                    session,
                    row=existing,
                    runtime_at=runtime_at,
                )
                session.commit()
                return work, result
            if existing.status not in {"pending", "candidate", "cancelled"}:
                session.rollback()
                return None, _pending_schedule_conflict_result(
                    session,
                    row=existing,
                    user_id=user_id,
                )
        schedule_row = (
            session.get(ProactiveOutreachLog, int(schedule_row_id))
            if schedule_row_id is not None
            else None
        )
        row = existing or schedule_row
        if existing is not None and schedule_row is not None:
            if int(existing.id) != int(schedule_row.id):
                if _cancelled_row_is_reusable(
                    session,
                    row=existing,
                    user_id=user_id,
                    generation_at=created_at,
                ):
                    if schedule_row.status == "pending":
                        schedule_row.status = "cancelled"
                    row = existing
                else:
                    session.rollback()
                    return None, _pending_schedule_conflict_result(
                        session,
                        row=existing,
                        user_id=user_id,
                    )
        if row is None:
            row = ProactiveOutreachLog(user_id=user_id)
            session.add(row)
        if row.user_id != user_id:
            raise OutboundConflictError(
                "主动外呼 generated 候选用户不一致"
            )
        if row.status == "cancelled" and not _cancelled_row_is_reusable(
            session,
            row=row,
            user_id=user_id,
            generation_at=created_at,
        ):
            session.rollback()
            return None, {
                "status": "cancelled_history_clear",
                "log_id": int(row.id),
                "forced": kind == "forced",
            }
        persisted_grounding = (
            prepare_proactive_generation_grounding(
                grounding=grounding,
                kind=kind,
                judge=judge,
            )
        )
        row.idempotency_key = idempotency_key
        row.grounding_json = _grounding_json(persisted_grounding)
        row.judge_should = True
        row.judge_reason = str(judge.get("reason") or "")
        row.next_check_at = next_check_at
        row.next_intent = next_intent
        row.message = ""
        row.forced = kind == "forced"
        row.status = "candidate"
        row.outbound_run_id = None
        row.created_at = created_at
        session.flush()
        work, result = _claim_generated_row_locked(
            session,
            row=row,
            runtime_at=runtime_at,
        )
        session.commit()
        return work, result
    except BaseException:
        session.rollback()
        raise


def _generated_lease_lost_result(
    work: _GeneratedOutreachWork,
) -> dict[str, Any]:
    return {
        "status": "lease_lost",
        "log_id": work.log_id,
        "run_id": work.run_id,
        "forced": work.forced,
    }


def _fail_generated_outreach(
    session: Session,
    *,
    work: _GeneratedOutreachWork,
    user_id: str,
    error_type: str,
    error_summary: str,
    evaluation_owner_token: str | None,
    research_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        runtime_at = lock_outbound_source_control(
            session,
            source_type=OUTREACH_SOURCE_TYPE,
            now=None,
        )
        if not _fence_evaluation_write(
            session,
            user_id=user_id,
            owner_token=evaluation_owner_token,
        ):
            session.rollback()
            return _generated_lease_lost_result(work)
        row = session.get(ProactiveOutreachLog, work.log_id)
        if row is None or row.outbound_run_id != work.run_id:
            session.rollback()
            return _generated_lease_lost_result(work)
        clear_at = _history_clear_at(session, user_id)
        history_cleared = clear_at is not None and work.created_at <= clear_at
        settled_error_type = "history_cleared" if history_cleared else error_type
        settled_summary = "用户历史已清除" if history_cleared else error_summary
        fail_outbound_generation(
            session,
            run_id=work.run_id,
            generation_attempt_id=work.generation_attempt_id,
            owner=work.owner,
            claim_token=work.claim_token,
            error_type=settled_error_type,
            error_summary=settled_summary,
            now=runtime_at,
        )
        session.expire_all()
        failed_row = session.get(ProactiveOutreachLog, work.log_id)
        if failed_row is None or failed_row.outbound_run_id != work.run_id:
            raise OutboundFencingError(
                "生成失败后的主动外呼来源已失效"
            )
        output_grounding = dict(work.persisted_grounding)
        output_grounding["generation_error"] = {
            "error_type": settled_error_type,
            "reason": str(settled_summary or "")[:500],
        }
        if research_payload:
            output_grounding["research"] = research_payload
        values: dict[Any, Any] = {
            ProactiveOutreachLog.grounding_json: _grounding_json(
                output_grounding
            )
        }
        if history_cleared:
            values[ProactiveOutreachLog.status] = "cancelled"
        updated = (
            session.query(ProactiveOutreachLog)
            .filter(
                ProactiveOutreachLog.id == work.log_id,
                ProactiveOutreachLog.user_id == user_id,
                ProactiveOutreachLog.idempotency_key == work.idempotency_key,
                ProactiveOutreachLog.outbound_run_id == work.run_id,
                ProactiveOutreachLog.status == "failed",
                ProactiveOutreachLog.message == "",
                ProactiveOutreachLog.created_at == work.created_at,
            )
            .update(values, synchronize_session=False)
        )
        if updated != 1:
            raise OutboundFencingError(
                "生成失败输出 CAS 已失效"
            )
        session.commit()
        if history_cleared:
            return {
                "status": "cancelled_history_clear",
                "log_id": work.log_id,
                "run_id": work.run_id,
                "forced": work.forced,
            }
        return {
            "status": "generation_error",
            "error_type": error_type,
            "reason": str(error_summary or "")[:500],
            "log_id": work.log_id,
            "run_id": work.run_id,
            "forced": work.forced,
        }
    except OutboundFencingError:
        session.rollback()
        return _generated_lease_lost_result(work)
    except BaseException:
        session.rollback()
        raise


async def _commit_generated_outreach(
    session: Session,
    *,
    work: _GeneratedOutreachWork,
    user_id: str,
    message: str,
    evaluation_owner_token: str | None,
    publisher: Callable[[str, str, str], Any] | None,
    research_payload: dict[str, Any] | None = None,
    forced_fallback: dict[str, Any] | None = None,
    generation_error_type: str = "",
    generation_error_summary: str = "",
    model_trace_id: str = "",
    commit_outbox: Callable[..., Any] = commit_generated_outbox,
    result_projector: Callable[..., dict[str, Any]] = _outreach_result,
    legacy_deliverer: Callable[..., Any] = _deliver_legacy_outreach_leaf,
) -> dict[str, Any]:
    cleaned_message = strip_think_blocks(str(message or "")).strip()
    if not cleaned_message:
        return _fail_generated_outreach(
            session,
            work=work,
            user_id=user_id,
            error_type="contract_error",
            error_summary="主动外呼正文为空",
            evaluation_owner_token=evaluation_owner_token,
            research_payload=research_payload,
        )
    try:
        runtime_at = lock_outbound_source_control(
            session,
            source_type=OUTREACH_SOURCE_TYPE,
            now=None,
        )
        if not _fence_evaluation_write(
            session,
            user_id=user_id,
            owner_token=evaluation_owner_token,
        ):
            session.rollback()
            return _generated_lease_lost_result(work)
        row = session.get(ProactiveOutreachLog, work.log_id)
        if row is None or row.outbound_run_id != work.run_id:
            session.rollback()
            return _generated_lease_lost_result(work)
        clear_at = _history_clear_at(session, user_id)
        if clear_at is not None and work.created_at <= clear_at:
            session.rollback()
            return _fail_generated_outreach(
                session,
                work=work,
                user_id=user_id,
                error_type="history_cleared",
                error_summary="用户历史已清除",
                evaluation_owner_token=evaluation_owner_token,
                research_payload=research_payload,
            )
        outbound_message = OutboundMessageContract(
            action=MessageAction.REPLY,
            recipient=RecipientIdentity(
                platform="qq",
                recipient_type="user",
                recipient_id=user_id,
            ),
            parts=(
                TextContent(
                    cleaned_message,
                    format=(
                        TextFormat.HTML
                        if is_html_reply(cleaned_message)
                        else TextFormat.PLAIN
                    ),
                ),
            ),
        )
        envelope = render_chat_json(
            outbound_message,
            status="ok",
            meta={
                "platform": "qq",
                "chat_type": "private",
                "source": OUTREACH_SOURCE_TYPE,
                "log_id": work.log_id,
            },
        )
        queued = commit_outbox(
            session,
            run_id=work.run_id,
            generation_attempt_id=work.generation_attempt_id,
            owner=work.owner,
            claim_token=work.claim_token,
            idempotency_key=(
                proactive_outreach_delivery_key(
                    log_id=work.log_id,
                    idempotency_key=work.idempotency_key,
                    source_revision=work.source_revision,
                )
            ),
            destination_snapshot=work.destination_snapshot,
            destination_fingerprint=work.destination_fingerprint,
            target_type="private",
            endpoint_key=OUTREACH_ENDPOINT_KEY,
            payload=envelope,
            max_attempts=_positive_env_integer(
                "NANOBOT_OUTBOUND_MAX_ATTEMPTS",
                OUTREACH_DEFAULT_MAX_ATTEMPTS,
            ),
            retry_deadline_at=(
                runtime_at
                + timedelta(seconds=_positive_env_number(
                    "NANOBOT_OUTBOUND_RETRY_DEADLINE_SECONDS",
                    OUTREACH_DEFAULT_RETRY_DEADLINE_SECONDS,
                ))
            ),
            endpoint_config_revision=work.endpoint_revision,
            payload_contract_fingerprint=OUTREACH_PAYLOAD_CONTRACT,
            model_trace_id=model_trace_id,
            generation_error_type=generation_error_type,
            generation_error_summary=generation_error_summary,
            now=runtime_at,
        )
        if queued.outbox_id is None:
            session.commit()
            result = result_projector(
                session,
                log_id=work.log_id,
                run_id=work.run_id,
                outbox_id=None,
                forced=work.forced,
                deduplicated=not queued.created,
            )
            session.rollback()
            return result
        if queued.created:
            output_grounding = dict(work.persisted_grounding)
            if research_payload:
                output_grounding["research"] = research_payload
            if forced_fallback:
                output_grounding["forced_fallback"] = forced_fallback
            session.expire_all()
            queued_row = session.get(ProactiveOutreachLog, work.log_id)
            if queued_row is None:
                raise OutboundFencingError(
                    "generated outbox 入队后来源丢失"
                )
            values: dict[Any, Any] = {
                ProactiveOutreachLog.grounding_json: _grounding_json(
                    output_grounding
                ),
                ProactiveOutreachLog.message: cleaned_message,
            }
            if forced_fallback:
                values[ProactiveOutreachLog.judge_reason] = (
                    f"{queued_row.judge_reason or ''}；"
                    "Generator 契约失败，使用服务端安全兜底"
                )[:1000]
            updated = (
                session.query(ProactiveOutreachLog)
                .filter(
                    ProactiveOutreachLog.id == work.log_id,
                    ProactiveOutreachLog.user_id == user_id,
                    ProactiveOutreachLog.idempotency_key
                    == work.idempotency_key,
                    ProactiveOutreachLog.outbound_run_id == work.run_id,
                    ProactiveOutreachLog.status == "queued",
                    ProactiveOutreachLog.grounding_json
                    == _grounding_json(work.persisted_grounding),
                    ProactiveOutreachLog.message == "",
                    ProactiveOutreachLog.created_at == work.created_at,
                )
                .update(values, synchronize_session=False)
            )
            if updated != 1:
                raise OutboundFencingError(
                    "generated 候选输出 CAS 已失效"
                )
        session.commit()
    except OutboundFencingError:
        session.rollback()
        return _generated_lease_lost_result(work)
    except BaseException:
        session.rollback()
        raise

    if (
        work.delivery_mode == "legacy_direct"
        and queued.outbox_id is not None
        and publisher is not None
    ):
        await legacy_deliverer(
            session=session,
            outbox_id=int(queued.outbox_id),
            publisher=publisher,
            endpoint_config_revision=work.endpoint_revision,
            now=None,
        )
    result = result_projector(
        session,
        log_id=work.log_id,
        run_id=work.run_id,
        outbox_id=queued.outbox_id,
        forced=work.forced,
        deduplicated=not queued.created,
    )
    session.rollback()
    return result


async def _run_generated_outreach(
    session: Session,
    *,
    work: _GeneratedOutreachWork,
    user_id: str,
    generator_fn: Callable[..., str],
    research_fn: Callable[..., Any] | None,
    publisher: Callable[[str, str, str], Any] | None,
    evaluation_owner_token: str | None,
    commit_outbox: Callable[..., Any] = commit_generated_outbox,
    result_projector: Callable[..., dict[str, Any]] = _outreach_result,
    legacy_deliverer: Callable[..., Any] = _deliver_legacy_outreach_leaf,
) -> dict[str, Any]:
    if work.kind == "forced":
        return _fail_generated_outreach(
            session,
            work=work,
            user_id=user_id,
            error_type="forced_delivery_disabled",
            error_summary="最长静默强制投递已停用，等待重新评估",
            evaluation_owner_token=evaluation_owner_token,
        )

    from core.proactive_candidate import materialize_outreach_candidate

    try:
        candidate_grounding = dict(work.input_grounding)
        candidate_grounding.pop("_trigger_runtime", None)
        evaluation = await materialize_outreach_candidate(
            user_id=user_id,
            request_id=work.idempotency_key,
            grounding=candidate_grounding,
            judge=work.judge,
            generator_fn=generator_fn,
            research_fn=research_fn,
            context_summary=_grounding_json_for_model(candidate_grounding),
        )
    except asyncio.CancelledError as exc:
        durable_reason = {
            "durable_task_cancelled": ("agent_run_cancelled", "研究 Run 已取消"),
            "durable_task_timed_out": ("agent_run_timed_out", "研究 Run 已超时"),
            "durable_task_lease_lost": (
                "agent_run_ambiguous",
                "研究 Run 执行租约已失效",
            ),
        }.get(str(exc))
        if durable_reason is None:
            raise
        return _fail_generated_outreach(
            session,
            work=work,
            user_id=user_id,
            error_type=durable_reason[0],
            error_summary=durable_reason[1],
            evaluation_owner_token=evaluation_owner_token,
        )
    status = str(evaluation.get("status") or "")
    research_payload = (
        dict(evaluation.get("research") or {})
        if isinstance(evaluation.get("research"), dict)
        else {}
    )
    if status == "candidate":
        return await _commit_generated_outreach(
            session,
            work=work,
            user_id=user_id,
            message=str(evaluation.get("message") or ""),
            evaluation_owner_token=evaluation_owner_token,
            publisher=publisher,
            research_payload=research_payload or None,
            model_trace_id=str(evaluation.get("model_trace_id") or "")[:128],
            commit_outbox=commit_outbox,
            result_projector=result_projector,
            legacy_deliverer=legacy_deliverer,
        )
    error_type = str(
        evaluation.get("error_type")
        or evaluation.get("reason_code")
        or "contract_error"
    )
    error_reason = str(
        evaluation.get("reason")
        or evaluation.get("error")
        or evaluation.get("reason_code")
        or "主动外呼正文生成失败"
    )[:500]
    failed = _fail_generated_outreach(
        session,
        work=work,
        user_id=user_id,
        error_type=error_type,
        error_summary=error_reason,
        evaluation_owner_token=evaluation_owner_token,
        research_payload=research_payload or None,
    )
    if status != "research_blocked" or failed.get("status") != "generation_error":
        return failed
    next_check_at = _parse_iso_datetime(work.judge.get("next_check_at"))
    pending_grounding = dict(work.input_grounding)
    if research_payload:
        pending_grounding["research"] = research_payload
    row = _upsert_pending_schedule(
        session,
        user_id=user_id,
        idempotency_key=_outreach_key(
            user_id,
            next_check_at or work.created_at,
            forced=False,
        ),
        grounding=pending_grounding,
        judge_reason=str(work.judge.get("reason") or ""),
        next_check_at=next_check_at,
        next_intent=str(work.judge.get("next_intent") or ""),
        created_at=work.created_at,
        evaluation_owner_token=evaluation_owner_token,
    )
    if row is None:
        return _generated_lease_lost_result(work)
    if row.status != "pending":
        return _pending_schedule_conflict_result(
            session,
            row=row,
            user_id=user_id,
        )
    return {
        "status": "research_blocked",
        "reason_code": str(
            evaluation.get("reason_code") or "runtime_error"
        ),
        "log_id": int(row.id),
        "failed_log_id": work.log_id,
        "run_id": work.run_id,
        "forced": False,
    }



__all__ = []
