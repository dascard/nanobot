"""WebUI 管理 API——Sticker/Block/Config/DB 管理。prefix=/api/v1/admin，认证使用 NANOBOT_ADMIN_TOKEN。"""

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from hmac import compare_digest
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import (
    get_db,
    StickerMemory, ChatStreamConfig, UserBlockRule, ContentBlockRule,
    SystemSetting, AdminAuditLog,
    ChatLog, ConversationTurn, User,
    AgentRun, ToolCall, PromptRenderLog,
)
from core.prompts import PromptRenderError, get_prompt_manager
from core.tracing import row_to_dict
from config import NANOBOT_ADMIN_TOKEN

logger = logging.getLogger("nanobot.admin")
router = APIRouter(prefix="/api/v1/admin")

from api.admin.prompt_v2_routes import router as prompt_v2_router
from api.admin.persona_routes import router as persona_router
from api.admin.rag_routes import router as rag_router
from api.admin.session_memory_routes import router as session_memory_router
from api.admin.system_routes import router as system_router

router.include_router(system_router)
router.include_router(prompt_v2_router)
router.include_router(persona_router)
router.include_router(rag_router)
router.include_router(session_memory_router)

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

from typing import Literal

class StickerCreate(BaseModel):
    group_id: str = ""
    chat_stream_id: str = ""
    file_ref: str
    sticker_hash: str = ""
    send_code: str = ""
    name: str = ""
    description: str = ""
    tags: list[str] = []
    emotions: list[str] = []
    status: Literal["active", "disabled"] = "active"


class StickerUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    emotions: Optional[list[str]] = None
    status: Optional[Literal["active", "disabled", "deleted"]] = None


