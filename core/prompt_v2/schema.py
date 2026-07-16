from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypedDict


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    role = str(message.get("role") or "user")
    content = message.get("content", "")
    return {"role": role, "content": content}


PromptFlowOrigin = Literal["flow", "fallback"]
PromptFlowStatus = Literal["emitted", "empty", "skipped_duplicate", "missing_template"]


class PromptFlowSection(TypedDict):
    node_id: str
    node_type: str
    template_key: str
    runtime_key: str
    origin: PromptFlowOrigin
    status: PromptFlowStatus
    message_indexes: list[int]


@dataclass(frozen=True)
class PromptPlan:
    engine: str
    chat_type: str
    prompt_key: str
    messages: list[dict[str, Any]]
    tool_schemas: list[dict[str, Any]]
    section_hashes: dict[str, str]
    prompt_sha256: str
    token_estimate: int
    warnings: list[str]
    debug: dict[str, Any]
    platform: str = "qq"
    flow_sections: list[PromptFlowSection] = field(default_factory=list)
    message_token_estimate: int = 0
    tool_schema_token_estimate: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", copy.deepcopy(list(self.messages or [])))
        object.__setattr__(
            self,
            "tool_schemas",
            copy.deepcopy(list(self.tool_schemas or [])),
        )

    @property
    def messages_without_current_user(self) -> list[dict[str, Any]]:
        if not self.messages:
            return []
        return copy.deepcopy(list(self.messages[:-1]))

    @property
    def current_user_content(self) -> Any:
        if not self.messages:
            return ""
        last = self.messages[-1]
        if str(last.get("role") or "") != "user":
            return ""
        return copy.deepcopy(last.get("content", ""))

    @property
    def request_json(self) -> dict[str, Any]:
        return {
            "messages": copy.deepcopy(self.messages),
            "tools": copy.deepcopy(self.tool_schemas),
        }

    @property
    def template_resolutions(self) -> dict[str, dict[str, Any]]:
        value = self.debug.get("template_resolutions", {})
        if not isinstance(value, dict):
            return {}
        return copy.deepcopy(value)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["messages_without_current_user"] = self.messages_without_current_user
        data["current_user_content"] = self.current_user_content
        data["request_json"] = self.request_json
        return data


@dataclass
class PromptCompileRequest:
    chat_type: str = "private"
    platform: str = "qq"
    prompt_key: str = ""
    session_id: str = ""
    user_id: str = ""
    group_id: str = ""
    sender_name: str = ""
    sender_id: str = ""
    is_super_user: bool = False
    session_name: str = ""
    trigger_reason: str = ""
    timing_decision: str = ""
    current_message_id: str = ""
    source_message_ids: list[str] = field(default_factory=list)
    self_id: str = ""
    bot_id: str = ""
    bot_name: str = ""
    bot_aliases: list[str] = field(default_factory=list)
    user_input: Any = ""
    persona_text: str = ""
    session_guidance: str = field(default="", repr=False)
    session_guidance_chat_stream_id: str = ""
    history_header: str = ""
    history_messages: list[dict[str, Any]] = field(default_factory=list)
    group_profile_context: str = ""
    expression_context: str = ""
    jargon_context: str = ""
    runtime_tool_prompt: str = ""
    effort_constraint: str = ""
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_chat_type(self) -> str:
        value = str(self.chat_type or "").strip().lower()
        if value == "group":
            return "group"
        return "private"

    @property
    def normalized_platform(self) -> str:
        value = str(self.platform or "").strip().lower()
        return value or "qq"

    @property
    def normalized_prompt_key(self) -> str:
        if self.prompt_key:
            return str(self.prompt_key).removesuffix(".md").strip()
        return "chat_group" if self.normalized_chat_type == "group" else "chat_private"

    def normalized_history_messages(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in self.history_messages or []:
            msg = _normalize_message(item)
            if msg["role"] in {"user", "assistant"} and str(msg["content"]).strip():
                result.append(msg)
        return result
