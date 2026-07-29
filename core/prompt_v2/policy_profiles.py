"""Prompt 平台身份与服务端策略档位的解析规则。"""

from __future__ import annotations

import re

from core.prompt_v2.flow_contract import PROMPT_POLICY_PROFILES


DEFAULT_EXTERNAL_POLICY_PROFILE = "external_private"
_PLATFORM_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_BUILTIN_PLATFORM_PROFILES = {
    "qq": "qq",
    "web": "web",
    "internal": "internal",
}


class PromptPolicyError(ValueError):
    """平台身份或 Prompt 策略档位不符合运行时契约。"""


def normalize_platform_id(value: object, *, default: str = "qq") -> str:
    """规范平台身份；只校验稳定 ID 语法，不限制平台注册表。"""

    normalized = str(value or default).strip().lower() or default
    if _PLATFORM_ID_RE.fullmatch(normalized) is None:
        raise PromptPolicyError(
            "platform 必须匹配 ^[a-z][a-z0-9_-]{0,31}$"
        )
    return normalized


def resolve_prompt_policy_profile(
    platform_id: object,
    policy_profile: object = "",
) -> str:
    """解析服务端策略档位；未知平台默认使用外部私聊档位。"""

    platform = normalize_platform_id(platform_id)
    explicit = str(policy_profile or "").strip().lower()
    resolved = explicit or _BUILTIN_PLATFORM_PROFILES.get(
        platform,
        DEFAULT_EXTERNAL_POLICY_PROFILE,
    )
    if resolved not in PROMPT_POLICY_PROFILES:
        raise PromptPolicyError(f"Prompt policy_profile 不支持: {resolved}")
    return resolved


__all__ = [
    "DEFAULT_EXTERNAL_POLICY_PROFILE",
    "PromptPolicyError",
    "normalize_platform_id",
    "resolve_prompt_policy_profile",
]
