"""Session Goal 与 Plan Mode 的受信控制面。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.database import get_db
from core.session_goal import (
    MAX_COMPLETION_CRITERIA,
    MAX_COMPLETION_CRITERION_CHARS,
    MAX_GOAL_OBJECTIVE_CHARS,
    SessionGoalBudget,
    SessionGoalConflictError,
    SessionGoalNotFoundError,
    SessionGoalPrincipal,
    SessionGoalService,
    SessionGoalSnapshot,
    SessionGoalStatus,
    SessionGoalValidationError,
    SessionPlanAsset,
)


router = APIRouter(tags=["session-goals"])


class SessionGoalPrincipalInput(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    owner_type: Literal["user", "group", "project"]
    owner_id: str = Field(min_length=1, max_length=255)
    session_id: str = Field(min_length=1, max_length=255)


class SessionGoalBudgetInput(BaseModel):
    max_model_steps: int = Field(default=64, ge=1, le=10_000)
    max_tool_calls: int = Field(default=128, ge=1, le=100_000)
    max_input_tokens: int = Field(default=1_000_000, ge=1, le=1_000_000_000)
    max_output_tokens: int = Field(default=200_000, ge=1, le=1_000_000_000)
    max_cost_microunits: int = Field(
        default=50_000_000,
        ge=1,
        le=10_000_000_000_000,
    )
    max_elapsed_seconds: int = Field(default=86_400, ge=1, le=31_536_000)


class SessionGoalCreateRequest(BaseModel):
    principal: SessionGoalPrincipalInput
    objective: str = Field(
        min_length=1,
        max_length=MAX_GOAL_OBJECTIVE_CHARS,
    )
    completion_criteria: list[
        Annotated[
            str,
            Field(min_length=1, max_length=MAX_COMPLETION_CRITERION_CHARS),
        ]
    ] = Field(min_length=1, max_length=MAX_COMPLETION_CRITERIA)
    budget: SessionGoalBudgetInput = Field(default_factory=SessionGoalBudgetInput)
    actor_id: str = Field(min_length=1, max_length=255)


class SessionGoalMutationRequest(BaseModel):
    principal: SessionGoalPrincipalInput
    expected_version: int = Field(ge=1)
    actor_id: str = Field(min_length=1, max_length=255)


class SessionPlanWriteRequest(SessionGoalMutationRequest):
    content: str = Field(min_length=1, max_length=262_144)
    source_run_id: str = Field(default="", max_length=160)


class SessionPlanApproveRequest(SessionGoalMutationRequest):
    expected_plan_revision: int = Field(ge=1)
    expected_plan_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )


class SessionGoalFinishRequest(SessionGoalMutationRequest):
    status: Literal["completed", "cancelled", "failed"]
    reason: str = Field(min_length=1, max_length=512)


def _principal(value: SessionGoalPrincipalInput) -> SessionGoalPrincipal:
    return SessionGoalPrincipal(**value.model_dump())


def _snapshot_payload(snapshot: SessionGoalSnapshot) -> dict[str, object]:
    return {
        "goal_id": snapshot.goal_id,
        "principal": asdict(snapshot.principal),
        "objective": snapshot.objective,
        "completion_criteria": list(snapshot.completion_criteria),
        "budget": asdict(snapshot.budget),
        "status": snapshot.status.value,
        "mode": snapshot.mode.value,
        "version": snapshot.version,
        "latest_plan_revision": snapshot.latest_plan_revision,
        "latest_plan_sha256": snapshot.latest_plan_sha256,
        "approved_plan_revision": snapshot.approved_plan_revision,
        "approved_plan_sha256": snapshot.approved_plan_sha256,
        "approved_by": snapshot.approved_by,
        "approved_at": snapshot.approved_at,
        "execution_started_at": snapshot.execution_started_at,
        "finished_at": snapshot.finished_at,
        "terminal_reason": snapshot.terminal_reason,
        "created_at": snapshot.created_at,
        "updated_at": snapshot.updated_at,
        "snapshot_sha256": snapshot.snapshot_sha256,
    }


def _plan_payload(plan: SessionPlanAsset | None) -> dict[str, object] | None:
    if plan is None:
        return None
    return {
        "revision": plan.revision,
        "content": plan.content,
        "content_sha256": plan.content_sha256,
        "size_bytes": plan.size_bytes,
        "source_run_id": plan.source_run_id,
        "created_by": plan.created_by,
        "created_at": plan.created_at,
    }


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SessionGoalNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, SessionGoalConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, SessionGoalValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, IntegrityError):
        return HTTPException(status_code=409, detail="Session Goal 并发写入冲突")
    raise exc


def _commit(db: Session, operation):
    try:
        result = operation()
        db.commit()
        return result
    except (SessionGoalNotFoundError, SessionGoalConflictError,
            SessionGoalValidationError, IntegrityError) as exc:
        db.rollback()
        raise _http_error(exc) from exc
    except BaseException:
        db.rollback()
        raise


@router.post("/session-goals")
def create_session_goal(
    request: SessionGoalCreateRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    snapshot = _commit(
        db,
        lambda: SessionGoalService(db).create_goal(
            principal=_principal(request.principal),
            objective=request.objective,
            completion_criteria=request.completion_criteria,
            budget=SessionGoalBudget(**request.budget.model_dump()),
            actor_id=request.actor_id,
        ),
    )
    return _snapshot_payload(snapshot)


@router.get("/session-goals/{goal_id}")
def get_session_goal(
    goal_id: str,
    platform: str = Query(min_length=1, max_length=32),
    owner_type: Literal["user", "group", "project"] = Query(),
    owner_id: str = Query(min_length=1, max_length=255),
    session_id: str = Query(min_length=1, max_length=255),
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    try:
        snapshot = SessionGoalService(db).get_goal(
            goal_id,
            SessionGoalPrincipal(platform, owner_type, owner_id, session_id),
        )
    except (SessionGoalNotFoundError, SessionGoalValidationError) as exc:
        raise _http_error(exc) from exc
    return _snapshot_payload(snapshot)


@router.get("/session-goals/{goal_id}/plan")
def get_session_plan(
    goal_id: str,
    platform: str = Query(min_length=1, max_length=32),
    owner_type: Literal["user", "group", "project"] = Query(),
    owner_id: str = Query(min_length=1, max_length=255),
    session_id: str = Query(min_length=1, max_length=255),
    revision: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    try:
        plan = SessionGoalService(db).get_plan(
            goal_id,
            SessionGoalPrincipal(platform, owner_type, owner_id, session_id),
            revision=revision,
        )
    except (SessionGoalNotFoundError, SessionGoalValidationError) as exc:
        raise _http_error(exc) from exc
    return {"goal_id": goal_id, "plan": _plan_payload(plan)}


@router.put("/session-goals/{goal_id}/plan")
def write_session_plan(
    goal_id: str,
    request: SessionPlanWriteRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    snapshot = _commit(
        db,
        lambda: SessionGoalService(db).write_plan(
            goal_id=goal_id,
            principal=_principal(request.principal),
            content=request.content,
            expected_version=request.expected_version,
            actor_id=request.actor_id,
            source_run_id=request.source_run_id,
        ),
    )
    return _snapshot_payload(snapshot)


@router.post("/session-goals/{goal_id}/request-approval")
def request_session_plan_approval(
    goal_id: str,
    request: SessionGoalMutationRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    snapshot = _commit(
        db,
        lambda: SessionGoalService(db).request_approval(
            goal_id=goal_id,
            principal=_principal(request.principal),
            expected_version=request.expected_version,
            actor_id=request.actor_id,
        ),
    )
    return _snapshot_payload(snapshot)


@router.post("/session-goals/{goal_id}/approve")
def approve_session_plan(
    goal_id: str,
    request: SessionPlanApproveRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    snapshot = _commit(
        db,
        lambda: SessionGoalService(db).approve(
            goal_id=goal_id,
            principal=_principal(request.principal),
            expected_version=request.expected_version,
            expected_plan_revision=request.expected_plan_revision,
            expected_plan_sha256=request.expected_plan_sha256,
            approver_id=request.actor_id,
        ),
    )
    return _snapshot_payload(snapshot)


@router.post("/session-goals/{goal_id}/start-execution")
def start_session_goal_execution(
    goal_id: str,
    request: SessionGoalMutationRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    snapshot = _commit(
        db,
        lambda: SessionGoalService(db).start_execution(
            goal_id=goal_id,
            principal=_principal(request.principal),
            expected_version=request.expected_version,
            actor_id=request.actor_id,
        ),
    )
    return _snapshot_payload(snapshot)


@router.post("/session-goals/{goal_id}/finish")
def finish_session_goal(
    goal_id: str,
    request: SessionGoalFinishRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    snapshot = _commit(
        db,
        lambda: SessionGoalService(db).finish(
            goal_id=goal_id,
            principal=_principal(request.principal),
            expected_version=request.expected_version,
            actor_id=request.actor_id,
            status=SessionGoalStatus(request.status),
            reason=request.reason,
        ),
    )
    return _snapshot_payload(snapshot)


__all__ = [
    "SessionGoalCreateRequest",
    "SessionGoalFinishRequest",
    "SessionGoalMutationRequest",
    "SessionPlanApproveRequest",
    "SessionPlanWriteRequest",
    "router",
]
