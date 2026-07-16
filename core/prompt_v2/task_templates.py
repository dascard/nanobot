from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.prompt_v2.flow_storage import read_regular_bytes
from core.prompt_v2.task_contracts import (
    TaskContractError,
    get_task_contract,
    get_task_invocation_for_template,
    get_task_invocation_spec,
    list_task_contract_keys,
    list_task_invocation_specs,
    validate_task_call_values,
    validate_task_invocation_specs,
    validate_task_template,
)
from core.prompt_v2.template_loader import PromptV2Template, split_frontmatter_text
from core.prompt_v2.template_registry import (
    first_existing_template_path,
    list_template_keys,
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


class TaskInvocationError(TaskContractError):
    """任务 invocation 无法生成满足合同的输入。"""


class TaskTemplateUnavailableError(TaskInvocationError):
    """任务模板缺失或所有候选均不满足合同。"""


class TaskTemplateRenderError(TaskInvocationError):
    """任务模板存在但无法生成有效内容。"""


@dataclass(frozen=True)
class RenderedTaskTemplate:
    task_key: str
    role: Literal["system", "user"]
    content: str
    source: str
    path: str
    version: Any


@dataclass(frozen=True)
class TaskPairRenderResult:
    system: RenderedTaskTemplate
    user: RenderedTaskTemplate

    @property
    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system.content},
            {"role": "user", "content": self.user.content},
        ]


def _template_from_path(task_key: str, path: Path) -> PromptV2Template:
    raw_bytes = read_regular_bytes(path)
    if raw_bytes is None:  # pragma: no cover - missing_ok=False 保证不会发生
        raise FileNotFoundError(path)
    raw = raw_bytes.decode("utf-8")
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
    _validate_task_registry()

    manifest = get_task_invocation_manifest()
    records: list[dict[str, Any]] = []
    for task_key in list_task_contract_keys():
        contract = get_task_contract(task_key)
        selection = select_task_template(task_key)
        if (
            contract is not None
            and contract.template_failure_policy == "runtime_default_fail_closed"
            and selection.template is None
        ):
            invalid = ",".join(selection.invalid_sources) or "missing"
            raise TaskTemplateUnavailableError(
                f"task {task_key} template unavailable: {invalid}"
            )
        records.append({
            "task_key": task_key,
            "source": selection.source,
            "invalid_sources": list(selection.invalid_sources),
            "invocation": manifest[task_key],
        })
    return records


def get_task_invocation_manifest() -> dict[str, str]:
    wrapper_names = {
        "prompt": "render_task_prompt",
        "messages": "render_task_messages",
        "paired_messages": "render_task_pair",
        "code_fallback_only": "code_fallback_only",
    }
    return {
        key: wrapper_names[spec.render_api]
        for spec in list_task_invocation_specs()
        for key in spec.template_keys
    }


def _validate_task_registry() -> None:
    validate_task_invocation_specs()
    contract_keys = set(list_task_contract_keys())
    file_keys = {
        key for key in list_template_keys() if key.startswith("tasks/")
    }
    missing_contracts = sorted(file_keys - contract_keys)
    if missing_contracts:
        raise TaskContractError(
            "task template missing contract: " + ", ".join(missing_contracts)
        )
    explicit_code_fallbacks = {
        key
        for key in contract_keys
        if get_task_contract(key).render_mode == "code_fallback_only"
    }
    missing_templates = sorted(contract_keys - file_keys - explicit_code_fallbacks)
    if missing_templates:
        raise TaskContractError(
            "active task contract missing template: " + ", ".join(missing_templates)
        )


def _require_invocation_api(
    task_key: str,
    *allowed_apis: str,
) -> None:
    key = resolve_template_key(task_key)
    spec = get_task_invocation_for_template(key)
    if spec is None:
        raise TaskInvocationError(f"task {key} invocation 未登记")
    if spec.render_api not in allowed_apis:
        raise TaskInvocationError(
            f"task {key} 必须通过 {spec.render_api} renderer 调用"
        )


