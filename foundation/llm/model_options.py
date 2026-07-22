"""与具体 Provider 无关的模型请求选项。"""

from __future__ import annotations

from typing import Any


ENABLE_THINKING_AUTO = "auto"
ENABLE_THINKING_TRUE = "true"
ENABLE_THINKING_FALSE = "false"


def normalize_enable_thinking(
    value: Any,
    default: str = ENABLE_THINKING_AUTO,
) -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return ENABLE_THINKING_TRUE if value else ENABLE_THINKING_FALSE
    text = str(value).strip().lower()
    if text in {"", "auto", "default", "inherit"}:
        return ENABLE_THINKING_AUTO
    if text in {"1", "true", "yes", "on", "enabled", "enable"}:
        return ENABLE_THINKING_TRUE
    if text in {"0", "false", "no", "off", "disabled", "disable"}:
        return ENABLE_THINKING_FALSE
    raise ValueError("enable_thinking must be auto/true/false")


def model_defaults_to_disabled_thinking(model: str) -> bool:
    lowered = str(model or "").lower()
    return any(marker in lowered for marker in ("deepseek", "r1", "reasoning"))


def apply_enable_thinking_to_payload(
    payload: dict[str, Any],
    model: str,
    enable_thinking: Any = ENABLE_THINKING_AUTO,
) -> dict[str, Any]:
    policy = normalize_enable_thinking(enable_thinking)
    payload.pop("enable_thinking", None)
    payload.pop("thinking", None)
    if policy == ENABLE_THINKING_TRUE:
        payload["enable_thinking"] = True
    elif policy == ENABLE_THINKING_FALSE:
        payload["enable_thinking"] = False
        payload["thinking"] = {"type": "disabled"}
    elif model_defaults_to_disabled_thinking(model):
        payload["thinking"] = {"type": "disabled"}
    return payload
