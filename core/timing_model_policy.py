"""TimingGate 模型层策略解析。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.runtime.extensions import (
    PolicyDescriptor,
    PolicyTypedFailure,
    RuntimeFailurePolicy,
    execute_policy,
)
from core.settings_service import settings


DEFAULT_POLICY_KEY = "timing_gate.model_policy.default"
PLATFORM_POLICY_KEY = "timing_gate.model_policy.platforms"
SESSION_POLICY_KEY = "timing_gate.model_policy.sessions"

_MODE_ALIASES = {
    "disabled": "rules_only",
    "rule_only": "rules_only",
    "rules": "rules_only",
    "no_model": "rules_only",
}


class TimingModelMode(StrEnum):
    ENABLED = "enabled"
    RULES_ONLY = "rules_only"
    SHADOW = "shadow"


@dataclass(frozen=True, slots=True)
class TimingModelPolicy:
    mode: TimingModelMode
    source: str

    def __post_init__(self) -> None:
        raw_mode = str(self.mode or "").strip().lower()
        raw_mode = _MODE_ALIASES.get(raw_mode, raw_mode)
        try:
            mode = TimingModelMode(raw_mode)
        except ValueError as exc:
            raise ValueError(
                f"mode 必须是有效 TimingModelMode：{raw_mode!r}"
            ) from exc
        source = str(self.source or "").strip()
        if not source or any(ord(char) < 32 for char in source):
            raise ValueError("TimingModelPolicy.source 无效")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "source", source)


TIMING_MODEL_POLICY_DESCRIPTOR = PolicyDescriptor(
    policy_id="timing.model_mode",
    owner_module="runtime.agent",
    domain="timing",
    input_contract="timing.model_policy.input.v1",
    output_contract="timing.model_policy.output.v1",
    failure_policy=RuntimeFailurePolicy.FAIL_OPEN,
    security_sensitive=False,
)


def _normalize_mode(value: Any) -> TimingModelMode:
    mode = str(value or "").strip().lower()
    mode = _MODE_ALIASES.get(mode, mode)
    try:
        return TimingModelMode(mode)
    except ValueError:
        return TimingModelMode.ENABLED


def _load_policy_map(raw: str) -> dict[str, TimingModelMode]:
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


def _resolve_timing_model_policy(
    session_id: str = "",
    platform: str = "",
) -> TimingModelPolicy:
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


def _fallback_timing_model_policy(
    failure: PolicyTypedFailure,
) -> TimingModelPolicy:
    return TimingModelPolicy(
        TimingModelMode.ENABLED,
        f"fallback:{failure.code.value}",
    )


def resolve_timing_model_policy(
    session_id: str = "",
    platform: str = "",
) -> TimingModelPolicy:
    """按 session > platform > default 解析；配置读取异常时显式 fail open。"""

    return execute_policy(
        TIMING_MODEL_POLICY_DESCRIPTOR,
        lambda: _resolve_timing_model_policy(
            session_id=session_id,
            platform=platform,
        ),
        fallback=_fallback_timing_model_policy,
    ).value


__all__ = [
    "TIMING_MODEL_POLICY_DESCRIPTOR",
    "TimingModelMode",
    "TimingModelPolicy",
    "resolve_timing_model_policy",
]
