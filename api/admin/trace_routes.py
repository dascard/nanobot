"""Admin Trace / LLM API 日志路由。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.database import (
    AgentRun,
    LLMApiRequestLog,
    PromptRenderLog,
    ReplyContractCheckLog,
    RunLedgerStreamHead,
    ToolCall,
    get_db,
)
from core.run_ledger.contracts import (
    RunLedgerContractError,
    RunLedgerIntegrityError,
    canonical_run_status,
)
from core.tracing import row_to_dict, sanitize_llm_log_payload
from core.run_ledger.persistence import SqlAlchemyRunEventLedger
from core.run_ledger.projection import (
    assess_run_ledger_readiness,
    run_ledger_record_to_dict,
)
from core.run_ledger.read_model import (
    AuthoritativeRunLedgerView,
    load_authoritative_run_view,
)

router = APIRouter(tags=["admin-trace"])


class RunTaskCancelRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=128)


def _load_run_view(
    ledger: SqlAlchemyRunEventLedger,
    run_id: str,
) -> AuthoritativeRunLedgerView | None:
    try:
        return load_authoritative_run_view(ledger, run_id)
    except (RunLedgerContractError, RunLedgerIntegrityError) as exc:
        raise HTTPException(
            503,
            detail={
                "code": "run_ledger_projection_unavailable",
                "run_id": run_id,
                "message": "Run Ledger 无法生成完整权威投影",
            },
        ) from exc


def _ledger_head_dict(view: AuthoritativeRunLedgerView) -> dict[str, object]:
    head = view.head
    return {
        "run_id": head.run_id,
        "last_sequence": head.last_sequence,
        "last_event_id": head.last_event_id,
        "last_event_sha256": head.last_event_sha256,
        "terminal_sequence": head.terminal_sequence,
    }


def _run_read_model(
    run: AgentRun | None,
    view: AuthoritativeRunLedgerView | None,
) -> dict[str, object]:
    """以 Ledger 为当前事实源；只为迁移前记录保留显式 legacy 回退。"""

    legacy = row_to_dict(run) if run is not None else {}
    if view is None:
        legacy["status_source"] = "legacy_compat"
        legacy["ledger_authoritative"] = False
        legacy["ledger_high_water_sequence"] = 0
        return legacy

    accepted = view.accepted.event
    projection = view.projection
    item: dict[str, object] = dict(legacy)
    if run is None:
        item.update({
            "run_id": projection.run_id,
            "trace_id": accepted.correlation.trace_id,
            "session_id": accepted.correlation.session_id,
            "user_id": (
                accepted.identity.actor_id
                if accepted.identity.actor_type == "user"
                else ""
            ),
            "chat_type": str(accepted.payload.get("chat_type") or ""),
            "group_id": (
                accepted.identity.owner_id
                if accepted.identity.owner_type == "group"
                else ""
            ),
            "run_type": str(accepted.payload.get("run_type") or ""),
            "prompt_source": "",
            "prompt_runtime_path": "",
            "prompt_default_path": "",
            "prompt_template_resolutions_json": "{}",
            "input_preview": "",
            "output_preview": "",
            "error": "",
            "latency_ms": 0,
            "meta_json": "{}",
        })
    item["legacy_status"] = legacy.get("status")
    item["status"] = projection.status
    item["status_source"] = "run_ledger"
    item["ledger_authoritative"] = True
    item["ledger_high_water_sequence"] = projection.high_water_sequence
    item["started_at"] = (
        projection.started_at.isoformat() if projection.started_at else None
    )
    item["finished_at"] = (
        projection.finished_at.isoformat() if projection.finished_at else None
    )
    item["updated_at"] = (
        projection.updated_at.isoformat() if projection.updated_at else None
    )
    item["prompt_mode"] = projection.prompt_mode or str(
        legacy.get("prompt_mode") or ""
    )
    item["prompt_key"] = projection.prompt_key or str(
        legacy.get("prompt_key") or ""
    )
    item["prompt_sha256"] = projection.prompt_sha256 or str(
        legacy.get("prompt_sha256") or ""
    )
    if projection.model_ids:
        item["model"] = projection.model_ids[-1]
    return item


def _matches_run_filters(
    item: dict[str, object],
    *,
    status: str,
    session_id: str,
    group_id: str,
    chat_type: str,
    trace_id: str,
    user_id: str,
    prompt_key: str,
    prompt_mode: str,
    run_type: str,
) -> bool:
    if status and canonical_run_status(item.get("status")) != (
        canonical_run_status(status)
    ):
        return False
    for field, expected in (
        ("session_id", session_id),
        ("group_id", group_id),
        ("chat_type", chat_type),
        ("trace_id", trace_id),
        ("user_id", user_id),
        ("prompt_key", prompt_key),
        ("prompt_mode", prompt_mode),
        ("run_type", run_type),
    ):
        if expected and str(item.get(field) or "") != expected:
            return False
    return True


def _run_started_sort_value(item: dict[str, object]) -> float:
    try:
        value = datetime.fromisoformat(str(item.get("started_at") or ""))
    except ValueError:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


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
    row_offset = offset if offset is not None else (page - 1) * limit
    ledger = SqlAlchemyRunEventLedger(db)
    legacy_rows = db.query(AgentRun).all()
    rows_by_run_id = {str(row.run_id): row for row in legacy_rows}
    ledger_run_ids = {
        str(run_id)
        for run_id, in db.query(RunLedgerStreamHead.run_id).all()
    }
    all_run_ids = set(rows_by_run_id) | ledger_run_ids
    projected_rows = [
        _run_read_model(
            rows_by_run_id.get(run_id),
            _load_run_view(ledger, run_id),
        )
        for run_id in all_run_ids
    ]
    matched = [
        item
        for item in projected_rows
        if _matches_run_filters(
            item,
            status=status,
            session_id=session_id,
            group_id=group_id,
            chat_type=chat_type,
            trace_id=trace_id,
            user_id=user_id,
            prompt_key=prompt_key,
            prompt_mode=prompt_mode,
            run_type=run_type,
        )
    ]
    matched.sort(key=_run_started_sort_value, reverse=True)
    total = len(matched)
    items = matched[row_offset:row_offset + limit]
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "offset": row_offset,
    }


@router.get("/agent-runs/{run_id}")
def get_agent_run(run_id: str, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    run = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
    ledger = SqlAlchemyRunEventLedger(db)
    ledger_view = _load_run_view(ledger, run_id)
    if run is None and ledger_view is None:
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
    ledger_records = ledger_view.records if ledger_view is not None else ()
    ledger_projection = (
        ledger_view.projection if ledger_view is not None else None
    )
    ledger_readiness = (
        assess_run_ledger_readiness(
            ledger_records,
            run_id=run_id,
            legacy_status=str(run.status or ""),
            legacy_finished_at=run.finished_at,
            projection_complete=True,
            high_water_sequence=ledger_view.head.last_sequence,
        )
        if run is not None and ledger_view is not None
        else None
    )
    from core.durable_tasks import SqlAlchemyRunTaskService

    task_view = SqlAlchemyRunTaskService(db).get(run_id)
    return {
        "run": _run_read_model(run, ledger_view),
        "durable_task": (
            task_view.to_dict() if task_view is not None else None
        ),
        "ledger": {
            "available": ledger_view is not None,
            "authoritative": ledger_view is not None,
            "source": (
                "run_ledger" if ledger_view is not None else "legacy_compat"
            ),
            "projection_complete": ledger_view is not None,
            "head": _ledger_head_dict(ledger_view) if ledger_view else None,
            "projection": (
                ledger_projection.to_dict()
                if ledger_projection is not None
                else None
            ),
            "legacy_audit": (
                ledger_readiness.to_dict()
                if ledger_readiness is not None
                else None
            ),
            "readiness": (
                ledger_readiness.to_dict()
                if ledger_readiness is not None
                else None
            ),
        },
        "tool_calls": [row_to_dict(row) for row in tool_calls],
        "prompt_render_logs": [row_to_dict(row) for row in prompt_logs],
        "llm_api_request_logs": [row_to_dict(x) for x in llm_logs],
        "reply_contract_check_logs": [row_to_dict(x) for x in reply_contract_logs],
    }


@router.post("/agent-runs/{run_id}/cancel")
def cancel_agent_run_task(
    run_id: str,
    body: RunTaskCancelRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """幂等请求当前执行 owner 取消；不在重连或读取时启动新任务。"""

    from core.durable_tasks import (
        RunTaskConflict,
        SqlAlchemyRunTaskService,
    )

    service = SqlAlchemyRunTaskService(db)
    if service.get(run_id) is None:
        raise HTTPException(404, "Run Durable Task 不存在")
    try:
        view = service.request_cancel(
            run_id,
            reason=body.reason,
        )
        db.commit()
    except RunTaskConflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return view.to_dict()


@router.get("/agent-runs/{run_id}/events")
def list_agent_run_events(
    run_id: str,
    after_sequence: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    ledger = SqlAlchemyRunEventLedger(db)
    head = ledger.head(run_id)
    if (
        head is None
        and db.query(AgentRun.run_id)
        .filter(AgentRun.run_id == run_id)
        .first()
        is None
    ):
        raise HTTPException(404, "Agent run not found")
    if head is None:
        return {
            "items": [],
            "run_id": run_id,
            "after_sequence": after_sequence,
            "next_after_sequence": after_sequence,
            "high_water_sequence": 0,
            "has_more": False,
        }
    records = ledger.read(
        run_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    next_after_sequence = (
        records[-1].sequence if records else after_sequence
    )
    return {
        "items": [run_ledger_record_to_dict(record) for record in records],
        "run_id": run_id,
        "after_sequence": after_sequence,
        "next_after_sequence": next_after_sequence,
        "high_water_sequence": head.last_sequence,
        "has_more": next_after_sequence < head.last_sequence,
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
    provider: str = "",
    model: str = "",
    status: str = "",
    cache_status: str = "",
    error_category: str = "",
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
    if provider:
        q = q.filter(LLMApiRequestLog.provider == provider)
    if model:
        q = q.filter(LLMApiRequestLog.model == model)
    if status:
        q = q.filter(LLMApiRequestLog.status == status)
    if cache_status:
        q = q.filter(LLMApiRequestLog.cache_status == cache_status)
    if error_category:
        q = q.filter(LLMApiRequestLog.error_category == error_category)
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
    by_cache_status = {
        str(row[0] or "pending"): int(row[1] or 0)
        for row in q.with_entities(
            LLMApiRequestLog.cache_status,
            func.count(LLMApiRequestLog.id),
        )
        .group_by(LLMApiRequestLog.cache_status)
        .all()
    }
    by_error_category = {
        str(row[0] or "none"): int(row[1] or 0)
        for row in q.with_entities(
            LLMApiRequestLog.error_category,
            func.count(LLMApiRequestLog.id),
        )
        .group_by(LLMApiRequestLog.error_category)
        .all()
    }
    cache_token_totals = q.with_entities(
        func.sum(LLMApiRequestLog.cache_hit_tokens),
        func.sum(LLMApiRequestLog.cache_miss_tokens),
        func.sum(LLMApiRequestLog.cache_write_tokens),
    ).one()
    cache_hit_token_total = int(cache_token_totals[0] or 0)
    cache_miss_token_total = int(cache_token_totals[1] or 0)
    cache_prompt_token_total = cache_hit_token_total + cache_miss_token_total
    avg_latency = (
        q.filter(LLMApiRequestLog.latency_ms > 0)
        .with_entities(func.avg(LLMApiRequestLog.latency_ms))
        .scalar()
    )
    avg_first_token_latency = q.with_entities(
        func.avg(func.nullif(LLMApiRequestLog.first_token_latency_ms, 0))
    ).scalar()
    performance_totals = q.with_entities(
        func.sum(LLMApiRequestLog.input_tokens),
        func.sum(LLMApiRequestLog.output_tokens),
        func.sum(LLMApiRequestLog.cost_microusd),
    ).one()
    provider_name_expr = func.coalesce(
        func.nullif(LLMApiRequestLog.provider, ""),
        "unknown",
    )
    provider_rows = q.with_entities(
        provider_name_expr,
        func.count(LLMApiRequestLog.id),
        func.sum(case((LLMApiRequestLog.status.in_((
            "success",
            "stream_success",
        )), 1), else_=0)),
        func.sum(case((LLMApiRequestLog.status.in_((
            "failed",
            "error",
            "stream_error",
        )), 1), else_=0)),
        func.sum(LLMApiRequestLog.cache_hit_tokens),
        func.sum(LLMApiRequestLog.cache_miss_tokens),
        func.sum(LLMApiRequestLog.cache_write_tokens),
        func.avg(func.nullif(LLMApiRequestLog.first_token_latency_ms, 0)),
        func.avg(func.nullif(LLMApiRequestLog.latency_ms, 0)),
        func.sum(LLMApiRequestLog.input_tokens),
        func.sum(LLMApiRequestLog.output_tokens),
        func.sum(LLMApiRequestLog.cost_microusd),
    ).group_by(provider_name_expr).all()
    provider_error_rows = q.with_entities(
        provider_name_expr,
        LLMApiRequestLog.error_category,
        func.count(LLMApiRequestLog.id),
    ).group_by(
        provider_name_expr,
        LLMApiRequestLog.error_category,
    ).all()
    provider_error_counts: dict[str, dict[str, int]] = {}
    for provider_name, category, count in provider_error_rows:
        provider_error_counts.setdefault(
            str(provider_name or "unknown"),
            {},
        )[str(category or "none")] = int(count or 0)
    by_provider = {}
    for provider_row in provider_rows:
        provider_name = str(provider_row[0] or "unknown")
        requests = int(provider_row[1] or 0)
        successful_requests = int(provider_row[2] or 0)
        failed_requests = int(provider_row[3] or 0)
        hit_tokens = int(provider_row[4] or 0)
        miss_tokens = int(provider_row[5] or 0)
        cache_tokens = hit_tokens + miss_tokens
        by_provider[provider_name] = {
            "requests": requests,
            "successful_requests": successful_requests,
            "failed_requests": failed_requests,
            "incomplete_requests": max(
                0,
                requests - successful_requests - failed_requests,
            ),
            "success_rate": (
                round(successful_requests / requests, 6)
                if requests > 0 else None
            ),
            "cache_hit_tokens": hit_tokens,
            "cache_miss_tokens": miss_tokens,
            "cache_write_tokens": int(provider_row[6] or 0),
            "cache_hit_token_ratio": (
                round(hit_tokens / cache_tokens, 6)
                if cache_tokens > 0 else None
            ),
            "avg_first_token_latency_ms": int(provider_row[7] or 0),
            "avg_total_latency_ms": int(provider_row[8] or 0),
            "input_tokens": int(provider_row[9] or 0),
            "output_tokens": int(provider_row[10] or 0),
            "cost_microusd": int(provider_row[11] or 0),
            "by_error_category": provider_error_counts.get(
                provider_name,
                {},
            ),
        }
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
                LLMApiRequestLog.phase,
                LLMApiRequestLog.round_index,
                LLMApiRequestLog.route_attempt_index,
                LLMApiRequestLog.provider,
                LLMApiRequestLog.model,
                LLMApiRequestLog.url,
                LLMApiRequestLog.method,
                LLMApiRequestLog.request_preview,
                LLMApiRequestLog.response_preview,
                LLMApiRequestLog.response_status,
                LLMApiRequestLog.status,
                LLMApiRequestLog.error_category,
                LLMApiRequestLog.cache_status,
                LLMApiRequestLog.cache_hit,
                LLMApiRequestLog.cache_hit_tokens,
                LLMApiRequestLog.cache_miss_tokens,
                LLMApiRequestLog.cache_write_tokens,
                LLMApiRequestLog.input_tokens,
                LLMApiRequestLog.output_tokens,
                LLMApiRequestLog.first_token_latency_ms,
                LLMApiRequestLog.cost_microusd,
                LLMApiRequestLog.cost_source,
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
            "avg_first_token_latency_ms": int(avg_first_token_latency or 0),
            "unbound_run_count": unbound_run_count,
            "by_status": by_status,
            "by_error_category": by_error_category,
            "cache_hit": by_cache_status.get("hit", 0),
            "cache_miss": by_cache_status.get("miss", 0),
            "cache_not_reported": by_cache_status.get("not_reported", 0),
            "cache_pending": by_cache_status.get("pending", 0),
            "cache_error": by_cache_status.get("error", 0),
            "cache_hit_tokens": cache_hit_token_total,
            "cache_miss_tokens": cache_miss_token_total,
            "cache_write_tokens": int(cache_token_totals[2] or 0),
            "cache_hit_token_ratio": (
                round(cache_hit_token_total / cache_prompt_token_total, 6)
                if cache_prompt_token_total > 0 else None
            ),
            "by_cache_status": by_cache_status,
            "input_tokens": int(performance_totals[0] or 0),
            "output_tokens": int(performance_totals[1] or 0),
            "cost_microusd": int(performance_totals[2] or 0),
            "by_provider": by_provider,
        },
    }


@router.get("/llm-api-logs/{log_id}")
def get_llm_api_log(log_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(LLMApiRequestLog).filter(LLMApiRequestLog.id == log_id).first()
    if not row:
        raise HTTPException(404, "LLM API request log not found")
    return row_to_dict(row)
