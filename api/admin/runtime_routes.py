"""Admin Runtime / Overview 路由。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from api.admin.sticker_routes import _sticker_dict
from core.database import ChatLog, ChatStreamConfig, StickerMemory, User, UserBlockRule, get_db
from core.time_utils import db_now_naive

router = APIRouter(tags=["admin-runtime"])


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
        return max(0, int((db_now_naive() - v).total_seconds()))
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


def _block_dict(r: UserBlockRule) -> dict:
    return {
        "id": r.id, "user_id": r.user_id, "target_type": r.target_type,
        "group_id": r.group_id, "rule_mode": r.rule_mode, "reason": r.reason,
        "enabled": r.enabled,
        "created_at": str(r.created_at) if r.created_at else "",
        "updated_at": str(r.updated_at) if r.updated_at else "",
    }


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


@router.get("/overview")
def overview(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    from config import agent_link_token_diagnostic

    now = db_now_naive()
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

    from core.settings_service import settings

    return {
        "time": now.isoformat(timespec="seconds"),
        "service": {"name": "Nanobot Server", "ok": True},
        "models": {
            "main": settings.get("model.reply", ""),
            "fast": settings.get("model.fast", ""),
            "smart": settings.get("model.smart", ""),
            "timing_gate": "Qwen TimingGate",
        },
        "credentials": {
            "agent_link": agent_link_token_diagnostic(),
        },
        "counters": counters,
        "timing": _timing_stats(timing_events),
        "health": health,
    }


@router.get("/groups")
def list_groups(limit: int = 100, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    now = db_now_naive()
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
            "session_name": (
                (user.name if user else "")
                or (latest_msg.session_name if latest_msg else "")
                or rt.get("session_name", "")
            ),
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
