from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from core.prompt_v2.flow_contract import (
    RUNTIME_NODE_KEYS,
    forbidden_conditional_contracts,
    required_contracts,
    reserved_contract_by_node_id,
    reserved_contract_by_runtime_key,
    reserved_contract_by_template_key,
)
from core.prompt_v2.context_adapters import SESSION_GUIDANCE_NOTICE
from core.prompt_v2.section_descriptors import descriptor_for_node
from core.session_guidance import (
    SessionGuidanceValidationError,
    normalize_session_guidance,
)


@dataclass(frozen=True)
class PromptAuditResult:
    ok: bool
    issues: list[str]


class PromptAuditError(RuntimeError):
    def __init__(self, issues: list[str], plan=None):
        self.issues = list(issues or [])
        self.plan = plan
        super().__init__("; ".join(self.issues) or "Prompt V2 audit failed")


_RUNTIME_REQUIRED_STRING_FIELDS = {
    "chat_type",
    "current_time",
    "platform",
    "session_id",
    "timezone",
    "user_id",
}
_RUNTIME_OPTIONAL_STRING_FIELDS = {"group_id"}
_RUNTIME_REQUIRED_BOOL_FIELDS = {"is_super_user"}
_VALID_SECTION_ORIGINS = {"flow", "fallback"}
_VALID_SECTION_STATUSES = {
    "emitted",
    "empty",
    "missing_template",
    "skipped_duplicate",
}
_MESSAGE_META_STRING_LIMITS = {
    "bot_id": 128,
    "bot_name": 160,
    "current_message_id": 128,
    "self_id": 128,
    "sender_name": 160,
    "session_name": 160,
    "timing_decision": 64,
    "trigger_reason": 64,
}
_SESSION_GUIDANCE_PREFIX = (
    f"<session_guidance>\n{SESSION_GUIDANCE_NOTICE}\n\n"
)
_SESSION_GUIDANCE_SUFFIX = "\n</session_guidance>"


def _section_field(section: dict, field: str) -> str:
    return str(section.get(field) or "").strip()


def _shown(value: str) -> str:
    return value or "<empty>"


def _section_origin(section: dict) -> str:
    return _section_field(section, "origin")


def _section_status(section: dict) -> str:
    return _section_field(section, "status")


def _section_descriptor(section: dict):
    """从代码侧注册表解析段落能力，不能信任计划内可被篡改的声明。"""

    return descriptor_for_node(
        {
            "id": _section_field(section, "node_id"),
            "type": _section_field(section, "node_type"),
            "template_key": _section_field(section, "template_key"),
            "runtime_key": _section_field(section, "runtime_key"),
        }
    )


def _expected_message_role(section: dict) -> str:
    descriptor = _section_descriptor(section)
    if descriptor.trust in {"untrusted_data", "untrusted_instruction"}:
        return "user"
    return "system"


def _audit_section_shape(section: dict, issues: list[str]) -> None:
    node_id = _section_field(section, "node_id") or "<empty>"
    node_type = _section_field(section, "node_type")
    template_key = _section_field(section, "template_key")
    runtime_key = _section_field(section, "runtime_key")

    if node_type not in {"template", "runtime"}:
        issues.append(f"{node_id} node_type is invalid")
    elif node_type == "template":
        if not template_key:
            issues.append(f"{node_id} template_key is required")
        if runtime_key:
            issues.append(f"{node_id} runtime_key must be empty")
    else:
        if runtime_key not in RUNTIME_NODE_KEYS:
            issues.append(f"{node_id} runtime_key is invalid")
        if template_key:
            issues.append(f"{node_id} template_key must be empty")

    if "origin" not in section:
        issues.append(f"{node_id} origin is required")
    elif _section_origin(section) not in _VALID_SECTION_ORIGINS:
        issues.append(f"{node_id} origin is invalid")

    if "status" not in section:
        issues.append(f"{node_id} status is required")
    elif _section_status(section) not in _VALID_SECTION_STATUSES:
        issues.append(f"{node_id} status is invalid")


