"""WebUI 管理 API——Sticker/Block/Config/DB 管理。prefix=/api/v1/admin，认证使用 NANOBOT_ADMIN_TOKEN。"""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from hmac import compare_digest
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import (
    get_db,
    StickerMemory, ChatStreamConfig, UserBlockRule, ContentBlockRule,
    SystemSetting, AdminAuditLog,
    ChatLog, ConversationTurn, User,
)
from core.tracing import row_to_dict
from config import NANOBOT_ADMIN_TOKEN

logger = logging.getLogger("nanobot.admin")
router = APIRouter(prefix="/api/v1/admin")

from api.admin.db_browser_routes import (
    BLOCKED_DB_TABLES,
    DB_TABLE_GROUPS,
    DB_TABLE_POLICIES,
    DEFAULT_DB_TABLE_POLICY,
    GLOBAL_PREVIEW_ONLY_COLUMNS,
    GLOBAL_REDACT_COLUMNS,
    READONLY_TABLES,
    READONLY_TABLE_SET,
    DbQuery,
    _available_db_groups,
    _available_readonly_tables,
    _db_table_meta,
    _db_table_policy,
    _extract_query_table_names,
    _quote_identifier,
    _safe_serialize_cell,
    _serialize_db_rows,
    _table_columns,
    _validate_query_tables_allowed,
    _validate_readonly_query,
    execute_readonly_query,
    list_tables,
    query_table,
    router as db_browser_router,
)
from api.admin.group_memory_routes import (
    GroupMemoryExtractRequest,
    GroupMemoryInjectionConfigRequest,
    GroupMemoryInjectionPreviewRequest,
    GroupMemoryUpdateRequest,
    _extract_group_memories_response,
    _group_memories_payload,
    _group_memory_row_dict,
    group_memories_extract,
    group_memories_list,
    group_memories_overview,
    group_memory_extract_alias,
    group_memory_injection_config,
    group_memory_injection_preview,
    group_memory_items,
    group_memory_update_item,
    router as group_memory_router,
)
from api.admin.log_routes import (
    FrontendErrorBody,
    _group_log_level_events,
    _is_allowed_log_name,
    _log_level_of,
    list_audit_logs,
    list_log_files,
    log_frontend_error,
    read_log,
    router as log_router,
)
from api.admin.model_routes import (
    ChatModelTestRequest,
    ModelCatalogPatch,
    ModelRouteEditBody,
    ModelRoutePatch,
    ProviderUpdateBody,
    TimingGateStabilityRequest,
    _ALLOWED_TIERS,
    _CHAT_ROUTES,
    _CLASSIFIER_ROUTE_KEYS,
    _ROUTE_ALIAS,
    _ROUTE_SETTING_MAP,
    _STAGE_META,
    _TINY_TEST_PNG,
    _redact,
    _resolve_route_key,
    _resolve_route_value,
    _test_nli_contradiction,
    chat_model_test,
    edit_model_route,
    get_model_catalog,
    get_model_catalog_v2,
    get_model_routes,
    get_resolved_route,
    get_route_references,
    list_available_models,
    list_model_providers,
    model_health_check,
    models_status,
    patch_model_catalog,
    patch_model_route,
    refresh_model_catalog,
    router as model_router,
    test_local_component,
    test_model_route,
    timing_gate_stability_test,
    update_model_provider,
    warmup_local_component,
)
from api.admin.reply_routes import (
    ReplyEvalCaseIn,
    ReplyEvalCasePatch,
    ReplyEvalRunIn,
    ReplyEvalSaveGeneratedIn,
    ReplyTestRunRequest,
    _is_reply_eval_test_session,
    _loads_json_list,
    _reply_case_to_dict,
    _reply_contract_has_final_action,
    _reply_contract_run_key,
    _reply_eval_run_to_dict,
    _reply_log_attempt,
    _resolve_reply_test_prompt_settings,
    _run_reply_test_once,
    _safe_rate,
    _upsert_reply_eval_case,
    reply_eval_create_case,
    reply_eval_delete_case,
    reply_eval_generate_preview,
    reply_eval_get_run,
    reply_eval_list_cases,
    reply_eval_list_runs,
    reply_eval_real_traffic,
    reply_eval_run,
    reply_eval_save_generated,
    reply_eval_update_case,
    reply_test_run,
    router as reply_router,
)
from api.admin.prompt_v2_routes import router as prompt_v2_router
from api.admin.persona_routes import router as persona_router
from api.admin.rag_routes import router as rag_router
from api.admin.session_memory_routes import router as session_memory_router
from api.admin.sticker_routes import (
    GeneratedImageCreate,
    MarkDuplicateBody,
    NearDuplicateAction,
    SetCanonicalBody,
    StickerCreate,
    StickerUpdate,
    _sticker_dict,
    backfill_phash_endpoint,
    batch_delete_stickers,
    create_generated_image,
    create_sticker,
    delete_sticker,
    disable_sticker,
    enable_sticker,
    generated_image_file,
    get_sticker,
    list_generated_images,
    list_near_duplicate_candidates,
    list_stickers,
    preview_sticker,
    redescribe_sticker,
    retry_preview,
    router as sticker_router,
    scan_near_duplicates_endpoint,
    sticker_duplicate_groups,
    sticker_mark_duplicate,
    sticker_set_canonical,
    stickers_dedupe_backfill,
    update_near_duplicate_candidate,
    update_sticker,
)
from api.admin.system_routes import router as system_router
from api.admin.trace_routes import (
    get_agent_run,
    get_llm_api_log,
    get_tool_call,
    list_agent_runs,
    list_llm_api_logs,
    list_tool_calls,
    router as trace_router,
)
from api.admin.tool_routes import (
    ToolOverrideBody,
    ToolSchemaOverrideBody,
    ToolUpdateBody,
    _TEMP_TOOL_TARGET_EXACT,
    _TEMP_TOOL_TARGET_PREFIXES,
    _is_temp_tool_target_id,
    _tool_target_label,
    delete_tool_override,
    delete_tool_schema_override_api,
    get_effective_tools,
    get_tool_schema_override,
    list_runtime_preset_decisions,
    list_tool_targets,
    list_tools,
    router as tool_router,
    save_tool_schema_override_api,
    set_tool_override,
    update_tool_defaults,
)

router.include_router(system_router)
router.include_router(db_browser_router)
router.include_router(prompt_v2_router)
router.include_router(persona_router)
router.include_router(rag_router)
router.include_router(session_memory_router)
router.include_router(sticker_router)
router.include_router(group_memory_router)
router.include_router(tool_router)
router.include_router(model_router)
router.include_router(reply_router)
router.include_router(trace_router)
router.include_router(log_router)

# ── Auth ──

