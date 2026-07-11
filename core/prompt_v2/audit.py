from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class PromptAuditResult:
    ok: bool
    issues: list[str]


class PromptAuditError(RuntimeError):
    def __init__(self, issues: list[str], plan=None):
        self.issues = list(issues or [])
        self.plan = plan
        super().__init__("; ".join(self.issues) or "Prompt V2 audit failed")


_SINGLETON_RUNTIME_KEYS = (
    ("persona_reference", "persona_reference"),
    ("runtime_tool_prompt", "runtime_tool_prompt"),
    ("current_user_event", "current user input"),
)

_POLICY_SECTIONS = {
    "group": ("group_policy", "chat/branch_group"),
    "private": ("private_policy", "chat/branch_private"),
}
_POLICY_BY_NODE_ID = {
    node_id: (chat_type, template_key)
    for chat_type, (node_id, template_key) in _POLICY_SECTIONS.items()
}
_POLICY_BY_TEMPLATE_KEY = {
    template_key: (chat_type, node_id)
    for chat_type, (node_id, template_key) in _POLICY_SECTIONS.items()
}
_SINGLETON_RUNTIME_IDS = {runtime_key for runtime_key, _label in _SINGLETON_RUNTIME_KEYS}


def _section_field(section: dict, field: str) -> str:
    return str(section.get(field) or "").strip()


def _shown(value: str) -> str:
    return value or "<empty>"


def _section_origin(section: dict) -> str:
    if "origin" not in section:
        return "flow"
    return _section_field(section, "origin")


def _section_status(section: dict) -> str:
    if "status" not in section:
        return "emitted"
    return _section_field(section, "status")


def _audit_reserved_section_identity(section: dict, issues: list[str]) -> None:
    node_id = _section_field(section, "node_id")
    node_type = _section_field(section, "node_type")
    template_key = _section_field(section, "template_key")
    runtime_key = _section_field(section, "runtime_key")

    expected_runtime_id = ""
    if node_id in _SINGLETON_RUNTIME_IDS:
        expected_runtime_id = node_id
    elif runtime_key in _SINGLETON_RUNTIME_IDS:
        expected_runtime_id = runtime_key
    if expected_runtime_id:
        if node_id != expected_runtime_id:
            issues.append(
                f"singleton {expected_runtime_id} node_id must be {expected_runtime_id}, "
                f"got {_shown(node_id)}"
            )
        if node_type != "runtime":
            issues.append(
                f"singleton {expected_runtime_id} node_type must be runtime, "
                f"got {_shown(node_type)}"
            )
        if runtime_key != expected_runtime_id:
            issues.append(
                f"singleton {expected_runtime_id} runtime_key must be {expected_runtime_id}, "
                f"got {_shown(runtime_key)}"
            )
        if template_key:
            issues.append(
                f"singleton {expected_runtime_id} template_key must be empty, got {template_key}"
            )

    expected_policy_id = ""
    expected_template_key = ""
    policy_type = ""
    if node_id in _POLICY_BY_NODE_ID:
        policy_type, expected_template_key = _POLICY_BY_NODE_ID[node_id]
        expected_policy_id = node_id
    elif template_key in _POLICY_BY_TEMPLATE_KEY:
        policy_type, expected_policy_id = _POLICY_BY_TEMPLATE_KEY[template_key]
        expected_template_key = template_key
    if expected_policy_id:
        if node_id != expected_policy_id:
            issues.append(
                f"{policy_type} policy node_id must be {expected_policy_id}, got {_shown(node_id)}"
            )
        if node_type != "template":
            issues.append(
                f"{policy_type} policy node_type must be template, got {_shown(node_type)}"
            )
        if template_key != expected_template_key:
            issues.append(
                f"{policy_type} policy template_key must be {expected_template_key}, "
                f"got {_shown(template_key)}"
            )
        if runtime_key:
            issues.append(
                f"{policy_type} policy runtime_key must be empty, got {runtime_key}"
            )


def audit_prompt_plan(plan) -> PromptAuditResult:
    issues: list[str] = []
    sections = [
        section
        for section in (getattr(plan, "flow_sections", None) or [])
        if isinstance(section, dict)
    ]
    for section in sections:
        _audit_reserved_section_identity(section, issues)
    declared_sections = [section for section in sections if _section_origin(section) == "flow"]
    runtime_key_counts = Counter(
        _section_field(section, "runtime_key") for section in declared_sections
    )
    template_key_counts = Counter(
        _section_field(section, "template_key") for section in declared_sections
    )

    for runtime_key, label in _SINGLETON_RUNTIME_KEYS:
        count = runtime_key_counts[runtime_key]
        if count != 1:
            issues.append(f"{label} flow node must appear once, got {count}")
            continue

        section = next(
            section
            for section in declared_sections
            if _section_field(section, "runtime_key") == runtime_key
        )
        status = _section_status(section)
        if status != "emitted":
            issues.append(
                f"singleton {runtime_key} status must be emitted, got {_shown(status)}"
            )

    chat_type = str(plan.chat_type or "").strip().lower()
    if chat_type in _POLICY_SECTIONS:
        expected_node_id, expected_template_key = _POLICY_SECTIONS[chat_type]
        expected_count = template_key_counts[expected_template_key]
        if expected_count != 1:
            issues.append(f"{chat_type} plan must select its policy once, got {expected_count}")

        other_type = "private" if chat_type == "group" else "group"
        other_count = template_key_counts[_POLICY_SECTIONS[other_type][1]]
        if other_count:
            issues.append(f"{chat_type} plan contains {other_type} policy flow node, got {other_count}")

        if expected_count == 1:
            section = next(
                section
                for section in declared_sections
                if _section_field(section, "template_key") == expected_template_key
            )
            status = _section_status(section)
            if status != "emitted":
                issues.append(
                    f"{chat_type} policy status must be emitted, got {_shown(status)}"
                )

    return PromptAuditResult(ok=not issues, issues=issues)