def _render_selection(selection: TaskTemplateSelection, values: dict[str, Any]) -> str:
    if selection.template is None:
        return ""
    return render_scoped_template(
        selection.template.prompt_key,
        selection.template.body,
        values,
    ).strip()


def render_task_prompt(prompt_key: str, values: dict[str, Any], *, fallback_text: str = "") -> str:
    render_values = dict(values or {})
    validate_task_call_values(prompt_key, render_values)
    _require_invocation_api(prompt_key, "prompt")
    selection = select_task_template(prompt_key)
    rendered = _render_selection(selection, render_values)
    if not rendered and selection.invalid_sources:
        logger.warning(
            "[PromptV2Task] render unavailable key=%s fallback=code invalid=%s",
            resolve_template_key(prompt_key),
            ",".join(selection.invalid_sources),
        )
    return rendered or str(fallback_text or "")


def render_task_messages(
    prompt_key: str,
    values: dict[str, Any],
    *,
    fallback_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    original_values = dict(values or {})
    contract = validate_task_call_values(prompt_key, original_values)
    _require_invocation_api(prompt_key, "messages", "code_fallback_only")
    selection = select_task_template(prompt_key)
    render_values = dict(original_values)
    if contract is not None and contract.render_mode == "system_with_user_ref":
        for variable in contract.payload_variables:
            render_values[variable] = TASK_PAYLOAD_MARKER
    rendered = _render_selection(selection, render_values)
    if not rendered and selection.invalid_sources:
        logger.warning(
            "[PromptV2Task] message render unavailable key=%s fallback=code invalid=%s",
            resolve_template_key(prompt_key),
            ",".join(selection.invalid_sources),
        )
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


def render_task_pair(
    invocation_id: str,
    values: dict[str, Any],
) -> TaskPairRenderResult:
    spec = get_task_invocation_spec(invocation_id)
    if spec is None or spec.render_api != "paired_messages":
        raise TaskInvocationError(
            f"task invocation {invocation_id!r} 不是 paired_messages"
        )
    if len(spec.template_keys) != 2:
        raise TaskInvocationError(
            f"task invocation {spec.invocation_id} 必须登记两个模板"
        )

    render_values = dict(values or {})
    rendered_parts: list[RenderedTaskTemplate] = []
    tool_names: list[str] = []
    for role, task_key in zip(("system", "user"), spec.template_keys, strict=True):
        validate_task_call_values(task_key, render_values)
        selection = select_task_template(task_key)
        if selection.template is None:
            invalid = ",".join(selection.invalid_sources) or "missing"
            raise TaskTemplateUnavailableError(
                f"task {selection.task_key} template unavailable: {invalid}"
            )
        frontmatter = selection.template.frontmatter or {}
        kind = str(frontmatter.get("kind") or "task").strip()
        tool_name = str(frontmatter.get("tool_name") or "").strip()
        if kind not in {"task", "tool"}:
            raise TaskTemplateUnavailableError(
                f"task {selection.task_key} template kind invalid"
            )
        tool_names.append(tool_name)
        content = _render_selection(selection, render_values)
        if not content:
            raise TaskTemplateRenderError(
                f"task {selection.task_key} rendered empty"
            )
        rendered_parts.append(
            RenderedTaskTemplate(
                task_key=selection.task_key,
                role=role,
                content=content,
                source=selection.source,
                path=str(selection.template.path),
                version=frontmatter.get("version", ""),
            )
        )
    if any(not name for name in tool_names) or set(tool_names) != {
        spec.invocation_id
    }:
        raise TaskTemplateUnavailableError(
            f"task invocation {spec.invocation_id} tool_name 不一致"
        )
    return TaskPairRenderResult(
        system=rendered_parts[0],
        user=rendered_parts[1],
    )
