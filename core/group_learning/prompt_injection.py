"""正式群体记忆进入 Prompt 前的确定性治理门禁。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from core.group_learning.aspects import list_group_analysis_aspects


PROMPT_INJECTABLE_MEMORY_TYPES = frozenset(
    descriptor.memory_type
    for descriptor in list_group_analysis_aspects()
    if descriptor.prompt_injectable
)

_PROMPT_REVISION_FIELDS = (
    "id",
    "chat_stream_id",
    "group_id",
    "memory_type",
    "content",
    "content_hash",
    "evidence_log_ids_json",
    "confidence",
    "evidence_count",
    "first_seen",
    "last_seen",
    "updated_at",
    "decay_score",
    "status",
    "inject_policy",
    "disabled_reason",
    "rejected_reason",
    "merged_into_id",
    "source",
    "meta_json",
    "approval_source",
    "governance_mode",
    "approved_content_hash",
    "model_review_run_id",
    "model_contract_version",
    "human_reviewer_id",
    "human_reviewed_at",
    "human_action",
    "conflict_group_id",
    "version",
)


@dataclass(frozen=True, slots=True)
class GroupMemoryPromptInjectionDecision:
    """一条正式群体记忆的 Prompt 注入准入结果。"""

    eligible: bool
    reason_code: str = ""


def _field(memory: object, name: str, default: Any = None) -> Any:
    if isinstance(memory, Mapping):
        return memory.get(name, default)
    return getattr(memory, name, default)


def _evidence_ids(memory: object) -> tuple[int, ...]:
    try:
        raw = json.loads(
            str(_field(memory, "evidence_log_ids_json", "") or "[]")
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(raw, list):
        return ()
    result: list[int] = []
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in result:
            result.append(value)
    return tuple(result)


def build_group_memory_prompt_revision(
    memories: tuple[object, ...] | list[object],
) -> str:
    """生成覆盖 Prompt 准入、排序与渲染输入的确定性版本摘要。"""

    rows = [
        [
            _revision_value(_field(memory, field_name))
            for field_name in _PROMPT_REVISION_FIELDS
        ]
        for memory in memories
    ]
    rows.sort(key=lambda row: (str(row[0]), str(row[4]), str(row[5])))
    payload = json.dumps(
        rows,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _revision_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def evaluate_group_memory_prompt_injection(
    memory: object,
) -> GroupMemoryPromptInjectionDecision:
    """验证状态、类型、审核来源和来源专属 provenance。

    该 Policy 只处理确定性准入，不做语义相关性排序。模型审核的记忆必须携带
    Evidence；人工创建或人工接受的记忆允许没有模型 Evidence。
    """

    if str(_field(memory, "status", "") or "") != "active":
        return GroupMemoryPromptInjectionDecision(False, "inactive_status")

    inject_policy = str(
        _field(memory, "inject_policy", "") or "auto"
    )
    if inject_policy == "manual_only":
        return GroupMemoryPromptInjectionDecision(False, "manual_only")
    if inject_policy == "never":
        return GroupMemoryPromptInjectionDecision(False, "never")
    if inject_policy != "auto":
        return GroupMemoryPromptInjectionDecision(False, "invalid_policy")

    memory_type = str(_field(memory, "memory_type", "") or "")
    if memory_type not in PROMPT_INJECTABLE_MEMORY_TYPES:
        return GroupMemoryPromptInjectionDecision(
            False,
            "memory_type_not_prompt_injectable",
        )
    if str(_field(memory, "conflict_group_id", "") or ""):
        return GroupMemoryPromptInjectionDecision(False, "conflict")

    approval_source = str(
        _field(memory, "approval_source", "") or ""
    )
    if not approval_source:
        return GroupMemoryPromptInjectionDecision(
            False,
            "approval_source_required",
        )
    if not str(_field(memory, "approved_content_hash", "") or ""):
        return GroupMemoryPromptInjectionDecision(
            False,
            "approved_content_hash_required",
        )

    if approval_source == "model":
        if str(_field(memory, "governance_mode", "") or "") != "automatic":
            return GroupMemoryPromptInjectionDecision(
                False,
                "model_governance_mode_required",
            )
        if not str(_field(memory, "model_review_run_id", "") or ""):
            return GroupMemoryPromptInjectionDecision(
                False,
                "model_review_run_required",
            )
        if not str(_field(memory, "model_contract_version", "") or ""):
            return GroupMemoryPromptInjectionDecision(
                False,
                "model_contract_version_required",
            )
        if not _evidence_ids(memory):
            return GroupMemoryPromptInjectionDecision(False, "no_evidence")
    elif approval_source == "human":
        if (
            str(_field(memory, "governance_mode", "") or "")
            != "human_managed"
        ):
            return GroupMemoryPromptInjectionDecision(
                False,
                "human_governance_mode_required",
            )
        if not str(_field(memory, "human_reviewer_id", "") or ""):
            return GroupMemoryPromptInjectionDecision(
                False,
                "human_reviewer_required",
            )
        if _field(memory, "human_reviewed_at") is None:
            return GroupMemoryPromptInjectionDecision(
                False,
                "human_review_time_required",
            )
        if not str(_field(memory, "human_action", "") or ""):
            return GroupMemoryPromptInjectionDecision(
                False,
                "human_action_required",
            )
    else:
        return GroupMemoryPromptInjectionDecision(
            False,
            "approval_source_invalid",
        )

    if float(_field(memory, "confidence", 0) or 0) < 0.55:
        return GroupMemoryPromptInjectionDecision(False, "low_confidence")
    if float(_field(memory, "decay_score", 0) or 0) < 0.3:
        return GroupMemoryPromptInjectionDecision(False, "low_decay")
    return GroupMemoryPromptInjectionDecision(True)


__all__ = [
    "build_group_memory_prompt_revision",
    "GroupMemoryPromptInjectionDecision",
    "PROMPT_INJECTABLE_MEMORY_TYPES",
    "evaluate_group_memory_prompt_injection",
]
