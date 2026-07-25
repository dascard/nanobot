"""Admin 审计日志与日志查看器路由。"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import sys
from collections import deque
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from api.admin.contract_models import AuditLogListResponse
from api.endpoint_contracts import standard_error_responses
from core.database import AdminAuditLog, get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(tags=["admin-logs"])


def _safe_json(raw):
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []


def _project_root() -> str:
    admin_routes = sys.modules.get("api.admin_routes")
    admin_routes_file = getattr(admin_routes, "__file__", "") if admin_routes else ""
    if admin_routes_file:
        return os.path.dirname(os.path.dirname(os.path.abspath(str(admin_routes_file))))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _data_dir() -> str:
    return os.path.join(_project_root(), "data")


@router.get(
    "/audit-logs",
    operation_id="adminAuditLogsList",
    response_model=AuditLogListResponse,
    responses=standard_error_responses(401, 422),
)
def list_audit_logs(
    page: int = 1, limit: int = 50,
    action: str = "", target_type: str = "", since: str = "",
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    q = db.query(AdminAuditLog).order_by(AdminAuditLog.id.desc())
    if action:
        q = q.filter(AdminAuditLog.action == action)
    if target_type:
        q = q.filter(AdminAuditLog.target_type == target_type)
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            q = q.filter(AdminAuditLog.created_at >= since_dt)
        except ValueError:
            pass
    total = q.count()
    rows = q.offset((page - 1) * limit).limit(limit).all()
    return {"total": total, "items": [{
        "id": r.id, "admin_user": r.admin_user, "action": r.action,
        "target_type": r.target_type, "target_id": r.target_id,
        "detail_json": _safe_json(r.detail_json),
        "ip_address": r.ip_address,
        "created_at": str(r.created_at) if r.created_at else "",
    } for r in rows]}


@router.get("/logs")
def list_log_files(_auth=Depends(verify_admin)):
    log_dir = _data_dir()
    files = []
    patterns = ["*.log", "*.log.*", "nanobot.log*"]
    for pat in patterns:
        for p in glob.glob(os.path.join(log_dir, pat)):
            fname = os.path.basename(p)
            if fname not in [f["name"] for f in files]:
                size = os.path.getsize(p)
                files.append({"name": fname, "size": size, "mtime": os.path.getmtime(p)})
    files.sort(key=lambda x: -x["mtime"])
    return {"files": files}


class FrontendErrorBody(BaseModel):
    message: str = Field(default="")
    stack: str = Field(default="")
    url: str = Field(default="")


@router.post("/logs/frontend-error")
def log_frontend_error(body: FrontendErrorBody, _auth=Depends(verify_admin)):
    logger.warning(f"[FrontendError] url={body.url} message={body.message}")
    if body.stack:
        logger.warning(f"[FrontendError] stack: {body.stack[:2000]}")
    return {"ok": True}


def _is_allowed_log_name(name: str) -> bool:
    n = os.path.basename(name)
    return n == "nanobot.log" or n.startswith("nanobot.log.") or n.endswith(".log") or ".log." in n


_LOG_START_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3}\s+\[(?P<level>[A-Z]+)\]")


def _log_level_of(line: str) -> str:
    match = _LOG_START_RE.match(line or "")
    return match.group("level") if match else ""


def _group_log_level_events(lines: list[str], *, level: str, before: int, after: int) -> list[dict[str, Any]]:
    target = str(level or "").upper()
    events: list[dict[str, Any]] = []
    idx = 0
    while idx < len(lines):
        line_level = _log_level_of(lines[idx])
        if line_level != target:
            idx += 1
            continue
        start = idx
        end = idx + 1
        while end < len(lines) and not _log_level_of(lines[end]):
            end += 1
        before_start = max(0, start - max(0, before))
        after_end = min(len(lines), end + max(0, after))
        events.append({
            "level": target,
            "line_start": start + 1,
            "line_end": end,
            "before_lines": [line.rstrip("\n") for line in lines[before_start:start]],
            "event_lines": [line.rstrip("\n") for line in lines[start:end]],
            "after_lines": [line.rstrip("\n") for line in lines[end:after_end]],
        })
        idx = end
    return events


@router.get("/logs/{name}")
def read_log(name: str, lines: str = "200", level: str = "", q: str = "",
             since_bytes: int = 0, group_errors: bool = False,
             context_before: int = 5, context_after: int = 8,
             _auth=Depends(verify_admin)):
    log_dir = os.path.abspath(_data_dir())
    fname = os.path.basename(name)
    if not _is_allowed_log_name(fname):
        raise HTTPException(400, "Invalid log file name")
    fpath = os.path.abspath(os.path.join(log_dir, fname))
    if not fpath.startswith(log_dir + os.sep) or not os.path.isfile(fpath):
        raise HTTPException(404, "Log not found")

    file_size = os.path.getsize(fpath)

    # tail 模式：从 since_bytes 增量读取
    if since_bytes > 0:
        if since_bytes >= file_size:
            return {"name": fname, "content": "", "lines": 0,
                    "raw_lines": 0, "file_size": file_size, "tail": True}
        with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(since_bytes)
            content = fh.read()
        if level or q:
            filtered = []
            for line in content.splitlines(True):
                if level and level.upper() not in line.upper():
                    continue
                if q and q.lower() not in line.lower():
                    continue
                filtered.append(line)
            content = "".join(filtered)
        return {"name": fname, "content": content, "lines": content.count("\n"),
                "raw_lines": len(content.splitlines()), "file_size": file_size, "tail": True}

    lines_text = str(lines or "200").strip().lower()
    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
        if lines_text == "all":
            tail = list(fh)
        else:
            try:
                requested_lines = int(lines_text)
            except ValueError:
                raise HTTPException(400, "lines must be an integer or all")
            max_lines = max(1, min(requested_lines, 5000))
            tail = list(deque(fh, maxlen=max_lines))
    if group_errors and str(level or "").upper() == "ERROR":
        events = _group_log_level_events(
            tail,
            level="ERROR",
            before=max(0, min(int(context_before or 0), 50)),
            after=max(0, min(int(context_after or 0), 50)),
        )
        content = "\n\n".join(
            "\n".join(event["before_lines"] + event["event_lines"] + event["after_lines"])
            for event in events
        )
        return {
            "name": fname,
            "lines": content.count("\n"),
            "content": content,
            "raw_lines": len(tail),
            "file_size": file_size,
            "events": events,
        }
    content = "".join(tail)
    if level or q:
        filtered = []
        for line in content.splitlines(True):
            if level and level.upper() not in line.upper():
                continue
            if q and q.lower() not in line.lower():
                continue
            filtered.append(line)
        content = "".join(filtered)
    return {"name": fname, "lines": content.count("\n"), "content": content,
            "raw_lines": len(tail), "file_size": file_size}
