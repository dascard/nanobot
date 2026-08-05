"""统一 Gateway 会话状态与远程 Run 控制管理 API。"""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.database import get_db
from core.gateway_control import (
    GatewayControlAccessDenied,
    GatewayControlConflict,
    GatewayControlError,
    GatewayControlIntegrityError,
    GatewayControlNotFound,
    GatewayControlPrincipal,
    SqlAlchemyGatewayControlService,
    get_gateway_model_profile_port,
)


logger = logging.getLogger("nanobot.admin.gateway_control")
router = APIRouter(
    prefix="/gateway-control",
    tags=["admin-gateway-control"],
    dependencies=[Depends(verify_admin)],
)


class StopRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=160)
    reason_code: str = Field(min_length=1, max_length=64)


class ResumeRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=160)


class ModelSwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=160)
    profile_id: str = Field(min_length=1, max_length=160)
    expected_generation: int = Field(ge=1)


def _principal(admin_id: str) -> GatewayControlPrincipal:
    return GatewayControlPrincipal.admin(admin_id)


def _model_profiles() -> list[dict[str, object]]:
    return [
        profile.to_payload()
        for profile in get_gateway_model_profile_port().list_profiles()
    ]


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, GatewayControlAccessDenied):
        raise HTTPException(403, detail={"code": exc.code, "message": str(exc)})
    if isinstance(exc, GatewayControlNotFound):
        raise HTTPException(404, detail={"code": exc.code, "message": str(exc)})
    if isinstance(exc, GatewayControlConflict):
        raise HTTPException(409, detail={"code": exc.code, "message": str(exc)})
    if isinstance(exc, GatewayControlIntegrityError):
        raise HTTPException(
            503,
            detail={"code": exc.code, "message": "Gateway 控制事实暂不可用"},
        )
    if isinstance(exc, ValueError):
        raise HTTPException(
            422,
            detail={"code": "gateway_control_input_invalid", "message": str(exc)},
        )
    if isinstance(exc, GatewayControlError):
        raise HTTPException(
            503,
            detail={"code": exc.code, "message": "Gateway 控制服务暂不可用"},
        )
    raise HTTPException(
        503,
        detail={
            "code": "gateway_control_unavailable",
            "message": "Gateway 控制服务暂不可用",
        },
    )


def _log_unexpected(action: str, run_id: str, exc: Exception) -> None:
    if isinstance(exc, (GatewayControlError, ValueError)):
        return
    logger.error(
        "%s run_id_sha256=%s error_type=%s",
        action,
        hashlib.sha256(str(run_id).encode("utf-8")).hexdigest(),
        type(exc).__name__,
    )


@router.get("/model-profiles")
def list_gateway_model_profiles():
    try:
        return {"items": _model_profiles()}
    except Exception as exc:
        _log_unexpected("列出 Gateway 模型 Profile 失败", "", exc)
        _raise_http(exc)


@router.get("/runs/{run_id}")
def get_gateway_run_status(
    run_id: str,
    db: Session = Depends(get_db),
    admin_id: str = Depends(verify_admin),
):
    try:
        return SqlAlchemyGatewayControlService(db).status(
            run_id,
            _principal(admin_id),
        )
    except Exception as exc:
        _log_unexpected("读取 Gateway Run 状态失败", run_id, exc)
        _raise_http(exc)


@router.post("/runs/{run_id}/stop")
def stop_gateway_run(
    run_id: str,
    body: StopRunBody,
    db: Session = Depends(get_db),
    admin_id: str = Depends(verify_admin),
):
    try:
        return SqlAlchemyGatewayControlService(db).stop(
            run_id=run_id,
            request_id=body.request_id,
            reason_code=body.reason_code,
            principal=_principal(admin_id),
        )
    except Exception as exc:
        db.rollback()
        _log_unexpected("停止 Gateway Run 失败", run_id, exc)
        _raise_http(exc)


@router.post("/runs/{run_id}/resume")
def authorize_gateway_run_resume(
    run_id: str,
    body: ResumeRunBody,
    db: Session = Depends(get_db),
    admin_id: str = Depends(verify_admin),
):
    """授权从原渠道继续；实际消息仍须走该渠道的标准入站路径。"""

    try:
        return SqlAlchemyGatewayControlService(db).authorize_resume(
            run_id=run_id,
            request_id=body.request_id,
            principal=_principal(admin_id),
        )
    except Exception as exc:
        db.rollback()
        _log_unexpected("授权 Gateway Run 继续失败", run_id, exc)
        _raise_http(exc)


@router.post("/runs/{run_id}/model-switch")
def switch_gateway_run_model(
    run_id: str,
    body: ModelSwitchBody,
    db: Session = Depends(get_db),
    admin_id: str = Depends(verify_admin),
):
    try:
        profiles = _model_profiles()
        return SqlAlchemyGatewayControlService(db).switch_model(
            run_id=run_id,
            request_id=body.request_id,
            profile_id=body.profile_id,
            expected_generation=body.expected_generation,
            available_profile_ids=[
                str(item.get("profile_id") or "") for item in profiles
            ],
            principal=_principal(admin_id),
        )
    except Exception as exc:
        db.rollback()
        _log_unexpected("切换 Gateway Run 模型失败", run_id, exc)
        _raise_http(exc)


__all__ = [
    "ModelSwitchBody",
    "ResumeRunBody",
    "StopRunBody",
    "authorize_gateway_run_resume",
    "get_gateway_run_status",
    "list_gateway_model_profiles",
    "router",
    "stop_gateway_run",
    "switch_gateway_run_model",
]
