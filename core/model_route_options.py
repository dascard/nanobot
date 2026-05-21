"""模型路由通用选项。"""

from typing import Any

ENABLE_THINKING_AUTO = "auto"
ENABLE_THINKING_TRUE = "true"
ENABLE_THINKING_FALSE = "false"


def normalize_enable_thinking(value: Any, default: str = ENABLE_THINKING_AUTO) -> str:
    """把 route enable_thinking 配置归一化为 auto/true/false。"""
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
    """auto 策略：对容易返回 reasoning 内容的模型默认禁用 thinking。"""
    lowered = str(model or "").lower()
    return any(marker in lowered for marker in ("deepseek", "r1", "reasoning"))


def apply_enable_thinking_to_payload(
    payload: dict[str, Any],
    model: str,
    enable_thinking: Any = ENABLE_THINKING_AUTO,
) -> dict[str, Any]:
    """根据 route 配置修改 OpenAI-compatible payload。"""
    policy = normalize_enable_thinking(enable_thinking)
    payload.pop("enable_thinking", None)
    payload.pop("thinking", None)

    if policy == ENABLE_THINKING_TRUE:
        payload["enable_thinking"] = True
        return payload

    if policy == ENABLE_THINKING_FALSE:
        payload["enable_thinking"] = False
        payload["thinking"] = {"type": "disabled"}
        return payload

    if model_defaults_to_disabled_thinking(model):
        payload["thinking"] = {"type": "disabled"}
    return payload
