"""群学习 Evidence Policy 的冻结事实源。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.group_learning.aspects import GROUP_LEARNING_MEMORY_TYPES
from core.registry import RegistryBuilder, RegistrySnapshot
from core.registry.validation import validate_identifier


GROUP_LEARNING_EVIDENCE_KINDS = (
    "message",
    "explicit_definition",
    "repeated_usage",
    "human_selected",
)


@dataclass(frozen=True, slots=True)
class EvidencePolicyDescriptor:
    policy_id: str
    candidate_type: str
    min_evidence_count: int
    min_sender_count: int
    explicit_evidence_kinds: tuple[str, ...] = ()
    min_explicit_evidence_count: int = 0
    same_sender_cross_batch_min_hits: int = 0
    same_sender_cross_batch_min_batches: int = 0
    version: int = 1
    owner_module: str = "core.group_learning"

    def __post_init__(self) -> None:
        validate_identifier(
            self.policy_id,
            field_name="evidence_policy.policy_id",
        )
        validate_identifier(
            self.owner_module,
            field_name="evidence_policy.owner_module",
        )
        if self.candidate_type not in GROUP_LEARNING_MEMORY_TYPES:
            raise ValueError("Evidence Policy candidate_type 无效")
        numeric_values = (
            self.min_evidence_count,
            self.min_sender_count,
            self.min_explicit_evidence_count,
            self.same_sender_cross_batch_min_hits,
            self.same_sender_cross_batch_min_batches,
            self.version,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in numeric_values
        ):
            raise ValueError("Evidence Policy 数值必须是非负整数")
        if self.min_evidence_count <= 0 or self.min_sender_count <= 0:
            raise ValueError("Evidence Policy 基础证据门槛必须为正")
        if self.version <= 0:
            raise ValueError("Evidence Policy version 必须为正")
        unknown_kinds = (
            set(self.explicit_evidence_kinds)
            - set(GROUP_LEARNING_EVIDENCE_KINDS)
        )
        if unknown_kinds:
            raise ValueError("Evidence Policy 包含未知 evidence kind")
        if len(self.explicit_evidence_kinds) != len(
            set(self.explicit_evidence_kinds)
        ):
            raise ValueError("Evidence Policy evidence kind 不能重复")

    @property
    def registry_namespace(self) -> str:
        return "group_learning_evidence_policy"

    @property
    def registry_id(self) -> str:
        return self.policy_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "candidate_type": self.candidate_type,
            "min_evidence_count": self.min_evidence_count,
            "min_sender_count": self.min_sender_count,
            "explicit_evidence_kinds": self.explicit_evidence_kinds,
            "min_explicit_evidence_count": (
                self.min_explicit_evidence_count
            ),
            "same_sender_cross_batch_min_hits": (
                self.same_sender_cross_batch_min_hits
            ),
            "same_sender_cross_batch_min_batches": (
                self.same_sender_cross_batch_min_batches
            ),
            "version": self.version,
            "owner_module": self.owner_module,
        }


@dataclass(frozen=True, slots=True)
class EvidenceFact:
    sender_id: str
    batch_id: str
    evidence_kind: str

    def __post_init__(self) -> None:
        if self.evidence_kind not in GROUP_LEARNING_EVIDENCE_KINDS:
            raise ValueError("evidence_kind 无效")


@dataclass(frozen=True, slots=True)
class EvidencePolicyDecision:
    eligible: bool
    reason_code: str
    evidence_count: int
    sender_count: int
    batch_count: int


def _build_evidence_registry(
) -> RegistrySnapshot[EvidencePolicyDescriptor]:
    descriptors = (
        EvidencePolicyDescriptor(
            policy_id="topic.v1",
            candidate_type="topic",
            min_evidence_count=2,
            min_sender_count=2,
        ),
        EvidencePolicyDescriptor(
            policy_id="expression.v1",
            candidate_type="expression",
            min_evidence_count=2,
            min_sender_count=2,
            same_sender_cross_batch_min_hits=3,
            same_sender_cross_batch_min_batches=2,
        ),
        EvidencePolicyDescriptor(
            policy_id="slang.v1",
            candidate_type="slang",
            min_evidence_count=2,
            min_sender_count=2,
            explicit_evidence_kinds=("explicit_definition",),
            min_explicit_evidence_count=1,
        ),
        EvidencePolicyDescriptor(
            policy_id="style.v1",
            candidate_type="style",
            min_evidence_count=3,
            min_sender_count=2,
        ),
    )
    builder = RegistryBuilder[EvidencePolicyDescriptor](
        "group_learning_evidence_policy"
    )
    for descriptor in descriptors:
        builder.register(descriptor)
    return builder.freeze()


GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY = _build_evidence_registry()


def evidence_policy_for(
    candidate_type: str,
) -> EvidencePolicyDescriptor:
    normalized = str(candidate_type or "").strip()
    matches = tuple(
        descriptor
        for descriptor in GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY
        if descriptor.candidate_type == normalized
    )
    if len(matches) != 1:
        raise KeyError(f"未登记的群学习 Evidence Policy：{normalized}")
    return matches[0]


def evaluate_evidence_policy(
    candidate_type: str,
    facts: tuple[EvidenceFact, ...] | list[EvidenceFact],
) -> EvidencePolicyDecision:
    policy = evidence_policy_for(candidate_type)
    normalized = tuple(facts)
    evidence_count = len(normalized)
    senders = {
        fact.sender_id.strip()
        for fact in normalized
        if fact.sender_id.strip()
    }
    batches = {
        fact.batch_id.strip()
        for fact in normalized
        if fact.batch_id.strip()
    }
    explicit_count = sum(
        fact.evidence_kind in policy.explicit_evidence_kinds
        for fact in normalized
    )
    explicit_ok = (
        policy.min_explicit_evidence_count > 0
        and explicit_count >= policy.min_explicit_evidence_count
    )
    normal_ok = (
        evidence_count >= policy.min_evidence_count
        and len(senders) >= policy.min_sender_count
    )
    same_sender_cross_batch_ok = (
        policy.same_sender_cross_batch_min_hits > 0
        and evidence_count >= policy.same_sender_cross_batch_min_hits
        and len(senders) == 1
        and len(batches)
        >= policy.same_sender_cross_batch_min_batches
    )
    eligible = explicit_ok or normal_ok or same_sender_cross_batch_ok
    if eligible:
        reason_code = (
            "explicit_evidence"
            if explicit_ok
            else (
                "same_sender_cross_batch"
                if same_sender_cross_batch_ok
                else "multi_sender_evidence"
            )
        )
    elif evidence_count < policy.min_evidence_count:
        reason_code = "insufficient_evidence"
    elif len(senders) < policy.min_sender_count:
        reason_code = "insufficient_senders"
    else:
        reason_code = "evidence_policy_not_satisfied"
    return EvidencePolicyDecision(
        eligible=eligible,
        reason_code=reason_code,
        evidence_count=evidence_count,
        sender_count=len(senders),
        batch_count=len(batches),
    )


__all__ = [
    "GROUP_LEARNING_EVIDENCE_KINDS",
    "GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY",
    "EvidenceFact",
    "EvidencePolicyDecision",
    "EvidencePolicyDescriptor",
    "evaluate_evidence_policy",
    "evidence_policy_for",
]
