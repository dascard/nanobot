"""主动外呼 prepared 候选的幂等入队与 cutover 投递编排。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.models.chat import User
from core.db.models.outbound import OutboundRun
from core.db.models.proactive import (
    ProactiveOutreachLease,
    ProactiveOutreachLog,
)
from core.message_envelope import is_html_reply
from core.message_transport_adapters import render_chat_json
from core.model_provider.response_normalization import strip_think_blocks
from core.outbound.contracts import OUTBOUND_PROTOCOL_VERSION
from core.outbound.control import lock_outbound_source_control
from core.outbound.generation import commit_prepared_outbox
from core.outbound.policy import (
    proactive_outreach_delivery_key,
    proactive_outreach_destination_fingerprint,
    proactive_outreach_occurrence_key,
    proactive_outreach_source_revision,
    proactive_outreach_source_snapshot,
)
from core.outbound.run_claims import claim_outbound_run
from core.proactive.config import (
    OUTREACH_CLAIM_LEASE_SECONDS,
    OUTREACH_DEFAULT_MAX_ATTEMPTS,
    OUTREACH_DEFAULT_RETRY_DEADLINE_SECONDS,
    OUTREACH_ENDPOINT_KEY,
    OUTREACH_PAYLOAD_CONTRACT,
    OUTREACH_SOURCE_TYPE,
    OUTREACH_WRITER_LEASE_SECONDS,
)
from core.proactive.delivery_runtime import _deliver_legacy_outreach_leaf
from core.proactive.generation import (
    _blocked_outreach_without_outbox_is_current,
    _outreach_result,
)
from core.proactive.lease import evaluation_lease_is_owned as _evaluation_lease_is_owned
from core.proactive.repository import (
    cancelled_row_is_reusable as _cancelled_row_is_reusable,
    history_clear_at as _history_clear_at,
)
from core.proactive.runtime_support import (
    _outbound_occurrence_utc,
    _outreach_endpoint_revision,
    _positive_env_integer,
    _positive_env_number,
    session_scope as _session_scope,
)
from core.proactive.runtime_identity import get_proactive_process_identity
from core.proactive.serialization import grounding_json as _grounding_json
from foundation.identity import RecipientIdentity
from foundation.message_contract import (
    MessageAction,
    OutboundMessageContract,
    TextContent,
    TextFormat,
)


logger = logging.getLogger("nanobot.proactive.delivery")


async def _enqueue_outreach_outbox(
    *,
    user_id: str,
    idempotency_key: str,
    grounding: dict[str, Any],
    judge_should: bool,
    judge_reason: str,
    next_check_at: datetime | None,
    next_intent: str,
    message: str,
    forced: bool,
    db: Session | None,
    schedule_row_id: int | None,
    schedule_expected_idempotency_key: str | None,
    created_at: datetime | None,
    delivered_at: datetime | None,
    publisher: Callable[[str, str, str], Any] | None,
    evaluation_owner_token: str | None,
    sanitize_text: Callable[[str], str] = strip_think_blocks,
    result_projector: Callable[..., dict[str, Any]] = _outreach_result,
    claim_run: Callable[..., Any] = claim_outbound_run,
    commit_outbox: Callable[..., Any] = commit_prepared_outbox,
    evaluation_lease_checker: Callable[..., bool] = _evaluation_lease_is_owned,
    legacy_deliverer: Callable[..., Any] = _deliver_legacy_outreach_leaf,
) -> dict[str, Any]:
    source_occurrence_at = delivered_at or created_at or datetime.now()
    endpoint_revision = _outreach_endpoint_revision()
    cleaned_message = sanitize_text(str(message or "")).strip()
    if not cleaned_message:
        return {
            "status": "generation_error",
            "error_type": "contract_error",
            "forced": bool(forced),
        }
    process_identity = get_proactive_process_identity()
    research_payload = grounding.get("research")
    if isinstance(research_payload, dict):
        from core.proactive_research import (
            normalize_research_publication_text,
            validate_research_publication_text,
        )

        research_sources = list(research_payload.get("sources") or [])
        cleaned_message = normalize_research_publication_text(
            cleaned_message,
            research_sources,
        )
        publication_error = validate_research_publication_text(
            cleaned_message,
            research_sources,
        )
        if publication_error:
            return {
                "status": "generation_error",
                "error_type": publication_error,
                "forced": bool(forced),
            }

    with _session_scope(db) as session:
        try:
            runtime_at = lock_outbound_source_control(
                session,
                source_type=OUTREACH_SOURCE_TYPE,
                now=None,
            )
            existing = (
                session.query(ProactiveOutreachLog)
                .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
                .first()
            )
            reusable_cancelled = _cancelled_row_is_reusable(
                session,
                row=existing,
                user_id=user_id,
                generation_at=created_at or source_occurrence_at,
            )
            reusable_blocked = _blocked_outreach_without_outbox_is_current(
                session,
                existing,
            )
            if (
                existing is not None
                and existing.status not in {"pending", "candidate"}
                and not reusable_cancelled
                and not reusable_blocked
            ):
                if existing.status in {
                    "sending",
                    "queued",
                    "delivering",
                    "retry_wait",
                    "sent",
                    "sent_after_ambiguous_replay",
                    "failed",
                    "blocked",
                    "ambiguous",
                    "legacy_ambiguous_hold",
                }:
                    session.rollback()
                    return {
                        "status": "skipped_duplicate",
                        "log_id": existing.id,
                        "forced": bool(existing.forced),
                    }
                if existing.status == "cancelled":
                    clear_at = _history_clear_at(session, user_id)
                    status = (
                        "cancelled_history_clear"
                        if clear_at is not None
                        and (
                            existing.created_at is None
                            or existing.created_at <= clear_at
                        )
                        else "stale_schedule"
                    )
                    session.rollback()
                    return {
                        "status": status,
                        "log_id": existing.id,
                        "forced": bool(existing.forced),
                    }
                session.rollback()
                return {
                    "status": "state_error",
                    "error_type": "unknown_delivery_state",
                    "log_id": existing.id,
                    "forced": bool(existing.forced),
                }

            row = existing
            if row is None and schedule_row_id is not None:
                candidate = session.get(ProactiveOutreachLog, schedule_row_id)
                expected_schedule_key = (
                    schedule_expected_idempotency_key
                    if schedule_expected_idempotency_key is not None
                    else idempotency_key
                )
                if (
                    candidate is not None
                    and candidate.user_id == user_id
                    and candidate.status in {"pending", "candidate"}
                    and candidate.idempotency_key == expected_schedule_key
                ):
                    row = candidate
                else:
                    session.rollback()
                    return {
                        "status": "stale_schedule",
                        "log_id": schedule_row_id,
                        "forced": (
                            bool(candidate.forced)
                            if candidate is not None
                            else bool(forced)
                        ),
                    }
            claim_created_at = (
                created_at
                if created_at is not None
                else (
                    row.created_at
                    if row is not None
                    else source_occurrence_at
                )
            )
            if row is None:
                row = ProactiveOutreachLog(
                    user_id=user_id,
                    status="pending",
                    created_at=claim_created_at,
                )
                session.add(row)
                session.flush()

            row_id = int(row.id)
            expected_status = str(row.status or "")
            expected_key = row.idempotency_key
            expected_created_at = row.created_at
            expected_run_id = row.outbound_run_id
            claim_query = (
                session.query(ProactiveOutreachLog)
                .filter(
                    ProactiveOutreachLog.id == row_id,
                    ProactiveOutreachLog.user_id == user_id,
                    ProactiveOutreachLog.status == expected_status,
                )
            )
            claim_query = claim_query.filter(
                ProactiveOutreachLog.idempotency_key.is_(None)
                if expected_key is None
                else ProactiveOutreachLog.idempotency_key == expected_key
            )
            claim_query = claim_query.filter(
                ProactiveOutreachLog.created_at.is_(None)
                if expected_created_at is None
                else ProactiveOutreachLog.created_at == expected_created_at
            )
            claim_query = claim_query.filter(
                ProactiveOutreachLog.outbound_run_id.is_(None)
                if expected_run_id is None
                else ProactiveOutreachLog.outbound_run_id == expected_run_id
            )
            if claim_created_at is None:
                claim_query = claim_query.filter(
                    ~exists().where(
                        User.id == user_id,
                        User.history_clear_at.isnot(None),
                    )
                )
            else:
                claim_query = claim_query.filter(
                    ~exists().where(
                        User.id == user_id,
                        User.history_clear_at.isnot(None),
                        User.history_clear_at >= claim_created_at,
                    )
                )
            if evaluation_owner_token is not None:
                claim_query = claim_query.filter(
                    exists().where(
                        ProactiveOutreachLease.user_id == user_id,
                        ProactiveOutreachLease.owner_token
                        == evaluation_owner_token,
                        ProactiveOutreachLease.lease_expires_at > datetime.now(),
                    )
                )
            source_created_at = (
                delivered_at or claim_created_at or source_occurrence_at
            )
            outbound_occurrence_at = _outbound_occurrence_utc(source_created_at)
            claim_values = {
                ProactiveOutreachLog.idempotency_key: idempotency_key,
                ProactiveOutreachLog.grounding_json: _grounding_json(grounding),
                ProactiveOutreachLog.judge_should: judge_should,
                ProactiveOutreachLog.judge_reason: judge_reason,
                ProactiveOutreachLog.next_check_at: next_check_at,
                ProactiveOutreachLog.next_intent: next_intent,
                ProactiveOutreachLog.message: cleaned_message,
                ProactiveOutreachLog.forced: forced,
                ProactiveOutreachLog.created_at: source_created_at,
                ProactiveOutreachLog.status: (
                    "blocked" if reusable_blocked else "candidate"
                ),
                ProactiveOutreachLog.outbound_run_id: (
                    expected_run_id if reusable_blocked else None
                ),
            }
            claimed = claim_query.update(
                claim_values,
                synchronize_session=False,
            )
            if claimed != 1:
                session.rollback()
                current = session.get(ProactiveOutreachLog, row_id)
                clear_at = _history_clear_at(session, user_id)
                if clear_at is not None and (
                    claim_created_at is None or claim_created_at <= clear_at
                ):
                    if current is not None and current.status in {
                        "pending",
                        "candidate",
                    }:
                        current.status = "cancelled"
                        session.commit()
                    else:
                        session.rollback()
                    return {
                        "status": "cancelled_history_clear",
                        "log_id": row_id,
                        "forced": (
                            bool(current.forced)
                            if current is not None
                            else bool(forced)
                        ),
                    }
                if (
                    evaluation_owner_token is not None
                    and not evaluation_lease_checker(
                        session,
                        user_id=user_id,
                        owner_token=evaluation_owner_token,
                    )
                ):
                    session.rollback()
                    return {
                        "status": "lease_lost",
                        "log_id": row_id if current is not None else None,
                        "forced": (
                            bool(current.forced)
                            if current is not None
                            else bool(forced)
                        ),
                    }
                session.rollback()
                return {
                    "status": "skipped_duplicate",
                    "log_id": row_id,
                    "forced": bool(current.forced) if current is not None else bool(forced),
                }

            session.flush()
            session.expire_all()
            row = session.get(ProactiveOutreachLog, row_id)
            if row is None:
                raise RuntimeError("主动外呼 candidate CAS 后记录丢失")
            snapshot = proactive_outreach_source_snapshot(row)
            source_revision = proactive_outreach_source_revision(row)
            occurrence_key = proactive_outreach_occurrence_key(
                log_id=row_id,
                idempotency_key=idempotency_key,
                source_revision=source_revision,
            )
            destination_snapshot = {
                "target_type": "private",
                "target_id": user_id,
            }
            destination_fingerprint = (
                proactive_outreach_destination_fingerprint(user_id)
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
                    "log_id": row_id,
                },
            )
            claim = claim_run(
                session,
                source_type=OUTREACH_SOURCE_TYPE,
                source_id=str(row_id),
                occurrence_key=occurrence_key,
                source_revision=source_revision,
                source_snapshot=snapshot,
                destination_snapshot=destination_snapshot,
                target_type="private",
                task_kind="proactive_outreach_prepared",
                scheduled_for=outbound_occurrence_at,
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
                session.commit()
                run = session.get(OutboundRun, claim.run_id)
                outbox_id = (
                    int(run.active_outbox_id)
                    if run is not None and run.active_outbox_id is not None
                    else None
                )
                result = result_projector(
                    session,
                    log_id=row_id,
                    run_id=claim.run_id,
                    outbox_id=outbox_id,
                    forced=forced,
                    deduplicated=True,
                )
                session.rollback()
                return result
            queued = commit_outbox(
                session,
                run_id=claim.run_id,
                owner=claim.owner,
                claim_token=claim.claim_token,
                idempotency_key=(
                    proactive_outreach_delivery_key(
                        log_id=row_id,
                        idempotency_key=idempotency_key,
                        source_revision=source_revision,
                    )
                ),
                destination_snapshot=destination_snapshot,
                destination_fingerprint=destination_fingerprint,
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
                endpoint_config_revision=endpoint_revision,
                payload_contract_fingerprint=OUTREACH_PAYLOAD_CONTRACT,
                now=runtime_at,
            )
            if (
                evaluation_owner_token is not None
                and not evaluation_lease_checker(
                    session,
                    user_id=user_id,
                    owner_token=evaluation_owner_token,
                )
            ):
                session.rollback()
                return {
                    "status": "lease_lost",
                    "log_id": row_id,
                    "forced": bool(forced),
                }
            session.commit()
        except IntegrityError:
            session.rollback()
            current = (
                session.query(ProactiveOutreachLog)
                .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
                .first()
            )
            if current is None:
                session.rollback()
                raise
            result = {
                "status": "skipped_duplicate",
                "log_id": int(current.id),
                "forced": bool(current.forced),
            }
            session.rollback()
            return result
        except BaseException:
            session.rollback()
            raise

        if (
            claim.delivery_mode == "legacy_direct"
            and queued.outbox_id is not None
            and publisher is not None
        ):
            await legacy_deliverer(
                session=session,
                outbox_id=int(queued.outbox_id),
                publisher=publisher,
                endpoint_config_revision=endpoint_revision,
                now=None,
            )
        result = result_projector(
            session,
            log_id=row_id,
            run_id=queued.run_id,
            outbox_id=queued.outbox_id,
            forced=forced,
            deduplicated=not queued.created,
        )
        session.rollback()
        return result


async def deliver_outreach_once(
    *,
    user_id: str,
    idempotency_key: str,
    grounding: dict[str, Any],
    judge_should: bool,
    judge_reason: str,
    next_check_at: datetime | None,
    next_intent: str,
    message: str,
    forced: bool,
    db: Session | None = None,
    schedule_row_id: int | None = None,
    schedule_expected_idempotency_key: str | None = None,
    created_at: datetime | None = None,
    delivered_at: datetime | None = None,
    publisher: Callable[[str, str, str], Any] | None = None,
    evaluation_owner_token: str | None = None,
    sanitize_text: Callable[[str], str] = strip_think_blocks,
    result_projector: Callable[..., dict[str, Any]] = _outreach_result,
    claim_run: Callable[..., Any] = claim_outbound_run,
    commit_outbox: Callable[..., Any] = commit_prepared_outbox,
    evaluation_lease_checker: Callable[..., bool] = _evaluation_lease_is_owned,
    legacy_deliverer: Callable[..., Any] = _deliver_legacy_outreach_leaf,
) -> dict[str, Any]:
    """幂等持久化一条主动外呼候选，并按 cutover 模式转交 worker。"""

    return await _enqueue_outreach_outbox(
        user_id=user_id,
        idempotency_key=idempotency_key,
        grounding=grounding,
        judge_should=judge_should,
        judge_reason=judge_reason,
        next_check_at=next_check_at,
        next_intent=next_intent,
        message=message,
        forced=forced,
        db=db,
        schedule_row_id=schedule_row_id,
        schedule_expected_idempotency_key=schedule_expected_idempotency_key,
        created_at=created_at,
        delivered_at=delivered_at,
        publisher=publisher,
        evaluation_owner_token=evaluation_owner_token,
        sanitize_text=sanitize_text,
        result_projector=result_projector,
        claim_run=claim_run,
        commit_outbox=commit_outbox,
        evaluation_lease_checker=evaluation_lease_checker,
        legacy_deliverer=legacy_deliverer,
    )



__all__ = ["deliver_outreach_once"]
