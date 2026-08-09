"""离线候选、独立门禁、人工批准与灰度回滚管理 API。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from api.admin.common import audited_external_action, verify_admin
from core.database import get_db
from core.evolution_control import (
    EvolutionCandidateBundle,
    EvolutionContractError,
    EvolutionControlStore,
    EvolutionGateEvidence,
    FrozenDatasetManifest,
    evolution_catalog_payload,
)
from core.evolution_control.store import (
    MAX_APPROVAL_TTL_SECONDS,
    MAX_CANARY_BASIS_POINTS,
    MAX_CANARY_DURATION_SECONDS,
)
from core.runtime_paths import RUNTIME_PATHS
from evals.harness_registry import EVAL_HARNESS_REGISTRY


EVOLUTION_CONTROL_ROOT = RUNTIME_PATHS.evolution_control_dir

router = APIRouter(
    prefix="/evals/evolution",
    tags=["admin-evolution-control"],
    dependencies=[Depends(verify_admin)],
)


class ArtifactImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: dict[str, Any]


class EvolutionGateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: dict[str, Any]


class EvolutionApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=128)
    reviewer_kind: Literal["human"]
    reason: str = Field(min_length=1, max_length=2_000)
    risk_scope: list[str] = Field(min_length=1, max_length=16)
    max_basis_points: int = Field(ge=1, le=MAX_CANARY_BASIS_POINTS)
    expires_in_seconds: int = Field(
        ge=60,
        le=MAX_APPROVAL_TTL_SECONDS,
    )


class EvolutionCanaryActivateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_id: str = Field(min_length=2, max_length=128)
    approval_token: SecretStr
    basis_points: int = Field(ge=1, le=MAX_CANARY_BASIS_POINTS)
    subject_allowlist: list[str] = Field(default_factory=list, max_length=100)
    duration_seconds: int = Field(
        ge=60,
        le=MAX_CANARY_DURATION_SECONDS,
    )
    operator: str = Field(min_length=1, max_length=128)


class EvolutionRollbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operator: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2_000)


def _store() -> EvolutionControlStore:
    return EvolutionControlStore(EVOLUTION_CONTROL_ROOT)


def _contract_error(exc: EvolutionContractError) -> HTTPException:
    message = str(exc)
    status_code = (
        status.HTTP_404_NOT_FOUND
        if "不存在" in message
        else status.HTTP_409_CONFLICT
    )
    return HTTPException(status_code=status_code, detail=message)


def _registry_payload() -> dict[str, object]:
    return {
        "namespace": EVAL_HARNESS_REGISTRY.namespace,
        "generation": EVAL_HARNESS_REGISTRY.generation,
        "sha256": EVAL_HARNESS_REGISTRY.sha256,
    }


@router.get("/catalog")
def evolution_catalog() -> dict[str, object]:
    return {
        **evolution_catalog_payload(),
        "harness_registry": _registry_payload(),
        "limits": {
            "max_canary_basis_points": MAX_CANARY_BASIS_POINTS,
            "max_canary_duration_seconds": MAX_CANARY_DURATION_SECONDS,
            "max_approval_ttl_seconds": MAX_APPROVAL_TTL_SECONDS,
        },
    }


@router.post("/datasets/import", status_code=status.HTTP_201_CREATED)
def evolution_import_dataset(
    body: ArtifactImportBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        manifest = FrozenDatasetManifest.from_dict(body.artifact)
        result = audited_external_action(
            db,
            request,
            action="import_evolution_dataset",
            target_type="evolution_dataset",
            target_id=manifest.dataset_sha256,
            request_detail={
                "dataset_id": manifest.dataset_id,
                "revision": manifest.revision,
                "source_revision": manifest.source_revision,
            },
            operation=lambda: _store().put_dataset(manifest),
            result_target_id=lambda _result: manifest.dataset_sha256,
            result_detail=lambda _result: {
                "dataset_id": manifest.dataset_id,
                "revision": manifest.revision,
                "source_revision": manifest.source_revision,
                "split_counts": {
                    item.role: item.expected_count for item in manifest.splits
                },
            },
        )
    except EvolutionContractError as exc:
        raise _contract_error(exc) from exc
    return result


@router.post("/candidates/import", status_code=status.HTTP_201_CREATED)
def evolution_import_candidate(
    body: ArtifactImportBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        candidate = EvolutionCandidateBundle.from_dict(body.artifact)
        result = audited_external_action(
            db,
            request,
            action="import_evolution_candidate",
            target_type="evolution_candidate",
            target_id=candidate.candidate_sha256,
            request_detail={
                "candidate_id": candidate.candidate_id,
                "dataset_sha256": candidate.dataset_sha256,
                "target_kind": candidate.target.kind.value,
                "resource_id": candidate.target.resource_id,
            },
            operation=lambda: _store().put_candidate(candidate),
            result_target_id=lambda _result: candidate.candidate_sha256,
            result_detail=lambda _result: {
                "candidate_id": candidate.candidate_id,
                "dataset_sha256": candidate.dataset_sha256,
                "target_kind": candidate.target.kind.value,
                "resource_id": candidate.target.resource_id,
                "repository_operations": candidate.repository_operations,
            },
        )
    except EvolutionContractError as exc:
        raise _contract_error(exc) from exc
    return result


@router.get("/candidates/{candidate_sha256}")
def evolution_get_candidate(candidate_sha256: str) -> dict[str, object]:
    try:
        return _store().get_candidate(candidate_sha256).to_dict()
    except EvolutionContractError as exc:
        raise _contract_error(exc) from exc


@router.post("/gates", status_code=status.HTTP_201_CREATED)
def evolution_gate_candidate(
    body: EvolutionGateBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        evidence = EvolutionGateEvidence.from_dict(body.evidence)
        report = audited_external_action(
            db,
            request,
            action="evaluate_evolution_candidate",
            target_type="evolution_gate",
            target_id=evidence.candidate_sha256,
            request_detail={
                "candidate_sha256": evidence.candidate_sha256,
                "dataset_sha256": evidence.dataset_sha256,
                "evaluator_id": evidence.evaluator_id,
            },
            operation=lambda: _store().evaluate_gate(
                evidence,
                current_harness_registry_sha256=(
                    EVAL_HARNESS_REGISTRY.sha256
                ),
            ),
            result_target_id=lambda result: str(
                result["gate_report_sha256"]
            ),
            result_detail=lambda result: {
                "candidate_sha256": result["candidate_sha256"],
                "dataset_sha256": result["dataset_sha256"],
                "passed": result["passed"],
                "error_codes": [
                    item["code"] for item in result.get("errors", [])
                ],
                "harness_registry_sha256": result[
                    "harness_registry_sha256"
                ],
            },
        )
    except EvolutionContractError as exc:
        raise _contract_error(exc) from exc
    return report


@router.post("/approvals", status_code=status.HTTP_201_CREATED)
def evolution_approve_candidate(
    body: EvolutionApprovalBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        approval, token = audited_external_action(
            db,
            request,
            action="approve_evolution_canary",
            target_type="evolution_approval",
            target_id=body.candidate_sha256,
            request_detail={
                "candidate_sha256": body.candidate_sha256,
                "gate_report_sha256": body.gate_report_sha256,
                "reviewer": body.reviewer,
                "reviewer_kind": body.reviewer_kind,
                "risk_scope": body.risk_scope,
                "max_basis_points": body.max_basis_points,
            },
            operation=lambda: _store().approve(
                candidate_sha256=body.candidate_sha256,
                gate_report_sha256=body.gate_report_sha256,
                confirm_candidate_sha256=body.confirm_candidate_sha256,
                reviewer=body.reviewer,
                reviewer_kind=body.reviewer_kind,
                reason=body.reason,
                risk_scope=tuple(body.risk_scope),
                max_basis_points=body.max_basis_points,
                expires_in_seconds=body.expires_in_seconds,
                current_harness_registry_sha256=(
                    EVAL_HARNESS_REGISTRY.sha256
                ),
            ),
            result_target_id=lambda result: str(result[0]["approval_id"]),
            result_detail=lambda result: {
                "approval_sha256": result[0]["approval_sha256"],
                "candidate_sha256": result[0]["candidate_sha256"],
                "gate_report_sha256": result[0]["gate_report_sha256"],
                "reviewer": result[0]["reviewer"],
                "reviewer_kind": result[0]["reviewer_kind"],
                "risk_scope": result[0]["risk_scope"],
                "max_basis_points": result[0]["max_basis_points"],
                "expires_at": result[0]["expires_at"],
                "approval_token_recorded": False,
            },
        )
    except EvolutionContractError as exc:
        raise _contract_error(exc) from exc
    return {
        "approval": approval,
        "approval_token": token,
        "token_delivery": "single_use_response_only",
    }


@router.post("/canary/activate", status_code=status.HTTP_201_CREATED)
def evolution_activate_canary(
    body: EvolutionCanaryActivateBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        release = audited_external_action(
            db,
            request,
            action="activate_evolution_canary",
            target_type="evolution_release",
            target_id=body.approval_id,
            request_detail={
                "candidate_sha256": body.candidate_sha256,
                "approval_id": body.approval_id,
                "basis_points": body.basis_points,
                "subject_allowlist_count": len(body.subject_allowlist),
                "duration_seconds": body.duration_seconds,
                "operator": body.operator,
                "approval_token_recorded": False,
            },
            operation=lambda: _store().activate_canary(
                candidate_sha256=body.candidate_sha256,
                approval_id=body.approval_id,
                approval_token=body.approval_token.get_secret_value(),
                basis_points=body.basis_points,
                subject_allowlist=tuple(body.subject_allowlist),
                duration_seconds=body.duration_seconds,
                operator=body.operator,
                current_harness_registry_sha256=(
                    EVAL_HARNESS_REGISTRY.sha256
                ),
            ),
            result_target_id=lambda result: str(result["release_id"]),
            result_detail=lambda result: {
                "release_sha256": result["release_sha256"],
                "candidate_sha256": result["candidate_sha256"],
                "approval_id": result["approval_id"],
                "target_key": result["target_key"],
                "basis_points": result["basis_points"],
                "subject_allowlist_count": len(result["subject_allowlist"]),
                "expires_at": result["expires_at"],
                "approval_token_recorded": False,
            },
        )
    except EvolutionContractError as exc:
        raise _contract_error(exc) from exc
    return release


@router.get("/canary/resolve")
def evolution_resolve_canary(
    target_kind: Literal["prompt", "skill", "routing", "manifest"] = Query(),
    resource_id: str = Query(min_length=1, max_length=256),
    subject_id: str = Query(min_length=1, max_length=256),
) -> dict[str, object]:
    try:
        return _store().resolve_canary(
            target_kind=target_kind,
            resource_id=resource_id,
            subject_id=subject_id,
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        )
    except EvolutionContractError as exc:
        raise _contract_error(exc) from exc


@router.post("/canary/{release_id}/rollback")
def evolution_rollback_canary(
    release_id: str,
    body: EvolutionRollbackBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        receipt = audited_external_action(
            db,
            request,
            action="rollback_evolution_canary",
            target_type="evolution_rollback",
            target_id=release_id,
            request_detail={
                "release_id": release_id,
                "operator": body.operator,
                "reason": body.reason,
            },
            operation=lambda: _store().rollback_canary(
                release_id=release_id,
                operator=body.operator,
                reason=body.reason,
            ),
            result_target_id=lambda result: str(result["rollback_id"]),
            result_detail=lambda result: {
                "rollback_sha256": result["rollback_sha256"],
                "release_id": result["release_id"],
                "target_key": result["target_key"],
                "restored_release_id": result["restored_release_id"],
                "operator": result["operator"],
                "reason": result["reason"],
            },
        )
    except EvolutionContractError as exc:
        raise _contract_error(exc) from exc
    return receipt


@router.get("/state")
def evolution_state() -> dict[str, object]:
    try:
        return {
            **_store().state(),
            "harness_registry": _registry_payload(),
        }
    except EvolutionContractError as exc:
        raise _contract_error(exc) from exc


__all__ = [
    "EVOLUTION_CONTROL_ROOT",
    "ArtifactImportBody",
    "EvolutionApprovalBody",
    "EvolutionCanaryActivateBody",
    "EvolutionGateBody",
    "EvolutionRollbackBody",
    "router",
]
