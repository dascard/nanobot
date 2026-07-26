"""后台调度器启动与停止。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from collections.abc import Callable


@dataclass
class ThreadHandle:
    thread: threading.Thread
    stop_event: threading.Event
    stop_timeout: float = 5.0

    def stop(self, timeout: float | None = None) -> None:
        self.stop_event.set()
        self.thread.join(
            timeout=self.stop_timeout if timeout is None else float(timeout)
        )


@dataclass
class SchedulerHandles:
    digest: ThreadHandle | None = None
    scheduled_tasks: ThreadHandle | None = None
    session_summary: ThreadHandle | None = None
    eval_sampling: ThreadHandle | None = None
    reply_eval: ThreadHandle | None = None
    proactive_outreach: ThreadHandle | None = None
    chat_delivery: ThreadHandle | None = None
    group_learning: ThreadHandle | None = None

    def stop_all(self) -> None:
        for handle in (
            self.digest,
            self.scheduled_tasks,
            self.session_summary,
            self.eval_sampling,
            self.reply_eval,
            self.proactive_outreach,
            self.chat_delivery,
            self.group_learning,
        ):
            if handle is not None:
                handle.stop()


def run_reply_eval_tick() -> dict | None:
    """单次 reply_eval 调度检查——未启用返回 None,否则运行套件并返回概要。"""
    from core.settings_service import settings

    if not settings.get_bool("eval.reply_eval_schedule_enabled", False):
        return None

    from api.admin.reply_routes import run_reply_eval_suite
    from core.async_bridge import run_awaitable_sync
    from core.database import SessionLocal

    db = SessionLocal()
    try:
        variant = settings.get_str("eval.reply_eval_variant", "v2_code_retry")
        return run_awaitable_sync(
            run_reply_eval_suite(db, variant=variant, name=f"scheduled {variant}")
        )
    finally:
        db.close()


def reply_eval_scheduler(stop_event: threading.Event) -> None:
    """reply_eval 周期调度线程——默认关闭,由 eval.reply_eval_schedule_enabled 控制。"""
    from core.settings_service import settings

    worker_logger = logging.getLogger("nanobot.reply_eval.scheduler")
    worker_logger.info("Reply eval scheduler started.")
    while True:
        interval_hours = max(
            1, settings.get_int("eval.reply_eval_interval_hours", 24)
        )
        if stop_event.wait(timeout=interval_hours * 3600):
            break
        try:
            result = run_reply_eval_tick()
            if result is not None:
                worker_logger.info(
                    "Reply eval scheduled run finished: total=%s passed=%s failed=%s",
                    result.get("total"),
                    result.get("passed"),
                    result.get("failed"),
                )
        except Exception as exc:
            worker_logger.exception("Reply eval scheduler error: %s", exc)
    worker_logger.info("Reply eval scheduler stopped.")


def _start_thread(
    *,
    name: str,
    target: Callable[[threading.Event], None],
) -> ThreadHandle:
    stop_event = threading.Event()
    thread = threading.Thread(
        target=target,
        args=(stop_event,),
        daemon=True,
        name=name,
    )
    thread.start()
    return ThreadHandle(thread=thread, stop_event=stop_event)


def _preload_sentinel(logger: logging.Logger) -> None:
    try:
        from clients.classifier_client import Guardrail

        Guardrail._load_sentinel()
    except Exception as exc:
        logger.warning("Sentinel pre-load failed (will retry on first classify): %s", exc)


def session_summary_worker_scheduler(stop_event: threading.Event) -> None:
    from workers.session_summary_worker import run_until_stopped

    logger = logging.getLogger("nanobot.session_summary.worker")
    logger.info("Session summary worker scheduler started.")
    try:
        run_until_stopped(stop_event)
    except Exception as exc:
        logger.exception("Session summary worker scheduler error: %s", exc)
    logger.info("Session summary worker scheduler stopped.")


def chat_delivery_worker_scheduler(stop_event: threading.Event) -> None:
    from workers.chat_delivery_worker import run_until_stopped

    logger = logging.getLogger("nanobot.chat_delivery.worker")
    logger.info("Chat delivery worker scheduler started.")
    try:
        run_until_stopped(stop_event)
    except Exception as exc:
        logger.exception("Chat delivery worker scheduler error: %s", exc)
    logger.info("Chat delivery worker scheduler stopped.")


def group_learning_worker_scheduler(stop_event: threading.Event) -> None:
    from app.group_learning.scheduler import run_until_stopped
    from core.db import SessionLocal

    worker_logger = logging.getLogger(
        "nanobot.group_learning.scheduler"
    )
    worker_logger.info("Group learning scheduler started.")
    try:
        run_until_stopped(
            stop_event,
            session_factory=SessionLocal,
        )
    except Exception as exc:
        worker_logger.exception(
            "Group learning scheduler error: %s",
            exc,
        )
    worker_logger.info("Group learning scheduler stopped.")


def start_schedulers(*, testing: bool, logger: logging.Logger) -> SchedulerHandles:
    """启动后台调度器；测试模式只返回空 handles。"""
    if testing:
        logger.info("NANOBOT_TESTING=1: skipped scheduler startup.")
        return SchedulerHandles()

    from config import get_session_summary_worker_mode
    from core.daily_digest import daily_digest_scheduler, scheduled_task_runner
    from core.eval_sampling.scheduler import eval_sampling_scheduler
    from core.proactive_outreach import proactive_outreach_scheduler

    session_summary_mode = get_session_summary_worker_mode()
    handles = SchedulerHandles()
    handles.digest = _start_thread(
        name="daily-digest-scheduler",
        target=daily_digest_scheduler,
    )
    logger.info("Memory digest scheduler initialized.")

    handles.scheduled_tasks = _start_thread(
        name="scheduled-task-runner",
        target=scheduled_task_runner,
    )
    logger.info("Scheduled task runner initialized.")

    if session_summary_mode == "embedded":
        handles.session_summary = _start_thread(
            name="session-summary-worker",
            target=session_summary_worker_scheduler,
        )
        logger.info("Session summary worker initialized in embedded mode.")
    else:
        logger.info(
            "Session summary worker not embedded mode=%s.",
            session_summary_mode,
        )

    handles.chat_delivery = _start_thread(
        name="chat-delivery-worker",
        target=chat_delivery_worker_scheduler,
    )
    if isinstance(handles.chat_delivery, ThreadHandle):
        handles.chat_delivery.stop_timeout = 35.0
    logger.info("Chat delivery worker initialized.")

    _preload_sentinel(logger)

    handles.eval_sampling = _start_thread(
        name="eval-sampling-scheduler",
        target=eval_sampling_scheduler,
    )
    logger.info("Eval sampling scheduler initialized.")

    handles.reply_eval = _start_thread(
        name="reply-eval-scheduler",
        target=reply_eval_scheduler,
    )
    logger.info("Reply eval scheduler initialized (disabled by default).")

    handles.proactive_outreach = _start_thread(
        name="proactive-outreach-scheduler",
        target=proactive_outreach_scheduler,
    )
    logger.info("Proactive outreach recovery scheduler initialized.")

    handles.group_learning = _start_thread(
        name="group-learning-scheduler",
        target=group_learning_worker_scheduler,
    )
    logger.info(
        "Group learning scheduler initialized with empty-by-default whitelist."
    )

    return handles
