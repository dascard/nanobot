"""构造 Web 预览用的 OpenAI-compatible tools schema。"""

from __future__ import annotations

import copy
import importlib
import json
import logging
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from core.tool_registry import TOOL_METADATA, get_tool_def

logger = logging.getLogger("nanobot.tool_schema")

TOOL_SCHEMA_OVERRIDE_PREFIX = "tool.schema_override."


PACKAGE_TOOL_CLASSES = {
    "sql_analysis": ("nanobot_kt.tools.sql_analysis", "SQLAnalysisTool"),
    "python_sandbox": ("nanobot_kt.tools.python_sandbox", "PythonSandboxTool"),
    "ai_daily": ("nanobot_kt.tools.ai_daily", "AiDailyTool"),
    "image_summary": ("nanobot_kt.tools.image_summary", "ImageSummaryTool"),
    "persona_update": ("nanobot_kt.tools.persona_update", "PersonaUpdateTool"),
    "schedule_task": ("nanobot_kt.tools.schedule_task", "ScheduleTaskTool"),
    "group_analysis": ("nanobot_kt.tools.group_analysis", "GroupAnalysisTool"),
    "sticker_search": ("nanobot_kt.tools.sticker_search", "StickerSearchTool"),
    "reply": ("nanobot_kt.tools.reply", "ReplyTool"),
    "no_reply": ("nanobot_kt.tools.reply", "NoReplyTool"),
}


def _background_schema() -> dict[str, str]:
    return {
        "type": "boolean",
        "description": "If true, run in background. Results delivered later, not immediately.",
    }


def _with_background_option(parameters: dict[str, Any]) -> dict[str, Any]:
    params = copy.deepcopy(parameters or {"type": "object", "properties": {}})
    if not isinstance(params.get("properties"), dict):
        params["properties"] = {}
    params["properties"]["run_in_background"] = _background_schema()
    return params


def _package_tool_schema(name: str) -> dict[str, Any] | None:
    target = PACKAGE_TOOL_CLASSES.get(name)
    if not target:
        return None
    module_name, class_name = target
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    tool = cls()
    parameters = {}
    if hasattr(tool, "get_parameters_schema"):
        parameters = tool.get_parameters_schema() or {}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": str(getattr(tool, "description", "") or ""),
            "parameters": _with_background_option(parameters),
        },
        "source": "package",
    }


def _builtin_tool_schema(name: str) -> dict[str, Any] | None:
    try:
        from kohakuterrarium.llm.tools import _BUILTIN_SCHEMAS  # type: ignore
    except Exception:
        _BUILTIN_SCHEMAS = {}
    if name not in _BUILTIN_SCHEMAS:
        return None
    td = get_tool_def(name)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": td.description if td else f"KT built-in tool: {name}",
            "parameters": _with_background_option(_BUILTIN_SCHEMAS[name]),
        },
        "source": "kt_builtin",
    }


def _metadata_fallback_schema(name: str) -> dict[str, Any]:
    td = get_tool_def(name)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": td.description if td else f"Tool: {name}",
            "parameters": _with_background_option({
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Input content for the tool",
                    }
                },
            }),
        },
        "source": "metadata_fallback",
    }


def _tool_schema_override_key(name: str) -> str:
    return f"{TOOL_SCHEMA_OVERRIDE_PREFIX}{str(name or '').strip()}"


def _validate_tool_schema(tool_name: str, schema: dict[str, Any]) -> dict[str, Any]:
    name = str(tool_name or "").strip()
    if not name:
        raise ValueError("tool_name required")
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")
    if schema.get("type") != "function":
        raise ValueError("schema.type must be function")
    function = schema.get("function")
    if not isinstance(function, dict):
        raise ValueError("schema.function must be an object")
    function_name = str(function.get("name") or "").strip()
    if function_name != name:
        raise ValueError(f"function.name must be {name}")
    parameters = function.get("parameters")
    if parameters is None:
        function["parameters"] = {"type": "object", "properties": {}}
    elif not isinstance(parameters, dict):
        raise ValueError("function.parameters must be an object")
    return copy.deepcopy(schema)


