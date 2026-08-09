"""模型 Route 解析后交给具体 Runtime Adapter 的不可变计划。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ReplyRoutePlan:
    provider_id: str
    registry_provider: str
    base_url: str
    api_key: str
    timeout: float
    driver_type: str = "openai"
    request_protocol: str = "openai_chat_completions"
    request_path: str = "/chat/completions"
    profile_id: str = ""
    model: str = ""
    temperature: object = None
    max_tokens: int | None = None
    max_context: int = 128000
    cost_input_1m: float | None = None
    cost_output_1m: float | None = None
    intelligence: int = 0
    fallback_only: bool = False
    input_modalities: tuple[str, ...] = ("text",)
    output_modalities: tuple[str, ...] = ("text",)
    reasoning_effort: str = ""
    service_tier: str = ""
    enable_thinking: object = "auto"
    capabilities: dict[str, bool] = field(default_factory=dict)
    capability_evidence: dict[str, str] = field(default_factory=dict)
    routing_evidence: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    retry_policy: dict[str, Any] = field(default_factory=dict)
    driver_options: dict[str, Any] = field(default_factory=dict)
    provider_name: str = ""
    provider_native_tools: tuple[str, ...] = ()
    codex_account_id: str = ""


__all__ = ["ReplyRoutePlan"]
