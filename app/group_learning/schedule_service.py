"""群学习白名单配置、增量批次和 fenced 调度应用服务。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json

from app.group_analysis.preprocess import (
    clean_message,
    is_analyzable_log,
)
from app.group_learning.candidate_service import (
    GroupLearningCandidateBatchRequest,
    GroupLearningMessage,
)
from core.chat_stream_identity import (
    ChatStreamIdentityError,
    parse_canonical_chat_stream_id,
)
from core.db.group_learning_schedule_contracts import (
    GroupLearningChatLogRecord,
    GroupLearningScheduleClaim,
    GroupLearningScheduleLeaseLost,
    GroupLearningScheduleRepositoryPort,
    GroupLearningScheduleState,
    GroupLearningScheduleWrite,
)
from core.group_learning import validate_aspect_selection
from core.group_learning.scheduling import (
    GROUP_LEARNING_SCHEDULE_POLICY,
)
from core.jobs import require_job_schedule_policy


@dataclass(frozen=True, slots=True)
class GroupLearningPreparedBatch:
    status: str
    request: GroupLearningCandidateBatchRequest | None
    raw_new_count: int
    eligible_new_count: int


def _canonical_group(chat_stream_id: str) -> str:
    try:
        identity = parse_canonical_chat_stream_id(
            str(chat_stream_id or "").strip()
        )
    except ChatStreamIdentityError as exc:
        raise ValueError(
            "群学习调度只接受 canonical chat_stream_id"
        ) from exc
    if identity.chat_type != "group":
        raise ValueError("群学习调度只接受 canonical group session")
    return identity.chat_stream_id


def _safe_meta(raw: object) -> dict[str, object]:
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "是",
        }
    return bool(value)


def _to_message(
    row: GroupLearningChatLogRecord,
    *,
    context_only: bool,
) -> GroupLearningMessage | None:
    meta = _safe_meta(row.meta_json)
    sender = meta.get("sender")
    sender = sender if isinstance(sender, dict) else {}
    if (
        _truthy(meta.get("no_learn"))
        or _truthy(meta.get("is_bot"))
        or _truthy(meta.get("sender_is_bot"))
        or _truthy(meta.get("external_bot"))
        or _truthy(sender.get("is_bot"))
    ):
        return None
    moderation = meta.get("moderation")
    if (
        isinstance(moderation, dict)
        and _truthy(moderation.get("no_learn"))
    ):
        return None
    if not is_analyzable_log(row):
        return None
    content = clean_message(row.content)
    if not content:
        return None
    sender_id = str(
        sender.get("id")
        or meta.get("sender_id")
        or ""
    ).strip()
    if not sender_id and row.user_id:
        sender_id = str(row.user_id).strip()
    if not sender_id:
        sender_id = str(row.sender_name or "").strip()
    if not sender_id:
        return None
    return GroupLearningMessage(
        chat_log_id=row.chat_log_id,
        sender_id=sender_id[:255],
        content=content[:2000],
        context_only=context_only,
    )


def _batch_identity(
    *,
    chat_stream_id: str,
    aspects: tuple[str, ...],
    cursor_start: int,
    cursor_end: int,
) -> tuple[str, str]:
    payload = json.dumps(
        {
            "chat_stream_id": chat_stream_id,
            "aspects": aspects,
            "cursor_start": cursor_start,
            "cursor_end": cursor_end,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"glr_{digest[:48]}", f"group-learning:{digest}"


class GroupLearningScheduleService:
    """白名单行是唯一自动学习授权；全局开关只控制执行。"""

    def __init__(
        self,
        *,
        repository: GroupLearningScheduleRepositoryPort,
        enabled: Callable[[], bool],
    ) -> None:
        if not isinstance(
            repository,
            GroupLearningScheduleRepositoryPort,
        ):
            raise TypeError(
                "repository 未实现 GroupLearningScheduleRepositoryPort"
            )
        if not callable(enabled):
            raise TypeError("enabled 必须可调用")
        self.repository = repository
        self.enabled = enabled

    @staticmethod
    def _now(value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError("now 必须是 datetime")
        return value

    def put_schedule(
        self,
        *,
        chat_stream_id: str,
        aspects: tuple[str, ...] | None = None,
        interval_minutes: int | None = None,
        window_hours: int | None = None,
        enabled: bool = True,
        now: datetime,
    ) -> GroupLearningScheduleState:
        policy = GROUP_LEARNING_SCHEDULE_POLICY
        canonical_id = _canonical_group(chat_stream_id)
        selected_aspects = validate_aspect_selection(
            aspects,
            use_schedule_default=True,
        )
        interval = policy.validate_interval(
            policy.default_interval_minutes
            if interval_minutes is None
            else interval_minutes
        )
        window = policy.validate_window(
            policy.default_window_hours
            if window_hours is None
            else window_hours
        )
        current_time = self._now(now)
        write = GroupLearningScheduleWrite(
            chat_stream_id=canonical_id,
            enabled=bool(enabled),
            aspects=selected_aspects,
            interval_minutes=interval,
            window_hours=window,
            next_run_at=current_time,
        )
        try:
            result = self.repository.put_schedule(
                write,
                now=current_time,
            )
            self.repository.commit()
            return result
        except BaseException:
            self.repository.rollback()
            raise

    def disable_schedule(
        self,
        *,
        chat_stream_id: str,
        now: datetime,
    ) -> GroupLearningScheduleState:
        canonical_id = _canonical_group(chat_stream_id)
        try:
            result = self.repository.disable_schedule(
                canonical_id,
                now=self._now(now),
            )
            self.repository.commit()
            return result
        except BaseException:
            self.repository.rollback()
            raise

    def get_schedule(
        self,
        chat_stream_id: str,
    ) -> GroupLearningScheduleState | None:
        return self.repository.get_schedule(
            _canonical_group(chat_stream_id)
        )

    def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
    ) -> GroupLearningScheduleClaim | None:
        if not bool(self.enabled()):
            return None
        normalized_worker = str(worker_id or "").strip()
        if not normalized_worker or len(normalized_worker) > 128:
            raise ValueError("群学习 worker_id 无效")
        policy = require_job_schedule_policy(
            GROUP_LEARNING_SCHEDULE_POLICY.job_schedule_policy_id
        )
        try:
            claim = self.repository.claim_due(
                worker_id=normalized_worker,
                now=self._now(now),
                lease_seconds=policy.lease_seconds,
            )
            if claim is None:
                self.repository.rollback()
                return None
            self.repository.commit()
            return claim
        except BaseException:
            self.repository.rollback()
            raise

    def prepare_batch(
        self,
        claim: GroupLearningScheduleClaim,
        *,
        now: datetime,
    ) -> GroupLearningPreparedBatch:
        if not bool(self.enabled()):
            return GroupLearningPreparedBatch(
                status="disabled",
                request=None,
                raw_new_count=0,
                eligible_new_count=0,
            )
        policy = GROUP_LEARNING_SCHEDULE_POLICY
        logs = self.repository.load_incremental_logs(
            claim,
            now=self._now(now),
            context_limit=policy.context_message_limit,
            max_new_messages=policy.max_new_messages,
        )
        context = tuple(
            message
            for row in logs.context
            if (
                message := _to_message(
                    row,
                    context_only=True,
                )
            )
            is not None
        )
        new = tuple(
            message
            for row in logs.new
            if (
                message := _to_message(
                    row,
                    context_only=False,
                )
            )
            is not None
        )[: policy.max_new_messages]
        if len(new) < policy.min_new_messages:
            return GroupLearningPreparedBatch(
                status="insufficient_messages",
                request=None,
                raw_new_count=len(logs.new),
                eligible_new_count=len(new),
            )
        cursor_end = max(message.chat_log_id for message in new)
        context_start = (
            min(message.chat_log_id for message in context)
            if context
            else 0
        )
        context_end = (
            max(message.chat_log_id for message in context)
            if context
            else 0
        )
        run_id, idempotency_key = _batch_identity(
            chat_stream_id=claim.chat_stream_id,
            aspects=claim.aspects,
            cursor_start=logs.success_cursor,
            cursor_end=cursor_end,
        )
        request = GroupLearningCandidateBatchRequest(
            run_id=run_id,
            idempotency_key=idempotency_key,
            chat_stream_id=claim.chat_stream_id,
            trigger="schedule",
            aspects=claim.aspects,
            cursor_start_chat_log_id=logs.success_cursor,
            cursor_end_chat_log_id=cursor_end,
            context_start_chat_log_id=context_start,
            context_end_chat_log_id=context_end,
            messages=(*context, *new),
            job_id=claim.lease.job_id,
        )
        return GroupLearningPreparedBatch(
            status="ready",
            request=request,
            raw_new_count=len(logs.new),
            eligible_new_count=len(new),
        )

    def settle_success(
        self,
        claim: GroupLearningScheduleClaim,
        *,
        now: datetime,
    ) -> GroupLearningScheduleState:
        try:
            result = self.repository.settle_success(
                claim,
                now=self._now(now),
            )
            self.repository.commit()
            return result
        except BaseException:
            self.repository.rollback()
            raise

    def settle_failure(
        self,
        claim: GroupLearningScheduleClaim,
        *,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> GroupLearningScheduleState:
        normalized_code = str(error_code or "").strip()
        if (
            not normalized_code
            or len(normalized_code) > 64
            or not normalized_code.replace("_", "").isalnum()
        ):
            raise ValueError("群学习 error_code 无效")
        if not isinstance(retry_at, datetime):
            raise TypeError("retry_at 必须是 datetime")
        current_time = self._now(now)
        if retry_at <= current_time:
            raise ValueError("retry_at 必须晚于 now")
        try:
            result = self.repository.settle_failure(
                claim,
                error_code=normalized_code,
                retry_at=retry_at,
                now=current_time,
            )
            self.repository.commit()
            return result
        except BaseException:
            self.repository.rollback()
            raise


__all__ = [
    "GroupLearningPreparedBatch",
    "GroupLearningScheduleLeaseLost",
    "GroupLearningScheduleService",
]
