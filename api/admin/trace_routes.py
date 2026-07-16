"""Admin Trace / LLM API 日志路由。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.database import (
    AgentRun,
    LLMApiRequestLog,
    PromptRenderLog,
    ReplyContractCheckLog,
    ToolCall,
    get_db,
)
from core.tracing import row_to_dict, sanitize_llm_log_payload

router = APIRouter(tags=["admin-trace"])


@router.get("/agent-runs")
def list_agent_runs(
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
    offset: int | None = Query(None, ge=0),
    status: str = "",
    session_id: str = "",
    group_id: str = "",
    chat_type: str = "",
    trace_id: str = "",
    user_id: str = "",
    prompt_key: str = "",
    prompt_mode: str = "",
    run_type: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    q = db.query(AgentRun)
    if status:
        q = q.filter(AgentRun.status == status)
    if session_id:
        q = q.filter(AgentRun.session_id == session_id)
    if group_id:
        q = q.filter(AgentRun.group_id == group_id)
    if chat_type:
        q = q.filter(AgentRun.chat_type == chat_type)
    if trace_id:
        q = q.filter(AgentRun.trace_id == trace_id)
    if user_id:
        q = q.filter(AgentRun.user_id == user_id)
    if prompt_key:
        q = q.filter(AgentRun.prompt_key == prompt_key)
    if prompt_mode:
        q = q.filter(AgentRun.prompt_mode == prompt_mode)
    if run_type:
        q = q.filter(AgentRun.run_type == run_type)
    total = q.count()
    row_offset = offset if offset is not None else (page - 1) * limit
    rows = (
        q.order_by(AgentRun.started_at.desc())
        .offset(row_offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [row_to_dict(row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "offset": row_offset,
    }


@router.get("/agent-runs/{run_id}")
def get_agent_run(run_id: str, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    run = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
    if not run:
        raise HTTPException(404, "Agent run not found")
    tool_calls = (
        db.query(ToolCall)
        .filter(ToolCall.run_id == run_id)
        .order_by(ToolCall.started_at.asc())
        .all()
    )
    prompt_logs = (
        db.query(PromptRenderLog)
        .filter(PromptRenderLog.run_id == run_id)
        .order_by(PromptRenderLog.created_at.asc())
        .all()
    )
    llm_logs = (
        db.query(LLMApiRequestLog)
        .filter(LLMApiRequestLog.run_id == run_id)
        .order_by(LLMApiRequestLog.created_at.asc())
        .all()
    )
    reply_contract_logs = (
        db.query(ReplyContractCheckLog)
        .filter(ReplyContractCheckLog.run_id == run_id)
        .order_by(ReplyContractCheckLog.created_at.asc())
        .all()
    )
    return {
        "run": row_to_dict(run),
        "tool_calls": [row_to_dict(row) for row in tool_calls],
        "prompt_render_logs": [row_to_dict(row) for row in prompt_logs],
        "llm_api_request_logs": [row_to_dict(x) for x in llm_logs],
        "reply_contract_check_logs": [row_to_dict(x) for x in reply_contract_logs],
    }


@router.get("/tool-calls")
def list_tool_calls(
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
    offset: int | None = Query(None, ge=0),
    run_id: str = "",
    trace_id: str = "",
    tool_name: str = "",
    status: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    q = db.query(ToolCall)
    if run_id:
        q = q.filter(ToolCall.run_id == run_id)
    if trace_id:
        q = q.filter(ToolCall.trace_id == trace_id)
    if tool_name:
        q = q.filter(ToolCall.tool_name == tool_name)
    if status:
        q = q.filter(ToolCall.status == status)
    total = q.count()
    row_offset = offset if offset is not None else (page - 1) * limit
    rows = (
        q.order_by(ToolCall.started_at.desc())
        .offset(row_offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [row_to_dict(row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "offset": row_offset,
    }


@router.get("/tool-calls/{tool_call_id}")
def get_tool_call(tool_call_id: str, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(ToolCall).filter(ToolCall.tool_call_id == tool_call_id).first()
    if not row:
        raise HTTPException(404, "Tool call not found")
    return row_to_dict(row)


@router.get("/llm-api-logs")
def list_llm_api_logs(
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
    offset: int | None = Query(None, ge=0),
    include_payload: bool = False,
    run_id: str = "",
    trace_id: str = "",
    source: str = "",
    model: str = "",
    status: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    q = db.query(LLMApiRequestLog)
    if run_id:
        q = q.filter(LLMApiRequestLog.run_id == run_id)
    if trace_id:
        q = q.filter(LLMApiRequestLog.trace_id == trace_id)
    if source:
        q = q.filter(LLMApiRequestLog.source == source)
    if model:
        q = q.filter(LLMApiRequestLog.model == model)
    if status:
        q = q.filter(LLMApiRequestLog.status == status)
    total = q.count()
    by_status = {
        str(row[0] or "created"): int(row[1] or 0)
        for row in q.with_entities(LLMApiRequestLog.status, func.count(LLMApiRequestLog.id))
        .group_by(LLMApiRequestLog.status)
        .all()
    }
    success_count = sum(by_status.get(s, 0) for s in ("success", "stream_success"))
    failed_error_count = sum(by_status.get(s, 0) for s in ("failed", "error", "stream_error"))
    created_count = sum(by_status.get(s, 0) for s in ("created", "stream_created"))
    avg_latency = (
        q.filter(LLMApiRequestLog.latency_ms > 0)
        .with_entities(func.avg(LLMApiRequestLog.latency_ms))
        .scalar()
    )
    unbound_run_count = q.filter(
        (LLMApiRequestLog.run_id.is_(None)) | (LLMApiRequestLog.run_id == "")
    ).count()
    row_offset = offset if offset is not None else (page - 1) * limit
    if include_payload:
        rows = (
            q.order_by(LLMApiRequestLog.created_at.desc())
            .offset(row_offset)
            .limit(limit)
            .all()
        )
        items = [row_to_dict(row) for row in rows]
    else:
        rows = (
            q.with_entities(
                LLMApiRequestLog.id,
                LLMApiRequestLog.trace_id,
                LLMApiRequestLog.run_id,
                LLMApiRequestLog.source,
                LLMApiRequestLog.provider,
                LLMApiRequestLog.model,
                LLMApiRequestLog.url,
                LLMApiRequestLog.method,
                LLMApiRequestLog.request_preview,
                LLMApiRequestLog.response_preview,
                LLMApiRequestLog.response_status,
                LLMApiRequestLog.status,
                LLMApiRequestLog.error,
                LLMApiRequestLog.latency_ms,
                LLMApiRequestLog.created_at,
                LLMApiRequestLog.finished_at,
            )
            .order_by(LLMApiRequestLog.created_at.desc())
            .offset(row_offset)
            .limit(limit)
            .all()
        )
        items = []
        for row in rows:
            item = sanitize_llm_log_payload(dict(row._mapping))
            for key in ("created_at", "finished_at"):
                value = item.get(key)
                if isinstance(value, datetime):
                    item[key] = value.isoformat()
            item["request_preview"] = str(item.get("request_preview") or "")[:240]
            item["response_preview"] = str(item.get("response_preview") or "")[:240]
            item["error"] = str(item.get("error") or "")[:240]
            item["summary_only"] = True
            items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "offset": row_offset,
        "stats": {
            "total": total,
            "success": success_count,
            "failed_error": failed_error_count,
            "created": created_count,
            "avg_latency_ms": int(avg_latency or 0),
            "unbound_run_count": unbound_run_count,
            "by_status": by_status,
        },
    }


@router.get("/llm-api-logs/{log_id}")
def get_llm_api_log(log_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(LLMApiRequestLog).filter(LLMApiRequestLog.id == log_id).first()
    if not row:
        raise HTTPException(404, "LLM API request log not found")
    return row_to_dict(row)
