"""私聊 Timing v2 的稳定枚举与业务结果合同。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


PRIVATE_DECISION_CONTRACT_VERSION = "private_decision_v2"
PRIVATE_RUNTIME_PRESET = "full"


class PrivateAction(StrEnum):
    NO_REPLY = "no_reply"
    WAIT = "wait"
    REPLY_NOW = "reply_now"


class PrivateEffort(StrEnum):
    CASUAL = "casual"
    SHORT = "short"
    SERIOUS = "serious"


class PrivateIntent(StrEnum):
    ACKNOWLEDGEMENT = "acknowledgement"
    WAIT_FOR_MORE = "wait_for_more"
    TRANSPORT_ONLY = "transport_only"
    GREETING = "greeting"
    IDENTITY_PROBE = "identity_probe"
    CHECK_CAPABILITY = "check_capability"
    IS_BOT_PROBE = "is_bot_probe"
    PERSONAL_PROBE = "personal_probe"
    MISSING_MATERIAL = "missing_material"
    TOO_BROAD = "too_broad"
    UNCERTAIN_DEBUG = "uncertain_debug"
    DAILY_REQUEST_CASUAL = "daily_request_casual"
    UNCLEAR_REQUEST = "unclear_request"
    IMAGE_NO_CONTEXT = "image_no_context"
    DAILY_REQUEST = "daily_request"
    SPECIFIC_TASK = "specific_task"
    GENERAL_QUESTION = "general_question"
    CONVERSATION = "conversation"
    OTHER = "other"


class PrivateResponseMode(StrEnum):
    TEMPLATE = "template"
    AGENT = "agent"
    NONE = "none"


class PrivateParseQuality(StrEnum):
    SCHEMA_VALID = "schema_valid"
    SCHEMA_REPAIRED = "schema_repaired"
    INVALID = "invalid"


class PrivateConflictSignal(StrEnum):
    ACTION = "action"
    INTENT = "intent"
    MATERIAL = "material"
    CONTEXT = "context"


class PrivateMaterialState(StrEnum):
    NONE = "none"
    MISSING = "missing"
    PROVIDED = "provided"
    ATTACHMENT_ONLY = "attachment_only"
    TRANSPORT_ONLY = "transport_only"
    UNKNOWN = "unknown"


class PrivateModelReasonCode(StrEnum):
    NO_CONVERSATION_INTENT = "no_conversation_intent"
    USER_WILL_CONTINUE = "user_will_continue"
    CASUAL_EXCHANGE = "casual_exchange"
    CLEAR_REQUEST = "clear_request"
    AMBIGUOUS_INPUT = "ambiguous_input"
    MATERIAL_MISSING = "material_missing"
    MATERIAL_PROVIDED = "material_provided"
    ATTACHMENT_REQUIRES_CONTEXT = "attachment_requires_context"


PRIVATE_TEMPLATE_INTENTS = frozenset({
    PrivateIntent.IDENTITY_PROBE,
    PrivateIntent.CHECK_CAPABILITY,
    PrivateIntent.IS_BOT_PROBE,
    PrivateIntent.PERSONAL_PROBE,
    PrivateIntent.MISSING_MATERIAL,
    PrivateIntent.TOO_BROAD,
    PrivateIntent.UNCERTAIN_DEBUG,
    PrivateIntent.DAILY_REQUEST_CASUAL,
    PrivateIntent.UNCLEAR_REQUEST,
    PrivateIntent.IMAGE_NO_CONTEXT,
})

PRIVATE_TEMPLATE_INTENT_VALUES = tuple(
    sorted(intent.value for intent in PRIVATE_TEMPLATE_INTENTS)
)
PRIVATE_ACTION_VALUES = tuple(action.value for action in PrivateAction)
PRIVATE_EFFORT_VALUES = tuple(effort.value for effort in PrivateEffort)
PRIVATE_INTENT_VALUES = tuple(intent.value for intent in PrivateIntent)
PRIVATE_RESPONSE_MODE_VALUES = tuple(
    mode.value for mode in PrivateResponseMode
)
PRIVATE_CONFLICT_SIGNAL_VALUES = tuple(
    signal.value for signal in PrivateConflictSignal
)
PRIVATE_MATERIAL_STATE_VALUES = tuple(
    state.value for state in PrivateMaterialState
)
PRIVATE_MODEL_REASON_CODE_VALUES = tuple(
    code.value for code in PrivateModelReasonCode
)


def complexity_for_effort(
    effort: PrivateEffort | str,
    *,
    action: PrivateAction | str,
) -> int:
    """把模型的有限 effort 投影为现有 Agent 预算兼容值。"""

    normalized_action = PrivateAction(action)
    if normalized_action is not PrivateAction.REPLY_NOW:
        return 0
    normalized_effort = PrivateEffort(effort)
    if normalized_effort is PrivateEffort.SERIOUS:
        return 6
    if normalized_effort is PrivateEffort.CASUAL:
        return 2
    return 3


@dataclass(frozen=True, slots=True)
class PrivateDecision:
    """应用层最终决策；模型提案与真正生效结果分开记录。"""

    action: PrivateAction
    effort: PrivateEffort
    intent: PrivateIntent
    response_mode: PrivateResponseMode
    confidence: float
    parse_quality: PrivateParseQuality
    error_type: str | None
    conflicting_signals: tuple[PrivateConflictSignal, ...]
    material_state: PrivateMaterialState
    reason_code: str
    contract_version: str
    task_run_id: str
    complexity: int
    timing_scoring: dict[str, Any] | None
    policy_mode: str
    policy_source: str
    proposed_action: PrivateAction | None = None
    proposed_effort: PrivateEffort | None = None
    proposed_intent: PrivateIntent | None = None
    proposed_response_mode: PrivateResponseMode | None = None
    proposed_reason_code: str = ""
    runtime_preset: str = PRIVATE_RUNTIME_PRESET

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", PrivateAction(self.action))
        object.__setattr__(self, "effort", PrivateEffort(self.effort))
        object.__setattr__(self, "intent", PrivateIntent(self.intent))
        object.__setattr__(
            self,
            "response_mode",
            PrivateResponseMode(self.response_mode),
        )
        object.__setattr__(
            self,
            "parse_quality",
            PrivateParseQuality(self.parse_quality),
        )
        object.__setattr__(
            self,
            "conflicting_signals",
            tuple(
                PrivateConflictSignal(signal)
                for signal in self.conflicting_signals
            ),
        )
        object.__setattr__(
            self,
            "material_state",
            PrivateMaterialState(self.material_state),
        )
        for field_name, enum_type in (
            ("proposed_action", PrivateAction),
            ("proposed_effort", PrivateEffort),
            ("proposed_intent", PrivateIntent),
            ("proposed_response_mode", PrivateResponseMode),
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, enum_type(value))
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("PrivateDecision.confidence 必须位于 0..1")
        object.__setattr__(self, "confidence", confidence)
        if self.response_mode is PrivateResponseMode.NONE and (
            self.action is PrivateAction.REPLY_NOW
        ):
            raise ValueError("reply_now 不能使用 none response_mode")
        if self.action is not PrivateAction.REPLY_NOW and (
            self.response_mode is not PrivateResponseMode.NONE
        ):
            raise ValueError("非 reply_now 动作必须使用 none response_mode")
        if self.complexity != complexity_for_effort(
            self.effort,
            action=self.action,
        ):
            raise ValueError("PrivateDecision.complexity 与 action/effort 不一致")
        for field_name in (
            "error_type",
            "reason_code",
            "contract_version",
            "task_run_id",
            "policy_mode",
            "policy_source",
            "proposed_reason_code",
        ):
            value = getattr(self, field_name)
            if value is not None and any(
                ord(character) < 32 for character in str(value)
            ):
                raise ValueError(f"PrivateDecision.{field_name} 含控制字符")

    @property
    def reason(self) -> str:
        """兼容现有持久化字段；只返回稳定 reason code。"""

        return self.reason_code


__all__ = [
    "PRIVATE_ACTION_VALUES",
    "PRIVATE_CONFLICT_SIGNAL_VALUES",
    "PRIVATE_DECISION_CONTRACT_VERSION",
    "PRIVATE_EFFORT_VALUES",
    "PRIVATE_INTENT_VALUES",
    "PRIVATE_MATERIAL_STATE_VALUES",
    "PRIVATE_MODEL_REASON_CODE_VALUES",
    "PRIVATE_RESPONSE_MODE_VALUES",
    "PRIVATE_RUNTIME_PRESET",
    "PRIVATE_TEMPLATE_INTENTS",
    "PRIVATE_TEMPLATE_INTENT_VALUES",
    "PrivateAction",
    "PrivateConflictSignal",
    "PrivateDecision",
    "PrivateEffort",
    "PrivateIntent",
    "PrivateMaterialState",
    "PrivateModelReasonCode",
    "PrivateParseQuality",
    "PrivateResponseMode",
    "complexity_for_effort",
]