def _audit_reserved_section_identity(section: dict, issues: list[str]) -> None:
    node_id = _section_field(section, "node_id")
    node_type = _section_field(section, "node_type")
    template_key = _section_field(section, "template_key")
    runtime_key = _section_field(section, "runtime_key")

    contract = reserved_contract_by_node_id().get(node_id)
    contract = contract or reserved_contract_by_template_key().get(template_key)
    contract = contract or reserved_contract_by_runtime_key().get(runtime_key)
    if contract is None:
        return

    if node_id != contract.node_id:
        issues.append(
            f"{contract.node_id} node_id must be {contract.node_id}, got {_shown(node_id)}"
        )
    if node_type != contract.node_type:
        issues.append(
            f"{contract.node_id} node_type must be {contract.node_type}, got {_shown(node_type)}"
        )
    if template_key != contract.template_key:
        issues.append(
            f"{contract.node_id} template_key must be {contract.template_key or '<empty>'}, "
            f"got {_shown(template_key)}"
        )
    if runtime_key != contract.runtime_key:
        issues.append(
            f"{contract.node_id} runtime_key must be {contract.runtime_key or '<empty>'}, "
            f"got {_shown(runtime_key)}"
        )


def _flow_runtime_section(sections: list[dict], runtime_key: str) -> dict | None:
    matches = [
        section
        for section in sections
        if _section_origin(section) == "flow"
        and _section_field(section, "runtime_key") == runtime_key
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _section_message(
    plan,
    section: dict | None,
    *,
    label: str,
    expected_role: str,
    issues: list[str],
) -> tuple[int, dict[str, Any]] | None:
    if section is None:
        return None
    indexes = section.get("message_indexes")
    if not isinstance(indexes, list) or len(indexes) != 1 or type(indexes[0]) is not int:
        issues.append(f"{label} must reference exactly one message index")
        return None
    index = indexes[0]
    messages = list(getattr(plan, "messages", None) or [])
    if index < 0 or index >= len(messages):
        issues.append(f"{label} message index is out of bounds")
        return None
    message = messages[index]
    if not isinstance(message, dict):
        issues.append(f"{label} message must be an object")
        return None
    role = str(message.get("role") or "")
    if role != expected_role:
        issues.append(f"{label} message role must be {expected_role}")
    return index, message


def _audit_all_message_indexes(plan, sections: list[dict], issues: list[str]) -> None:
    messages = list(getattr(plan, "messages", None) or [])
    owners: dict[int, list[str]] = defaultdict(list)
    previous_flow_message_index = -1
    flow_order_invalid = False
    for section in sections:
        node_id = _section_field(section, "node_id") or "<empty>"
        node_type = _section_field(section, "node_type")
        runtime_key = _section_field(section, "runtime_key")
        indexes = section.get("message_indexes")
        if not isinstance(indexes, list):
            issues.append(f"{node_id} message_indexes must be a list")
            continue
        seen: set[int] = set()
        valid_indexes: list[int] = []
        for index in indexes:
            if type(index) is not int:
                issues.append(f"{node_id} message index must be an integer")
                continue
            if index in seen:
                issues.append(f"{node_id} message_indexes contains duplicates")
                continue
            seen.add(index)
            if index < 0 or index >= len(messages):
                issues.append(f"{node_id} message index is out of bounds")
                continue
            if not isinstance(messages[index], dict):
                issues.append(f"{node_id} message must be an object")
                continue
            owners[index].append(node_id)
            valid_indexes.append(index)

            role = str(messages[index].get("role") or "")
            if node_type == "runtime" and runtime_key == "history_messages":
                if role not in {"user", "assistant"}:
                    issues.append(
                        f"{node_id} message role must be user or assistant"
                    )
            else:
                expected_role = _expected_message_role(section)
                if role != expected_role:
                    issues.append(
                        f"{node_id} message role must be {expected_role}"
                    )

        if valid_indexes != sorted(valid_indexes):
            issues.append(f"{node_id} message index order is invalid")
        if _section_origin(section) == "flow" and valid_indexes:
            if valid_indexes[0] <= previous_flow_message_index:
                flow_order_invalid = True
            previous_flow_message_index = max(
                previous_flow_message_index,
                *valid_indexes,
            )

    for index, node_ids in sorted(owners.items()):
        if len(node_ids) > 1:
            issues.append(
                "message index is owned by multiple flow sections: "
                f"index={index}, nodes={','.join(node_ids)}"
            )
    for index in range(len(messages)):
        if index not in owners:
            issues.append(
                f"message index is not owned by a flow section: index={index}"
            )
    if flow_order_invalid:
        issues.append("flow section message order is invalid")


def _audit_core_contracts(
    plan,
    sections: list[dict],
    *,
    audit_messages: bool,
    issues: list[str],
) -> None:
    platform = str(getattr(plan, "platform", "qq") or "qq").strip().lower() or "qq"
    chat_type = str(getattr(plan, "chat_type", "private") or "private").strip().lower()
    required = required_contracts(platform, chat_type)

    flow_positions: list[int] = []
    message_positions: list[int] = []
    for contract in required:
        node_matches = [
            section
            for section in sections
            if _section_field(section, "node_id") == contract.node_id
        ]
        flow_matches = [
            section
            for section in node_matches
            if _section_origin(section) == "flow"
        ]
        if len(flow_matches) != 1:
            issues.append(
                f"required flow section {contract.node_id} must appear once, "
                f"got {len(flow_matches)}"
            )
        if any(_section_origin(section) != "flow" for section in node_matches):
            issues.append(
                f"required flow section {contract.node_id} must originate from flow"
            )
        if len(flow_matches) != 1:
            continue

        section = flow_matches[0]
        status = _section_status(section)
        allowed_statuses = {"emitted", "empty"} if contract.allow_empty else {"emitted"}
        if status not in allowed_statuses:
            expected = "emitted or empty" if contract.allow_empty else "emitted"
            issues.append(
                f"required flow section {contract.node_id} status must be {expected}, "
                f"got {_shown(status)}"
            )
        flow_positions.append(sections.index(section))
        if not audit_messages:
            continue
        if contract.allow_empty and status == "empty":
            if section.get("message_indexes") != []:
                issues.append(
                    f"empty {contract.node_id} must not reference message indexes"
                )
            continue
        resolved = _section_message(
            plan,
            section,
            label=contract.node_id,
            expected_role=_expected_message_role(section),
            issues=issues,
        )
        if resolved is not None:
            message_positions.append(resolved[0])

    for contract in forbidden_conditional_contracts(platform, chat_type):
        count = sum(
            1
            for section in sections
            if _section_field(section, "node_id") == contract.node_id
        )
        if count:
            issues.append(
                f"forbidden flow section {contract.node_id} appears for "
                f"platform={platform}, chat_type={chat_type}, got {count}"
            )

    if flow_positions != sorted(flow_positions):
        issues.append("core flow section order is invalid")
    if audit_messages and message_positions != sorted(message_positions):
        issues.append("core flow message order is invalid")


def _audit_session_guidance(plan, sections: list[dict], issues: list[str]) -> None:
    section = _flow_runtime_section(sections, "session_guidance")
    if section is None:
        return

    debug = getattr(plan, "debug", None)
    if not isinstance(debug, dict):
        issues.append("session_guidance debug must be an object")
        return

    status = _section_status(section)
    debug_status = debug.get("session_guidance_status")
    if debug_status not in {"empty", "emitted"}:
        issues.append("session_guidance debug status is invalid")
    elif debug_status != status:
        issues.append("session_guidance debug status does not match section")

    configured = debug.get("session_guidance_configured")
    if type(configured) is not bool:
        issues.append("session_guidance configured flag must be a boolean")
        return

    chars = debug.get("session_guidance_chars")
    if type(chars) is not int or chars < 0:
        issues.append("session_guidance chars must be a non-negative integer")
        return

    digest = debug.get("session_guidance_sha256")
    if not isinstance(digest, str):
        issues.append("session_guidance sha256 must be a string")
        return

    messages = list(getattr(plan, "messages", None) or [])
    wrapper_count = sum(
        1
        for message in messages
        if isinstance(message, dict)
        and str(message.get("role") or "") == "system"
        and str(message.get("content") or "").startswith(
            _SESSION_GUIDANCE_PREFIX
        )
        and str(message.get("content") or "").endswith(
            _SESSION_GUIDANCE_SUFFIX
        )
    )
    expected_wrapper_count = 1 if configured else 0
    if wrapper_count != expected_wrapper_count:
        issues.append(
            "session_guidance fixed wrapper count must be "
            f"{expected_wrapper_count}, got {wrapper_count}"
        )

    indexes = section.get("message_indexes")
    if not configured:
        if status != "empty":
            issues.append("empty session_guidance status must be empty")
        if indexes != []:
            issues.append("empty session_guidance must not reference message indexes")
        if chars != 0:
            issues.append("empty session_guidance chars must be zero")
        if digest:
            issues.append("empty session_guidance sha256 must be empty")
        return

    if status != "emitted":
        issues.append("configured session_guidance status must be emitted")
    resolved = _section_message(
        plan,
        section,
        label="session_guidance",
        expected_role="system",
        issues=issues,
    )
    if resolved is None:
        return
    _index, message = resolved
    content = str(message.get("content") or "")
    if not content.startswith(_SESSION_GUIDANCE_PREFIX) or not content.endswith(
        _SESSION_GUIDANCE_SUFFIX
    ):
        issues.append("session_guidance must use the fixed wrapper")
        return

    body = content[
        len(_SESSION_GUIDANCE_PREFIX):-len(_SESSION_GUIDANCE_SUFFIX)
    ]
    try:
        normalized = normalize_session_guidance(body)
    except SessionGuidanceValidationError:
        issues.append("session_guidance wrapper body is invalid")
        return
    if not normalized or body != normalized:
        issues.append("session_guidance wrapper body must be normalized and non-empty")
        return

    expected_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if chars != len(normalized):
        issues.append("session_guidance chars do not match wrapper body")
    if digest != expected_digest:
        issues.append("session_guidance sha256 does not match wrapper body")


def _parse_tagged_json_object(
    content: str,
    tag: str,
    issues: list[str],
    *,
    require_whole_content: bool,
) -> dict[str, Any] | None:
    opening = f"<{tag}>"
    closing = f"</{tag}>"
    text = str(content or "").strip()
    if text.count(opening) != 1 or text.count(closing) != 1:
        issues.append(f"{tag} must contain exactly one tag pair")
        return None
    if not text.startswith(opening):
        issues.append(f"{tag} must start its message content")
        return None
    if require_whole_content and not text.endswith(closing):
        issues.append(f"{tag} must occupy the whole message content")
        return None
    body = text.split(opening, 1)[1].split(closing, 1)[0].strip()
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        issues.append(f"{tag} body must be valid JSON")
        return None
    if not isinstance(value, dict):
        issues.append(f"{tag} body must be a JSON object")
        return None
    return value


def _audit_runtime_context(plan, sections: list[dict], issues: list[str]) -> None:
    section = _flow_runtime_section(sections, "runtime_context")
    resolved = _section_message(
        plan,
        section,
        label="runtime_context",
        expected_role="system",
        issues=issues,
    )
    if resolved is None:
        return
    _index, message = resolved
    facts = _parse_tagged_json_object(
        str(message.get("content") or ""),
        "runtime_context",
        issues,
        require_whole_content=True,
    )
    if facts is None:
        return

    allowed_fields = (
        _RUNTIME_REQUIRED_STRING_FIELDS
        | _RUNTIME_OPTIONAL_STRING_FIELDS
        | _RUNTIME_REQUIRED_BOOL_FIELDS
    )
    unknown_fields = sorted(set(facts) - allowed_fields)
    if unknown_fields:
        issues.append("runtime_context contains unsupported fields")
    for field in sorted(_RUNTIME_REQUIRED_STRING_FIELDS):
        if field not in facts:
            issues.append(f"runtime_context missing required field {field}")
        elif not isinstance(facts[field], str):
            issues.append(f"runtime_context field {field} must be a string")
    for field in sorted(_RUNTIME_OPTIONAL_STRING_FIELDS):
        if field in facts and not isinstance(facts[field], str):
            issues.append(f"runtime_context field {field} must be a string")
    for field in sorted(_RUNTIME_REQUIRED_BOOL_FIELDS):
        if field not in facts:
            issues.append(f"runtime_context missing required field {field}")
        elif not isinstance(facts[field], bool):
            issues.append(f"runtime_context field {field} must be a boolean")
    if isinstance(facts.get("chat_type"), str) and facts["chat_type"] not in {"group", "private"}:
        issues.append("runtime_context field chat_type is invalid")
    if isinstance(facts.get("chat_type"), str) and facts["chat_type"] != str(plan.chat_type or ""):
        issues.append("runtime_context field chat_type does not match plan")
    if isinstance(facts.get("platform"), str):
        runtime_platform = facts["platform"].strip().lower()
        plan_platform = str(getattr(plan, "platform", "") or "").strip().lower()
        if runtime_platform != plan_platform:
            issues.append("runtime_context field platform does not match plan")
    for field in ("session_id", "user_id", "group_id"):
        value = facts.get(field)
        if isinstance(value, str) and len(value) > 128:
            issues.append(f"runtime_context field {field} exceeds its length limit")


def _message_meta_carrier(content: Any, issues: list[str]) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or not content:
        issues.append("message_meta current user content must be text or a non-empty parts list")
        return ""
    first = content[0]
    if not isinstance(first, dict) or str(first.get("type") or "") not in {"text", "input_text"}:
        issues.append("message_meta must be carried by the first text part")
        return ""
    text = first.get("text")
    if not isinstance(text, str):
        issues.append("message_meta first text part must contain text")
        return ""
    return text


def _audit_message_meta(plan, sections: list[dict], issues: list[str]) -> None:
    messages = list(getattr(plan, "messages", None) or [])
    section = _flow_runtime_section(sections, "current_user_event")
    resolved = _section_message(
        plan,
        section,
        label="current_user_event",
        expected_role="user",
        issues=issues,
    )
    if not messages:
        issues.append("current_user_event requires a final user message")
        return
    final_message = messages[-1]
    if not isinstance(final_message, dict):
        issues.append("current_user_event final message must be an object")
        return
    if str(final_message.get("role") or "") != "user":
        issues.append("current_user_event must be the final user message")
    if resolved is not None and resolved[0] != len(messages) - 1:
        issues.append("current_user_event must reference the final message")

    for index, message in enumerate(messages[:-1]):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") != "system":
            continue
        text = str(message.get("content") or "")
        if "</message_meta>" in text:
            issues.append(f"message_meta cannot appear in system message {index}")

    carrier = _message_meta_carrier(final_message.get("content"), issues)
    if not carrier:
        return
    metadata = _parse_tagged_json_object(
        carrier,
        "message_meta",
        issues,
        require_whole_content=False,
    )
    if metadata is None:
        return
    allowed_fields = set(_MESSAGE_META_STRING_LIMITS) | {"bot_aliases"}
    if set(metadata) - allowed_fields:
        issues.append("message_meta contains unsupported fields")
    for field, max_chars in _MESSAGE_META_STRING_LIMITS.items():
        if field not in metadata:
            continue
        value = metadata[field]
        if not isinstance(value, str):
            issues.append(f"message_meta field {field} must be a string")
        elif len(value) > max_chars:
            issues.append(f"message_meta field {field} exceeds its length limit")
    aliases = metadata.get("bot_aliases")
    if aliases is not None:
        if not isinstance(aliases, list) or any(not isinstance(item, str) for item in aliases):
            issues.append("message_meta field bot_aliases must be a string list")
        elif len(aliases) > 10 or any(len(item) > 80 for item in aliases):
            issues.append("message_meta field bot_aliases exceeds its limit")


def _audit_persona_reference(plan, sections: list[dict], issues: list[str]) -> None:
    section = _flow_runtime_section(sections, "persona_reference")
    resolved = _section_message(
        plan,
        section,
        label="persona_reference",
        expected_role="user",
        issues=issues,
    )
    if resolved is None:
        return
    _index, message = resolved
    envelope = _parse_context_data_envelope(
        str(message.get("content") or ""),
        label="persona_reference",
        issues=issues,
    )
    if envelope is None:
        return
    if envelope.get("section") != "persona_reference":
        issues.append("persona_reference context envelope section is invalid")
    if envelope.get("trust") != "untrusted_data":
        issues.append("persona_reference context envelope trust is invalid")
    content_value = envelope.get("content")
    if not isinstance(content_value, str):
        issues.append("persona_reference context envelope content must be a string")
        return
    content = content_value.strip()
    opening = "<persona_reference>"
    closing = "</persona_reference>"
    if content.count(opening) != 1 or content.count(closing) != 1:
        issues.append("persona_reference must contain exactly one fixed tag pair")
        return
    if not content.startswith(opening) or not content.endswith(closing):
        issues.append("persona_reference must occupy the whole context payload")
        return
    data_opening = "<persona_data>"
    data_closing = "</persona_data>"
    if content.count(data_opening) != 1 or content.count(data_closing) != 1:
        issues.append("persona_reference must contain one persona_data block")
        return
    data_body = content.split(data_opening, 1)[1].split(data_closing, 1)[0].strip()
    try:
        data = json.loads(data_body)
    except (json.JSONDecodeError, TypeError):
        issues.append("persona_reference persona_data must be valid JSON")
        return
    if not isinstance(data, dict):
        issues.append("persona_reference persona_data must be a JSON object")
        return
    if set(data) != {"profile", "user_id"}:
        issues.append("persona_reference persona_data fields are invalid")
        return
    if not isinstance(data.get("profile"), str):
        issues.append("persona_reference profile must be a string")
    if not isinstance(data.get("user_id"), str):
        issues.append("persona_reference user_id must be a string")
    elif len(data["user_id"]) > 128:
        issues.append("persona_reference user_id exceeds its length limit")


def _parse_context_data_envelope(
    raw_content: str,
    *,
    label: str,
    issues: list[str],
) -> dict[str, Any] | None:
    content = str(raw_content or "").strip()
    opening = "<context_data_json>"
    closing = "</context_data_json>"
    if content.count(opening) != 1 or content.count(closing) != 1:
        issues.append(f"{label} must contain exactly one context data envelope")
        return None
    if not content.startswith(f"{opening}\n") or not content.endswith(
        f"\n{closing}"
    ):
        issues.append(f"{label} context data envelope must occupy the whole message")
        return None
    body = content[len(opening) : -len(closing)].strip()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        issues.append(f"{label} context data envelope must be valid JSON")
        return None
    if not isinstance(payload, dict):
        issues.append(f"{label} context data envelope must be a JSON object")
        return None
    if set(payload) != {"section", "trust", "content"}:
        issues.append(f"{label} context data envelope fields are invalid")
        return None
    return payload


def audit_prompt_plan(plan, *, audit_messages: bool = True) -> PromptAuditResult:
    issues: list[str] = []
    sections: list[dict] = []
    for index, section in enumerate(getattr(plan, "flow_sections", None) or []):
        if not isinstance(section, dict):
            issues.append(f"flow section at index {index} must be an object")
            continue
        sections.append(section)
    for section in sections:
        _audit_section_shape(section, issues)
        _audit_reserved_section_identity(section, issues)
    _audit_core_contracts(
        plan,
        sections,
        audit_messages=audit_messages,
        issues=issues,
    )

    if audit_messages:
        _audit_all_message_indexes(plan, sections, issues)
        _audit_runtime_context(plan, sections, issues)
        _audit_session_guidance(plan, sections, issues)
        _audit_persona_reference(plan, sections, issues)
        _audit_message_meta(plan, sections, issues)

    return PromptAuditResult(ok=not issues, issues=issues)
