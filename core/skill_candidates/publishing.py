"""将已通过门禁且经人工批准的候选发布到正式 Skill 生命周期。"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db.models.skill import SkillEvaluationRow
from core.skills import (
    SkillGovernanceService,
    SkillLifecycleService,
    semver_key,
)

from .contracts import (
    NO_SKILL_BASELINE_SHA256,
    SkillCandidateContractError,
    SkillExperienceCandidate,
)


def _binding_for_candidate(
    service: SkillLifecycleService,
    candidate: SkillExperienceCandidate,
):
    name = candidate.parsed_bundle.name
    return next(
        (
            item
            for item in service.list_bindings(target=candidate.target)
            if item.skill_name == name
        ),
        None,
    )


def _version_for_candidate(
    service: SkillLifecycleService,
    candidate: SkillExperienceCandidate,
):
    bundle = candidate.parsed_bundle
    return next(
        (
            item
            for item in service.list_versions(
                target=candidate.target,
                skill_name=bundle.name,
            )
            if item.version == bundle.version
        ),
        None,
    )


def _record_evaluation_once(
    db: Session,
    *,
    service: SkillLifecycleService,
    candidate: SkillExperienceCandidate,
    report: Mapping[str, object],
    reviewer: str,
) -> str:
    package = _version_for_candidate(service, candidate)
    if package is None:
        raise SkillCandidateContractError("正式 Skill 版本写入后不可见")
    evidence_sha = str(report.get("gate_report_sha256") or "")
    existing = db.execute(
        select(SkillEvaluationRow).where(
            SkillEvaluationRow.package_id == package.package_id,
            SkillEvaluationRow.evidence_sha256 == evidence_sha,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing.evaluation_id)
    package_row = service.package_by_id(package.package_id)
    if package_row is None:
        raise SkillCandidateContractError("正式 Skill package 不存在")
    entry = service.lock_entry_from_package(package_row)
    return SkillGovernanceService(db).record_evaluation(
        entry,
        suite_id=str(report.get("suite_id") or ""),
        evaluator_id=str(report.get("evaluator_id") or ""),
        evaluator_version=str(report.get("evaluator_version") or ""),
        passed=True,
        score=int(report.get("candidate_score_micros") or 0) / 1_000_000,
        prompt_tokens=0,
        cost_microunits=int(report.get("candidate_cost_microunits") or 0),
        evidence_sha256=evidence_sha,
        actor_id=f"human:{reviewer}",
    )


def publish_candidate_to_skill_registry(
    db: Session,
    candidate: SkillExperienceCandidate,
    report: Mapping[str, object],
    approval: Mapping[str, object],
) -> dict[str, object]:
    """真实写入受管版本；已有 Skill 只暂存版本，不自动切换激活绑定。"""

    if not isinstance(db, Session):
        raise TypeError("db 必须是 SQLAlchemy Session")
    if not isinstance(candidate, SkillExperienceCandidate):
        raise TypeError("candidate 必须是 SkillExperienceCandidate")
    if report.get("passed") is not True:
        raise SkillCandidateContractError("未通过门禁的候选不能发布")
    reviewer = str(approval.get("reviewer") or "").strip()
    if approval.get("reviewer_kind") != "human" or not reviewer:
        raise SkillCandidateContractError("发布必须绑定人工 reviewer")
    expected_generation = approval.get("expected_binding_generation")
    if type(expected_generation) is not int or expected_generation < 0:
        raise SkillCandidateContractError("批准中的 binding generation 无效")

    service = SkillLifecycleService(db)
    bundle = candidate.parsed_bundle
    source_label = f"experience-candidate:{candidate.candidate_sha256}"
    before = _binding_for_candidate(service, candidate)
    existing_version = _version_for_candidate(service, candidate)
    try:
        if existing_version is not None:
            if (
                existing_version.bundle_sha256 != bundle.bundle_sha256
                or existing_version.source_label != source_label
            ):
                raise SkillCandidateContractError(
                    "同版本已由其他来源占用或正文不一致"
                )
            after = _binding_for_candidate(service, candidate)
            if after is None or after.status != "active":
                raise SkillCandidateContractError("正式 Skill binding 状态无效")
        elif before is None:
            if expected_generation != 0:
                raise SkillCandidateContractError(
                    "新 Skill 发布必须确认 binding generation=0"
                )
            if candidate.baseline_bundle_sha256 != NO_SKILL_BASELINE_SHA256:
                raise SkillCandidateContractError("新 Skill 的冻结基线必须是 absent")
            if candidate.target_scope != "user":
                raise SkillCandidateContractError(
                    "新 Skill 首发只允许显式 user scope，禁止宽作用域自动激活"
                )
            after = service.install(
                candidate.target,
                bundle,
                actor_id=f"human:{reviewer}",
                source_label=source_label,
                trusted_source=True,
                pin=True,
            )
        else:
            if before.status != "active":
                raise SkillCandidateContractError(
                    "已卸载 Skill 不能通过经验候选隐式重装"
                )
            if expected_generation != before.generation:
                raise SkillCandidateContractError(
                    "Skill binding generation 已变化，需重新人工批准"
                )
            if before.active_bundle_sha256 != candidate.baseline_bundle_sha256:
                raise SkillCandidateContractError(
                    "正式 Skill 基线已变化，需重新提取和评测候选"
                )
            if semver_key(bundle.version) <= semver_key(before.active_version):
                raise SkillCandidateContractError(
                    "经验候选版本必须高于当前正式 Skill SemVer"
                )
            after = service.install(
                candidate.target,
                bundle,
                actor_id=f"human:{reviewer}",
                source_label=source_label,
                trusted_source=True,
                pin=before.pinned,
                expected_generation=before.generation,
            )
        published = _version_for_candidate(service, candidate)
        if published is None or published.bundle_sha256 != bundle.bundle_sha256:
            raise SkillCandidateContractError("候选未写入正式 Skill 版本库")
        evaluation_id = _record_evaluation_once(
            db,
            service=service,
            candidate=candidate,
            report=report,
            reviewer=reviewer,
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise

    if after.active_package_id == published.package_id:
        publication_mode = "installed_active"
        previous_active_package_id = ""
        previous_active_version = ""
        rollback_action = "skill.uninstall"
    else:
        publication_mode = "version_staged"
        previous_active_package_id = after.active_package_id
        previous_active_version = after.active_version
        rollback_action = "none_runtime_unchanged"
    return {
        "package_id": published.package_id,
        "binding_id": after.binding_id,
        "binding_generation": after.generation,
        "active_package_id": after.active_package_id,
        "active_version": after.active_version,
        "bundle_sha256": published.bundle_sha256,
        "publication_mode": publication_mode,
        "previous_active_package_id": previous_active_package_id,
        "previous_active_version": previous_active_version,
        "rollback_action": rollback_action,
        "evaluation_id": evaluation_id,
    }


__all__ = ["publish_candidate_to_skill_registry"]
