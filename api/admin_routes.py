"""WebUI 管理 API——Sticker/Block/Config/DB 管理。prefix=/api/v1/admin，认证使用 NANOBOT_ADMIN_TOKEN。"""

import json
import logging
from hmac import compare_digest
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import (
    get_db,
    SystemSetting, AdminAuditLog,
)
from config import NANOBOT_ADMIN_TOKEN

# Legacy facade：聚合拆分子路由并保留旧导入路径兼容。
from api.admin.db_browser_routes import (
    BLOCKED_DB_TABLES as BLOCKED_DB_TABLES,
    DB_TABLE_GROUPS as DB_TABLE_GROUPS,
    DB_TABLE_POLICIES as DB_TABLE_POLICIES,
    DEFAULT_DB_TABLE_POLICY as DEFAULT_DB_TABLE_POLICY,
    GLOBAL_PREVIEW_ONLY_COLUMNS as GLOBAL_PREVIEW_ONLY_COLUMNS,
    GLOBAL_REDACT_COLUMNS as GLOBAL_REDACT_COLUMNS,
    READONLY_TABLES as READONLY_TABLES,
    READONLY_TABLE_SET as READONLY_TABLE_SET,
    DbQuery as DbQuery,
    _available_db_groups as _available_db_groups,
    _available_readonly_tables as _available_readonly_tables,
    _db_table_meta as _db_table_meta,
    _db_table_policy as _db_table_policy,
    _extract_query_table_names as _extract_query_table_names,
    _quote_identifier as _quote_identifier,
    _safe_serialize_cell as _safe_serialize_cell,
    _serialize_db_rows as _serialize_db_rows,
    _table_columns as _table_columns,
    _validate_query_tables_allowed as _validate_query_tables_allowed,
    _validate_readonly_query as _validate_readonly_query,
    execute_readonly_query as execute_readonly_query,
    list_tables as list_tables,
    query_table as query_table,
    router as db_browser_router,
)
from api.admin.group_memory_routes import (
    GroupMemoryExtractRequest as GroupMemoryExtractRequest,
    GroupMemoryInjectionConfigRequest as GroupMemoryInjectionConfigRequest,
    GroupMemoryInjectionPreviewRequest as GroupMemoryInjectionPreviewRequest,
    GroupMemoryUpdateRequest as GroupMemoryUpdateRequest,
    _extract_group_memories_response as _extract_group_memories_response,
    _group_memories_payload as _group_memories_payload,
    _group_memory_row_dict as _group_memory_row_dict,
    group_memories_extract as group_memories_extract,
    group_memories_list as group_memories_list,
    group_memories_overview as group_memories_overview,
    group_memory_extract_alias as group_memory_extract_alias,
    group_memory_injection_config as group_memory_injection_config,
    group_memory_injection_preview as group_memory_injection_preview,
    group_memory_items as group_memory_items,
    group_memory_update_item as group_memory_update_item,
    router as group_memory_router,
)
from api.admin.log_routes import (
    FrontendErrorBody as FrontendErrorBody,
    _group_log_level_events as _group_log_level_events,
    _is_allowed_log_name as _is_allowed_log_name,
    _log_level_of as _log_level_of,
    list_audit_logs as list_audit_logs,
    list_log_files as list_log_files,
    log_frontend_error as log_frontend_error,
    read_log as read_log,
    router as log_router,
)
from api.admin.model_routes import (
    ChatModelTestRequest as ChatModelTestRequest,
    ModelCatalogPatch as ModelCatalogPatch,
    ModelRouteEditBody as ModelRouteEditBody,
    ModelRoutePatch as ModelRoutePatch,
    ProviderUpdateBody as ProviderUpdateBody,
    TimingGateStabilityRequest as TimingGateStabilityRequest,
    _ALLOWED_TIERS as _ALLOWED_TIERS,
    _CHAT_ROUTES as _CHAT_ROUTES,
    _CLASSIFIER_ROUTE_KEYS as _CLASSIFIER_ROUTE_KEYS,
    _ROUTE_ALIAS as _ROUTE_ALIAS,
    _ROUTE_SETTING_MAP as _ROUTE_SETTING_MAP,
    _STAGE_META as _STAGE_META,
    _TINY_TEST_PNG as _TINY_TEST_PNG,
    _redact as _redact,
    _resolve_route_key as _resolve_route_key,
    _resolve_route_value as _resolve_route_value,
    _test_nli_contradiction as _test_nli_contradiction,
    chat_model_test as chat_model_test,
    edit_model_route as edit_model_route,
    get_model_catalog as get_model_catalog,
    get_model_catalog_v2 as get_model_catalog_v2,
    get_model_routes as get_model_routes,
    get_resolved_route as get_resolved_route,
    get_route_references as get_route_references,
    list_available_models as list_available_models,
    list_model_providers as list_model_providers,
    model_health_check as model_health_check,
    models_status as models_status,
    patch_model_catalog as patch_model_catalog,
    patch_model_route as patch_model_route,
    refresh_model_catalog as refresh_model_catalog,
    router as model_router,
    test_local_component as test_local_component,
    test_model_route as test_model_route,
    timing_gate_stability_test as timing_gate_stability_test,
    update_model_provider as update_model_provider,
    warmup_local_component as warmup_local_component,
)
from api.admin.reply_routes import (
    ReplyEvalCaseIn as ReplyEvalCaseIn,
    ReplyEvalCasePatch as ReplyEvalCasePatch,
    ReplyEvalRunIn as ReplyEvalRunIn,
    ReplyEvalSaveGeneratedIn as ReplyEvalSaveGeneratedIn,
    ReplyTestRunRequest as ReplyTestRunRequest,
    _is_reply_eval_test_session as _is_reply_eval_test_session,
    _loads_json_list as _loads_json_list,
    _reply_case_to_dict as _reply_case_to_dict,
    _reply_contract_has_final_action as _reply_contract_has_final_action,
    _reply_contract_run_key as _reply_contract_run_key,
    _reply_eval_run_to_dict as _reply_eval_run_to_dict,
    _reply_log_attempt as _reply_log_attempt,
    _resolve_reply_test_prompt_settings as _resolve_reply_test_prompt_settings,
    _run_reply_test_once as _run_reply_test_once,
    _safe_rate as _safe_rate,
    _upsert_reply_eval_case as _upsert_reply_eval_case,
    reply_eval_create_case as reply_eval_create_case,
    reply_eval_delete_case as reply_eval_delete_case,
    reply_eval_generate_preview as reply_eval_generate_preview,
    reply_eval_get_run as reply_eval_get_run,
    reply_eval_list_cases as reply_eval_list_cases,
    reply_eval_list_runs as reply_eval_list_runs,
    reply_eval_real_traffic as reply_eval_real_traffic,
    reply_eval_run as reply_eval_run,
    reply_eval_save_generated as reply_eval_save_generated,
    reply_eval_update_case as reply_eval_update_case,
    reply_test_run as reply_test_run,
    router as reply_router,
)
from api.admin.eval_routes import (
    CandidateBatchAuditDecision as CandidateBatchAuditDecision,
    CandidateBatchAuditRequest as CandidateBatchAuditRequest,
    CandidatePreflightRequest as CandidatePreflightRequest,
    CandidateTriageRequest as CandidateTriageRequest,
    EvalCandidatePatch as EvalCandidatePatch,
    EvalRunRequest as EvalRunRequest,
    LabelRequest as LabelRequest,
    PromoteRequest as PromoteRequest,
    TIMING_TUNING_PROPOSAL_REPORT as TIMING_TUNING_PROPOSAL_REPORT,
    TIMING_TUNING_REVIEW_DECISIONS as TIMING_TUNING_REVIEW_DECISIONS,
    TimingTuningProposalReviewRequest as TimingTuningProposalReviewRequest,
    _current_timing_tuning_proposal_report as _current_timing_tuning_proposal_report,
    _proposal_missing_response as _proposal_missing_response,
    _proposal_review_from_audit as _proposal_review_from_audit,
    _proposal_sha256 as _proposal_sha256,
    _triage_response_or_404 as _triage_response_or_404,
    eval_candidate_batch_audit as eval_candidate_batch_audit,
    eval_candidates_trend as eval_candidates_trend,
    eval_defer_candidate as eval_defer_candidate,
    eval_expected_contract as eval_expected_contract,
    eval_get_candidate as eval_get_candidate,
    eval_get_run as eval_get_run,
    eval_ignore_candidate as eval_ignore_candidate,
    eval_label_candidate as eval_label_candidate,
    eval_list_candidates as eval_list_candidates,
    eval_list_runs as eval_list_runs,
    eval_patch_candidate as eval_patch_candidate,
    eval_preflight_candidates as eval_preflight_candidates,
    eval_promote_candidate as eval_promote_candidate,
    eval_reject_candidate as eval_reject_candidate,
    eval_reopen_candidate as eval_reopen_candidate,
    eval_run_sample as eval_run_sample,
    eval_run_suite as eval_run_suite,
    eval_sample_status as eval_sample_status,
    eval_timing_tuning_proposal as eval_timing_tuning_proposal,
    eval_timing_tuning_proposal_review as eval_timing_tuning_proposal_review,
    eval_timing_tuning_proposal_review_state as eval_timing_tuning_proposal_review_state,
    router as eval_router,
)
from api.admin.prompt_v2_routes import router as prompt_v2_router
from api.admin.persona_routes import router as persona_router
from api.admin.rag_routes import router as rag_router
from api.admin.session_memory_routes import router as session_memory_router
from api.admin.chat_config_routes import (
    BlockRuleCreate as BlockRuleCreate,
    BlockRuleUpdate as BlockRuleUpdate,
    ConfigUpdate as ConfigUpdate,
    ContentBlockRuleCreate as ContentBlockRuleCreate,
    ContentBlockRuleTestRequest as ContentBlockRuleTestRequest,
    ContentBlockRuleUpdate as ContentBlockRuleUpdate,
    _block_dict as _block_dict,
    _config_default as _config_default,
    _config_dict as _config_dict,
    _content_block_dict as _content_block_dict,
    _group_stream_id as _group_stream_id,
    _raw_group_id as _raw_group_id,
    create_block_rule as create_block_rule,
    create_content_block_rule as create_content_block_rule,
    delete_block_rule as delete_block_rule,
    delete_config as delete_config,
    delete_content_block_rule as delete_content_block_rule,
    get_config as get_config,
    list_block_rules as list_block_rules,
    list_chat_streams as list_chat_streams,
    list_configs as list_configs,
    list_content_block_rules as list_content_block_rules,
    router as chat_config_router,
    test_block_rules as test_block_rules,
    toggle_content_block_rule as toggle_content_block_rule,
    update_block_rule as update_block_rule,
    update_config as update_config,
    update_content_block_rule as update_content_block_rule,
)
from api.admin.sticker_routes import (
    GeneratedImageCreate as GeneratedImageCreate,
    MarkDuplicateBody as MarkDuplicateBody,
    NearDuplicateAction as NearDuplicateAction,
    SetCanonicalBody as SetCanonicalBody,
    StickerCreate as StickerCreate,
    StickerUpdate as StickerUpdate,
    _sticker_dict as _sticker_dict,
    backfill_phash_endpoint as backfill_phash_endpoint,
    batch_delete_stickers as batch_delete_stickers,
    create_generated_image as create_generated_image,
    create_sticker as create_sticker,
    delete_sticker as delete_sticker,
    disable_sticker as disable_sticker,
    enable_sticker as enable_sticker,
    generated_image_file as generated_image_file,
    get_sticker as get_sticker,
    list_generated_images as list_generated_images,
    list_near_duplicate_candidates as list_near_duplicate_candidates,
    list_stickers as list_stickers,
    preview_sticker as preview_sticker,
    redescribe_sticker as redescribe_sticker,
    retry_preview as retry_preview,
    router as sticker_router,
    scan_near_duplicates_endpoint as scan_near_duplicates_endpoint,
    sticker_duplicate_groups as sticker_duplicate_groups,
    sticker_mark_duplicate as sticker_mark_duplicate,
    sticker_set_canonical as sticker_set_canonical,
    stickers_dedupe_backfill as stickers_dedupe_backfill,
    update_near_duplicate_candidate as update_near_duplicate_candidate,
    update_sticker as update_sticker,
)
from api.admin.runtime_routes import (
    TimingGateTestRequest as TimingGateTestRequest,
    _runtime_snapshot as _runtime_snapshot,
    _timing_event_dict as _timing_event_dict,
    _timing_meta as _timing_meta,
    _timing_stats as _timing_stats,
    group_detail as group_detail,
    list_groups as list_groups,
    overview as overview,
    router as runtime_router,
    timing_gate_events as timing_gate_events,
    timing_gate_test as timing_gate_test,
)
from api.admin.system_routes import router as system_router
from api.admin.trace_routes import (
    get_agent_run as get_agent_run,
    get_llm_api_log as get_llm_api_log,
    get_tool_call as get_tool_call,
    list_agent_runs as list_agent_runs,
    list_llm_api_logs as list_llm_api_logs,
    list_tool_calls as list_tool_calls,
    router as trace_router,
)
from api.admin.tool_routes import (
    ToolOverrideBody as ToolOverrideBody,
    ToolSchemaOverrideBody as ToolSchemaOverrideBody,
    ToolUpdateBody as ToolUpdateBody,
    _TEMP_TOOL_TARGET_EXACT as _TEMP_TOOL_TARGET_EXACT,
    _TEMP_TOOL_TARGET_PREFIXES as _TEMP_TOOL_TARGET_PREFIXES,
    _is_temp_tool_target_id as _is_temp_tool_target_id,
    _tool_target_label as _tool_target_label,
    delete_tool_override as delete_tool_override,
    delete_tool_schema_override_api as delete_tool_schema_override_api,
    get_effective_tools as get_effective_tools,
    get_tool_schema_override as get_tool_schema_override,
    list_runtime_preset_decisions as list_runtime_preset_decisions,
    list_tool_targets as list_tool_targets,
    list_tools as list_tools,
    router as tool_router,
    save_tool_schema_override_api as save_tool_schema_override_api,
    set_tool_override as set_tool_override,
    update_tool_defaults as update_tool_defaults,
)
from api.admin.web_search_routes import router as web_search_router
from api.admin.proactive_outreach_routes import router as proactive_outreach_router

logger = logging.getLogger("nanobot.admin")
router = APIRouter(prefix="/api/v1/admin")

router.include_router(system_router)
router.include_router(db_browser_router)
router.include_router(prompt_v2_router)
router.include_router(persona_router)
router.include_router(rag_router)
router.include_router(session_memory_router)
router.include_router(chat_config_router)
router.include_router(sticker_router)
router.include_router(group_memory_router)
router.include_router(runtime_router)
router.include_router(tool_router)
router.include_router(model_router)
router.include_router(web_search_router)
router.include_router(proactive_outreach_router)
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

def _safe_dict(raw) -> dict:
    try:
        parsed = json.loads(raw or "{}") if isinstance(raw, str) else (raw or {})
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _iso(v) -> str:
    return v.isoformat(sep=" ", timespec="seconds") if v else ""


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
        if defn.category == "web_search":
            continue
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
