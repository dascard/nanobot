"""主动情感外呼运行时基础能力。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.async_bridge import run_awaitable_sync
from core import outbound_delivery
from core.model_provider import ModelProviderResponse
from core.model_provider.response_normalization import strip_think_blocks
from core.proactive_diagnostics import OutreachModelContractError
from core.proactive.config import (
    DEFAULT_AMBIGUOUS_HOLD_MIN,
    DEFAULT_DAILY_DELIVERY_QUOTA,
    DEFAULT_MAX_CHECK_INTERVAL_MIN,
    DEFAULT_MAX_SILENCE_MIN,
    DEFAULT_MIN_INTERVAL_MIN,
    DEFAULT_REPEAT_TOPIC_COOLDOWN_MIN,
    DEFAULT_SURGE_MAX_PROB,
    DEFAULT_SURGE_MIN_PROB,
)
from core.proactive.delivery import deliver_outreach_once as _deliver_outreach_once_impl
from core.proactive.delivery_runtime import (
    _deliver_legacy_outreach_leaf,
    drain_due_legacy_proactive_outboxes as drain_due_legacy_proactive_outboxes,
)
from core.proactive.evaluation_policy import (
    repeated_topic_guard as _repeated_topic_guard,
)
from core.proactive.generation import (
    _outreach_result,
    _run_generated_outreach as _run_generated_outreach_impl,
)
from core.proactive.grounding import (
    active_hours,
    build_outreach_grounding,
    extract_recent_threads,
)
from core.proactive.identity_policy import outreach_key as _outreach_key
from core.proactive.lease import (
    acquire_evaluation_lease as _acquire_evaluation_lease,
    evaluation_lease_is_owned as _evaluation_lease_is_owned,
    release_evaluation_lease as _release_evaluation_lease,
)
from core.proactive.model_service import (
    generate_outreach_message as generate_outreach_message,
    judge_outreach as judge_outreach,
    review_outreach_message as review_outreach_message,
)
from core.proactive.orchestrator import (
    _run_outreach_once_acquired as _run_outreach_once_acquired_impl,
    run_outreach_once as _run_outreach_once_impl,
)
from core.proactive.simulation import (
    run_outreach_dry_run_once as _run_outreach_dry_run_once_impl,
)
from core.proactive.scheduling_service import (
    run_outreach_due_once as _run_outreach_due_once_impl,
)
from core.proactive.scheduler import (
    proactive_outreach_scheduler as _proactive_outreach_scheduler_impl,
)
from core.proactive.scheduling_policy import surge_probability as _surge_probability
from core.proactive.schedule_repository import (
    upsert_pending_schedule as _upsert_pending_schedule,
)
from core.identity import get_super_user_ids
from core.settings_service import settings
from core.trigger_runtime import TriggerKind, TriggerRunContext

# 兼容现有调用方显式注入旧三态 publisher；生产默认保持为空并走结构化 transport。
push_to_qq: Callable[[str, str, str], Any] | None = None
ModelRouteResponse = ModelProviderResponse

__all__ = [
    "DEFAULT_SURGE_MAX_PROB",
    "DEFAULT_SURGE_MIN_PROB",
    "ModelRouteResponse",
    "OutreachModelContractError",
    "_acquire_evaluation_lease",
    "_deliver_legacy_outreach_leaf",
    "_outreach_key",
    "_outreach_result",
    "_release_evaluation_lease",
    "_repeated_topic_guard",
    "_surge_probability",
    "_upsert_pending_schedule",
    "active_hours",
    "build_outreach_grounding",
    "deliver_outreach_once",
    "drain_due_legacy_proactive_outboxes",
    "extract_recent_threads",
    "generate_outreach_message",
    "judge_outreach",
    "review_outreach_message",
    "proactive_outreach_scheduler",
    "push_to_qq",
    "run_outreach_dry_run_once",
    "run_outreach_due_once",
    "run_outreach_once",
    "settings",
    "strip_think_blocks",
]


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
) -> dict[str, Any]:
    """兼容入口：把可替换依赖显式传入模块化投递服务。"""

    return await _deliver_outreach_once_impl(
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
        sanitize_text=strip_think_blocks,
        result_projector=_outreach_result,
        claim_run=outbound_delivery.claim_outbound_run,
        commit_outbox=outbound_delivery.commit_prepared_outbox,
        evaluation_lease_checker=_evaluation_lease_is_owned,
        legacy_deliverer=_deliver_legacy_outreach_leaf,
    )


async def _run_generated_outreach(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """兼容注入点：把旧 façade 上可替换的落账能力显式传入生成服务。"""

    kwargs.setdefault("commit_outbox", outbound_delivery.commit_generated_outbox)
    kwargs.setdefault("result_projector", _outreach_result)
    kwargs.setdefault("legacy_deliverer", _deliver_legacy_outreach_leaf)
    return await _run_generated_outreach_impl(*args, **kwargs)


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
) -> dict[str, Any]:
    return await _run_outreach_once_acquired_impl(
        user_id,
        db=db,
        now=now,
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
        evaluation_owner_token=evaluation_owner_token,
        evaluation_generation_at=evaluation_generation_at,
        trigger_runtime=trigger_runtime,
        judge_service=judge_outreach,
        generator_service=generate_outreach_message,
        grounding_builder=build_outreach_grounding,
        delivery_service=deliver_outreach_once,
        generated_runner=_run_generated_outreach,
        sanitize_text=strip_think_blocks,
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
) -> dict[str, Any]:
    return await _run_outreach_once_impl(
        user_id,
        db=db,
        now=now,
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
        trigger_kind=trigger_kind,
        trigger_source_ref=trigger_source_ref,
        trigger_idempotency_key=trigger_idempotency_key,
        trigger_occurred_at=trigger_occurred_at,
        trigger_evaluated_status=trigger_evaluated_status,
        acquired_runner=_run_outreach_once_acquired,
        acquire_lease=_acquire_evaluation_lease,
        release_lease=_release_evaluation_lease,
    )


async def run_outreach_dry_run_once(
    user_id: str,
    *,
    db: Session | None = None,
    now: datetime | None = None,
    min_interval_min: int = DEFAULT_MIN_INTERVAL_MIN,
    max_check_interval_min: int = DEFAULT_MAX_CHECK_INTERVAL_MIN,
    judge_fn: Callable[..., dict[str, Any]] | None = None,
    generator_fn: Callable[..., str] | None = None,
    research_fn: Callable[..., Any] | None = None,
    thread_extractor: Callable[[list[dict[str, Any]]], list[Any]] | None = None,
) -> dict[str, Any]:
    return await _run_outreach_dry_run_once_impl(
        user_id,
        db=db,
        now=now,
        min_interval_min=min_interval_min,
        max_check_interval_min=max_check_interval_min,
        judge_fn=judge_fn,
        generator_fn=generator_fn,
        research_fn=research_fn,
        thread_extractor=thread_extractor,
        grounding_builder=build_outreach_grounding,
        judge_service=judge_outreach,
        generator_service=generate_outreach_message,
    )

async def run_outreach_due_once(
    user_id: str,
    *,
    db: Session | None = None,
    now: datetime | None = None,
    min_interval_min: int | None = None,
    max_check_interval_min: int | None = None,
    max_silence_min: int | None = None,
    ambiguous_hold_min: int | None = None,
    repeat_topic_cooldown_min: int | None = None,
    daily_delivery_quota: int | None = None,
    allow_early_surge: bool = False,
    surge_min_prob: float | None = None,
    surge_max_prob: float | None = None,
    random_fn: Callable[[], float] | None = None,
    publisher: Callable[[str, str, str], Any] | None = None,
    trigger_kind: TriggerKind | str = TriggerKind.HEARTBEAT,
) -> dict[str, Any]:
    return await _run_outreach_due_once_impl(
        user_id,
        db=db,
        now=now,
        min_interval_min=min_interval_min,
        max_check_interval_min=max_check_interval_min,
        max_silence_min=max_silence_min,
        ambiguous_hold_min=ambiguous_hold_min,
        repeat_topic_cooldown_min=repeat_topic_cooldown_min,
        daily_delivery_quota=daily_delivery_quota,
        allow_early_surge=allow_early_surge,
        surge_min_prob=surge_min_prob,
        surge_max_prob=surge_max_prob,
        random_fn=random_fn,
        publisher=publisher,
        trigger_kind=trigger_kind,
        outreach_runner=run_outreach_once,
    )


def proactive_outreach_scheduler(stop_event: threading.Event) -> None:
    return _proactive_outreach_scheduler_impl(
        stop_event,
        due_runner=run_outreach_due_once,
        settings_port=settings,
        super_user_provider=get_super_user_ids,
        awaitable_runner=run_awaitable_sync,
        monotonic=time.monotonic,
    )
