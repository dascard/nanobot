"""经验提取、Skill 候选独立门禁、人工批准和正式发布 API。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from core.database import get_db
from core.repositories.run_viewer import OfflineRunViewRepository
from core.run_ledger.contracts import RunLedgerIntegrityError
from core.runtime_paths import RUNTIME_PATHS
from core.skill_candidates import (
    SkillCandidateContractError,
    SkillCandidateEvaluationEvidence,
    SkillCandidateStore,
    SkillDraftSpec,
    extract_skill_candidate,
    skill_candidate_catalog_payload,
)
from core.skill_candidates.store import MAX_APPROVAL_TTL_SECONDS
from evals.harness_registry import EVAL_HARNESS_REGISTRY


SKILL_CANDIDATE_ROOT = RUNTIME_PATHS.skill_candidate_dir

router = APIRouter(
    prefix="/skills/candidates",
    tags=["admin-skill-candidates"],
    dependencies=[Depends(verify_admin)],
)


class SkillCandidateExtractBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_ids: list[str] = Field(min_length=2, max_length=20)
    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1_024)
    target_scope: Literal["project", "agent", "user"]
    target_scope_key: str = Field(min_length=1, max_length=255)
    baseline_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_revision: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    created_at: str = Field(min_length=1, max_length=64)
    capability_tags: list[str] = Field(min_length=1, max_length=32)
    applies_to: list[Literal[
        "all",
        "chat",
        "private",
        "group",
        "scheduled",
        "task",
    ]] = Field(min_length=1, max_length=6)
    allowed_tools: list[str] = Field(default_factory=list, max_length=32)
    extra_evidence_by_run: dict[str, list[str]] = Field(default_factory=dict)
    generator_id: str = Field(
        default="trajectory-skill-extractor",
        min_length=2,
        max_length=128,
    )
    generator_version: str = Field(default="1.0.0", min_length=1, max_length=128)
    generation_cost_microunits: int = Field(default=0, ge=0, le=10**12)


class SkillCandidateEvaluationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: dict[str, Any]


class SkillCandidateApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_kind: Literal["human"]
    reason: str = Field(min_length=1, max_length=2_000)
    expected_binding_generation: int = Field(ge=0, le=2**31 - 1)
    expires_in_seconds: int = Field(ge=60, le=MAX_APPROVAL_TTL_SECONDS)


class SkillCandidatePublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_id: str = Field(min_length=1, max_length=128)
    approval_token: SecretStr


def _store() -> SkillCandidateStore:
    return SkillCandidateStore(SKILL_CANDIDATE_ROOT)


def _contract_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError) or "不存在" in str(exc):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, (SkillCandidateContractError, RunLedgerIntegrityError)):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(status_code=code, detail=str(exc))


def _registry_payload() -> dict[str, object]:
    return {
        "namespace": EVAL_HARNESS_REGISTRY.namespace,
        "generation": EVAL_HARNESS_REGISTRY.generation,
        "sha256": EVAL_HARNESS_REGISTRY.sha256,
    }


@router.get("/catalog")
def skill_candidate_catalog() -> dict[str, object]:
    return {
        **skill_candidate_catalog_payload(),
        "harness_registry": _registry_payload(),
        "max_approval_ttl_seconds": MAX_APPROVAL_TTL_SECONDS,
    }


@router.post("/extract", status_code=status.HTTP_201_CREATED)
def extract_candidate_from_runs(
    body: SkillCandidateExtractBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        if len(body.run_ids) != len(set(body.run_ids)):
            raise SkillCandidateContractError("run_ids 不能重复")
        unknown_evidence_runs = sorted(
            set(body.extra_evidence_by_run) - set(body.run_ids)
        )
        if unknown_evidence_runs:
            raise SkillCandidateContractError(
                "extra_evidence_by_run 引用了未提供的 Run"
            )
        repository = OfflineRunViewRepository(db)
        viewers = [repository.build_persisted(run_id) for run_id in body.run_ids]
        spec = SkillDraftSpec(
            name=body.name,
            version=body.version,
            description=body.description,
            target_scope=body.target_scope,
            target_scope_key=body.target_scope_key,
            baseline_bundle_sha256=body.baseline_bundle_sha256,
            source_revision=body.source_revision,
            created_at=body.created_at,
            capability_tags=tuple(body.capability_tags),
            applies_to=tuple(body.applies_to),
            allowed_tools=tuple(body.allowed_tools),
            generator_id=body.generator_id,
            generator_version=body.generator_version,
            generation_cost_microunits=body.generation_cost_microunits,
        )
        candidate = extract_skill_candidate(
            viewers,
            spec=spec,
            extra_evidence_by_run={
                run_id: tuple(values)
                for run_id, values in body.extra_evidence_by_run.items()
            },
        )
        result = _store().put_candidate(candidate)
    except (
        LookupError,
        RunLedgerIntegrityError,
        SkillCandidateContractError,
        ValueError,
    ) as exc:
        raise _contract_error(exc) from exc
    audit_request(
        db,
        request,
        "skill_candidate.extract",
        "skill_candidate",
        candidate.candidate_sha256,
        {
            "skill_name": candidate.parsed_bundle.name,
            "version": candidate.parsed_bundle.version,
            "source_run_count": len(candidate.source_runs),
            "source_trajectory_sha256s": list(
                candidate.source_trajectory_sha256s
            ),
            "deduplicated": result["deduplicated"],
            "raw_production_content_access": False,
            "repository_operations": "forbidden",
        },
    )
    return result


@router.get("/items/{candidate_sha256}")
def get_skill_candidate(candidate_sha256: str) -> dict[str, object]:
    try:
        return _store().get_candidate(candidate_sha256).to_dict()
    except SkillCandidateContractError as exc:
        raise _contract_error(exc) from exc


@router.post("/evaluations", status_code=status.HTTP_201_CREATED)
def evaluate_candidate(
    body: SkillCandidateEvaluationBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        evidence = SkillCandidateEvaluationEvidence.from_dict(body.evidence)
        report = _store().evaluate(
            evidence,
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        )
    except SkillCandidateContractError as exc:
        raise _contract_error(exc) from exc
    audit_request(
        db,
        request,
        "skill_candidate.evaluate",
        "skill_candidate_gate",
        str(report["gate_report_sha256"]),
        {
            "candidate_sha256": report["candidate_sha256"],
            "passed": report["passed"],
            "error_codes": [item["code"] for item in report["errors"]],
            "dataset_sha256": report["dataset_sha256"],
            "harness_registry_sha256": report["harness_registry_sha256"],
        },
    )
    return report


@router.get("/evaluations/{gate_report_sha256}")
def get_candidate_evaluation(gate_report_sha256: str) -> dict[str, object]:
    try:
        return _store().get_gate_report(
            gate_report_sha256,
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        )
    except SkillCandidateContractError as exc:
        raise _contract_error(exc) from exc


@router.post("/approvals", status_code=status.HTTP_201_CREATED)
def approve_candidate(
    body: SkillCandidateApprovalBody,
    request: Request,
    db: Session = Depends(get_db),
    admin_id: str = Depends(verify_admin),
) -> dict[str, object]:
    try:
        approval = _store().approve(
            candidate_sha256=body.candidate_sha256,
            gate_report_sha256=body.gate_report_sha256,
            confirm_candidate_sha256=body.confirm_candidate_sha256,
            reviewer=admin_id,
            reviewer_kind=body.reviewer_kind,
            reason=body.reason,
            expected_binding_generation=body.expected_binding_generation,
            expires_in_seconds=body.expires_in_seconds,
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        )
    except SkillCandidateContractError as exc:
        raise _contract_error(exc) from exc
    audit_request(
        db,
        request,
        "skill_candidate.approve",
        "skill_candidate_approval",
        str(approval["approval_id"]),
        {
            "candidate_sha256": approval["candidate_sha256"],
            "gate_report_sha256": approval["gate_report_sha256"],
            "reviewer": approval["reviewer"],
            "reviewer_kind": approval["reviewer_kind"],
            "expected_binding_generation": approval[
                "expected_binding_generation"
            ],
            "approval_token_recorded": False,
        },
    )
    return approval


@router.get("/approvals/{approval_id}")
def get_candidate_approval(approval_id: str) -> dict[str, object]:
    try:
        return _store().get_approval(approval_id)
    except SkillCandidateContractError as exc:
        raise _contract_error(exc) from exc


@router.post("/publish", status_code=status.HTTP_201_CREATED)
def publish_candidate(
    body: SkillCandidatePublishBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        receipt = _store().publish(
            candidate_sha256=body.candidate_sha256,
            approval_id=body.approval_id,
            approval_token=body.approval_token.get_secret_value(),
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
            db=db,
        )
    except (SkillCandidateContractError, ValueError, RuntimeError) as exc:
        db.rollback()
        raise _contract_error(exc) from exc
    audit_request(
        db,
        request,
        "skill_candidate.publish",
        "skill_candidate_publication",
        str(receipt["publication_id"]),
        {
            "candidate_sha256": receipt["candidate_sha256"],
            "gate_report_sha256": receipt["gate_report_sha256"],
            "approval_id": receipt["approval_id"],
            "package_id": receipt["package_id"],
            "publication_mode": receipt["publication_mode"],
            "rollback_action": receipt["rollback_action"],
            "approval_token_recorded": False,
        },
    )
    return receipt


@router.get("/publications/{publication_id}")
def get_candidate_publication(publication_id: str) -> dict[str, object]:
    try:
        return _store().get_publication(publication_id)
    except SkillCandidateContractError as exc:
        raise _contract_error(exc) from exc


@router.get("/state")
def skill_candidate_state(
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return _store().state(db)


__all__ = [
    "SKILL_CANDIDATE_ROOT",
    "router",
]
