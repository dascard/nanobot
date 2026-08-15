"""主动外呼单用户评估、恢复与租约编排。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.agent_runtime.contracts import RuntimePrincipal
from core.db.models.proactive import ProactiveOutreachLog
from core.model_provider.response_normalization import strip_think_blocks
from core.outbound.contracts import OutboundConflictError, OutboundSafetyError
from core.outbound.control import check_outbound_generation_gate
from core.outbound.policy import proactive_outreach_destination_fingerprint
from core.proactive.config import (
    DEFAULT_AMBIGUOUS_HOLD_MIN,
    DEFAULT_DAILY_DELIVERY_QUOTA,
    DEFAULT_MAX_CHECK_INTERVAL_MIN,
    DEFAULT_MAX_SILENCE_MIN,
    DEFAULT_MIN_INTERVAL_MIN,
    DEFAULT_REPEAT_TOPIC_COOLDOWN_MIN,
    DEFAULT_SENDING_AMBIGUITY_MINUTES,
    KNOWN_OUTREACH_STATES,
    OUTREACH_ENDPOINT_KEY,
    OUTREACH_PAYLOAD_CONTRACT,
    OUTREACH_SOURCE_TYPE,
)
from core.proactive.delivery import deliver_outreach_once
from core.proactive.evaluation_policy import (
    repeated_topic_guard as _repeated_topic_guard,
)
from core.proactive.generation import (
    _blocked_outreach_without_outbox_is_current,
    _generated_row_facts,
    _prepare_generated_outreach,
    _resume_generated_outreach,
    _run_generated_outreach,
)
from core.proactive.grounding import build_outreach_grounding
from core.proactive.identity_policy import outreach_key as _outreach_key
from core.proactive.lease import (
    acquire_evaluation_lease as _acquire_evaluation_lease,
    fence_evaluation_write as _fence_evaluation_write,
    release_evaluation_lease as _release_evaluation_lease,
)
from core.proactive.model_policy import parse_iso_datetime as _parse_iso_datetime
from core.proactive.model_service import generate_outreach_message, judge_outreach
from core.proactive.repository import (
    cancel_pre_clear_schedules as _cancel_pre_clear_schedules,
    current_schedule_row as _current_schedule_row,
    first_outreach_attempt as _first_outreach_attempt,
    history_clear_at as _history_clear_at,
    last_effective_outreach as _last_effective_outreach,
    latest_next_check_at as _latest_next_check_at,
    latest_outreach_row as _latest_outreach_row,
)
from core.proactive.runtime_support import (
    _outreach_business_day_utc_bounds,
    _outreach_local_naive,
    _outbound_occurrence_utc,
    _outreach_endpoint_revision,
    _outreach_session_factory,
    session_scope as _session_scope,
)
from core.proactive.schedule_repository import (
    pending_schedule_conflict_result as _pending_schedule_conflict_result,
    record_evaluation_error as _record_evaluation_error,
    upsert_pending_schedule as _upsert_pending_schedule,
)
from core.proactive.serialization import (
    iso_or_empty as _iso_or_empty,
    json_object as _json_object,
)
from core.proactive_candidate import evaluate_outreach_min_interval_gate
from core.trigger_runtime import (
    TriggerKind,
    TriggerRunContext,
    build_trigger_envelope,
    trigger_run_status,
)


logger = logging.getLogger("nanobot.proactive.orchestrator")

_DAILY_QUOTA_STATUSES = frozenset({
    "candidate",
    "sending",
    "queued",
    "delivering",
    "retry_wait",
    "sent",
    "sent_after_ambiguous_replay",
    "blocked",
    "ambiguous",
    "legacy_ambiguous_hold",
})


def _daily_quota_gate(
    session: Session,
    *,
    user_id: str,
    now: datetime,
    quota: int,
) -> dict[str, Any] | None:
    """在模型调用前按上海业务日限制新外呼候选数。"""

    normalized_quota = max(0, int(quota))
    if normalized_quota == 0:
        return {
            "status": "skipped_daily_quota",
            "daily_delivery_quota": 0,
            "daily_delivery_count": 0,
        }
    day_start_utc, day_end_utc = _outreach_business_day_utc_bounds(now)
    day_start = _outreach_local_naive(
        day_start_utc.replace(tzinfo=timezone.utc),
    )
    day_end = _outreach_local_naive(
        day_end_utc.replace(tzinfo=timezone.utc),
    )
    # proactive_outreach_log.created_at 沿用上海墙钟 naive；不能拿 UTC naive
    # 直接查询旧记录。业务日先形成明确 UTC 边界，再在既有存储域内计数。
    used = (
        session.query(ProactiveOutreachLog.id)
        .filter(
            ProactiveOutreachLog.user_id == user_id,
            ProactiveOutreachLog.created_at >= day_start,
            ProactiveOutreachLog.created_at < day_end,
            ProactiveOutreachLog.status.in_(_DAILY_QUOTA_STATUSES),
        )
        .count()
    )
    if used < normalized_quota:
        return None
    return {
        "status": "skipped_daily_quota",
        "daily_delivery_quota": normalized_quota,
        "daily_delivery_count": int(used),
        "next_check_at": day_end_utc.replace(tzinfo=timezone.utc).isoformat(),
    }


def _governed_generator(
    generator: Callable[..., str],
    trigger_runtime: TriggerRunContext | None,
) -> Callable[..., str]:
    if trigger_runtime is None:
        return generator

    def invoke(*args: Any, **kwargs: Any) -> str:
        # 默认 Generator 还会执行一次质量复核；两次调用在入口处一并预约，
        # 即使第一步失败也不会把剩余额度错误地交给同轮其它工作。
        trigger_runtime.reserve_model("proactive_generate")
        trigger_runtime.reserve_model("proactive_quality")
        return generator(*args, **kwargs)

    return invoke


def _governed_research(
    research: Callable[..., Any] | None,
    trigger_runtime: TriggerRunContext | None,
) -> Callable[..., Any] | None:
    if research is None or trigger_runtime is None:
        return research

    async def invoke(*args: Any, **kwargs: Any) -> Any:
        reservation = trigger_runtime.reserve_subagent("proactive_research")
        try:
            return await research(*args, **kwargs)
        finally:
            trigger_runtime.release(reservation)

    return invoke


async def _run_governed_delivery(
    operation: Callable[..., Any],
    *,
    trigger_runtime: TriggerRunContext | None,
    **kwargs: Any,
) -> dict[str, Any]:
    if trigger_runtime is None:
        return await operation(**kwargs)
    reservation = trigger_runtime.reserve_delivery(OUTREACH_ENDPOINT_KEY)
    try:
        result = await operation(**kwargs)
    finally:
        trigger_runtime.release(reservation)
    if str(result.get("status") or "") in {
        "queued",
        "sending",
        "sent",
        "sent_after_ambiguous_replay",
        "delivering",
        "retry_wait",
        "ambiguous",
    }:
        trigger_runtime.mark_delivery_committed(str(result.get("status") or ""))
    return result


async def _run_outreach_once_acquired(
    user_id: str,
    *,
    db: Session | None = None,
    now: datetime | None = None,
    min_interval_min: int = DEFAULT_MIN_INTERVAL_MIN,
    max_check_interval_min: int = DEFAULT_MAX_CHECK_INTERVAL_MIN,
    max_silence_min: int = DEFAULT_MAX_SILENCE_MIN,
    ambiguous_hold_min: int = DEFAULT_AMBIGUOUS_HOLD_MIN,
    repeat_topic_cooldown_min: int = DEFAULT_REPEAT_TOPIC_COOLDOWN_MIN,
    daily_delivery_quota: int = DEFAULT_DAILY_DELIVERY_QUOTA,
    judge_fn: Callable[..., dict[str, Any]] | None = None,
    generator_fn: Callable[..., str] | None = None,
    research_fn: Callable[..., Any] | None = None,
    thread_extractor: Callable[[list[dict[str, Any]]], list[Any]] | None = None,
    publisher: Callable[[str, str, str], Any] | None = None,
    evaluation_owner_token: str | None = None,
    evaluation_generation_at: datetime | None = None,
    trigger_runtime: TriggerRunContext | None = None,
    judge_service: Callable[..., dict[str, Any]] = judge_outreach,
    generator_service: Callable[..., str] = generate_outreach_message,
    grounding_builder: Callable[..., dict[str, Any]] = build_outreach_grounding,
    delivery_service: Callable[..., Any] = deliver_outreach_once,
    generated_runner: Callable[..., Any] = _run_generated_outreach,
    sanitize_text: Callable[[str], str] = strip_think_blocks,
) -> dict[str, Any]:
    """执行一次主动外呼检查；超过最长沉默窗口时强制重新评估。"""

    current = _outreach_local_naive(now)
    generation_at = evaluation_generation_at or current
    outbound_generation_at = _outbound_occurrence_utc(generation_at)
    with _session_scope(db) as session:
        _cancel_pre_clear_schedules(session, user_id)
        resumable_row = _current_schedule_row(session, user_id)
        if (
            resumable_row is not None
            and resumable_row.outbound_run_id is not None
            and resumable_row.status in {"candidate", "blocked"}
        ):
            try:
                generated_facts = _generated_row_facts(resumable_row)
            except OutboundConflictError as exc:
                session.rollback()
                return {
                    "status": "state_error",
                    "error_type": "generated_source_invalid",
                    "reason": str(exc)[:300],
                    "log_id": int(resumable_row.id),
                    "forced": bool(resumable_row.forced),
                }
            if generated_facts is not None:
                work, recovery_result = _resume_generated_outreach(
                    session,
                    row=resumable_row,
                    user_id=user_id,
                    evaluation_owner_token=evaluation_owner_token,
                )
                if recovery_result is not None:
                    return recovery_result
                if work is None:
                    raise RuntimeError("generated 恢复未返回工作或结果")
                effective_research_fn = research_fn
                if work.kind == "research" and effective_research_fn is None:
                    from core.proactive_research import run_proactive_research

                    effective_research_fn = run_proactive_research
                return await _run_governed_delivery(
                    generated_runner,
                    trigger_runtime=trigger_runtime,
                    session=session,
                    work=work,
                    user_id=user_id,
                    generator_fn=_governed_generator(
                        generator_fn or generator_service,
                        trigger_runtime,
                    ),
                    research_fn=_governed_research(
                        effective_research_fn,
                        trigger_runtime,
                    ),
                    publisher=publisher,
                    evaluation_owner_token=evaluation_owner_token,
                )
        try:
            generation_gate = check_outbound_generation_gate(
                session,
                source_type=OUTREACH_SOURCE_TYPE,
                occurrence_at=outbound_generation_at,
                endpoint_key=OUTREACH_ENDPOINT_KEY,
                destination_fingerprint=(
                    proactive_outreach_destination_fingerprint(
                        user_id
                    )
                ),
                endpoint_config_revision=_outreach_endpoint_revision(),
                payload_contract_fingerprint=OUTREACH_PAYLOAD_CONTRACT,
                now=None,
            )
        except OutboundSafetyError as exc:
            session.rollback()
            return {
                "status": "skipped_delivery_control",
                "error_type": "delivery_control_unavailable",
                "reason": str(exc)[:300],
                "forced": False,
            }
        session.rollback()
        if not generation_gate.allowed:
            return {
                "status": (
                    "skipped_circuit"
                    if generation_gate.reason_type == "circuit_open"
                    else "skipped_delivery_control"
                ),
                "error_type": generation_gate.reason_type,
                "reason": generation_gate.reason_summary,
                "forced": False,
            }
        _cancel_pre_clear_schedules(session, user_id)
        latest_row = _latest_outreach_row(session, user_id)
        latest_status = str(latest_row.status or "") if latest_row is not None else ""
        if latest_row is not None and latest_status not in KNOWN_OUTREACH_STATES:
            return {
                "status": "state_error",
                "error_type": "unknown_delivery_state",
                "delivery_state": latest_status,
                "log_id": latest_row.id,
                "forced": bool(latest_row.forced),
            }
        clear_at = _history_clear_at(session, user_id)
        if (
            latest_row is not None
            and latest_status == "ambiguous"
            and latest_row.created_at is not None
            and (clear_at is None or latest_row.created_at > clear_at)
        ):
            hold_until = latest_row.created_at + timedelta(
                minutes=max(0, int(ambiguous_hold_min))
            )
            if current < hold_until:
                return {
                    "status": "skipped_ambiguous",
                    "next_check_at": hold_until.isoformat(),
                    "log_id": latest_row.id,
                    "forced": bool(latest_row.forced),
                }
        schedule_row = _current_schedule_row(session, user_id)
        if (
            schedule_row is not None
            and bool(schedule_row.forced)
            and schedule_row.status in {"candidate", "blocked"}
        ):
            if schedule_row.outbound_run_id is not None:
                return {
                    "status": "state_error",
                    "error_type": "forced_delivery_disabled",
                    "reason": "遗留强制候选绑定了无法安全回收的 outbound run",
                    "log_id": int(schedule_row.id),
                    "forced": True,
                }
            if not _fence_evaluation_write(
                session,
                user_id=user_id,
                owner_token=evaluation_owner_token,
            ):
                session.rollback()
                return {
                    "status": "lease_lost",
                    "log_id": int(schedule_row.id),
                    "forced": True,
                }
            retired = (
                session.query(ProactiveOutreachLog)
                .filter(
                    ProactiveOutreachLog.id == schedule_row.id,
                    ProactiveOutreachLog.user_id == user_id,
                    ProactiveOutreachLog.status == schedule_row.status,
                    ProactiveOutreachLog.forced.is_(True),
                    ProactiveOutreachLog.outbound_run_id.is_(None),
                )
                .update(
                    {ProactiveOutreachLog.status: "cancelled"},
                    synchronize_session=False,
                )
            )
            if retired != 1:
                session.rollback()
                return {
                    "status": "state_error",
                    "error_type": "forced_delivery_disabled",
                    "reason": "遗留强制候选状态已变化",
                    "log_id": int(schedule_row.id),
                    "forced": True,
                }
            session.commit()
            schedule_row = None
        if _blocked_outreach_without_outbox_is_current(session, schedule_row):
            candidate_message = sanitize_text(
                schedule_row.message or ""
            ).strip()
            if not candidate_message:
                return {
                    "status": "state_error",
                    "error_type": "blocked_candidate_empty",
                    "log_id": schedule_row.id,
                    "forced": bool(schedule_row.forced),
                }
            return await _run_governed_delivery(
                delivery_service,
                trigger_runtime=trigger_runtime,
                user_id=user_id,
                idempotency_key=str(schedule_row.idempotency_key or ""),
                grounding=_json_object(schedule_row.grounding_json),
                judge_should=bool(schedule_row.judge_should),
                judge_reason=schedule_row.judge_reason or "",
                next_check_at=schedule_row.next_check_at,
                next_intent=schedule_row.next_intent or "",
                message=candidate_message,
                forced=bool(schedule_row.forced),
                db=session,
                schedule_row_id=schedule_row.id,
                schedule_expected_idempotency_key=(
                    schedule_row.idempotency_key
                ),
                created_at=schedule_row.created_at,
                publisher=publisher,
                evaluation_owner_token=evaluation_owner_token,
            )
        if schedule_row is not None and schedule_row.status in {
            "queued",
            "delivering",
            "retry_wait",
            "blocked",
        }:
            return {
                "status": "skipped_duplicate",
                "log_id": schedule_row.id,
                "forced": bool(schedule_row.forced),
            }
        if (
            schedule_row is not None
            and schedule_row.status == "sending"
            and schedule_row.outbound_run_id is None
        ):
            stale_cutoff = current - timedelta(
                minutes=DEFAULT_SENDING_AMBIGUITY_MINUTES
            )
            if (
                schedule_row.created_at is None
                or schedule_row.created_at > stale_cutoff
            ):
                return {
                    "status": "skipped_duplicate",
                    "log_id": schedule_row.id,
                    "forced": bool(schedule_row.forced),
                }
            if not _fence_evaluation_write(
                session,
                user_id=user_id,
                owner_token=evaluation_owner_token,
            ):
                session.rollback()
                return {
                    "status": "lease_lost",
                    "log_id": schedule_row.id,
                    "forced": bool(schedule_row.forced),
                }
            retired = (
                session.query(ProactiveOutreachLog)
                .filter(ProactiveOutreachLog.id == schedule_row.id)
                .filter(ProactiveOutreachLog.user_id == user_id)
                .filter(ProactiveOutreachLog.status == "sending")
                .filter(ProactiveOutreachLog.created_at == schedule_row.created_at)
                .update(
                    {ProactiveOutreachLog.status: "ambiguous"},
                    synchronize_session=False,
                )
            )
            if retired != 1:
                session.rollback()
                return {
                    "status": "skipped_duplicate",
                    "log_id": schedule_row.id,
                    "forced": bool(schedule_row.forced),
                }
            session.commit()
            # 刚改判的 ambiguous 行必须立即执行与入口处一致的冷静期
            # 判定:消息可能已实际送达,同轮继续评估会在 hold 期内再次
            # 外呼,与下一轮的 skipped_ambiguous 行为自相矛盾。
            if (
                schedule_row.created_at is not None
                and (clear_at is None or schedule_row.created_at > clear_at)
            ):
                hold_until = schedule_row.created_at + timedelta(
                    minutes=max(0, int(ambiguous_hold_min))
                )
                if current < hold_until:
                    return {
                        "status": "skipped_ambiguous",
                        "next_check_at": hold_until.isoformat(),
                        "log_id": schedule_row.id,
                        "forced": bool(schedule_row.forced),
                    }
            schedule_row = None
        last_effective = _last_effective_outreach(session, user_id)
        first_attempt = _first_outreach_attempt(session, user_id)
        interval_decision = evaluate_outreach_min_interval_gate(
            now=current,
            last_effective_at=last_effective.created_at
            if last_effective is not None
            else None,
            first_attempt_at=first_attempt.created_at
            if first_attempt is not None
            else None,
            min_interval_min=min_interval_min,
            max_silence_min=max_silence_min,
        )
        force_evaluation = bool(interval_decision["forced"])
        if interval_decision["status"] == "skipped_min_interval":
            return {
                "status": "skipped_min_interval",
                "minutes_since_last": int(interval_decision["minutes_since_last"]),
            }

        if schedule_row is not None and schedule_row.status == "candidate":
            candidate_message = sanitize_text(schedule_row.message or "").strip()
            if candidate_message:
                return await _run_governed_delivery(
                    delivery_service,
                    trigger_runtime=trigger_runtime,
                    user_id=user_id,
                    idempotency_key=schedule_row.idempotency_key,
                    grounding=_json_object(schedule_row.grounding_json),
                    judge_should=True,
                    judge_reason=schedule_row.judge_reason or "",
                    next_check_at=schedule_row.next_check_at,
                    next_intent=schedule_row.next_intent or "",
                    message=candidate_message,
                    forced=False,
                    db=session,
                    schedule_row_id=schedule_row.id,
                    created_at=schedule_row.created_at or current,
                    delivered_at=current,
                    publisher=publisher,
                    evaluation_owner_token=evaluation_owner_token,
                )
            schedule_row.status = "cancelled"
            session.commit()
            schedule_row = None
        quota_decision = _daily_quota_gate(
            session,
            user_id=user_id,
            now=current,
            quota=daily_delivery_quota,
        )
        if quota_decision is not None:
            return quota_decision
        latest_next_check_at = _latest_next_check_at(session, user_id)
        schedule_anchor = (
            schedule_row.next_check_at
            if schedule_row is not None and schedule_row.next_check_at is not None
            else latest_next_check_at
        ) or current
        grounding_kwargs: dict[str, Any] = {"db": session, "now": current}
        if thread_extractor is not None:
            grounding_kwargs["thread_extractor"] = thread_extractor
        # Grounding 会调用同步模型路由提炼近期话题；必须整体移出事件循环，
        # 否则单个候选超时会连带阻塞健康检查和其它 HTTP 请求。
        grounding = await asyncio.to_thread(
            grounding_builder,
            user_id,
            **grounding_kwargs,
        )
        persistence_grounding = grounding
        if trigger_runtime is not None:
            persistence_grounding = dict(grounding)
            persistence_grounding["_trigger_runtime"] = (
                trigger_runtime.envelope.safe_snapshot(
                    run_id=trigger_runtime.run_id,
                )
            )
        if force_evaluation:
            grounding = dict(grounding)
            grounding["trigger"] = {
                "kind": "max_silence_evaluation",
                "requires_delivery": False,
                "max_silence_min": max(1, int(max_silence_min)),
            }
            persistence_grounding = dict(persistence_grounding)
            persistence_grounding["trigger"] = dict(grounding["trigger"])

        from core.proactive_candidate import evaluate_outreach_judgement
        from core.proactive_research import run_proactive_research

        idempotency_key = _outreach_key(user_id, schedule_anchor, forced=False)
        if trigger_runtime is not None:
            trigger_runtime.reserve_model("proactive_judge")
        evaluation = await asyncio.to_thread(
            evaluate_outreach_judgement,
            grounding=grounding,
            now=current,
            judge_fn=judge_fn or judge_service,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
        )
        judge = dict(evaluation.get("judge") or {})
        next_check_at = _parse_iso_datetime(judge.get("next_check_at"))
        status = str(evaluation.get("status") or "")
        if status == "judge_error":
            error_type = str(
                evaluation.get("error_type") or "contract_error"
            )
            error_reason = str(evaluation.get("reason") or "")[:500]
            row = _record_evaluation_error(
                session,
                user_id=user_id,
                idempotency_key=idempotency_key,
                phase="judge",
                grounding=persistence_grounding,
                error_type=error_type,
                reason=error_reason,
                next_check_at=next_check_at,
                next_intent=str(judge.get("next_intent") or ""),
                created_at=generation_at,
                forced=False,
                evaluation_owner_token=evaluation_owner_token,
            )
            if row is None:
                return {
                    "status": "lease_lost",
                    "log_id": None,
                    "forced": False,
                }
            return {
                "status": "judge_error",
                "error_type": error_type,
                "reason": error_reason,
                "next_check_at": _iso_or_empty(next_check_at),
                "log_id": row.id,
                "forced": False,
            }
        if status == "no_candidate":
            row = _upsert_pending_schedule(
                session,
                user_id=user_id,
                idempotency_key=_outreach_key(user_id, next_check_at or schedule_anchor, forced=False),
                grounding=persistence_grounding,
                judge_reason=str(judge.get("reason") or ""),
                next_check_at=next_check_at,
                next_intent=str(judge.get("next_intent") or ""),
                created_at=generation_at,
                evaluation_owner_token=evaluation_owner_token,
            )
            if row is None:
                return {
                    "status": "lease_lost",
                    "log_id": None,
                    "forced": False,
                }
            if row.status != "pending":
                return _pending_schedule_conflict_result(
                    session,
                    row=row,
                    user_id=user_id,
                )
            return {"status": "pending", "forced": False, "log_id": row.id}

        if status != "generation_required":
            error_reason = f"未知候选状态: {status}"[:500]
            row = _record_evaluation_error(
                session,
                user_id=user_id,
                idempotency_key=idempotency_key,
                phase="candidate_contract",
                grounding=persistence_grounding,
                error_type="contract_error",
                reason=error_reason,
                next_check_at=next_check_at,
                next_intent=str(judge.get("next_intent") or ""),
                created_at=generation_at,
                forced=False,
                evaluation_owner_token=evaluation_owner_token,
            )
            if row is None:
                return {
                    "status": "lease_lost",
                    "log_id": None,
                    "forced": False,
                }
            return {
                "status": "judge_error",
                "error_type": "contract_error",
                "reason": error_reason,
                "next_check_at": _iso_or_empty(next_check_at),
                "log_id": row.id,
                "forced": False,
            }
        repeated_topic = _repeated_topic_guard(
            session,
            user_id=user_id,
            grounding=grounding,
            judge=judge,
            now=current,
            cooldown_min=max(0, int(repeat_topic_cooldown_min)),
        )
        if repeated_topic is not None:
            cooldown_until = repeated_topic["cooldown_until"]
            deferred_next_check = max(
                next_check_at or current,
                cooldown_until,
            )
            reason_code = str(repeated_topic["reason_code"])
            row = _upsert_pending_schedule(
                session,
                user_id=user_id,
                idempotency_key=_outreach_key(
                    user_id,
                    deferred_next_check,
                    forced=False,
                ),
                grounding=persistence_grounding,
                judge_reason=f"重复话题门禁:{reason_code}",
                next_check_at=deferred_next_check,
                next_intent=str(judge.get("next_intent") or ""),
                created_at=generation_at,
                evaluation_owner_token=evaluation_owner_token,
            )
            if row is None:
                return {
                    "status": "lease_lost",
                    "log_id": None,
                    "forced": False,
                }
            if row.status != "pending":
                return _pending_schedule_conflict_result(
                    session,
                    row=row,
                    user_id=user_id,
                )
            return {
                "status": "skipped_repeated_topic",
                "reason_code": reason_code,
                "matched_log_id": int(repeated_topic["matched_log_id"]),
                "next_check_at": deferred_next_check.isoformat(),
                "log_id": int(row.id),
                "forced": False,
            }
        kind = (
            "research"
            if str(judge.get("outreach_kind") or "message") == "research"
            else "message"
        )
        work, preparation_result = _prepare_generated_outreach(
            session,
            user_id=user_id,
            idempotency_key=idempotency_key,
            grounding=persistence_grounding,
            judge=judge,
            kind=kind,
            next_check_at=next_check_at,
            next_intent=str(judge.get("next_intent") or ""),
            created_at=generation_at,
            schedule_row_id=(
                int(schedule_row.id)
                if schedule_row is not None and schedule_row.status == "pending"
                else None
            ),
            evaluation_owner_token=evaluation_owner_token,
        )
        if preparation_result is not None:
            return preparation_result
        if work is None:
            raise RuntimeError("generated 准备未返回工作或结果")
        return await _run_governed_delivery(
            generated_runner,
            trigger_runtime=trigger_runtime,
            session=session,
            work=work,
            user_id=user_id,
            generator_fn=_governed_generator(
                generator_fn or generator_service,
                trigger_runtime,
            ),
            research_fn=_governed_research(
                (
                    research_fn or run_proactive_research
                    if kind == "research"
                    else None
                ),
                trigger_runtime,
            ),
            publisher=publisher,
            evaluation_owner_token=evaluation_owner_token,
        )


async def run_outreach_once(
    user_id: str,
    *,
    db: Session | None = None,
    now: datetime | None = None,
    min_interval_min: int = DEFAULT_MIN_INTERVAL_MIN,
    max_check_interval_min: int = DEFAULT_MAX_CHECK_INTERVAL_MIN,
    max_silence_min: int = DEFAULT_MAX_SILENCE_MIN,
    ambiguous_hold_min: int = DEFAULT_AMBIGUOUS_HOLD_MIN,
    repeat_topic_cooldown_min: int = DEFAULT_REPEAT_TOPIC_COOLDOWN_MIN,
    daily_delivery_quota: int = DEFAULT_DAILY_DELIVERY_QUOTA,
    judge_fn: Callable[..., dict[str, Any]] | None = None,
    generator_fn: Callable[..., str] | None = None,
    research_fn: Callable[..., Any] | None = None,
    thread_extractor: Callable[[list[dict[str, Any]]], list[Any]] | None = None,
    publisher: Callable[[str, str, str], Any] | None = None,
    trigger_kind: TriggerKind | str = TriggerKind.EVENT,
    trigger_source_ref: str = "",
    trigger_idempotency_key: str = "",
    trigger_occurred_at: datetime | None = None,
    trigger_evaluated_status: str = "allowed",
    acquired_runner: Callable[..., Any] = _run_outreach_once_acquired,
    acquire_lease: Callable[..., str | None] = _acquire_evaluation_lease,
    release_lease: Callable[..., Any] = _release_evaluation_lease,
) -> dict[str, Any]:
    """获取按用户评估租约后执行一次主动外呼检查。"""

    current = _outreach_local_naive(now)
    occurred_at = trigger_occurred_at or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    source_ref = str(trigger_source_ref or "").strip() or (
        f"proactive:{user_id}:{current.isoformat()}"
    )
    trigger_key = str(trigger_idempotency_key or "").strip() or source_ref
    trigger_envelope = build_trigger_envelope(
        kind=TriggerKind(trigger_kind),
        source_type=OUTREACH_SOURCE_TYPE,
        source_ref=source_ref,
        idempotency_key=trigger_key,
        principal=RuntimePrincipal("qq", "user", user_id),
        delivery_endpoints=(OUTREACH_ENDPOINT_KEY,),
        allowed_subagents=("proactive_research",),
        max_model_calls=3,
        max_steps=8,
        timeout_seconds=180,
        max_subagents=1,
        occurred_at=occurred_at,
        ttl_seconds=180,
    )
    with _session_scope(db) as session:
        lease_acquired_at = datetime.now()
        owner_token = acquire_lease(
            session,
            user_id=user_id,
            now=lease_acquired_at,
        )
        if owner_token is None:
            return {"status": "skipped_in_progress", "forced": False}
        clear_at = _history_clear_at(session, user_id)
        evaluation_generation_at = current
        if clear_at is not None and evaluation_generation_at <= clear_at:
            evaluation_generation_at = max(
                lease_acquired_at,
                clear_at + timedelta(microseconds=1),
            )
        trigger_runtime: TriggerRunContext | None = None
        result: dict[str, Any] | None = None
        failure: BaseException | None = None
        try:
            trigger_runtime = await TriggerRunContext.start(
                trigger_envelope,
                session_factory=_outreach_session_factory(session),
                evaluated_status=trigger_evaluated_status,
            )
            business_result = await acquired_runner(
                user_id,
                db=session,
                now=current,
                min_interval_min=min_interval_min,
                max_check_interval_min=max_check_interval_min,
                max_silence_min=max_silence_min,
                ambiguous_hold_min=ambiguous_hold_min,
                repeat_topic_cooldown_min=repeat_topic_cooldown_min,
                daily_delivery_quota=daily_delivery_quota,
                judge_fn=judge_fn,
                generator_fn=generator_fn,
                research_fn=research_fn,
                thread_extractor=thread_extractor,
                publisher=publisher,
                evaluation_owner_token=owner_token,
                evaluation_generation_at=evaluation_generation_at,
                trigger_runtime=trigger_runtime,
            )
            result = dict(business_result or {})
            return result
        except BaseException as exc:
            failure = exc
            session.rollback()
            raise
        finally:
            try:
                release_lease(
                    session,
                    user_id=user_id,
                    owner_token=owner_token,
                )
            except Exception:
                logger.exception(
                    "释放主动外呼评估租约失败，等待租约 TTL 自动失效: user_id=%s",
                    user_id,
                )
            if trigger_runtime is not None:
                try:
                    await trigger_runtime.finish(
                        status=trigger_run_status(result, failure),
                        output=result,
                        error=failure,
                    )
                except Exception:
                    if failure is None:
                        raise
                    logger.exception(
                        "主动外呼 Trigger Run 终结失败，保留原业务异常: user_id=%s",
                        user_id,
                    )



__all__ = ["run_outreach_once"]
