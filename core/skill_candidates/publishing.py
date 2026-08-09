"""Skill 候选正式写入与可重建发布意图的事务边界。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.admin_audit import stage_admin_audit
from core.db.models.admin import AdminAuditLog
from core.db.models.skill import (
    SkillBindingRow,
    SkillCandidatePublicationIntentRow,
    SkillEvaluationRow,
    SkillPackageRow,
)
from core.evolution_control.contracts import canonical_json, sha256_json
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


_INTENT_STATUSES = frozenset({"pending", "ambiguous", "finalized"})


@dataclass(frozen=True, slots=True)
class SkillCandidatePublicationIntent:
    """发布意图的已校验只读快照。"""

    publication_id: str
    approval_id: str
    candidate_sha256: str
    gate_report_sha256: str
    approval_token_sha256: str
    publication_sha256: str
    package_id: str
    binding_id: str
    evaluation_id: str
    receipt: dict[str, object]
    status: str
    reconcile_attempts: int
    last_error_code: str


def _db_now() -> datetime:
    return datetime.now().replace(tzinfo=None)


def _intent_from_row(
    row: SkillCandidatePublicationIntentRow,
) -> SkillCandidatePublicationIntent:
    try:
        receipt = json.loads(str(row.receipt_json))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SkillCandidateContractError("Skill 发布意图回执无法解析") from exc
    if not isinstance(receipt, dict):
        raise SkillCandidateContractError("Skill 发布意图回执必须是对象")
    payload = {
        key: value
        for key, value in receipt.items()
        if key != "publication_sha256"
    }
    declared_sha256 = str(receipt.get("publication_sha256") or "")
    expected = {
        "publication_id": str(row.publication_id),
        "approval_id": str(row.approval_id),
        "candidate_sha256": str(row.candidate_sha256),
        "gate_report_sha256": str(row.gate_report_sha256),
        "publication_sha256": str(row.publication_sha256),
        "package_id": str(row.package_id),
        "binding_id": str(row.binding_id),
        "evaluation_id": str(row.evaluation_id),
    }
    if (
        declared_sha256 != expected["publication_sha256"]
        or sha256_json(payload) != declared_sha256
        or any(str(receipt.get(key) or "") != value for key, value in expected.items())
    ):
        raise SkillCandidateContractError("Skill 发布意图与不可变回执不一致")
    status = str(row.status or "")
    if status not in _INTENT_STATUSES:
        raise SkillCandidateContractError("Skill 发布意图状态无效")
    return SkillCandidatePublicationIntent(
        publication_id=str(row.publication_id),
        approval_id=str(row.approval_id),
        candidate_sha256=str(row.candidate_sha256),
        gate_report_sha256=str(row.gate_report_sha256),
        approval_token_sha256=str(row.approval_token_sha256),
        publication_sha256=str(row.publication_sha256),
        package_id=str(row.package_id),
        binding_id=str(row.binding_id),
        evaluation_id=str(row.evaluation_id),
        receipt=dict(receipt),
        status=status,
        reconcile_attempts=int(row.reconcile_attempts or 0),
        last_error_code=str(row.last_error_code or ""),
    )


def load_candidate_publication_intent(
    db: Session,
    *,
    approval_id: str,
) -> SkillCandidatePublicationIntent | None:
    row = db.execute(
        select(SkillCandidatePublicationIntentRow).where(
            SkillCandidatePublicationIntentRow.approval_id == approval_id
        )
    ).scalar_one_or_none()
    return _intent_from_row(row) if row is not None else None


def list_candidate_publication_intents(
    db: Session,
    *,
    statuses: frozenset[str] | None = None,
) -> tuple[SkillCandidatePublicationIntent, ...]:
    statement = select(SkillCandidatePublicationIntentRow).order_by(
        SkillCandidatePublicationIntentRow.created_at,
        SkillCandidatePublicationIntentRow.publication_id,
    )
    if statuses is not None:
        invalid = statuses - _INTENT_STATUSES
        if invalid:
            raise ValueError(f"无效发布意图状态: {sorted(invalid)}")
        statement = statement.where(
            SkillCandidatePublicationIntentRow.status.in_(statuses)
        )
    return tuple(_intent_from_row(row) for row in db.execute(statement).scalars())


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


def stage_candidate_to_skill_registry(
    db: Session,
    candidate: SkillExperienceCandidate,
    report: Mapping[str, object],
    approval: Mapping[str, object],
) -> dict[str, object]:
    """暂存正式变更；调用方必须把发布意图加入同一事务后再提交。"""

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


def _assert_intent_matches(
    intent: SkillCandidatePublicationIntent,
    *,
    approval_token_sha256: str,
    receipt: Mapping[str, object],
) -> None:
    expected = {
        "publication_id": intent.publication_id,
        "approval_id": intent.approval_id,
        "candidate_sha256": intent.candidate_sha256,
        "gate_report_sha256": intent.gate_report_sha256,
        "publication_sha256": intent.publication_sha256,
        "package_id": intent.package_id,
        "binding_id": intent.binding_id,
        "evaluation_id": intent.evaluation_id,
    }
    if (
        intent.approval_token_sha256 != approval_token_sha256
        or canonical_json(intent.receipt) != canonical_json(receipt)
        or any(str(receipt.get(key) or "") != value for key, value in expected.items())
    ):
        raise SkillCandidateContractError("人工批准已绑定不同的发布意图")


def _publication_audit_facts(
    receipt: Mapping[str, object],
) -> tuple[str, dict[str, object]]:
    publication_id = str(receipt.get("publication_id") or "")
    if not publication_id:
        raise SkillCandidateContractError("发布回执缺少 publication_id")
    return f"skill_candidate.publish:{publication_id}", {
        "candidate_sha256": receipt.get("candidate_sha256"),
        "gate_report_sha256": receipt.get("gate_report_sha256"),
        "approval_id": receipt.get("approval_id"),
        "package_id": receipt.get("package_id"),
        "publication_mode": receipt.get("publication_mode"),
        "rollback_action": receipt.get("rollback_action"),
        "approval_token_recorded": False,
    }


def stage_candidate_publication_audit(
    db: Session,
    *,
    receipt: Mapping[str, object],
    admin_user: str = "admin",
    ip_address: str = "",
) -> None:
    """将正式 Skill 发布审计加入发布意图所在事务。"""

    event_id, detail = _publication_audit_facts(receipt)
    stage_admin_audit(
        db,
        event_id=event_id,
        admin_user=admin_user,
        action="skill_candidate.publish",
        target_type="skill_candidate_publication",
        target_id=str(receipt.get("publication_id") or ""),
        detail=detail,
        ip_address=ip_address,
    )


def ensure_candidate_publication_audit(
    db: Session,
    *,
    receipt: Mapping[str, object],
    admin_user: str = "admin",
    ip_address: str = "",
) -> None:
    """从持久发布意图幂等补齐旧版本缺失的正式审计行。"""

    event_id, _detail = _publication_audit_facts(receipt)
    existing = db.execute(
        select(AdminAuditLog).where(AdminAuditLog.event_id == event_id)
    ).scalar_one_or_none()
    if existing is not None:
        admin_user = str(existing.admin_user or "admin")
        ip_address = str(existing.ip_address or "")
    stage_candidate_publication_audit(
        db,
        receipt=receipt,
        admin_user=admin_user,
        ip_address=ip_address,
    )
    try:
        db.commit()
    except BaseException:
        db.rollback()
        recovered = db.execute(
            select(AdminAuditLog).where(AdminAuditLog.event_id == event_id)
        ).scalar_one_or_none()
        if recovered is None:
            raise
        stage_candidate_publication_audit(
            db,
            receipt=receipt,
            admin_user=str(recovered.admin_user or "admin"),
            ip_address=str(recovered.ip_address or ""),
        )
        db.rollback()


def commit_candidate_publication_intent(
    db: Session,
    *,
    approval_token_sha256: str,
    receipt: Mapping[str, object],
    audit_admin_user: str = "admin",
    audit_ip_address: str = "",
) -> SkillCandidatePublicationIntent:
    """将正式 Skill 变更和 approval 消费意图作为一个事务提交。"""

    receipt_copy = dict(receipt)
    approval_id = str(receipt_copy.get("approval_id") or "")
    row = SkillCandidatePublicationIntentRow(
        publication_id=str(receipt_copy.get("publication_id") or ""),
        approval_id=approval_id,
        candidate_sha256=str(receipt_copy.get("candidate_sha256") or ""),
        gate_report_sha256=str(receipt_copy.get("gate_report_sha256") or ""),
        approval_token_sha256=approval_token_sha256,
        publication_sha256=str(receipt_copy.get("publication_sha256") or ""),
        package_id=str(receipt_copy.get("package_id") or ""),
        binding_id=str(receipt_copy.get("binding_id") or ""),
        evaluation_id=str(receipt_copy.get("evaluation_id") or ""),
        receipt_json=canonical_json(receipt_copy),
        status="pending",
        reconcile_attempts=0,
        last_error_code="",
        created_at=_db_now(),
        updated_at=_db_now(),
    )
    db.add(row)
    stage_candidate_publication_audit(
        db,
        receipt=receipt_copy,
        admin_user=audit_admin_user,
        ip_address=audit_ip_address,
    )
    try:
        db.commit()
    except BaseException:
        db.rollback()
        recovered = load_candidate_publication_intent(
            db,
            approval_id=approval_id,
        )
        if recovered is None:
            raise
        _assert_intent_matches(
            recovered,
            approval_token_sha256=approval_token_sha256,
            receipt=receipt_copy,
        )
        return recovered
    recovered = load_candidate_publication_intent(db, approval_id=approval_id)
    if recovered is None:
        raise SkillCandidateContractError("提交后发布意图不可见")
    _assert_intent_matches(
        recovered,
        approval_token_sha256=approval_token_sha256,
        receipt=receipt_copy,
    )
    return recovered


def set_candidate_publication_projection_state(
    db: Session,
    *,
    intent: SkillCandidatePublicationIntent,
    status: str,
    error_code: str = "",
) -> SkillCandidatePublicationIntent:
    """只推进可变投影状态，不允许改写发布事实。"""

    if status not in _INTENT_STATUSES:
        raise ValueError("status 无效")
    row = db.get(
        SkillCandidatePublicationIntentRow,
        intent.publication_id,
    )
    if row is None:
        raise SkillCandidateContractError("Skill 发布意图不存在")
    current = _intent_from_row(row)
    _assert_intent_matches(
        current,
        approval_token_sha256=intent.approval_token_sha256,
        receipt=intent.receipt,
    )
    if current.status == "finalized" and status != "ambiguous":
        return current
    target_status = (
        "ambiguous"
        if current.status == "ambiguous" and status == "pending"
        else status
    )
    row.status = target_status
    row.reconcile_attempts = current.reconcile_attempts + 1
    row.last_error_code = str(error_code or "")[:128]
    row.updated_at = _db_now()
    if target_status == "finalized":
        row.finalized_at = _db_now()
    try:
        db.commit()
    except BaseException:
        db.rollback()
        recovered = load_candidate_publication_intent(
            db,
            approval_id=intent.approval_id,
        )
        if recovered is not None and recovered.status == target_status:
            return recovered
        raise
    recovered = load_candidate_publication_intent(
        db,
        approval_id=intent.approval_id,
    )
    if recovered is None or recovered.status != target_status:
        raise SkillCandidateContractError("Skill 发布投影状态提交后不可见")
    return recovered


def validate_candidate_publication_receipt(
    db: Session,
    *,
    candidate: SkillExperienceCandidate,
    receipt: Mapping[str, object],
) -> None:
    """采用旧回执前确认其正式 Skill、binding 与评测事实均真实存在。"""

    package_id = str(receipt.get("package_id") or "")
    binding_id = str(receipt.get("binding_id") or "")
    evaluation_id = str(receipt.get("evaluation_id") or "")
    package = db.get(SkillPackageRow, package_id)
    binding = db.get(SkillBindingRow, binding_id)
    evaluation = db.get(SkillEvaluationRow, evaluation_id)
    if (
        package is None
        or binding is None
        or evaluation is None
        or str(package.bundle_sha256) != candidate.parsed_bundle.bundle_sha256
        or str(package.source_label)
        != f"experience-candidate:{candidate.candidate_sha256}"
        or str(binding.scope) != candidate.target_scope
        or str(binding.scope_key) != candidate.target_scope_key
        or str(binding.skill_name) != candidate.parsed_bundle.name
        or str(evaluation.package_id) != package_id
        or str(evaluation.evidence_sha256)
        != str(receipt.get("gate_report_sha256") or "")
    ):
        raise SkillCandidateContractError("旧发布回执与正式 Skill 事实不一致")


__all__ = [
    "SkillCandidatePublicationIntent",
    "commit_candidate_publication_intent",
    "ensure_candidate_publication_audit",
    "list_candidate_publication_intents",
    "load_candidate_publication_intent",
    "set_candidate_publication_projection_state",
    "stage_candidate_publication_audit",
    "stage_candidate_to_skill_registry",
    "validate_candidate_publication_receipt",
]
