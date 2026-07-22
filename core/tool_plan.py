"""请求级工具可用性计划。

ToolPlan 是运行时工具事实源：prompt、API tools schema、执行前校验都从
同一份计划读取，避免在请求处理中改写 KT registry。
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from core.runtime_tool_service import build_runtime_tool_prompt, resolve_effective_tools
from core.tool_schema_preview import build_effective_tool_schemas


class ToolPlanExecutionError(RuntimeError):
    """工具不在当前请求可执行集合内。"""


def _tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function.get("name") or "")
    if tool.get("name"):
        return str(tool.get("name") or "")
    return ""


def _stable_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_wire_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """把管理端 schema 收窄为 OpenAI-compatible wire 形态。"""

    if not isinstance(schema, dict):
        raise ValueError("tool schema must be an object")
    if schema.get("type") != "function":
        raise ValueError("tool schema type must be function")
    function = schema.get("function")
    if not isinstance(function, dict):
        raise ValueError("tool schema function must be an object")
    name = str(function.get("name") or "").strip()
    if not name:
        raise ValueError("tool schema function.name required")
    parameters = function.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError(f"tool schema parameters must be an object: {name}")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": str(function.get("description") or ""),
            "parameters": copy.deepcopy(parameters),
        },
    }


def _build_effective_tool_schemas(enabled: dict[str, bool], db: Any = None) -> list[dict[str, Any]]:
    if db is None:
        return build_effective_tool_schemas(enabled)
    try:
        signature = inspect.signature(build_effective_tool_schemas)
        accepts_db = "db" in signature.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in signature.parameters.values()
        )
    except (TypeError, ValueError):
        accepts_db = True
    if accepts_db:
        return build_effective_tool_schemas(enabled, db=db)
    return build_effective_tool_schemas(enabled)


@dataclass(frozen=True)
class ToolPlan:
    enabled: dict[str, bool]
    disabled: dict[str, str]
    sent_tool_names: frozenset[str]
    _sent_tool_schemas: tuple[dict[str, Any], ...] = field(repr=False)
    executable_tool_names: frozenset[str]
    runtime_tool_prompt: str
    sha256: str
    hidden_framework_tool_names: frozenset[str] = field(default_factory=lambda: frozenset({"skill"}))

    @property
    def allowed(self) -> set[str]:
        """兼容旧 FinalToolSet 调用方。"""
        return set(self.sent_tool_names)

    @property
    def sent_tools(self) -> list[str]:
        """兼容旧 FinalToolSet 调用方。"""
        return sorted(self.sent_tool_names)

    @property
    def sent_tool_schemas(self) -> tuple[dict[str, Any], ...]:
        """返回请求级 wire schema 的防御性副本。"""

        return tuple(copy.deepcopy(schema) for schema in self._sent_tool_schemas)

    def can_execute(self, tool_name: str) -> bool:
        return str(tool_name or "").strip() in self.executable_tool_names

    def disabled_reason(self, tool_name: str) -> str:
        return self.disabled.get(str(tool_name or "").strip(), "当前请求未启用")

    def ensure_executable(self, tool_name: str) -> None:
        name = str(tool_name or "").strip()
        if not name or name not in self.executable_tool_names:
            raise ToolPlanExecutionError(
                f"Tool '{name}' is disabled for this request: {self.disabled_reason(name)}"
            )

    @classmethod
    def from_effective_tools(
        cls,
        *,
        enabled: dict[str, bool],
        disabled: dict[str, str] | None = None,
        chat_type: str = "group",
        tool_schemas: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        db: Any = None,
    ) -> "ToolPlan":
        enabled_map = {str(k): bool(v) for k, v in (enabled or {}).items()}
        disabled_map = {str(k): str(v) for k, v in (disabled or {}).items()}
        sent_names = frozenset(sorted(k for k, ok in enabled_map.items() if ok))
        if tool_schemas is not None:
            schemas = list(tool_schemas)
        else:
            schemas = _build_effective_tool_schemas(enabled_map, db=db)
        normalized_schemas = tuple(
            normalize_wire_tool_schema(tool)
            for tool in schemas
        )
        sent_schemas = tuple(
            tool
            for tool in normalized_schemas
            if _tool_name(tool) in sent_names
        )
        executable_names = frozenset(sent_names)
        runtime_prompt = build_runtime_tool_prompt(enabled_map, disabled_map, chat_type)
        sha256 = _stable_sha256({
            "enabled": enabled_map,
            "disabled": disabled_map,
            "sent_tool_names": sorted(sent_names),
            "sent_tool_schemas": list(sent_schemas),
            "executable_tool_names": sorted(executable_names),
            "runtime_tool_prompt": runtime_prompt,
        })
        return cls(
            enabled=enabled_map,
            disabled=disabled_map,
            sent_tool_names=sent_names,
            _sent_tool_schemas=sent_schemas,
            executable_tool_names=executable_names,
            runtime_tool_prompt=runtime_prompt,
            sha256=sha256,
        )


def build_tool_plan(
    *,
    chat_type: str = "group",
    group_id: str = "",
    user_id: str = "",
    platform: str = "",
    session_id: str = "",
    runtime_preset: str = "full",
    db: Any = None,
) -> ToolPlan:
    enabled, disabled = resolve_effective_tools(
        chat_type=chat_type,
        group_id=group_id,
        user_id=user_id,
        platform=platform,
        session_id=session_id,
        runtime_preset=runtime_preset,
        db=db,
    )
    return ToolPlan.from_effective_tools(
        enabled=enabled,
        disabled=disabled,
        chat_type=chat_type,
        db=db,
    )


_CURRENT_TOOL_PLAN: ContextVar[ToolPlan | None] = ContextVar(
    "nanobot_tool_plan",
    default=None,
)


def get_current_tool_plan() -> ToolPlan | None:
    return _CURRENT_TOOL_PLAN.get()


def set_current_tool_plan(plan: ToolPlan | None) -> Token[ToolPlan | None]:
    return _CURRENT_TOOL_PLAN.set(plan)


def reset_current_tool_plan(token: Token[ToolPlan | None]) -> None:
    _CURRENT_TOOL_PLAN.reset(token)


@contextmanager
def tool_plan_scope(plan: ToolPlan | None) -> Iterator[None]:
    token = set_current_tool_plan(plan)
    try:
        yield
    finally:
        reset_current_tool_plan(token)