def verify_admin(authorization: str = Header(default="")) -> str:
    if not NANOBOT_ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    token = authorization.replace("Bearer ", "").strip()
    if not token or not compare_digest(token, NANOBOT_ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid token")
    return "admin"


def _audit(db: Session, action: str, target_type: str = "", target_id: str = "", detail: dict | None = None,
            ip_address: str = ""):
    try:
        db.add(AdminAuditLog(
            action=action, target_type=target_type, target_id=str(target_id),
            detail_json=json.dumps(detail or {}, ensure_ascii=False),
            ip_address=(ip_address or "")[:45],
        ))
        db.commit()
    except Exception:
        pass


def _client_ip(request) -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()[:45]
    client = getattr(request, "client", None)
    if client and hasattr(client, "host"):
        return str(client.host)[:45]
    return ""


def _audit_request(db: Session, request: Request, action: str,
                   target_type: str = "", target_id: str = "",
                   detail: dict | None = None):
    """写审计日志——自动从 request 提取客户端 IP。"""
    return _audit(db, action, target_type, target_id, detail,
                  ip_address=_client_ip(request))

# ── Models ──

class BlockRuleCreate(BaseModel):
    user_id: str
    target_type: str = "private"
    group_id: str = ""
    rule_mode: str = "log_only"
    reason: str = ""


class BlockRuleUpdate(BaseModel):
    rule_mode: Optional[str] = None
    reason: Optional[str] = None
    enabled: Optional[int] = None


class ContentBlockRuleCreate(BaseModel):
    pattern: str
    match_type: str = "contains"
    scope_type: str = "session"
    chat_stream_id: str = ""
    no_reply: int = 0
    no_learn: int = 1
    no_context: int = 0
    category: str = "no_learn"
    reason: str = ""


class ContentBlockRuleUpdate(BaseModel):
    pattern: Optional[str] = None
    match_type: Optional[str] = None
    scope_type: Optional[str] = None
    chat_stream_id: Optional[str] = None
    no_reply: Optional[int] = None
    no_learn: Optional[int] = None
    no_context: Optional[int] = None
    category: Optional[str] = None
    reason: Optional[str] = None
    enabled: Optional[int] = None


class ContentBlockRuleTestRequest(BaseModel):
    message: str
    chat_stream_id: str = ""


class ConfigUpdate(BaseModel):
    talk_value: Optional[float] = None
    mentioned_bot_reply: Optional[int] = None
    use_expression: Optional[int] = None
    enable_expression_learning: Optional[int] = None
    enable_jargon_learning: Optional[int] = None
    group_profile_mode: Optional[str] = None
    enable_group_profile: Optional[int] = None  # deprecated, 兼容旧调用方
    planner_smooth: Optional[int] = None


class EffectivePromptPreviewRequest(BaseModel):
    chat_type: Literal["private", "group"] = "private"
    platform: str = "qq"
    session_id: str = ""
    user_id: str = ""
    group_id: str = ""
    sender_name: str = ""
    prompt_key: str = ""
    engine: Literal["v1", "v2", "prompt"] = "prompt"
    mode: Literal["legacy", "shadow", "managed"] = "shadow"
    user_input: str = ""
    runtime_preset: str = "full"


# ── Helpers ──

def _safe_json(raw):
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []


def _block_dict(r: UserBlockRule) -> dict:
    return {
        "id": r.id, "user_id": r.user_id, "target_type": r.target_type,
        "group_id": r.group_id, "rule_mode": r.rule_mode, "reason": r.reason,
        "enabled": r.enabled,
        "created_at": str(r.created_at) if r.created_at else "",
        "updated_at": str(r.updated_at) if r.updated_at else "",
    }


def _config_dict(r: ChatStreamConfig) -> dict:
    return {
        "chat_stream_id": r.chat_stream_id,
        "talk_value": r.talk_value,
        "mentioned_bot_reply": bool(r.mentioned_bot_reply),
        "use_expression": bool(r.use_expression),
        "enable_expression_learning": bool(r.enable_expression_learning),
        "enable_jargon_learning": bool(r.enable_jargon_learning),
        "group_profile_mode": r.group_profile_mode or "off",
        "planner_smooth": r.planner_smooth,
    }


def _safe_dict(raw) -> dict:
    try:
        parsed = json.loads(raw or "{}") if isinstance(raw, str) else (raw or {})
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _iso(v) -> str:
    return v.isoformat(sep=" ", timespec="seconds") if v else ""


def _age_seconds(v) -> int | None:
    if not v:
        return None
    try:
        return max(0, int((datetime.now() - v).total_seconds()))
    except Exception:
        return None


def _raw_group_id(group_id: str) -> str:
    raw = str(group_id or "").strip()
    if raw.startswith("group_"):
        return raw.removeprefix("group_")
    if raw.startswith("qq:") and raw.endswith(":group"):
        return raw.removeprefix("qq:").removesuffix(":group")
    return raw


def _group_session_id(group_id: str) -> str:
    raw = _raw_group_id(group_id)
    return f"group_{raw}" if raw else ""


def _group_stream_id(group_id: str) -> str:
    raw = _raw_group_id(group_id)
    return f"qq:{raw}:group" if raw else ""


def _timing_meta(row: ChatLog) -> dict:
    meta = _safe_dict(row.meta_json)
    timing = meta.get("timing_gate")
    return timing if isinstance(timing, dict) else {}


def _timing_event_dict(row: ChatLog) -> dict:
    timing = _timing_meta(row)
    group_id = _raw_group_id(row.session_id or row.user_id or "")
    error_type = str(timing.get("error_type") or "")
    return {
        "id": row.id,
        "group_id": group_id,
        "session_id": row.session_id or "",
        "session_name": row.session_name or "",
        "time": _iso(row.created_at),
        "trigger_message": row.content or "",
        "message_id": row.message_id or "",
        "mode": timing.get("mode", "message"),
        "pending_count": timing.get("pending_count"),
        "context_chars": timing.get("context_chars"),
        "talk_value": timing.get("talk_value"),
        "msg_1m": timing.get("msg_1m"),
        "msg_5m": timing.get("msg_5m"),
        "context": timing.get("context", "") or timing.get("model_input", ""),
        "input_summary": timing.get("context_summary", ""),
        "raw": timing.get("raw", ""),
        "action": timing.get("action", ""),
        "delay_seconds": timing.get("delay_seconds"),
        "reason": timing.get("reason", ""),
        "latency_ms": timing.get("latency_ms"),
        "generation": timing.get("generation"),
        "trigger_reason": timing.get("trigger_reason", ""),
        "error_type": error_type,
        "parse_error": bool(timing.get("parse_error") or error_type == "parse_error"),
        "fallback_action": timing.get("fallback_action", ""),
        "cooldown_ago": timing.get("cooldown_ago"),
        "hard_rule": timing.get("hard_rule"),
        "directed_to_other": timing.get("directed_to_other"),
        "pending_text": timing.get("pending_text", ""),
        "scoring": timing.get("scoring") or {},
    }


def _timing_stats(events: list[dict]) -> dict:
    actions = {"continue": 0, "wait": 0, "no_reply": 0}
    latencies: list[int] = []
    parse_error = 0
    for event in events:
        action = str(event.get("action") or "")
        if action in actions:
            actions[action] += 1
        if event.get("parse_error"):
            parse_error += 1
        try:
            if event.get("latency_ms") is not None:
                latencies.append(int(event["latency_ms"]))
        except (TypeError, ValueError):
            pass
    total = max(1, len(events))
    sorted_lat = sorted(latencies)
    p95 = sorted_lat[min(len(sorted_lat) - 1, int(len(sorted_lat) * 0.95))] if sorted_lat else 0
    return {
        "total": len(events),
        "actions": actions,
        "continue_ratio": round(actions["continue"] / total, 3),
        "wait_ratio": round(actions["wait"] / total, 3),
        "no_reply_ratio": round(actions["no_reply"] / total, 3),
        "parse_error": parse_error,
        "parse_error_ratio": round(parse_error / total, 3),
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
        "p95_latency_ms": p95,
    }


def _runtime_snapshot() -> dict:
    try:
        from core.timing_runtime import get_group_runtime
        runtime = get_group_runtime()
        snapshot = getattr(runtime, "snapshot_states", lambda: {})()
        return snapshot if isinstance(snapshot, dict) else {}
    except Exception:
        return {}


# ═══════════════════════════════════════════
# Observability / Runtime
# ═══════════════════════════════════════════

@router.get("/overview")
def overview(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    now = datetime.now()
    since = now - timedelta(hours=1)
    group_filter = ChatLog.session_id.like("group_%")

    timing_rows = (
        db.query(ChatLog)
        .filter(ChatLog.created_at >= since, ChatLog.meta_json.contains("timing_gate"))
        .order_by(ChatLog.id.desc())
        .limit(500)
        .all()
    )
    timing_events = [_timing_event_dict(r) for r in timing_rows if _timing_meta(r)]

    counters = {
        "requests_1h": db.query(ChatLog).filter(ChatLog.created_at >= since).count(),
        "group_messages_1h": db.query(ChatLog).filter(
            ChatLog.created_at >= since, ChatLog.role == "ambient", group_filter,
        ).count(),
        "replies_1h": db.query(ChatLog).filter(
            ChatLog.created_at >= since, ChatLog.role == "assistant",
        ).count(),
        "recent_errors": sum(1 for e in timing_events if e.get("error_type")),
        "timing_parse_errors": sum(1 for e in timing_events if e.get("parse_error")),
        "sticker_cache_failures": db.query(StickerMemory).filter(
            StickerMemory.preview_status.notin_(["ok", "pending", ""])
        ).count(),
        "sticker_describe_failures": db.query(StickerMemory).filter(
            StickerMemory.describe_status == "failed"
        ).count(),
        "tagging_failures": db.query(StickerMemory).filter(
            StickerMemory.describe_status == "failed"
        ).count(),
    }

    health = []
    try:
        db.execute(text("SELECT 1")).scalar()
        health.append({"name": "数据库可用", "ok": True, "detail": "SELECT 1"})
    except Exception as e:
        health.append({"name": "数据库可用", "ok": False, "detail": str(e)})

    try:
        from config import (
            NEW_API_BASE_URL, NEW_API_KEY, CLASSIFIER_API_URL,
            IMAGE_SUMMARY_API_URL, LOG_DIR,
        )
        from core.sticker_preview import _cache_dir

        health.extend([
            {"name": "模型 API 可用", "ok": bool(NEW_API_BASE_URL), "detail": NEW_API_BASE_URL},
            {"name": "模型 API Token", "ok": bool(NEW_API_KEY), "detail": "已配置" if NEW_API_KEY else "未配置"},
            {"name": "Qwen 图片模型可用", "ok": bool(IMAGE_SUMMARY_API_URL or CLASSIFIER_API_URL),
             "detail": IMAGE_SUMMARY_API_URL or CLASSIFIER_API_URL},
        ])

        from core.prompt_v2.template_registry import default_template_dir, runtime_template_dir

        default_prompt_dir = default_template_dir()
        runtime_prompt_dir = runtime_template_dir()
        health.append({
            "name": "Prompt 模板状态",
            "ok": default_prompt_dir.is_dir() and runtime_prompt_dir.is_dir(),
            "detail": f"default={default_prompt_dir}; runtime={runtime_prompt_dir}",
        })

        sticker_dir = _cache_dir()
        health.append({
            "name": "表情包缓存目录可写",
            "ok": os.access(sticker_dir, os.W_OK),
            "detail": sticker_dir,
        })
        log_dir = os.path.abspath(LOG_DIR)
        health.append({
            "name": "日志目录可读",
            "ok": os.path.isdir(log_dir) and os.access(log_dir, os.R_OK),
            "detail": log_dir,
        })
    except Exception as e:
        health.append({"name": "健康检查异常", "ok": False, "detail": str(e)})

    from config import LLM_MODEL_REPLY, LLM_MODEL_FAST, LLM_MODEL_SMART
    return {
        "time": now.isoformat(timespec="seconds"),
        "service": {"name": "Nanobot Server", "ok": True},
        "models": {
            "main": LLM_MODEL_REPLY,
            "fast": LLM_MODEL_FAST,
            "smart": LLM_MODEL_SMART,
            "timing_gate": "Qwen TimingGate",
        },
        "counters": counters,
        "timing": _timing_stats(timing_events),
        "health": health,
    }


@router.get("/groups")
def list_groups(limit: int = 100, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    now = datetime.now()
    since_1m = now - timedelta(minutes=1)
    since_5m = now - timedelta(minutes=5)
    runtime = _runtime_snapshot()

    group_ids = {u.id for u in db.query(User).filter(User.id.like("group_%")).all()}
    group_ids.update(
        row[0] for row in db.query(ChatLog.session_id)
        .filter(ChatLog.session_id.like("group_%"))
        .distinct()
        .all()
        if row[0]
    )
    group_ids.update(runtime.keys())

    items = []
    for sid in sorted(group_ids, reverse=True)[:max(1, min(limit, 500))]:
        raw = _raw_group_id(sid)
        stream_id = _group_stream_id(raw)
        user = db.query(User).filter(User.id == sid).first()
        cfg = db.query(ChatStreamConfig).filter(ChatStreamConfig.chat_stream_id == stream_id).first()
        latest_msg = (
            db.query(ChatLog)
            .filter(ChatLog.session_id == sid, ChatLog.role == "ambient")
            .order_by(ChatLog.id.desc())
            .first()
        )
        latest_reply = (
            db.query(ChatLog)
            .filter(ChatLog.session_id == sid, ChatLog.role == "assistant")
            .order_by(ChatLog.id.desc())
            .first()
        )
        latest_timing = (
            db.query(ChatLog)
            .filter(ChatLog.session_id == sid, ChatLog.meta_json.contains("timing_gate"))
            .order_by(ChatLog.id.desc())
            .first()
        )
        timing = _timing_meta(latest_timing) if latest_timing else {}
        rt = runtime.get(sid, {})
        items.append({
            "group_id": raw,
            "session_id": sid,
            "session_name": (user.name if user else "") or (latest_msg.session_name if latest_msg else "") or rt.get("session_name", ""),
            "talk_value": (cfg.talk_value if cfg else rt.get("talk_value", 0.5)),
            "recent_message_time": _iso(latest_msg.created_at if latest_msg else None),
            "recent_bot_reply_time": _iso(latest_reply.created_at if latest_reply else None),
            "since_last_reply": _age_seconds(latest_reply.created_at if latest_reply else None),
            "msg_1m": db.query(ChatLog).filter(
                ChatLog.session_id == sid, ChatLog.role == "ambient",
                ChatLog.created_at >= since_1m,
            ).count(),
            "msg_5m": db.query(ChatLog).filter(
                ChatLog.session_id == sid, ChatLog.role == "ambient",
                ChatLog.created_at >= since_5m,
            ).count(),
            "generation": timing.get("generation") or rt.get("generation", 0),
            "has_pending_timer": bool(rt.get("has_pending_timer")),
            "pending_count": rt.get("pending_count", 0),
            "recent_action": timing.get("action", ""),
            "recent_reason": timing.get("reason", ""),
            "recent_latency_ms": timing.get("latency_ms"),
        })
    return {"total": len(items), "items": items}


@router.get("/groups/{group_id:path}")
def group_detail(group_id: str, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    sid = _group_session_id(group_id)
    raw = _raw_group_id(group_id)
    stream_id = _group_stream_id(raw)
    groups = list_groups(limit=500, db=db, _auth=_auth)["items"]
    group = next((g for g in groups if g["session_id"] == sid), {
        "group_id": raw, "session_id": sid, "session_name": "",
        "talk_value": 0.5, "generation": 0, "has_pending_timer": False,
    })

    def _log_dict(row: ChatLog) -> dict:
        return {
            "id": row.id,
            "time": _iso(row.created_at),
            "sender_name": row.sender_name or "",
            "role": row.role,
            "content": row.content or "",
            "message_id": row.message_id or "",
            "meta": _safe_dict(row.meta_json),
        }

    ambient = (
        db.query(ChatLog)
        .filter(ChatLog.session_id == sid, ChatLog.role == "ambient")
        .order_by(ChatLog.id.desc())
        .limit(50)
        .all()
    )
    replies = (
        db.query(ChatLog)
        .filter(ChatLog.session_id == sid, ChatLog.role == "assistant")
        .order_by(ChatLog.id.desc())
        .limit(20)
        .all()
    )
    timing_rows = (
        db.query(ChatLog)
        .filter(ChatLog.session_id == sid, ChatLog.meta_json.contains("timing_gate"))
        .order_by(ChatLog.id.desc())
        .limit(50)
        .all()
    )
    blocks = (
        db.query(UserBlockRule)
        .filter(
            (UserBlockRule.target_type == "all") |
            ((UserBlockRule.target_type == "group") & (UserBlockRule.group_id == raw))
        )
        .order_by(UserBlockRule.id.desc())
        .limit(50)
        .all()
    )
    stickers = (
        db.query(StickerMemory)
        .filter(StickerMemory.chat_stream_id.in_([stream_id, "global"]))
        .order_by(StickerMemory.last_seen.desc(), StickerMemory.id.desc())
        .limit(50)
        .all()
    )
    runtime = _runtime_snapshot().get(sid, {})
    return {
        "group": group,
        "runtime": runtime,
        "ambient_messages": [_log_dict(r) for r in ambient],
        "bot_replies": [_log_dict(r) for r in replies],
        "timing_events": [_timing_event_dict(r) for r in timing_rows if _timing_meta(r)],
        "blocked_rules": [_block_dict(r) for r in blocks],
        "sticker_records": [_sticker_dict(r) for r in stickers],
    }


@router.get("/timing-gate/events")
def timing_gate_events(
    group_id: str = "",
    page: int = 1,
    limit: int = 50,
    error_only: int = 0,
    parse_error_only: int = 0,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    q = db.query(ChatLog).filter(ChatLog.meta_json.contains("timing_gate"))
    if group_id:
        q = q.filter(ChatLog.session_id == _group_session_id(group_id))
    _limit = max(1, min(int(limit), 200))
    # 过滤时多取 10 倍原始记录再切片，避免页面空
    raw_limit = _limit * 10 if (error_only or parse_error_only) else _limit
    rows = q.order_by(ChatLog.id.desc()).offset((max(page, 1) - 1) * _limit).limit(raw_limit).all()
    items = [_timing_event_dict(r) for r in rows if _timing_meta(r)]
    if error_only:
        items = [x for x in items if x.get("error_type")]
    if parse_error_only:
        items = [x for x in items if x.get("parse_error")]
    items = items[:_limit]

    stat_rows = q.order_by(ChatLog.id.desc()).limit(500).all()
    stat_items = [_timing_event_dict(r) for r in stat_rows if _timing_meta(r)]
    return {
        "total": q.count(),
        "page": page,
        "items": items,
        "stats": _timing_stats(stat_items),
    }


class TimingGateTestRequest(BaseModel):
    context: str = Field(default="", max_length=8000)
    repeats: int = Field(default=1, ge=1, le=5)


@router.post("/timing-gate/test")
async def timing_gate_test(body: TimingGateTestRequest, _auth=Depends(verify_admin)):
    import time
    from clients.classifier_client import get_timing_gate

    gate = get_timing_gate()
    context = body.context.strip() or "<timing_context>\n[未知用户]\n测试 TimingGate\n</timing_context>"
    runs = []
    for idx in range(body.repeats):
        t0 = time.time()
        result = await asyncio.to_thread(gate.judge, context)
        latency_ms = int((time.time() - t0) * 1000)
        runs.append({
            "index": idx + 1,
            "latency_ms": latency_ms,
            **result,
            "parse_error": result.get("error_type") == "parse_error",
        })
    stats = _timing_stats([{
        "action": r.get("action"),
        "latency_ms": r.get("latency_ms"),
        "parse_error": r.get("parse_error"),
    } for r in runs])
    return {"runs": runs, "stats": stats}


# ═══════════════════════════════════════════
# UserBlockRule CRUD
# ═══════════════════════════════════════════

@router.get("/block-rules")
def list_block_rules(page: int = 1, limit: int = 20, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    q = db.query(UserBlockRule).order_by(UserBlockRule.id.desc())
    total = q.count()
    rows = q.offset((page - 1) * limit).limit(limit).all()
    return {"total": total, "page": page, "items": [_block_dict(r) for r in rows]}


@router.post("/block-rules")
def create_block_rule(body: BlockRuleCreate, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    rule = UserBlockRule(**body.model_dump())
    db.add(rule); db.commit()
    _audit_request(db, request, "create_block_rule", "block_rule", rule.id, body.model_dump())
    return _block_dict(rule)


@router.put("/block-rules/{rule_id}")
def update_block_rule(rule_id: int, body: BlockRuleUpdate, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(UserBlockRule).filter(UserBlockRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    updates = {}
    for field in ("rule_mode", "reason", "enabled"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(row, field, val); updates[field] = val
    db.commit()
    _audit_request(db, request, "update_block_rule", "block_rule", rule_id, updates)
    return _block_dict(row)


@router.delete("/block-rules/{rule_id}")
def delete_block_rule(rule_id: int, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(UserBlockRule).filter(UserBlockRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row); db.commit()
    _audit_request(db, request, "delete_block_rule", "block_rule", rule_id)
    return {"ok": True}


# ═══════════════════════════════════════════
# ContentBlockRule CRUD
# ═══════════════════════════════════════════

def _content_block_dict(r: ContentBlockRule) -> dict:
    return {
        "id": r.id, "pattern": r.pattern, "match_type": r.match_type,
        "scope_type": r.scope_type, "chat_stream_id": r.chat_stream_id or "",
        "no_reply": bool(r.no_reply), "no_learn": bool(r.no_learn),
        "no_context": bool(r.no_context), "category": r.category,
        "enabled": bool(r.enabled), "reason": r.reason or "",
        "created_at": _iso(r.created_at), "updated_at": _iso(r.updated_at),
    }


@router.get("/content-block-rules")
def list_content_block_rules(scope_type: str = "", enabled: str = "", category: str = "",
                              page: int = 1, limit: int = 50,
                              db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    q = db.query(ContentBlockRule)
    if scope_type:
        q = q.filter(ContentBlockRule.scope_type == scope_type)
    if enabled:
        q = q.filter(ContentBlockRule.enabled == (1 if enabled == "1" else 0))
    if category:
        q = q.filter(ContentBlockRule.category == category)
    total = q.count()
    rows = q.order_by(ContentBlockRule.id.desc()).offset((page - 1) * limit).limit(limit).all()
    return {"total": total, "page": page, "items": [_content_block_dict(r) for r in rows]}


@router.post("/content-block-rules")
def create_content_block_rule(body: ContentBlockRuleCreate, request: Request,
                               db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    if body.match_type not in ("contains", "exact", "regex"):
        raise HTTPException(400, "match_type 必须是 contains/exact/regex")
    if body.scope_type not in ("global", "session"):
        raise HTTPException(400, "scope_type 必须是 global/session")
    rule = ContentBlockRule(**body.model_dump())
    db.add(rule); db.commit()
    _audit_request(db, request, "create_content_block_rule", "content_block_rule", rule.id)
    return _content_block_dict(rule)


@router.put("/content-block-rules/{rule_id}")
def update_content_block_rule(rule_id: int, body: ContentBlockRuleUpdate, request: Request,
                               db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(ContentBlockRule).filter(ContentBlockRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    if body.match_type is not None and body.match_type not in ("contains", "exact", "regex"):
        raise HTTPException(400, "match_type 必须是 contains/exact/regex")
    if body.scope_type is not None and body.scope_type not in ("global", "session"):
        raise HTTPException(400, "scope_type 必须是 global/session")
    int_fields = ("no_reply", "no_learn", "no_context", "enabled")
    updates = {}
    for field in ("pattern", "match_type", "scope_type", "chat_stream_id", "category", "reason"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(row, field, val); updates[field] = val
    for field in int_fields:
        val = getattr(body, field, None)
        if val is not None:
            setattr(row, field, int(val)); updates[field] = int(val)
    db.commit()
    _audit_request(db, request, "update_content_block_rule", "content_block_rule", rule_id, updates)
    return _content_block_dict(row)


@router.delete("/content-block-rules/{rule_id}")
def delete_content_block_rule(rule_id: int, request: Request,
                               db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(ContentBlockRule).filter(ContentBlockRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row); db.commit()
    _audit_request(db, request, "delete_content_block_rule", "content_block_rule", rule_id)
    return {"ok": True}


@router.post("/content-block-rules/{rule_id}/toggle")
def toggle_content_block_rule(rule_id: int, request: Request,
                               db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(ContentBlockRule).filter(ContentBlockRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.enabled = 0 if row.enabled else 1
    db.commit()
    _audit_request(db, request, "toggle_content_block_rule", "content_block_rule", rule_id)
    return _content_block_dict(row)


@router.post("/block-rules/test")
def test_block_rules(body: ContentBlockRuleTestRequest,
                      db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """命中测试：返回匹配的规则列表 + 合并后的效果（OR 逻辑）。"""
    from core.moderation import check_message_moderation_db
    result = check_message_moderation_db(
        db, body.message, chat_stream_id=body.chat_stream_id,
    )
    if not result:
        return {"matched": False, "rules": [], "final_effects": {}}
    return {
        "matched": True,
        "rules": [result],
        "final_effects": {
            "no_reply": bool(result.get("no_reply")),
            "no_learn": bool(result.get("no_learn")),
            "no_context": bool(result.get("no_context")),
        },
    }


@router.get("/chat-streams")
def list_chat_streams(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """返回所有已知的 chat_stream_id，供局部规则下拉选择。"""
    from core.database import ChatStreamConfig
    seen: set[str] = set()
    # 1. 覆写表
    for r in db.query(ChatStreamConfig).all():
        seen.add(r.chat_stream_id)
    # 2. ChatLog 群聊
    for (sid,) in db.query(ChatLog.session_id).filter(
        ChatLog.session_id.like("group_%")
    ).distinct().all():
        seen.add(_group_stream_id(_raw_group_id(sid)))
    # 3. runtime
    for sid in _runtime_snapshot():
        seen.add(_group_stream_id(_raw_group_id(sid)))
    return {"items": sorted(seen)}


# ═══════════════════════════════════════════
# ChatStreamConfig CRUD
# ═══════════════════════════════════════════

@router.get("/configs")
def list_configs(search: str = "", page: int = 1, limit: int = 20, effective: int = 0,
                 db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    if effective:
        stream_ids: set[str] = set()

        # 1. ChatStreamConfig 覆写表
        all_overrides = db.query(ChatStreamConfig).all()
        for r in all_overrides:
            stream_ids.add(r.chat_stream_id)

        # 2. User 表里 group_* 开头的 ID
        for u in db.query(User).filter(User.id.like("group_%")).all():
            stream_ids.add(_group_stream_id(_raw_group_id(u.id)))

        # 3. ChatLog 里出现过的群聊 session
        for (sid,) in db.query(ChatLog.session_id).filter(
            ChatLog.session_id.like("group_%")
        ).distinct().all():
            stream_ids.add(_group_stream_id(_raw_group_id(sid)))

        # 4. runtime snapshot
        runtime_snap = _runtime_snapshot()
        for sid in runtime_snap:
            stream_ids.add(_group_stream_id(_raw_group_id(sid)))

        rows_by_id = {r.chat_stream_id: r for r in all_overrides}

        items = []
        for sid in sorted(stream_ids):
            row = rows_by_id.get(sid)
            if row:
                cfg = _config_dict(row)
                cfg["has_override"] = True
                cfg["source"] = "db"
            else:
                cfg = _config_default(sid)
                cfg["has_override"] = False
                cfg["source"] = "default"
            items.append(cfg)

        if search:
            items = [x for x in items if search in x["chat_stream_id"]]

        total = len(items)
        page_items = items[(page - 1) * limit: page * limit]
        return {"items": page_items, "total": total, "page": page}

    q = db.query(ChatStreamConfig)
    if search:
        q = q.filter(ChatStreamConfig.chat_stream_id.contains(search))
    total = q.count()
    rows = q.order_by(ChatStreamConfig.chat_stream_id).offset((page - 1) * limit).limit(limit).all()
    return {"total": total, "page": page, "items": [_config_dict(r) for r in rows]}


@router.get("/configs/{chat_stream_id:path}")
def get_config(chat_stream_id: str, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(ChatStreamConfig).filter(ChatStreamConfig.chat_stream_id == chat_stream_id).first()
    if not row:
        return _config_default(chat_stream_id)
    return _config_dict(row)


def _config_default(sid: str) -> dict:
    return {"chat_stream_id": sid, "talk_value": 0.5, "mentioned_bot_reply": True,
            "use_expression": True, "enable_expression_learning": True,
            "enable_jargon_learning": True, "group_profile_mode": "off",
            "planner_smooth": 3}


@router.put("/configs/{chat_stream_id:path}")
def update_config(chat_stream_id: str, body: ConfigUpdate, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(ChatStreamConfig).filter(ChatStreamConfig.chat_stream_id == chat_stream_id).first()
    if not row:
        row = ChatStreamConfig(chat_stream_id=chat_stream_id)
        db.add(row); db.flush()
    updates = {}
    int_fields = ("mentioned_bot_reply", "use_expression", "enable_expression_learning",
                  "enable_jargon_learning", "planner_smooth")
    for field in ("talk_value",) + int_fields:
        val = getattr(body, field, None)
        if val is not None:
            setattr(row, field, int(val) if field in int_fields else val)
            updates[field] = val
    if body.group_profile_mode is not None:
        mode = str(body.group_profile_mode).strip()
        if mode not in ("off", "preview", "on"):
            raise HTTPException(status_code=400, detail=f"invalid group_profile_mode: {mode}")
        row.group_profile_mode = mode
        updates["group_profile_mode"] = mode
    elif body.enable_group_profile is not None:
        row.group_profile_mode = "on" if body.enable_group_profile else "off"
        updates["group_profile_mode"] = row.group_profile_mode
    db.commit()
    _audit(db, "update_config", "config", chat_stream_id, updates, ip_address=_client_ip(request))
    return _config_dict(row)


@router.delete("/configs/{chat_stream_id:path}")
def delete_config(chat_stream_id: str, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(ChatStreamConfig).filter(ChatStreamConfig.chat_stream_id == chat_stream_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")
    db.delete(row)
    db.commit()
    _audit_request(db, request, "delete_config", "config", chat_stream_id)
    return {"ok": True}


@router.post("/prompt/effective-preview")
async def preview_effective_prompt(
    body: EffectivePromptPreviewRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    if body.engine in {"prompt", "v2"}:
        from app.prompt_runtime.preview_service import preview_effective_prompt_v2

        return await preview_effective_prompt_v2(body, db)
    raise HTTPException(
        status_code=410,
        detail="Prompt V1 effective preview 已降级为只读迁移入口；请使用 engine=prompt",
    )


def _legacy_prompt_routes_removed() -> HTTPException:
    return HTTPException(
        status_code=410,
        detail="Legacy prompt 管理入口已降级为只读迁移入口；请使用 Prompt Runtime 模板页面",
    )


@router.api_route("/prompts", methods=["GET", "POST", "PUT", "DELETE"])
@router.api_route("/prompts/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
def legacy_managed_prompt_routes_removed(path: str = "", _auth=Depends(verify_admin)):
    raise _legacy_prompt_routes_removed()


@router.api_route("/prompt", methods=["GET", "POST", "PUT", "DELETE"])
@router.api_route("/prompt/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
def legacy_prompt_routes_removed(path: str = "", _auth=Depends(verify_admin)):
    raise _legacy_prompt_routes_removed()




# ── Model Replies ──

@router.get("/model-replies")
def model_replies(
    group_id: str = "", limit: int = 50, kind: str = "group_reply",
    before_id: int = 0,
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    """模型主动回复日志——游标分页（before_id），按 id DESC 翻页。"""
    from core.database import ChatLog
    from core.group_runtime.ids import normalize_group_session_id
    from sqlalchemy import func

    _limit = max(1, min(limit, 100))
    BATCH = max(_limit * 3, 200)

    # COUNT：SQL 层精确过滤 kind
    count_q = db.query(ChatLog).filter(
        ChatLog.role == "assistant",
        ChatLog.session_id.like("group_%"),
    )
    if group_id:
        count_q = count_q.filter(ChatLog.session_id == normalize_group_session_id(group_id))
    if kind:
        count_q = count_q.filter(
            func.json_extract(ChatLog.meta_json, "$.kind") == kind
        )
    total = count_q.count()

    # 数据查询：循环拉取直到凑够 _limit 个匹配项
    base_q = db.query(ChatLog).filter(
        ChatLog.role == "assistant",
        ChatLog.session_id.like("group_%"),
    )
    if group_id:
        base_q = base_q.filter(ChatLog.session_id == normalize_group_session_id(group_id))

    items: list[dict] = []
    cursor = before_id if before_id else None
    last_scanned_id = 0

    while len(items) <= _limit:
        q = base_q
        if cursor:
            q = q.filter(ChatLog.id < cursor)
        batch = q.order_by(ChatLog.id.desc()).limit(BATCH).all()
        if not batch:
            break

        cursor = batch[-1].id
        last_scanned_id = cursor

        for r in batch:
            meta = _safe_dict(r.meta_json)
            if kind and meta.get("kind") != kind:
                continue
            items.append({
                "id": r.id,
                "created_at": _iso(r.created_at),
                "group_id": str(r.session_id or "").removeprefix("group_"),
                "content": str(r.content or "")[:500],
                "reply_meta": meta.get("reply_meta"),
                "kind": meta.get("kind", ""),
            })
            if len(items) > _limit:
                break

        if len(batch) < BATCH:
            break  # 已扫完 DB

    has_more = len(items) > _limit
    items = items[:_limit]
    next_before_id = items[-1]["id"] if items else last_scanned_id

    return {
        "items": items,
        "count": total,
        "page_info": {
            "has_more": has_more,
            "next_before_id": next_before_id,
        },
    }




# ═══════════════════════════════════════════
# Audit logs + DB backup
# ═══════════════════════════════════════════

@router.get("/db/backup")
def download_backup(_auth=Depends(verify_admin)):
    from fastapi.responses import FileResponse
    import os as _os
    from config import DATABASE_URL as _db_url
    if not (_db_url or "").startswith("sqlite:///"):
        raise HTTPException(400, "Only SQLite backup supported")
    db_rel = _db_url.removeprefix("sqlite:///")
    base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    db_path = _os.path.join(base, db_rel) if not _os.path.isabs(db_rel) else db_rel
    if not _os.path.exists(db_path):
        raise HTTPException(404, "Database file not found")
    return FileResponse(db_path, media_type="application/octet-stream", filename="nanobot.db")


@router.post("/db/vacuum")
def db_vacuum(request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    import time as _time
    t0 = _time.time()
    db.execute(text("VACUUM"))
    db.commit()
    elapsed = int((_time.time() - t0) * 1000)
    _audit_request(db, request, "vacuum_db", "db", "main")
    return {"ok": True, "elapsed_ms": elapsed}


# ═══════════════════════════════════════════
# Settings (热重载配置)
# ═══════════════════════════════════════════

@router.get("/settings")
def list_settings(_auth=Depends(verify_admin)):
    from core.config_registry import SETTING_DEFS
    from core.settings_service import settings

    values = settings.all_values()
    result = []
    for key, defn in sorted(SETTING_DEFS.items(), key=lambda x: x[1].category + x[0]):
        val = values.get(key, defn.default)
        result.append({
            "key": key, "value": None if defn.sensitive else val,
            "display_value": "****" if defn.sensitive else str(val),
            "default": defn.default, "value_type": defn.value_type,
            "category": defn.category, "description": defn.description,
            "restart_required": defn.restart_required,
            "dangerous": defn.dangerous, "sensitive": defn.sensitive,
            "readonly": defn.key == "database.url","min_value": defn.min_value, "max_value": defn.max_value,
        })
    return {"settings": result, "version": settings.version}


@router.put("/settings/{key:path}")
def update_setting(key: str, body: dict, request: Request, db: Session = Depends(get_db),
                   _auth=Depends(verify_admin)):
    from core.config_registry import SETTING_DEFS
    from core.settings_service import settings

    defn = SETTING_DEFS.get(key)
    if not defn:
        raise HTTPException(400, f"Unknown setting: {key}")
    if defn.restart_required and defn.key == "database.url":
        raise HTTPException(400, "database.url is read-only, change via env var")
    raw_value = body.get("value")
    if raw_value is None:
        raise HTTPException(400, "Missing 'value'")
    try:
        if defn.value_type == "bool":
            val = bool(raw_value) if isinstance(raw_value, bool) else str(raw_value).lower() in {"1", "true", "yes", "on"}
        elif defn.value_type == "int":
            val = int(raw_value)
            if defn.min_value is not None and val < defn.min_value:
                raise HTTPException(400, f"Min: {defn.min_value}")
            if defn.max_value is not None and val > defn.max_value:
                raise HTTPException(400, f"Max: {defn.max_value}")
        elif defn.value_type == "float":
            val = float(raw_value)
            if defn.min_value is not None and val < defn.min_value:
                raise HTTPException(400, f"Min: {defn.min_value}")
            if defn.max_value is not None and val > defn.max_value:
                raise HTTPException(400, f"Max: {defn.max_value}")
        else:
            val = str(raw_value)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))

    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not row:
        row = SystemSetting(key=key, value=str(val), description=defn.description)
        db.add(row)
    else:
        row.value = str(val)
    db.commit()
    _audit(db, "update_setting", "setting", key, {"value": str(val)}, ip_address=_client_ip(request))
    settings.invalidate()
    return {"key": key, "value": val, "restart_required": defn.restart_required,
            "version": settings.version}


@router.post("/settings/{key:path}/reset")
def reset_setting(key: str, request: Request, db: Session = Depends(get_db),
                  _auth=Depends(verify_admin)):
    from core.config_registry import SETTING_DEFS
    from core.settings_service import settings

    defn = SETTING_DEFS.get(key)
    if not defn:
        raise HTTPException(400, f"Unknown setting: {key}")
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row:
        db.delete(row)
        db.commit()
        _audit(db, "reset_setting", "setting", key, ip_address=_client_ip(request))
    settings.invalidate()
    return {"key": key, "value": defn.default, "reset_to": "default",
            "version": settings.version}


@router.post("/settings/reload")
def reload_settings(_auth=Depends(verify_admin)):
    from core.settings_service import settings
    settings.invalidate()
    return {"version": settings.version}




# Eval 系统 API
# ═══════════════════════════════════════════

from core.database import EvalCandidate, EvalRun, EvalRunResult
from core.eval_sampling.store import (
    list_candidates, get_candidate, update_candidate,
    label_candidate, ignore_candidate, plan_candidate_promotion, promote_candidate,
    candidate_queue_summary, candidate_trend_report, preflight_candidate_promotions,
    plan_candidate_batch_audit, record_candidate_batch_audit,
    reject_candidate, defer_candidate, reopen_candidate,
    save_run, save_run_results, get_runs, get_run,
)
from evals.expected_contract import expected_contract_payload

TIMING_TUNING_PROPOSAL_REPORT = Path("evals/reports/timing_tuning_proposal_latest.json")
TIMING_TUNING_REVIEW_DECISIONS = {
    "needs_data",
    "rejected",
    "approved_for_manual_experiment",
    "reviewed_no_change",
}


class TimingTuningProposalReviewRequest(BaseModel):
    decision: str = Field(..., min_length=1, max_length=64)
    reason_code: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=2000)
    reviewer: str = Field(default="", max_length=128)


def _proposal_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _proposal_missing_response(path: Path) -> dict[str, Any]:
    return {
        "exists": False,
        "report_path": str(path),
        "readiness": {
            "ready": False,
            "blocking_reasons": [
                {
                    "code": "proposal_report_missing",
                    "message": "调参提案报告不存在，请先运行 evals.timing_tuning_proposal",
                }
            ],
        },
    }


def _proposal_review_from_audit(row: AdminAuditLog | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        detail = json.loads(row.detail_json or "{}")
    except json.JSONDecodeError:
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    detail["audit_id"] = row.id
    detail["created_at"] = row.created_at.isoformat() if row.created_at else ""
    return detail


@router.get("/evals/expected-contract")
def eval_expected_contract(_auth=Depends(verify_admin)):
    return expected_contract_payload()


@router.get("/evals/timing-tuning/proposal")
def eval_timing_tuning_proposal(_auth=Depends(verify_admin)):
    path = Path(TIMING_TUNING_PROPOSAL_REPORT)
    if not path.exists():
        return _proposal_missing_response(path)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"invalid proposal report: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=500,
            detail="invalid proposal report: JSON object expected",
        )
    return {"exists": True, "report_path": str(path), "report": payload}


@router.get("/evals/timing-tuning/proposal/review")
def eval_timing_tuning_proposal_review_state(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    path = Path(TIMING_TUNING_PROPOSAL_REPORT)
    if not path.exists():
        payload = _proposal_missing_response(path)
        payload["review"] = None
        payload["proposal_sha256"] = ""
        return payload

    proposal_sha256 = _proposal_sha256(path)
    row = (
        db.query(AdminAuditLog)
        .filter(
            AdminAuditLog.action == "review_timing_tuning_proposal",
            AdminAuditLog.target_type == "timing_tuning_proposal",
            AdminAuditLog.target_id == proposal_sha256,
        )
        .order_by(AdminAuditLog.id.desc())
        .first()
    )
    return {
        "exists": True,
        "report_path": str(path),
        "proposal_sha256": proposal_sha256,
        "review": _proposal_review_from_audit(row),
    }


@router.post("/evals/timing-tuning/proposal/reviews")
def eval_timing_tuning_proposal_review(
    payload: TimingTuningProposalReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    decision = payload.decision.strip()
    if decision not in TIMING_TUNING_REVIEW_DECISIONS:
        raise HTTPException(status_code=422, detail="invalid review decision")

    path = Path(TIMING_TUNING_PROPOSAL_REPORT)
    if not path.exists():
        raise HTTPException(status_code=404, detail="proposal report missing")

    proposal_sha256 = _proposal_sha256(path)
    detail = {
        "decision": decision,
        "reason_code": payload.reason_code.strip(),
        "note": payload.note.strip(),
        "reviewer": payload.reviewer.strip(),
        "report_path": str(path),
        "proposal_sha256": proposal_sha256,
    }
    row = AdminAuditLog(
        action="review_timing_tuning_proposal",
        target_type="timing_tuning_proposal",
        target_id=proposal_sha256,
        detail_json=json.dumps(detail, ensure_ascii=False),
        ip_address=_client_ip(request),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _proposal_review_from_audit(row)


@router.get("/evals/candidates")
def eval_list_candidates(
    suite: str = "",
    status: str = "",
    source: str = "",
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    items, total = list_candidates(
        db, suite=suite, status=status, source=source,
        limit=max(1, min(limit, 200)),
        offset=(max(page, 1) - 1) * limit,
    )
    summary = candidate_queue_summary(db, suite=suite, status=status, source=source)
    return {"items": items, "total": total, "page": page, "summary": summary}


class CandidatePreflightRequest(BaseModel):
    case_ids: list[str] = Field(default_factory=list)
    suite: str = ""
    status: str = "labeled"
    source: str = ""
    target_dataset: str = ""
    limit: int = 200


@router.post("/evals/candidates/preflight")
def eval_preflight_candidates(
    body: CandidatePreflightRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        return preflight_candidate_promotions(
            db,
            case_ids=body.case_ids,
            suite=body.suite,
            status=body.status,
            source=body.source,
            target_dataset=body.target_dataset,
            limit=body.limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


class CandidateBatchAuditDecision(BaseModel):
    case_id: str
    decision: str = "noop"
    reason_code: str = ""
    note: str = ""
    defer_until: str = ""
    expected_status: str = ""
    expected_updated_at: str = ""


class CandidateBatchAuditRequest(BaseModel):
    dry_run: bool = True
    case_ids: list[str] = Field(default_factory=list)
    suite: str = ""
    status: str = ""
    source: str = ""
    target_dataset: str = ""
    limit: int = 200
    batch_note: str = ""
    decisions: list[CandidateBatchAuditDecision] = Field(default_factory=list)


@router.post("/evals/candidates/batch-audit")
def eval_candidate_batch_audit(
    body: CandidateBatchAuditRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        plan = plan_candidate_batch_audit(
            db,
            case_ids=body.case_ids,
            suite=body.suite,
            status=body.status,
            source=body.source,
            target_dataset=body.target_dataset,
            limit=body.limit,
            batch_note=body.batch_note,
            decisions=[decision.model_dump() for decision in body.decisions],
        )
        if body.dry_run:
            return plan
        if not plan.get("ok"):
            raise ValueError("candidate batch audit has item errors")
        return record_candidate_batch_audit(
            db,
            plan,
            ip_address=_client_ip(request),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/evals/candidates/trend")
def eval_candidates_trend(
    days: int = Query(30, ge=1, le=90),
    suite: str = "",
    status: str = "",
    source: str = "",
    target_dataset: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        return candidate_trend_report(
            db,
            days=days,
            suite=suite,
            status=status,
            source=source,
            target_dataset=target_dataset,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/evals/candidates/{case_id}")
def eval_get_candidate(case_id: str, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.eval_sampling.store import _candidate_dict
    row = get_candidate(db, case_id)
    if not row:
        raise HTTPException(404, "candidate not found")
    return _candidate_dict(row)


class EvalCandidatePatch(BaseModel):
    priority: Optional[int] = None
    note: Optional[str] = None
    status: Optional[str] = None


@router.patch("/evals/candidates/{case_id}")
def eval_patch_candidate(
    case_id: str, body: EvalCandidatePatch,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    updates = {}
    if body.priority is not None:
        updates["priority"] = body.priority
    if body.note is not None:
        updates["note"] = body.note
    if body.status is not None:
        updates["status"] = body.status
    try:
        result = update_candidate(db, case_id, **updates)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not result:
        raise HTTPException(404, "candidate not found")
    _audit_request(db, request, "update_candidate", "eval_candidate", case_id, updates)
    return result


class LabelRequest(BaseModel):
    expected: dict = Field(default_factory=dict)
    expected_json: Optional[dict] = None
    note: str = ""

    def normalized_expected(self) -> dict:
        if self.expected and self.expected_json and self.expected != self.expected_json:
            raise ValueError("expected and expected_json conflict")
        return self.expected or self.expected_json or {}


class PromoteRequest(BaseModel):
    target_dataset: str = "regression"
    dry_run: bool = False


class CandidateTriageRequest(BaseModel):
    reason_code: str = ""
    note: str = ""
    defer_until: str = ""


def _triage_response_or_404(result):
    if not result:
        raise HTTPException(404, "candidate not found")
    return result


@router.post("/evals/candidates/{case_id}/label")
def eval_label_candidate(
    case_id: str, body: LabelRequest,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        expected = body.normalized_expected()
        result = label_candidate(db, case_id, expected, note=body.note or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not result:
        raise HTTPException(404, "candidate not found")
    _audit_request(db, request, "label_candidate", "eval_candidate", case_id,
                   {"expected_keys": list(expected.keys())})
    return result


@router.post("/evals/candidates/{case_id}/reject")
def eval_reject_candidate(
    case_id: str,
    body: CandidateTriageRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = reject_candidate(
            db,
            case_id,
            reason_code=body.reason_code,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    result = _triage_response_or_404(result)
    _audit_request(db, request, "reject_candidate", "eval_candidate", case_id, result["audit"])
    return result["candidate"]


@router.post("/evals/candidates/{case_id}/defer")
def eval_defer_candidate(
    case_id: str,
    body: CandidateTriageRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = defer_candidate(
            db,
            case_id,
            reason_code=body.reason_code,
            note=body.note,
            defer_until=body.defer_until,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    result = _triage_response_or_404(result)
    _audit_request(db, request, "defer_candidate", "eval_candidate", case_id, result["audit"])
    return result["candidate"]


@router.post("/evals/candidates/{case_id}/reopen")
def eval_reopen_candidate(
    case_id: str,
    body: CandidateTriageRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = reopen_candidate(
            db,
            case_id,
            reason_code=body.reason_code,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    result = _triage_response_or_404(result)
    _audit_request(db, request, "reopen_candidate", "eval_candidate", case_id, result["audit"])
    return result["candidate"]


@router.post("/evals/candidates/{case_id}/ignore")
def eval_ignore_candidate(
    case_id: str, request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = ignore_candidate(db, case_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not result:
        raise HTTPException(404, "candidate not found")
    _audit_request(db, request, "ignore_candidate", "eval_candidate", case_id)
    return result


@router.post("/evals/candidates/{case_id}/promote")
def eval_promote_candidate(
    case_id: str, request: Request, body: PromoteRequest | None = None,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    body = body or PromoteRequest()
    try:
        if body.dry_run:
            plan = plan_candidate_promotion(db, case_id, target_dataset=body.target_dataset)
            _audit_request(
                db,
                request,
                "promote_candidate_dry_run",
                "eval_candidate",
                case_id,
                {"path": plan["path"], "target_dataset": plan["target_dataset"]},
            )
            return {"dry_run": True, **plan}
        plan = plan_candidate_promotion(db, case_id, target_dataset=body.target_dataset)
        path = promote_candidate(db, case_id, target_dataset=body.target_dataset)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not path:
        raise HTTPException(404, "candidate not found")
    _audit_request(
        db,
        request,
        "promote_candidate",
        "eval_candidate",
        case_id,
        {"path": path, "target_dataset": body.target_dataset},
    )
    return {
        "ok": True,
        "dry_run": False,
        "case_id": plan["case_id"],
        "suite": plan["suite"],
        "target_dataset": plan["target_dataset"],
        "path": path,
    }


@router.post("/evals/sample/run")
async def eval_run_sample(
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.eval_sampling.scheduler import run_sampling_cycle
    created = await run_sampling_cycle()
    _audit_request(db, request, "run_eval_sample", "eval", "", {"created": created})
    return {"ok": True, "created": created}


@router.get("/evals/sample/status")
def eval_sample_status(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.database import EvalSampleCursor
    rows = db.query(EvalSampleCursor).order_by(EvalSampleCursor.id.desc()).all()
    return {
        "cursors": [
            {
                "source_type": r.source_type,
                "source_key": r.source_key,
                "cursor": json.loads(r.cursor_json or "{}"),
                "updated_at": str(r.updated_at) if r.updated_at else "",
            }
            for r in rows
        ]
    }


class EvalRunRequest(BaseModel):
    suite: str = "regression"
    include_candidates: bool = False


@router.post("/evals/run")
def eval_run_suite(
    body: EvalRunRequest,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from evals.run import run_suite_with_details

    try:
        report, case_results = run_suite_with_details(
            body.suite, include_candidates=body.include_candidates)
    except Exception as e:
        raise HTTPException(500, f"eval run failed: {e}")

    run_record = save_run(db, body.suite, {
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "pass_rate": report.pass_rate,
        "summary": {"failed_cases": report.failed_cases or []},
    })
    save_run_results(db, run_record.id, case_results)

    _audit_request(db, request, "run_eval_suite", "eval", body.suite,
                   {"run_id": run_record.id, "passed": report.passed, "failed": report.failed})

    return {
        "run_id": run_record.id,
        "suite": report.suite,
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "pass_rate": report.pass_rate,
        "failed_cases": report.failed_cases or [],
    }


@router.get("/evals/runs")
def eval_list_runs(
    limit: int = 20, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    items = get_runs(db, limit=max(1, min(limit, 100)))
    return {"items": items}


@router.get("/evals/runs/{run_id}")
def eval_get_run(
    run_id: int, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    run_dict, results = get_run(db, run_id)
    if not run_dict:
        raise HTTPException(404, "run not found")
    return {"run": run_dict, "results": results}
