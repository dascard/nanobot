"""Canonical Prompt Runtime 核心 flow 节点契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FLOW_SCHEMA_VERSION = 2
LIVE_PROMPT_BRANCHES: tuple[tuple[str, str], ...] = (
    ("qq", "group"),
    ("qq", "private"),
    ("web", "group"),
    ("web", "private"),
    ("internal", "private"),
)
LIVE_PROMPT_BRANCH_SET = frozenset(LIVE_PROMPT_BRANCHES)
RUNTIME_PLATFORMS = frozenset(platform for platform, _chat_type in LIVE_PROMPT_BRANCHES)


def is_live_prompt_branch(platform: str, chat_type: str) -> bool:
    """判断平台与会话类型是否属于受审计的在线分支。"""

    return (str(platform), str(chat_type)) in LIVE_PROMPT_BRANCH_SET


RUNTIME_NODE_KEYS = frozenset(
    {
        "runtime_context",
        "session_guidance",
        "persona_reference",
        "conversation_context_header",
        "history_messages",
        "group_context",
        "effort_constraint",
        "runtime_tool_prompt",
        "current_user_event",
    }
)


@dataclass(frozen=True)
class ReservedSectionContract:
    node_id: str
    node_type: Literal["template", "runtime"]
    template_key: str = ""
    runtime_key: str = ""
    expected_role: str = "system"
    platforms: frozenset[str] = frozenset()
    chat_types: frozenset[str] = frozenset()
    required: bool = True
    allow_empty: bool = False

    def applies_to(self, platform: str, chat_type: str) -> bool:
        normalized_platform = str(platform or "qq").strip().lower() or "qq"
        normalized_chat_type = str(chat_type or "private").strip().lower() or "private"
        return (
            (not self.platforms or normalized_platform in self.platforms)
            and (not self.chat_types or normalized_chat_type in self.chat_types)
        )

    @property
    def is_conditional(self) -> bool:
        return bool(self.platforms or self.chat_types)


RESERVED_SECTION_CONTRACTS: tuple[ReservedSectionContract, ...] = (
    ReservedSectionContract(
        node_id="base_contract",
        node_type="template",
        template_key="chat/main",
    ),
    ReservedSectionContract(
        node_id="qq_common_policy",
        node_type="template",
        template_key="chat/platform/qq/common",
        platforms=frozenset({"qq"}),
    ),
    ReservedSectionContract(
        node_id="group_policy",
        node_type="template",
        template_key="chat/branch_group",
        chat_types=frozenset({"group"}),
    ),
    ReservedSectionContract(
        node_id="qq_group_policy",
        node_type="template",
        template_key="chat/platform/qq/group",
        platforms=frozenset({"qq"}),
        chat_types=frozenset({"group"}),
    ),
    ReservedSectionContract(
        node_id="private_policy",
        node_type="template",
        template_key="chat/branch_private",
        chat_types=frozenset({"private"}),
    ),
    ReservedSectionContract(
        node_id="runtime_context",
        node_type="runtime",
        runtime_key="runtime_context",
    ),
    ReservedSectionContract(
        node_id="identity_context",
        node_type="template",
        template_key="chat/identity_context",
    ),
    ReservedSectionContract(
        node_id="session_guidance",
        node_type="runtime",
        runtime_key="session_guidance",
        allow_empty=True,
    ),
    ReservedSectionContract(
        node_id="persona_reference",
        node_type="runtime",
        runtime_key="persona_reference",
    ),
    ReservedSectionContract(
        node_id="runtime_tool_prompt",
        node_type="runtime",
        runtime_key="runtime_tool_prompt",
    ),
    ReservedSectionContract(
        node_id="current_user_event",
        node_type="runtime",
        runtime_key="current_user_event",
        expected_role="user",
    ),
)


def reserved_contract_by_node_id() -> dict[str, ReservedSectionContract]:
    return {contract.node_id: contract for contract in RESERVED_SECTION_CONTRACTS}


def reserved_contract_by_template_key() -> dict[str, ReservedSectionContract]:
    return {
        contract.template_key: contract
        for contract in RESERVED_SECTION_CONTRACTS
        if contract.template_key
    }


def reserved_contract_by_runtime_key() -> dict[str, ReservedSectionContract]:
    return {
        contract.runtime_key: contract
        for contract in RESERVED_SECTION_CONTRACTS
        if contract.runtime_key
    }


def required_contracts(
    platform: str,
    chat_type: str,
) -> tuple[ReservedSectionContract, ...]:
    return tuple(
        contract
        for contract in RESERVED_SECTION_CONTRACTS
        if contract.required and contract.applies_to(platform, chat_type)
    )


def forbidden_conditional_contracts(
    platform: str,
    chat_type: str,
) -> tuple[ReservedSectionContract, ...]:
    return tuple(
        contract
        for contract in RESERVED_SECTION_CONTRACTS
        if contract.is_conditional and not contract.applies_to(platform, chat_type)
    )
