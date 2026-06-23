"""聊天 guardrail 兼容门面。"""

from __future__ import annotations

from typing import Any


def detect_guardrail(
    guardrail: Any,
    message: str,
    *,
    allow_passthrough: bool = False,
) -> dict[str, Any]:
    """兼容新 detect_injection 和旧 classify 测试桩。"""
    if hasattr(guardrail, "detect_injection"):
        return guardrail.detect_injection(message, allow_passthrough=allow_passthrough)

    result = guardrail.classify(
        message,
        allow_injection_passthrough=allow_passthrough,
    )
    if not isinstance(result, dict):
        result = {}

    status = str(result.get("status") or "").strip()
    if status == "silent":
        return {
            **result,
            "status": "silent",
            "injection": False,
            "passthrough": bool(allow_passthrough),
        }

    injection = status == "injection"
    return {
        **result,
        "status": "injection" if injection else "safe",
        "injection": injection,
        "passthrough": bool(allow_passthrough and not injection),
    }


def guardrail_status_from_result(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return "safe"
    status = str(result.get("status") or "").strip()
    if status == "injection":
        return "injection"
    if status == "silent":
        return "silent"
    return "safe"
