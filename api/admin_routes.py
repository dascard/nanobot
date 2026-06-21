"""WebUI 管理 API——Sticker/Block/Config/DB 管理。prefix=/api/v1/admin，认证使用 NANOBOT_ADMIN_TOKEN。"""

import json
import logging
from datetime import datetime
from hmac import compare_digest
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import (
    get_db,
    ChatStreamConfig, UserBlockRule, ContentBlockRule,
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
from api.admin.eval_routes import (
    CandidateBatchAuditDecision,
    CandidateBatchAuditRequest,
    CandidatePreflightRequest,
    CandidateTriageRequest,
    EvalCandidatePatch,
    EvalRunRequest,
    LabelRequest,
    PromoteRequest,
    TIMING_TUNING_PROPOSAL_REPORT,
    TIMING_TUNING_REVIEW_DECISIONS,
    TimingTuningProposalReviewRequest,
    _current_timing_tuning_proposal_report,
    _proposal_missing_response,
    _proposal_review_from_audit,
    _proposal_sha256,
    _triage_response_or_404,
    eval_candidate_batch_audit,
    eval_candidates_trend,
    eval_defer_candidate,
    eval_expected_contract,
    eval_get_candidate,
    eval_get_run,
    eval_ignore_candidate,
    eval_label_candidate,
    eval_list_candidates,
    eval_list_runs,
    eval_patch_candidate,
    eval_preflight_candidates,
    eval_promote_candidate,
    eval_reject_candidate,
    eval_reopen_candidate,
    eval_run_sample,
    eval_run_suite,
    eval_sample_status,
    eval_timing_tuning_proposal,
    eval_timing_tuning_proposal_review,
    eval_timing_tuning_proposal_review_state,
    router as eval_router,
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
from api.admin.runtime_routes import (
    TimingGateTestRequest,
    _runtime_snapshot,
    _timing_event_dict,
    _timing_meta,
    _timing_stats,
    group_detail,
    list_groups,
    overview,
    router as runtime_router,
    timing_gate_events,
    timing_gate_test,
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
router.include_router(runtime_router)
router.include_router(tool_router)
router.include_router(model_router)
router.include_router(reply_router)
router.include_router(eval_router)
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
