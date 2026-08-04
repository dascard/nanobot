"""请求级工具可用性计划。

ToolPlan 是运行时工具事实源：prompt、API tools schema、执行前校验都从
同一份计划读取，避免在请求处理中改写 KT registry。
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from core.runtime_tool_service import (
    build_sandbox_tool_schema_guidance,
    resolve_effective_tools,
)
from core.tool_registration import TOOL_REGISTRATION_REGISTRY
from core.tool_schema_preview import build_effective_tool_schemas


class ToolPlanExecutionError(RuntimeError):
    """工具不在当前请求可执行集合内。"""


SKILL_LOCK_PENDING_REASON = "当前请求尚未冻结可见 Skill 版本锁"


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


def _attach_sandbox_profile_guidance(
    schemas: list[dict[str, Any]],
    *,
    enabled: dict[str, bool],
    chat_type: str,
    platform: str,
    session_id: str,
    db: Any,
) -> list[dict[str, Any]]:
    """把请求级 Sandbox Profile 只附到一个实际发送的 Sandbox schema。"""

    guidance = build_sandbox_tool_schema_guidance(
        enabled,
        chat_type,
        platform=platform,
        session_id=session_id,
        db=db,
    )
    result = copy.deepcopy(schemas)
    if not guidance:
        return result
    preferred = (
        "sandbox_exec",
        "workspace_edit",
        "workspace_read",
        "workspace_search",
        "workspace_write",
        "workspace_list",
        "asset_import",
        "asset_publish",
    )
    by_name = {
        _tool_name(schema): schema
        for schema in result
    }
    target = next((by_name[name] for name in preferred if name in by_name), None)
    if target is None:
        return result
    function = target.get("function")
    if isinstance(function, dict):
        description = str(function.get("description") or "").rstrip()
        function["description"] = (
            f"{description}\n\n{guidance}".strip()
        )
    return result


@dataclass(frozen=True)
class ToolPlan:
    enabled: dict[str, bool]
    disabled: dict[str, str]
    sent_tool_names: frozenset[str]
    _sent_tool_schemas: tuple[dict[str, Any], ...] = field(repr=False)
    executable_tool_names: frozenset[str]
    runtime_tool_prompt: str
    sha256: str
    registration_generation: int
    registration_sha256: str
    hidden_framework_tool_names: frozenset[str] = field(default_factory=frozenset)

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

    def validate_arguments(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> None:
        """按本轮冻结 Schema 复验 Hook 或模型产生的工具参数。"""

        name = str(tool_name or "").strip()
        self.ensure_executable(name)
        if not isinstance(arguments, Mapping):
            raise ToolPlanExecutionError(
                f"Tool '{name}' arguments must be an object"
            )
        schema = next(
            (
                item.get("function", {}).get("parameters")
                for item in self._sent_tool_schemas
                if _tool_name(item) == name
            ),
            None,
        )
        if not isinstance(schema, Mapping):
            raise ToolPlanExecutionError(
                f"Tool '{name}' has no frozen argument schema"
            )
        try:
            frozen_schema = dict(schema)
            Draft202012Validator.check_schema(frozen_schema)
            validator = Draft202012Validator(frozen_schema)
        except SchemaError as exc:
            raise ToolPlanExecutionError(
                f"Tool '{name}' frozen argument schema is invalid"
            ) from exc
        errors = sorted(
            validator.iter_errors(dict(arguments)),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if errors:
            first = errors[0]
            path = ".".join(str(item) for item in first.absolute_path) or "<root>"
            raise ToolPlanExecutionError(
                f"Tool '{name}' arguments violate frozen schema at {path}: "
                f"{first.validator}"
            )

    @classmethod
    def from_effective_tools(
        cls,
        *,
        enabled: dict[str, bool],
        disabled: dict[str, str] | None = None,
        chat_type: str = "group",
        tool_schemas: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        platform: str = "",
        session_id: str = "",
        db: Any = None,
    ) -> "ToolPlan":
        enabled_map = {str(k): bool(v) for k, v in (enabled or {}).items()}
        disabled_map = {str(k): str(v) for k, v in (disabled or {}).items()}
        sent_names = frozenset(sorted(k for k, ok in enabled_map.items() if ok))
        if tool_schemas is not None:
            schemas = list(tool_schemas)
        else:
            schemas = _build_effective_tool_schemas(enabled_map, db=db)
        schemas = _attach_sandbox_profile_guidance(
            schemas,
            enabled=enabled_map,
            chat_type=chat_type,
            platform=platform,
            session_id=session_id,
            db=db,
        )
        normalized_schemas = tuple(sorted(
            (
                normalize_wire_tool_schema(tool)
                for tool in schemas
            ),
            key=_tool_name,
        ))
        sent_schemas = tuple(
            tool
            for tool in normalized_schemas
            if _tool_name(tool) in sent_names
        )
        executable_names = frozenset(sent_names)
        runtime_prompt = ""
        registration_snapshot = (
            TOOL_REGISTRATION_REGISTRY.registry_snapshot
        )
        sha256 = _stable_sha256({
            "enabled": enabled_map,
            "disabled": disabled_map,
            "sent_tool_names": sorted(sent_names),
            "sent_tool_schemas": list(sent_schemas),
            "executable_tool_names": sorted(executable_names),
            "runtime_tool_prompt": runtime_prompt,
            "registration_generation": registration_snapshot.generation,
            "registration_sha256": registration_snapshot.sha256,
        })
        return cls(
            enabled=enabled_map,
            disabled=disabled_map,
            sent_tool_names=sent_names,
            _sent_tool_schemas=sent_schemas,
            executable_tool_names=executable_names,
            runtime_tool_prompt=runtime_prompt,
            sha256=sha256,
            registration_generation=registration_snapshot.generation,
            registration_sha256=registration_snapshot.sha256,
        )


_ADDITIONAL_TOOL_SCHEMAS: ContextVar[
    tuple[dict[str, Any], ...]
] = ContextVar(
    "nanobot_additional_tool_schemas",
    default=(),
)


def get_additional_tool_schemas() -> tuple[dict[str, Any], ...]:
    """返回当前请求由外部 Adapter 注入的附加工具 Schema。"""

    return tuple(
        copy.deepcopy(schema)
        for schema in _ADDITIONAL_TOOL_SCHEMAS.get()
    )


@contextmanager
def additional_tool_schemas_scope(
    schemas: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> Iterator[None]:
    """仅在当前异步上下文内公布附加工具，防止跨会话污染。"""

    normalized = tuple(normalize_wire_tool_schema(schema) for schema in schemas)
    token = _ADDITIONAL_TOOL_SCHEMAS.set(normalized)
    try:
        yield
    finally:
        _ADDITIONAL_TOOL_SCHEMAS.reset(token)


def extend_tool_plan(
    plan: ToolPlan,
    tool_schemas: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    chat_type: str,
    platform: str,
    session_id: str,
    db: Any = None,
) -> ToolPlan:
    """将可信 Adapter 提供的动态工具并入当前请求级计划。"""

    normalized = tuple(normalize_wire_tool_schema(schema) for schema in tool_schemas)
    if not normalized:
        return plan
    names = {_tool_name(schema) for schema in normalized}
    names.discard("")
    collision = sorted(names & set(plan.enabled))
    if collision:
        raise ValueError(
            "附加工具与已注册工具重名：" + ", ".join(collision)
        )
    enabled = dict(plan.enabled)
    enabled.update({name: True for name in names})
    disabled = dict(plan.disabled)
    for name in names:
        disabled.pop(name, None)
    return ToolPlan.from_effective_tools(
        enabled=enabled,
        disabled=disabled,
        chat_type=chat_type,
        tool_schemas=[*plan.sent_tool_schemas, *normalized],
        platform=platform,
        session_id=session_id,
        db=db,
    )


def enable_registered_tool(
    plan: ToolPlan,
    tool_schema: dict[str, Any],
    *,
    chat_type: str,
    platform: str,
    session_id: str,
    db: Any = None,
) -> ToolPlan:
    """由受信服务端决策启用一个已注册工具，并冻结其请求级 schema。"""

    normalized = normalize_wire_tool_schema(tool_schema)
    name = _tool_name(normalized)
    registration = TOOL_REGISTRATION_REGISTRY.get(name)
    if registration is None or registration.lifecycle != "active":
        raise ValueError(f"动态工具未处于 active 注册状态：{name}")
    enabled = dict(plan.enabled)
    enabled[name] = True
    disabled = dict(plan.disabled)
    disabled.pop(name, None)
    schemas = [
        schema
        for schema in plan.sent_tool_schemas
        if _tool_name(schema) != name
    ]
    schemas.append(normalized)
    return ToolPlan.from_effective_tools(
        enabled=enabled,
        disabled=disabled,
        chat_type=chat_type,
        tool_schemas=schemas,
        platform=platform,
        session_id=session_id,
        db=db,
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
    extra_disabled: Mapping[str, str] | None = None,
    session_goal_mode: str = "",
    session_plan_writable: bool = False,
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
    # 请求来源级硬禁用(如定时任务会话防递归):只减不增,
    # 在效果表之后、计划冻结之前应用。
    skill_hard_disabled = False
    for raw_name, raw_reason in (extra_disabled or {}).items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        enabled[name] = False
        disabled[name] = str(raw_reason or "来源上下文禁用").strip()
        if name == "skill":
            skill_hard_disabled = True
    # Skill 只能在服务端解析出请求级精确版本锁后启用，配置覆盖不得提前暴露。
    enabled["skill"] = False
    if not skill_hard_disabled:
        disabled["skill"] = SKILL_LOCK_PENDING_REASON
    goal_mode = str(session_goal_mode or "").strip().lower()
    if goal_mode not in {"", "plan", "execute"}:
        raise ValueError("session_goal_mode 必须是 plan/execute 或空")
    if goal_mode:
        enabled["session_plan_read"] = True
        disabled.pop("session_plan_read", None)
        if goal_mode == "plan" and session_plan_writable:
            enabled["session_plan_write"] = True
            disabled.pop("session_plan_write", None)
        else:
            enabled["session_plan_write"] = False
            disabled["session_plan_write"] = "当前 Session Goal 状态禁止修改计划"
    plan = ToolPlan.from_effective_tools(
        enabled=enabled,
        disabled=disabled,
        chat_type=chat_type,
        platform=platform,
        session_id=session_id,
        db=db,
    )
    return extend_tool_plan(
        plan,
        get_additional_tool_schemas(),
        chat_type=chat_type,
        platform=platform,
        session_id=session_id,
        db=db,
    )


def restrict_tool_plan(
    plan: ToolPlan,
    allowed_tool_names: set[str] | frozenset[str],
    *,
    disabled_reason: str = "当前执行阶段不允许调用",
) -> ToolPlan:
    """把现有计划只减不增地收窄到指定工具集合。"""

    allowed = {
        str(name or "").strip()
        for name in allowed_tool_names
        if str(name or "").strip()
    }
    enabled = dict(plan.enabled)
    disabled = dict(plan.disabled)
    for name in set(enabled) | set(plan.executable_tool_names):
        if name in allowed and name in plan.executable_tool_names:
            enabled[name] = True
            disabled.pop(name, None)
        else:
            enabled[name] = False
            disabled[name] = disabled_reason
    return ToolPlan.from_effective_tools(
        enabled=enabled,
        disabled=disabled,
        tool_schemas=list(plan.sent_tool_schemas),
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
