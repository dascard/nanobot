"""私聊 Timing v2 的确定性质量、灰度与降级策略。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.private_timing_contracts import (
    PRIVATE_DECISION_CONTRACT_VERSION,
    PRIVATE_TEMPLATE_INTENTS,
    PrivateAction,
    PrivateConflictSignal,
    PrivateDecision,
    PrivateEffort,
    PrivateIntent,
    PrivateMaterialState,
    PrivateModelReasonCode,
    PrivateParseQuality,
    PrivateResponseMode,
    complexity_for_effort,
)
from core.settings_service import settings


class PrivateTimingRolloutMode(StrEnum):
    DISABLED = "disabled"
    OBSERVATION = "observation"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class _ModelProposal:
    action: PrivateAction
    effort: PrivateEffort
    intent: PrivateIntent
    response_mode: PrivateResponseMode
    confidence: float
    parse_quality: PrivateParseQuality
    error_type: str | None
    conflicting_signals: tuple[PrivateConflictSignal, ...]
    material_state: PrivateMaterialState
    reason_code: PrivateModelReasonCode
    contract_version: str
    task_run_id: str


@dataclass(frozen=True, slots=True)
class PrivateTimingPolicy:
    """只读取类型化模型结果和数值门槛，不读取原始用户文本。"""

    mode: PrivateTimingRolloutMode = PrivateTimingRolloutMode.DISABLED
    decision_confidence_threshold: float = 0.70
    template_confidence_threshold: float = 0.85
    source: str = "default"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "mode",
            PrivateTimingRolloutMode(self.mode),
        )
        decision_threshold = float(self.decision_confidence_threshold)
        template_threshold = float(self.template_confidence_threshold)
        if not 0 <= decision_threshold <= template_threshold <= 1:
            raise ValueError(
                "PrivateTimingPolicy 置信度门槛必须满足 "
                "0 <= decision <= template <= 1"
            )
        object.__setattr__(
            self,
            "decision_confidence_threshold",
            decision_threshold,
        )
        object.__setattr__(
            self,
            "template_confidence_threshold",
            template_threshold,
        )
        source = str(self.source or "").strip()
        if not source or any(ord(character) < 32 for character in source):
            raise ValueError("PrivateTimingPolicy.source 无效")
        object.__setattr__(self, "source", source)

    def disabled_decision(
        self,
        *,
        timing_scoring: dict[str, Any] | None,
    ) -> PrivateDecision:
        return self._agent_fallback(
            error_type=None,
            reason_code="feature_disabled",
            parse_quality=PrivateParseQuality.INVALID,
            timing_scoring=timing_scoring,
        )

    def structural_empty_decision(
        self,
        *,
        timing_scoring: dict[str, Any] | None,
    ) -> PrivateDecision:
        return PrivateDecision(
            action=PrivateAction.NO_REPLY,
            effort=PrivateEffort.SHORT,
            intent=PrivateIntent.OTHER,
            response_mode=PrivateResponseMode.NONE,
            confidence=1.0,
            parse_quality=PrivateParseQuality.SCHEMA_VALID,
            error_type=None,
            conflicting_signals=(),
            material_state=PrivateMaterialState.NONE,
            reason_code="empty_event",
            contract_version=PRIVATE_DECISION_CONTRACT_VERSION,
            task_run_id="",
            complexity=0,
            timing_scoring=timing_scoring,
            policy_mode=self.mode.value,
            policy_source=self.source,
        )

    def decide(
        self,
        model_result: Mapping[str, Any],
        *,
        timing_scoring: dict[str, Any] | None,
    ) -> PrivateDecision:
        """把模型提案收敛为可执行结果；任何歧义都回到正常 Agent。"""

        try:
            proposal = _proposal_from_mapping(model_result)
        except (KeyError, TypeError, ValueError):
            return self._agent_fallback(
                error_type="invalid_contract",
                reason_code="task_failure",
                parse_quality=PrivateParseQuality.INVALID,
                timing_scoring=timing_scoring,
            )

        if proposal.parse_quality is not PrivateParseQuality.SCHEMA_VALID:
            return self._agent_fallback(
                error_type=proposal.error_type or "invalid_contract",
                reason_code="task_failure",
                parse_quality=PrivateParseQuality.INVALID,
                timing_scoring=timing_scoring,
                proposal=proposal,
            )
        if proposal.error_type:
            return self._agent_fallback(
                error_type=proposal.error_type,
                reason_code="task_failure",
                parse_quality=PrivateParseQuality.INVALID,
                timing_scoring=timing_scoring,
                proposal=proposal,
            )
        if proposal.confidence < self.decision_confidence_threshold:
            return self._agent_fallback(
                error_type="low_confidence",
                reason_code="low_confidence",
                parse_quality=proposal.parse_quality,
                timing_scoring=timing_scoring,
                proposal=proposal,
            )
        if proposal.conflicting_signals:
            return self._agent_fallback(
                error_type="conflicting_signals",
                reason_code="conflicting_signals",
                parse_quality=proposal.parse_quality,
                timing_scoring=timing_scoring,
                proposal=proposal,
            )
        if proposal.response_mode is PrivateResponseMode.TEMPLATE and (
            proposal.intent not in PRIVATE_TEMPLATE_INTENTS
            or proposal.effort is not PrivateEffort.CASUAL
            or proposal.confidence < self.template_confidence_threshold
        ):
            return self._agent_fallback(
                error_type="template_policy_rejected",
                reason_code="template_policy_rejected",
                parse_quality=proposal.parse_quality,
                timing_scoring=timing_scoring,
                proposal=proposal,
            )
        if self.mode is PrivateTimingRolloutMode.OBSERVATION:
            return self._agent_fallback(
                error_type=None,
                reason_code="observation_only",
                parse_quality=proposal.parse_quality,
                timing_scoring=timing_scoring,
                proposal=proposal,
            )
        if self.mode is PrivateTimingRolloutMode.DISABLED:
            return self.disabled_decision(timing_scoring=timing_scoring)
        return PrivateDecision(
            action=proposal.action,
            effort=proposal.effort,
            intent=proposal.intent,
            response_mode=proposal.response_mode,
            confidence=proposal.confidence,
            parse_quality=proposal.parse_quality,
            error_type=None,
            conflicting_signals=proposal.conflicting_signals,
            material_state=proposal.material_state,
            reason_code=proposal.reason_code.value,
            contract_version=proposal.contract_version,
            task_run_id=proposal.task_run_id,
            complexity=complexity_for_effort(
                proposal.effort,
                action=proposal.action,
            ),
            timing_scoring=timing_scoring,
            policy_mode=self.mode.value,
            policy_source=self.source,
        )

    def _agent_fallback(
        self,
        *,
        error_type: str | None,
        reason_code: str,
        parse_quality: PrivateParseQuality,
        timing_scoring: dict[str, Any] | None,
        proposal: _ModelProposal | None = None,
    ) -> PrivateDecision:
        return PrivateDecision(
            action=PrivateAction.REPLY_NOW,
            effort=PrivateEffort.SHORT,
            intent=PrivateIntent.OTHER,
            response_mode=PrivateResponseMode.AGENT,
            confidence=proposal.confidence if proposal is not None else 0.0,
            parse_quality=parse_quality,
            error_type=error_type,
            conflicting_signals=(
                proposal.conflicting_signals if proposal is not None else ()
            ),
            material_state=(
                proposal.material_state
                if proposal is not None
                else PrivateMaterialState.UNKNOWN
            ),
            reason_code=reason_code,
            contract_version=(
                proposal.contract_version
                if proposal is not None
                else PRIVATE_DECISION_CONTRACT_VERSION
            ),
            task_run_id=(
                proposal.task_run_id if proposal is not None else ""
            ),
            complexity=3,
            timing_scoring=timing_scoring,
            policy_mode=self.mode.value,
            policy_source=self.source,
            proposed_action=(
                proposal.action if proposal is not None else None
            ),
            proposed_effort=(
                proposal.effort if proposal is not None else None
            ),
            proposed_intent=(
                proposal.intent if proposal is not None else None
            ),
            proposed_response_mode=(
                proposal.response_mode if proposal is not None else None
            ),
            proposed_reason_code=(
                proposal.reason_code.value if proposal is not None else ""
            ),
        )


def _proposal_from_mapping(
    value: Mapping[str, Any],
) -> _ModelProposal:
    conflicts = value["conflicting_signals"]
    if not isinstance(conflicts, (list, tuple)):
        raise TypeError("conflicting_signals 必须是数组")
    confidence = value["confidence"]
    if isinstance(confidence, bool):
        raise TypeError("confidence 不能是布尔值")
    error_value = value.get("error_type")
    error_type = None if error_value in {None, ""} else str(error_value)
    contract_version = str(value["contract_version"])
    if contract_version != PRIVATE_DECISION_CONTRACT_VERSION:
        raise ValueError("private decision contract version 不匹配")
    return _ModelProposal(
        action=PrivateAction(value["action"]),
        effort=PrivateEffort(value["effort"]),
        intent=PrivateIntent(value["intent"]),
        response_mode=PrivateResponseMode(value["response_mode"]),
        confidence=float(confidence),
        parse_quality=PrivateParseQuality(value["parse_quality"]),
        error_type=error_type,
        conflicting_signals=tuple(
            PrivateConflictSignal(signal) for signal in conflicts
        ),
        material_state=PrivateMaterialState(value["material_state"]),
        reason_code=PrivateModelReasonCode(value["reason_code"]),
        contract_version=contract_version,
        task_run_id=str(value.get("task_run_id") or ""),
    )


def _parse_rollout_mode(value: object) -> PrivateTimingRolloutMode:
    try:
        return PrivateTimingRolloutMode(str(value or "").strip().lower())
    except ValueError:
        return PrivateTimingRolloutMode.DISABLED


def _session_modes(raw: str) -> dict[str, PrivateTimingRolloutMode]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(session_id).strip(): _parse_rollout_mode(mode)
        for session_id, mode in value.items()
        if str(session_id).strip()
    }


def resolve_private_timing_policy(
    session_id: str,
) -> PrivateTimingPolicy:
    """按 canonical session 解析灰度；正式生效还需独立发布门禁。"""

    session_key = str(session_id or "").strip()
    modes = _session_modes(
        settings.get_str(
            "private_timing.rollout.session_modes",
            "{}",
        )
    )
    if session_key and session_key in modes:
        requested = modes[session_key]
        source = f"session:{requested.value}"
    else:
        requested = _parse_rollout_mode(
            settings.get_str(
                "private_timing.rollout.default_mode",
                "disabled",
            )
        )
        source = "default"
    if (
        requested is PrivateTimingRolloutMode.ACTIVE
        and not settings.get_bool(
            "private_timing.rollout.active_allowed",
            False,
        )
    ):
        requested = PrivateTimingRolloutMode.OBSERVATION
        source = "session:active_blocked"
    return PrivateTimingPolicy(
        mode=requested,
        decision_confidence_threshold=settings.get_float(
            "private_timing.decision_confidence_threshold",
            0.70,
        ),
        template_confidence_threshold=settings.get_float(
            "private_timing.template_confidence_threshold",
            0.85,
        ),
        source=source,
    )


__all__ = [
    "PrivateTimingPolicy",
    "PrivateTimingRolloutMode",
    "resolve_private_timing_policy",
]
