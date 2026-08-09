"""Session Goal 的显式管理员批准入口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.admin.common import (
    AuthenticatedAdminPrincipal,
    stage_audit_request,
    verify_admin,
)
from api.session_goal_routes import session_goal_snapshot_payload
from core.database import get_db
from core.session_goal import (
    SessionGoalConflictError,
    SessionGoalNotFoundError,
    SessionGoalService,
    SessionGoalValidationError,
)
from core.session_goal_control import (
    SessionGoalControlIdentity,
    SessionGoalControlIdentityIntegrityError,
    SessionGoalControlIdentityNotFound,
    resolve_session_goal_control_identity,
)


router = APIRouter(prefix="/session-goals", tags=["admin-session-goals"])


class AdminSessionPlanApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    gateway_run_id: str = Field(min_length=1, max_length=160)
    expected_version: int = Field(ge=1)
    expected_plan_revision: int = Field(ge=1)
    expected_plan_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    reason: str = Field(min_length=1, max_length=512)


def _control_identity(
    db: Session,
    gateway_run_id: str,
) -> SessionGoalControlIdentity:
    try:
        return resolve_session_goal_control_identity(db, gateway_run_id)
    except SessionGoalControlIdentityNotFound as exc:
        raise HTTPException(404, "Gateway Run 不存在") from exc
    except SessionGoalControlIdentityIntegrityError as exc:
        raise HTTPException(503, "Gateway 身份事实不可用") from exc


def _raise_session_goal_error(exc: Exception) -> None:
    if isinstance(exc, SessionGoalNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, SessionGoalConflictError):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, SessionGoalValidationError):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, IntegrityError):
        raise HTTPException(409, "Session Goal 并发写入冲突") from exc
    raise exc


@router.post("/{goal_id}/approve")
def approve_session_plan_as_admin(
    goal_id: str,
    body: AdminSessionPlanApproveRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: AuthenticatedAdminPrincipal = Depends(verify_admin),
):
    """以独立 Admin scope 批准绑定计划，不模拟用户 actor。"""

    identity = _control_identity(db, body.gateway_run_id)
    if (
        not isinstance(admin, AuthenticatedAdminPrincipal)
        or not admin.has_scope("session_goal:approve")
    ):
        raise HTTPException(403, "当前 Admin 主体无 Session Goal 批准权限")
    admin_id = admin.subject
    approver_id = f"admin:{admin_id}"
    try:
        snapshot = SessionGoalService(db).approve(
            goal_id=goal_id,
            principal=identity.principal,
            expected_version=body.expected_version,
            expected_plan_revision=body.expected_plan_revision,
            expected_plan_sha256=body.expected_plan_sha256,
            approver_id=approver_id,
            source_run_id=identity.gateway_run_id,
        )
        stage_audit_request(
            db,
            request,
            "session_goal.admin_approve",
            "session_goal",
            goal_id,
            {
                "scope": "session_goal:approve",
                "gateway_run_id": identity.gateway_run_id,
                "gateway_binding_id": identity.gateway_binding_id,
                "owner": {
                    "platform": identity.principal.platform,
                    "owner_type": identity.principal.owner_type,
                    "owner_id": identity.principal.owner_id,
                    "session_id": identity.principal.session_id,
                },
                "approver_id": approver_id,
                "expected_version": body.expected_version,
                "plan_revision": body.expected_plan_revision,
                "plan_sha256": body.expected_plan_sha256.lower(),
                "reason": body.reason,
            },
            admin_user=admin_id,
            event_id=(
                f"session_goal.admin_approve:{goal_id}:"
                f"{body.expected_version}"
            ),
        )
        db.commit()
    except (
        SessionGoalNotFoundError,
        SessionGoalConflictError,
        SessionGoalValidationError,
        IntegrityError,
    ) as exc:
        db.rollback()
        _raise_session_goal_error(exc)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            500,
            "Session Goal 管理批准未提交",
        ) from exc
    except BaseException:
        db.rollback()
        raise
    return session_goal_snapshot_payload(snapshot)


__all__ = [
    "AdminSessionPlanApproveRequest",
    "approve_session_plan_as_admin",
    "router",
]
