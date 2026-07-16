from __future__ import annotations

import json
from typing import Any


class TemplateContentValidationError(ValueError):
    """模板原始字节无法满足其类型对应的运行合同。"""


def _decode_utf8(template_key: str, content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TemplateContentValidationError(
            f"模板内容无效 {template_key}: 必须是 UTF-8"
        ) from exc


def _validate_frontmatter(template_key: str, raw: str) -> tuple[dict[str, Any], str]:
    from core.prompt_v2.template_loader import split_frontmatter_text

    if raw.startswith("---"):
        lines = raw.splitlines()
        if not any(line.strip() == "---" for line in lines[1:]):
            raise TemplateContentValidationError(
                f"模板内容无效 {template_key}: frontmatter 未闭合"
            )
    frontmatter, body = split_frontmatter_text(raw)
    kind = str(frontmatter.get("kind") or "").strip()
    category = template_key.split("/", 1)[0]
    allowed_kinds = {
        "chat": {"", "chat"},
        "tasks": {"", "task", "tool"},
        "tools": {"", "tool"},
    }.get(category, {"", "tool"})
    if kind not in allowed_kinds:
        raise TemplateContentValidationError(
            f"模板内容无效 {template_key}: frontmatter kind={kind!r} 与路径不一致"
        )
    tool_name = str(frontmatter.get("tool_name") or "").strip()
    if tool_name:
        from core.prompt_v2.template_registry import classify_template

        expected_tool_name = classify_template(template_key, {}).tool_name
        if expected_tool_name and tool_name != expected_tool_name:
            raise TemplateContentValidationError(
                f"模板内容无效 {template_key}: frontmatter tool_name 与模板 key 不一致"
            )
    return frontmatter, body


def validate_template_bytes(
    template_key: str,
    content: bytes,
    *,
    require_runtime_contract: bool = True,
) -> None:
    """在模板进入基线、计划或 runtime 前验证其完整运行合同。"""
    from core.prompt_v2.template_registry import resolve_template_key

    key = resolve_template_key(template_key)
    raw = _decode_utf8(key, content)
    try:
        if key == "chat/flow":
            from core.prompt_v2.flow import validate_flow, validate_runtime_contract

            value = json.loads(raw)
            if not isinstance(value, dict):
                raise TemplateContentValidationError(
                    f"模板内容无效 {key}: flow 顶层必须是对象"
                )
            normalized = validate_flow(value)
            if require_runtime_contract:
                validate_runtime_contract(normalized)
            return

        _frontmatter, body = _validate_frontmatter(key, raw)
        if not require_runtime_contract:
            return

        from core.prompt_v2.variables import validate_scoped_template
        validate_scoped_template(key, body)
        if key.startswith("tasks/"):
            from core.prompt_v2.task_contracts import validate_task_template

            validate_task_template(key, body)
    except TemplateContentValidationError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TemplateContentValidationError(
            f"模板内容无效 {key}: {exc}"
        ) from exc
