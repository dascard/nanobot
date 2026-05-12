"""WebUI 管理 API——Sticker/Block/Config/DB 管理。prefix=/api/v1/admin，认证使用 NANOBOT_ADMIN_TOKEN。"""

import json
import logging
import os
from datetime import datetime, timedelta
from hmac import compare_digest
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import (
    get_db,
    StickerMemory, ChatStreamConfig, UserBlockRule, SystemSetting, AdminAuditLog,
    ChatLog, User,
)
from config import NANOBOT_ADMIN_TOKEN

logger = logging.getLogger("nanobot.admin")
router = APIRouter(prefix="/api/v1/admin")

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

# ── Auth check endpoint ──

@router.get("/me")
def admin_me(_auth=Depends(verify_admin)):
    return {"ok": True, "user": "admin"}


_VERSION_CACHE: dict | None = None


@router.get("/version")
def admin_version(_auth=Depends(verify_admin)):
    global _VERSION_CACHE
    if _VERSION_CACHE is not None:
        return _VERSION_CACHE

    # 优先读环境变量（Docker build-arg 注入），无则 git fallback
    commit = os.environ.get("NANOBOT_GIT_COMMIT") or ""
    branch = os.environ.get("NANOBOT_GIT_BRANCH") or ""
    commit_date = os.environ.get("NANOBOT_GIT_COMMIT_DATE") or ""
    dirty_raw = os.environ.get("NANOBOT_GIT_DIRTY") or ""

    if not commit or not branch:
        import subprocess
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def _git(args: list[str]) -> str | None:
            try:
                return subprocess.check_output(
                    ["git", *args],
                    cwd=base,
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                ).strip()
            except Exception:
                return None

        commit = commit or _git(["rev-parse", "--short", "HEAD"]) or "unknown"
        branch = branch or _git(["rev-parse", "--abbrev-ref", "HEAD"]) or ""
        commit_date = commit_date or _git(["log", "-1", "--format=%ci", "--date=iso-strict"]) or ""

        if not dirty_raw:
            status = _git([
                "status",
                "--porcelain",
                "--untracked-files=no",
                "--",
                ".",
                ":(exclude)data",
                ":(exclude).claude",
                ":(exclude)sentinel/model.safetensors",
                ":(exclude).env",
                ":(exclude).vscode",
                ":(exclude).idea",
                ":(exclude)webui/node_modules",
            ])
            if status is None:
                dirty_raw = "null"
            elif status:
                dirty_raw = "true"
            else:
                dirty_raw = "false"

    # 解析 dirty 状态：null=未知, true=有改动, false=干净
    if dirty_raw == "true":
        dirty = True
    elif dirty_raw == "false":
        dirty = False
    else:
        dirty = None

    _VERSION_CACHE = {
        "commit": commit or "unknown",
        "full_commit": os.environ.get("NANOBOT_GIT_FULL_COMMIT", "") or "",
        "branch": branch or "",
        "commit_date": commit_date or "",
        "dirty": dirty,
        "display": f"{commit}{'-dirty' if dirty and commit != 'unknown' else ''}",
    }
    return _VERSION_CACHE


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
    from core.database import GroupMemory
    from core.group_runtime.ids import normalize_group_session_id

    norm = normalize_group_session_id(group_id)
    q = db.query(GroupMemory).filter(GroupMemory.group_id == norm)
    if memory_type:
        q = q.filter(GroupMemory.memory_type == memory_type)
    rows = q.order_by(GroupMemory.confidence.desc(), GroupMemory.last_seen.desc()).limit(100).all()
    return {
        "memories": [
            {
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
                "evidence_log_ids_json": r.evidence_log_ids_json,
            }
            for r in rows
        ]
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
    repeats: int = Field(default=1, ge=1, le=20)


@router.post("/timing-gate/test")
def timing_gate_test(body: TimingGateTestRequest, _auth=Depends(verify_admin)):
    import time
    from clients.classifier_client import get_timing_gate

    gate = get_timing_gate()
    context = body.context.strip() or "<timing_context>\n[未知用户]\n测试 TimingGate\n</timing_context>"
    runs = []
    for idx in range(body.repeats):
        t0 = time.time()
        result = gate.judge(context)
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
        groups.append({
            "content_hash": content_hash,
            "count": n,
            "items": [_sticker_dict(r) for r in stickers],
        })
    return {"groups": groups}


@router.get("/stickers/{sticker_id}")
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
# ChatStreamConfig CRUD
# ═══════════════════════════════════════════

@router.get("/configs")
def list_configs(search: str = "", page: int = 1, limit: int = 20,
                 db: Session = Depends(get_db), _auth=Depends(verify_admin)):
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


# ═══════════════════════════════════════════
# DB Browser (read-only)
# ═══════════════════════════════════════════

READONLY_TABLES = [
    "users", "personas", "persona_facts", "persona_behaviors",
    "chat_logs", "conversation_turns", "memory_digests",
    "group_memories", "expression_memories", "jargon_memories",
    "sticker_memories", "chat_stream_configs",
    "system_prompts", "scheduled_tasks",
    "admin_audit_logs", "user_block_rules", "system_settings",
]


@router.get("/db/tables")
def list_tables(_auth=Depends(verify_admin)):
    return {"tables": READONLY_TABLES}


@router.get("/db/tables/{table_name}")
def query_table(table_name: str, page: int = 1, limit: int = 50,
                db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    if table_name not in READONLY_TABLES:
        raise HTTPException(400, f"Unknown table: {table_name}")
    try:
        result = db.execute(
            text(f"SELECT * FROM {table_name} ORDER BY rowid DESC LIMIT :limit OFFSET :offset"),
            {"limit": limit, "offset": (page - 1) * limit})
        columns = list(result.keys())
        rows = [dict(zip(columns, [str(v) if v is not None else None for v in row]))
                for row in result.fetchall()]
        total = db.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
        return {"total": total, "page": page, "columns": columns, "rows": rows}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/db/query")
def execute_readonly_query(body: DbQuery, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    q = body.query.strip()
    q_upper = q.upper()
    if ";" in q.rstrip(";"):
        raise HTTPException(400, "Multi-statement forbidden")
    if not q_upper.startswith("SELECT "):
        raise HTTPException(400, "Only SELECT allowed")
    forbidden = (
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
        "PRAGMA", "ATTACH", "DETACH", "VACUUM", "REINDEX", "LOAD_EXTENSION",
    )
    for word in forbidden:
        if word in q_upper:
            raise HTTPException(400, f"Forbidden: {word}")
    try:
        limited = f"SELECT * FROM ({q}) LIMIT 500"
        result = db.execute(text(limited))
        columns = list(result.keys()) if result.returns_rows else []
        rows = [dict(zip(columns, [str(v) if v is not None else None for v in row]))
                for row in result.fetchall()] if result.returns_rows else []
        return {"columns": columns, "rows": rows, "row_count": len(rows)}
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════
# Prompt (read-only)
# ═══════════════════════════════════════════

@router.get("/prompt")
def get_prompt(_auth=Depends(verify_admin)):
    import os as _os
    base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    prompt_path = _os.path.join(base, "creatures", "nanobot", "prompt.md")
    if not _os.path.exists(prompt_path):
        raise HTTPException(404, "prompt.md not found")
    with open(prompt_path, "r", encoding="utf-8") as fh:
        content = fh.read()
        return {"content": content, "metrics": _prompt_metrics(content)}


@router.get("/prompt/fragments")
def list_prompt_fragments(_auth=Depends(verify_admin)):
    import os as _os
    base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    frag_dir = _os.path.join(base, "creatures", "nanobot", "prompts", "system")
    items = []
    if _os.path.isdir(frag_dir):
        for fname in sorted(_os.listdir(frag_dir)):
            if fname.endswith(".md"):
                fpath = _os.path.join(frag_dir, fname)
                with open(fpath, "r", encoding="utf-8") as fh:
                    items.append({"name": fname, "content": fh.read()})
    return {"fragments": items}


@router.put("/prompt/fragments/{name}")
def update_prompt_fragment(name: str, body: dict, db: Session = Depends(get_db),
                           _auth=Depends(verify_admin)):
    import os as _os, hashlib, re, shutil
    base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    frag_dir = _os.path.join(base, "creatures", "nanobot", "prompts", "system")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.md", name):
        raise HTTPException(400, "Invalid fragment name")
    fpath = _os.path.join(frag_dir, name)
    if not _os.path.exists(fpath):
        raise HTTPException(404, "Fragment not found")
    content = str(body.get("content", ""))
    if not content.strip():
        raise HTTPException(400, "Refuse to save empty prompt fragment")
    with open(fpath, "r", encoding="utf-8") as fh:
        old = fh.read()
    old_hash = hashlib.sha256(old.encode()).hexdigest()[:12]
    backup_dir = _os.path.join(base, "data", "prompt_backups")
    _os.makedirs(backup_dir, exist_ok=True)
    ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = _os.path.join(backup_dir, f"{name}.{ts}.{old_hash}.bak")
    shutil.copy2(fpath, backup_path)
    with open(fpath, "w", encoding="utf-8") as fh:
        fh.write(content)
    new_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
    _audit_request(db, request, "update_prompt_fragment", "prompt_fragment", name, {
        "before_hash": old_hash, "after_hash": new_hash,
        "size_before": len(old), "size_after": len(content),
    })
    return {"name": name, "saved": True, "before_hash": old_hash, "after_hash": new_hash}


@router.post("/prompt/build")
def rebuild_prompt(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    import subprocess, os as _os
    base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    script = _os.path.join(base, "scripts", "build_nanobot_prompt.py")
    try:
        result = subprocess.run(["python", script], capture_output=True, text=True, cwd=base, timeout=10)
        ok = result.returncode == 0
        _audit_request(db, request, "rebuild_prompt", "prompt", "nanobot", {
            "ok": ok, "returncode": result.returncode,
        })
        if not ok:
            return {"ok": False, "stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "returncode": result.returncode}
        return {
            "ok": True,
            "output": result.stdout.strip(),
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _prompt_backup_dir() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "data", "prompt_backups")


def _parse_prompt_backup_name(name: str) -> dict | None:
    import re
    m = re.fullmatch(
        r"(?P<fragment>[A-Za-z0-9_.-]+\.md)\.(?P<ts>\d{8}_\d{6}_\d{6})\.(?P<hash>[a-f0-9]+)\.bak",
        name,
    )
    return m.groupdict() if m else None


@router.get("/prompt/backups")
def list_prompt_backups(_auth=Depends(verify_admin)):
    backup_dir = _prompt_backup_dir()
    items = []
    if os.path.isdir(backup_dir):
        for fname in sorted(os.listdir(backup_dir), reverse=True):
            parsed = _parse_prompt_backup_name(fname)
            if not parsed:
                continue
            path = os.path.join(backup_dir, fname)
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
def rollback_prompt_backup(backup_name: str, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    import hashlib, shutil

    parsed = _parse_prompt_backup_name(os.path.basename(backup_name))
    if not parsed:
        raise HTTPException(400, "Invalid backup name")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    backup_path = os.path.abspath(os.path.join(_prompt_backup_dir(), backup_name))
    backup_dir = os.path.abspath(_prompt_backup_dir())
    if not backup_path.startswith(backup_dir + os.sep) or not os.path.isfile(backup_path):
        raise HTTPException(404, "Backup not found")

    frag_dir = os.path.join(base, "creatures", "nanobot", "prompts", "system")
    target = os.path.abspath(os.path.join(frag_dir, parsed["fragment"]))
    if not target.startswith(os.path.abspath(frag_dir) + os.sep) or not os.path.isfile(target):
        raise HTTPException(404, "Target fragment not found")

    with open(target, "r", encoding="utf-8") as fh:
        current = fh.read()
    current_hash = hashlib.sha256(current.encode()).hexdigest()[:12]
    rollback_guard = os.path.join(
        backup_dir,
        f"{parsed['fragment']}.{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.{current_hash}.bak",
    )
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copy2(target, rollback_guard)
    shutil.copy2(backup_path, target)
    _audit_request(db, request, "rollback_prompt_fragment", "prompt_fragment", parsed["fragment"], {
        "backup": backup_name,
        "before_hash": current_hash,
    })
    return {"ok": True, "fragment": parsed["fragment"], "backup": backup_name}


# ═══════════════════════════════════════════
# Model status / tests
# ═══════════════════════════════════════════

@router.get("/models/status")
def model_status(_auth=Depends(verify_admin)):
    from config import (
        NEW_API_BASE_URL, NEW_API_KEY, NEW_API_TIMEOUT,
        LLM_MODEL_REPLY, LLM_MODEL_FAST, LLM_MODEL_SMART,
        CLASSIFIER_API_URL, IMAGE_SUMMARY_API_URL,
    )
    from clients.model_registry import registry
    from clients.new_api_client import NewAPIClient

    models = []
    tracker = None
    try:
        tracker = NewAPIClient.get_failure_tracker()
    except Exception:
        tracker = None

    for item in registry.data.get("models", [])[:300]:
        model_id = str(item.get("id") or "")
        disabled = tracker.sync_is_disabled(model_id) if tracker is not None and model_id else False
        models.append({
            "name": model_id,
            "provider": item.get("provider", "new-api"),
            "tier": item.get("tier", ""),
            "base_url": NEW_API_BASE_URL,
            "available": not disabled,
            "disabled": disabled,
            "intelligence": item.get("intelligence", 0),
            "cost_input_1m": item.get("cost_input_1m", 0),
            "tags": item.get("tags", []),
            "timeout": NEW_API_TIMEOUT,
            "max_tokens": "",
            "temperature": "",
            "recent_error": "熔断冷却中" if disabled else "",
        })

    configured = [
        {"role": "主模型", "name": LLM_MODEL_REPLY},
        {"role": "快模型", "name": LLM_MODEL_FAST},
        {"role": "智能模型", "name": LLM_MODEL_SMART},
        {"role": "TimingGate", "name": "Qwen TimingGate", "base_url": CLASSIFIER_API_URL},
        {"role": "图片打标", "name": "Qwen Vision", "base_url": IMAGE_SUMMARY_API_URL},
    ]
    return {
        "base_url": NEW_API_BASE_URL,
        "api_key_configured": bool(NEW_API_KEY),
        "configured": configured,
        "models": models,
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


@router.get("/logs/{name}")
def read_log(name: str, lines: int = 200, level: str = "", q: str = "",
             since_bytes: int = 0, _auth=Depends(verify_admin)):
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

    max_lines = max(1, min(int(lines), 5000))
    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
        tail = deque(fh, maxlen=max_lines)
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


@router.get("/health")
def health():
    return {"ok": True, "time": datetime.now().isoformat()}
