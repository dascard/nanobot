from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable

from core.prompt_v2.section_renderer import sha256_text
from core.prompt_v2.template_loader import load_template
from core.prompt_v2.template_registry import (
    default_template_dir,
    list_template_keys,
    resolve_template_key,
    runtime_template_dir,
)
from core.prompt_v2.variables import render_scoped_template

logger = logging.getLogger("nanobot.prompt_v2.tool_templates")

@dataclass(frozen=True)
class ToolTemplatePolicy:
    template_key: str
    tool_name: str
    body: str
    path: str
    source: str
    sha256: str


def _template_keys() -> set[str]:
    return set(_template_keys_for_dirs(str(default_template_dir()), str(runtime_template_dir())))


@lru_cache(maxsize=32)
def _template_keys_for_dirs(_default_dir: str, _runtime_dir: str) -> tuple[str, ...]:
    return tuple(list_template_keys())


def _kind_for(key: str, frontmatter: dict[str, Any]) -> str:
    raw = str(frontmatter.get("kind") or "").strip()
    if raw:
        return raw
    if key.startswith("chat/") or key.startswith("chat_") or key == "identity_context":
        return "chat"
    return "tool"


def _tool_name_for(key: str, frontmatter: dict[str, Any], kind: str) -> str:
    raw = str(frontmatter.get("tool_name") or "").strip()
    if raw:
        return raw
    if kind != "tool":
        return ""
    if key.startswith("tools/"):
        parts = key.split("/")
        if len(parts) > 1:
            return parts[1]
    if key == "reply_contract_retry":
        return "reply"
    return key


def _policy_from_key(key: str, values: dict[str, Any] | None = None) -> ToolTemplatePolicy | None:
    try:
        canonical = resolve_template_key(key)
        template = load_template(canonical)
    except Exception:
        return None

    frontmatter = template.frontmatter
    kind = _kind_for(canonical, frontmatter)
    tool_name = _tool_name_for(canonical, frontmatter, kind)
    if kind not in {"tool", "task"} or not tool_name:
        return None

    rendered = render_scoped_template(canonical, template.body, values or {}).strip()
    return ToolTemplatePolicy(
        template_key=canonical,
        tool_name=tool_name,
        body=rendered,
        path=str(template.path),
        source="runtime" if str(template.path).startswith(str(runtime_template_dir())) else "default",
        sha256=sha256_text(rendered),
    )


def _get_tool_template_policy_uncached(tool_name: str, values: dict[str, Any] | None = None) -> ToolTemplatePolicy | None:
    name = str(tool_name or "").strip()
    if not name:
        return None
    from core.tool_registration import get_tool_registration

    registration = get_tool_registration(name)
    if registration is not None:
        preferred_keys = tuple(
            key
            for key in registration.prompt_template_keys
            if key.endswith("/usage")
        )
        candidate_keys = (
            *preferred_keys,
            *registration.prompt_template_keys,
        )
    else:
        # 兼容管理端预览尚未登记的临时模板；生产 ToolPlan 不会消费此分支。
        candidate_keys = (f"tools/{name}/usage", name)
    for key in candidate_keys:
        direct = _policy_from_key(key, values)
        if direct and direct.tool_name == name:
            return direct
    for key in sorted(_template_keys()):
        if not key.startswith(f"tools/{name}/") or not key.endswith("/usage"):
            continue
        policy = _policy_from_key(key, values)
        if policy and policy.tool_name == name:
            return policy
    return None


@lru_cache(maxsize=256)
def _cached_tool_template_policy(tool_name: str, _default_dir: str, _runtime_dir: str) -> ToolTemplatePolicy | None:
    return _get_tool_template_policy_uncached(tool_name, None)


def clear_tool_template_policy_cache() -> None:
    _template_keys_for_dirs.cache_clear()
    _cached_tool_template_policy.cache_clear()


def get_tool_template_policy(tool_name: str, values: dict[str, Any] | None = None) -> ToolTemplatePolicy | None:
    name = str(tool_name or "").strip()
    if not name:
        return None
    if values:
        return _get_tool_template_policy_uncached(name, values)
    return _cached_tool_template_policy(name, str(default_template_dir()), str(runtime_template_dir()))


def render_tool_execution_template(
    template_key: str,
    values: dict[str, Any] | None = None,
    *,
    fallback: str = "",
    expected_tool_name: str = "",
) -> str:
    """渲染实际工具内部 LLM 调用使用的 V2 模板。

    工具执行路径用这个入口，WebUI 编辑页和运行时读取同一套默认/运行时模板。
    fallback 只用于模板缺失或历史运行目录不完整时保底。
    """
    key = str(template_key or "").strip()
    if not key:
        return fallback
    try:
        canonical = resolve_template_key(key)
        template = load_template(canonical)
        frontmatter = template.frontmatter or {}
        kind = _kind_for(canonical, frontmatter)
        tool_name = _tool_name_for(canonical, frontmatter, kind)
        if expected_tool_name and tool_name and tool_name != expected_tool_name:
            logger.warning(
                "Prompt V2 tool template %s tool_name=%s does not match expected=%s",
                key,
                tool_name,
                expected_tool_name,
            )
            return fallback
        if kind not in {"tool", "task"}:
            logger.warning("Prompt V2 template %s is not a tool/task template: kind=%s", key, kind)
            return fallback
        rendered = render_scoped_template(canonical, template.body, values or {}).strip()
        return rendered or fallback
    except Exception as exc:
        logger.warning("Failed to render Prompt V2 tool execution template %s: %s", key, exc)
        return fallback


def collect_tool_template_resolutions(
    tool_schemas: Iterable[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """收集工具 usage 模板来源，只写入 trace，不进入模型可见描述。"""

    resolutions: dict[str, dict[str, Any]] = {}
    names: set[str] = set()
    for schema in tool_schemas or []:
        function = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(function, dict):
            name = str(function.get("name") or "").strip()
            if name:
                names.add(name)
    for name in sorted(names):
        from core.tool_registration import get_tool_registration

        registration = get_tool_registration(name)
        if registration is None:
            continue
        usage_key = next(
            (
                key
                for key in registration.prompt_template_keys
                if key.endswith("/usage")
            ),
            "",
        )
        if not usage_key:
            continue
        try:
            template = load_template(usage_key)
        except (FileNotFoundError, ValueError):
            continue
        if template.resolution is None:
            continue
        resolutions[f"tool_schema:{name}"] = template.resolution.to_dict()
    return resolutions


def overlay_tool_schema_description(tool_schema: dict[str, Any], *, max_chars: int = 1600) -> dict[str, Any]:
    schema = copy.deepcopy(tool_schema or {})
    function = schema.get("function")
    if not isinstance(function, dict):
        return schema
    name = str(function.get("name") or "").strip()
    if not name:
        return schema
    policy = get_tool_template_policy(name)
    if not policy or not policy.body:
        return schema

    body = policy.body
    if len(body) > max_chars:
        body = f"{body[:max_chars].rstrip()}\n...[truncated:{len(policy.body)} chars]"
    # usage 模板是模型可见工具说明的唯一正文；静态 description 只作为模板缺失兜底。
    function["description"] = body
    return schema
