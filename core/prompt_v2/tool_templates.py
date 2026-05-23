from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any

from core.prompt_v2.section_renderer import sha256_text
from core.prompt_v2.template_loader import load_template
from core.prompt_v2.template_registry import list_template_keys, resolve_template_key, runtime_template_dir
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
    return set(list_template_keys())


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


def get_tool_template_policy(tool_name: str, values: dict[str, Any] | None = None) -> ToolTemplatePolicy | None:
    name = str(tool_name or "").strip()
    if not name:
        return None
    for key in (f"tools/{name}/usage", name):
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


def format_enabled_tool_templates(
    enabled: dict[str, bool],
    values: dict[str, Any] | None = None,
    *,
    max_chars_per_tool: int = 2600,
) -> str:
    sections: list[str] = []
    for name in sorted(n for n, ok in (enabled or {}).items() if ok):
        policy = get_tool_template_policy(name, values)
        if not policy or not policy.body:
            continue
        body = policy.body
        if len(body) > max_chars_per_tool:
            body = f"{body[:max_chars_per_tool].rstrip()}\n...[truncated:{len(policy.body)} chars sha256:{policy.sha256}]"
        sections.append(
            "\n".join([
                f"[ToolTemplate:{name}]",
                f"source: {policy.source} path: {policy.path} sha256: {policy.sha256[:12]}",
                body,
                f"[/ToolTemplate:{name}]",
            ])
        )
    return "\n\n".join(sections)


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

    original = str(function.get("description") or "").strip()
    body = policy.body
    if len(body) > max_chars:
        body = f"{body[:max_chars].rstrip()}\n...[truncated:{len(policy.body)} chars sha256:{policy.sha256}]"
    marker = f"[V2ToolTemplate:{policy.template_key} sha256:{policy.sha256[:12]}]\n{body}"
    function["description"] = f"{original}\n\n{marker}" if original else marker
    return schema
