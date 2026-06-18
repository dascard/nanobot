from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    role = str(message.get("role") or "user")
    content = message.get("content", "")
    return {"role": role, "content": content}


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

    @property
    def messages_without_current_user(self) -> list[dict[str, Any]]:
        if not self.messages:
            return []
        return list(self.messages[:-1])

    @property
    def current_user_content(self) -> Any:
        if not self.messages:
            return ""
        last = self.messages[-1]
        if str(last.get("role") or "") != "user":
            return ""
        return last.get("content", "")

    @property
    def request_json(self) -> dict[str, Any]:
        return {"messages": self.messages, "tools": self.tool_schemas}

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
