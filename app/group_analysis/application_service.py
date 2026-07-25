"""群分析共享应用服务；Tool、Scheduler 和 Web 只做输入输出适配。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from app.group_analysis.analyzer import analyze_group
from app.group_analysis.preprocess import build_analysis_payload
from app.group_analysis.render import format_scrapbook_html
from app.group_analysis.schemas import RawChatLog
from app.group_learning.candidate_service import (
    GroupLearningCandidateBatchRequest,
    GroupLearningMessage,
)
from app.group_learning.scheduler import (
    GroupLearningProcessingOutcome,
)
from core.async_bridge import run_awaitable_sync
from core.group_learning import validate_aspect_selection


class GroupLearningPipelinePort(Protocol):
    def process_with_analysis(
        self,
        request: GroupLearningCandidateBatchRequest,
        *,
        analysis: Mapping[str, object],
    ) -> GroupLearningProcessingOutcome: ...


@dataclass(frozen=True, slots=True)
class GroupAnalysisApplicationResult:
    analysis: Mapping[str, object]
    report: str
    group_stats: Mapping[str, object]
    learning_outcome: GroupLearningProcessingOutcome | None


def _message_meta(
    *,
    sender_id: str,
    context_only: bool,
) -> str:
    value: dict[str, object] = {
        "sender": {"id": sender_id},
    }
    if context_only:
        value["moderation"] = {"no_learn": True}
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _payload_from_learning_request(
    request: GroupLearningCandidateBatchRequest,
) -> dict[str, object]:
    logs = [
        RawChatLog(
            id=message.chat_log_id,
            role="ambient",
            user_id=message.sender_id,
            sender_name=message.sender_id,
            content=message.content,
            session_id=request.chat_stream_id,
            meta_json=_message_meta(
                sender_id=message.sender_id,
                context_only=message.context_only,
            ),
        )
        for message in request.messages
    ]
    payload = build_analysis_payload(logs)
    official_ids = {
        message.chat_log_id
        for message in request.messages
        if not message.context_only
    }
    trusted_messages = tuple(
        message
        for message in payload["messages"]
        if (
            int(message.get("log_id") or 0) in official_ids
            and message.get("memory_evidence_trusted")
        )
    )
    payload["trusted_source_log_ids"] = [
        int(message["log_id"])
        for message in trusted_messages
    ]
    payload["trusted_source_speakers"] = {
        str(int(message["log_id"])): str(
            message.get("speaker_id") or "?"
        )
        for message in trusted_messages
    }
    return payload


def build_group_analysis_learning_request(
    *,
    chat_stream_id: str,
    aspects: object,
    payload: Mapping[str, object],
    trigger: str,
    cursor_start_chat_log_id: int,
    cursor_end_chat_log_id: int,
    trace_id: str = "",
    job_id: str = "",
) -> GroupLearningCandidateBatchRequest | None:
    """把群分析可信消息投影为稳定、可幂等的学习批次。"""

    selected_aspects = validate_aspect_selection(aspects)
    start_cursor = int(cursor_start_chat_log_id)
    end_cursor = int(cursor_end_chat_log_id)
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, (list, tuple)):
        return None
    messages: list[GroupLearningMessage] = []
    signature: list[tuple[int, str, str]] = []
    seen_ids: set[int] = set()
    for raw in raw_messages:
        if (
            not isinstance(raw, Mapping)
            or not raw.get("memory_evidence_trusted")
        ):
            continue
        raw_id = raw.get("log_id")
        if type(raw_id) is not int:
            continue
        chat_log_id = raw_id
        if (
            chat_log_id <= start_cursor
            or chat_log_id > end_cursor
            or chat_log_id in seen_ids
        ):
            continue
        sender_id = str(
            raw.get("speaker_id")
            or raw.get("user_id")
            or ""
        ).strip()[:255]
        content = str(raw.get("content") or "").strip()[:2000]
        if not sender_id or not content:
            continue
        message = GroupLearningMessage(
            chat_log_id=chat_log_id,
            sender_id=sender_id,
            content=content,
        )
        messages.append(message)
        seen_ids.add(chat_log_id)
        signature.append((
            chat_log_id,
            sender_id,
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
        ))
    if not messages:
        return None
    messages.sort(key=lambda item: item.chat_log_id)
    signature.sort()
    digest = hashlib.sha256(
        json.dumps(
            {
                "chat_stream_id": str(chat_stream_id or "").strip(),
                "trigger": str(trigger or "").strip(),
                "aspects": selected_aspects,
                "cursor_start": start_cursor,
                "cursor_end": end_cursor,
                "messages": signature,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return GroupLearningCandidateBatchRequest(
        run_id=f"glr_{digest[:48]}",
        idempotency_key=f"group-learning:{digest}",
        chat_stream_id=chat_stream_id,
        trigger=trigger,
        aspects=selected_aspects,
        cursor_start_chat_log_id=start_cursor,
        cursor_end_chat_log_id=end_cursor,
        context_start_chat_log_id=0,
        context_end_chat_log_id=0,
        messages=tuple(messages),
        trace_id=trace_id,
        job_id=job_id,
    )


class GroupAnalysisApplicationService:
    """先生成被选分析分支，再把同一结果交给受治理学习 Pipeline。"""

    def __init__(
        self,
        *,
        learning_pipeline: GroupLearningPipelinePort,
        analyzer: Callable[
            ...,
            Awaitable[dict[str, object]],
        ] = analyze_group,
    ) -> None:
        self.learning_pipeline = learning_pipeline
        self.analyzer = analyzer

    async def process_learning_batch(
        self,
        request: GroupLearningCandidateBatchRequest,
        *,
        group_name: str = "",
        instructions: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> GroupAnalysisApplicationResult:
        analysis_payload = (
            dict(payload)
            if payload is not None
            else _payload_from_learning_request(request)
        )
        return await self.process_payload(
            aspects=request.aspects,
            payload=analysis_payload,
            group_name=group_name,
            instructions=instructions,
            learning_request=request,
        )

    async def process_payload(
        self,
        *,
        aspects: object,
        payload: Mapping[str, object],
        group_name: str,
        instructions: str = "",
        learning_request: (
            GroupLearningCandidateBatchRequest | None
        ) = None,
    ) -> GroupAnalysisApplicationResult:
        selected_aspects = validate_aspect_selection(aspects)
        if (
            learning_request is not None
            and learning_request.aspects != selected_aspects
        ):
            raise ValueError("群分析与学习批次 aspects 不一致")
        analysis_payload = dict(payload)
        analysis = await self.analyzer(
            analysis_payload,
            str(instructions or "").strip(),
            aspects=selected_aspects,
        )
        outcome = (
            self.learning_pipeline.process_with_analysis(
                learning_request,
                analysis=analysis,
            )
            if learning_request is not None
            else None
        )
        stats = dict(analysis_payload.get("group_stats") or {})
        report = format_scrapbook_html(
            str(
                group_name
                or (
                    learning_request.chat_stream_id
                    if learning_request is not None
                    else "群聊"
                )
            ),
            stats,
            dict(analysis.get("topics") or {}),
            dict(analysis.get("titles") or {}),
            dict(analysis.get("quotes") or {}),
            dict(analysis.get("quality") or {}),
            aspects=selected_aspects,
        )
        return GroupAnalysisApplicationResult(
            analysis=analysis,
            report=report,
            group_stats=stats,
            learning_outcome=outcome,
        )


class GroupAnalysisScheduleProcessor:
    """Durable Job 的同步 Adapter；业务仍由共享异步服务执行。"""

    def __init__(
        self,
        application_service: GroupAnalysisApplicationService,
    ) -> None:
        self.application_service = application_service

    def process(
        self,
        request: GroupLearningCandidateBatchRequest,
    ) -> GroupLearningProcessingOutcome:
        result = run_awaitable_sync(
            self.application_service.process_learning_batch(request)
        )
        if not isinstance(
            result.learning_outcome,
            GroupLearningProcessingOutcome,
        ):
            raise TypeError("群分析应用服务返回了无效学习结果")
        return result.learning_outcome


__all__ = [
    "GroupAnalysisApplicationResult",
    "GroupAnalysisApplicationService",
    "GroupAnalysisScheduleProcessor",
    "GroupLearningPipelinePort",
    "build_group_analysis_learning_request",
]
