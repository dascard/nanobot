"""运行证据的保留、导出、法律保留与受控删除管理 API。"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.admin.common import client_ip, verify_admin
from core.database import get_db
from core.run_ledger.governance import (
    RunEvidenceAccessDenied,
    RunEvidenceConflict,
    RunEvidenceError,
    RunEvidenceIntegrityError,
    RunEvidenceNotFound,
    RunEvidencePolicyDenied,
    RunEvidencePrincipal,
    RunEvidenceRole,
    sha256_text,
)
from core.run_ledger.governance_service import RunEvidenceGovernanceService


logger = logging.getLogger("nanobot.admin.run_evidence")
router = APIRouter(tags=["admin-run-evidence"])


class LegalHoldBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason_code: Literal["audit", "incident", "legal", "user_dispute"]


class ErasurePreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason_code: Literal["retention_expired", "privacy_request"]


class ErasureBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=160)
    confirm_run_id: str = Field(min_length=1, max_length=160)
    reason_code: Literal["retention_expired", "privacy_request"]
    expected_manifest_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )


def _principal(admin_id: str) -> RunEvidencePrincipal:
    return RunEvidencePrincipal(
        role=RunEvidenceRole.ADMIN,
        principal_id=admin_id,
    )


def _raise_governance_http_error(exc: Exception) -> None:
    if isinstance(exc, RunEvidenceAccessDenied):
        raise HTTPException(
            403,
            detail={
                "code": exc.code,
                "message": "无权访问该运行证据",
            },
        ) from exc
    if isinstance(exc, RunEvidenceNotFound):
        raise HTTPException(
            404,
            detail={
                "code": exc.code,
                "message": "运行证据不存在",
            },
        ) from exc
    if isinstance(exc, RunEvidencePolicyDenied):
        raise HTTPException(
            409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if isinstance(exc, RunEvidenceConflict):
        raise HTTPException(
            409,
            detail={
                "code": exc.code,
                "message": "运行证据状态与请求预期不一致",
            },
        ) from exc
    if isinstance(exc, RunEvidenceIntegrityError):
        raise HTTPException(
            503,
            detail={
                "code": exc.code,
                "message": "运行证据暂时无法安全处理",
            },
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            422,
            detail={
                "code": "run_evidence_input_invalid",
                "message": "运行证据请求参数无效",
            },
        ) from exc
    if isinstance(exc, RunEvidenceError):
        raise HTTPException(
            503,
            detail={
                "code": exc.code,
                "message": "运行证据操作失败",
            },
        ) from exc
    raise HTTPException(
        503,
        detail={
            "code": "run_evidence_unavailable",
            "message": "运行证据服务暂时不可用",
        },
    ) from exc


def _log_unexpected(action: str, run_id: str, exc: Exception) -> None:
    if isinstance(exc, (RunEvidenceError, ValueError)):
        return
    logger.error(
        "%s run_id_sha256=%s error_type=%s",
        action,
        sha256_text(str(run_id)),
        type(exc).__name__,
    )


@router.get("/agent-runs/{run_id}/evidence/governance")
def get_run_evidence_governance(
    run_id: str,
    db: Session = Depends(get_db),
    admin_id: str = Depends(verify_admin),
):
    try:
        return RunEvidenceGovernanceService(db).governance_status(
            run_id,
            _principal(admin_id),
        )
    except Exception as exc:
        _log_unexpected("读取运行证据治理状态失败", run_id, exc)
        _raise_governance_http_error(exc)


@router.get("/agent-runs/{run_id}/evidence/export-manifest")
def export_run_evidence_manifest(
    run_id: str,
    db: Session = Depends(get_db),
    admin_id: str = Depends(verify_admin),
):
    try:
        return RunEvidenceGovernanceService(db).export_manifest(
            run_id,
            _principal(admin_id),
        ).to_dict()
    except Exception as exc:
        _log_unexpected("导出运行证据清单失败", run_id, exc)
        _raise_governance_http_error(exc)


@router.put("/agent-runs/{run_id}/evidence/legal-holds/{hold_id}")
def place_run_evidence_legal_hold(
    run_id: str,
    hold_id: str,
    body: LegalHoldBody,
    request: Request,
    db: Session = Depends(get_db),
    admin_id: str = Depends(verify_admin),
):
    try:
        return RunEvidenceGovernanceService(db).place_legal_hold(
            run_id=run_id,
            hold_id=hold_id,
            reason_code=body.reason_code,
            principal=_principal(admin_id),
            ip_address=client_ip(request),
        )
    except Exception as exc:
        _log_unexpected("设置运行证据法律保留失败", run_id, exc)
        _raise_governance_http_error(exc)


@router.delete("/agent-runs/{run_id}/evidence/legal-holds/{hold_id}")
def release_run_evidence_legal_hold(
    run_id: str,
    hold_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin_id: str = Depends(verify_admin),
):
    try:
        return RunEvidenceGovernanceService(db).release_legal_hold(
            run_id=run_id,
            hold_id=hold_id,
            principal=_principal(admin_id),
            ip_address=client_ip(request),
        )
    except Exception as exc:
        _log_unexpected("释放运行证据法律保留失败", run_id, exc)
        _raise_governance_http_error(exc)


@router.post("/agent-runs/{run_id}/evidence/erasure-preview")
def preview_run_evidence_erasure(
    run_id: str,
    body: ErasurePreviewBody,
    db: Session = Depends(get_db),
    admin_id: str = Depends(verify_admin),
):
    try:
        return RunEvidenceGovernanceService(db).erasure_preview(
            run_id=run_id,
            reason=body.reason_code,
            principal=_principal(admin_id),
        )
    except Exception as exc:
        _log_unexpected("预览运行证据删除失败", run_id, exc)
        _raise_governance_http_error(exc)


@router.delete("/agent-runs/{run_id}/evidence")
def erase_run_evidence(
    run_id: str,
    body: ErasureBody,
    request: Request,
    db: Session = Depends(get_db),
    admin_id: str = Depends(verify_admin),
):
    try:
        result = RunEvidenceGovernanceService(db).erase(
            run_id=run_id,
            request_id=body.request_id,
            confirm_run_id=body.confirm_run_id,
            reason=body.reason_code,
            expected_manifest_sha256=body.expected_manifest_sha256,
            principal=_principal(admin_id),
            ip_address=client_ip(request),
        )
        return result.to_dict()
    except Exception as exc:
        _log_unexpected("删除运行证据失败", run_id, exc)
        _raise_governance_http_error(exc)


__all__ = [
    "ErasureBody",
    "ErasurePreviewBody",
    "LegalHoldBody",
    "erase_run_evidence",
    "export_run_evidence_manifest",
    "get_run_evidence_governance",
    "place_run_evidence_legal_hold",
    "preview_run_evidence_erasure",
    "release_run_evidence_legal_hold",
    "router",
]
