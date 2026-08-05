"""主动外呼后台 scheduler 显式循环。"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from core.async_bridge import run_awaitable_sync
from core.identity import get_super_user_ids
from core.proactive.config import (
    DEFAULT_AMBIGUOUS_HOLD_MIN,
    DEFAULT_DAILY_DELIVERY_QUOTA,
    DEFAULT_MAX_CHECK_INTERVAL_MIN,
    DEFAULT_MAX_SILENCE_MIN,
    DEFAULT_MIN_INTERVAL_MIN,
    DEFAULT_REPEAT_TOPIC_COOLDOWN_MIN,
    DEFAULT_SURGE_MAX_PROB,
    DEFAULT_SURGE_MIN_PROB,
    OUTREACH_SCHEDULER_POLL_SECONDS,
)
from core.proactive.scheduling_service import run_outreach_due_once
from core.settings_service import settings


logger = logging.getLogger("nanobot.proactive.scheduler")


def outreach_due_threshold_kwargs(settings_port: Any = settings) -> dict[str, Any]:
    """从托管设置组装 run_outreach_due_once 的阈值参数。

    调度器与管理端 run-once 必须共用同一组装,否则 run-once 会退回代码
    默认值,与线上调度行为不一致。
    """
    return {
        "min_interval_min": settings_port.get_int(
            "proactive_outreach.min_interval_min",
            DEFAULT_MIN_INTERVAL_MIN,
        ),
        "max_check_interval_min": settings_port.get_int(
            "proactive_outreach.max_check_interval_min",
            DEFAULT_MAX_CHECK_INTERVAL_MIN,
        ),
        "max_silence_min": settings_port.get_int(
            "proactive_outreach.max_silence_min",
            DEFAULT_MAX_SILENCE_MIN,
        ),
        "ambiguous_hold_min": settings_port.get_int(
            "proactive_outreach.ambiguous_hold_min",
            DEFAULT_AMBIGUOUS_HOLD_MIN,
        ),
        "repeat_topic_cooldown_min": settings_port.get_int(
            "proactive_outreach.repeat_topic_cooldown_min",
            DEFAULT_REPEAT_TOPIC_COOLDOWN_MIN,
        ),
        "daily_delivery_quota": settings_port.get_int(
            "proactive_outreach.daily_delivery_quota",
            DEFAULT_DAILY_DELIVERY_QUOTA,
        ),
        "allow_early_surge": settings_port.get_bool(
            "proactive_outreach.allow_early_surge",
            False,
        ),
        "surge_min_prob": settings_port.get_float(
            "proactive_outreach.surge_min_prob",
            DEFAULT_SURGE_MIN_PROB,
        ),
        "surge_max_prob": settings_port.get_float(
            "proactive_outreach.surge_max_prob",
            DEFAULT_SURGE_MAX_PROB,
        ),
    }


_CHECK_THRESHOLD_KEYS = (
    "min_interval_min",
    "max_check_interval_min",
    "max_silence_min",
    "ambiguous_hold_min",
    "repeat_topic_cooldown_min",
    "daily_delivery_quota",
)


def outreach_check_threshold_kwargs(settings_port: Any = settings) -> dict[str, Any]:
    """run_outreach_once(check 模式)可接受的托管阈值子集。"""
    due_kwargs = outreach_due_threshold_kwargs(settings_port)
    return {key: due_kwargs[key] for key in _CHECK_THRESHOLD_KEYS}


def proactive_outreach_scheduler(
    stop_event: threading.Event,
    *,
    due_runner: Callable[..., Any] = run_outreach_due_once,
    settings_port: Any = settings,
    super_user_provider: Callable[[], set[str]] = get_super_user_ids,
    awaitable_runner: Callable[[Any], Any] = run_awaitable_sync,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """后台主动外呼调度器。"""

    logger.info("Proactive outreach scheduler started.")
    next_evaluation_at = 0.0
    while not stop_event.is_set():
        enabled = False
        try:
            enabled = settings_port.get_bool("proactive_outreach.enabled", False)
            monotonic_now = monotonic()
            if not enabled:
                next_evaluation_at = 0.0
            elif monotonic_now >= next_evaluation_at:
                interval_min = settings_port.get_int(
                    "proactive_outreach.fallback_interval_min",
                    120,
                )
                next_evaluation_at = monotonic_now + max(
                    60.0,
                    float(interval_min) * 60.0,
                )
                user_ids = sorted(super_user_provider())
                if not user_ids:
                    logger.info(
                        "Proactive outreach skipped: no superuser configured."
                    )
                for user_id in user_ids:
                    if stop_event.is_set():
                        break
                    result = awaitable_runner(due_runner(
                        user_id,
                        **outreach_due_threshold_kwargs(settings_port),
                    ))
                    status = str((result or {}).get("status") or "")
                    if status in {
                        "judge_error",
                        "generation_error",
                        "research_blocked",
                        "ambiguous",
                        "lease_lost",
                        "state_error",
                    }:
                        logger.warning(
                            "Proactive outreach evaluation did not produce a normal "
                            "schedule: user_id=%s status=%s error_type=%s "
                            "reason_code=%s log_id=%s",
                            user_id,
                            status,
                            str((result or {}).get("error_type") or ""),
                            str((result or {}).get("reason_code") or ""),
                            (result or {}).get("log_id"),
                        )
        except Exception as exc:
            logger.exception("Proactive outreach scheduler error: %s", exc)

        wait_seconds = OUTREACH_SCHEDULER_POLL_SECONDS
        if enabled and next_evaluation_at > 0:
            wait_seconds = min(
                wait_seconds,
                max(0.1, next_evaluation_at - monotonic()),
            )
        stop_event.wait(timeout=wait_seconds)

    logger.info("Proactive outreach scheduler stopped.")

__all__ = ["proactive_outreach_scheduler"]
