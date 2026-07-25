"""私聊 Timing v2：单次结构化分类与确定性策略编排。"""

from __future__ import annotations

import asyncio
import logging
import urllib.error
from dataclasses import asdict, dataclass, field
from typing import Any

from core.model_provider.decision_runtime import classify_private_decision
from core.private_timing_contracts import (
    PRIVATE_DECISION_CONTRACT_VERSION,
    PRIVATE_RUNTIME_PRESET,
    PrivateDecision,
    PrivateParseQuality,
)
from core.private_timing_policy import (
    PrivateTimingPolicy,
    PrivateTimingRolloutMode,
    resolve_private_timing_policy,
)
from core.settings_service import settings
from core.timing_score import TimingDecision, TimingModelHint, decide_timing


logger = logging.getLogger("nanobot.private_timing")


_EFFORT_CONSTRAINTS = {
    "casual": (
        "本轮只随口接一句。不要分析、不要列步骤、不要调用工具、不要解释能力。"
        "回复控制在1句话，最好2-12个字。如果缺材料，只让对方发材料。"
    ),
    "short": (
        "本轮简短处理。先给判断，最多补1-3个要点，最多3句话。"
        "不要用Markdown标题，不要列表超过3条。不要写报告。"
    ),
    "serious": (
        "本轮认真处理。可以使用工具，可以分步骤分析。"
        "先给结论，再给依据、修改点和验收方式。"
    ),
}


def get_effort_constraint(effort: str | None) -> str:
    return _EFFORT_CONSTRAINTS.get(str(effort or ""), "")


def _failure_model_result(error_type: str) -> dict[str, Any]:
    return {
        "action": "reply_now",
        "effort": "short",
        "intent": "other",
        "response_mode": "agent",
        "confidence": 0.0,
        "parse_quality": "invalid",
        "error_type": error_type,
        "conflicting_signals": [],
        "material_state": "unknown",
        "reason_code": "ambiguous_input",
        "contract_version": PRIVATE_DECISION_CONTRACT_VERSION,
        "task_run_id": "",
    }


def _model_hint_from_result(
    result: dict[str, Any],
) -> TimingModelHint | None:
    if result.get("parse_quality") != PrivateParseQuality.SCHEMA_VALID.value:
        return None
    if result.get("error_type"):
        return None
    confidence = result.get("confidence")
    if isinstance(confidence, bool) or not isinstance(
        confidence,
        (int, float),
    ):
        return None
    return TimingModelHint(
        action=str(result.get("action") or ""),
        confidence=float(confidence),
        raw="",
        reason="",
    )


def _score_private_timing(
    text: str,
    *,
    has_files: bool,
    model_result: dict[str, Any] | None = None,
) -> tuple[TimingDecision, dict[str, Any]]:
    scoring = decide_timing(
        text,
        is_group=False,
        is_private=True,
        has_files=has_files,
        model_hint=(
            _model_hint_from_result(model_result)
            if model_result is not None
            else None
        ),
    )
    payload = asdict(scoring)
    payload["semantic_effect"] = "diagnostic_only"
    return scoring, payload


@dataclass
class PrivateTimingGate:
    """每条已灰度私聊最多调用一次语义 Task。"""

    classifier: object | None = None
    policy: PrivateTimingPolicy | None = None
    stats: dict[str, int] = field(default_factory=lambda: {
        "no_reply": 0,
        "wait": 0,
        "reply_now": 0,
        "total": 0,
        "model_calls": 0,
    })

    async def classify(
        self,
        text: str,
        *,
        user_id: str = "",
        session_id: str = "",
        has_files: bool = False,
    ) -> PrivateDecision:
        normalized = str(text or "").strip()
        self.stats["total"] += 1
        policy = self.policy or resolve_private_timing_policy(session_id)
        logger.info(
            "[PrivateDecision] start user=%s len=%d has_files=%s mode=%s",
            user_id,
            len(normalized),
            has_files,
            policy.mode.value,
        )

        _scoring, scoring_payload = _score_private_timing(
            normalized,
            has_files=has_files,
        )
        if not normalized and not has_files:
            decision = policy.structural_empty_decision(
                timing_scoring=scoring_payload,
            )
            self._record(decision)
            return decision
        if policy.mode is PrivateTimingRolloutMode.DISABLED:
            decision = policy.disabled_decision(
                timing_scoring=scoring_payload,
            )
            self._record(decision)
            return decision

        classifier = self.classifier
        classify = (
            classifier.classify
            if classifier is not None
            else classify_private_decision
        )
        self.stats["model_calls"] += 1
        try:
            model_result = await asyncio.wait_for(
                asyncio.to_thread(classify, normalized, has_files),
                timeout=(
                    settings.get_float("classifier.timeout", 15.0) + 1.0
                ),
            )
            if not isinstance(model_result, dict):
                model_result = dict(model_result)
        except asyncio.TimeoutError:
            logger.warning(
                "[PrivateDecision] timeout user=%s",
                user_id,
            )
            model_result = _failure_model_result("execution_timeout")
        except (ConnectionError, urllib.error.URLError) as exc:
            logger.warning(
                "[PrivateDecision] unavailable user=%s error_type=%s",
                user_id,
                type(exc).__name__,
            )
            model_result = _failure_model_result("provider_unavailable")

        _scoring, scoring_payload = _score_private_timing(
            normalized,
            has_files=has_files,
            model_result=model_result,
        )
        decision = policy.decide(
            model_result,
            timing_scoring=scoring_payload,
        )
        self._record(decision)
        logger.info(
            "[PrivateDecision] result user=%s action=%s mode=%s "
            "response_mode=%s intent=%s confidence=%.2f error=%s",
            user_id,
            decision.action.value,
            decision.policy_mode,
            decision.response_mode.value,
            decision.intent.value,
            decision.confidence,
            decision.error_type or "",
        )
        return decision

    def _record(self, decision: PrivateDecision) -> None:
        self.stats[decision.action.value] += 1


_gate: PrivateTimingGate | None = None


def get_private_gate() -> PrivateTimingGate:
    global _gate
    if _gate is None:
        _gate = PrivateTimingGate()
        logger.info("[PrivateGate] initialized")
    return _gate


__all__ = [
    "PRIVATE_RUNTIME_PRESET",
    "PrivateDecision",
    "PrivateTimingGate",
    "get_effort_constraint",
    "get_private_gate",
]
