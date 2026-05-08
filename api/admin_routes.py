"""WebUI 管理 API——Sticker/Block/Config/DB 管理。prefix=/api/v1/admin，认证复用 NANOBOT_API_TOKEN。"""

import json
import logging
from datetime import datetime
from hmac import compare_digest
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import (
    get_db,
    StickerMemory, ChatStreamConfig, UserBlockRule, SystemSetting, AdminAuditLog,
)
from config import NANOBOT_API_TOKEN

logger = logging.getLogger("nanobot.admin")
router = APIRouter(prefix="/api/v1/admin")

# ── Auth ──

def verify_admin(authorization: str = Header(default="")) -> str:
    if not NANOBOT_API_TOKEN:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    token = authorization.replace("Bearer ", "").strip()
    if not token or not compare_digest(token, NANOBOT_API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid token")
    return "admin"


def _audit(db: Session, action: str, target_type: str = "", target_id: str = "", detail: dict | None = None):
    try:
        db.add(AdminAuditLog(
            action=action, target_type=target_type, target_id=str(target_id),
            detail_json=json.dumps(detail or {}, ensure_ascii=False),
        ))
        db.commit()
    except Exception:
        pass


# ── Auth check endpoint ──

@router.get("/me")
def admin_me(_auth=Depends(verify_admin)):
    return {"ok": True, "user": "admin"}


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
        "planner_smooth": r.planner_smooth,
    }


# ═══════════════════════════════════════════
# StickerMemory CRUD
# ═══════════════════════════════════════════

@router.post("/stickers")
def create_sticker(body: StickerCreate, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
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
    _audit(db, "create_sticker", "sticker", sticker.get("id"), {
        "name": body.name, "status": body.status,
        "stream_id": sticker.get("chat_stream_id", ""),
        "description": body.description[:80] if body.description else "",
        "tags": body.tags[:5],
    })
    return sticker


@router.get("/stickers")
def list_stickers(
    search: str = "", status: str = "", page: int = 1, limit: int = 20,
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
    total = q.count()
    rows = q.order_by(StickerMemory.id.desc()).offset((page - 1) * limit).limit(limit).all()
    return {"total": total, "page": page, "items": [_sticker_dict(r) for r in rows]}


@router.get("/stickers/{sticker_id}")
def get_sticker(sticker_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    return _sticker_dict(row)


@router.put("/stickers/{sticker_id}")
def update_sticker(sticker_id: int, body: StickerUpdate, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
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
    _audit(db, "update_sticker", "sticker", sticker_id, updates)
    return _sticker_dict(row)


@router.post("/stickers/{sticker_id}/enable")
def enable_sticker(sticker_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.status = "active"; db.commit()
    _audit(db, "enable_sticker", "sticker", sticker_id)
    return {"ok": True}


@router.post("/stickers/{sticker_id}/disable")
def disable_sticker(sticker_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.status = "disabled"; db.commit()
    _audit(db, "disable_sticker", "sticker", sticker_id)
    return {"ok": True}


@router.delete("/stickers/{sticker_id}")
def delete_sticker(sticker_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.status = "deleted"; db.commit()
    _audit(db, "soft_delete_sticker", "sticker", sticker_id)
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
def create_block_rule(body: BlockRuleCreate, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    rule = UserBlockRule(**body.model_dump())
    db.add(rule); db.commit()
    _audit(db, "create_block_rule", "block_rule", rule.id, body.model_dump())
    return _block_dict(rule)


@router.put("/block-rules/{rule_id}")
def update_block_rule(rule_id: int, body: BlockRuleUpdate, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(UserBlockRule).filter(UserBlockRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    updates = {}
    for field in ("rule_mode", "reason", "enabled"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(row, field, val); updates[field] = val
    db.commit()
    _audit(db, "update_block_rule", "block_rule", rule_id, updates)
    return _block_dict(row)


@router.delete("/block-rules/{rule_id}")
def delete_block_rule(rule_id: int, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(UserBlockRule).filter(UserBlockRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row); db.commit()
    _audit(db, "delete_block_rule", "block_rule", rule_id)
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
            "enable_jargon_learning": True, "planner_smooth": 3}


@router.put("/configs/{chat_stream_id:path}")
def update_config(chat_stream_id: str, body: ConfigUpdate, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
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
    db.commit()
    _audit(db, "update_config", "config", chat_stream_id, updates)
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
        return {"content": fh.read()}


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


# ═══════════════════════════════════════════
# Audit logs + DB backup
# ═══════════════════════════════════════════

@router.get("/audit-logs")
def list_audit_logs(page: int = 1, limit: int = 50,
                    db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    q = db.query(AdminAuditLog).order_by(AdminAuditLog.id.desc())
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


@router.get("/health")
def health():
    return {"ok": True, "time": datetime.now().isoformat()}
