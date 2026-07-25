"""Admin Chat Config 路由。"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.session_config import DiscoveredChatStream, discover_chat_streams
from api.admin.common import audit_request, client_ip, verify_admin
from api.admin.runtime_routes import _runtime_snapshot
from core.chat_stream_identity import (
    ChatStreamIdentity,
    ChatStreamIdentityError,
    canonicalize_legacy_chat_stream_id,
    parse_canonical_chat_stream_id,
    resolve_chat_stream_identity,
)
from core.content_rules import (
    ContentRuleAction,
    ContentRuleMatchKind,
    ContentRuleScope,
    validate_web_content_rule,
)
from core.database import (
    AdminAuditLog,
    ChatStreamConfig,
    ContentBlockRule,
    UserBlockRule,
    get_db,
)
from core.session_guidance import (
    SessionGuidanceValidationError,
    normalize_session_guidance,
)
from core.time_utils import db_now_naive

router = APIRouter(tags=["admin-chat-config"])

_GROUP_LEARNING_REPLACEMENT = "/api/v1/admin/group-learning"
_LEGACY_LEARNING_FIELDS = (
    "enable_expression_learning",
    "enable_jargon_learning",
)


class BlockRuleCreate(BaseModel):
    user_id: str
    target_type: str = "private"
    group_id: str = ""
    rule_mode: str = "log_only"
    reason: str = ""


class BlockRuleUpdate(BaseModel):
    rule_mode: str | None = None
    reason: str | None = None
    enabled: int | None = None


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
    pattern: str | None = None
    match_type: str | None = None
    scope_type: str | None = None
    chat_stream_id: str | None = None
    no_reply: int | None = None
    no_learn: int | None = None
    no_context: int | None = None
    category: str | None = None
    reason: str | None = None
    enabled: int | None = None


class ContentBlockRuleTestRequest(BaseModel):
    message: str
    chat_stream_id: str = ""


class ConfigUpdate(BaseModel):
    talk_value: float | None = None
    mentioned_bot_reply: int | None = None
    use_expression: int | None = None
    enable_expression_learning: int | None = None
    enable_jargon_learning: int | None = None
    group_profile_mode: str | None = None
    enable_group_profile: int | None = None  # deprecated, 兼容旧调用方
    planner_smooth: int | None = None
    session_guidance: str | None = None


class ConfigUpsert(ConfigUpdate):
    platform: str
    chat_type: Literal["group", "private"]
    session_id: str
    session_guidance: str


def _validate_content_rule_payload(
    *,
    pattern: str,
    match_type: str,
    scope_type: str,
    chat_stream_id: str,
    no_reply: int,
    no_learn: int,
    no_context: int,
) -> None:
    flags = {
        "no_reply": no_reply,
        "no_learn": no_learn,
        "no_context": no_context,
    }
    if any(value not in {0, 1} for value in flags.values()):
        raise HTTPException(400, "内容规则动作字段只允许 0/1")
    actions = tuple(
        action
        for field, action in (
            ("no_reply", ContentRuleAction.NO_REPLY),
            ("no_learn", ContentRuleAction.NO_LEARN),
            ("no_context", ContentRuleAction.NO_CONTEXT),
        )
        if flags[field] == 1
    )
    try:
        validate_web_content_rule(
            pattern=pattern,
            match_kind=ContentRuleMatchKind(match_type),
            scope=ContentRuleScope(scope_type),
            scope_id=chat_stream_id,
            actions=actions,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _block_dict(r: UserBlockRule) -> dict:
    return {
        "id": r.id, "user_id": r.user_id, "target_type": r.target_type,
        "group_id": r.group_id, "rule_mode": r.rule_mode, "reason": r.reason,
        "enabled": r.enabled,
        "created_at": str(r.created_at) if r.created_at else "",
        "updated_at": str(r.updated_at) if r.updated_at else "",
    }


def _guidance_summary(text: object, updated_at: object = None) -> dict:
    raw = text if isinstance(text, str) else str(text or "")
    try:
        normalized = normalize_session_guidance(raw)
    except SessionGuidanceValidationError:
        # 历史脏数据仍可被治理，但列表和审计绝不回传正文。
        normalized = raw
    configured = bool(normalized)
    return {
        "session_guidance_configured": configured,
        "session_guidance_chars": len(normalized),
        "session_guidance_sha256": (
            hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if configured
            else ""
        ),
        "session_guidance_updated_at": _iso(updated_at),
    }


def _identity_summary(chat_stream_id: str) -> dict:
    try:
        identity = parse_canonical_chat_stream_id(chat_stream_id)
    except ChatStreamIdentityError:
        return {"platform": "", "chat_type": "", "session_id": ""}
    return {
        "platform": identity.platform,
        "chat_type": identity.chat_type,
        "session_id": identity.external_session_id,
    }


def _config_dict(r: ChatStreamConfig) -> dict:
    group_profile_mode = str(r.group_profile_mode or "off")
    result = {
        "chat_stream_id": r.chat_stream_id,
        "talk_value": r.talk_value,
        "mentioned_bot_reply": bool(r.mentioned_bot_reply),
        "use_expression": group_profile_mode == "on",
        "enable_expression_learning": False,
        "enable_jargon_learning": False,
        "group_profile_mode": group_profile_mode,
        "planner_smooth": r.planner_smooth,
        "legacy_group_learning_controls": {
            "lifecycle": "retired",
            "replacement": _GROUP_LEARNING_REPLACEMENT,
            "write_behavior": "rejected",
            "use_expression": {
                "lifecycle": "compatibility_alias",
                "canonical_field": "group_profile_mode",
            },
            "retired_fields": list(_LEGACY_LEARNING_FIELDS),
        },
    }
    result.update(_identity_summary(r.chat_stream_id))
    result.update(_guidance_summary(
        r.session_guidance,
        r.session_guidance_updated_at,
    ))
    return result


def _config_detail_dict(r: ChatStreamConfig) -> dict:
    result = _config_dict(r)
    result["session_guidance"] = str(r.session_guidance or "")
    return result


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
    result = {
        "chat_stream_id": sid,
        "talk_value": 0.5,
        "mentioned_bot_reply": True,
        "use_expression": False,
        "enable_expression_learning": False,
        "enable_jargon_learning": False,
        "group_profile_mode": "off",
        "planner_smooth": 3,
        "legacy_group_learning_controls": {
            "lifecycle": "retired",
            "replacement": _GROUP_LEARNING_REPLACEMENT,
            "write_behavior": "rejected",
            "use_expression": {
                "lifecycle": "compatibility_alias",
                "canonical_field": "group_profile_mode",
            },
            "retired_fields": list(_LEGACY_LEARNING_FIELDS),
        },
    }
    result.update(_identity_summary(sid))
    result.update(_guidance_summary(""))
    return result


def _config_default_detail(sid: str) -> dict:
    result = _config_default(sid)
    result["session_guidance"] = ""
    return result


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
    _validate_content_rule_payload(
        pattern=body.pattern,
        match_type=body.match_type,
        scope_type=body.scope_type,
        chat_stream_id=body.chat_stream_id,
        no_reply=body.no_reply,
        no_learn=body.no_learn,
        no_context=body.no_context,
    )
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
    only_disables_legacy_regex = (
        str(row.match_type or "") == "regex"
        and body.enabled == 0
        and all(
            getattr(body, field) is None
            for field in (
                "pattern",
                "match_type",
                "scope_type",
                "chat_stream_id",
                "no_reply",
                "no_learn",
                "no_context",
                "category",
                "reason",
            )
        )
    )
    if not only_disables_legacy_regex:
        _validate_content_rule_payload(
            pattern=(
                body.pattern
                if body.pattern is not None
                else str(row.pattern or "")
            ),
            match_type=(
                body.match_type
                if body.match_type is not None
                else str(row.match_type or "contains")
            ),
            scope_type=(
                body.scope_type
                if body.scope_type is not None
                else str(row.scope_type or "session")
            ),
            chat_stream_id=(
                body.chat_stream_id
                if body.chat_stream_id is not None
                else str(row.chat_stream_id or "")
            ),
            no_reply=(
                body.no_reply
                if body.no_reply is not None
                else int(row.no_reply or 0)
            ),
            no_learn=(
                body.no_learn
                if body.no_learn is not None
                else int(row.no_learn or 0)
            ),
            no_context=(
                body.no_context
                if body.no_context is not None
                else int(row.no_context or 0)
            ),
        )
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
    if not row.enabled:
        _validate_content_rule_payload(
            pattern=str(row.pattern or ""),
            match_type=str(row.match_type or "contains"),
            scope_type=str(row.scope_type or "session"),
            chat_stream_id=str(row.chat_stream_id or ""),
            no_reply=int(row.no_reply or 0),
            no_learn=int(row.no_learn or 0),
            no_context=int(row.no_context or 0),
        )
    row.enabled = 0 if row.enabled else 1
    db.commit()
    audit_request(db, request, "toggle_content_block_rule", "content_block_rule", rule_id)
    return _content_block_dict(row)


@router.get("/chat-streams")
def list_chat_streams(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """返回所有可无歧义管理的 canonical 会话 ID。"""
    discovered = discover_chat_streams(
        db,
        runtime_snapshot=_runtime_snapshot(),
    )
    return {
        "items": sorted(
            item.chat_stream_id
            for item in discovered
            if item.platform and item.chat_type
        ),
    }


def _discovery_metadata(item: DiscoveredChatStream) -> dict:
    return {
        "platform": item.platform,
        "chat_type": item.chat_type,
        "session_id": item.session_id,
        "runtime_session_id": item.runtime_session_id,
        "session_name": item.session_name,
        "identity_status": item.identity_status,
        "identity_conflict": item.identity_conflict,
        "legacy_aliases": list(item.legacy_aliases),
        "sources": list(item.sources),
    }


def _filter_config_items(
    items: list[dict],
    *,
    search: str,
    platform: str,
    chat_type: str,
    configured: int | None,
) -> list[dict]:
    normalized_search = str(search or "").strip().casefold()
    normalized_platform = str(platform or "").strip().lower()
    normalized_chat_type = str(chat_type or "").strip().lower()
    if normalized_chat_type and normalized_chat_type not in {"group", "private"}:
        raise HTTPException(status_code=422, detail="chat_type 只允许 group 或 private")
    if configured not in {None, 0, 1}:
        raise HTTPException(status_code=422, detail="configured 只允许 0 或 1")

    result: list[dict] = []
    for item in items:
        if normalized_search:
            searchable = " ".join((
                str(item.get("chat_stream_id") or ""),
                str(item.get("session_name") or ""),
            )).casefold()
            if normalized_search not in searchable:
                continue
        if normalized_platform and item.get("platform") != normalized_platform:
            continue
        if normalized_chat_type and item.get("chat_type") != normalized_chat_type:
            continue
        if configured is not None and bool(
            item.get("session_guidance_configured")
        ) is not bool(configured):
            continue
        result.append(item)
    return result


@router.get("/configs")
def list_configs(
    search: str = "",
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    effective: int = 0,
    platform: str = "",
    chat_type: str = "",
    configured: int | None = None,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    all_overrides = db.query(ChatStreamConfig).all()
    discovered = discover_chat_streams(
        db,
        runtime_snapshot=_runtime_snapshot(),
    )

    discovery_by_id = {item.chat_stream_id: item for item in discovered}
    for item in discovered:
        for alias in item.legacy_aliases:
            discovery_by_id.setdefault(alias, item)

    if effective:
        canonical_rows: dict[str, ChatStreamConfig] = {}
        for row in all_overrides:
            try:
                identity = parse_canonical_chat_stream_id(row.chat_stream_id)
            except ChatStreamIdentityError:
                continue
            canonical_rows[identity.chat_stream_id] = row

        items: list[dict] = []
        for stream in discovered:
            row = canonical_rows.get(stream.chat_stream_id)
            if row is not None:
                config = _config_dict(row)
                config["has_override"] = True
                config["source"] = "db"
            else:
                config = _config_default(stream.chat_stream_id)
                config["has_override"] = False
                config["source"] = "default"
            config.update(_discovery_metadata(stream))
            items.append(config)
    else:
        items = []
        for row in sorted(all_overrides, key=lambda value: value.chat_stream_id):
            config = _config_dict(row)
            stream = discovery_by_id.get(row.chat_stream_id)
            if stream is not None:
                config.update(_discovery_metadata(stream))
                config["chat_stream_id"] = row.chat_stream_id
            items.append(config)

    items = _filter_config_items(
        items,
        search=search,
        platform=platform,
        chat_type=chat_type,
        configured=configured,
    )
    total = len(items)
    page_items = items[(page - 1) * limit: page * limit]
    return {"items": page_items, "total": total, "page": page}


def _identity_http_error(exc: ChatStreamIdentityError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": exc.code, "message": str(exc)},
    )


def _resolve_upsert_identity(body: ConfigUpsert) -> ChatStreamIdentity:
    try:
        return resolve_chat_stream_identity(
            platform=body.platform,
            chat_type=body.chat_type,
            session_id=body.session_id,
        )
    except ChatStreamIdentityError as exc:
        raise _identity_http_error(exc) from exc


def _resolve_dynamic_identity(chat_stream_id: str) -> ChatStreamIdentity:
    try:
        return parse_canonical_chat_stream_id(chat_stream_id)
    except ChatStreamIdentityError as canonical_error:
        canonical = canonicalize_legacy_chat_stream_id(chat_stream_id)
        if canonical is None:
            raise _identity_http_error(canonical_error) from canonical_error
        try:
            return parse_canonical_chat_stream_id(canonical)
        except ChatStreamIdentityError as exc:
            raise _identity_http_error(exc) from exc


def _normalized_guidance_or_422(value: str) -> str:
    try:
        return normalize_session_guidance(value)
    except SessionGuidanceValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


def _apply_config_update(
    db: Session,
    *,
    identity: ChatStreamIdentity,
    body: ConfigUpdate,
    request: Request,
) -> dict:
    retired_writes = [
        field
        for field in _LEGACY_LEARNING_FIELDS
        if getattr(body, field) is not None
    ]
    if retired_writes:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "legacy_group_learning_control_retired",
                "message": "旧表达／黑话学习开关已退役",
                "fields": retired_writes,
                "lifecycle": "retired",
                "replacement": _GROUP_LEARNING_REPLACEMENT,
            },
        )
    normalized_guidance = (
        _normalized_guidance_or_422(body.session_guidance)
        if body.session_guidance is not None
        else None
    )
    normalized_group_profile_mode: str | None = None
    if body.group_profile_mode is not None:
        normalized_group_profile_mode = str(body.group_profile_mode).strip()
        if normalized_group_profile_mode not in {"off", "preview", "on"}:
            raise HTTPException(
                status_code=400,
                detail=f"invalid group_profile_mode: {normalized_group_profile_mode}",
            )
    compatibility_mode: str | None = None
    if body.use_expression is not None:
        compatibility_mode = "on" if bool(body.use_expression) else "off"
        if (
            normalized_group_profile_mode is not None
            and normalized_group_profile_mode != compatibility_mode
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "group_profile_compatibility_conflict",
                    "message": (
                        "use_expression 与 group_profile_mode 冲突"
                    ),
                    "canonical_field": "group_profile_mode",
                },
            )
        normalized_group_profile_mode = compatibility_mode
        from core.lifecycle import record_compatibility_usage

        record_compatibility_usage(
            "schema.chat_config_use_expression"
        )
    if body.enable_group_profile is not None:
        deprecated_mode = (
            "on" if bool(body.enable_group_profile) else "off"
        )
        if (
            normalized_group_profile_mode is not None
            and normalized_group_profile_mode != deprecated_mode
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "group_profile_compatibility_conflict",
                    "message": (
                        "enable_group_profile 与 group_profile_mode 冲突"
                    ),
                    "canonical_field": "group_profile_mode",
                },
            )
        normalized_group_profile_mode = deprecated_mode

    try:
        row = db.query(ChatStreamConfig).filter(
            ChatStreamConfig.chat_stream_id == identity.chat_stream_id,
        ).first()
        old_guidance = str(row.session_guidance or "") if row is not None else ""
        if row is None:
            row = ChatStreamConfig(chat_stream_id=identity.chat_stream_id)
            db.add(row)

        changed_fields: list[str] = []
        safe_values: dict[str, object] = {}
        int_fields = (
            "mentioned_bot_reply",
            "planner_smooth",
        )
        for field in ("talk_value",) + int_fields:
            value = getattr(body, field, None)
            if value is None:
                continue
            stored_value = int(value) if field in int_fields else value
            setattr(row, field, stored_value)
            changed_fields.append(field)
            safe_values[field] = stored_value

        if normalized_group_profile_mode is not None:
            row.group_profile_mode = normalized_group_profile_mode
            row.use_expression = int(
                normalized_group_profile_mode == "on"
            )
            changed_fields.append("group_profile_mode")
            safe_values["group_profile_mode"] = normalized_group_profile_mode
            if compatibility_mode is not None:
                changed_fields.append("use_expression")
                safe_values["use_expression"] = bool(
                    row.use_expression
                )
        else:
            row.use_expression = int(
                str(row.group_profile_mode or "off") == "on"
            )

        if normalized_guidance is not None:
            row.session_guidance = normalized_guidance
            row.session_guidance_updated_at = db_now_naive()
            changed_fields.append("session_guidance")

        new_guidance = (
            normalized_guidance
            if normalized_guidance is not None
            else old_guidance
        )
        old_summary = _guidance_summary(old_guidance)
        new_summary = _guidance_summary(new_guidance)
        audit_detail = {
            "chat_stream_id": identity.chat_stream_id,
            "updated_fields": sorted(set(changed_fields)),
            "config_values": safe_values,
            "session_guidance_changed": (
                normalized_guidance is not None
                and old_guidance != normalized_guidance
            ),
            "old_chars": old_summary["session_guidance_chars"],
            "old_sha256": old_summary["session_guidance_sha256"],
            "new_chars": new_summary["session_guidance_chars"],
            "new_sha256": new_summary["session_guidance_sha256"],
        }
        db.add(AdminAuditLog(
            action="update_config",
            target_type="config",
            target_id=identity.chat_stream_id,
            detail_json=json.dumps(
                audit_detail,
                ensure_ascii=False,
                sort_keys=True,
            ),
            ip_address=client_ip(request),
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _config_detail_dict(row)


@router.put("/configs")
def upsert_config(
    body: ConfigUpsert,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    identity = _resolve_upsert_identity(body)
    return _apply_config_update(
        db,
        identity=identity,
        body=body,
        request=request,
    )


@router.get("/configs/{chat_stream_id:path}")
def get_config(chat_stream_id: str, db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    identity = _resolve_dynamic_identity(chat_stream_id)
    row = db.query(ChatStreamConfig).filter(
        ChatStreamConfig.chat_stream_id == identity.chat_stream_id,
    ).first()
    if not row:
        return _config_default_detail(identity.chat_stream_id)
    return _config_detail_dict(row)


@router.put("/configs/{chat_stream_id:path}")
def update_config(
    chat_stream_id: str,
    body: ConfigUpdate,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    identity = _resolve_dynamic_identity(chat_stream_id)
    return _apply_config_update(
        db,
        identity=identity,
        body=body,
        request=request,
    )


@router.delete("/configs/{chat_stream_id:path}")
def delete_config(
    chat_stream_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    identity = _resolve_dynamic_identity(chat_stream_id)
    row = db.query(ChatStreamConfig).filter(
        ChatStreamConfig.chat_stream_id == identity.chat_stream_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")
    try:
        old_summary = _guidance_summary(row.session_guidance)
        audit_detail = {
            "chat_stream_id": identity.chat_stream_id,
            "session_guidance_changed": bool(
                old_summary["session_guidance_configured"],
            ),
            "old_chars": old_summary["session_guidance_chars"],
            "old_sha256": old_summary["session_guidance_sha256"],
            "new_chars": 0,
            "new_sha256": "",
        }
        db.delete(row)
        db.add(AdminAuditLog(
            action="delete_config",
            target_type="config",
            target_id=identity.chat_stream_id,
            detail_json=json.dumps(
                audit_detail,
                ensure_ascii=False,
                sort_keys=True,
            ),
            ip_address=client_ip(request),
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ok": True}
