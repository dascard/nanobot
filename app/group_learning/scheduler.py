"""群学习调度器：显式白名单 claim → 共享服务 → fenced settle。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import os
import socket
import threading
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from app.group_learning.candidate_service import (
    GroupLearningCandidateBatchRequest,
)
from app.group_learning.schedule_service import (
    GroupLearningScheduleService,
)
from core.jobs import (
    JobFailure,
    require_job_retry_policy,
)
from core.resilience import FailureCategory
from core.time_utils import db_now_naive


logger = logging.getLogger("nanobot.group_learning.scheduler")


@dataclass(frozen=True, slots=True)
class GroupLearningProcessingOutcome:
    status: str
    run_id: str
    error_code: str = ""
    retryable: bool = False
    failure_category: FailureCategory = FailureCategory.PERMANENT

    @classmethod
    def succeeded(
        cls,
        *,
        run_id: str,
    ) -> "GroupLearningProcessingOutcome":
        return cls(status="succeeded", run_id=str(run_id or ""))

    @classmethod
    def failed(
        cls,
        *,
        run_id: str,
        error_code: str,
        retryable: bool,
        failure_category: FailureCategory | None = None,
    ) -> "GroupLearningProcessingOutcome":
        category = failure_category or (
            FailureCategory.UNAVAILABLE
            if retryable
            else FailureCategory.PERMANENT
        )
        return cls(
            status="failed",
            run_id=str(run_id or ""),
            error_code=str(error_code or ""),
            retryable=bool(retryable),
            failure_category=category,
        )


@runtime_checkable
class GroupLearningProcessorPort(Protocol):
    def process(
        self,
        request: GroupLearningCandidateBatchRequest,
    ) -> GroupLearningProcessingOutcome: ...


@dataclass(frozen=True, slots=True)
class GroupLearningSchedulerTick:
    status: str
    chat_stream_id: str = ""
    run_id: str = ""
    error_code: str = ""


class GroupLearningScheduleRunner:
    """一次只处理一个显式 schedule；异常留给租约过期恢复。"""

    def __init__(
        self,
        *,
        schedule_service: GroupLearningScheduleService,
        processor: GroupLearningProcessorPort,
    ) -> None:
        if not isinstance(
            schedule_service,
            GroupLearningScheduleService,
        ):
            raise TypeError(
                "schedule_service 必须是 GroupLearningScheduleService"
            )
        if not isinstance(processor, GroupLearningProcessorPort):
            raise TypeError(
                "processor 未实现 GroupLearningProcessorPort"
            )
        self.schedule_service = schedule_service
        self.processor = processor

    def run_once(
        self,
        *,
        worker_id: str,
        now: datetime,
    ) -> GroupLearningSchedulerTick:
        claim = self.schedule_service.claim_due(
            worker_id=worker_id,
            now=now,
        )
        if claim is None:
            return GroupLearningSchedulerTick(status="idle")
        prepared = self.schedule_service.prepare_batch(
            claim,
            now=now,
        )
        if prepared.request is None:
            if prepared.status == "disabled":
                return GroupLearningSchedulerTick(
                    status="disabled",
                    chat_stream_id=claim.chat_stream_id,
                )
            self.schedule_service.settle_success(claim, now=now)
            return GroupLearningSchedulerTick(
                status=prepared.status,
                chat_stream_id=claim.chat_stream_id,
            )
        outcome = self.processor.process(prepared.request)
        if not isinstance(outcome, GroupLearningProcessingOutcome):
            raise TypeError(
                "群学习 processor 必须返回 GroupLearningProcessingOutcome"
            )
        if outcome.status == "succeeded":
            self.schedule_service.settle_success(claim, now=now)
            return GroupLearningSchedulerTick(
                status="succeeded",
                chat_stream_id=claim.chat_stream_id,
                run_id=outcome.run_id,
            )
        if outcome.status != "failed":
            raise ValueError("群学习 processor status 无效")
        failure = JobFailure(
            code=outcome.error_code,
            category=outcome.failure_category,
            retryable=outcome.retryable,
            safe_summary="群学习处理失败",
        )
        retry_policy = require_job_retry_policy(
            "group_memory_learning.v1"
        )
        if retry_policy.allows_retry(
            failure,
            attempt_count=claim.lease.attempt_no,
        ):
            delay_seconds = retry_policy.delay_seconds(
                attempt_count=claim.lease.attempt_no
            )
        else:
            delay_seconds = claim.interval_minutes * 60
        self.schedule_service.settle_failure(
            claim,
            error_code=outcome.error_code,
            retry_at=now + timedelta(seconds=delay_seconds),
            now=now,
        )
        return GroupLearningSchedulerTick(
            status="failed",
            chat_stream_id=claim.chat_stream_id,
            run_id=outcome.run_id,
            error_code=outcome.error_code,
        )


def _worker_id() -> str:
    configured = str(
        os.environ.get("NANOBOT_GROUP_LEARNING_WORKER_ID") or ""
    ).strip()
    if configured:
        return configured[:128]
    return f"{socket.gethostname()}:{os.getpid()}:group-learning"[:128]


def run_until_stopped(
    stop_event: threading.Event,
    *,
    session_factory: Callable[[], Any],
    poll_seconds: float = 30.0,
) -> None:
    """生产轮询入口；Composition Root 只在 Feature 打开时构建处理器。"""

    from core.db.group_learning_schedule_adapter import (
        SqlAlchemyGroupLearningScheduleRepository,
    )
    from core.settings_service import settings

    worker_id = _worker_id()
    while not stop_event.is_set():
        if not settings.get_bool("group_learning.enabled", False):
            stop_event.wait(poll_seconds)
            continue
        session = session_factory()
        try:
            from app.group_analysis.application_service import (
                GroupAnalysisApplicationService,
                GroupAnalysisScheduleProcessor,
            )
            from app.group_learning.pipeline_service import (
                build_group_learning_processor,
            )

            schedule_service = GroupLearningScheduleService(
                repository=(
                    SqlAlchemyGroupLearningScheduleRepository(session)
                ),
                enabled=lambda: settings.get_bool(
                    "group_learning.enabled",
                    False,
                ),
            )
            GroupLearningScheduleRunner(
                schedule_service=schedule_service,
                processor=GroupAnalysisScheduleProcessor(
                    GroupAnalysisApplicationService(
                        learning_pipeline=(
                            build_group_learning_processor(session)
                        ),
                    )
                ),
            ).run_once(
                worker_id=worker_id,
                now=db_now_naive(),
            )
        except Exception:
            session.rollback()
            logger.exception("群学习调度执行失败")
        finally:
            session.close()
        stop_event.wait(poll_seconds)


__all__ = [
    "GroupLearningProcessingOutcome",
    "GroupLearningProcessorPort",
    "GroupLearningScheduleRunner",
    "GroupLearningSchedulerTick",
    "run_until_stopped",
]
