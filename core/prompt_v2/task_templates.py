from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.prompt_v2.task_contracts import (
    TaskContractError,
    get_task_contract,
    validate_task_call_values,
    validate_task_template,
)
from core.prompt_v2.template_loader import PromptV2Template, split_frontmatter_text
from core.prompt_v2.template_registry import (
    first_existing_template_path,
    resolve_template_key,
)
from core.prompt_v2.variables import render_scoped_template

logger = logging.getLogger("nanobot.prompt_v2.task_templates")

TASK_USER_INSTRUCTION = "请根据上面的任务内容输出结果。"
TASK_PAYLOAD_MARKER = "[TaskPayload: 真实内容见下一条 user 消息]"


@dataclass(frozen=True)
class TaskTemplateSelection:
    task_key: str
    source: str
    template: PromptV2Template | None
    invalid_sources: tuple[str, ...] = ()


def _template_from_path(task_key: str, path: Path) -> PromptV2Template:
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter_text(raw)
    return PromptV2Template(
        prompt_key=task_key,
        path=path,
        frontmatter=frontmatter,
        body=body.strip(),
        raw=raw,
    )


def select_task_template(prompt_key: str) -> TaskTemplateSelection:
    task_key = resolve_template_key(prompt_key)
    contract = get_task_contract(task_key)
    if contract is not None and contract.render_mode == "code_fallback_only":
        return TaskTemplateSelection(task_key, "code_fallback", None)

    invalid_sources: list[str] = []
    for source, runtime in (("runtime", True), ("default", False)):
        path = first_existing_template_path(task_key, runtime=runtime)
        if path is None:
            continue
        try:
            template = _template_from_path(task_key, path)
            validate_task_template(task_key, template.body)
            return TaskTemplateSelection(
                task_key,
                source,
                template,
                tuple(invalid_sources),
            )
        except (OSError, UnicodeError, ValueError):
            invalid_sources.append(source)
    return TaskTemplateSelection(
        task_key,
        "code_fallback",
        None,
        tuple(invalid_sources),
    )


def inspect_live_task_templates() -> list[dict[str, Any]]:
    from core.prompt_v2.task_contracts import list_task_contract_keys

    return [
        {
            "task_key": task_key,
            "source": selection.source,
            "invalid_sources": list(selection.invalid_sources),
        }
        for task_key in list_task_contract_keys()
        for selection in [select_task_template(task_key)]
    ]


def _render_selection(selection: TaskTemplateSelection, values: dict[str, Any]) -> str:
    if selection.template is None:
        return ""
    return render_scoped_template(
        selection.template.prompt_key,
        selection.template.body,
        values,
    ).strip()


def render_task_prompt(prompt_key: str, values: dict[str, Any], *, fallback_text: str = "") -> str:
    try:
        render_values = dict(values or {})
        validate_task_call_values(prompt_key, render_values)
        selection = select_task_template(prompt_key)
        rendered = _render_selection(selection, render_values)
        return rendered or str(fallback_text or "")
    except (TaskContractError, OSError, UnicodeError, ValueError) as exc:
        logger.warning(
            "[PromptV2Task] render failed key=%s fallback=code error=%s",
            resolve_template_key(prompt_key),
            exc,
        )
        return str(fallback_text or "")


def render_task_messages(
    prompt_key: str,
    values: dict[str, Any],
    *,
    fallback_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    original_values = dict(values or {})
    try:
        contract = validate_task_call_values(prompt_key, original_values)
        selection = select_task_template(prompt_key)
        render_values = dict(original_values)
        if contract is not None and contract.render_mode == "system_with_user_ref":
            for variable in contract.payload_variables:
                render_values[variable] = TASK_PAYLOAD_MARKER
        rendered = _render_selection(selection, render_values)
    except (TaskContractError, OSError, UnicodeError, ValueError) as exc:
        logger.warning(
            "[PromptV2Task] message render failed key=%s fallback=code error=%s",
            resolve_template_key(prompt_key),
            exc,
        )
        rendered = ""
    if not rendered:
        return list(fallback_messages or [])
    user_content = str(
        original_values.get("pending_text")
        or original_values.get("message")
        or TASK_USER_INSTRUCTION
    )
    return [
        {"role": "system", "content": rendered},
        {"role": "user", "content": user_content},
    ]
