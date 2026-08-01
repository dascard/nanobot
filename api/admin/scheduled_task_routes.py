"""WebUI 管理员定时任务（触发器）路由。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from core.database import ScheduledTask, ScheduledTaskExecution, get_db
from core.schedule_spec import (
    KIND_CRON,
    KIND_INTERVAL,
    KIND_ONCE,
    ScheduleSpecError,
    resolve_schedule_fields,
    schedule_display,
    spec_from_fields,
    utc_now_naive,
)
from core.scheduled_task_contract import (
    MAX_SCHEDULED_TASK_NAME_CHARS,
    MAX_SCHEDULED_TASK_PROGRAM_BYTES,
    MAX_SCHEDULED_TASK_PROMPT_CHARS,
    ScheduledTaskContractError,
    apply_scheduled_task_owner,
    apply_scheduled_task_program,
    normalize_scheduled_task_definition,
    scheduled_task_owner_from_target,
)
from core.scheduled_task_outbound import (
    ScheduledTaskNotFoundError,
    cancel_scheduled_task_deliveries,
)
from core.scheduled_workflow import enqueue_scheduled_task_execution


router = APIRouter(prefix="/triggers", tags=["admin-triggers"])


class TriggerUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=MAX_SCHEDULED_TASK_NAME_CHARS,
    )
    schedule: str = Field(min_length=1, max_length=255)
    target_type: Literal["private", "group"] = "private"
    target_id: str = Field(min_length=1, max_length=512)
    prompt_template: str | None = Field(
        default=None,
        max_length=MAX_SCHEDULED_TASK_PROMPT_CHARS,
    )
    content: str | None = Field(
        default=None,
        max_length=MAX_SCHEDULED_TASK_PROGRAM_BYTES,
    )
    program: dict[str, Any] | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_definition_source(self) -> "TriggerUpsertRequest":
        sources = (
            self.prompt_template is not None,
            self.content is not None,
            self.program is not None,
        )
        if sum(sources) != 1:
            raise ValueError(
                "prompt_template、content 和 program 必须且只能填写一个"
            )
        return self


class TriggerVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class TriggerRunRequest(TriggerVersionRequest):
    request_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _load_program(task: ScheduledTask) -> tuple[dict[str, Any] | None, str]:
    raw = str(task.program_json or "").strip()
    try:
        if raw:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("program 不是对象")
            return parsed, ""
        program = normalize_scheduled_task_definition(
            name=task.name,
            prompt_template=task.prompt_template,
        )[2]
        return program, ""
    except (ScheduledTaskContractError, TypeError, ValueError):
        return None, "任务 program 无法解析，请修复持久化定义后再编辑"


def _schedule_payload(task: ScheduledTask) -> tuple[str, str]:
    spec = spec_from_fields(
        task.schedule_kind,
        task.schedule_spec,
        task.cron_expr,
    )
    if spec is None:
        return str(task.cron_expr or ""), "无法解析"
    kind = str(spec.get("kind") or "")
    if kind == KIND_CRON:
        schedule_input = str(spec.get("expr") or "")
    elif kind == KIND_INTERVAL:
        schedule_input = f"every {int(spec.get('minutes') or 0)}m"
    elif kind == KIND_ONCE:
        schedule_input = str(spec.get("run_at") or "")
    else:
        schedule_input = str(task.cron_expr or "")
    return schedule_input, schedule_display(spec)


def _latest_execution(
    db: Session,
    task_id: int,
) -> dict[str, Any] | None:
    row = (
        db.query(ScheduledTaskExecution)
        .filter(ScheduledTaskExecution.task_id == int(task_id))
        .order_by(ScheduledTaskExecution.id.desc())
        .first()
    )
    if row is None:
        return None
    return {
        "execution_id": int(row.id),
        "status": str(row.status),
        "trigger_type": str(row.trigger_type or ""),
        "current_step_id": str(row.current_step_id or ""),
        "error_code": str(row.last_error_code or ""),
        "error_summary": str(row.last_error_summary or ""),
        "scheduled_for": _utc_iso(row.scheduled_for),
        "started_at": _utc_iso(row.started_at),
        "finished_at": _utc_iso(row.finished_at),
    }


def _definition_payload(
    task: ScheduledTask,
    program: dict[str, Any] | None,
) -> dict[str, Any]:
    prompt = str(task.prompt_template or "")
    steps = program.get("steps") if isinstance(program, Mapping) else None
    if (
        prompt
        and isinstance(steps, list)
        and [step.get("op") for step in steps if isinstance(step, Mapping)]
        == ["model", "emit"]
    ):
        mode = "prompt"
        content = ""
    elif (
        isinstance(steps, list)
        and len(steps) == 1
        and isinstance(steps[0], Mapping)
        and steps[0].get("op") == "emit"
        and isinstance(steps[0].get("content"), str)
    ):
        mode = "content"
        content = str(steps[0]["content"])
    else:
        mode = "program"
        content = ""
    return {
        "mode": mode,
        "prompt_template": prompt,
        "content": content,
        "program": program,
    }


def _task_payload(
    db: Session,
    task: ScheduledTask,
    *,
    detail: bool,
) -> dict[str, Any]:
    schedule_input, display = _schedule_payload(task)
    program, program_error = _load_program(task)
    payload: dict[str, Any] = {
        "id": int(task.id),
        "name": str(task.name or ""),
        "enabled": bool(task.enabled),
        "schedule": schedule_input,
        "schedule_display": display,
        "schedule_kind": str(task.schedule_kind or ""),
        "next_fire_at": _utc_iso(task.next_fire_at),
        "target_type": str(task.target_type or ""),
        "target_id": str(task.target_id or ""),
        "owner_chat_stream_id": str(task.owner_chat_stream_id or ""),
        "owner_migration_required": bool(task.owner_migration_required),
        "definition_version": int(task.definition_version or 1),
        "delivery_status": str(task.delivery_status or ""),
        "last_attempt_at": _utc_iso(task.last_attempt_at),
        "last_success_at": _utc_iso(task.last_success_at),
        "last_error_summary": str(task.last_error_summary or ""),
        "latest_execution": _latest_execution(db, int(task.id)),
        "created_at": _iso(task.created_at),
        "updated_at": _iso(task.updated_at),
        "program_error": program_error,
    }
    if detail:
        payload["definition"] = _definition_payload(task, program)
        payload["program_sha256"] = str(task.program_sha256 or "")
    return payload


def _task_or_404(db: Session, task_id: int) -> ScheduledTask:
    task = db.get(ScheduledTask, int(task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="触发器不存在")
    return task


def _check_version(task: ScheduledTask, expected_version: int) -> None:
    current = int(task.definition_version or 1)
    if current != int(expected_version):
        raise HTTPException(
            status_code=409,
            detail=f"触发器已被其他操作更新（当前版本 {current}），请刷新后重试",
        )


def _normalize_request(
    body: TriggerUpsertRequest,
    *,
    actor: str,
):
    try:
        fields = resolve_schedule_fields(
            schedule=body.schedule,
            cron_expr=None,
            now_utc=utc_now_naive(),
        )
        owner = scheduled_task_owner_from_target(
            target_type=body.target_type,
            target_id=body.target_id,
            platform="qq",
            created_by_actor_id=f"admin:{actor}",
        )
        definition = normalize_scheduled_task_definition(
            name=body.name,
            prompt_template=body.prompt_template,
            content=body.content,
            program=body.program,
        )
        return fields, owner, definition
    except (ScheduleSpecError, ScheduledTaskContractError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def list_triggers(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    tasks = (
        db.query(ScheduledTask)
        .order_by(ScheduledTask.created_at.desc(), ScheduledTask.id.desc())
        .all()
    )
    return {
        "items": [
            _task_payload(db, task, detail=False)
            for task in tasks
        ],
        "total": len(tasks),
    }


@router.get("/{task_id}")
def get_trigger(
    task_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    return _task_payload(db, _task_or_404(db, task_id), detail=True)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_trigger(
    body: TriggerUpsertRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin_user: str = Depends(verify_admin),
):
    fields, owner, definition = _normalize_request(body, actor=admin_user)
    name, prompt, program, _program_json, _program_sha256 = definition
    task = ScheduledTask(
        cron_expr=fields.cron_expr,
        schedule_kind=fields.schedule_kind,
        schedule_spec=fields.schedule_spec,
        next_fire_at=fields.next_fire_at,
        target_type=owner.target_type,
        target_id=owner.target_id,
        definition_version=1,
        enabled=1,
        updated_at=utc_now_naive(),
    )
    apply_scheduled_task_program(
        task,
        name=name,
        prompt_template=prompt,
        program=program,
    )
    apply_scheduled_task_owner(task, owner)
    db.add(task)
    db.commit()
    db.refresh(task)
    audit_request(
        db,
        request,
        "trigger_create",
        "scheduled_task",
        str(task.id),
        {
            "definition_version": 1,
            "schedule_kind": fields.schedule_kind,
            "target_type": owner.target_type,
        },
    )
    return _task_payload(db, task, detail=True)


@router.put("/{task_id}")
def update_trigger(
    body: TriggerUpsertRequest,
    request: Request,
    task_id: int,
    db: Session = Depends(get_db),
    admin_user: str = Depends(verify_admin),
):
    task = _task_or_404(db, task_id)
    if body.expected_version is None:
        raise HTTPException(status_code=422, detail="更新触发器必须提供 expected_version")
    _check_version(task, body.expected_version)
    fields, owner, definition = _normalize_request(body, actor=admin_user)
    name, prompt, program, _program_json, _program_sha256 = definition
    cancellation = cancel_scheduled_task_deliveries(
        db,
        task=task,
        reason_type="task_updated",
        safe_summary="管理员修改任务定义",
    )
    if cancellation.unsafe:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="触发器仍有投递中或结果不确定记录，暂时不能修改",
        )
    task.cron_expr = fields.cron_expr
    task.schedule_kind = fields.schedule_kind
    task.schedule_spec = fields.schedule_spec
    task.next_fire_at = fields.next_fire_at
    task.target_type = owner.target_type
    task.target_id = owner.target_id
    apply_scheduled_task_program(
        task,
        name=name,
        prompt_template=prompt,
        program=program,
    )
    apply_scheduled_task_owner(task, owner)
    task.definition_version = int(task.definition_version or 0) + 1
    task.updated_at = utc_now_naive()
    db.commit()
    db.refresh(task)
    audit_request(
        db,
        request,
        "trigger_update",
        "scheduled_task",
        str(task.id),
        {
            "definition_version": int(task.definition_version),
            "schedule_kind": fields.schedule_kind,
            "target_type": owner.target_type,
        },
    )
    return _task_payload(db, task, detail=True)


@router.post("/{task_id}/toggle")
def toggle_trigger(
    body: TriggerVersionRequest,
    request: Request,
    task_id: int,
    db: Session = Depends(get_db),
    _admin_user: str = Depends(verify_admin),
):
    task = _task_or_404(db, task_id)
    _check_version(task, body.expected_version)
    cancellation = cancel_scheduled_task_deliveries(
        db,
        task=task,
        reason_type="task_toggled",
        safe_summary="管理员修改任务启停状态",
    )
    if cancellation.unsafe:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="触发器仍有投递中或结果不确定记录，暂时不能切换状态",
        )
    task.enabled = 0 if task.enabled else 1
    if task.enabled:
        task.next_fire_at = None
    task.definition_version = int(task.definition_version or 0) + 1
    task.updated_at = utc_now_naive()
    db.commit()
    db.refresh(task)
    audit_request(
        db,
        request,
        "trigger_toggle",
        "scheduled_task",
        str(task.id),
        {
            "enabled": bool(task.enabled),
            "definition_version": int(task.definition_version),
        },
    )
    return _task_payload(db, task, detail=False)


@router.post("/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_trigger(
    body: TriggerRunRequest,
    request: Request,
    task_id: int,
    db: Session = Depends(get_db),
    _admin_user: str = Depends(verify_admin),
):
    task = _task_or_404(db, task_id)
    _check_version(task, body.expected_version)
    try:
        result = enqueue_scheduled_task_execution(
            db,
            task_id=int(task.id),
            trigger_type="manual",
            manual_idempotency_key=body.request_id,
        )
    except ScheduledTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="触发器不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="手动执行请求无效") from exc
    db.commit()
    audit_request(
        db,
        request,
        "trigger_run",
        "scheduled_task",
        str(task.id),
        {
            "execution_id": int(result.execution_id),
            "deduplicated": bool(result.deduplicated),
        },
    )
    return {
        "status": result.status,
        "execution_id": int(result.execution_id),
        "deduplicated": bool(result.deduplicated),
    }


__all__ = ["router"]
