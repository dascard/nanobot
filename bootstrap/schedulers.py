"""后台调度器启动与停止。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable


@dataclass
class ThreadHandle:
    thread: threading.Thread
    stop_event: threading.Event

    def stop(self, timeout: float = 5.0) -> None:
        self.stop_event.set()
        self.thread.join(timeout=timeout)


@dataclass
class SchedulerHandles:
    digest: ThreadHandle | None = None
    scheduled_tasks: ThreadHandle | None = None
    expression_learner: ThreadHandle | None = None
    eval_sampling: ThreadHandle | None = None

    def stop_all(self) -> None:
        for handle in (
            self.digest,
            self.scheduled_tasks,
            self.expression_learner,
            self.eval_sampling,
        ):
            if handle is not None:
                handle.stop()


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


def start_schedulers(*, testing: bool, logger: logging.Logger) -> SchedulerHandles:
    """启动后台调度器；测试模式只返回空 handles。"""
    if testing:
        logger.info("NANOBOT_TESTING=1: skipped scheduler startup.")
        return SchedulerHandles()

    from config import DAILY_DIGEST_ENABLED
    from core.daily_digest import daily_digest_scheduler, scheduled_task_runner
    from core.eval_sampling.scheduler import eval_sampling_scheduler
    from core.expression_learner import expression_learner_scheduler

    handles = SchedulerHandles()
    if DAILY_DIGEST_ENABLED:
        handles.digest = _start_thread(
            name="daily-digest-scheduler",
            target=daily_digest_scheduler,
        )
        logger.info("Daily digest scheduler initialized.")

    handles.scheduled_tasks = _start_thread(
        name="scheduled-task-runner",
        target=scheduled_task_runner,
    )
    logger.info("Scheduled task runner initialized.")

    _preload_sentinel(logger)

    handles.expression_learner = _start_thread(
        name="expression-learner",
        target=expression_learner_scheduler,
    )
    logger.info("Expression learner scheduler initialized.")

    handles.eval_sampling = _start_thread(
        name="eval-sampling-scheduler",
        target=eval_sampling_scheduler,
    )
    logger.info("Eval sampling scheduler initialized.")

    return handles
