"""群学习共享处理链：候选、模型审核、治理和成功游标。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import re

from sqlalchemy.orm import Session

from app.group_learning.candidate_service import (
    GroupLearningCandidateBatchRequest,
    GroupLearningCandidateService,
    group_learning_candidate_identity,
    group_learning_evidence_identity,
)
from app.group_learning.governance_service import (
    GroupLearningGovernanceService,
)
from app.group_learning.review_service import (
    GroupLearningModelReviewRequest,
    GroupLearningModelReviewService,
)
from app.group_learning.scheduler import GroupLearningProcessingOutcome
from core.db.group_learning_adapter import (
    SqlAlchemyGroupLearningQueryRepository,
)
from core.db.group_learning_command_adapter import (
    SqlAlchemyGroupLearningCommandRepository,
)
from core.db.group_learning_contracts import (
    GroupLearningCandidateWrite,
    GroupLearningEvidenceWrite,
    GroupLearningObservationMetrics,
)
from core.db.group_learning_governance_adapter import (
    SqlAlchemyGroupLearningGovernanceRepository,
)
from core.db.group_memory_adapter import (
    SqlAlchemyGroupMemoryRepository,
)
from core.prompt_v2.task_contracts import (
    TaskOutputContractError,
    parse_task_output,
)
from core.resilience import FailureCategory
from core.settings_service import settings
from core.task_runtime import (
    TaskInvocation,
    TaskResult,
    execute_task,
    thaw_task_value,
)
from core.task_runtime.validators import (
    TaskBusinessValidationError,
    validate_task_business_output,
)
from core.time_utils import db_now_naive


_MODEL_MEMORY_ASPECTS = frozenset({
    "expressions",
    "slang",
    "style",
})
_REPORT_ONLY_ASPECTS = frozenset({
    "titles",
    "quotes",
    "quality",
})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256(*parts: object) -> str:
    return hashlib.sha256(
        "\0".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()


def _non_negative_int(
    value: object,
    *,
    field_name: str,
) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} 必须是非负整数")
    return value


def _usage_count(
    usage: Mapping[str, object],
    *names: str,
) -> int:
    for name in names:
        value = usage.get(name)
        if type(value) is int and value >= 0:
            return value
    return 0


class GroupLearningPipelineService:
    """供 Scheduler、Web 和 Tool 共同调用的框架无关处理器。"""

    def __init__(
        self,
        *,
        candidate_service: GroupLearningCandidateService,
        review_service: GroupLearningModelReviewService,
        governance_service: GroupLearningGovernanceService,
        query_repository: SqlAlchemyGroupLearningQueryRepository,
        command_repository: SqlAlchemyGroupLearningCommandRepository,
        task_executor: Callable[[TaskInvocation], TaskResult],
        enabled: Callable[[], bool],
    ) -> None:
        self.candidate_service = candidate_service
        self.review_service = review_service
        self.governance_service = governance_service
        self.query_repository = query_repository
        self.command_repository = command_repository
        self.task_executor = task_executor
        self._enabled = enabled

    @staticmethod
    def _failed(
        request: GroupLearningCandidateBatchRequest,
        *,
        error_code: str,
        retryable: bool = False,
        failure_category: FailureCategory = FailureCategory.PERMANENT,
    ) -> GroupLearningProcessingOutcome:
        return GroupLearningProcessingOutcome.failed(
            run_id=request.run_id,
            error_code=error_code,
            retryable=retryable,
            failure_category=failure_category,
        )

    @staticmethod
    def _task_metrics(
        result: TaskResult,
        *,
        input_chars: int,
    ) -> GroupLearningObservationMetrics:
        usage = result.usage
        input_tokens = _usage_count(
            usage,
            "prompt_tokens",
            "input_tokens",
        )
        output_tokens = _usage_count(
            usage,
            "completion_tokens",
            "output_tokens",
        )
        total_tokens = _usage_count(usage, "total_tokens")
        if not total_tokens:
            total_tokens = input_tokens + output_tokens
        cost_microusd = usage.get("cost_microusd")
        if type(cost_microusd) is not int or cost_microusd < 0:
            cost_microusd = None
        return GroupLearningObservationMetrics(
            task_run_id=str(result.run_id or ""),
            contract_version=str(result.contract_version or ""),
            provider=str(result.provider or ""),
            model=str(result.model or ""),
            input_chars=max(0, int(input_chars)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_microusd=cost_microusd,
            latency_ms=max(0, round(float(result.latency_ms or 0))),
            attempt_count=max(0, int(result.attempt_count or 0)),
            raw_output_bytes=max(0, int(result.raw_output_bytes or 0)),
            raw_output_sha256=str(result.raw_output_sha256 or ""),
            route_key=str(result.route_key or ""),
        )

    @staticmethod
    def _topic_provenance_metrics(
        branch: Mapping[str, object],
    ) -> GroupLearningObservationMetrics:
        provenance = branch.get("_task_provenance")
        if not isinstance(provenance, Mapping):
            raise ValueError("topic_provenance_missing")
        run_id = str(provenance.get("run_id") or "").strip()
        contract_version = str(
            provenance.get("contract_version") or ""
        ).strip()
        route_key = str(provenance.get("route_key") or "").strip()
        provider = str(provenance.get("provider") or "").strip()
        model = str(provenance.get("model") or "").strip()
        raw_output_sha256 = str(
            provenance.get("raw_output_sha256") or ""
        ).strip().lower()
        if (
            not run_id
            or contract_version != "group_analysis_topics_v1"
            or route_key != "group_analysis_topics"
            or not provider
            or not model
            or not _SHA256_RE.fullmatch(raw_output_sha256)
        ):
            raise ValueError("topic_provenance_missing")
        attempt_count = _non_negative_int(
            provenance.get("attempt_count"),
            field_name="topic provenance attempt_count",
        )
        if attempt_count < 1:
            raise ValueError("topic_provenance_missing")
        latency_ms = _non_negative_int(
            provenance.get("latency_ms"),
            field_name="topic provenance latency_ms",
        )
        raw_output_bytes = _non_negative_int(
            provenance.get("raw_output_bytes"),
            field_name="topic provenance raw_output_bytes",
        )
        usage = provenance.get("usage")
        if not isinstance(usage, Mapping):
            raise ValueError("topic_provenance_missing")
        input_tokens = _usage_count(
            usage,
            "prompt_tokens",
            "input_tokens",
        )
        output_tokens = _usage_count(
            usage,
            "completion_tokens",
            "output_tokens",
        )
        total_tokens = _usage_count(usage, "total_tokens")
        if total_tokens < input_tokens + output_tokens:
            raise ValueError("topic_provenance_missing")
        cost_microusd = usage.get("cost_microusd")
        if type(cost_microusd) is not int or cost_microusd < 0:
            cost_microusd = None
        return GroupLearningObservationMetrics(
            task_run_id=run_id,
            contract_version=contract_version,
            provider=provider,
            model=model,
            input_chars=0,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_microusd=cost_microusd,
            latency_ms=latency_ms,
            attempt_count=attempt_count,
            raw_output_bytes=raw_output_bytes,
            raw_output_sha256=raw_output_sha256,
            route_key=route_key,
        )

    @staticmethod
    def _topic_analysis_from_result(
        result: TaskResult,
    ) -> dict[str, object]:
        parsed_value = thaw_task_value(result.parsed_value)
        if not isinstance(parsed_value, Mapping):
            raise ValueError("topic_contract_invalid")
        return {
            "topics": {
                **dict(parsed_value),
                "_generator": "llm",
                "_task_provenance": {
                    "run_id": str(result.run_id or ""),
                    "contract_version": str(
                        result.contract_version or ""
                    ),
                    "route_key": str(result.route_key or ""),
                    "provider": str(result.provider or ""),
                    "model": str(result.model or ""),
                    "attempt_count": max(
                        0,
                        int(result.attempt_count or 0),
                    ),
                    "latency_ms": max(
                        0,
                        round(float(result.latency_ms or 0)),
                    ),
                    "raw_output_sha256": str(
                        result.raw_output_sha256 or ""
                    ),
                    "raw_output_bytes": max(
                        0,
                        int(result.raw_output_bytes or 0),
                    ),
                    "usage": dict(result.usage),
                },
            }
        }

    def _execute_topics(
        self,
        request: GroupLearningCandidateBatchRequest,
    ) -> tuple[
        Mapping[str, object] | None,
        GroupLearningProcessingOutcome | None,
    ]:
        message_cards = [{
            "chat_log_id": message.chat_log_id,
            "sender_id": message.sender_id,
            "content": message.content,
            "context_only": message.context_only,
        } for message in request.messages]
        input_text = json.dumps(
            {"messages": message_cards},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        result = self.task_executor(TaskInvocation(
            invocation_id="group_analysis_topics",
            route_key="group_analysis_topics",
            input_values={"message": input_text},
            request_context={
                "allowed_evidence_log_ids": tuple(
                    message.chat_log_id
                    for message in request.messages
                    if not message.context_only
                ),
            },
            idempotency_key=(
                f"group_analysis_topics:{request.run_id}"
            ),
            timeout_budget_seconds=90.0,
        ))
        if result.ok:
            return self._topic_analysis_from_result(result), None
        metrics = self._task_metrics(
            result,
            input_chars=len(input_text),
        )
        error_code = (
            result.failure.code.value
            if result.failure is not None
            else "provider_error"
        )
        try:
            self.command_repository.record_model_failure(
                run_id=request.run_id,
                error_code=error_code,
                metrics=metrics,
            )
            self.command_repository.commit()
        except BaseException:
            self.command_repository.rollback()
            raise
        return None, self._failed(
            request,
            error_code=error_code,
            retryable=bool(
                result.failure.retryable
                if result.failure is not None
                else False
            ),
            failure_category=(
                result.failure.category
                if result.failure is not None
                else FailureCategory.TRANSIENT_TRANSPORT
            ),
        )

    def _record_topics(
        self,
        request: GroupLearningCandidateBatchRequest,
        *,
        analysis: Mapping[str, object],
    ) -> GroupLearningProcessingOutcome | None:
        raw_branch = analysis.get("topics")
        if not isinstance(raw_branch, Mapping):
            return self._failed(
                request,
                error_code="topic_analysis_missing",
                failure_category=FailureCategory.CONTRACT_VIOLATION,
            )
        if str(raw_branch.get("_generator") or "") != "llm":
            return self._failed(
                request,
                error_code="topic_not_model_generated",
                failure_category=FailureCategory.CONTRACT_VIOLATION,
            )
        try:
            metrics = self._topic_provenance_metrics(raw_branch)
        except ValueError:
            return self._failed(
                request,
                error_code="topic_provenance_missing",
                failure_category=FailureCategory.CONTRACT_VIOLATION,
            )
        raw_topics = raw_branch.get("topics")
        schema_payload = {"topics": raw_topics}
        try:
            parsed = parse_task_output(
                "tasks/group_analysis_topics",
                json.dumps(
                    schema_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            validated = validate_task_business_output(
                "group_analysis_topics_v1",
                parsed,
                request_context={
                    "allowed_evidence_log_ids": tuple(
                        message.chat_log_id
                        for message in request.messages
                        if not message.context_only
                    ),
                },
            )
        except (TaskOutputContractError, TaskBusinessValidationError):
            return self._failed(
                request,
                error_code="topic_contract_invalid",
                failure_category=FailureCategory.CONTRACT_VIOLATION,
            )

        message_by_id = {
            message.chat_log_id: message
            for message in request.messages
            if not message.context_only
        }
        candidates: list[GroupLearningCandidateWrite] = []
        evidence: list[GroupLearningEvidenceWrite] = []
        seen_fingerprints: set[str] = set()
        for topic in validated["topics"]:
            content = str(topic["topic"]).strip()
            meaning = str(topic["detail"]).strip()
            (
                normalized_key,
                fingerprint,
                content_hash,
                candidate_id,
            ) = group_learning_candidate_identity(
                chat_stream_id=request.chat_stream_id,
                candidate_type="topic",
                content=content,
                meaning=meaning,
            )
            if fingerprint in seen_fingerprints:
                return self._failed(
                    request,
                    error_code="topic_contract_invalid",
                    failure_category=(
                        FailureCategory.CONTRACT_VIOLATION
                    ),
                )
            seen_fingerprints.add(fingerprint)
            candidates.append(GroupLearningCandidateWrite(
                candidate_id=candidate_id,
                chat_stream_id=request.chat_stream_id,
                candidate_type="topic",
                content=content,
                meaning=meaning,
                normalized_key=normalized_key,
                fingerprint=fingerprint,
                content_hash=content_hash,
                source="model",
                rule_id="",
                rule_version=0,
                source_run_id=request.run_id,
            ))
            for raw_log_id in topic["evidence_log_ids"]:
                chat_log_id = int(raw_log_id)
                source_message = message_by_id[chat_log_id]
                evidence_id, evidence_hash = (
                    group_learning_evidence_identity(
                        candidate_id=candidate_id,
                        chat_log_id=chat_log_id,
                        sender_id=source_message.sender_id,
                        evidence_kind="message",
                        content_sha256=_sha256(
                            source_message.content
                        ),
                    )
                )
                evidence.append(GroupLearningEvidenceWrite(
                    evidence_id=evidence_id,
                    candidate_id=candidate_id,
                    chat_log_id=chat_log_id,
                    sender_id=source_message.sender_id,
                    source_run_id=request.run_id,
                    batch_id=request.run_id,
                    evidence_hash=evidence_hash,
                    evidence_kind="message",
                ))
        try:
            self.command_repository.record_model_observation(
                run_id=request.run_id,
                observations=(),
                discoveries=tuple(candidates),
                discovery_evidence=tuple(evidence),
                metrics=metrics,
                observed_at=db_now_naive(),
            )
            self.command_repository.commit()
        except BaseException:
            self.command_repository.rollback()
            raise
        return None

    def _settle_report_only(
        self,
        request: GroupLearningCandidateBatchRequest,
        *,
        analysis: Mapping[str, object],
    ) -> GroupLearningProcessingOutcome:
        if any(
            not isinstance(analysis.get(aspect_id), Mapping)
            for aspect_id in request.aspects
        ):
            return self._failed(
                request,
                error_code="report_analysis_missing",
                failure_category=FailureCategory.CONTRACT_VIOLATION,
            )
        try:
            self.command_repository.complete_report_only_run(
                run_id=request.run_id,
                completed_at=db_now_naive(),
            )
            self.command_repository.commit()
        except BaseException:
            self.command_repository.rollback()
            raise
        return self._settle_governance(request)

    def _settle_governance(
        self,
        request: GroupLearningCandidateBatchRequest,
    ) -> GroupLearningProcessingOutcome:
        result = self.governance_service.settle_model_run(
            run_id=request.run_id,
            chat_stream_id=request.chat_stream_id,
        )
        if result.status in {"succeeded", "replayed"}:
            return GroupLearningProcessingOutcome.succeeded(
                run_id=request.run_id
            )
        return self._failed(
            request,
            error_code="group_learning_disabled",
            failure_category=FailureCategory.AUTHORIZATION,
        )

    def process(
        self,
        request: GroupLearningCandidateBatchRequest,
    ) -> GroupLearningProcessingOutcome:
        if not self._enabled():
            return self._failed(
                request,
                error_code="group_learning_disabled",
                failure_category=FailureCategory.AUTHORIZATION,
            )
        analysis: Mapping[str, object] = {}
        if "topics" in request.aspects:
            self.candidate_service.persist_rule_candidates(request)
            topic_analysis, failure = self._execute_topics(request)
            if failure is not None:
                return failure
            analysis = topic_analysis or {}
        if set(request.aspects) <= _REPORT_ONLY_ASPECTS:
            return self._failed(
                request,
                error_code="report_analysis_required",
                failure_category=FailureCategory.CONTRACT_VIOLATION,
            )
        return self.process_with_analysis(request, analysis=analysis)

    def process_with_analysis(
        self,
        request: GroupLearningCandidateBatchRequest,
        *,
        analysis: Mapping[str, object],
    ) -> GroupLearningProcessingOutcome:
        if not self._enabled():
            return self._failed(
                request,
                error_code="group_learning_disabled",
                failure_category=FailureCategory.AUTHORIZATION,
            )
        persisted = self.candidate_service.persist_rule_candidates(
            request
        )
        existing_run = self.query_repository.get_run(request.run_id)
        if (
            existing_run is not None
            and existing_run.mode == "active"
            and existing_run.status == "succeeded"
        ):
            return GroupLearningProcessingOutcome.succeeded(
                run_id=request.run_id
            )
        if set(request.aspects) <= _REPORT_ONLY_ASPECTS:
            return self._settle_report_only(
                request,
                analysis=analysis,
            )

        model_aspects = tuple(
            aspect_id
            for aspect_id in request.aspects
            if aspect_id in _MODEL_MEMORY_ASPECTS
        )
        if model_aspects:
            review = self.review_service.review(
                GroupLearningModelReviewRequest(
                    run_id=request.run_id,
                    chat_stream_id=request.chat_stream_id,
                    aspects=model_aspects,
                    candidate_ids=persisted.candidate_ids,
                    messages=request.messages,
                )
            )
            if review.status == "failed":
                return self._failed(
                    request,
                    error_code=(
                        review.failure_code
                        or "group_learning_review_failed"
                    ),
                    retryable=review.retryable,
                    failure_category=review.failure_category,
                )

        if "topics" in request.aspects:
            topic_failure = self._record_topics(
                request,
                analysis=analysis,
            )
            if topic_failure is not None:
                return topic_failure
        elif not model_aspects:
            return self._failed(
                request,
                error_code="no_long_term_aspect_selected",
                failure_category=FailureCategory.VALIDATION,
            )
        return self._settle_governance(request)


def build_group_learning_processor(
    session: Session,
    *,
    task_executor: Callable[[TaskInvocation], TaskResult] = execute_task,
    enabled: Callable[[], bool] | None = None,
) -> GroupLearningPipelineService:
    """在 Composition Root 中装配 SQL Adapter，不向调用方泄漏 ORM。"""

    enabled_check = enabled or (
        lambda: settings.get_bool("group_learning.enabled", False)
    )
    query_repository = SqlAlchemyGroupLearningQueryRepository(session)
    command_repository = SqlAlchemyGroupLearningCommandRepository(
        session
    )
    memory_repository = SqlAlchemyGroupMemoryRepository(session)
    return GroupLearningPipelineService(
        candidate_service=GroupLearningCandidateService(
            command_repository
        ),
        review_service=GroupLearningModelReviewService(
            query_repository=query_repository,
            command_repository=command_repository,
            group_memory_repository=memory_repository,
            task_executor=task_executor,
        ),
        governance_service=GroupLearningGovernanceService(
            repository=SqlAlchemyGroupLearningGovernanceRepository(
                session
            ),
            enabled=enabled_check,
        ),
        query_repository=query_repository,
        command_repository=command_repository,
        task_executor=task_executor,
        enabled=enabled_check,
    )


__all__ = [
    "GroupLearningPipelineService",
    "build_group_learning_processor",
]
