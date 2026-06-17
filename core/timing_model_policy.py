"""TimingGate 模型层策略解析。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.settings_service import settings


DEFAULT_POLICY_KEY = "timing_gate.model_policy.default"
PLATFORM_POLICY_KEY = "timing_gate.model_policy.platforms"
SESSION_POLICY_KEY = "timing_gate.model_policy.sessions"

_VALID_MODES = {"enabled", "rules_only", "shadow"}
_MODE_ALIASES = {
    "disabled": "rules_only",
    "rule_only": "rules_only",
    "rules": "rules_only",
    "no_model": "rules_only",
}


@dataclass(frozen=True)
class TimingModelPolicy:
    mode: str
    source: str


def _normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    mode = _MODE_ALIASES.get(mode, mode)
    if mode in _VALID_MODES:
        return mode
    return "enabled"


def _load_policy_map(raw: str) -> dict[str, str]:
    try:
        data = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key).strip(): _normalize_mode(value)
        for key, value in data.items()
        if str(key).strip()
    }


def resolve_timing_model_policy(
    session_id: str = "",
    platform: str = "",
) -> TimingModelPolicy:
    """按 session > platform > default 解析 TimingGate 模型策略。"""
    session_key = str(session_id or "").strip()
    platform_key = str(platform or "").strip()

    session_policies = _load_policy_map(settings.get_str(SESSION_POLICY_KEY, "{}"))
    if session_key and session_key in session_policies:
        return TimingModelPolicy(session_policies[session_key], f"session:{session_key}")

    platform_policies = _load_policy_map(settings.get_str(PLATFORM_POLICY_KEY, "{}"))
    if platform_key and platform_key in platform_policies:
        return TimingModelPolicy(platform_policies[platform_key], f"platform:{platform_key}")

    default_mode = _normalize_mode(settings.get_str(DEFAULT_POLICY_KEY, "enabled"))
    return TimingModelPolicy(default_mode, "default")
