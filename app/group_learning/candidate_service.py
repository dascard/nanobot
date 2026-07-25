"""阶段 7B 群学习规则候选生成与原子持久化服务。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from collections.abc import Callable
from typing import Final

from core.chat_stream_identity import (
    ChatStreamIdentityError,
    parse_canonical_chat_stream_id,
)
from core.db.group_learning_contracts import (
    GroupLearningBatchPersistResult,
    GroupLearningBatchWrite,
    GroupLearningCandidateWrite,
    GroupLearningCommandRepositoryPort,
    GroupLearningEvidenceWrite,
    GroupLearningRunWrite,
)
from core.group_learning import (
    GROUP_ANALYSIS_ASPECT_REGISTRY,
    LEARNING_SIGNAL_RULE_REGISTRY,
    canonicalize_learning_text,
    dry_run_learning_rules,
    validate_aspect_selection,
)


MAX_BATCH_MESSAGES: Final = 500
MAX_BATCH_CHARS: Final = 60_000
MAX_BATCH_CANDIDATES: Final = 100
MAX_BATCH_EVIDENCE: Final = 500
_TRIGGERS = frozenset({"schedule", "manual", "tool", "migration_review"})


def _sha256(*parts: object) -> str:
    payload = "\0".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def group_learning_candidate_identity(
    *,
    chat_stream_id: str,
    candidate_type: str,
    content: str,
    meaning: str,
) -> tuple[str, str, str, str]:
    """返回 normalized key、fingerprint、content hash 和稳定 candidate ID。"""

    canonical_content = canonicalize_learning_text(content)
    canonical_meaning = canonicalize_learning_text(meaning)
    if not canonical_content:
        raise ValueError("candidate content 不能为空")
    normalized_key = canonical_content.casefold()
    normalized_meaning = canonical_meaning.casefold()
    fingerprint = _sha256(
        chat_stream_id,
        candidate_type,
        normalized_key,
        normalized_meaning,
    )
    content_hash = _sha256(canonical_content, canonical_meaning)
    return (
        normalized_key,
        fingerprint,
        content_hash,
        f"glc_{fingerprint[:48]}",
    )


def group_learning_evidence_identity(
    *,
    candidate_id: str,
    chat_log_id: int,
    sender_id: str,
    evidence_kind: str,
    content_sha256: str = "",
) -> tuple[str, str]:
    evidence_hash = _sha256(
        candidate_id,
        chat_log_id,
        sender_id,
        evidence_kind,
        content_sha256,
    )
    return f"gle_{evidence_hash[:48]}", evidence_hash


@dataclass(frozen=True, slots=True)
class GroupLearningMessage:
    chat_log_id: int
    sender_id: str
    content: str
    context_only: bool = False

    def __post_init__(self) -> None:
        if (
            isinstance(self.chat_log_id, bool)
            or not isinstance(self.chat_log_id, int)
            or self.chat_log_id <= 0
        ):
            raise ValueError("chat_log_id 必须为正整数")
        sender_id = str(self.sender_id or "").strip()
        content = str(self.content or "").strip()
        if not sender_id or len(sender_id) > 255:
            raise ValueError("sender_id 无效")
        if not content or len(content) > 2000:
            raise ValueError("群学习单条消息必须为 1..2000 字符")
        object.__setattr__(self, "sender_id", sender_id)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "context_only", bool(self.context_only))


@dataclass(frozen=True, slots=True)
class GroupLearningCandidateBatchRequest:
    run_id: str
    idempotency_key: str
    chat_stream_id: str
    trigger: str
    aspects: tuple[str, ...]
    cursor_start_chat_log_id: int
    cursor_end_chat_log_id: int
    context_start_chat_log_id: int
    context_end_chat_log_id: int
    messages: tuple[GroupLearningMessage, ...]
    trace_id: str = ""
    job_id: str = ""

    def __post_init__(self) -> None:
        run_id = str(self.run_id or "").strip()
        idempotency_key = str(self.idempotency_key or "").strip()
        chat_stream_id = str(self.chat_stream_id or "").strip()
        trigger = str(self.trigger or "").strip()
        if not run_id or len(run_id) > 64:
            raise ValueError("run_id 无效")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key 无效")
        try:
            identity = parse_canonical_chat_stream_id(chat_stream_id)
        except ChatStreamIdentityError as exc:
            raise ValueError(
                "群学习只接受 canonical chat_stream_id"
            ) from exc
        if identity.chat_type != "group":
            raise ValueError("群学习只接受 canonical group session")
        if trigger not in _TRIGGERS:
            raise ValueError("群学习 trigger 无效")
        aspects = validate_aspect_selection(self.aspects)
        messages = tuple(self.messages)
        if not messages or len(messages) > MAX_BATCH_MESSAGES:
            raise ValueError("群学习批次消息数无效")
        if sum(len(message.content) for message in messages) > MAX_BATCH_CHARS:
            raise ValueError("群学习批次字符数超限")
        message_ids = tuple(message.chat_log_id for message in messages)
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("群学习批次 chat_log_id 不能重复")

        cursor_values = (
            self.cursor_start_chat_log_id,
            self.cursor_end_chat_log_id,
            self.context_start_chat_log_id,
            self.context_end_chat_log_id,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in cursor_values
        ):
            raise ValueError("群学习游标必须是非负整数")
        if self.cursor_end_chat_log_id < self.cursor_start_chat_log_id:
            raise ValueError("群学习扫描游标范围无效")
        if (
            self.context_end_chat_log_id
            and self.context_end_chat_log_id
            < self.context_start_chat_log_id
        ):
            raise ValueError("群学习上下文游标范围无效")
        for message in messages:
            if message.context_only:
                if not (
                    self.context_start_chat_log_id
                    <= message.chat_log_id
                    <= self.context_end_chat_log_id
                ):
                    raise ValueError("context-only 消息不在上下文范围")
            elif not (
                self.cursor_start_chat_log_id
                < message.chat_log_id
                <= self.cursor_end_chat_log_id
            ):
                raise ValueError("正式 evidence 消息不在扫描范围")
        if not any(not message.context_only for message in messages):
            raise ValueError("群学习批次没有新增正式消息")

        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "idempotency_key", idempotency_key)
        object.__setattr__(self, "chat_stream_id", identity.chat_stream_id)
        object.__setattr__(self, "trigger", trigger)
        object.__setattr__(self, "aspects", aspects)
        object.__setattr__(self, "messages", messages)
        object.__setattr__(
            self,
            "trace_id",
            str(self.trace_id or "").strip()[:64],
        )
        object.__setattr__(
            self,
            "job_id",
            str(self.job_id or "").strip()[:64],
        )


def _selected_memory_types(aspects: tuple[str, ...]) -> frozenset[str]:
    return frozenset(
        descriptor.memory_type
        for aspect_id in aspects
        if (
            descriptor := GROUP_ANALYSIS_ASPECT_REGISTRY.require(
                aspect_id
            )
        ).writes_long_term_memory
    )


class GroupLearningCandidateService:
    """把确定性规则信号写为候选；不提供正式记忆写入。"""

    def __init__(
        self,
        repository: GroupLearningCommandRepositoryPort,
        *,
        rule_ids_for_session: (
            Callable[[str], tuple[str, ...]] | None
        ) = None,
    ) -> None:
        self.repository = repository
        if rule_ids_for_session is None:
            from core.group_learning.rule_activation import (
                effective_group_learning_rule_ids,
            )

            rule_ids_for_session = effective_group_learning_rule_ids
        if not callable(rule_ids_for_session):
            raise TypeError("rule_ids_for_session 必须可调用")
        self._rule_ids_for_session = rule_ids_for_session

    def persist_batch(
        self,
        write: GroupLearningBatchWrite,
    ) -> GroupLearningBatchPersistResult:
        try:
            result = self.repository.persist_candidate_batch(write)
            self.repository.commit()
            return result
        except BaseException:
            self.repository.rollback()
            raise

    def persist_rule_candidates(
        self,
        request: GroupLearningCandidateBatchRequest,
    ) -> GroupLearningBatchPersistResult:
        selected_types = _selected_memory_types(request.aspects)
        candidate_writes: dict[
            str,
            GroupLearningCandidateWrite,
        ] = {}
        evidence_writes: dict[
            tuple[str, int],
            GroupLearningEvidenceWrite,
        ] = {}
        enabled_rule_ids = self._rule_ids_for_session(
            request.chat_stream_id
        )
        eligible_messages = tuple(
            message
            for message in request.messages
            if not message.context_only
        )
        for message in eligible_messages:
            dry_run = dry_run_learning_rules(
                message.content,
                rule_ids=enabled_rule_ids,
            )
            for match in dry_run.matches:
                if match.candidate_type not in selected_types:
                    continue
                (
                    normalized_key,
                    fingerprint,
                    content_hash,
                    candidate_id,
                ) = group_learning_candidate_identity(
                    chat_stream_id=request.chat_stream_id,
                    candidate_type=match.candidate_type,
                    content=match.canonical_content,
                    meaning=match.meaning,
                )
                candidate_writes.setdefault(
                    fingerprint,
                    GroupLearningCandidateWrite(
                        candidate_id=candidate_id,
                        chat_stream_id=request.chat_stream_id,
                        candidate_type=match.candidate_type,
                        content=match.canonical_content,
                        meaning=match.meaning,
                        normalized_key=normalized_key,
                        fingerprint=fingerprint,
                        content_hash=content_hash,
                        source="rule",
                        rule_id=match.rule_id,
                        rule_version=match.rule_version,
                        source_run_id=request.run_id,
                    ),
                )
                evidence_kind = (
                    "explicit_definition"
                    if match.candidate_type == "slang"
                    and bool(match.meaning)
                    else (
                        "repeated_usage"
                        if match.candidate_type == "expression"
                        else "message"
                    )
                )
                evidence_id, evidence_hash = (
                    group_learning_evidence_identity(
                        candidate_id=candidate_id,
                        chat_log_id=message.chat_log_id,
                        sender_id=message.sender_id,
                        evidence_kind=evidence_kind,
                        content_sha256=_sha256(message.content),
                    )
                )
                evidence_writes.setdefault(
                    (candidate_id, message.chat_log_id),
                    GroupLearningEvidenceWrite(
                        evidence_id=evidence_id,
                        candidate_id=candidate_id,
                        chat_log_id=message.chat_log_id,
                        sender_id=message.sender_id,
                        source_run_id=request.run_id,
                        batch_id=request.run_id,
                        evidence_hash=evidence_hash,
                        evidence_kind=evidence_kind,
                    ),
                )
                if len(candidate_writes) > MAX_BATCH_CANDIDATES:
                    raise ValueError("群学习候选数超限")
                if len(evidence_writes) > MAX_BATCH_EVIDENCE:
                    raise ValueError("群学习 evidence 数超限")

        batch = GroupLearningBatchWrite(
            run=GroupLearningRunWrite(
                run_id=request.run_id,
                idempotency_key=request.idempotency_key,
                chat_stream_id=request.chat_stream_id,
                trigger=request.trigger,
                selected_aspects_json=json.dumps(
                    request.aspects,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                cursor_start_chat_log_id=(
                    request.cursor_start_chat_log_id
                ),
                cursor_end_chat_log_id=request.cursor_end_chat_log_id,
                context_start_chat_log_id=(
                    request.context_start_chat_log_id
                ),
                context_end_chat_log_id=(
                    request.context_end_chat_log_id
                ),
                rules_generation=(
                    LEARNING_SIGNAL_RULE_REGISTRY.generation
                ),
                raw_message_count=len(request.messages),
                cleaned_message_count=len(request.messages),
                eligible_message_count=len(eligible_messages),
                trace_id=request.trace_id,
                job_id=request.job_id,
            ),
            candidates=tuple(candidate_writes.values()),
            evidence=tuple(evidence_writes.values()),
        )
        return self.persist_batch(batch)


__all__ = [
    "GroupLearningCandidateBatchRequest",
    "GroupLearningCandidateService",
    "GroupLearningMessage",
    "MAX_BATCH_CANDIDATES",
    "MAX_BATCH_CHARS",
    "MAX_BATCH_EVIDENCE",
    "MAX_BATCH_MESSAGES",
    "group_learning_candidate_identity",
    "group_learning_evidence_identity",
]
