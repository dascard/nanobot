"""冻结计划、任务板、Agent handoff 与人工复核管理 API。"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.agent_collaboration import (
    AgentCollaborationAccessDenied,
    AgentCollaborationConflict,
    AgentCollaborationError,
    AgentCollaborationNotFound,
    require_agent_collaboration_enabled,
)
from core.agent_collaboration.service import SqlAlchemyAgentCollaborationService
from core.agent_orchestration import (
    AgentOrchestrationError,
    AgentPlanGovernanceService,
    SqlAlchemyAgentPlanRepository,
)
from core.agent_orchestration.serialization import (
    agent_orchestration_plan_from_dict,
)
from core.agent_runtime import (
    RuntimeActor,
    RuntimeActorType,
    RuntimeOwnerType,
    RuntimePrincipal,
    RuntimeRunIdentity,
)
from core.database import get_db
from core.db.session import session_factory_from_session
from core.lifecycle import FeatureScope


logger = logging.getLogger("nanobot.admin.collaboration")


router = APIRouter(
    prefix="/collaboration",
    tags=["admin-agent-collaboration"],
    dependencies=[Depends(verify_admin)],
)


class OwnerBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    platform: str = Field(min_length=1, max_length=64)
    owner_type: Literal["user", "group", "project", "system"]
    owner_id: str = Field(min_length=1, max_length=255)


class ActorBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    actor_type: Literal["user", "agent", "tool", "system", "adapter"]
    actor_id: str = Field(min_length=1, max_length=160)
    parent_actor_id: str = Field(default="", max_length=160)


class IdentityBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=160)
    correlation_id: str = Field(min_length=1, max_length=160)
    actor: ActorBody
    owner: OwnerBody


class PlanPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    identity: IdentityBody
    plan: dict[str, Any]
    proposed_by: str = Field(min_length=1, max_length=160)
    repair_reason_code: str = Field(default="", max_length=128)


class PlanApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    owner: OwnerBody
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_event_sequence: int = Field(ge=1)


class PlanFreezeBody(PlanApproveBody):
    approval_id: str = Field(min_length=1, max_length=160)


class BoardCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    identity: IdentityBody
    plan_id: str = Field(min_length=1, max_length=160)
    plan_revision: int = Field(ge=1)
    root_input: dict[str, Any]
    source_type: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=160)


class TaskClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    actor_id: str = Field(min_length=1, max_length=160)


class DeliveryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    actor_id: str = Field(min_length=1, max_length=160)
    lease_token: str = Field(min_length=1, max_length=64)
    lease_generation: int = Field(ge=1)
    attempt_no: int = Field(ge=1)
    output: dict[str, Any]


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    owner: OwnerBody
    expected_delivery_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: bool
    reason_code: str = Field(default="", max_length=128)


def _owner(body: OwnerBody) -> RuntimePrincipal:
    return RuntimePrincipal(
        body.platform,
        RuntimeOwnerType(body.owner_type),
        body.owner_id,
    )


def _identity(body: IdentityBody) -> RuntimeRunIdentity:
    return RuntimeRunIdentity(
        run_id=body.run_id,
        turn_id=body.turn_id,
        correlation_id=body.correlation_id,
        actor=RuntimeActor(
            RuntimeActorType(body.actor.actor_type),
            body.actor.actor_id,
            body.actor.parent_actor_id,
        ),
        owner=_owner(body.owner),
    )


def _owner_from_query(
    platform: str,
    owner_type: str,
    owner_id: str,
) -> RuntimePrincipal:
    try:
        return RuntimePrincipal(
            platform,
            RuntimeOwnerType(owner_type),
            owner_id,
        )
    except ValueError as exc:
        raise HTTPException(422, detail="owner 参数无效") from exc


def _view_payload(view: Any) -> dict[str, object]:
    return {
        "preview_id": view.record.preview_id,
        "state": view.state.value,
        "owner": view.record.owner.canonical_id,
        "plan": view.record.plan.to_dict(),
        "source_run_id": view.record.source_run_id,
        "source_turn_id": view.record.source_turn_id,
        "proposed_by": view.record.proposed_by,
        "proposed_at": view.record.proposed_at.isoformat(),
        "repair": view.record.repair.to_dict(),
        "latest_event_sequence": view.latest_event_sequence,
        "approval": (
            {
                "approval_id": view.approval.approval_id,
                "approved_by": view.approval.approved_by,
                "approved_at": view.approval.approved_at.isoformat(),
            }
            if view.approval is not None
            else None
        ),
        "freeze": (
            {
                "freeze_id": view.freeze.freeze_id,
                "approval_id": view.freeze.approval_id,
                "frozen_by": view.freeze.frozen_by,
                "frozen_at": view.freeze.frozen_at.isoformat(),
            }
            if view.freeze is not None
            else None
        ),
    }


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, AgentCollaborationNotFound):
        raise HTTPException(
            404,
            detail={"code": exc.code, "message": exc.safe_message},
        ) from exc
    if isinstance(exc, AgentCollaborationAccessDenied):
        raise HTTPException(
            403,
            detail={"code": exc.code, "message": exc.safe_message},
        ) from exc
    if isinstance(exc, AgentCollaborationConflict):
        raise HTTPException(
            409,
            detail={"code": exc.code, "message": exc.safe_message},
        ) from exc
    if isinstance(exc, AgentCollaborationError):
        status = 409 if exc.code == "agent_collaboration_disabled" else 503
        raise HTTPException(
            status,
            detail={"code": exc.code, "message": exc.safe_message},
        ) from exc
    if isinstance(exc, AgentOrchestrationError):
        raise HTTPException(
            409,
            detail={"code": exc.code, "message": exc.summary},
        ) from exc
    if isinstance(exc, (TypeError, ValueError)):
        raise HTTPException(
            422,
            detail={"code": "collaboration_input_invalid", "message": str(exc)},
        ) from exc
    raise HTTPException(
        503,
        detail={
            "code": "collaboration_unavailable",
            "message": "协作服务暂时不可用",
        },
    ) from exc


def _require_admin_feature() -> None:
    require_agent_collaboration_enabled(FeatureScope.ADMIN)


@router.post("/plans/preview")
def preview_plan(
    body: PlanPreviewBody,
    db: Session = Depends(get_db),
):
    try:
        _require_admin_feature()
        service = AgentPlanGovernanceService(SqlAlchemyAgentPlanRepository(db))
        view = service.preview(
            agent_orchestration_plan_from_dict(body.plan),
            identity=_identity(body.identity),
            proposed_by=body.proposed_by,
            repair_reason_code=body.repair_reason_code,
        )
        db.commit()
        return _view_payload(view)
    except Exception as exc:
        db.rollback()
        _raise_http(exc)


@router.get("/plans/{plan_id}/revisions/{revision}")
def get_plan_revision(
    plan_id: str,
    revision: int,
    owner_platform: str = Query(..., min_length=1, max_length=64),
    owner_type: str = Query(...),
    owner_id: str = Query(..., min_length=1, max_length=255),
    db: Session = Depends(get_db),
):
    try:
        _require_admin_feature()
        owner = _owner_from_query(owner_platform, owner_type, owner_id)
        view = AgentPlanGovernanceService(
            SqlAlchemyAgentPlanRepository(db)
        ).get_revision(plan_id, revision, owner=owner)
        if view is None:
            raise AgentCollaborationNotFound(
                "collaboration_plan_not_found",
                "计划 revision 不存在或 owner 不匹配",
            )
        return _view_payload(view)
    except Exception as exc:
        _raise_http(exc)


@router.post("/plans/{plan_id}/revisions/{revision}/approve")
def approve_plan(
    plan_id: str,
    revision: int,
    body: PlanApproveBody,
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    try:
        _require_admin_feature()
        view = AgentPlanGovernanceService(
            SqlAlchemyAgentPlanRepository(db)
        ).approve(
            plan_id=plan_id,
            revision=revision,
            plan_sha256=body.plan_sha256,
            owner=_owner(body.owner),
            approved_by=f"admin:{_auth}",
            expected_event_sequence=body.expected_event_sequence,
        )
        db.commit()
        return _view_payload(view)
    except Exception as exc:
        db.rollback()
        _raise_http(exc)


@router.post("/plans/{plan_id}/revisions/{revision}/freeze")
def freeze_plan(
    plan_id: str,
    revision: int,
    body: PlanFreezeBody,
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    try:
        _require_admin_feature()
        view = AgentPlanGovernanceService(
            SqlAlchemyAgentPlanRepository(db)
        ).freeze(
            plan_id=plan_id,
            revision=revision,
            plan_sha256=body.plan_sha256,
            approval_id=body.approval_id,
            owner=_owner(body.owner),
            frozen_by=f"admin:{_auth}",
            expected_event_sequence=body.expected_event_sequence,
        )
        db.commit()
        return _view_payload(view)
    except Exception as exc:
        db.rollback()
        _raise_http(exc)


@router.post("/boards")
def create_board(
    body: BoardCreateBody,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    try:
        service = SqlAlchemyAgentCollaborationService(
            db,
            session_factory=session_factory_from_session(db),
        )
        board = service.create_board(
            identity=_identity(body.identity),
            plan_id=body.plan_id,
            plan_revision=body.plan_revision,
            root_input=body.root_input,
            source_type=body.source_type,
            source_id=body.source_id,
            created_by=f"admin:{_auth}",
            idempotency_key=idempotency_key,
            scope=FeatureScope.ADMIN,
        )
        db.commit()
        return service.board_view(
            board_id=board.board_id,
            owner=board.identity.owner,
            scope=FeatureScope.ADMIN,
        )
    except Exception as exc:
        db.rollback()
        _raise_http(exc)


@router.get("/boards/{board_id}")
def get_board(
    board_id: str,
    owner_platform: str = Query(..., min_length=1, max_length=64),
    owner_type: str = Query(...),
    owner_id: str = Query(..., min_length=1, max_length=255),
    db: Session = Depends(get_db),
):
    try:
        return SqlAlchemyAgentCollaborationService(db).board_view(
            board_id=board_id,
            owner=_owner_from_query(owner_platform, owner_type, owner_id),
            scope=FeatureScope.ADMIN,
        )
    except Exception as exc:
        _raise_http(exc)


@router.post("/boards/{board_id}/tasks/{task_id}/claim")
def claim_task(
    board_id: str,
    task_id: str,
    body: TaskClaimBody,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    try:
        claim = SqlAlchemyAgentCollaborationService(db).claim_task(
            board_id=board_id,
            task_id=task_id,
            actor_id=body.actor_id,
            idempotency_key=idempotency_key,
            require_invitation=False,
            scope=FeatureScope.ADMIN,
        )
        db.commit()
        return claim.to_dict()
    except Exception as exc:
        db.rollback()
        _raise_http(exc)


@router.post("/boards/{board_id}/tasks/{task_id}/deliver")
def deliver_task(
    board_id: str,
    task_id: str,
    body: DeliveryBody,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    try:
        result = SqlAlchemyAgentCollaborationService(db).submit_delivery(
            board_id=board_id,
            task_id=task_id,
            actor_id=body.actor_id,
            lease_token=body.lease_token,
            lease_generation=body.lease_generation,
            attempt_no=body.attempt_no,
            output_payload=body.output,
            idempotency_key=idempotency_key,
            scope=FeatureScope.ADMIN,
        )
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        _raise_http(exc)


@router.post("/boards/{board_id}/deliveries/{delivery_id}/review")
async def review_delivery(
    board_id: str,
    delivery_id: str,
    body: ReviewBody,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_admin),
):
    try:
        owner = _owner(body.owner)
        service = SqlAlchemyAgentCollaborationService(
            db,
            session_factory=session_factory_from_session(db),
        )
        result = service.review_delivery(
            board_id=board_id,
            delivery_id=delivery_id,
            expected_delivery_sha256=body.expected_delivery_sha256,
            owner=owner,
            reviewer_id=f"admin:{_auth}",
            approved=body.approved,
            reason_code=body.reason_code,
            idempotency_key=idempotency_key,
            scope=FeatureScope.ADMIN,
        )
        db.commit()
        checkpoint = None
        checkpoint_pending = False
        if body.approved:
            try:
                checkpoint = await service.advance_checkpoints(
                    board_id=board_id,
                    owner=owner,
                )
            except Exception:
                checkpoint_pending = True
                logger.exception(
                    "人工审批已提交，但协作 checkpoint 推进失败"
                )
        return {
            **result,
            "checkpoint_id": checkpoint.checkpoint_id if checkpoint else "",
            "checkpoint_sequence": checkpoint.sequence if checkpoint else 0,
            "checkpoint_pending": checkpoint_pending,
        }
    except Exception as exc:
        db.rollback()
        _raise_http(exc)


__all__ = ["router"]
