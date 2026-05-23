"""构造 Web 预览用的 OpenAI-compatible tools schema。"""

from __future__ import annotations

import copy
import importlib
from typing import Any

from core.tool_registry import TOOL_METADATA, get_tool_def


PACKAGE_TOOL_CLASSES = {
    "sql_analysis": ("nanobot_kt.tools.sql_analysis", "SQLAnalysisTool"),
    "python_sandbox": ("nanobot_kt.tools.python_sandbox", "PythonSandboxTool"),
    "ai_daily": ("nanobot_kt.tools.ai_daily", "AiDailyTool"),
    "news_search": ("nanobot_kt.tools.news_search", "NewsSearchTool"),
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


def build_tool_schema(name: str) -> dict[str, Any]:
    """按工具名构造单个 OpenAI-compatible schema，供管理端展示。"""
    tool_name = str(name or "").strip()
    schema = _package_tool_schema(tool_name) or _builtin_tool_schema(tool_name) or _metadata_fallback_schema(tool_name)
    try:
        from core.prompt_v2.tool_templates import overlay_tool_schema_description
        schema = overlay_tool_schema_description(schema)
    except Exception:
        pass
    if tool_name in TOOL_METADATA:
        td = TOOL_METADATA[tool_name]
        schema["category"] = td.category
        schema["risk_level"] = td.risk_level
        schema["label"] = td.label
    return schema


def build_effective_tool_schemas(enabled: dict[str, bool]) -> list[dict[str, Any]]:
    """按当前启用工具构造预览 schema；memory subagent 保持元数据兜底。"""
    schemas: list[dict[str, Any]] = []
    for name in sorted(n for n, ok in (enabled or {}).items() if ok):
        schemas.append(build_tool_schema(name))
    return schemas
