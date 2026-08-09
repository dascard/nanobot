"""Skill 经验候选的独立安全、质量、成本和证据完整性门禁。"""

from __future__ import annotations

from .contracts import (
    MAX_COST_INCREASE_BASIS_POINTS,
    SkillCandidateContractError,
    SkillCandidateEvaluationEvidence,
    SkillExperienceCandidate,
)
from core.evolution_control.contracts import sha256_json


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def evaluate_skill_candidate(
    candidate: SkillExperienceCandidate,
    evidence: SkillCandidateEvaluationEvidence,
    *,
    current_harness_registry_sha256: str,
) -> dict[str, object]:
    """生成阻断式不可变报告；任何不完整、漂移或回退均不能发布。"""

    if not isinstance(candidate, SkillExperienceCandidate):
        raise TypeError("candidate 必须是 SkillExperienceCandidate")
    if not isinstance(evidence, SkillCandidateEvaluationEvidence):
        raise TypeError("evidence 必须是 SkillCandidateEvaluationEvidence")
    current_registry = str(current_harness_registry_sha256 or "").strip().lower()
    if len(current_registry) != 64 or any(
        char not in "0123456789abcdef" for char in current_registry
    ):
        raise SkillCandidateContractError(
            "current_harness_registry_sha256 必须是 SHA-256"
        )

    bundle = candidate.parsed_bundle
    errors: list[dict[str, str]] = []
    if evidence.candidate_sha256 != candidate.candidate_sha256:
        errors.append(_error(
            "candidate_hash_mismatch",
            "评测未绑定当前候选摘要",
        ))
    if evidence.candidate_bundle_sha256 != bundle.bundle_sha256:
        errors.append(_error(
            "bundle_hash_mismatch",
            "评测未绑定当前 Skill bundle",
        ))
    if evidence.baseline_bundle_sha256 != candidate.baseline_bundle_sha256:
        errors.append(_error(
            "baseline_hash_mismatch",
            "评测基线与候选声明不一致",
        ))
    if evidence.source_revision != candidate.source_revision:
        errors.append(_error(
            "source_revision_mismatch",
            "评测源码 revision 与候选不一致",
        ))
    if evidence.harness_registry_sha256 != current_registry:
        errors.append(_error(
            "harness_registry_drift",
            "评测使用的 Harness Registry 已漂移",
        ))
    if evidence.evaluator_id == candidate.generator_id:
        errors.append(_error(
            "evaluator_not_independent",
            "候选生成器不能兼任独立评测器",
        ))
    if evidence.source_trajectory_sha256s != candidate.source_trajectory_sha256s:
        errors.append(_error(
            "trajectory_evidence_mismatch",
            "评测未覆盖候选的完整来源 trajectory",
        ))
    if evidence.validated_pattern_sha256s != candidate.pattern_sha256s:
        errors.append(_error(
            "pattern_coverage_incomplete",
            "评测未覆盖全部流程和失败模式",
        ))
    if not evidence.safety_passed or evidence.safety_failure_count != 0:
        errors.append(_error(
            "safety_gate_failed",
            "Skill 候选安全评测未通过",
        ))
    if evidence.candidate_score_micros <= evidence.baseline_score_micros:
        errors.append(_error(
            "quality_not_improved",
            "候选质量必须严格优于冻结基线",
        ))
    if evidence.candidate_cost_microunits > evidence.approved_cost_microunits:
        errors.append(_error(
            "cost_budget_exceeded",
            "候选评测成本超过显式批准预算",
        ))
    if (
        evidence.baseline_cost_microunits > 0
        and evidence.candidate_cost_microunits * 10_000
        > evidence.baseline_cost_microunits
        * (10_000 + MAX_COST_INCREASE_BASIS_POINTS)
    ):
        errors.append(_error(
            "cost_regression_exceeded",
            "候选成本增幅超过 20% 上限",
        ))
    required_artifacts = {
        evidence.evidence_sha256,
        evidence.dataset_sha256,
        *candidate.source_trajectory_sha256s,
    }
    missing_artifacts = sorted(
        required_artifacts - set(evidence.artifact_sha256s)
    )
    if missing_artifacts:
        errors.append(_error(
            "artifact_evidence_incomplete",
            "评测 Artifact 未覆盖证据、数据集和来源 trajectory",
        ))

    payload: dict[str, object] = {
        "schema_version": evidence.schema_version,
        "candidate_sha256": candidate.candidate_sha256,
        "candidate_id": candidate.candidate_id,
        "skill_name": bundle.name,
        "version": bundle.version,
        "bundle_sha256": bundle.bundle_sha256,
        "baseline_bundle_sha256": candidate.baseline_bundle_sha256,
        "dataset_sha256": evidence.dataset_sha256,
        "source_revision": candidate.source_revision,
        "harness_registry_sha256": current_registry,
        "evaluation_sha256": evidence.evaluation_sha256,
        "evaluator_id": evidence.evaluator_id,
        "evaluator_version": evidence.evaluator_version,
        "suite_id": evidence.suite_id,
        "source_run_ids": [item.run_id for item in candidate.source_runs],
        "source_trajectory_sha256s": list(candidate.source_trajectory_sha256s),
        "validated_pattern_sha256s": list(evidence.validated_pattern_sha256s),
        "baseline_score_micros": evidence.baseline_score_micros,
        "candidate_score_micros": evidence.candidate_score_micros,
        "quality_delta_micros": (
            evidence.candidate_score_micros - evidence.baseline_score_micros
        ),
        "baseline_cost_microunits": evidence.baseline_cost_microunits,
        "candidate_cost_microunits": evidence.candidate_cost_microunits,
        "approved_cost_microunits": evidence.approved_cost_microunits,
        "artifact_sha256s": list(evidence.artifact_sha256s),
        "started_at": evidence.started_at,
        "finished_at": evidence.finished_at,
        "passed": not errors,
        "errors": errors,
    }
    return {
        **payload,
        "gate_report_sha256": sha256_json(payload),
    }


__all__ = ["evaluate_skill_candidate"]
