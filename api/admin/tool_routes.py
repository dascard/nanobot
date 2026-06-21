"""Admin Tools 路由。"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import audit, client_ip, verify_admin
from core.database import ChatLog, ChatStreamConfig, ConversationTurn, SystemSetting, User, get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(prefix="/tools", tags=["admin-tools"])


class ToolUpdateBody(BaseModel):
    private_default: Optional[bool] = None
    private_superuser_default: Optional[bool] = None
    group_default: Optional[bool] = None
    lightweight_default: Optional[bool] = None


class ToolOverrideBody(BaseModel):
    scope_type: str  # "group" | "user" | "chat_type" | "platform"
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


def _iso(v) -> str:
    return v.isoformat(sep=" ", timespec="seconds") if v else ""


def _raw_group_id(group_id: str) -> str:
    raw = str(group_id or "").strip()
    if raw.startswith("group_"):
        return raw.removeprefix("group_")
    if raw.startswith("qq:") and raw.endswith(":group"):
        return raw.removeprefix("qq:").removesuffix(":group")
    return raw


def _runtime_snapshot() -> dict:
    try:
        from core.timing_runtime import get_group_runtime
        runtime = get_group_runtime()
        snapshot = getattr(runtime, "snapshot_states", lambda: {})()
        return snapshot if isinstance(snapshot, dict) else {}
    except Exception:
        return {}


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


@router.get("")
async def list_tools(chat_type: str = "group", group_id: str = "",
                     user_id: str = "", platform: str = "qq",
                     runtime_preset: str = "full",
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
        normalize_tool_platform,
        resolve_effective_tools,
        resolve_lightweight_default,
        resolve_tool_default,
        normalize_runtime_preset,
    )

    runtime_preset = normalize_runtime_preset(runtime_preset)
    chat_type = normalize_tool_chat_type(chat_type)
    platform = normalize_tool_platform(platform) or "qq"
    configured_enabled, configured_disabled = resolve_effective_tools(
        chat_type=chat_type, group_id=group_id, user_id=user_id,
        platform=platform, runtime_preset="full", db=db,
    )
    runtime_enabled, runtime_disabled = resolve_effective_tools(
        chat_type=chat_type, group_id=group_id, user_id=user_id,
        platform=platform, runtime_preset=runtime_preset, db=db,
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
    elif platform:
        override_scope_type = "platform"
        override_scope_id = platform
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
            "runtime_preset": runtime_preset,
            "platform": platform}


@router.get("/targets")
def list_tool_targets(scope_type: str = "group", search: str = "", limit: int = 50,
                      db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """列出工具覆盖可选择的真实群聊/私聊目标。"""
    if scope_type == "platform":
        scope = "platform"
    elif scope_type == "user":
        scope = "user"
    else:
        scope = "group"
    search_text = str(search or "").strip().lower()
    max_items = max(1, min(int(limit or 50), 100))
    candidates: dict[str, dict] = {}

    if scope == "platform":
        from core.database import ToolOverride
        from core.runtime_tool_service import normalize_tool_platform

        def add_platform_candidate(platform_id: str, name: str, source: str) -> None:
            clean_id = normalize_tool_platform(platform_id)
            if not clean_id:
                return
            clean_name = str(name or "").strip() or clean_id
            label = f"{clean_name} ({clean_id})"
            haystack = f"{clean_id} {clean_name} {label}".lower()
            if search_text and search_text not in haystack:
                return
            old = candidates.get(clean_id)
            if old:
                sources = set(old.get("_sources") or [])
                if source:
                    sources.add(source)
                old["_sources"] = sorted(sources)
                old["source"] = "+".join(old["_sources"])
                return
            candidates[clean_id] = {
                "id": clean_id,
                "label": label,
                "name": clean_name,
                "scope_type": "platform",
                "source": source,
                "recent_at": "",
                "_sources": [source] if source else [],
            }

        for platform_id, name in (("qq", "QQ"), ("web", "Web"), ("synergy", "Synergy")):
            add_platform_candidate(platform_id, name, "builtin")
        for row in db.query(ToolOverride.scope_id).filter(
            ToolOverride.scope_type == "platform"
        ).distinct().all():
            add_platform_candidate(row[0], row[0], "tool_overrides")

        items = sorted(candidates.values(), key=lambda item: item["label"])[:max_items]
        for item in items:
            item.pop("_sources", None)
        return {"scope_type": scope, "items": items}

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


@router.get("/effective")
def get_effective_tools(chat_type: str = "group", group_id: str = "", user_id: str = "",
                        platform: str = "qq",
                        runtime_preset: str = "full",
                        db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    """查看给定上下文的实际生效工具列表。"""
    from core.runtime_tool_service import (
        build_runtime_tool_prompt,
        normalize_tool_platform,
        normalize_runtime_preset,
        resolve_effective_tools,
    )
    from core.tool_schema_preview import build_effective_tool_schemas
    runtime_preset = normalize_runtime_preset(runtime_preset)
    platform = normalize_tool_platform(platform) or "qq"
    enabled, disabled = resolve_effective_tools(
        chat_type=chat_type, group_id=group_id, user_id=user_id,
        platform=platform, runtime_preset=runtime_preset, db=db,
    )
    prompt = build_runtime_tool_prompt(enabled, disabled, chat_type)
    tool_schemas = build_effective_tool_schemas(enabled, db=db)
    return {
        "chat_type": chat_type, "group_id": group_id, "user_id": user_id,
        "platform": platform,
        "runtime_preset": runtime_preset,
        "enabled": {k: v for k, v in enabled.items() if v},
        "disabled": {k: v for k, v in disabled.items()},
        "prompt": prompt,
        "tool_schemas": tool_schemas,
    }


@router.get("/decisions")
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
            "platform": getattr(r, "platform", "") or "",
            "runtime_preset": r.runtime_preset,
            "effective_tools": _json.loads(r.effective_tools_json or "[]") if isinstance(r.effective_tools_json or "", str) else [],
            "disabled_reasons": reasons,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })
    return {"items": items, "total": len(items)}


@router.get("/{tool_name}/schema")
def get_tool_schema_override(tool_name: str, db: Session = Depends(get_db),
                             _auth=Depends(verify_admin)):
    from core.tool_schema_preview import build_tool_schema_config

    try:
        return build_tool_schema_config(db, tool_name)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/{tool_name}/schema")
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
    audit(db, "tool_schema_override", "tool", tool_name, {"schema": result["editable_schema"]},
          ip_address=client_ip(request))
    return result


@router.delete("/{tool_name}/schema")
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
        audit(db, "tool_schema_override_delete", "tool", tool_name,
              ip_address=client_ip(request))
    return build_tool_schema_config(db, tool_name)


@router.put("/{tool_name}")
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
        audit(db, "tool_default_update", "tool", tool_name, updates,
              ip_address=client_ip(request))
    return {"ok": True, "tool": tool_name}


@router.put("/{tool_name}/override")
def set_tool_override(tool_name: str, body: ToolOverrideBody,
                      request: Request, db: Session = Depends(get_db),
                      _auth=Depends(verify_admin)):
    """设置工具作用域覆盖——upsert ToolOverride。"""
    from core.tool_registry import get_tool_def
    from core.database import ToolOverride
    from core.runtime_tool_service import normalize_tool_platform

    scope_type = str(body.scope_type or "").strip().lower()
    scope_id = str(body.scope_id or "").strip()
    if scope_type not in {"group", "user", "chat_type", "platform"}:
        raise HTTPException(400, "scope_type must be group/user/chat_type/platform")
    if scope_type == "chat_type" and scope_id not in {"private", "private_superuser", "group"}:
        raise HTTPException(400, "chat_type scope_id must be private/private_superuser/group")
    if scope_type == "platform":
        scope_id = normalize_tool_platform(scope_id)
        if not scope_id:
            raise HTTPException(400, "scope_id required for platform scope")
    if scope_type in {"group", "user"} and not scope_id:
        raise HTTPException(400, "scope_id required for group/user scope")

    td = get_tool_def(tool_name)
    if not td:
        raise HTTPException(404, f"unknown tool: {tool_name}")
    if td.force_enabled:
        raise HTTPException(400, f"{tool_name} is force_enabled, cannot override")

    row = db.query(ToolOverride).filter(
        ToolOverride.tool_name == tool_name,
        ToolOverride.scope_type == scope_type,
        ToolOverride.scope_id == scope_id,
    ).first()
    if not row:
        row = ToolOverride(tool_name=tool_name, scope_type=scope_type,
                           scope_id=scope_id)
        db.add(row)
    row.enabled = 1 if body.enabled else 0
    row.reason = body.reason
    db.commit()
    audit(db, "tool_override", "tool", tool_name,
          {"scope_type": scope_type, "scope_id": scope_id,
           "enabled": row.enabled, "reason": body.reason},
          ip_address=client_ip(request))
    return {"ok": True, "tool": tool_name}


@router.delete("/{tool_name}/override")
def delete_tool_override(tool_name: str, request: Request, scope_type: str = "",
                         scope_id: str = "", db: Session = Depends(get_db),
                         _auth=Depends(verify_admin)):
    """删除工具作用域覆盖。"""
    from core.database import ToolOverride
    from core.runtime_tool_service import normalize_tool_platform
    scope_type = str(scope_type or "").strip().lower()
    scope_id = str(scope_id or "").strip()
    if scope_type == "platform":
        scope_id = normalize_tool_platform(scope_id)
    row = db.query(ToolOverride).filter(
        ToolOverride.tool_name == tool_name,
        ToolOverride.scope_type == scope_type,
        ToolOverride.scope_id == scope_id,
    ).first()
    if not row:
        raise HTTPException(404, "Override not found")
    db.delete(row)
    db.commit()
    audit(db, "tool_override_delete", "tool", tool_name,
          {"scope_type": scope_type, "scope_id": scope_id},
          ip_address=client_ip(request))
    return {"ok": True}
