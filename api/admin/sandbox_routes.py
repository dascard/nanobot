"""Sandbox 管理状态、无损关闭开关与单次运行取消。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from core.database import (
    Asset,
    SandboxRun,
    SystemSetting,
    Workspace,
    WorkspaceAsset,
    get_db,
)
from core.sandbox.client import HttpSandboxdBackend
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.tool_service import resolve_sandbox_setting
from core.settings_service import settings


router = APIRouter(prefix="/sandbox")
_RUN_STATUS = Literal["pending", "running", "completed", "failed", "cancelled"]


class SandboxKillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=255)


def _sandbox_backend(db: Session) -> HttpSandboxdBackend:
    return HttpSandboxdBackend(
        socket_path=str(resolve_sandbox_setting(db, "sandbox.sandboxd_socket")),
        token_file=str(resolve_sandbox_setting(db, "sandbox.sandboxd_token_file")),
        timeout_seconds=float(resolve_sandbox_setting(
            db,
            "sandbox.backend_timeout_seconds",
        )),
        run_timeout_seconds=float(resolve_sandbox_setting(
            db,
            "sandbox.run_timeout_seconds",
        )),
    )


def _safe_probe_error(error: SandboxServiceError) -> dict[str, object]:
    return {
        "ok": False,
        "summary": error.summary,
        "error": {
            "code": error.code.value,
            "retryable": error.retryable,
        },
    }


def _controller_status(db: Session) -> dict[str, object]:
    backend = _sandbox_backend(db)
    try:
        try:
            health = backend.health()
            health_state: dict[str, object] = {
                "ok": health.get("status") == "success",
                "service": str(
                    (health.get("data") or {}).get("service")
                    if isinstance(health.get("data"), dict)
                    else ""
                )[:64],
            }
        except SandboxServiceError as exc:
            health_state = _safe_probe_error(exc)

        try:
            ready = backend.ready()
            raw_data = ready.get("data") if isinstance(ready.get("data"), dict) else {}
            ready_state: dict[str, object] = {
                "ok": ready.get("status") == "success",
                "docker": raw_data.get("docker") is True,
                "image_id": str(raw_data.get("image_id") or "")[:72],
                "apparmor_profile": str(
                    raw_data.get("apparmor_profile") or ""
                )[:128],
                "disk_used_percent": raw_data.get("disk_used_percent"),
                "disk_free_bytes": raw_data.get("disk_free_bytes"),
            }
        except SandboxServiceError as exc:
            ready_state = _safe_probe_error(exc)
        return {"health": health_state, "ready": ready_state}
    finally:
        backend.close()


def _run_summary(row: SandboxRun) -> dict[str, object]:
    """只返回账本元数据，不返回命令、stdout、stderr 或宿主路径。"""

    return {
        "run_id": row.run_id,
        "workspace_id": row.workspace_id,
        "trace_id": row.trace_id,
        "agent_run_id": row.agent_run_id,
        "tool_call_id": row.tool_call_id,
        "image_digest": row.image_digest,
        "status": row.status,
        "exit_code": row.exit_code,
        "termination_reason": row.termination_reason,
        "cpu_time_ms": int(row.cpu_time_ms or 0),
        "peak_memory_bytes": int(row.peak_memory_bytes or 0),
        "stdout_bytes": int(row.stdout_bytes or 0),
        "stderr_bytes": int(row.stderr_bytes or 0),
        "stdout_truncated": bool(row.stdout_truncated),
        "stderr_truncated": bool(row.stderr_truncated),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _recent_runs(
    db: Session,
    *,
    statuses: tuple[str, ...] | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    query = db.query(SandboxRun)
    if statuses:
        query = query.filter(SandboxRun.status.in_(statuses))
    rows = query.order_by(SandboxRun.created_at.desc()).limit(limit).all()
    return [_run_summary(row) for row in rows]


def _usage_summary(db: Session) -> dict[str, int]:
    workspace_count, used_bytes, quota_bytes = db.query(
        func.count(Workspace.id),
        func.coalesce(func.sum(Workspace.used_bytes), 0),
        func.coalesce(func.sum(Workspace.quota_bytes), 0),
    ).one()
    asset_count, asset_bytes = db.query(
        func.count(Asset.sha256),
        func.coalesce(func.sum(Asset.size_bytes), 0),
    ).one()
    asset_link_count = db.query(func.count(WorkspaceAsset.id)).scalar()
    return {
        "workspace_count": int(workspace_count or 0),
        "workspace_used_bytes": int(used_bytes or 0),
        "workspace_quota_bytes": int(quota_bytes or 0),
        "asset_count": int(asset_count or 0),
        "asset_physical_bytes": int(asset_bytes or 0),
        "asset_link_count": int(asset_link_count or 0),
    }


@router.get("/status")
def sandbox_status(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    return {
        "feature": {
            "enabled": bool(resolve_sandbox_setting(db, "sandbox.enabled")),
            "exec_enabled": bool(resolve_sandbox_setting(
                db,
                "sandbox.exec_enabled",
            )),
            "group_enabled": bool(resolve_sandbox_setting(
                db,
                "sandbox.group_enabled",
            )),
        },
        "controller": _controller_status(db),
        "usage": _usage_summary(db),
        "disk_watermark": {
            "max_used_percent": int(resolve_sandbox_setting(
                db,
                "sandbox.disk_max_percent",
            )),
            "min_free_bytes": int(resolve_sandbox_setting(
                db,
                "sandbox.disk_min_free_bytes",
            )),
        },
        "current_runs": _recent_runs(
            db,
            statuses=("pending", "running"),
            limit=20,
        ),
        "recent_failures": _recent_runs(db, statuses=("failed",), limit=20),
    }


@router.get("/runs")
def list_sandbox_runs(
    status: _RUN_STATUS | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    statuses = (status,) if status else None
    return {"items": _recent_runs(db, statuses=statuses, limit=limit)}


@router.post("/runs/{run_id}/cancel")
def cancel_sandbox_run(
    request: Request,
    run_id: str = Path(
        min_length=8,
        max_length=64,
        pattern=r"^sbxrun_[A-Za-z0-9_-]+$",
    ),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    row = db.get(SandboxRun, run_id)
    if row is None:
        raise HTTPException(404, "Sandbox 运行不存在")
    backend = _sandbox_backend(db)
    try:
        response = backend.cancel_run(
            run_id,
            request_id=f"admin-cancel-{run_id}"[:64],
        )
    except SandboxServiceError as exc:
        if exc.code is SandboxErrorCode.AUTHORIZATION_FAILED:
            raise HTTPException(404, "Sandbox 运行不存在") from exc
        raise HTTPException(503, "Sandbox 取消暂时不可用") from exc
    finally:
        backend.close()
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    safe_data = {
        key: data.get(key)
        for key in (
            "run_id",
            "workspace_id",
            "image_digest",
            "status",
            "created_at_unix",
            "started_at_unix",
            "finished_at_unix",
            "error_code",
        )
        if key in data
    }
    audit_request(
        db,
        request,
        "sandbox_cancel_run",
        "sandbox_run",
        run_id,
        {"status": safe_data.get("status")},
    )
    return {"ok": True, "data": safe_data}


@router.post("/kill-switch")
def sandbox_kill_switch(
    body: SandboxKillSwitchRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    previous = {
        "enabled": bool(resolve_sandbox_setting(db, "sandbox.enabled")),
        "exec_enabled": bool(resolve_sandbox_setting(
            db,
            "sandbox.exec_enabled",
        )),
    }
    try:
        for key in ("sandbox.enabled", "sandbox.exec_enabled"):
            row = db.get(SystemSetting, key)
            if row is None:
                row = SystemSetting(
                    key=key,
                    value="0",
                    description="Sandbox 管理端无损关闭开关",
                )
                db.add(row)
            else:
                row.value = "0"
        db.commit()
        settings.invalidate()
    except Exception as exc:
        db.rollback()
        raise HTTPException(503, "Sandbox 关闭开关暂时不可用") from exc

    active_count = db.query(func.count(SandboxRun.run_id)).filter(
        SandboxRun.status.in_(("pending", "running")),
    ).scalar()
    audit_request(
        db,
        request,
        "sandbox_kill_switch",
        "sandbox",
        "global",
        {
            "previous": previous,
            "reason": body.reason,
            "active_run_count": int(active_count or 0),
        },
    )
    return {
        "ok": True,
        "feature": {"enabled": False, "exec_enabled": False},
        "active_run_count": int(active_count or 0),
        "data_preserved": True,
    }