class GeneratedImageCreate(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    size: Literal["1024x1024", "1024x1536", "1536x1024", "auto"] = "1024x1024"
    quality: Literal["low", "medium", "high", "auto"] = "high"
    background: Literal["auto", "transparent", "opaque"] = "auto"


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


class DbQuery(BaseModel):
    query: str


class PromptSaveRequest(BaseModel):
    content: str


class PromptPreviewRequest(BaseModel):
    variables: dict = Field(default_factory=dict)
    mode: str = "preview"


class EffectivePromptPreviewRequest(BaseModel):
    chat_type: Literal["private", "group"] = "private"
    session_id: str = ""
    user_id: str = ""
    group_id: str = ""
    sender_name: str = ""
    prompt_key: str = ""
    engine: Literal["v1", "v2"] = "v1"
    mode: Literal["legacy", "shadow", "managed"] = "shadow"
    user_input: str = ""
    runtime_preset: str = "full"


class GroupMemoryExtractRequest(BaseModel):
    window_hours: int = Field(default=24, ge=0, le=720)
    instructions: str = ""


class GroupMemoryInjectionConfigRequest(BaseModel):
    group_profile_mode: Literal["off", "preview", "on"] = "on"


class GroupMemoryInjectionPreviewRequest(BaseModel):
    user_input: str = ""
    max_items: int = Field(default=10, ge=1, le=30)
    max_chars: int = Field(default=1200, ge=200, le=4000)


class GroupMemoryUpdateRequest(BaseModel):
    content: Optional[str] = None
    status: Optional[Literal["review", "active", "disabled", "archived", "rejected"]] = None
    inject_policy: Optional[Literal["auto", "manual_only", "never"]] = None
    disabled_reason: Optional[str] = None
    rejected_reason: Optional[str] = None


class PromptRollbackRequest(BaseModel):
    backup_name: str


# ── Helpers ──

def _safe_json(raw):
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []


def _sticker_dict(r: StickerMemory) -> dict:
    return {
        "id": r.id, "chat_stream_id": r.chat_stream_id,
        "sticker_hash": r.sticker_hash, "file_ref": r.file_ref,
        "name": r.name, "description": r.description,
        "tags": _safe_json(r.tags_json),
        "emotions": _safe_json(r.emotions_json),
        "source_type": r.source_type, "source_count": r.source_count,
        "status": r.status, "usage_count": r.usage_count,
        "first_seen": str(r.first_seen) if r.first_seen else "",
        "last_seen": str(r.last_seen) if r.last_seen else "",
        "last_used": str(r.last_used) if r.last_used else "",
        "local_path": r.local_path or "",
        "preview_status": r.preview_status or "pending",
        "content_hash": r.content_hash or "",
        "byte_size": r.byte_size or 0,
        "width": r.width or 0,
        "height": r.height or 0,
        "duplicate_of_id": r.duplicate_of_id,
        "dedupe_status": r.dedupe_status or "unique",
        "describe_status": r.describe_status or "pending",
        "describe_attempts": r.describe_attempts or 0,
        "describe_last_error": r.describe_last_error or "",
        "described_at": str(r.described_at) if r.described_at else "",
    }


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


def _prompt_metrics(content: str) -> dict:
    cjk = sum(1 for ch in content if "\u4e00" <= ch <= "\u9fff")
    ascii_chars = sum(1 for ch in content if ord(ch) < 128)
    other = max(0, len(content) - cjk - ascii_chars)
    estimated_tokens = int(cjk * 1.0 + ascii_chars * 0.35 + other * 0.8)
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    seen: set[str] = set()
    duplicates: list[str] = []
    for line in lines:
        if len(line) < 24:
            continue
        key = line[:160]
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    danger_markers = [
        "/mnt/", "C:\\", "D:\\", "NANOBOT_ADMIN_TOKEN", "NEW_API_KEY",
        "API_KEY", "SECRET", "TOKEN=",
    ]
    return {
        "chars": len(content),
        "estimated_tokens": estimated_tokens,
        "duplicate_fragments": duplicates[:10],
        "danger_markers": [m for m in danger_markers if m in content],
    }


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

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        prompt_path = os.path.join(base, "creatures", "nanobot", "prompt.md")
        health.append({
            "name": "Prompt 构建状态",
            "ok": os.path.exists(prompt_path) and os.path.getsize(prompt_path) > 0,
            "detail": prompt_path,
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


@router.get("/groups/{group_id:path}/memories")
def group_memories_list(
    group_id: str,
    memory_type: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """查询某群的 GroupMemory 列表——用于 WebUI 群体记忆页。"""
    return _group_memories_payload(db, group_id, memory_type)


def _group_memory_row_dict(r) -> dict:
    return {
        "id": r.id, "group_id": r.group_id,
        "memory_type": r.memory_type, "content": r.content,
        "content_hash": r.content_hash or "",
        "cluster_key": r.cluster_key or "",
        "confidence": r.confidence, "evidence_count": r.evidence_count,
        "decay_score": r.decay_score,
        "first_seen": r.first_seen.strftime("%Y-%m-%d") if r.first_seen else "",
        "last_seen": r.last_seen.strftime("%Y-%m-%d") if r.last_seen else "",
        "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else "",
        "status": r.status, "source": r.source or "group_analysis",
        "inject_policy": getattr(r, "inject_policy", "auto") or "auto",
        "disabled_reason": getattr(r, "disabled_reason", "") or "",
        "rejected_reason": getattr(r, "rejected_reason", "") or "",
        "merged_into_id": getattr(r, "merged_into_id", None),
        "last_injected_at": r.last_injected_at.strftime("%Y-%m-%d %H:%M") if getattr(r, "last_injected_at", None) else "",
        "injected_count": getattr(r, "injected_count", 0) or 0,
        "evidence_log_ids_json": r.evidence_log_ids_json,
    }


def _group_memories_payload(db: Session, group_id: str, memory_type: str = "") -> dict:
    from core.database import GroupMemory
    from core.group_runtime.ids import normalize_group_session_id

    norm = normalize_group_session_id(group_id)
    q = db.query(GroupMemory).filter(GroupMemory.group_id == norm)
    if memory_type:
        q = q.filter(GroupMemory.memory_type == memory_type)
    rows = q.order_by(GroupMemory.confidence.desc(), GroupMemory.last_seen.desc()).limit(100).all()
    return {"group_id": norm, "total": len(rows), "memories": [_group_memory_row_dict(r) for r in rows]}


@router.get("/group-memories/overview")
def group_memories_overview(
    limit: int = 300,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """群体记忆覆盖概览——列出有群聊日志或已有记忆的群。"""
    from app.group_memory.extraction_service import build_group_memory_overview

    items = build_group_memory_overview(db, limit=limit)
    return {"total": len(items), "items": items}


@router.get("/group-memories/{group_id:path}/items")
def group_memory_items(
    group_id: str,
    memory_type: str = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """群体记忆专用列表接口，避免 /groups/{group_id:path} 路由吞掉子路径。"""
    return _group_memories_payload(db, group_id, memory_type)


@router.post("/group-memories/{group_id:path}/extract")
async def group_memory_extract_alias(
    group_id: str,
    body: GroupMemoryExtractRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """群体记忆专用提取接口，避免 /groups/{group_id:path} 路由吞掉子路径。"""
    return await _extract_group_memories_response(group_id, body, request, db)


@router.put("/group-memories/{group_id:path}/injection-config")
def group_memory_injection_config(
    group_id: str,
    body: GroupMemoryInjectionConfigRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """开启/关闭群体记忆注入；写入 canonical qq:<raw>:group 配置。"""
    from app.group_memory.injection_service import group_memory_config_ids

    chat_stream_id = group_memory_config_ids(group_id)[0]
    row = db.query(ChatStreamConfig).filter(ChatStreamConfig.chat_stream_id == chat_stream_id).first()
    if not row:
        row = ChatStreamConfig(chat_stream_id=chat_stream_id)
        db.add(row)
        db.flush()
    row.group_profile_mode = body.group_profile_mode
    db.commit()
    _audit_request(
        db,
        request,
        "update_group_memory_injection",
        "group_memory",
        group_id,
        {"chat_stream_id": chat_stream_id, "group_profile_mode": body.group_profile_mode},
    )
    return {
        "ok": True,
        "group_id": group_id,
        "chat_stream_id": chat_stream_id,
        "group_profile_mode": row.group_profile_mode,
    }


@router.post("/group-memories/{group_id:path}/injection-preview")
def group_memory_injection_preview(
    group_id: str,
    body: GroupMemoryInjectionPreviewRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """模拟当前输入下会注入哪些群体记忆。"""
    from app.group_memory.injection_service import GroupMemoryInjectionService

    result = GroupMemoryInjectionService(db).build_context(
        group_id=group_id,
        current_user_input=body.user_input,
        recent_messages=[],
        max_items=body.max_items,
        max_chars=body.max_chars,
    )
    return {
        "group_id": group_id,
        "group_profile_mode": result.debug.get("group_profile_mode", "off"),
        "group_memory_context": result.debug.get("group_memory_context", ""),
        "group_memory_ids": result.selected_ids,
        "group_memory_skipped": result.skipped,
        "group_memory_context_chars": result.debug.get("group_memory_context_chars", 0),
        "score_components": result.score_components,
        "debug": result.debug,
    }


@router.patch("/group-memories/items/{memory_id}")
def group_memory_update_item(
    memory_id: int,
    body: GroupMemoryUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """编辑群体记忆治理字段。"""
    from core.database import GroupMemory
    from core.group_memory import _cluster_key, _content_hash

    row = db.query(GroupMemory).filter(GroupMemory.id == memory_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="group memory not found")
    updates: dict = {}
    if body.content is not None:
        content = str(body.content).strip()
        if not content:
            raise HTTPException(status_code=400, detail="content is empty")
        content_hash = _content_hash(content)
        duplicate = db.query(GroupMemory).filter(
            GroupMemory.id != row.id,
            GroupMemory.group_id == row.group_id,
            GroupMemory.memory_type == row.memory_type,
            GroupMemory.content_hash == content_hash,
        ).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="已有相同记忆，可合并或归档")
        row.content = content
        row.content_hash = content_hash
        row.cluster_key = _cluster_key(content)
        updates["content"] = content
    for field in ("status", "inject_policy", "disabled_reason", "rejected_reason"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
            updates[field] = value
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="已有相同记忆，可合并或归档") from exc
    _audit_request(
        db,
        request,
        "update_group_memory_item",
        "group_memory",
        str(memory_id),
        updates,
    )
    return {"ok": True, "memory": _group_memory_row_dict(row)}


async def _extract_group_memories_response(
    group_id: str,
    body: GroupMemoryExtractRequest,
    request: Request,
    db: Session,
) -> dict:
    from app.group_memory import extraction_service

    try:
        result = await extraction_service.extract_group_memories(
            db,
            group_id,
            window_hours=body.window_hours,
            instructions=body.instructions,
        )
    except extraction_service.GroupMemoryGroupNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except extraction_service.GroupMemoryInsufficientData as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _audit_request(
        db,
        request,
        "extract_group_memory",
        "group_memory",
        group_id,
        result.to_dict(),
    )
    payload = result.to_dict()
    payload.update(_group_memories_payload(db, payload.get("group_id") or group_id))
    return payload


@router.post("/groups/{group_id:path}/memories/extract")
async def group_memories_extract(
    group_id: str,
    body: GroupMemoryExtractRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """手动触发群体记忆提取。"""
    return await _extract_group_memories_response(group_id, body, request, db)


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
# StickerMemory CRUD
# ═══════════════════════════════════════════

@router.post("/stickers")
def create_sticker(body: StickerCreate, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.sticker_memory import register_sticker
    try:
        sticker = register_sticker(
            db,
            group_id=body.group_id,
            chat_stream_id=body.chat_stream_id,
            file_ref=body.file_ref,
            sticker_hash=body.sticker_hash,
            send_code=body.send_code,
            name=body.name,
            description=body.description,
            tags=body.tags,
            emotions=body.emotions,
            source_type="manual",
            status=body.status,
            meta={"source": "webui"},
        )
    except Exception as e:
        raise HTTPException(400, str(e))
    _audit_request(db, request, "create_sticker", "sticker", sticker.get("id"), {
        "name": body.name, "status": body.status,
        "stream_id": sticker.get("chat_stream_id", ""),
        "description": body.description[:80] if body.description else "",
        "tags": body.tags[:5],
    })
    return sticker


@router.get("/stickers")
def list_stickers(
    search: str = "", status: str = "", page: int = 1, limit: int = 20,
    preview_status: str = "", describe_status: str = "",
    dedupe_status: str = "", failure: str = "",
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    q = db.query(StickerMemory)
    if search:
        q = q.filter(
            StickerMemory.name.contains(search) |
            StickerMemory.description.contains(search)
        )
    if status:
        q = q.filter(StickerMemory.status == status)
    if preview_status:
        q = q.filter(StickerMemory.preview_status == preview_status)
    if describe_status:
        q = q.filter(StickerMemory.describe_status == describe_status)
    if dedupe_status:
        q = q.filter(StickerMemory.dedupe_status == dedupe_status)
    if failure == "preview_failed":
        q = q.filter(StickerMemory.preview_status.notin_(["ok", "pending", ""]))
    elif failure == "describe_failed":
        q = q.filter(StickerMemory.describe_status == "failed")
    elif failure == "unlabeled":
        q = q.filter(StickerMemory.describe_status.in_(["pending", "failed"]))
    elif failure == "duplicate":
        q = q.filter(
            (StickerMemory.dedupe_status == "duplicate") |
            (StickerMemory.status == "duplicate")
        )
    total = q.count()
    rows = q.order_by(StickerMemory.id.desc()).offset((page - 1) * limit).limit(limit).all()
    return {"total": total, "page": page, "items": [_sticker_dict(r) for r in rows]}


@router.get("/generated-images")
def list_generated_images(
    page: int = 1,
    limit: int = 20,
    search: str = "",
    _auth=Depends(verify_admin),
):
    from core.generated_images import list_generated_images as _list_generated_images

    data = _list_generated_images(page=page, limit=limit, search=search)
    for item in data["items"]:
        item["image_url"] = f"/api/v1/admin/generated-images/{item['id']}/image"
    return data


@router.post("/generated-images")
async def create_generated_image(
    body: GeneratedImageCreate,
    _auth=Depends(verify_admin),
):
    from core.generated_images import GENERATED_IMAGE_REF_PATTERN, get_generated_image
    from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

    prompt = str(body.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")

    result = await ImageGenerationTool().execute({
        "prompt": prompt,
        "size": body.size,
        "quality": body.quality,
        "background": body.background,
    })
    if not result.success:
        error = str(getattr(result, "error", "") or "image generation failed")
        raise HTTPException(status_code=502, detail=error)

    try:
        payload = json.loads(result.output or "{}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"invalid image generation output: {exc}")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="invalid image generation output")

    reply_token = str(payload.get("reply_token") or "")
    match = GENERATED_IMAGE_REF_PATTERN.search(reply_token)
    if not match:
        raise HTTPException(status_code=500, detail="image generation output missing reply_token")

    try:
        item = get_generated_image(match.group(1))
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="generated image file not found")
    item["image_url"] = f"/api/v1/admin/generated-images/{item['id']}/image"
    return {
        "ok": True,
        "item": item,
        "tool_output": {
            "reply_token": reply_token,
            "mime": payload.get("mime") or item.get("mime") or "image/png",
            "model": payload.get("model") or item.get("model") or "",
            "size": payload.get("size") or item.get("size") or "",
            "quality": payload.get("quality") or item.get("quality") or "",
            "background": payload.get("background") or item.get("background") or "",
            "text_output": payload.get("text_output") or "",
            "revised_prompt": payload.get("revised_prompt") or "",
        },
    }


@router.get("/generated-images/{image_id}/image")
def generated_image_file(image_id: str, _auth=Depends(verify_admin)):
    from core.generated_images import get_generated_image_path

    try:
        path = get_generated_image_path(image_id)
    except FileNotFoundError:
        raise HTTPException(404, "generated image not found")
    return FileResponse(path, media_type="image/png")


@router.get("/stickers/duplicate-groups")
def sticker_duplicate_groups(limit: int = 50, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    rows = db.execute(text("""
        SELECT content_hash, COUNT(*) AS n
        FROM sticker_memories
        WHERE content_hash IS NOT NULL AND content_hash != ''
        GROUP BY content_hash
        HAVING n > 1
        ORDER BY n DESC
        LIMIT :limit
    """), {"limit": max(1, min(limit, 200))}).fetchall()
    groups = []
    for content_hash, n in rows:
        stickers = (
            db.query(StickerMemory)
            .filter(StickerMemory.content_hash == content_hash)
            .order_by(StickerMemory.status.asc(), StickerMemory.usage_count.desc(), StickerMemory.id.asc())
            .all()
        )
        # canonical: active, 非 duplicate, duplicate_of_id 为空
        canonical = next((r for r in stickers if r.status == "active"
                         and r.dedupe_status != "duplicate"
                         and not r.duplicate_of_id), None)
        groups.append({
            "content_hash": content_hash,
            "count": n,
            "canonical_id": canonical.id if canonical else None,
            "needs_canonical": canonical is None,
            "items": [_sticker_dict(r) for r in stickers],
        })
    return {"groups": groups}


@router.get("/stickers/{sticker_id:int}")
def get_sticker(sticker_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    return _sticker_dict(row)


@router.put("/stickers/{sticker_id}")
def update_sticker(sticker_id: int, body: StickerUpdate, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    updates = {}
    for field, attr in [("name", "name"), ("description", "description"), ("status", "status")]:
        val = getattr(body, field, None)
        if val is not None:
            setattr(row, attr, val); updates[field] = val
    if body.tags is not None:
        row.tags_json = json.dumps(body.tags, ensure_ascii=False); updates["tags"] = body.tags
    if body.emotions is not None:
        row.emotions_json = json.dumps(body.emotions, ensure_ascii=False); updates["emotions"] = body.emotions
    db.commit()
    _audit_request(db, request, "update_sticker", "sticker", sticker_id, updates)
    return _sticker_dict(row)


@router.post("/stickers/{sticker_id}/enable")
def enable_sticker(sticker_id: int, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    if row.dedupe_status == "duplicate":
        raise HTTPException(400, "duplicate sticker cannot be enabled directly")
    row.status = "active"; db.commit()
    _audit_request(db, request, "enable_sticker", "sticker", sticker_id)
    return {"ok": True}


@router.post("/stickers/{sticker_id}/disable")
def disable_sticker(sticker_id: int, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.status = "disabled"; db.commit()
    _audit_request(db, request, "disable_sticker", "sticker", sticker_id)
    return {"ok": True}


@router.get("/stickers/{sticker_id}/preview")
def preview_sticker(sticker_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from fastapi.responses import FileResponse
    from core.sticker_preview import (
        cache_sticker_preview, media_type_for_path, safe_existing_local_path,
    )

    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")

    existing = safe_existing_local_path(row.local_path or "")
    if existing:
        return FileResponse(existing, media_type=media_type_for_path(existing))

    result = cache_sticker_preview(db, sticker_id)
    if result.ok and result.local_path:
        return FileResponse(result.local_path, media_type=media_type_for_path(result.local_path))

    status_code = 400 if result.status == "blocked" else 404
    raise HTTPException(status_code, f"preview {result.status}: {result.error}")


@router.post("/stickers/{sticker_id}/redescribe")
def redescribe_sticker(sticker_id: int, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.sticker_memory import auto_describe_sticker

    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    try:
        auto_describe_sticker(sticker_id, force=True)
        db.refresh(row)
        ok = row.describe_status == "ok"
        _audit_request(db, request, "redescribe_sticker", "sticker", sticker_id)
        return {"ok": ok, "describe_status": row.describe_status, "description": row.description or "",
                "error": row.describe_last_error if not ok else ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/stickers/{sticker_id}/preview/retry")
def retry_preview(sticker_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.sticker_preview import cache_sticker_preview

    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.preview_status = "pending"
    row.local_path = ""
    db.commit()

    result = cache_sticker_preview(db, sticker_id, force=True)
    return {
        "ok": result.ok,
        "status": result.status,
        "error": result.error,
        "sticker_id": sticker_id,
    }


@router.post("/stickers/dedupe/exact/backfill")
def stickers_dedupe_backfill(
    request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    from core.sticker_preview import backfill_exact_dedupe
    result = backfill_exact_dedupe(db)
    _audit_request(db, request, "sticker.dedupe.backfill", "sticker", "", result)
    return result


@router.get("/stickers/near-duplicate-candidates")
def list_near_duplicate_candidates(
    limit: int = 50, db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    from core.database import StickerDuplicateCandidate, StickerMemory as SM
    rows = (
        db.query(StickerDuplicateCandidate)
        .filter(StickerDuplicateCandidate.status == "pending")
        .order_by((StickerDuplicateCandidate.phash_dist + StickerDuplicateCandidate.dhash_dist).asc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    items = []
    for r in rows:
        a = db.query(SM).filter(SM.id == r.sticker_a_id).first()
        b = db.query(SM).filter(SM.id == r.sticker_b_id).first()
        if a and b:
            items.append({
                "id": r.id,
                "sticker_a": _sticker_dict(a),
                "sticker_b": _sticker_dict(b),
                "phash_dist": r.phash_dist,
                "dhash_dist": r.dhash_dist,
                "content_hash": r.content_hash,
                "status": r.status,
                "created_at": _iso(r.created_at),
            })
    return {"items": items, "total": len(rows)}


_NEAR_DUP_SCAN_LOCK = threading.Lock()

@router.post("/stickers/near-duplicate/scan")
def scan_near_duplicates_endpoint(
    request: Request, limit: int = 100,
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    if not _NEAR_DUP_SCAN_LOCK.acquire(blocking=False):
        raise HTTPException(409, "扫描正在进行中，请稍后再试")
    try:
        from core.sticker_preview import scan_near_duplicates
        result = scan_near_duplicates(db, limit=min(limit, 500))
        _audit_request(db, request, "sticker.near_duplicate.scan", "sticker", "", result)
        return result
    except Exception as e:
        logger.exception("scan near duplicates failed")
        raise HTTPException(500, f"扫描失败: {str(e)[:300]}")
    finally:
        _NEAR_DUP_SCAN_LOCK.release()


@router.post("/stickers/phash/backfill")
def backfill_phash_endpoint(
    request: Request, limit: int = 200,
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    from core.sticker_preview import backfill_phash
    result = backfill_phash(db, limit=min(limit, 1000))
    _audit_request(db, request, "sticker.phash.backfill", "sticker", "", result)
    return result


class NearDuplicateAction(BaseModel):
    action: str = "ignore"  # ignore or confirm
    canonical_id: int = 0


@router.post("/stickers/near-duplicate-candidates/{candidate_id}/{action}")
def update_near_duplicate_candidate(
    candidate_id: int, action: str, request: Request,
    body: NearDuplicateAction = NearDuplicateAction(),
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    from core.database import StickerDuplicateCandidate, StickerMemory as SM

    row = db.query(StickerDuplicateCandidate).filter(StickerDuplicateCandidate.id == candidate_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    if row.status != "pending":
        raise HTTPException(400, f"candidate already {row.status}")
    if action == "ignore":
        row.status = "ignored"
        db.commit()
        _audit_request(db, request, "sticker.near_duplicate.ignore", "sticker", str(candidate_id))
        return {"ok": True, "status": row.status}

    if action != "confirm":
        raise HTTPException(400, "action must be ignore or confirm")

    # 确认疑似重复：将 sticker_b 标记为 sticker_a 的 duplicate
    canonical_id = body.canonical_id or row.sticker_a_id
    if canonical_id not in {row.sticker_a_id, row.sticker_b_id}:
        raise HTTPException(400, "canonical_id must be sticker_a_id or sticker_b_id")
    dup_id = row.sticker_b_id if canonical_id == row.sticker_a_id else row.sticker_a_id

    canonical = db.query(SM).filter(SM.id == canonical_id).first()
    dup = db.query(SM).filter(SM.id == dup_id).first()
    if not canonical or not dup:
        raise HTTPException(404, "sticker not found")
    if canonical.status == "duplicate" or canonical.dedupe_status == "duplicate" or canonical.duplicate_of_id:
        raise HTTPException(400, "canonical is itself duplicate")

    dup.status = "duplicate"
    dup.dedupe_status = "duplicate"
    dup.duplicate_of_id = canonical.id

    import json as _json
    try:
        meta = _json.loads(dup.meta_json or "{}")
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    meta["dedupe_reason"] = "near_duplicate"
    meta["near_duplicate_candidate_id"] = candidate_id
    meta["phash_dist"] = row.phash_dist
    meta["dhash_dist"] = row.dhash_dist
    dup.meta_json = _json.dumps(meta, ensure_ascii=False)

    row.status = "confirmed"
    db.commit()
    _audit_request(db, request, "sticker.near_duplicate.confirm", "sticker",
                   str(dup_id), {"canonical_id": canonical_id, "candidate_id": candidate_id})
    return {"ok": True, "status": "confirmed", "duplicate_id": dup_id}


class SetCanonicalBody(BaseModel):
    activate: bool = Field(default=True)


@router.post("/stickers/{sticker_id}/set-canonical")
def sticker_set_canonical(
    sticker_id: int, request: Request, body: SetCanonicalBody = SetCanonicalBody(),
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    from core.sticker_preview import dedupe_by_content_hash

    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    if not row.content_hash:
        raise HTTPException(400, "no content_hash")

    if body.activate and row.status in ("duplicate", "disabled"):
        row.status = "active"
        # 不 commit，让 dedupe_by_content_hash 统一提交

    canonical_id = dedupe_by_content_hash(db, sticker_id, force_set_canonical=sticker_id)
    _audit_request(db, request, "sticker.set_canonical", "sticker", str(sticker_id),
                   {"canonical_id": canonical_id})
    return {"ok": True, "canonical_id": canonical_id}


class MarkDuplicateBody(BaseModel):
    canonical_id: int = Field(default=0)


@router.post("/stickers/{sticker_id}/mark-duplicate")
def sticker_mark_duplicate(
    sticker_id: int, request: Request, body: MarkDuplicateBody = MarkDuplicateBody(),
    db: Session = Depends(get_db), _auth=Depends(verify_admin),
):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    if not body.canonical_id:
        raise HTTPException(400, "canonical_id required")

    canonical = db.query(StickerMemory).filter(StickerMemory.id == body.canonical_id).first()
    if not canonical:
        raise HTTPException(404, "canonical not found")
    if canonical.id == sticker_id:
        raise HTTPException(400, "cannot mark self as duplicate")
    if canonical.content_hash != row.content_hash:
        raise HTTPException(400, "content_hash mismatch")
    if canonical.status == "duplicate" or canonical.dedupe_status == "duplicate" or canonical.duplicate_of_id:
        raise HTTPException(400, "canonical is itself a duplicate — 不能形成链式重复")

    row.status = "duplicate"
    row.dedupe_status = "duplicate"
    row.duplicate_of_id = canonical.id
    db.commit()
    _audit_request(db, request, "sticker.mark_duplicate", "sticker", str(sticker_id),
                   {"canonical_id": canonical.id})
    return {"ok": True}


@router.post("/stickers/batch-delete")
def batch_delete_stickers(body: dict, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    raw = body.get("ids", [])
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "ids required")
    ids: set[int] = set()
    for x in raw:
        try:
            ids.add(int(x))
        except (ValueError, TypeError):
            continue
    if not ids:
        raise HTTPException(400, "no valid ids")
    if len(ids) > 500:
        raise HTTPException(400, f"too many ids, max 500")
    rows = db.query(StickerMemory).filter(StickerMemory.id.in_(list(ids))).all()
    count = 0
    for row in rows:
        if row.status != "deleted":
            row.status = "deleted"
            count += 1
    db.commit()
    _audit_request(db, request, "batch_delete_stickers", "sticker", f"batch_{len(ids)}", {
        "count": count, "ids_sample": sorted(ids)[:50],
    })
    return {"ok": True, "deleted": count}


@router.delete("/stickers/{sticker_id}")
def delete_sticker(sticker_id: int, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.status = "deleted"; db.commit()
    _audit_request(db, request, "soft_delete_sticker", "sticker", sticker_id)
    return {"ok": True}


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


# ═══════════════════════════════════════════
# DB Browser (read-only)
# ═══════════════════════════════════════════

DB_TABLE_GROUPS = [
    {
        "key": "core",
        "label": "核心对话",
        "tables": [
            "users", "chat_logs", "conversation_turns", "memory_digests",
            "rolling_session_summaries", "session_summary_jobs",
        ],
    },
    {
        "key": "persona",
        "label": "画像与记忆",
        "tables": [
            "personas", "persona_facts", "persona_behaviors",
            "group_memories", "expression_memories", "jargon_memories",
            "sticker_memories",
        ],
    },
    {
        "key": "rag",
        "label": "向量与知识库",
        "tables": [
            "semantic_index_items", "semantic_index_jobs",
            "knowledge_sources", "knowledge_documents", "knowledge_chunks",
        ],
    },
    {
        "key": "runtime",
        "label": "LLM 与 Agent 调试",
        "tables": [
            "agent_runs", "tool_calls", "llm_api_request_logs",
            "runtime_tool_decisions", "tool_policy_decisions", "tool_overrides",
            "prompt_render_logs", "prompt_file_versions",
            "reply_contract_check_logs",
        ],
    },
    {
        "key": "rules",
        "label": "配置与规则",
        "tables": [
            "chat_stream_configs", "system_prompts", "scheduled_tasks",
            "admin_audit_logs", "user_block_rules", "content_block_rules",
            "system_settings",
        ],
    },
]

READONLY_TABLES = [table for group in DB_TABLE_GROUPS for table in group["tables"]]
READONLY_TABLE_SET = set(READONLY_TABLES)
BLOCKED_DB_TABLES = {
    "sensitive_data",
    "sqlite_master",
    "sqlite_schema",
    "sqlite_temp_master",
}
GLOBAL_REDACT_COLUMNS = {"headers_json"}
GLOBAL_PREVIEW_ONLY_COLUMNS = {"request_json", "response_json", "message_sources_json"}

DEFAULT_DB_TABLE_POLICY = {
    "description": "",
    "default_sort": "rowid DESC",
    "hidden_columns": [],
    "redact_columns": [],
    "preview_only_columns": [],
    "max_text_length": 1000,
}

DB_TABLE_POLICIES = {
    "chat_logs": {"description": "原始消息存档，含 tool 与 ambient。", "default_sort": "id DESC"},
    "conversation_turns": {"description": "精简对话上下文。", "default_sort": "id DESC"},
    "persona_facts": {"description": "用户画像事实与聚类数据。", "default_sort": "id DESC"},
    "persona_behaviors": {"description": "用户行为模式候选。", "default_sort": "id DESC"},
    "semantic_index_items": {
        "description": "统一语义索引条目。",
        "default_sort": "id DESC",
        "preview_only_columns": ["text", "lexical_text", "embedding_text", "meta_json"],
    },
    "semantic_index_jobs": {"description": "语义索引异步任务。", "default_sort": "id DESC"},
    "knowledge_sources": {"description": "外部知识来源。", "default_sort": "id DESC"},
    "knowledge_documents": {
        "description": "知识库文档和 ai_daily 入库记录。",
        "default_sort": "id DESC",
        "preview_only_columns": ["summary", "meta_json"],
    },
    "knowledge_chunks": {
        "description": "知识库文档 chunk。",
        "default_sort": "id DESC",
        "preview_only_columns": ["text", "citation_json", "meta_json"],
    },
    "llm_api_request_logs": {
        "description": "模型网关请求日志；默认仅展示预览字段。",
        "default_sort": "id DESC",
        "hidden_columns": ["headers_json", "request_json", "response_json", "message_sources_json"],
        "redact_columns": ["headers_json"],
        "preview_only_columns": ["request_json", "response_json", "message_sources_json"],
        "max_text_length": 600,
    },
    "prompt_render_logs": {
        "description": "PromptManager 渲染记录。",
        "default_sort": "id DESC",
        "preview_only_columns": ["variables_json", "rendered_preview", "warnings_json"],
        "max_text_length": 600,
    },
    "rolling_session_summaries": {
        "description": "滚动上下文摘要结果。",
        "default_sort": "id DESC",
        "preview_only_columns": ["summary_text", "summary_json", "source_turn_ids_json", "meta_json"],
    },
    "session_summary_jobs": {"description": "滚动摘要异步生成任务。", "default_sort": "id DESC"},
    "reply_contract_check_logs": {"description": "reply/no_reply 合约审核日志。", "default_sort": "id DESC"},
    "content_block_rules": {"description": "内容屏蔽规则。", "default_sort": "id DESC"},
    "agent_runs": {"description": "一次模型/Agent 处理请求。", "default_sort": "started_at DESC"},
    "tool_calls": {"description": "工具调用记录。", "default_sort": "started_at DESC"},
    "runtime_tool_decisions": {"description": "每轮运行时工具决策。", "default_sort": "id DESC"},
    "tool_policy_decisions": {"description": "工具策略决策记录。", "default_sort": "id DESC"},
    "tool_overrides": {"description": "工具权限覆盖。", "default_sort": "id DESC"},
}


def _db_table_policy(table_name: str) -> dict[str, Any]:
    policy = dict(DEFAULT_DB_TABLE_POLICY)
    policy.update(DB_TABLE_POLICIES.get(table_name, {}))
    return policy


def _db_table_meta(table_name: str) -> dict[str, Any]:
    policy = _db_table_policy(table_name)
    return {
        "description": policy["description"] or table_name,
        "default_sort": policy["default_sort"],
        "hidden_columns": list(policy["hidden_columns"]),
        "redact_columns": list(policy["redact_columns"]),
        "preview_only_columns": list(policy["preview_only_columns"]),
        "max_text_length": int(policy["max_text_length"]),
    }


def _quote_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier or ""):
        raise HTTPException(400, f"Invalid identifier: {identifier}")
    return '"' + identifier.replace('"', '""') + '"'


def _table_columns(db: Session, table_name: str) -> list[str]:
    quoted = _quote_identifier(table_name)
    rows = db.execute(text(f"PRAGMA table_info({quoted})")).fetchall()
    return [str(row[1]) for row in rows]


def _safe_serialize_cell(value: Any, table_name: str, column_name: str) -> tuple[Any, dict[str, Any]]:
    policy = _db_table_policy(table_name)
    redact_columns = set(policy["redact_columns"]) | GLOBAL_REDACT_COLUMNS
    preview_only_columns = set(policy["preview_only_columns"]) | GLOBAL_PREVIEW_ONLY_COLUMNS
    meta: dict[str, Any] = {
        "kind": "null" if value is None else "value",
        "truncated": False,
        "full_length": None,
    }
    if column_name in redact_columns:
        meta["kind"] = "redacted"
        return "<redacted>", meta
    if value is None:
        return None, meta
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        size = len(value)
        meta.update({"kind": "binary", "truncated": True, "full_length": size})
        return f"<binary {size} bytes>", meta

    if isinstance(value, (str, int, float, bool)):
        display = value
    else:
        display = str(value)

    if isinstance(display, str):
        max_len = int(policy["max_text_length"])
        if column_name in preview_only_columns:
            max_len = min(max_len, 300)
        meta.update({"kind": "text", "full_length": len(display)})
        if len(display) > max_len:
            meta["truncated"] = True
            return display[:max_len] + "...", meta
    return display, meta


def _serialize_db_rows(table_name: str, columns: list[str], fetched_rows: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    cell_meta: list[dict[str, Any]] = []
    for raw_row in fetched_rows:
        values = list(raw_row)
        row_dict: dict[str, Any] = {}
        meta_dict: dict[str, Any] = {}
        for column, value in zip(columns, values):
            display, meta = _safe_serialize_cell(value, table_name, column)
            row_dict[column] = display
            meta_dict[column] = meta
        rows.append(row_dict)
        cell_meta.append(meta_dict)
    return rows, cell_meta


def _extract_query_table_names(query: str) -> list[str]:
    """提取 SELECT 中 FROM/JOIN 后的表名，避免使用连接级 SQLite authorizer。"""
    tables: list[str] = []
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+(?:main\.)?(?:\"([A-Za-z_][A-Za-z0-9_]*)\"|([A-Za-z_][A-Za-z0-9_]*))",
        re.IGNORECASE,
    )
    for match in pattern.finditer(query):
        name = (match.group(1) or match.group(2) or "").strip()
        if name:
            tables.append(name)
    return tables


def _validate_query_tables_allowed(query: str) -> None:
    sqlite_table = re.search(r"\bsqlite_[A-Za-z0-9_]*\b", query, re.IGNORECASE)
    if sqlite_table:
        raise HTTPException(400, f"Forbidden table: {sqlite_table.group(0)}")
    for table in _extract_query_table_names(query):
        normalized = table.lower()
        if normalized in BLOCKED_DB_TABLES or normalized.startswith("sqlite_") or table not in READONLY_TABLE_SET:
            raise HTTPException(400, f"Forbidden table: {table}")


def _validate_readonly_query(query: str) -> str:
    q = query.strip()
    q_no_trailing_semicolon = q[:-1].strip() if q.endswith(";") else q
    if not q_no_trailing_semicolon:
        raise HTTPException(400, "Empty query")
    if ";" in q_no_trailing_semicolon:
        raise HTTPException(400, "Multi-statement forbidden")
    if not re.match(r"^\s*SELECT\b", q_no_trailing_semicolon, re.IGNORECASE):
        raise HTTPException(400, "Only SELECT allowed")

    forbidden = (
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
        "PRAGMA", "ATTACH", "DETACH", "VACUUM", "REINDEX", "LOAD_EXTENSION",
        "REPLACE",
    )
    for word in forbidden:
        if re.search(rf"\b{word}\b", q_no_trailing_semicolon, re.IGNORECASE):
            raise HTTPException(400, f"Forbidden: {word}")
    for table in BLOCKED_DB_TABLES:
        if re.search(rf"\b{re.escape(table)}\b", q_no_trailing_semicolon, re.IGNORECASE):
            raise HTTPException(400, f"Forbidden table: {table}")
    _validate_query_tables_allowed(q_no_trailing_semicolon)
    return q_no_trailing_semicolon


def _available_readonly_tables(db: Session) -> list[str]:
    existing = {
        str(row[0])
        for row in db.execute(
            text("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        ).fetchall()
    }
    return [table for table in READONLY_TABLES if table in existing]


def _available_db_groups(db: Session) -> list[dict[str, Any]]:
    available = set(_available_readonly_tables(db))
    groups: list[dict[str, Any]] = []
    for group in DB_TABLE_GROUPS:
        tables = [table for table in group["tables"] if table in available]
        if tables:
            groups.append({**group, "tables": tables})
    return groups


@router.get("/db/tables")
def list_tables(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    tables = _available_readonly_tables(db)
    return {
        "tables": tables,
        "groups": _available_db_groups(db),
        "table_meta": {table: _db_table_meta(table) for table in tables},
    }


@router.get("/db/tables/{table_name}")
def query_table(table_name: str, page: int = 1, limit: int = 50,
                db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    if table_name not in READONLY_TABLES:
        raise HTTPException(400, f"Unknown table: {table_name}")
    page = max(int(page), 1)
    limit = max(1, min(int(limit), 200))
    try:
        all_columns = _table_columns(db, table_name)
        hidden_columns = set(_db_table_policy(table_name)["hidden_columns"])
        columns = [column for column in all_columns if column not in hidden_columns]
        if not columns:
            raise HTTPException(400, f"No visible columns: {table_name}")
        quoted_table = _quote_identifier(table_name)
        select_columns = ", ".join(_quote_identifier(column) for column in columns)
        default_sort = _db_table_policy(table_name)["default_sort"]
        result = db.execute(
            text(f"SELECT {select_columns} FROM {quoted_table} ORDER BY {default_sort} LIMIT :limit OFFSET :offset"),
            {"limit": limit, "offset": (page - 1) * limit})
        result_columns = list(result.keys())
        fetched = result.fetchall()
        rows, cell_meta = _serialize_db_rows(table_name, result_columns, fetched)
        total = db.execute(text(f"SELECT COUNT(*) FROM {quoted_table}")).scalar()
        return {
            "total": total,
            "page": page,
            "limit": limit,
            "has_next": (page * limit) < int(total or 0),
            "columns": result_columns,
            "rows": rows,
            "cell_meta": cell_meta,
            "table_meta": _db_table_meta(table_name),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/db/query")
def execute_readonly_query(body: DbQuery, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    q = _validate_readonly_query(body.query)
    try:
        query_with_limit = f"SELECT * FROM ({q}) LIMIT 500"
        result = db.execute(text(query_with_limit))
        columns = list(result.keys()) if result.returns_rows else []
        fetched = result.fetchall() if result.returns_rows else []
        rows, cell_meta = _serialize_db_rows("", columns, fetched)
        return {"columns": columns, "rows": rows, "cell_meta": cell_meta, "row_count": len(rows)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════
# PromptManager templates
# ═══════════════════════════════════════════

@router.get("/prompts")
def list_managed_prompts(_auth=Depends(verify_admin)):
    from core.settings_service import settings

    manager = get_prompt_manager()
    return {
        "items": manager.list_prompts(),
        "prompt_dir": str(manager.prompt_dir),
        "default_dir": manager.default_dir,
        "backup_dir": str(manager.backup_dir),
        "mode": str(settings.get("prompt_system.mode", "shadow") or "shadow"),
    }


@router.get("/prompts/{prompt_key}")
def get_managed_prompt(prompt_key: str, _auth=Depends(verify_admin)):
    try:
        return get_prompt_manager().get_prompt(prompt_key)
    except PromptRenderError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))


@router.put("/prompts/{prompt_key}")
def save_managed_prompt(
    prompt_key: str,
    body: PromptSaveRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = get_prompt_manager().save_prompt(prompt_key, body.content, operator="admin")
    except PromptRenderError as e:
        raise HTTPException(400, str(e))
    _audit_request(db, request, "save_managed_prompt", "prompt", prompt_key, result)
    return result


@router.post("/prompts/{prompt_key}/preview")
def preview_managed_prompt(prompt_key: str, body: PromptPreviewRequest, _auth=Depends(verify_admin)):
    try:
        rendered = get_prompt_manager().render(
            prompt_key,
            body.variables,
            mode=body.mode or "preview",
        )
        return rendered.to_dict()
    except PromptRenderError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))


def _prompt_sha256(content: str) -> str:
    import hashlib

    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()


def _legacy_prompt_preview_meta() -> dict:
    from core.legacy_prompt_runtime import read_runtime_or_default_prompt

    result = read_runtime_or_default_prompt()
    content = str(result.get("content") or "")
    source_key = str(result.get("source") or "")
    if source_key == "runtime":
        source = "Legacy runtime prompt"
    elif source_key == "default":
        source = "Legacy default prompt"
    else:
        source = "bridge manual assembly"
    return {
        "content": content,
        "prompt_source": source,
        "prompt_runtime_path": str(result.get("output_path") or ""),
        "prompt_default_path": str(result.get("default_path") or ""),
        "prompt_sha256": _prompt_sha256(content) if content else "",
    }


def _recent_prompt_preview_logs(db: Session, body: EffectivePromptPreviewRequest) -> tuple[str, list[dict]]:
    from core.database import LLMApiRequestLog

    q = db.query(AgentRun)
    if body.session_id:
        q = q.filter(AgentRun.session_id == body.session_id)
    if body.user_id:
        q = q.filter(AgentRun.user_id == body.user_id)
    if body.group_id:
        q = q.filter(AgentRun.group_id == body.group_id)
    if body.chat_type:
        q = q.filter(AgentRun.chat_type == body.chat_type)
    run = q.order_by(AgentRun.started_at.desc()).first()
    if not run:
        return "", []
    logs = (
        db.query(LLMApiRequestLog)
        .filter(LLMApiRequestLog.run_id == run.run_id)
        .order_by(LLMApiRequestLog.created_at.desc())
        .limit(5)
        .all()
    )
    return run.run_id, [row_to_dict(row) for row in logs]


@router.post("/prompt/effective-preview")
async def preview_effective_prompt(
    body: EffectivePromptPreviewRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    if body.engine == "v2":
        from app.prompt_runtime.preview_service import preview_effective_prompt_v2

        return await preview_effective_prompt_v2(body, db)

    from core.database import Persona
    from core.context_builder import build_chat_context
    from core.runtime_tool_service import build_runtime_tool_prompt, resolve_effective_tools
    from core.tool_schema_preview import build_effective_tool_schemas

    is_group = body.chat_type == "group"
    group_id = body.group_id.strip()
    session_id = body.session_id.strip() or (f"group_{group_id}" if is_group and group_id else "")
    user_id = body.user_id.strip()
    prompt_key = body.prompt_key.strip() or ("group_chat" if is_group else "private_chat")

    persona_text = ""
    if user_id:
        persona = db.query(Persona).filter(Persona.user_id == user_id).first()
        if persona and persona.persona_json:
            persona_text = persona.persona_json

    history_header, history_messages, history_debug = build_chat_context(
        db,
        session_id,
        user_id=user_id,
        is_group=is_group,
        group_id=group_id,
        current_user_input=body.user_input,
    )
    runtime_preset = (body.runtime_preset or "full").strip() or "full"
    enabled, disabled = resolve_effective_tools(
        chat_type=body.chat_type,
        group_id=group_id,
        user_id=user_id,
        runtime_preset=runtime_preset,
        db=db,
    )
    runtime_tool_prompt = build_runtime_tool_prompt(enabled, disabled, body.chat_type)
    tool_schemas = build_effective_tool_schemas(enabled, db=db)

    from core.prompt_assembler import PromptAssembler, PromptBuildContext

    prompt_build = PromptAssembler().build(
        PromptBuildContext(
            mode=body.mode,
            chat_type=body.chat_type,
            prompt_key=prompt_key,
            session_id=session_id,
            user_id=user_id,
            group_id=group_id,
            sender_name=body.sender_name,
            sender_id=user_id,
            user_input=body.user_input,
            persona_text=persona_text or "无已存储画像",
            history_header=history_header,
            history_messages=history_messages,
            runtime_tool_prompt=runtime_tool_prompt,
            tool_schemas=tool_schemas,
        )
    )
    messages = prompt_build.messages
    recent_run_id, recent_logs = _recent_prompt_preview_logs(db, body)
    tools = [{"name": name, "enabled": bool(enabled.get(name, True))} for name in sorted(enabled.keys())]
    request_json = prompt_build.request_json
    return {
        "chat_type": body.chat_type,
        "session_id": session_id,
        "user_id": user_id,
        "group_id": group_id,
        "prompt_key": prompt_key,
        "prompt_mode": prompt_build.prompt_mode,
        "prompt_source": prompt_build.prompt_source,
        "prompt_runtime_path": prompt_build.prompt_runtime_path,
        "prompt_default_path": prompt_build.prompt_default_path,
        "prompt_sha256": prompt_build.prompt_sha256,
        "managed_prompt_sha256": prompt_build.managed_prompt_sha256,
        "legacy_prompt_sha256": prompt_build.legacy_prompt_sha256,
        "legacy_vs_managed_diff": prompt_build.diff,
        "warnings": prompt_build.warnings,
        "system_message": messages[0]["content"] if messages else "",
        "identity_context": next((m["content"] for m in messages if "<identity_context>" in str(m.get("content", ""))), ""),
        "runtime_context": next((m["content"] for m in messages if "<runtime_context>" in str(m.get("content", ""))), ""),
        "persona_reference": next((m["content"] for m in messages if "<persona_reference" in str(m.get("content", ""))), ""),
        "history_context": prompt_build.variables.get("history_context", ""),
        "history_debug": history_debug,
        "group_recent_context": "",
        "runtime_preset": runtime_preset,
        "runtime_tool_prompt": runtime_tool_prompt,
        "tools": tools,
        "tool_schemas": tool_schemas,
        "effective_tool_schemas": tool_schemas,
        "disabled_tools": disabled,
        "prompt_manager_render": prompt_build.render,
        "compiled_prompt": prompt_build.to_dict(),
        "prompt_build": prompt_build.to_dict(),
        "managed_messages": prompt_build.managed_messages,
        "legacy_messages": prompt_build.legacy_messages,
        "messages": messages,
        "request_json": request_json,
        "recent_agent_run_id": recent_run_id,
        "recent_llm_api_logs": recent_logs,
    }


@router.post("/prompts/reload")
def reload_managed_prompts(_auth=Depends(verify_admin)):
    return get_prompt_manager().reload()


@router.get("/prompts/{prompt_key}/history")
def managed_prompt_history(prompt_key: str, _auth=Depends(verify_admin)):
    try:
        return {"items": get_prompt_manager().history(prompt_key)}
    except PromptRenderError as e:
        raise HTTPException(400, str(e))


@router.post("/prompts/{prompt_key}/rollback")
def rollback_managed_prompt(
    prompt_key: str,
    body: PromptRollbackRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = get_prompt_manager().rollback(prompt_key, body.backup_name, operator="admin")
    except PromptRenderError as e:
        raise HTTPException(400, str(e))
    _audit_request(db, request, "rollback_managed_prompt", "prompt", prompt_key, result)
    return result


# ═══════════════════════════════════════════
# Agent trace
# ═══════════════════════════════════════════

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
    from core.database import LLMApiRequestLog, ReplyContractCheckLog
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


# ═══════════════════════════════════════════
# LLM API 请求日志
# ═══════════════════════════════════════════

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
    from core.database import LLMApiRequestLog
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
            item = dict(row._mapping)
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
    from core.database import LLMApiRequestLog
    row = db.query(LLMApiRequestLog).filter(LLMApiRequestLog.id == log_id).first()
    if not row:
        raise HTTPException(404, "LLM API request log not found")
    return row_to_dict(row)


# ═══════════════════════════════════════════
# Legacy Prompt——运行时分离
# ═══════════════════════════════════════════

@router.get("/prompt")
def get_prompt(_auth=Depends(verify_admin)):
    from core.legacy_prompt_runtime import read_runtime_or_default_prompt
    result = read_runtime_or_default_prompt()
    if result["source"] == "none":
        raise HTTPException(404, "prompt.md not found (no runtime or default)")
    return {
        "content": result["content"],
        "source": result["source"],
        "output_path": result["output_path"],
        "default_path": result["default_path"],
        "metrics": _prompt_metrics(result["content"]),
    }


@router.get("/prompt/fragments")
def list_prompt_fragments(_auth=Depends(verify_admin)):
    from core.legacy_prompt_runtime import list_fragments_with_status, default_fragments_dir, runtime_fragments_dir, runtime_prompt_output, backup_dir as _lbkp_dir
    items = list_fragments_with_status()
    return {
        "fragments": items,
        "default_dir": default_fragments_dir(),
        "runtime_dir": runtime_fragments_dir(),
        "output_path": runtime_prompt_output(),
        "backup_dir": _lbkp_dir(),
    }


@router.put("/prompt/fragments/{name}")
def update_prompt_fragment(name: str, body: dict, request: Request, db: Session = Depends(get_db),
                           _auth=Depends(verify_admin)):
    from core.legacy_prompt_runtime import save_fragment
    content = str(body.get("content", ""))
    if not content.strip():
        raise HTTPException(400, "Refuse to save empty prompt fragment")
    try:
        result = save_fragment(name, content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _audit_request(db, request, "update_prompt_fragment", "prompt_fragment", name, {
        "before_hash": result["before_hash"], "after_hash": result["after_hash"],
        "runtime_path": result["runtime_path"],
    })
    return result


@router.post("/prompt/build")
def rebuild_prompt(request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.legacy_prompt_runtime import build_prompt_from_runtime
    try:
        result = build_prompt_from_runtime(chat_type="base")
        _audit_request(db, request, "rebuild_prompt", "prompt", "legacy", {
            "ok": result.get("ok"), "output": result.get("output", ""),
        })
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/prompt/fragments/{name}/default")
def get_prompt_fragment_default(name: str, _auth=Depends(verify_admin)):
    from core.legacy_prompt_runtime import get_default_fragment
    result = get_default_fragment(name)
    if not result:
        raise HTTPException(404, f"Default fragment not found: {name}")
    return result


@router.get("/prompt/fragments/{name}/diff-default")
def diff_prompt_fragment(name: str, _auth=Depends(verify_admin)):
    import difflib
    from core.legacy_prompt_runtime import get_default_fragment, list_fragments_with_status
    default = get_default_fragment(name)
    if not default:
        raise HTTPException(404, f"Default fragment not found: {name}")
    # 找运行时版本
    items = [f for f in list_fragments_with_status() if f["name"] == name]
    runtime_content = items[0]["content"] if items else ""
    diff_lines = list(difflib.unified_diff(
        default["content"].splitlines(keepends=True),
        runtime_content.splitlines(keepends=True) if runtime_content else [""],
        fromfile=f"default/{name}",
        tofile=f"runtime/{name}",
    ))
    return {
        "name": name,
        "default_hash": default["hash"],
        "runtime_hash": items[0]["runtime_hash"] if items else "",
        "is_modified": items[0]["is_modified"] if items else True,
        "diff": "".join(diff_lines) if diff_lines else "无差异",
    }


@router.post("/prompt/fragments/{name}/reset-to-default")
def reset_prompt_fragment(name: str, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.legacy_prompt_runtime import reset_to_default
    try:
        result = reset_to_default(name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    _audit_request(db, request, "reset_prompt_fragment", "prompt_fragment", name, result)
    return result


@router.post("/prompt/init-runtime")
def init_prompt_runtime(request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from core.legacy_prompt_runtime import init_legacy_prompt_runtime_dir
    result = init_legacy_prompt_runtime_dir()
    _audit_request(db, request, "init_prompt_runtime", "prompt", "legacy", {
        "copied": result["copied"],
    })
    return result


def _prompt_backup_dir() -> str:
    from core.legacy_prompt_runtime import backup_dir
    return backup_dir()


def _parse_prompt_backup_name(name: str) -> dict | None:
    import re
    m = re.fullmatch(
        r"(?P<fragment>[A-Za-z0-9_.-]+\.md)\.(?P<ts>\d{8}_\d{6}_\d{6})\.(?P<hash>[a-f0-9]+)\.bak",
        name,
    )
    return m.groupdict() if m else None


@router.get("/prompt/backups")
def list_prompt_backups(_auth=Depends(verify_admin)):
    bkp_dir = _prompt_backup_dir()
    items = []
    if os.path.isdir(bkp_dir):
        for fname in sorted(os.listdir(bkp_dir), reverse=True):
            parsed = _parse_prompt_backup_name(fname)
            if not parsed:
                continue
            path = os.path.join(bkp_dir, fname)
            items.append({
                "name": fname,
                "fragment": parsed["fragment"],
                "hash": parsed["hash"],
                "created_at": parsed["ts"],
                "size": os.path.getsize(path),
                "mtime": os.path.getmtime(path),
            })
    return {"backups": items}


@router.post("/prompt/backups/{backup_name}/rollback")
def rollback_prompt_backup(backup_name: str, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    import hashlib, shutil
    from core.legacy_prompt_runtime import runtime_fragments_dir

    parsed = _parse_prompt_backup_name(os.path.basename(backup_name))
    if not parsed:
        raise HTTPException(400, "Invalid backup name")
    bkp_dir = os.path.abspath(_prompt_backup_dir())
    backup_path = os.path.abspath(os.path.join(bkp_dir, backup_name))
    if not backup_path.startswith(bkp_dir + os.sep) or not os.path.isfile(backup_path):
        raise HTTPException(404, "Backup not found")

    runtime_dir = os.path.abspath(runtime_fragments_dir())
    target = os.path.abspath(os.path.join(runtime_dir, parsed["fragment"]))
    if not target.startswith(runtime_dir + os.sep):
        raise HTTPException(400, "Invalid fragment name")

    # 回滚到运行时 fragment（不写 default）
    if os.path.isfile(target):
        with open(target, "r", encoding="utf-8") as fh:
            current = fh.read()
        current_hash = hashlib.sha256(current.encode()).hexdigest()[:12]
        rollback_guard = os.path.join(
            bkp_dir,
            f"{parsed['fragment']}.{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.{current_hash}.bak",
        )
        shutil.copy2(target, rollback_guard)
    os.makedirs(runtime_dir, exist_ok=True)
    shutil.copy2(backup_path, target)
    _audit_request(db, request, "rollback_prompt_fragment", "prompt_fragment", parsed["fragment"], {
        "backup": backup_name,
    })
    return {"ok": True, "fragment": parsed["fragment"], "backup": backup_name}


# ═══════════════════════════════════════════
# Model status / tests
# ═══════════════════════════════════════════

@router.get("/models/status")
def models_status(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from clients.classifier_client import (
        Guardrail, resolve_model_route, list_providers, build_model_catalog,
    )
    from config import NEW_API_BASE_URL, NEW_API_KEY

    # ── Providers (脱敏) ──
    from clients.classifier_client import provider_public
    raw_providers = list_providers()
    # 确保内置 provider 始终存在（即使无 DB 配置时通过 env fallback 出现）
    if not any(p["id"] == "newapi" for p in raw_providers):
        raw_providers.append({
            "id": "newapi", "base_url": str(NEW_API_BASE_URL or ""),
            "api_key": str(NEW_API_KEY or ""), "enabled": bool(NEW_API_BASE_URL),
        })
    if not any(p["id"] in ("local_llama", "local_qwen") for p in raw_providers):
        raw_providers.append({
            "id": "local_llama", "base_url": str(CLASSIFIER_API_URL or ""),
            "api_key": "", "enabled": bool(CLASSIFIER_API_URL),
        })
    providers = [provider_public(p) for p in raw_providers]

    # ── Routes ──
    from core.route_metadata import ROUTE_METADATA, route_label_for
    routes = {}
    for rk in ROUTE_METADATA:
        r = resolve_model_route(rk)
        entry = {
            "route_key": rk,
            "label": route_label_for(rk),
            "route_type": r.get("route_type", "unknown"),
            "provider_id": r["provider_id"],
            "model": r["model"],
            "api_key_configured": r["api_key_configured"],
            "route_api_key_configured": r.get("route_api_key_configured", False),
            "provider_enabled": r.get("provider_enabled", True),
            "timeout": r["timeout"], "temperature": r["temperature"],
            "max_tokens": r["max_tokens"],
            "enable_thinking": r.get("enable_thinking", "auto"),
            "source": r.get("source", "provider"),
            "editable": True,
        }
        if r.get("inherited_from"):
            entry["inherited_from"] = r["inherited_from"]
            entry["overridden_fields"] = r.get("overridden_fields", {})
        if rk == "classifier_legacy":
            entry["note"] = "兼容旧 reply/no_reply 分类路径；正常群聊优先使用 TimingGate"
        routes[rk] = entry

    # ── Local Components ──
    persona_configured = False
    persona_load_state = "not_loaded"
    persona_error = ""
    try:
        from core.persona_preprocess import _EMBEDDER_MODEL, embed_text  # noqa: F401
        persona_configured = True
    except Exception as e:
        persona_error = str(e)[:200]
        persona_load_state = "unavailable"

    nli_configured = False
    nli_load_state = "not_loaded"
    nli_error = ""
    try:
        from core.persona_preprocess import _NLI_MODEL  # noqa: F401
        nli_configured = True
    except Exception as e:
        nli_error = str(e)[:200]
        nli_load_state = "unavailable"
    try:
        from core.semantic.provider_factory import describe_reranker_provider_config
        rag_reranker = describe_reranker_provider_config()
    except Exception as e:
        rag_reranker = {
            "configured": False,
            "load_state": "unavailable",
            "model": "BAAI/bge-reranker-v2-m3",
            "model_path": "./models/bge-reranker-v2-m3",
            "error": str(e)[:200],
        }

    sentinel_configured = True
    sentinel_load_state = "not_loaded"
    try:
        g = Guardrail()
        if g._sentinel is not None:
            sentinel_load_state = "loaded"
    except Exception:
        sentinel_configured = False
        sentinel_load_state = "unavailable"

    return {
        "providers": providers,
        "routes": routes,
        "local_components": {
            "persona_embed": {
                "model": "BAAI/bge-base-zh-v1.5",
                "loader": "sentence-transformers / HuggingFace",
                "configured": persona_configured,
                "load_state": persona_load_state,
                "error": persona_error,
                "role": "PersonaFact/PersonaBehavior 语义去重、聚类",
                "trigger": "首次画像候选处理 / 点击「测试 embedding」",
                "note": "按需懒加载；配置完成不等于已加载到内存",
            },
            "nli": {
                "model": "roberta-large-mnli",
                "loader": "transformers pipeline / HuggingFace",
                "configured": nli_configured,
                "load_state": nli_load_state,
                "error": nli_error,
                "role": "画像矛盾检测 (fallback: cosine)",
                "trigger": "首次矛盾检测 / 点击「测试 NLI」",
                "note": "按需懒加载；失败时降级为 cosine 检测",
            },
            "rag_reranker": {
                "model": rag_reranker.get("model") or "BAAI/bge-reranker-v2-m3",
                "model_path": rag_reranker.get("model_path") or "./models/bge-reranker-v2-m3",
                "resolved_model_path": rag_reranker.get("resolved_model_path"),
                "download_repo_id": rag_reranker.get("download_repo_id"),
                "loader": rag_reranker.get("loader") or "sentence-transformers CrossEncoder",
                "configured": bool(rag_reranker.get("configured")),
                "load_state": rag_reranker.get("load_state") or "not_loaded",
                "error": "" if rag_reranker.get("configured") else "本地 reranker 模型目录不存在或未配置",
                "role": "Memory / Sticker / Knowledge / GroupAnalysis RAG 候选重排",
                "trigger": "首次 RAG 查询 / 点击「测试 reranker」",
                "note": "本地模型组件，不走 new-api；默认下载 BAAI/bge-reranker-v2-m3 到 ./models/bge-reranker-v2-m3",
                "path_exists": rag_reranker.get("path_exists"),
                "source": rag_reranker.get("source"),
            },
            "sentinel": {
                "model": "prompt-injection-sentinel",
                "loader": "transformers pipeline",
                "configured": sentinel_configured,
                "load_state": sentinel_load_state,
                "role": "L1 prompt injection 检测",
                "trigger": "首次调用 _load_sentinel() 时加载",
                "note": "按需懒加载",
            },
        },
        "unsupported": {
            "rerank": {"implemented": True, "note": "通过本地 rag_reranker 组件接入 RAG rerank pipeline"},
        },
    }


class ChatModelTestRequest(BaseModel):
    model: str = ""
    prompt: str = Field(default="用一句话回复：Nanobot 模型连通性测试", max_length=4000)
    json_mode: bool = False


@router.post("/models/chat-test")
async def chat_model_test(body: ChatModelTestRequest, _auth=Depends(verify_admin)):
    import time
    from clients.new_api_client import NewAPIClient
    from config import NEW_API_KEY, NEW_API_BASE_URL

    client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL)
    user_prompt = body.prompt
    if body.json_mode:
        user_prompt = body.prompt + "\n只输出 JSON: {\"ok\": true, \"summary\": \"...\"}"
    t0 = time.time()
    from core.llm_trace_context import llm_trace_scope
    with llm_trace_scope(source="admin"):
        result = await client.chat_completion(
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0,
            manual_model=body.model,
            max_tokens=200,
        )
    latency_ms = int((time.time() - t0) * 1000)
    return {"latency_ms": latency_ms, "result": result}


# ── Model Catalog & Routes ──

@router.get("/model-catalog")
def get_model_catalog(_auth=Depends(verify_admin)):
    from clients.model_registry import registry

    models = []
    for m in registry.data.get("models", []):
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "")
        if not mid:
            continue
        models.append({
            "key": mid, "id": mid,
            "model": m.get("model") or mid,
            "provider": m.get("provider") or "",
            "tier": m.get("tier") or "",
            "intel": m.get("intelligence", 0),
            "intelligence": m.get("intelligence", 0),
            "input_cost": m.get("cost_input_1m", 0),
            "output_cost": m.get("cost_output_1m", 0),
            "cost_input_1m": m.get("cost_input_1m", 0),
            "cost_output_1m": m.get("cost_output_1m", 0),
            "tags": m.get("tags") or [],
            "description": m.get("description") or "",
            "enabled": bool(m.get("enabled", True)),
            "available": bool(m.get("available", True)),
        })
    return {"models": models, "last_updated": registry.data.get("last_updated", "never")}


# ── Provider 管理 ──

@router.get("/models/providers")
def list_model_providers(_auth=Depends(verify_admin)):
    """列出所有已配置的供应商（api_key 脱敏）。"""
    from clients.classifier_client import list_providers, provider_public
    return {"providers": [provider_public(p) for p in list_providers()]}


class ProviderUpdateBody(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    enabled: Optional[bool] = None
    registry_provider: Optional[str] = None


@router.put("/models/providers/{provider_id}")
def update_model_provider(
    provider_id: str, body: ProviderUpdateBody,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """更新供应商配置——写入 SystemSetting。旧 provider 名自动 canonicalize。"""
    _ALLOWED_PROVIDERS = {"newapi", "local_llama", "local_vision", "local_qwen", "vision_qwen"}
    if provider_id not in _ALLOWED_PROVIDERS:
        raise HTTPException(404, f"unknown provider: {provider_id}")
    from core.settings_service import settings
    from core.route_metadata import canonical_provider_id

    raw_provider_id = provider_id
    provider_id = canonical_provider_id(provider_id)

    prefix = f"model.providers.{provider_id}"
    written = {}
    fields = {"base_url": body.base_url, "api_key": body.api_key}
    for field, value in fields.items():
        if value is None:
            continue
        key = f"{prefix}.{field}"
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            row = SystemSetting(key=key, value=str(value), description=f"provider {provider_id} {field}")
            db.add(row)
        else:
            row.value = str(value)
        written[key] = str(value)
    if body.enabled is not None:
        key = f"{prefix}.enabled"
        val = "1" if body.enabled else "0"
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            row = SystemSetting(key=key, value=val, description=f"provider {provider_id} enabled")
            db.add(row)
        else:
            row.value = val
        written[key] = val
    if body.registry_provider is not None:
        key = f"{prefix}.registry_provider"
        val = str(body.registry_provider).strip()
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            row = SystemSetting(key=key, value=val, description=f"provider {provider_id} registry_provider")
            db.add(row)
        else:
            row.value = val
        written[key] = val
    db.commit()
    _audit(db, "update_provider", "provider", provider_id, _redact(written), ip_address=_client_ip(request))
    settings.invalidate()
    return {
        "ok": True,
        "provider_id": provider_id,
        "input_provider_id": raw_provider_id if raw_provider_id != provider_id else None,
        "version": settings.version,
    }


# ── 模型目录 ──

@router.get("/models/catalog")
def get_model_catalog_v2(provider: str = "", q: str = "", limit: int = 0, offset: int = 0,
                          db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """增强版模型目录：支持 provider/q/limit/offset 过滤。"""
    from clients.classifier_client import build_provider_catalog
    items = build_provider_catalog(db)
    if provider:
        items = [e for e in items if e["provider"] == provider]
    if q:
        ql = q.lower()
        items = [e for e in items if ql in e["model"].lower() or ql in e["provider"]]
    if offset:
        items = items[offset:]
    if limit:
        items = items[:limit]
    return {"catalog": items}


@router.get("/models/route-references")
def get_route_references(_auth=Depends(verify_admin)):
    """路由引用模型——标记是否在 provider_catalog 中确认存在。"""
    from clients.classifier_client import build_route_references
    return {"route_references": build_route_references()}


@router.post("/models/catalog/refresh")
def refresh_model_catalog(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """从各 provider 的 /models 端点刷新模型列表，持久化到 SystemSetting。"""
    import urllib.request as _ur
    from datetime import datetime
    from clients.classifier_client import list_providers, build_provider_catalog

    results = []
    for p in list_providers():
        base_url = p["base_url"].rstrip("/")
        if not base_url:
            continue
        if p.get("enabled") is False:
            results.append({"provider": p["id"], "models": [], "ok": False, "error": "provider disabled"})
            continue
        headers = {"Content-Type": "application/json"}
        if p.get("api_key"):
            headers["Authorization"] = f"Bearer {p['api_key']}"
        try:
            req = _ur.Request(f"{base_url}/models", headers=headers, method="GET")
            proxy_handler = _ur.ProxyHandler({})
            opener = _ur.build_opener(proxy_handler)
            with opener.open(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            items = body.get("data", []) if isinstance(body, dict) else []
            models = sorted(set(m["id"] for m in items if isinstance(m, dict) and m.get("id")))
            key = f"model.catalog.{p['id']}"
            val = json.dumps({
                "models": models, "updated_at": datetime.now().isoformat(),
                "last_refresh_ok": True, "last_error": "",
            }, ensure_ascii=False)
            row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            if not row:
                row = SystemSetting(key=key, value=val, description=f"model catalog for {p['id']}")
                db.add(row)
            else:
                row.value = val
            results.append({"provider": p["id"], "models": models, "ok": True})
        except Exception as e:
            key = f"model.catalog.{p['id']}"
            old_models = []
            old_row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            if old_row:
                try: old_models = json.loads(old_row.value or "{}").get("models", [])
                except Exception: pass
            val = json.dumps({
                "models": old_models, "updated_at": datetime.now().isoformat(),
                "last_refresh_ok": False, "last_error": str(e)[:300],
            }, ensure_ascii=False)
            if not old_row:
                row = SystemSetting(key=key, value=val, description=f"model catalog for {p['id']}")
                db.add(row)
            else:
                old_row.value = val
            results.append({"provider": p["id"], "models": old_models, "ok": False, "error": str(e)[:300]})
    db.commit()
    return {"results": results, "catalog": build_provider_catalog(db)}


_ALLOWED_TIERS = {"fast", "smart", "reasoning", "unknown"}


class ModelCatalogPatch(BaseModel):
    intelligence: int | None = Field(default=None, ge=0, le=15)
    cost_input_1m: float | None = Field(default=None, ge=0)
    cost_output_1m: float | None = Field(default=None, ge=0)
    tier: str | None = None
    enabled: bool | None = None
    tags: list[str] | None = None


@router.patch("/model-catalog/{model_id}")
def patch_model_catalog(
    model_id: str, body: ModelCatalogPatch,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from clients.model_registry import registry

    m = registry.get_model_info(model_id)
    if not m:
        raise HTTPException(404, f"model '{model_id}' not found")
    if body.tier is not None and body.tier not in _ALLOWED_TIERS:
        raise HTTPException(422, f"invalid tier: {body.tier}")

    before = {
        "intelligence": m.get("intelligence"),
        "cost_input_1m": m.get("cost_input_1m"),
        "cost_output_1m": m.get("cost_output_1m"),
        "tier": m.get("tier"),
        "enabled": m.get("enabled", True),
        "tags": list(m.get("tags") or []),
    }
    updates = {}
    if body.intelligence is not None:
        m["intelligence"] = body.intelligence
        updates["intelligence"] = body.intelligence
    if body.cost_input_1m is not None:
        m["cost_input_1m"] = body.cost_input_1m
        updates["cost_input_1m"] = body.cost_input_1m
    if body.cost_output_1m is not None:
        m["cost_output_1m"] = body.cost_output_1m
        updates["cost_output_1m"] = body.cost_output_1m
    if body.tier is not None:
        m["tier"] = body.tier
        updates["tier"] = body.tier
    if body.enabled is not None:
        m["enabled"] = body.enabled
        updates["enabled"] = body.enabled
    if body.tags is not None:
        cleaned = []
        for t in body.tags:
            s = str(t).strip().lower()[:40]
            if s and s not in cleaned:
                cleaned.append(s)
        m["tags"] = cleaned[:20]
        updates["tags"] = cleaned

    registry.add_or_update_model(m)
    _audit_request(db, request, "update_model_catalog", "model", model_id,
                   {"before": before, "after": updates})
    return {"ok": True, "model": model_id, "updates": updates}


# ── Stage Routes ──

_STAGE_META = {
    "main_chat":       {"key": "model.reply",              "field": "model",   "env": "LLM_MODEL_REPLY"},
    "fast_chat":       {"key": "model.fast",               "field": "model",   "env": "LLM_MODEL_FAST"},
    "smart_chat":      {"key": "model.smart",              "field": "model",   "env": "LLM_MODEL_SMART"},
    "timing_gate":     {"key": "model.route.timing_gate",  "field": "api_url", "env": "CLASSIFIER_API_URL"},
    "sticker_describe":{"key": "model.route.sticker_describe","field": "api_url","env": "IMAGE_SUMMARY_API_URL"},
}


def _resolve_route_value(stage: str, db: Session) -> tuple[str, str, str]:
    """Return (value, source, is_overridden). source 准确反映值来源。"""
    from core.settings_service import settings
    from config import (
        LLM_MODEL_REPLY, LLM_MODEL_FAST, LLM_MODEL_SMART,
        CLASSIFIER_API_URL, IMAGE_SUMMARY_API_URL,
    )
    meta = _STAGE_META[stage]
    key = meta["key"]

    # 检查 DB 是否有覆盖
    db_row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if db_row and db_row.value is not None:
        return db_row.value, "db_override", True

    # 没有 DB 覆盖，查 config 值
    env_name = meta["env"]
    val = settings.get(key)
    if val:
        return str(val), env_name, True
    return "", "default", True


@router.get("/model-routes")
def get_model_routes(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    routes = {}
    for stage, meta in _STAGE_META.items():
        val, source, editable = _resolve_route_value(stage, db)
        field = meta["field"]
        entry = {"editable": editable, "source": source, field: val}
        if field == "api_url":
            entry["model"] = ""
        routes[stage] = entry
    return {"routes": routes}


class ModelRoutePatch(BaseModel):
    value: str = Field(default="", max_length=256)


@router.patch("/model-routes/{stage}")
def patch_model_route(
    stage: str, body: ModelRoutePatch,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    if stage not in _STAGE_META:
        raise HTTPException(404, f"unknown stage: {stage}")

    from core.config_registry import SETTING_DEFS
    from core.settings_service import settings
    from clients.model_registry import registry

    meta = _STAGE_META[stage]
    key = meta["key"]
    defn = SETTING_DEFS[key]

    # 模型路由：校验 value 是否存在于 registry
    if meta["field"] == "model" and body.value:
        if not registry.get_model_info(body.value):
            raise HTTPException(404, f"model not found in catalog: {body.value}")

    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not row:
        row = SystemSetting(key=key, value=body.value, description=defn.description)
        db.add(row)
    else:
        row.value = body.value
    db.commit()
    _audit(db, "update_model_route", "route", stage, {"value": body.value},
           ip_address=_client_ip(request))
    settings.invalidate()
    return {"ok": True, "stage": stage, "value": body.value, "version": settings.version}


# ── 模型路由编辑（完整字段）──

# route_key → setting prefix 映射（reply/fast/smart 使用 model.*；classifier 使用 model.route.*）
_ROUTE_SETTING_MAP: dict[str, str] = {
    "reply": "model.reply",
    "fast": "model.fast",
    "smart": "model.smart",
    "session_summary": "model.session_summary",
    "memory_digest": "model.memory_digest",
}
# classifier routes: route_key 直接对应 model.route.<key>
_CLASSIFIER_ROUTE_KEYS = {"timing_gate", "private_decision", "classifier_legacy", "sticker_describe"}
# frontend 友好名称 → 后端 route_key
_ROUTE_ALIAS: dict[str, str] = {
    "vision": "sticker_describe",
}

_CHAT_ROUTES = {"reply", "fast", "smart", "session_summary", "memory_digest"}


def _resolve_route_key(route_key: str) -> tuple[str, str, bool]:
    """解析前端 route_key → (prefix, db_key, is_classifier)。

    返回 (setting_prefix, route_key_for_db, is_classifier_route)。
    """
    route_key = _ROUTE_ALIAS.get(route_key, route_key)
    if route_key in _CHAT_ROUTES:
        return _ROUTE_SETTING_MAP[route_key], route_key, False
    return f"model.route.{route_key}", route_key, True


def _redact(v: dict) -> dict:
    """脱敏：api_key → ***"""
    return {k: ("***" if k.endswith(".api_key") else v) for k, v in v.items()}


class ModelRouteEditBody(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    timeout: Optional[float] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    enable_thinking: Optional[str] = None


@router.put("/models/routes/{route_key}")
def edit_model_route(
    route_key: str, body: ModelRouteEditBody,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    """编辑模型路由配置——写入 SystemSetting 子字段。"""
    from core.config_registry import SETTING_DEFS
    from core.settings_service import settings

    prefix, db_key, is_classifier = _resolve_route_key(route_key)
    if is_classifier:
        if db_key not in _CLASSIFIER_ROUTE_KEYS:
            raise HTTPException(404, f"unknown route: {route_key}")
    elif prefix not in SETTING_DEFS:
        raise HTTPException(404, f"unknown route: {route_key}")

    written = {}
    fields = {
        "provider": body.provider,
        "model": body.model,
        "api_key": body.api_key,
        "timeout": body.timeout,
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "enable_thinking": body.enable_thinking,
    }
    if not is_classifier:
        allowed = {"model", "provider", "timeout", "temperature", "max_tokens", "enable_thinking"}
    else:
        allowed = {"provider", "model", "api_key", "timeout", "temperature", "max_tokens", "enable_thinking"}

    for field, value in fields.items():
        if value is None or field not in allowed:
            continue
        if not is_classifier:
            if field == "model":
                key = prefix
            elif field == "provider":
                key = f"model.route.{db_key}.provider"
            elif field in {"timeout", "temperature", "max_tokens", "enable_thinking"}:
                key = f"model.route.{db_key}.{field}"
            else:
                continue
        else:
            key = f"{prefix}.{field}"
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        defn = SETTING_DEFS.get(key)
        desc = defn.description if defn else f"model route {route_key}.{field}"
        if field == "enable_thinking":
            from core.model_route_options import normalize_enable_thinking
            try:
                stored_value = normalize_enable_thinking(value)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
        elif defn and defn.value_type == "int":
            stored_value = str(int(value))
        elif defn and defn.value_type == "float":
            stored_value = str(float(value))
        else:
            stored_value = str(value)
        if not row:
            row = SystemSetting(key=key, value=stored_value, description=desc)
            db.add(row)
        else:
            row.value = stored_value
        written[key] = stored_value
    db.commit()
    _audit(db, "edit_model_route", "route", route_key, _redact(written), ip_address=_client_ip(request))
    settings.invalidate()
    # 清除 image_summary 30s route cache（invalidate 后清理，避免并发重建旧缓存）
    if db_key == "sticker_describe":
        try:
            from creatures.nanobot.prompts.skills.image_summary.tool import _get_image_summary_route
            if hasattr(_get_image_summary_route, "_cache"):
                delattr(_get_image_summary_route, "_cache")
        except Exception as e:
            logger.warning("[models] clear image_summary route cache failed: %s", e, exc_info=True)
    # 不返回 written，只返回 api_key_configured
    resp: dict = {"ok": True, "route_key": route_key, "version": settings.version}
    api_key_written = any(k.endswith(".api_key") for k in written)
    if api_key_written:
        resp["api_key_configured"] = bool(body.api_key)
    return resp


_TINY_TEST_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


@router.post("/models/routes/{route_key}/test")
async def test_model_route(route_key: str, mode: str = "ping", _auth=Depends(verify_admin)):
    """测试某个模型路由的连通性。"""
    import time, asyncio
    from clients.classifier_client import call_model_route, ensure_model_route_enabled, resolve_model_route
    from core.llm_trace_context import llm_trace_scope

    t0 = time.time()
    route_key = _ROUTE_ALIAS.get(route_key, route_key)
    route = resolve_model_route(route_key)
    try:
        ensure_model_route_enabled(route_key, route)
    except RuntimeError as e:
        return {"ok": False, "route_key": route_key, "error": str(e)[:500]}

    if route_key in _CHAT_ROUTES:
        from clients.new_api_client import NewAPIClient
        from nanobot_kt.bridge import _registry_provider_for_route
        model = route.get("model", "") or route_key
        client = NewAPIClient(
            api_key=route["api_key"],
            base_url=route["base_url"],
            registry_provider=_registry_provider_for_route(route.get("provider_id", "")),
        )
        try:
            with llm_trace_scope(source="admin"):
                result = await client.chat_completion(
                    messages=[{"role": "user", "content": "回复OK"}],
                    manual_model=model, max_tokens=10, temperature=0,
                    enable_thinking=route.get("enable_thinking", "auto"),
                )
            return {
                "ok": True, "route_key": route_key, "model": model,
                "provider": route.get("provider_id", ""),
                "base_url": route.get("base_url", ""),
                "latency_ms": int((time.time() - t0) * 1000),
                "raw_output": str(result)[:300],
            }
        except Exception as e:
            return {"ok": False, "route_key": route_key, "error": str(e)[:500]}
    elif route_key == "sticker_describe":
        # vision route: ping 为文本连通性；vision 会真实发送 OpenAI-compatible 多模态 payload。
        try:
            if mode == "vision":
                messages = [
                    {"role": "system", "content": "你是视觉连通性测试模型。只回复 ok。"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "请确认你收到了这张 1x1 测试图片，只回复 ok。"},
                            {"type": "image_url", "image_url": {"url": _TINY_TEST_PNG}},
                        ],
                    },
                ]
                with llm_trace_scope(source="admin"):
                    raw = await asyncio.to_thread(
                        call_model_route,
                        route_key=route_key,
                        messages=messages,
                        max_tokens=20,
                        temperature=0,
                    )
                vision_payload_ok = True
                note = "真实视觉 payload 连通性测试"
            else:
                with llm_trace_scope(source="admin"):
                    raw = await asyncio.to_thread(
                        call_model_route,
                        route_key=route_key,
                        user_message="测试连通性",
                        system_prompt="你是一个视觉描述模型。收到图片时输出JSON描述。此消息仅测试连通性，回复 ok。",
                        max_tokens=20,
                    )
                vision_payload_ok = False
                note = "仅文本连通性测试，非完整视觉描述测试"
            return {
                "ok": True, "route_key": route_key,
                "provider": route.get("provider_id", ""),
                "base_url": route.get("base_url", ""),
                "model": route.get("model", ""),
                "latency_ms": int((time.time() - t0) * 1000),
                "raw_output": raw[:200],
                "vision_payload_ok": vision_payload_ok,
                "note": note,
            }
        except Exception as e:
            return {"ok": False, "route_key": route_key, "error": str(e)[:500]}
    else:
        try:
            with llm_trace_scope(source="admin"):
                raw = await asyncio.to_thread(
                    call_model_route,
                    route_key=route_key,
                    user_message="判断是否需要bot回复",
                    system_prompt="群聊节奏判断——是否需要bot回复。输出JSON",
                    max_tokens=60,
                )
            return {
                "ok": True, "route_key": route_key,
                "provider": route.get("provider_id", ""),
                "base_url": route.get("base_url", ""),
                "model": route.get("model", ""),
                "latency_ms": int((time.time() - t0) * 1000),
                "raw_output": raw[:500],
            }
        except Exception as e:
            return {"ok": False, "route_key": route_key, "error": str(e)[:500]}


@router.get("/models/routes/{route_key}/resolved")
def get_resolved_route(route_key: str, _auth=Depends(verify_admin)):
    """路由诊断——返回 resolve_model_route() 的脱敏完整结果。

    用于排查"页面显示 newapi，实际走不走 newapi"一类问题。
    """
    from clients.classifier_client import resolve_model_route
    from nanobot_kt.bridge import _registry_provider_for_route

    route = resolve_model_route(route_key)
    return {
        "route_key": route_key,
        "provider_id": route.get("provider_id", ""),
        "registry_provider": _registry_provider_for_route(route.get("provider_id", "")),
        "base_url": route.get("base_url", ""),
        "model": route.get("model", ""),
        "api_key_configured": bool(route.get("api_key")),
        "api_key_source": route.get("api_key_source", ""),
        "timeout": route.get("timeout", 15),
        "temperature": route.get("temperature", 0),
        "max_tokens": route.get("max_tokens", 30),
        "enable_thinking": route.get("enable_thinking", "auto"),
        "source": route.get("source", ""),
        "provider_enabled": route.get("provider_enabled", True),
        "inherited_from": route.get("inherited_from", None),
        "overridden_fields": route.get("overridden_fields", None),
    }


@router.get("/models/available")
def list_available_models(route_key: str = "", base_url_override: str = "",
                          db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """获取某个 route 的可选模型列表——从 provider /models 端点拉取。"""
    import urllib.request as _ur
    import urllib.error as _ure

    route_key = _ROUTE_ALIAS.get(route_key, route_key)

    from clients.classifier_client import resolve_model_route, _resolve_classifier_route
    effective_route_key = route_key or "timing_gate"
    if effective_route_key in _CHAT_ROUTES or effective_route_key in _CLASSIFIER_ROUTE_KEYS:
        route = resolve_model_route(effective_route_key)
    else:
        route = _resolve_classifier_route(effective_route_key)

    provider_id = str(route.get("provider_id", "") or "")
    if provider_id and route.get("provider_enabled") is False:
        return {"models": [], "error": f"provider disabled: {provider_id}", "source": "provider_disabled"}

    route_base_url = str(route.get("base_url", "")).rstrip("/")
    api_key = str(route.get("api_key", "") or "")
    if base_url_override:
        base_url = base_url_override.rstrip("/")
        if base_url != route_base_url:
            api_key = ""
    else:
        base_url = route_base_url

    if not base_url:
        return {"models": [], "error": "no base_url configured", "source": "none"}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = _ur.Request(f"{base_url}/models", headers=headers, method="GET")
        proxy_handler = _ur.ProxyHandler({})
        opener = _ur.build_opener(proxy_handler)
        with opener.open(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        items = body.get("data", []) if isinstance(body, dict) else []
        ids = [m["id"] for m in items if isinstance(m, dict) and m.get("id")]
        return {"models": sorted(ids)[:100], "source": f"{base_url}/models"}
    except _ure.HTTPError as e:
        b = e.read().decode("utf-8", errors="ignore")[:300] if e.fp else ""
        return {"models": [], "error": f"HTTP {e.code}: {b}", "source": base_url}
    except Exception as e:
        return {"models": [], "error": str(e)[:300], "source": base_url}


# ── 本地语义组件测试/预热 ──

def _test_nli_contradiction(a: str, b: str) -> dict:
    """模块级 NLI 矛盾检测——避免调用实例方法。"""
    from core.persona_preprocess import _get_nli
    nli = _get_nli()
    if nli is None:
        return {"available": False, "fallback": "cosine", "label": "nli_unavailable"}
    result = nli(f"{a} </s></s> {b}")
    if isinstance(result, list) and result:
        r = result[0]
        return {
            "available": True, "label": r.get("label", ""),
            "score": round(float(r.get("score", 0)), 4),
            "contradiction": r.get("label") == "CONTRADICTION",
        }
    return {"available": True, "raw": str(result)[:200]}


@router.post("/models/local/{component}/test")
async def test_local_component(component: str, _auth=Depends(verify_admin)):
    """测试本地语义组件。component: persona_embed | nli | rag_reranker"""
    import time
    t0 = time.time()
    if component == "persona_embed":
        try:
            from core.persona_preprocess import embed_text, _EMBEDDER_MODEL
            vec = embed_text("测试文本——用于验证embedding组件")
            return {
                "ok": True, "component": component, "model": str(_EMBEDDER_MODEL),
                "load_state": "loaded", "dim": len(vec),
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {
                "ok": False, "component": component,
                "load_state": "failed", "error": str(e)[:500],
                "hint": "检查 HuggingFace 连接、磁盘缓存、sentence-transformers 安装",
            }
    elif component == "nli":
        try:
            from core.persona_preprocess import _NLI_MODEL
            result = _test_nli_contradiction("我喜欢苹果", "我不喜欢苹果")
            available = result.get("available", False)
            return {
                "ok": True, "component": component, "model": str(_NLI_MODEL),
                "load_state": "loaded" if available else "fallback",
                "fallback": "cosine" if not available else None,
                "result": result,
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {
                "ok": False, "component": component,
                "load_state": "failed", "error": str(e)[:500],
                "hint": "检查 HuggingFace 连接、磁盘缓存、transformers 安装",
            }
    elif component == "rag_reranker":
        try:
            from core.semantic.provider_factory import get_reranker_provider
            from core.semantic.reranker import SemanticCandidate

            provider = get_reranker_provider()
            if provider is None:
                return {
                    "ok": False,
                    "component": component,
                    "load_state": "unavailable",
                    "error": "本地 reranker 模型未配置或模型目录不存在",
                    "hint": "默认会自动下载 BAAI/bge-reranker-v2-m3 到 ./models/bge-reranker-v2-m3",
                }
            results = provider.rerank(
                "端口冲突怎么解决",
                [
                    SemanticCandidate(
                        candidate_id="test:1",
                        source_type="debug",
                        title="端口冲突",
                        text="8000 端口被占用时，使用 lsof 或 netstat 找到占用进程。",
                    ),
                    SemanticCandidate(
                        candidate_id="test:2",
                        source_type="debug",
                        title="无关内容",
                        text="今天的天气很好。",
                    ),
                ],
                top_k=2,
            )
            best = results[0] if results else None
            return {
                "ok": True,
                "component": component,
                "model": getattr(provider, "model_name", ""),
                "load_state": "loaded",
                "best_candidate_id": best.candidate_id if best else "",
                "best_score": best.score if best else None,
                "raw_score": best.raw_score if best else None,
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {
                "ok": False,
                "component": component,
                "load_state": "failed",
                "error": str(e)[:500],
                "hint": "检查本地 reranker 模型目录和 sentence-transformers 安装",
            }
    else:
        raise HTTPException(404, f"unknown component: {component}")


@router.post("/models/local/{component}/warmup")
async def warmup_local_component(component: str, _auth=Depends(verify_admin)):
    """预热本地语义组件——触发懒加载。"""
    import time
    t0 = time.time()
    if component == "persona_embed":
        try:
            from core.persona_preprocess import embed_text, _EMBEDDER_MODEL
            vec = embed_text("预热文本")
            return {
                "ok": True, "component": component, "model": str(_EMBEDDER_MODEL),
                "load_state": "loaded", "dim": len(vec),
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {"ok": False, "component": component, "error": str(e)[:500]}
    elif component == "nli":
        try:
            from core.persona_preprocess import _NLI_MODEL
            result = _test_nli_contradiction("预热", "预热")
            available = result.get("available", False)
            return {
                "ok": True, "component": component, "model": str(_NLI_MODEL),
                "load_state": "loaded" if available else "fallback",
                "fallback": "cosine" if not available else None,
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {"ok": False, "component": component, "error": str(e)[:500]}
    elif component == "rag_reranker":
        try:
            return await test_local_component(component, _auth)
        except Exception as e:
            return {"ok": False, "component": component, "error": str(e)[:500]}
    else:
        raise HTTPException(404, f"unknown component: {component}")


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


class TimingGateStabilityRequest(BaseModel):
    cases: list[dict] = Field(default_factory=list, max_length=10)
    runs: int = Field(default=20, ge=1, le=20)


@router.post("/models/timing-gate-stability-test")
async def timing_gate_stability_test(body: TimingGateStabilityRequest, _auth=Depends(verify_admin)):
    """TimingGate JSON 稳定性测试——连续跑 N 次，统计 parse_error 和延迟分布。

    不会写入真实 TimingGate 记录。
    """
    import time
    from clients.classifier_client import get_timing_gate

    gate = get_timing_gate()
    all_results: list[dict] = []
    default_cases = [
        {"name": "普通玩梗", "context": "<recent>\n[用户A]: 笑死我了\n[用户B]: 哈哈哈哈\n</recent>", "pending_count": 2},
        {"name": "技术求助", "context": "<recent>\n[用户C]: 问一下这个报错怎么修\n[用户D]: 贴代码看看\n</recent>", "pending_count": 1},
        {"name": "连续发材料", "context": "<recent>\n[用户G]: 等下我发日志\n[用户G]: 还有一张图\n</recent>", "pending_count": 2},
        {"name": "直接叫bot", "context": "<recent>\n[用户E]: @bot 你在吗\n</recent>", "pending_count": 1},
        {"name": "群命令", "context": "<recent>\n[用户F]: /status\n</recent>", "pending_count": 0},
    ]
    cases = body.cases if body.cases else default_cases

    for case in cases:
        name = case.get("name", "unknown")
        base_context = str(case.get("context", ""))
        pending = int(case.get("pending_count", 0))
        runs_list: list[dict] = []
        parse_errors = 0
        latencies: list[float] = []
        actions: dict[str, int] = {}
        errors: dict[str, int] = {}
        raw_samples: list[str] = []

        # 构造接近真实 TimingGate 输入的 context
        timing_context = (
            f"<timing_context>\n"
            f"pending_count: {pending}\n"
            f"talk_value: {case.get('talk_value', 0.5)}\n"
            f"msg_1m: {case.get('msg_1m', 0)}\n"
            f"msg_5m: {case.get('msg_5m', 0)}\n"
            f"{base_context}\n"
            f"</timing_context>"
        )

        for i in range(body.runs):
            t0 = time.time()
            result = gate.judge(timing_context)
            lat = time.time() - t0
            latencies.append(lat)

            action = result.get("action", "no_reply")
            error_type = result.get("error_type")
            if error_type == "parse_error":
                parse_errors += 1
            actions[action] = actions.get(action, 0) + 1
            errors[error_type or "none"] = errors.get(error_type or "none", 0) + 1
            runs_list.append({
                "index": i,
                "action": action,
                "reason": result.get("reason", ""),
                "delay": result.get("delay_seconds"),
                "error_type": error_type,
                "latency_ms": int(lat * 1000),
            })

            if i < 3:
                raw_samples.append((result.get("raw") or "")[:300])

        n = body.runs
        all_results.append({
            "name": name,
            "run_count": n,
            "parse_error_count": parse_errors,
            "parse_error_ratio": round(parse_errors / n, 3),
            "avg_latency_ms": int(sum(latencies) / len(latencies) * 1000),
            "action_dist": actions,
            "error_dist": errors,
            "runs": runs_list,
            "raw_samples": raw_samples,
        })

    total_errors = sum(r["parse_error_count"] for r in all_results)
    total_runs = len(cases) * body.runs
    return {
        "dry_run": True,
        "cases": all_results,
        "overall_parse_error_count": total_errors,
        "overall_parse_error_ratio": round(total_errors / total_runs, 3) if total_runs else 0,
    }


# ═══════════════════════════════════════════
# Audit logs + DB backup
# ═══════════════════════════════════════════

@router.get("/audit-logs")
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
# Log viewer
# ═══════════════════════════════════════════

@router.get("/logs")
def list_log_files(_auth=Depends(verify_admin)):
    import os as _os, glob
    base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    log_dir = _os.path.join(base, "data")
    files = []
    patterns = ["*.log", "*.log.*", "nanobot.log*"]
    for pat in patterns:
        for p in glob.glob(_os.path.join(log_dir, pat)):
            fname = _os.path.basename(p)
            if fname not in [f["name"] for f in files]:
                size = _os.path.getsize(p)
                files.append({"name": fname, "size": size, "mtime": _os.path.getmtime(p)})
    files.sort(key=lambda x: -x["mtime"])
    return {"files": files}


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
    from collections import deque

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.abspath(os.path.join(base, "data"))
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


# ── 工具管理 ──

class ToolUpdateBody(BaseModel):
    private_default: Optional[bool] = None
    private_superuser_default: Optional[bool] = None
    group_default: Optional[bool] = None
    lightweight_default: Optional[bool] = None


class ToolOverrideBody(BaseModel):
    scope_type: str  # "group" | "user" | "chat_type"
    scope_id: str
    enabled: bool
    reason: str = ""


class ToolSchemaOverrideBody(BaseModel):
    tool_schema: dict = Field(default_factory=dict, alias="schema")


_TEMP_TOOL_TARGET_EXACT = {
    "admin", "default", "default_session", "local_test", "test",
    "test_session", "test-user", "unknown",
}
_TEMP_TOOL_TARGET_PREFIXES = (
    "fake", "local_", "mock", "pytest", "temp", "tmp", "test",
)


def _is_temp_tool_target_id(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return True
    lowered = raw.lower()
    if lowered.startswith("private_"):
        return True
    if lowered in _TEMP_TOOL_TARGET_EXACT:
        return True
    if lowered.endswith("_test") or "local_test" in lowered:
        return True
    return any(lowered.startswith(prefix) for prefix in _TEMP_TOOL_TARGET_PREFIXES)


def _tool_target_label(name: str, target_id: str, fallback: str) -> str:
    clean_name = str(name or "").strip()
    clean_id = str(target_id or "").strip()
    if clean_name:
        return f"{clean_name} ({clean_id})" if clean_id else clean_name
    return fallback or clean_id


@router.get("/tools")
async def list_tools(chat_type: str = "group", group_id: str = "",
                      user_id: str = "", runtime_preset: str = "full",
                      db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """列出所有工具配置状态，并可预览指定运行时预设下的可用性。"""
    # registry probe: 无 child bridge 时自动创建一个用于探测
    try:
        from server import app as _app
        pool = getattr(_app.state, 'bridge', None)
        if pool and hasattr(pool, 'ensure_registry_probe'):
            await pool.ensure_registry_probe()
    except Exception as e:
        logger.warning("[Tools] registry probe failed: %s", e, exc_info=True)

    from core.tool_registry import TOOL_METADATA
    from core.runtime_tool_service import (
        normalize_tool_chat_type,
        resolve_effective_tools,
        resolve_lightweight_default,
        resolve_tool_default,
        normalize_runtime_preset,
    )

    runtime_preset = normalize_runtime_preset(runtime_preset)
    chat_type = normalize_tool_chat_type(chat_type)
    configured_enabled, configured_disabled = resolve_effective_tools(
        chat_type=chat_type, group_id=group_id, user_id=user_id,
        runtime_preset="full", db=db,
    )
    runtime_enabled, runtime_disabled = resolve_effective_tools(
        chat_type=chat_type, group_id=group_id, user_id=user_id,
        runtime_preset=runtime_preset, db=db,
    )
    # 从 bridge 获取 KT registry 实际加载的工具列表
    registry_info: dict = {}
    kt_loaded: set[str] = set()
    try:
        from server import app
        bridge = getattr(app.state, 'bridge', None)
        info = getattr(bridge, '_tool_registry_info', {}) if bridge else {}
        if info:
            registry_info = {
                "kt_loaded": info.get("kt_loaded", []),
                "missing_meta": info.get("missing_meta", []),
                "missing_kt": info.get("missing_kt", []),
            }
            kt_loaded = set(info.get("kt_loaded", []))
    except Exception:
        registry_info = {}
        kt_loaded = set()

    registry_available = bool(registry_info)
    override_scope_type = ""
    override_scope_id = ""
    if user_id:
        override_scope_type = "user"
        override_scope_id = str(user_id).strip()
    elif group_id:
        override_scope_type = "group"
        override_scope_id = str(group_id).strip()
    override_state: dict[str, bool] = {}
    if override_scope_type and override_scope_id:
        try:
            from core.database import ToolOverride
            rows = db.query(ToolOverride).filter(
                ToolOverride.scope_type == override_scope_type,
                ToolOverride.scope_id == override_scope_id,
            ).all()
            override_state = {row.tool_name: bool(row.enabled) for row in rows}
        except Exception as e:
            logger.warning("[Tools] failed to load override state: %s", e)
            override_state = {}

    items = []
    for name, td in sorted(TOOL_METADATA.items(), key=lambda x: x[1].label):
        registered = name in kt_loaded if kt_loaded else None
        is_subagent = name in ("memory_read", "memory_write")
        items.append({
            "name": td.name, "label": td.label, "category": td.category,
            "risk_level": td.risk_level,
            "private_default": resolve_tool_default(name, "private", db=db),
            "private_superuser_default": resolve_tool_default(name, "private_superuser", db=db),
            "group_default": resolve_tool_default(name, "group", db=db),
            "lightweight_default": resolve_lightweight_default(name, db=db),
            "force_enabled": td.force_enabled,
            "force_disabled_group": td.force_disabled_group,
            "description": td.description,
            "configured_enabled": configured_enabled.get(name, False),
            "configured_disabled_reason": configured_disabled.get(name, ""),
            "runtime_effective": runtime_enabled.get(name, False),
            "runtime_disabled_reason": runtime_disabled.get(name, ""),
            "override_present": name in override_state,
            "override_enabled": override_state.get(name) if name in override_state else None,
            # 兼容旧前端字段：工具管理页的 effective 表示配置启用状态，不混入 lightweight/none 策略。
            "effective": configured_enabled.get(name, False),
            "disabled_reason": configured_disabled.get(name, ""),
            "registered": registered,
            "is_subagent": is_subagent,
        })
    bridge_count = 0
    try:
        bridge_count = getattr(app.state.bridge, 'bridge_count', 0)
    except Exception:
        pass

    return {"tools": items, "registry_info": registry_info,
            "registry_available": registry_available,
            "registry_empty": bool(registry_available and len(kt_loaded) == 0),
            "bridge_count": bridge_count,
            "runtime_preset": runtime_preset}


@router.get("/tools/targets")
def list_tool_targets(scope_type: str = "group", search: str = "", limit: int = 50,
                      db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """列出工具覆盖可选择的真实群聊/私聊目标。"""
    scope = "user" if scope_type == "user" else "group"
    search_text = str(search or "").strip().lower()
    max_items = max(1, min(int(limit or 50), 100))
    candidates: dict[str, dict] = {}

    def add_candidate(target_id: str, name: str = "", source: str = "",
                      recent_at=None) -> None:
        raw_id = _raw_group_id(target_id) if scope == "group" else str(target_id or "").strip()
        if _is_temp_tool_target_id(raw_id):
            return
        if scope == "group" and not raw_id:
            return
        if scope == "user" and (raw_id.startswith("group_") or raw_id.startswith("qq:")):
            return
        label = _tool_target_label(
            name,
            raw_id,
            f"群聊 {raw_id}" if scope == "group" else f"用户 {raw_id}",
        )
        haystack = f"{raw_id} {name} {label}".lower()
        if search_text and search_text not in haystack:
            return
        clean_name = str(name or "").strip()
        old = candidates.get(raw_id)
        if old:
            sources = set(old.get("_sources") or [])
            if source:
                sources.add(source)
            old["_sources"] = sorted(sources)
            old["source"] = "+".join(old["_sources"])
            if clean_name and not old.get("name"):
                old["name"] = clean_name
                old["label"] = _tool_target_label(
                    clean_name,
                    raw_id,
                    f"群聊 {raw_id}" if scope == "group" else f"用户 {raw_id}",
                )
            if recent_at and (not old.get("_recent_at") or recent_at > old["_recent_at"]):
                old["_recent_at"] = recent_at
                old["recent_at"] = _iso(recent_at)
            return
        candidates[raw_id] = {
            "id": raw_id,
            "label": label,
            "name": clean_name,
            "scope_type": scope,
            "source": source,
            "recent_at": _iso(recent_at),
            "_recent_at": recent_at,
            "_sources": [source] if source else [],
        }

    if scope == "group":
        for user in db.query(User).filter(User.id.like("group_%")).all():
            add_candidate(user.id, user.name or "", "users", None)
        for cfg in db.query(ChatStreamConfig).all():
            add_candidate(cfg.chat_stream_id, "", "chat_stream_config", None)
        for sid, info in _runtime_snapshot().items():
            add_candidate(sid, str(info.get("session_name") or ""), "runtime", None)
        for row in db.query(ChatLog).filter(
            ChatLog.session_id.like("group_%")
        ).order_by(ChatLog.id.desc()).limit(5000).all():
            add_candidate(row.session_id or "", row.session_name or "", "chat_logs", row.created_at)
    else:
        for user in db.query(User).all():
            uid = str(user.id or "").strip()
            if uid and not uid.startswith("group_"):
                add_candidate(uid, user.name or "", "users", None)
        for row in db.query(ChatLog).filter(
            ~ChatLog.session_id.like("group_%")
        ).order_by(ChatLog.id.desc()).limit(5000).all():
            uid = str(row.user_id or "").strip()
            sid = str(row.session_id or "").strip()
            if not uid or uid.startswith("group_"):
                continue
            if sid and not (sid == uid or sid.startswith("private_")):
                continue
            add_candidate(uid, row.sender_name or "", "chat_logs", row.created_at)
        for row in db.query(ConversationTurn).filter(
            ~ConversationTurn.session_id.like("group_%")
        ).order_by(ConversationTurn.id.desc()).limit(5000).all():
            uid = str(row.user_id or "").strip()
            sid = str(row.session_id or "").strip()
            if not uid or uid.startswith("group_"):
                continue
            if sid and not (sid == uid or sid.startswith("private_")):
                continue
            add_candidate(uid, "", "conversation_turns", row.created_at)

    for item in candidates.values():
        item.pop("_recent_at", None)
        item.pop("_sources", None)

    items = sorted(
        candidates.values(),
        key=lambda item: (item.get("recent_at") or "", item["label"]),
        reverse=True,
    )[:max_items]
    return {"scope_type": scope, "items": items}


@router.get("/tools/{tool_name}/schema")
def get_tool_schema_override(tool_name: str, db: Session = Depends(get_db),
                             _auth=Depends(verify_admin)):
    from core.tool_schema_preview import build_tool_schema_config

    try:
        return build_tool_schema_config(db, tool_name)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/tools/{tool_name}/schema")
def save_tool_schema_override_api(tool_name: str, body: ToolSchemaOverrideBody,
                                  request: Request, db: Session = Depends(get_db),
                                  _auth=Depends(verify_admin)):
    from core.tool_schema_preview import build_tool_schema_config, save_tool_schema_override

    try:
        save_tool_schema_override(db, tool_name, body.tool_schema)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    result = build_tool_schema_config(db, tool_name)
    _audit(db, "tool_schema_override", "tool", tool_name, {"schema": result["editable_schema"]},
           ip_address=_client_ip(request))
    return result


@router.delete("/tools/{tool_name}/schema")
def delete_tool_schema_override_api(tool_name: str, request: Request,
                                    db: Session = Depends(get_db),
                                    _auth=Depends(verify_admin)):
    from core.tool_schema_preview import build_tool_schema_config, delete_tool_schema_override

    try:
        deleted = delete_tool_schema_override(db, tool_name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if deleted:
        db.commit()
        _audit(db, "tool_schema_override_delete", "tool", tool_name,
               ip_address=_client_ip(request))
    return build_tool_schema_config(db, tool_name)


@router.put("/tools/{tool_name}")
def update_tool_defaults(tool_name: str, body: ToolUpdateBody,
                          request: Request, db: Session = Depends(get_db),
                          _auth=Depends(verify_admin)):
    """更新工具默认值——写入 SystemSetting。"""
    from core.tool_registry import get_tool_def
    from core.settings_service import settings

    td = get_tool_def(tool_name)
    if not td:
        raise HTTPException(404, f"unknown tool: {tool_name}")

    prefix = f"tool.defaults.{tool_name}"
    updates = {}
    for field, val in [("private_default", body.private_default),
                       ("private_superuser_default", body.private_superuser_default),
                       ("group_default", body.group_default)]:
        if val is None:
            continue
        updates[field] = bool(val)
        key = f"{prefix}.{field}"
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not row:
            row = SystemSetting(key=key, value="1" if val else "0", description=f"{tool_name} {field}")
            db.add(row)
        else:
            row.value = "1" if val else "0"
    if body.lightweight_default is not None and not td.force_enabled:
        key = "tool.lightweight_set"
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        try:
            raw_lightweight_set = row.value if row and row.value else ""
            if raw_lightweight_set:
                parsed_lightweight_set = json.loads(raw_lightweight_set)
                if not isinstance(parsed_lightweight_set, list):
                    raise TypeError("tool.lightweight_set must be a list")
                lightweight_set = {str(x) for x in parsed_lightweight_set if str(x).strip()}
            else:
                from core.runtime_tool_service import _DEFAULT_LIGHTWEIGHT_SET
                lightweight_set = set(_DEFAULT_LIGHTWEIGHT_SET)
        except (json.JSONDecodeError, TypeError):
            from core.runtime_tool_service import _DEFAULT_LIGHTWEIGHT_SET
            lightweight_set = set(_DEFAULT_LIGHTWEIGHT_SET)
        if body.lightweight_default:
            lightweight_set.add(tool_name)
        else:
            lightweight_set.discard(tool_name)
        value = json.dumps(sorted(lightweight_set), ensure_ascii=False)
        if not row:
            row = SystemSetting(key=key, value=value, description="自动降档轻量工具预设")
            db.add(row)
        else:
            row.value = value
        updates["lightweight_default"] = bool(body.lightweight_default)
    db.commit()
    settings.invalidate()
    if updates:
        _audit(db, "tool_default_update", "tool", tool_name, updates,
               ip_address=_client_ip(request))
    return {"ok": True, "tool": tool_name}


@router.put("/tools/{tool_name}/override")
def set_tool_override(tool_name: str, body: ToolOverrideBody,
                      request: Request, db: Session = Depends(get_db),
                      _auth=Depends(verify_admin)):
    """设置工具作用域覆盖——upsert ToolOverride。"""
    from core.tool_registry import get_tool_def
    from core.database import ToolOverride

    if body.scope_type not in {"group", "user", "chat_type"}:
        raise HTTPException(400, "scope_type must be group/user/chat_type")
    if body.scope_type == "chat_type" and body.scope_id not in {"private", "private_superuser", "group"}:
        raise HTTPException(400, "chat_type scope_id must be private/private_superuser/group")
    if body.scope_type in {"group", "user"} and not body.scope_id.strip():
        raise HTTPException(400, "scope_id required for group/user scope")

    td = get_tool_def(tool_name)
    if not td:
        raise HTTPException(404, f"unknown tool: {tool_name}")
    if td.force_enabled:
        raise HTTPException(400, f"{tool_name} is force_enabled, cannot override")

    row = db.query(ToolOverride).filter(
        ToolOverride.tool_name == tool_name,
        ToolOverride.scope_type == body.scope_type,
        ToolOverride.scope_id == body.scope_id,
    ).first()
    if not row:
        row = ToolOverride(tool_name=tool_name, scope_type=body.scope_type,
                           scope_id=body.scope_id)
        db.add(row)
    row.enabled = 1 if body.enabled else 0
    row.reason = body.reason
    db.commit()
    _audit(db, "tool_override", "tool", tool_name,
           {"scope_type": body.scope_type, "scope_id": body.scope_id,
            "enabled": row.enabled, "reason": body.reason},
           ip_address=_client_ip(request))
    return {"ok": True, "tool": tool_name}


@router.delete("/tools/{tool_name}/override")
def delete_tool_override(tool_name: str, request: Request, scope_type: str = "",
                         scope_id: str = "", db: Session = Depends(get_db),
                         _auth=Depends(verify_admin)):
    """删除工具作用域覆盖。"""
    from core.database import ToolOverride
    row = db.query(ToolOverride).filter(
        ToolOverride.tool_name == tool_name,
        ToolOverride.scope_type == scope_type,
        ToolOverride.scope_id == scope_id,
    ).first()
    if not row:
        raise HTTPException(404, "Override not found")
    db.delete(row)
    db.commit()
    _audit(db, "tool_override_delete", "tool", tool_name,
           {"scope_type": scope_type, "scope_id": scope_id},
           ip_address=_client_ip(request))
    return {"ok": True}


@router.get("/tools/effective")
def get_effective_tools(chat_type: str = "group", group_id: str = "", user_id: str = "",
                         runtime_preset: str = "full",
                         db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """查看给定上下文的实际生效工具列表。"""
    from core.runtime_tool_service import (
        build_runtime_tool_prompt,
        normalize_runtime_preset,
        resolve_effective_tools,
    )
    from core.tool_schema_preview import build_effective_tool_schemas
    runtime_preset = normalize_runtime_preset(runtime_preset)
    enabled, disabled = resolve_effective_tools(
        chat_type=chat_type, group_id=group_id, user_id=user_id,
        runtime_preset=runtime_preset, db=db,
    )
    prompt = build_runtime_tool_prompt(enabled, disabled, chat_type)
    tool_schemas = build_effective_tool_schemas(enabled, db=db)
    return {
        "chat_type": chat_type, "group_id": group_id, "user_id": user_id,
        "runtime_preset": runtime_preset,
        "enabled": {k: v for k, v in enabled.items() if v},
        "disabled": {k: v for k, v in disabled.items()},
        "prompt": prompt,
        "tool_schemas": tool_schemas,
    }


@router.get("/tools/decisions")
def list_runtime_preset_decisions(session_id: str = "", limit: int = 50,
                               db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """查询每轮运行时工具预设决策记录。"""
    from core.database import RuntimeToolDecision
    import json as _json
    q = db.query(RuntimeToolDecision).order_by(RuntimeToolDecision.id.desc())
    if session_id:
        q = q.filter(RuntimeToolDecision.session_id == session_id)
    rows = q.limit(min(limit, 200)).all()
    items = []
    for r in rows:
        try:
            reasons = _json.loads(r.disabled_reasons_json or "{}")
        except Exception:
            reasons = {}
        items.append({
            "id": r.id, "session_id": r.session_id, "message_id": r.message_id,
            "chat_type": r.chat_type, "group_id": r.group_id, "user_id": r.user_id,
            "runtime_preset": r.runtime_preset,
            "effective_tools": _json.loads(r.effective_tools_json or "[]") if isinstance(r.effective_tools_json or "", str) else [],
            "disabled_reasons": reasons,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })
    return {"items": items, "total": len(items)}


# ── Model Health Check ──

@router.post("/models/health-check")
async def model_health_check(_auth=Depends(verify_admin)):
    """连通性探测：对每个配置的 API 端点做 GET /models，返回可达性、可用性、延迟。"""
    import time
    import aiohttp
    from core.settings_service import settings
    from config import (
        NEW_API_BASE_URL, NEW_API_KEY,
        CLASSIFIER_API_URL, IMAGE_SUMMARY_API_URL,
    )

    new_api_base = str(settings.get("new_api.base_url") or NEW_API_BASE_URL or "")
    new_api_key = str(NEW_API_KEY or "")
    classifier_url = str(settings.get("model.route.timing_gate") or CLASSIFIER_API_URL or "")
    image_url = str(settings.get("model.route.sticker_describe") or IMAGE_SUMMARY_API_URL or "")

    # (name, url, auth_header_or_none)
    targets: list[tuple[str, str, dict | None]] = [
        ("new_api", new_api_base, {"Authorization": f"Bearer {new_api_key}"} if new_api_key else None),
        ("classifier", classifier_url, None),
        ("image_summary", image_url, None),
    ]

    results = {}
    async with aiohttp.ClientSession() as session:
        for name, url, headers in targets:
            if not url:
                results[name] = {"reachable": False, "usable": False,
                                 "latency_ms": 0, "error": "not configured"}
                continue
            start = time.monotonic()
            try:
                async with session.get(
                    f"{url.rstrip('/')}/models",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    latency = (time.monotonic() - start) * 1000
                    ok = 200 <= resp.status < 300
                    results[name] = {
                        "reachable": True,
                        "usable": ok,
                        "latency_ms": round(latency, 1),
                        "status": resp.status,
                        "auth_error": resp.status in (401, 403),
                        "url": url,
                    }
            except TimeoutError:
                latency = (time.monotonic() - start) * 1000
                results[name] = {"reachable": False, "usable": False,
                                 "latency_ms": round(latency, 1),
                                 "error": "timeout (10s)", "url": url}
            except Exception as e:
                latency = (time.monotonic() - start) * 1000
                results[name] = {"reachable": False, "usable": False,
                                 "latency_ms": round(latency, 1),
                                 "error": str(e)[:200], "url": url}

    return {"endpoints": results}


# ═══════════════════════════════════════════
# Reply 手动测试 / A-B 评估
# ═══════════════════════════════════════════

class ReplyTestRunRequest(BaseModel):
    chat_type: Literal["group", "private"] = "group"
    session_id: str = "reply-test"
    sender_id: str = "admin"
    sender_name: str = "admin"
    character_name: str = ""
    message: str
    recent_context: str = ""
    persona_text: str = ""
    prompt_engine: Literal["v1", "v2"] = "v1"
    variant: Literal[
        "baseline",
        "prompt_only",
        "code_retry",
        "v1_baseline",
        "v2_prompt_only",
        "v2_code_retry",
    ] = "code_retry"
    enable_reply_contract_retry: bool = True
    dry_run: bool = True


def _loads_json_list(raw: str) -> list:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _reply_case_to_dict(row) -> dict:
    data = row_to_dict(row)
    data["context"] = json.loads(row.context_json or "{}")
    data["expected_keywords"] = _loads_json_list(row.expected_keywords_json)
    data["forbidden_keywords"] = _loads_json_list(row.forbidden_keywords_json)
    data["tags"] = _loads_json_list(row.tags_json)
    return data


def _reply_eval_run_to_dict(row) -> dict:
    data = row_to_dict(row)
    try:
        data["metrics"] = json.loads(row.summary_json or "{}")
    except Exception:
        data["metrics"] = {}
    return data


def _reply_log_attempt(log) -> dict:
    if not log:
        return {
            "raw_output": "",
            "called_reply": False,
            "called_no_reply": False,
            "structured_fallback": False,
            "reply_tool_call_count": 0,
            "no_reply_tool_call_count": 0,
            "structured_fallback_count": 0,
            "total_final_action_count": 0,
            "result": "",
        }
    return {
        "raw_output": log.raw_output_preview or "",
        "called_reply": bool(log.has_reply_tool),
        "called_no_reply": bool(log.has_no_reply_tool),
        "structured_fallback": bool(log.has_structured_fallback),
        "reply_tool_call_count": int(getattr(log, "reply_tool_call_count", 0) or 0),
        "no_reply_tool_call_count": int(getattr(log, "no_reply_tool_call_count", 0) or 0),
        "structured_fallback_count": int(getattr(log, "structured_fallback_count", 0) or 0),
        "total_final_action_count": int(getattr(log, "total_final_action_count", 0) or 0),
        "result": log.result or "",
    }


def _reply_contract_has_final_action(log) -> bool:
    return bool(
        getattr(log, "has_reply_tool", 0)
        or getattr(log, "has_no_reply_tool", 0)
        or getattr(log, "has_structured_fallback", 0)
    )


def _reply_contract_run_key(log) -> str:
    return (
        str(getattr(log, "run_id", "") or "").strip()
        or str(getattr(log, "trace_id", "") or "").strip()
        or f"log:{getattr(log, 'id', '')}"
    )


def _is_reply_eval_test_session(session_id: str) -> bool:
    sid = str(session_id or "").strip().lower()
    if not sid:
        return False
    prefixes = (
        "reply-test",
        "reply_test",
        "reply-eval",
        "reply_eval",
        "test-",
        "private_smoke",
    )
    return sid.startswith(prefixes) or "smoke" in sid


def _safe_rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _resolve_reply_test_prompt_settings(body: ReplyTestRunRequest) -> tuple[str, str, bool]:
    variant = str(body.variant or "code_retry")
    engine = str(body.prompt_engine or "v1")
    prompt_mode = "legacy"
    enable_retry = bool(body.enable_reply_contract_retry)

    if variant == "prompt_only":
        prompt_mode = "managed"
        enable_retry = False
    elif variant == "code_retry":
        prompt_mode = "legacy"
        enable_retry = enable_retry
    elif variant == "baseline":
        prompt_mode = "legacy"
        enable_retry = False
    elif variant == "v1_baseline":
        engine = "v1"
        prompt_mode = "legacy"
        enable_retry = False
    elif variant == "v2_prompt_only":
        engine = "v2"
        prompt_mode = "legacy"
        enable_retry = False
    elif variant == "v2_code_retry":
        engine = "v2"
        prompt_mode = "legacy"
        enable_retry = enable_retry

    if engine not in {"v1", "v2"}:
        engine = "v1"
    return engine, prompt_mode, enable_retry


async def _run_reply_test_once(body: ReplyTestRunRequest, db: Session) -> dict:
    from core.database import AgentRun, LLMApiRequestLog, ReplyContractCheckLog
    from core.tracing import new_trace_id
    from nanobot_kt.bridge import get_bridge

    trace_id = new_trace_id()
    session_id = (body.session_id or f"reply-test-{uuid.uuid4().hex[:8]}").strip()
    sender_id = (body.sender_id or "admin").strip()
    prompt_engine, prompt_mode, enable_retry = _resolve_reply_test_prompt_settings(body)
    metadata = {
        "trace_id": trace_id,
        "chat_type": body.chat_type,
        "is_group": body.chat_type == "group",
        "group_id": session_id if body.chat_type == "group" else "",
        "sender_id": sender_id,
        "sender_name": body.sender_name,
        "character_name": body.character_name,
        "persona_text": body.persona_text,
        "history_header": body.recent_context,
        "variant": body.variant,
        "prompt_runtime_engine_override": prompt_engine,
        "prompt_system_mode_override": prompt_mode,
        "enable_reply_contract_retry": enable_retry,
        "dry_run": bool(body.dry_run),
    }
    bridge = get_bridge()
    content = await bridge.handle_message(
        body.message,
        user_id=sender_id,
        session_id=session_id,
        sender_name=body.sender_name,
        metadata=metadata,
    )

    run = (
        db.query(AgentRun)
        .filter(AgentRun.trace_id == trace_id)
        .order_by(AgentRun.started_at.desc())
        .first()
    )
    run_id = run.run_id if run else ""
    prompt_sha256 = run.prompt_sha256 if run else ""
    prompt_source = run.prompt_source if run else ""
    prompt_mode_actual = run.prompt_mode if run else prompt_mode
    reply_logs = []
    llm_logs = []
    if run_id:
        reply_logs = (
            db.query(ReplyContractCheckLog)
            .filter(ReplyContractCheckLog.run_id == run_id)
            .order_by(ReplyContractCheckLog.attempt.asc(), ReplyContractCheckLog.created_at.asc())
            .all()
        )
        llm_logs = (
            db.query(LLMApiRequestLog)
            .filter(LLMApiRequestLog.run_id == run_id)
            .order_by(LLMApiRequestLog.created_at.asc())
            .all()
        )
    first_log = reply_logs[0] if reply_logs else None
    retry_log = next((log for log in reply_logs if int(log.attempt or 0) == 1), None)
    action = "reply" if str(content or "").strip() else "no_reply"
    called_reply_or_no_reply = any(
        bool(log.has_reply_tool) or bool(log.has_no_reply_tool) or bool(log.has_structured_fallback)
        for log in reply_logs
    )
    prompt_miss_count = sum(
        1 for log in reply_logs
        if int(log.attempt or 0) == 0
        and not (bool(log.has_reply_tool) or bool(log.has_no_reply_tool) or bool(log.has_structured_fallback))
    )
    total_final_action_count = sum(int(getattr(log, "total_final_action_count", 0) or 0) for log in reply_logs)
    retry_used = retry_log is not None
    return {
        "ok": True,
        "trace_id": trace_id,
        "run_id": run_id,
        "prompt_engine": prompt_engine,
        "prompt_mode": prompt_mode_actual,
        "prompt_source": prompt_source,
        "prompt_sha256": prompt_sha256,
        "first_attempt": _reply_log_attempt(first_log),
        "retry_attempt": {
            "enabled": enable_retry,
            **_reply_log_attempt(retry_log),
        },
        "final": {"action": action, "content": str(content or "")},
        "metrics": {
            "reply_contract_ok": bool(called_reply_or_no_reply),
            "retry_used": bool(retry_used),
            "retry_success": bool(retry_log and retry_log.result == "retry_success"),
            "prompt_miss_count": int(prompt_miss_count),
            "total_final_action_count": int(total_final_action_count),
        },
        "llm_api_request_logs": [row_to_dict(row) for row in llm_logs],
        "reply_contract_check_logs": [row_to_dict(row) for row in reply_logs],
    }


@router.post("/reply-test/run")
async def reply_test_run(
    body: ReplyTestRunRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    return await _run_reply_test_once(body, db)


class ReplyEvalCaseIn(BaseModel):
    case_id: str = ""
    title: str = ""
    chat_type: Literal["group", "private"] = "group"
    input_text: str
    context: dict = Field(default_factory=dict)
    expected_action: Literal["reply", "no_reply", "any"] = "any"
    expected_keywords: list[str] = Field(default_factory=list)
    forbidden_keywords: list[str] = Field(default_factory=list)
    source: str = "manual"
    tags: list[str] = Field(default_factory=list)
    enabled: int = 1


class ReplyEvalCasePatch(BaseModel):
    title: Optional[str] = None
    chat_type: Optional[Literal["group", "private"]] = None
    input_text: Optional[str] = None
    context: Optional[dict] = None
    expected_action: Optional[Literal["reply", "no_reply", "any"]] = None
    expected_keywords: Optional[list[str]] = None
    forbidden_keywords: Optional[list[str]] = None
    source: Optional[str] = None
    tags: Optional[list[str]] = None
    enabled: Optional[int] = None


class ReplyEvalSaveGeneratedIn(BaseModel):
    items: list[ReplyEvalCaseIn]


class ReplyEvalRunIn(BaseModel):
    name: str = ""
    variant: Literal[
        "baseline",
        "prompt_only",
        "code_retry",
        "v1_baseline",
        "v2_prompt_only",
        "v2_code_retry",
    ] = "code_retry"
    case_ids: list[str] = Field(default_factory=list)
    limit: int = 50


def _upsert_reply_eval_case(db: Session, item: ReplyEvalCaseIn):
    from core.database import ReplyEvalCase

    case_id = (item.case_id or f"reply_case_{uuid.uuid4().hex[:12]}").strip()
    row = db.query(ReplyEvalCase).filter(ReplyEvalCase.case_id == case_id).first()
    if row is None:
        row = ReplyEvalCase(case_id=case_id)
        db.add(row)
    row.title = item.title or item.input_text[:40]
    row.chat_type = item.chat_type
    row.input_text = item.input_text
    row.context_json = json.dumps(item.context or {}, ensure_ascii=False)
    row.expected_action = item.expected_action
    row.expected_keywords_json = json.dumps(item.expected_keywords or [], ensure_ascii=False)
    row.forbidden_keywords_json = json.dumps(item.forbidden_keywords or [], ensure_ascii=False)
    row.source = item.source or "manual"
    row.tags_json = json.dumps(item.tags or [], ensure_ascii=False)
    row.enabled = 1 if item.enabled else 0
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return row


@router.get("/reply-eval/cases")
def reply_eval_list_cases(
    enabled: int | None = Query(None),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalCase

    q = db.query(ReplyEvalCase)
    if enabled is not None:
        q = q.filter(ReplyEvalCase.enabled == int(enabled))
    rows = q.order_by(ReplyEvalCase.created_at.desc()).all()
    return {"items": [_reply_case_to_dict(row) for row in rows], "total": len(rows)}


@router.post("/reply-eval/cases")
def reply_eval_create_case(
    body: ReplyEvalCaseIn,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    return _reply_case_to_dict(_upsert_reply_eval_case(db, body))


@router.put("/reply-eval/cases/{case_id}")
def reply_eval_update_case(
    case_id: str,
    body: ReplyEvalCasePatch,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalCase

    row = db.query(ReplyEvalCase).filter(ReplyEvalCase.case_id == case_id).first()
    if row is None:
        raise HTTPException(404, "Reply eval case not found")
    updates = body.model_dump(exclude_unset=True)
    if "title" in updates:
        row.title = updates["title"] or row.title
    if "chat_type" in updates:
        row.chat_type = updates["chat_type"] or row.chat_type
    if "input_text" in updates:
        row.input_text = updates["input_text"] or row.input_text
    if "context" in updates:
        row.context_json = json.dumps(updates["context"] or {}, ensure_ascii=False)
    if "expected_action" in updates:
        row.expected_action = updates["expected_action"] or row.expected_action
    if "expected_keywords" in updates:
        row.expected_keywords_json = json.dumps(updates["expected_keywords"] or [], ensure_ascii=False)
    if "forbidden_keywords" in updates:
        row.forbidden_keywords_json = json.dumps(updates["forbidden_keywords"] or [], ensure_ascii=False)
    if "source" in updates:
        row.source = updates["source"] or row.source
    if "tags" in updates:
        row.tags_json = json.dumps(updates["tags"] or [], ensure_ascii=False)
    if "enabled" in updates:
        row.enabled = 1 if updates["enabled"] else 0
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return _reply_case_to_dict(row)


@router.delete("/reply-eval/cases/{case_id}")
def reply_eval_delete_case(
    case_id: str,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalCase

    row = db.query(ReplyEvalCase).filter(ReplyEvalCase.case_id == case_id).first()
    if row is None:
        raise HTTPException(404, "Reply eval case not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/reply-eval/generate-preview")
def reply_eval_generate_preview(_body: dict = {}, _auth=Depends(verify_admin)):
    categories = {
        "被叫到": [("有人叫我", "凛音在吗"), ("明确点名", "凛音帮我看下"), ("喊名字", "凛音")],
        "直接问题": [("是否在线", "你在吗"), ("简单求助", "这个怎么弄"), ("确认问题", "能帮我解释下吗")],
        "普通闲聊": [("随口感叹", "今天好困"), ("日常吐槽", "天气又变冷了"), ("闲聊接话", "这也太离谱了")],
        "情绪低落": [("低落", "有点撑不住了"), ("烦躁", "今天真的很烦"), ("自我怀疑", "是不是我太菜了")],
        "技术求助": [("报错", "pytest 这里为什么炸了"), ("配置", "docker 端口怎么映射"), ("代码", "这个函数怎么改")],
        "信息不足": [("半截问题", "那个东西咋办"), ("缺上下文", "然后呢"), ("不清楚对象", "它又坏了")],
        "纯哈哈哈": [("笑", "哈哈哈哈"), ("表情", "笑死"), ("附和", "草")],
        "别人在和别人说话": [("转述", "你问小王吧"), ("对别人", "老张你看看"), ("旁观", "他们说的有道理")],
        "半句话": [("断句", "我刚才那个"), ("等待", "等一下我"), ("未完成", "就是那个")],
        "身份试探": [("问身份", "你是谁"), ("问机器人", "你是 bot 吗"), ("问模型", "你用的什么模型")],
        "生活场景邀请": [("吃饭", "晚上吃啥"), ("游戏", "要不要开一把"), ("出门", "周末出去吗")],
    }
    items = []
    for category, examples in categories.items():
        for idx, (title, text) in enumerate(examples + examples[:1], start=1):
            expected = "reply" if category in {"被叫到", "直接问题", "情绪低落", "技术求助", "身份试探"} else "any"
            items.append({
                "case_id": f"reply_gen_{category}_{idx}_{uuid.uuid4().hex[:6]}",
                "title": f"{category}-{title}",
                "chat_type": "group",
                "input_text": text,
                "context": {},
                "expected_action": expected,
                "expected_keywords": [],
                "forbidden_keywords": [],
                "source": "generated",
                "tags": [category],
                "enabled": 1,
            })
    return {"items": items, "total": len(items)}


@router.post("/reply-eval/save-generated")
def reply_eval_save_generated(
    body: ReplyEvalSaveGeneratedIn,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    saved = 0
    for item in body.items:
        item.source = item.source or "generated"
        _upsert_reply_eval_case(db, item)
        saved += 1
    return {"ok": True, "saved": saved}


@router.post("/reply-eval/run")
async def reply_eval_run(
    body: ReplyEvalRunIn,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalCase, ReplyEvalResult, ReplyEvalRun

    q = db.query(ReplyEvalCase).filter(ReplyEvalCase.enabled == 1)
    if body.case_ids:
        q = q.filter(ReplyEvalCase.case_id.in_(body.case_ids))
    cases = q.order_by(ReplyEvalCase.created_at.asc()).limit(max(1, min(body.limit or 50, 200))).all()
    run = ReplyEvalRun(name=body.name or f"{body.variant} {datetime.now().strftime('%m-%d %H:%M')}", variant=body.variant)
    db.add(run)
    db.commit()
    db.refresh(run)

    results = []
    totals = {
        "reply_contract_ok": 0,
        "retry_used": 0,
        "retry_success": 0,
        "no_tool_call": 0,
        "fake_tool_claim": 0,
        "empty_output": 0,
        "passed": 0,
    }
    for case in cases:
        context = json.loads(case.context_json or "{}")
        test_body = ReplyTestRunRequest(
            chat_type=case.chat_type or "group",
            session_id=str(context.get("session_id") or f"reply-eval-{case.case_id}"),
            sender_id=str(context.get("sender_id") or "eval"),
            sender_name=str(context.get("sender_name") or "eval"),
            message=case.input_text,
            recent_context=str(context.get("recent_context") or ""),
            persona_text=str(context.get("persona_text") or ""),
            variant=body.variant,
            enable_reply_contract_retry=body.variant in {"code_retry", "v2_code_retry"},
            dry_run=True,
        )
        try:
            outcome = await _run_reply_test_once(test_body, db)
            actual_action = outcome["final"]["action"]
            final_content = outcome["final"].get("content", "")
            expected_action = case.expected_action or "any"
            expected_ok = expected_action == "any" or actual_action == expected_action
            expected_keywords = _loads_json_list(case.expected_keywords_json)
            forbidden_keywords = _loads_json_list(case.forbidden_keywords_json)
            keyword_ok = all(k in final_content for k in expected_keywords)
            forbidden_ok = not any(k in final_content for k in forbidden_keywords)
            passed = bool(expected_ok and keyword_ok and forbidden_ok)
            first = outcome.get("first_attempt", {})
            retry = outcome.get("retry_attempt", {})
            called = bool(outcome["metrics"].get("reply_contract_ok"))
            retry_used = bool(outcome["metrics"].get("retry_used"))
            retry_success = bool(outcome["metrics"].get("retry_success"))
            totals["reply_contract_ok"] += 1 if called else 0
            totals["retry_used"] += 1 if retry_used else 0
            totals["retry_success"] += 1 if retry_success else 0
            totals["no_tool_call"] += 1 if first.get("result") == "no_tool_call" else 0
            totals["fake_tool_claim"] += 1 if first.get("result") == "fake_tool_call_claim" else 0
            totals["empty_output"] += 1 if not final_content else 0
            totals["passed"] += 1 if passed else 0
            row = ReplyEvalResult(
                run_id=run.id,
                agent_run_id=str(outcome.get("run_id") or ""),
                trace_id=str(outcome.get("trace_id") or ""),
                prompt_sha256=str(outcome.get("prompt_sha256") or ""),
                case_id=case.case_id,
                variant=body.variant,
                expected_action=expected_action,
                actual_action=actual_action,
                called_reply_or_no_reply=1 if called else 0,
                retry_used=1 if retry_used else 0,
                passed=1 if passed else 0,
                raw_output_preview=str(first.get("raw_output") or "")[:2000],
                final_content_preview=str(final_content or "")[:2000],
                error="",
            )
        except Exception as e:
            passed = False
            row = ReplyEvalResult(
                run_id=run.id,
                agent_run_id="",
                trace_id="",
                prompt_sha256="",
                case_id=case.case_id,
                variant=body.variant,
                expected_action=case.expected_action or "any",
                actual_action="error",
                passed=0,
                error=str(e)[:1000],
            )
        db.add(row)
        results.append(row)

    total = len(cases)
    failed = total - totals["passed"]
    metrics = {
        "reply_call_rate": round(totals["reply_contract_ok"] / total, 4) if total else 0,
        "valid_action_rate": round(totals["reply_contract_ok"] / total, 4) if total else 0,
        "expected_action_accuracy": round(totals["passed"] / total, 4) if total else 0,
        "retry_used_rate": round(totals["retry_used"] / total, 4) if total else 0,
        "retry_success_rate": round(totals["retry_success"] / total, 4) if total else 0,
        "no_tool_call_rate": round(totals["no_tool_call"] / total, 4) if total else 0,
        "fake_tool_claim_rate": round(totals["fake_tool_claim"] / total, 4) if total else 0,
        "empty_output_rate": round(totals["empty_output"] / total, 4) if total else 0,
    }
    run.total = total
    run.reply_contract_ok = totals["reply_contract_ok"]
    run.retry_used = totals["retry_used"]
    run.passed = totals["passed"]
    run.failed = failed
    run.summary_json = json.dumps(metrics, ensure_ascii=False)
    db.commit()
    db.refresh(run)
    return {**_reply_eval_run_to_dict(run), "results": [row_to_dict(row) for row in results]}


@router.get("/reply-eval/traffic")
def reply_eval_real_traffic(
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(50, ge=1, le=200),
    session_id: str = "",
    include_test_sessions: bool = False,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyContractCheckLog

    window_hours = max(1, min(int(hours or 24), 168))
    sample_limit = max(1, min(int(limit or 50), 200))
    since = datetime.now() - timedelta(hours=window_hours)
    query = db.query(ReplyContractCheckLog).filter(ReplyContractCheckLog.created_at >= since)
    if session_id:
        query = query.filter(ReplyContractCheckLog.session_id == session_id)
    rows = query.order_by(ReplyContractCheckLog.created_at.desc(), ReplyContractCheckLog.id.desc()).limit(5000).all()
    if not include_test_sessions:
        rows = [row for row in rows if not _is_reply_eval_test_session(row.session_id)]

    runs: dict[str, list[Any]] = {}
    for row in rows:
        runs.setdefault(_reply_contract_run_key(row), []).append(row)

    total_runs = len(runs)
    contract_ok_runs = 0
    first_attempt_ok_runs = 0
    prompt_miss_count = 0
    retry_used_runs = 0
    retry_success_runs = 0
    retry_failed_after_prompt_count = 0
    first_overcall_count = 0
    retry_overcall_count = 0
    total_final_action_count = 0
    reply_tool_call_count = 0
    no_reply_tool_call_count = 0
    structured_fallback_count = 0
    result_counts: dict[str, int] = {}
    session_stats: dict[str, dict[str, Any]] = {}

    for row in rows:
        result = str(row.result or "")
        result_counts[result or "-"] = result_counts.get(result or "-", 0) + 1
        total_final_action_count += int(getattr(row, "total_final_action_count", 0) or 0)
        reply_tool_call_count += int(getattr(row, "reply_tool_call_count", 0) or 0)
        no_reply_tool_call_count += int(getattr(row, "no_reply_tool_call_count", 0) or 0)
        structured_fallback_count += int(getattr(row, "structured_fallback_count", 0) or 0)

    for run_id, run_rows in runs.items():
        ordered = sorted(run_rows, key=lambda row: (int(row.attempt or 0), row.created_at or datetime.min, int(row.id or 0)))
        first = next((row for row in ordered if int(row.attempt or 0) == 0), ordered[0] if ordered else None)
        retry_rows = [row for row in ordered if int(row.attempt or 0) > 0]
        has_ok = any(_reply_contract_has_final_action(row) for row in ordered)
        first_ok = bool(first and _reply_contract_has_final_action(first))
        first_miss = bool(first and not _reply_contract_has_final_action(first))
        retry_success = any(
            str(row.result or "") == "retry_success" or _reply_contract_has_final_action(row)
            for row in retry_rows
        )
        retry_failed_after_prompt = bool(retry_rows and not retry_success)

        contract_ok_runs += 1 if has_ok else 0
        first_attempt_ok_runs += 1 if first_ok else 0
        prompt_miss_count += 1 if first_miss else 0
        retry_used_runs += 1 if retry_rows else 0
        retry_success_runs += 1 if retry_success else 0
        retry_failed_after_prompt_count += 1 if retry_failed_after_prompt else 0
        first_overcall_count += 1 if first and int(getattr(first, "total_final_action_count", 0) or 0) > 1 else 0
        retry_overcall_count += sum(
            1 for row in retry_rows
            if int(getattr(row, "total_final_action_count", 0) or 0) != 1
        )

        sid = str(getattr(first or ordered[0], "session_id", "") or "")
        stat = session_stats.setdefault(sid, {
            "session_id": sid,
            "total_runs": 0,
            "contract_ok_runs": 0,
            "prompt_miss_count": 0,
            "retry_used_runs": 0,
            "retry_success_runs": 0,
            "latest_at": "",
        })
        stat["total_runs"] += 1
        stat["contract_ok_runs"] += 1 if has_ok else 0
        stat["prompt_miss_count"] += 1 if first_miss else 0
        stat["retry_used_runs"] += 1 if retry_rows else 0
        stat["retry_success_runs"] += 1 if retry_success else 0
        latest = max((row.created_at for row in ordered if row.created_at), default=None)
        if latest and str(latest.isoformat()) > str(stat.get("latest_at") or ""):
            stat["latest_at"] = latest.isoformat()

    for stat in session_stats.values():
        stat["contract_ok_rate"] = _safe_rate(int(stat["contract_ok_runs"]), int(stat["total_runs"]))

    failure_rows = [
        row for row in rows
        if not _reply_contract_has_final_action(row)
        or int(getattr(row, "total_final_action_count", 0) or 0) > 1
    ][:sample_limit]

    return {
        "window_hours": window_hours,
        "since": since.isoformat(),
        "total_logs": len(rows),
        "total_runs": total_runs,
        "contract_ok_runs": contract_ok_runs,
        "contract_ok_rate": _safe_rate(contract_ok_runs, total_runs),
        "first_attempt_ok_runs": first_attempt_ok_runs,
        "first_attempt_ok_rate": _safe_rate(first_attempt_ok_runs, total_runs),
        "prompt_miss_count": prompt_miss_count,
        "prompt_miss_rate": _safe_rate(prompt_miss_count, total_runs),
        "retry_used_runs": retry_used_runs,
        "retry_used_rate": _safe_rate(retry_used_runs, total_runs),
        "retry_success_runs": retry_success_runs,
        "retry_success_rate": _safe_rate(retry_success_runs, total_runs),
        "retry_failed_after_prompt_count": retry_failed_after_prompt_count,
        "retry_failed_after_prompt_rate": _safe_rate(retry_failed_after_prompt_count, total_runs),
        "first_overcall_count": first_overcall_count,
        "retry_overcall_count": retry_overcall_count,
        "total_final_action_count": total_final_action_count,
        "reply_tool_call_count": reply_tool_call_count,
        "no_reply_tool_call_count": no_reply_tool_call_count,
        "structured_fallback_count": structured_fallback_count,
        "result_counts": result_counts,
        "session_breakdown": sorted(
            session_stats.values(),
            key=lambda item: (-int(item["total_runs"]), str(item["session_id"])),
        )[:20],
        "recent_failures": [
            {
                "id": row.id,
                "trace_id": row.trace_id,
                "run_id": row.run_id,
                "session_id": row.session_id,
                "attempt": int(row.attempt or 0),
                "result": row.result or "",
                "total_final_action_count": int(getattr(row, "total_final_action_count", 0) or 0),
                "reply_tool_call_count": int(getattr(row, "reply_tool_call_count", 0) or 0),
                "no_reply_tool_call_count": int(getattr(row, "no_reply_tool_call_count", 0) or 0),
                "raw_output_preview": row.raw_output_preview or "",
                "created_at": row.created_at.isoformat() if row.created_at else "",
            }
            for row in failure_rows
        ],
        "include_test_sessions": bool(include_test_sessions),
        "truncated": len(rows) >= 5000,
    }


@router.get("/reply-eval/runs")
def reply_eval_list_runs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalRun

    rows = db.query(ReplyEvalRun).order_by(ReplyEvalRun.created_at.desc()).limit(limit).all()
    return {"items": [_reply_eval_run_to_dict(row) for row in rows], "total": len(rows)}


@router.get("/reply-eval/runs/{run_id}")
def reply_eval_get_run(
    run_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.database import ReplyEvalResult, ReplyEvalRun

    run = db.query(ReplyEvalRun).filter(ReplyEvalRun.id == int(run_id)).first()
    if run is None:
        raise HTTPException(404, "Reply eval run not found")
    results = (
        db.query(ReplyEvalResult)
        .filter(ReplyEvalResult.run_id == run.id)
        .order_by(ReplyEvalResult.id.asc())
        .all()
    )
    return {**_reply_eval_run_to_dict(run), "results": [row_to_dict(row) for row in results]}


# ═══════════════════════════════════════════
# Eval 系统 API
# ═══════════════════════════════════════════

from core.database import EvalCandidate, EvalRun, EvalRunResult
from core.eval_sampling.store import (
    list_candidates, get_candidate, update_candidate,
    label_candidate, ignore_candidate, promote_candidate,
    save_run, save_run_results, get_runs, get_run,
)


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
    return {"items": items, "total": total, "page": page}


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
    result = update_candidate(db, case_id, **updates)
    if not result:
        raise HTTPException(404, "candidate not found")
    _audit_request(db, request, "update_candidate", "eval_candidate", case_id, updates)
    return result


class LabelRequest(BaseModel):
    expected: dict = Field(default_factory=dict)


@router.post("/evals/candidates/{case_id}/label")
def eval_label_candidate(
    case_id: str, body: LabelRequest,
    request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    result = label_candidate(db, case_id, body.expected)
    if not result:
        raise HTTPException(404, "candidate not found")
    _audit_request(db, request, "label_candidate", "eval_candidate", case_id,
                   {"expected_keys": list(body.expected.keys())})
    return result


@router.post("/evals/candidates/{case_id}/ignore")
def eval_ignore_candidate(
    case_id: str, request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    result = ignore_candidate(db, case_id)
    if not result:
        raise HTTPException(404, "candidate not found")
    _audit_request(db, request, "ignore_candidate", "eval_candidate", case_id)
    return result


@router.post("/evals/candidates/{case_id}/promote")
def eval_promote_candidate(
    case_id: str, request: Request, db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        path = promote_candidate(db, case_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not path:
        raise HTTPException(404, "candidate not found")
    _audit_request(db, request, "promote_candidate", "eval_candidate", case_id, {"path": path})
    return {"ok": True, "path": path}


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
