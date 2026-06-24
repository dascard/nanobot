"""Admin Chat Config 路由。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.admin.common import audit, audit_request, client_ip, verify_admin
from api.admin.runtime_routes import _runtime_snapshot
from core.database import (
    ChatLog,
    ChatStreamConfig,
    ContentBlockRule,
    User,
    UserBlockRule,
    get_db,
)

router = APIRouter(tags=["admin-chat-config"])


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


def _iso(v) -> str:
    return v.isoformat(sep=" ", timespec="seconds") if v else ""


def _raw_group_id(group_id: str) -> str:
    raw = str(group_id or "").strip()
    if raw.startswith("group_"):
        return raw.removeprefix("group_")
    if raw.startswith("qq:") and raw.endswith(":group"):
        return raw.removeprefix("qq:").removesuffix(":group")
    return raw


def _group_stream_id(group_id: str) -> str:
    raw = _raw_group_id(group_id)
    return f"qq:{raw}:group" if raw else ""


def _content_block_dict(r: ContentBlockRule) -> dict:
    return {
        "id": r.id, "pattern": r.pattern, "match_type": r.match_type,
        "scope_type": r.scope_type, "chat_stream_id": r.chat_stream_id or "",
        "no_reply": bool(r.no_reply), "no_learn": bool(r.no_learn),
        "no_context": bool(r.no_context), "category": r.category,
        "enabled": bool(r.enabled), "reason": r.reason or "",
        "created_at": _iso(r.created_at), "updated_at": _iso(r.updated_at),
    }


def _config_default(sid: str) -> dict:
    return {"chat_stream_id": sid, "talk_value": 0.5, "mentioned_bot_reply": True,
            "use_expression": True, "enable_expression_learning": True,
            "enable_jargon_learning": True, "group_profile_mode": "off",
            "planner_smooth": 3}


@router.get("/block-rules")
def list_block_rules(page: int = 1, limit: int = 20, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    q = db.query(UserBlockRule).order_by(UserBlockRule.id.desc())
    total = q.count()
    rows = q.offset((page - 1) * limit).limit(limit).all()
    return {"total": total, "page": page, "items": [_block_dict(r) for r in rows]}


@router.post("/block-rules")
def create_block_rule(body: BlockRuleCreate, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    rule = UserBlockRule(**body.model_dump())
    db.add(rule)
    db.commit()
    audit_request(db, request, "create_block_rule", "block_rule", rule.id, body.model_dump())
    return _block_dict(rule)


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


@router.put("/block-rules/{rule_id}")
def update_block_rule(rule_id: int, body: BlockRuleUpdate, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(UserBlockRule).filter(UserBlockRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    updates = {}
    for field in ("rule_mode", "reason", "enabled"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(row, field, val)
            updates[field] = val
    db.commit()
    audit_request(db, request, "update_block_rule", "block_rule", rule_id, updates)
    return _block_dict(row)


@router.delete("/block-rules/{rule_id}")
def delete_block_rule(rule_id: int, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(UserBlockRule).filter(UserBlockRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    audit_request(db, request, "delete_block_rule", "block_rule", rule_id)
    return {"ok": True}


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
    db.add(rule)
    db.commit()
    audit_request(db, request, "create_content_block_rule", "content_block_rule", rule.id)
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
            setattr(row, field, val)
            updates[field] = val
    for field in int_fields:
        val = getattr(body, field, None)
        if val is not None:
            setattr(row, field, int(val))
            updates[field] = int(val)
    db.commit()
    audit_request(db, request, "update_content_block_rule", "content_block_rule", rule_id, updates)
    return _content_block_dict(row)


@router.delete("/content-block-rules/{rule_id}")
def delete_content_block_rule(rule_id: int, request: Request,
                               db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(ContentBlockRule).filter(ContentBlockRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    audit_request(db, request, "delete_content_block_rule", "content_block_rule", rule_id)
    return {"ok": True}


@router.post("/content-block-rules/{rule_id}/toggle")
def toggle_content_block_rule(rule_id: int, request: Request,
                               db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(ContentBlockRule).filter(ContentBlockRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.enabled = 0 if row.enabled else 1
    db.commit()
    audit_request(db, request, "toggle_content_block_rule", "content_block_rule", rule_id)
    return _content_block_dict(row)


@router.get("/chat-streams")
def list_chat_streams(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """返回所有已知的 chat_stream_id，供局部规则下拉选择。"""
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


@router.put("/configs/{chat_stream_id:path}")
def update_config(chat_stream_id: str, body: ConfigUpdate, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(ChatStreamConfig).filter(ChatStreamConfig.chat_stream_id == chat_stream_id).first()
    if not row:
        row = ChatStreamConfig(chat_stream_id=chat_stream_id)
        db.add(row)
        db.flush()
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
    audit(db, "update_config", "config", chat_stream_id, updates, ip_address=client_ip(request))
    return _config_dict(row)


@router.delete("/configs/{chat_stream_id:path}")
def delete_config(chat_stream_id: str, request: Request, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    row = db.query(ChatStreamConfig).filter(ChatStreamConfig.chat_stream_id == chat_stream_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")
    db.delete(row)
    db.commit()
    audit_request(db, request, "delete_config", "config", chat_stream_id)
    return {"ok": True}