def load_tool_schema_override(db, name: str) -> dict[str, Any] | None:
    """读取工具 schema 运行时覆盖。"""
    if db is None:
        return None
    tool_name = str(name or "").strip()
    if not tool_name:
        return None
    try:
        from core.database import SystemSetting

        row = db.query(SystemSetting).filter(SystemSetting.key == _tool_schema_override_key(tool_name)).first()
        if not row or not row.value:
            return None
        parsed = json.loads(row.value)
        return _validate_tool_schema(tool_name, parsed)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid tool schema override JSON for %s: %s", tool_name, exc)
        return None
    except SQLAlchemyError as exc:
        logger.warning("Failed to load tool schema override for %s: %s", tool_name, exc)
        return None


def save_tool_schema_override(db, name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """保存工具 schema 覆盖。调用方负责 commit。"""
    if db is None:
        raise ValueError("db required")
    tool_name = _ensure_known_tool(name)
    normalized = _validate_tool_schema(tool_name, schema)
    from core.database import SystemSetting

    value = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    key = _tool_schema_override_key(tool_name)
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not row:
        row = SystemSetting(key=key, value=value, description=f"{tool_name} runtime tool schema override")
        db.add(row)
    else:
        row.value = value
        row.description = f"{tool_name} runtime tool schema override"
    return normalized


def delete_tool_schema_override(db, name: str) -> bool:
    if db is None:
        raise ValueError("db required")
    tool_name = _ensure_known_tool(name)
    from core.database import SystemSetting

    row = db.query(SystemSetting).filter(SystemSetting.key == _tool_schema_override_key(tool_name)).first()
    if not row:
        return False
    db.delete(row)
    return True


def _add_tool_metadata(schema: dict[str, Any], tool_name: str) -> dict[str, Any]:
    result = copy.deepcopy(schema or {})
    if tool_name in TOOL_METADATA:
        td = TOOL_METADATA[tool_name]
        result["category"] = td.category
        result["risk_level"] = td.risk_level
        result["label"] = td.label
    return result


def _base_tool_schema(name: str) -> dict[str, Any]:
    tool_name = str(name or "").strip()
    return _package_tool_schema(tool_name) or _builtin_tool_schema(tool_name) or _metadata_fallback_schema(tool_name)


def _ensure_known_tool(name: str) -> str:
    tool_name = str(name or "").strip()
    if not tool_name:
        raise ValueError("tool_name required")
    if tool_name not in TOOL_METADATA:
        raise ValueError(f"unknown tool: {tool_name}")
    return tool_name


def build_tool_schema(name: str, *, db=None, include_template_overlay: bool = True) -> dict[str, Any]:
    """按工具名构造单个 OpenAI-compatible schema，供运行时和管理端展示。"""
    tool_name = _ensure_known_tool(name)
    override = load_tool_schema_override(db, tool_name)
    schema = override or _base_tool_schema(tool_name)
    if override:
        schema["source"] = "runtime_override"
    try:
        from core.prompt_v2.tool_templates import overlay_tool_schema_description

        if include_template_overlay:
            schema = overlay_tool_schema_description(schema)
    except Exception:
        pass
    return _add_tool_metadata(schema, tool_name)


def build_tool_schema_config(db, name: str) -> dict[str, Any]:
    """返回 WebUI schema 编辑所需的默认、覆盖、可编辑和生效 schema。"""
    tool_name = _ensure_known_tool(name)
    override = load_tool_schema_override(db, tool_name)
    default_schema = _add_tool_metadata(_base_tool_schema(tool_name), tool_name)
    editable_schema = _add_tool_metadata(override or default_schema, tool_name)
    effective_schema = build_tool_schema(tool_name, db=db)
    return {
        "tool": tool_name,
        "default_schema": default_schema,
        "override_schema": _add_tool_metadata(override, tool_name) if override else None,
        "editable_schema": editable_schema,
        "tool_schema": effective_schema,
        "override_present": override is not None,
    }


def build_effective_tool_schemas(enabled: dict[str, bool], *, db=None) -> list[dict[str, Any]]:
    """按当前启用工具构造预览 schema；memory subagent 保持元数据兜底。"""
    schemas: list[dict[str, Any]] = []
    for name in sorted(n for n, ok in (enabled or {}).items() if ok):
        if name not in TOOL_METADATA:
            logger.warning("Skip unknown runtime tool schema: %s", name)
            continue
        schemas.append(build_tool_schema(name, db=db))
    return schemas
