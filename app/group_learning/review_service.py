"""阶段 7B 群学习模型观察与人工审核应用服务。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from types import MappingProxyType

from app.group_learning.candidate_service import (
    GroupLearningMessage,
    group_learning_candidate_identity,
    group_learning_evidence_identity,
)
from core.chat_stream_identity import (
    ChatStreamIdentityError,
    parse_canonical_chat_stream_id,
)
from core.db.group_learning_contracts import (
    GroupLearningCandidateWrite,
    GroupLearningCommandRepositoryPort,
    GroupLearningEvidenceWrite,
    GroupLearningHumanReviewWrite,
    GroupLearningObservationMetrics,
    GroupLearningObservationWrite,
    GroupLearningQueryRepositoryPort,
)
from core.db.group_memory_contracts import GroupMemoryQueryRepositoryPort
from core.group_learning import (
    GROUP_ANALYSIS_ASPECT_REGISTRY,
    evidence_policy_for,
    validate_aspect_selection,
)
from core.resilience import FailureCategory
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
from core.token_utils import estimate_tokens


_MODEL_REVIEW_TYPES = frozenset({"expression", "slang", "style"})
_MAX_REVIEW_CANDIDATES = 40
_MAX_REVIEW_MESSAGES = 500
_MAX_REVIEW_CHARS = 60_000


def _sha256(*parts: object) -> str:
    return hashlib.sha256(
        "\0".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()


def _usage_count(value: object) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if (
        type(value) is float
        and value.is_integer()
        and value >= 0
    ):
        return int(value)
    return None


@dataclass(frozen=True, slots=True)
class GroupLearningModelReviewRequest:
    run_id: str
    chat_stream_id: str
    aspects: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    messages: tuple[GroupLearningMessage, ...]
    nearby_memory_ids: Mapping[
        str,
        tuple[int, ...],
    ] = field(default_factory=dict)

    def __post_init__(self) -> None:
        run_id = str(self.run_id or "").strip()
        chat_stream_id = str(self.chat_stream_id or "").strip()
        if not run_id or len(run_id) > 64:
            raise ValueError("群学习审核 run_id 无效")
        try:
            identity = parse_canonical_chat_stream_id(chat_stream_id)
        except ChatStreamIdentityError as exc:
            raise ValueError(
                "群学习审核只接受 canonical chat_stream_id"
            ) from exc
        if identity.chat_type != "group":
            raise ValueError("群学习审核只接受 canonical group session")
        aspects = validate_aspect_selection(self.aspects)
        selected_types = {
            GROUP_ANALYSIS_ASPECT_REGISTRY.require(
                aspect_id
            ).memory_type
            for aspect_id in aspects
        } & _MODEL_REVIEW_TYPES
        if not selected_types:
            raise ValueError("群学习审核未选择可审核的长期方面")
        candidate_ids = tuple(
            str(item or "").strip()
            for item in self.candidate_ids
        )
        if (
            len(candidate_ids) > _MAX_REVIEW_CANDIDATES
            or any(not item for item in candidate_ids)
            or len(candidate_ids) != len(set(candidate_ids))
        ):
            raise ValueError("群学习审核 candidate_ids 无效")
        messages = tuple(self.messages)
        if (
            not messages
            or len(messages) > _MAX_REVIEW_MESSAGES
            or sum(len(item.content) for item in messages)
            > _MAX_REVIEW_CHARS
        ):
            raise ValueError("群学习审核消息预算无效")
        log_ids = tuple(item.chat_log_id for item in messages)
        if len(log_ids) != len(set(log_ids)):
            raise ValueError("群学习审核消息 ID 不能重复")
        raw_nearby = dict(self.nearby_memory_ids)
        if set(raw_nearby) - set(candidate_ids):
            raise ValueError("近似记忆映射包含当前批次外候选")
        nearby: dict[str, tuple[int, ...]] = {}
        for candidate_id, values in raw_nearby.items():
            normalized = tuple(dict.fromkeys(values))
            if (
                len(normalized) > 8
                or any(
                    type(item) is not int or item <= 0
                    for item in normalized
                )
            ):
                raise ValueError("近似记忆 ID 列表无效")
            nearby[candidate_id] = normalized
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(
            self,
            "chat_stream_id",
            identity.chat_stream_id,
        )
        object.__setattr__(self, "aspects", aspects)
        object.__setattr__(self, "candidate_ids", candidate_ids)
        object.__setattr__(self, "messages", messages)
        object.__setattr__(
            self,
            "nearby_memory_ids",
            MappingProxyType(nearby),
        )


@dataclass(frozen=True, slots=True)
class GroupLearningReviewOutcome:
    run_id: str
    status: str
    reviewed_count: int = 0
    discovery_count: int = 0
    failure_code: str = ""
    task_run_id: str = ""
    retryable: bool = False
    failure_category: FailureCategory = FailureCategory.PERMANENT


class GroupLearningModelReviewService:
    """调用统一 Task Runtime，只记录旁路提案，不晋级正式记忆。"""

    def __init__(
        self,
        *,
        query_repository: GroupLearningQueryRepositoryPort,
        command_repository: GroupLearningCommandRepositoryPort,
        group_memory_repository: GroupMemoryQueryRepositoryPort,
        task_executor: Callable[[TaskInvocation], TaskResult] = execute_task,
    ) -> None:
        self.query_repository = query_repository
        self.command_repository = command_repository
        self.group_memory_repository = group_memory_repository
        self.task_executor = task_executor

    @staticmethod
    def _metrics(
        result: TaskResult,
        *,
        input_text: str,
    ) -> GroupLearningObservationMetrics:
        usage = result.usage
        input_tokens = _usage_count(usage.get("prompt_tokens"))
        if input_tokens is None:
            input_tokens = _usage_count(usage.get("input_tokens"))
        if input_tokens is None:
            input_tokens = estimate_tokens(input_text)
        output_tokens = _usage_count(
            usage.get("completion_tokens")
        )
        if output_tokens is None:
            output_tokens = _usage_count(usage.get("output_tokens"))
        if output_tokens is None:
            output_tokens = 0
        total_tokens = _usage_count(usage.get("total_tokens"))
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens
        cost_microusd = _usage_count(usage.get("cost_microusd"))
        if cost_microusd is None:
            cost_microusd = _usage_count(
                result.execution_metadata.get("cost_microusd")
            )
        return GroupLearningObservationMetrics(
            task_run_id=str(result.run_id or ""),
            contract_version=str(result.contract_version or ""),
            provider=str(result.provider or ""),
            model=str(result.model or ""),
            input_chars=len(input_text),
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

    def _record_failure(
        self,
        *,
        request: GroupLearningModelReviewRequest,
        error_code: str,
        metrics: GroupLearningObservationMetrics,
        retryable: bool = False,
        failure_category: FailureCategory = FailureCategory.PERMANENT,
    ) -> GroupLearningReviewOutcome:
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
        return GroupLearningReviewOutcome(
            run_id=request.run_id,
            status="failed",
            failure_code=error_code,
            task_run_id=metrics.task_run_id,
            retryable=retryable,
            failure_category=failure_category,
        )

    def _load_contract(
        self,
        request: GroupLearningModelReviewRequest,
    ) -> tuple[
        tuple[dict[str, object], ...],
        tuple[dict[str, object], ...],
        dict[str, str],
        dict[str, tuple[int, ...]],
        dict[str, str],
    ]:
        run = self.query_repository.get_run(request.run_id)
        if run is None:
            raise LookupError("group learning run not found")
        if (
            run.chat_stream_id != request.chat_stream_id
            or run.mode != "candidate_only"
        ):
            raise ValueError("群学习审核 run scope 不一致")

        eligible_messages = {
            message.chat_log_id: message
            for message in request.messages
            if not message.context_only
        }
        selected_types = {
            GROUP_ANALYSIS_ASPECT_REGISTRY.require(
                aspect_id
            ).memory_type
            for aspect_id in request.aspects
        } & _MODEL_REVIEW_TYPES
        candidate_cards: list[dict[str, object]] = []
        candidate_types: dict[str, str] = {}
        candidate_fingerprints: dict[str, str] = {}
        target_ids: dict[str, tuple[int, ...]] = {}
        nearby_cards: list[dict[str, object]] = []
        for candidate_id in request.candidate_ids:
            candidate = self.query_repository.get_candidate(candidate_id)
            if candidate is None:
                raise LookupError("group learning candidate not found")
            if candidate.approval_source == "human":
                raise ValueError("人工审核后的候选不得再次送模型")
            if (
                candidate.chat_stream_id != request.chat_stream_id
                or candidate.source_run_id != request.run_id
                or candidate.candidate_type not in selected_types
            ):
                raise ValueError("群学习审核 candidate scope 不一致")
            evidence = tuple(
                item
                for item in self.query_repository.list_evidence(
                    candidate_id=candidate_id,
                    limit=100,
                )
                if item.chat_log_id in eligible_messages
            )
            if not evidence:
                raise ValueError("群学习候选没有本批正式 evidence")
            candidate_cards.append({
                "candidate_id": candidate.candidate_id,
                "candidate_type": candidate.candidate_type,
                "content": candidate.content,
                "meaning": candidate.meaning,
                "rule_id": candidate.rule_id,
                "rule_version": candidate.rule_version,
                "evidence_log_ids": [
                    item.chat_log_id for item in evidence
                ],
                "evidence_policy": (
                    evidence_policy_for(
                        candidate.candidate_type
                    ).registry_payload()
                ),
            })
            candidate_types[candidate_id] = candidate.candidate_type
            candidate_fingerprints[candidate_id] = (
                candidate.fingerprint
            )

            validated_targets: list[int] = []
            for memory_id in request.nearby_memory_ids.get(
                candidate_id,
                (),
            ):
                memory = self.group_memory_repository.get_memory(
                    memory_id
                )
                if (
                    memory is None
                    or memory.chat_stream_id
                    != request.chat_stream_id
                    or memory.memory_type != candidate.candidate_type
                ):
                    raise ValueError(
                        "近似记忆必须属于当前会话和同一类型"
                    )
                validated_targets.append(memory.id)
                nearby_cards.append({
                    "candidate_id": candidate_id,
                    "memory_id": memory.id,
                    "memory_type": memory.memory_type,
                    "content": memory.content[:240],
                    "status": memory.status,
                })
            target_ids[candidate_id] = tuple(validated_targets)
        return (
            tuple(candidate_cards),
            tuple(nearby_cards),
            candidate_types,
            target_ids,
            candidate_fingerprints,
        )

    def review(
        self,
        request: GroupLearningModelReviewRequest,
    ) -> GroupLearningReviewOutcome:
        run = self.query_repository.get_run(request.run_id)
        if run is not None and run.status == "succeeded":
            return GroupLearningReviewOutcome(
                run_id=request.run_id,
                status="replayed",
                reviewed_count=int(run.accepted_count or 0)
                + int(run.rejected_count or 0)
                + int(run.conflict_count or 0),
                task_run_id=run.task_run_id,
            )
        (
            candidate_cards,
            nearby_cards,
            candidate_types,
            target_ids,
            candidate_fingerprints,
        ) = self._load_contract(request)
        message_cards = tuple({
            "chat_log_id": message.chat_log_id,
            "sender_id": message.sender_id,
            "content": message.content,
            "context_only": message.context_only,
        } for message in request.messages)
        payload = {
            "selected_aspects": list(request.aspects),
            "messages": message_cards,
            "rule_candidates": candidate_cards,
            "nearby_memories": nearby_cards,
        }
        input_text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        allowed_evidence_log_ids = tuple(
            message.chat_log_id
            for message in request.messages
            if not message.context_only
        )
        selected_candidate_types = tuple(sorted(
            set(candidate_types.values())
            | {
                GROUP_ANALYSIS_ASPECT_REGISTRY.require(
                    aspect_id
                ).memory_type
                for aspect_id in request.aspects
                if GROUP_ANALYSIS_ASPECT_REGISTRY.require(
                    aspect_id
                ).memory_type in _MODEL_REVIEW_TYPES
            }
        ))
        request_context = {
            "allowed_candidate_ids": request.candidate_ids,
            "candidate_types": candidate_types,
            "allowed_evidence_log_ids": allowed_evidence_log_ids,
            "allowed_target_memory_ids": target_ids,
            "selected_candidate_types": selected_candidate_types,
        }
        result = self.task_executor(TaskInvocation(
            invocation_id="group_memory_learning",
            route_key="group_memory_learning",
            input_values={"message": input_text},
            request_context=request_context,
            idempotency_key=(
                f"group_memory_learning:{request.run_id}"
            ),
            timeout_budget_seconds=90.0,
        ))
        metrics = self._metrics(result, input_text=input_text)
        if not result.ok:
            failure_code = (
                result.failure.code.value
                if result.failure is not None
                else "provider_error"
            )
            return self._record_failure(
                request=request,
                error_code=failure_code,
                metrics=metrics,
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

        parsed = thaw_task_value(result.parsed_value)
        try:
            validated = validate_task_business_output(
                "group_memory_learning_v1",
                parsed,
                request_context=request_context,
            )
        except TaskBusinessValidationError:
            return self._record_failure(
                request=request,
                error_code="business_validation_failed",
                metrics=metrics,
                failure_category=FailureCategory.CONTRACT_VIOLATION,
            )
        message_by_id = {
            message.chat_log_id: message
            for message in request.messages
            if not message.context_only
        }
        observations: list[GroupLearningObservationWrite] = []
        seen_fingerprints = set(candidate_fingerprints.values())
        for review in validated["reviews"]:
            content = str(review["content"]).strip()
            meaning = str(review["meaning"]).strip()
            _key, reviewed_fingerprint, _hash, _candidate_id = (
                group_learning_candidate_identity(
                    chat_stream_id=request.chat_stream_id,
                    candidate_type=str(review["candidate_type"]),
                    content=content,
                    meaning=meaning,
                )
            )
            seen_fingerprints.add(reviewed_fingerprint)
            observations.append(GroupLearningObservationWrite(
                candidate_id=str(review["candidate_id"]),
                action=str(review["action"]),
                reviewed_content=content,
                reviewed_meaning=meaning,
                reviewed_content_hash=_sha256(content, meaning),
                target_memory_id=review["target_memory_id"],
                reason_hash=_sha256(str(review["reason"])),
            ))

        discoveries: list[GroupLearningCandidateWrite] = []
        discovery_evidence: list[GroupLearningEvidenceWrite] = []
        for discovery in validated["discoveries"]:
            candidate_type = str(discovery["candidate_type"])
            content = str(discovery["content"]).strip()
            meaning = str(discovery["meaning"]).strip()
            (
                normalized_key,
                fingerprint,
                content_hash,
                candidate_id,
            ) = group_learning_candidate_identity(
                chat_stream_id=request.chat_stream_id,
                candidate_type=candidate_type,
                content=content,
                meaning=meaning,
            )
            if fingerprint in seen_fingerprints:
                return self._record_failure(
                    request=request,
                    error_code="business_validation_failed",
                    metrics=metrics,
                    failure_category=(
                        FailureCategory.CONTRACT_VIOLATION
                    ),
                )
            seen_fingerprints.add(fingerprint)
            discoveries.append(GroupLearningCandidateWrite(
                candidate_id=candidate_id,
                chat_stream_id=request.chat_stream_id,
                candidate_type=candidate_type,
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
            for chat_log_id in discovery["evidence_log_ids"]:
                source_message = message_by_id[int(chat_log_id)]
                evidence_kind = (
                    "repeated_usage"
                    if candidate_type == "expression"
                    else "message"
                )
                evidence_id, evidence_hash = (
                    group_learning_evidence_identity(
                        candidate_id=candidate_id,
                        chat_log_id=int(chat_log_id),
                        sender_id=source_message.sender_id,
                        evidence_kind=evidence_kind,
                        content_sha256=_sha256(
                            source_message.content
                        ),
                    )
                )
                discovery_evidence.append(
                    GroupLearningEvidenceWrite(
                        evidence_id=evidence_id,
                        candidate_id=candidate_id,
                        chat_log_id=int(chat_log_id),
                        sender_id=source_message.sender_id,
                        source_run_id=request.run_id,
                        batch_id=request.run_id,
                        evidence_hash=evidence_hash,
                        evidence_kind=evidence_kind,
                    )
                )
        try:
            self.command_repository.record_model_observation(
                run_id=request.run_id,
                observations=tuple(observations),
                discoveries=tuple(discoveries),
                discovery_evidence=tuple(discovery_evidence),
                metrics=metrics,
                observed_at=db_now_naive(),
            )
            self.command_repository.commit()
        except BaseException:
            self.command_repository.rollback()
            raise
        return GroupLearningReviewOutcome(
            run_id=request.run_id,
            status="observed",
            reviewed_count=len(observations),
            discovery_count=len(discoveries),
            task_run_id=metrics.task_run_id,
        )


class GroupLearningHumanReviewService:
    """人工审核只改候选；阶段 7B 不创建正式记忆。"""

    def __init__(
        self,
        *,
        query_repository: GroupLearningQueryRepositoryPort,
        command_repository: GroupLearningCommandRepositoryPort,
    ) -> None:
        self.query_repository = query_repository
        self.command_repository = command_repository

    def review(
        self,
        *,
        candidate_id: str,
        reviewer_id: str,
        action: str,
        reviewed_content: str | None = None,
        reviewed_meaning: str | None = None,
        reviewed_at: datetime | None = None,
    ):
        candidate = self.query_repository.get_candidate(candidate_id)
        if candidate is None:
            raise LookupError("group learning candidate not found")
        content = (
            candidate.content
            if reviewed_content is None
            else str(reviewed_content).strip()
        )
        meaning = (
            candidate.meaning
            if reviewed_meaning is None
            else str(reviewed_meaning).strip()
        )
        write = GroupLearningHumanReviewWrite(
            candidate_id=candidate.candidate_id,
            reviewer_id=str(reviewer_id or "").strip(),
            action=str(action or "").strip(),
            reviewed_content=content,
            reviewed_meaning=meaning,
            reviewed_content_hash=_sha256(content, meaning),
            reviewed_at=reviewed_at or db_now_naive(),
        )
        try:
            result = self.command_repository.apply_human_review(write)
            self.command_repository.commit()
            return result
        except BaseException:
            self.command_repository.rollback()
            raise


__all__ = [
    "GroupLearningHumanReviewService",
    "GroupLearningModelReviewRequest",
    "GroupLearningModelReviewService",
    "GroupLearningReviewOutcome",
]
