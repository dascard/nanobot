"""TimingGate 候选参数离线模拟。"""
from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core import timing_score as live_timing
from core.timing_score import TimingModelHint, TimingSignals, decide_timing, extract_signals


SIGNAL_PARAM_NAMES = {
    "s_ack",
    "s_transport",
    "s_other",
    "s_bot",
    "w_marker",
    "w_file",
    "w_incomplete",
}


def load_timing_cases(path: str | Path) -> list[dict[str, Any]]:
    root = Path(path)
    if not root.exists():
        return []
    if root.is_file():
        return [_load_case_file(root)]
    cases: list[dict[str, Any]] = []
    for item in sorted(root.rglob("*.json")):
        cases.append(_load_case_file(item))
    return cases


def _load_case_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    payload.setdefault("source_ref", str(path))
    return payload


def _clip01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, min(1.0, number))


def _round_score(value: Any) -> float:
    return round(_clip01(value), 6)


def _noisy_or(*values: Any) -> float:
    product = 1.0
    for value in values:
        product *= 1.0 - _clip01(value)
    return _round_score(1.0 - product)


def _float_param(param_diff: dict[str, Any], name: str, default: float) -> float:
    if name not in param_diff:
        return float(default)
    try:
        return float(param_diff[name])
    except (TypeError, ValueError):
        return float(default)


def _expected_action(case: dict[str, Any]) -> str:
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    return str(
        expected.get("timing_action")
        or expected.get("expected_action")
        or expected.get("action")
        or ""
    )


def _case_id(case: dict[str, Any]) -> str:
    return str(case.get("case_id") or case.get("id") or "unknown")


def _sample_expected_action(sample: dict[str, Any]) -> str:
    for field in ("final_timing_action", "timing_action_truth", "expected_action"):
        value = str(sample.get(field) or "").strip()
        if value:
            return value
    return ""


def _audit_sample_case(sample: dict[str, Any]) -> dict[str, Any] | None:
    timing_input = sample.get("timing_input")
    if not isinstance(timing_input, dict):
        return None
    log_id = sample.get("log_id")
    signal_name = str(sample.get("signal_name") or "")
    return {
        "case_id": f"audit:{log_id or 'unknown'}:{signal_name or 'unknown'}",
        "input": dict(timing_input),
        "expected": {"timing_action": _sample_expected_action(sample)},
    }


def _last_message_text(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("content") or "").strip()
        if text:
            return text
    return ""


def _model_hint_from_input(inp: dict[str, Any]) -> TimingModelHint | None:
    data = inp.get("model_hint")
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "").strip()
    if not action:
        return None
    return TimingModelHint(
        action=action,
        confidence=float(data.get("confidence", 0.0) or 0.0),
        raw=str(data.get("raw") or ""),
        reason=str(data.get("reason") or ""),
    )


def _decision_kwargs(case: dict[str, Any]) -> dict[str, Any]:
    inp = case.get("input") if isinstance(case.get("input"), dict) else {}
    trigger_reason = str(inp.get("trigger_reason") or "").strip()
    text = str(
        inp.get("text")
        or inp.get("message")
        or _last_message_text(inp.get("messages"))
        or ""
    )
    return {
        "text": text,
        "is_group": bool(inp.get("is_group", True)),
        "is_private": bool(inp.get("is_private", False)),
        "is_at_bot": bool(inp.get("is_at_bot", trigger_reason == "at_bot")),
        "is_reply_to_bot": bool(
            inp.get("is_reply_to_bot", trigger_reason == "reply_to_bot")
        ),
        "bot_name_mentioned": bool(
            inp.get(
                "bot_name_mentioned",
                trigger_reason in {"bot_name_mentioned", "mentioned"},
            )
        ),
        "direct_call": bool(inp.get("direct_call", trigger_reason == "direct_call")),
        "is_directed_to_other": bool(inp.get("is_directed_to_other", False)),
        "has_other_recipient": bool(inp.get("has_other_recipient", False)),
        "is_other_bot": bool(inp.get("is_other_bot", False)),
        "has_files": bool(inp.get("has_files", False)),
        "linger_score": float(inp.get("linger_score", 0.0) or 0.0),
        "force_direct_score": float(inp.get("force_direct_score", 0.0) or 0.0),
        "min_interval_active": bool(inp.get("min_interval_active", False)),
        "min_interval_remaining": float(inp.get("min_interval_remaining", 0.0) or 0.0),
        "model_hint": _model_hint_from_input(inp),
    }


def _apply_signal_overrides(
    signals: TimingSignals,
    param_diff: dict[str, Any],
) -> TimingSignals:
    data = asdict(signals)
    sub_signals = dict(data["sub_signals"])
    for name in SIGNAL_PARAM_NAMES:
        if name in param_diff:
            sub_signals[name] = _round_score(param_diff[name])

    suppress_score = _noisy_or(
        sub_signals.get("s_ack", 0.0),
        sub_signals.get("s_transport", 0.0),
        sub_signals.get("s_other", 0.0),
        sub_signals.get("s_bot", 0.0),
    )
    wait_signal = _noisy_or(
        sub_signals.get("w_marker", 0.0),
        sub_signals.get("w_file", 0.0),
        sub_signals.get("w_incomplete", 0.0),
    )
    return TimingSignals(
        explicit_direct_score=float(data["explicit_direct_score"]),
        linger_score=float(data["linger_score"]),
        direct_score=float(data["direct_score"]),
        wait_signal=wait_signal,
        suppress_score=suppress_score,
        sub_signals=sub_signals,
    )


def _clip_delay(value: int) -> int:
    return max(3, min(15, int(value)))


def _action_after_readiness(
    *,
    participate: bool,
    wait_signal: float,
    min_interval_active: bool,
    min_interval_remaining: float,
    model_action: str = "",
) -> tuple[str, int | None, str]:
    if not participate:
        return "no_reply", None, "participation_score_below_theta"
    if min_interval_active:
        delay = _clip_delay(math.ceil(max(0.0, min_interval_remaining)))
        return "wait", delay, "min_interval"
    if wait_signal >= 0.8:
        return "wait", 8, "strong_wait_signal"
    if wait_signal >= 0.4:
        return "wait", 5, "weak_wait_signal"
    if str(model_action or "").strip().lower() == "wait":
        return "wait", 5, "model_wait"
    return "continue", None, "ready"


def _valid_model_hint(model_hint: TimingModelHint | None) -> bool:
    if model_hint is None:
        return False
    if _clip01(model_hint.confidence) <= 0:
        return False
    return str(model_hint.action or "").strip().lower() in {
        "no_reply",
        "wait",
        "reply_now",
        "continue",
    }


def _soft_reject_cap_limit(signals: TimingSignals, gamma: float) -> float:
    s_bot = _clip01(signals.sub_signals.get("s_bot", 0.0))
    if s_bot <= 0:
        return 1.0
    return _round_score(1.0 - gamma * s_bot)


def _soft_reject_cap(score: float, signals: TimingSignals, gamma: float) -> float:
    return _round_score(min(score, _soft_reject_cap_limit(signals, gamma)))


def _decision_payload(
    *,
    action: str,
    stage: str,
    participation_score: float,
    final_score: float,
    theta: float,
    low_threshold: float,
    high_threshold: float,
    conflict_score: float,
    soft_reject_cap: float,
    delay_seconds: int | None,
    model_used: bool,
    model_action: str,
    model_confidence: float,
    model_weight: float,
    signals: TimingSignals,
    reason: str,
) -> dict[str, Any]:
    return {
        "action": action,
        "stage": stage,
        "participation_score": _round_score(participation_score),
        "final_score": _round_score(final_score),
        "theta": round(float(theta), 6),
        "low_threshold": round(float(low_threshold), 6),
        "high_threshold": round(float(high_threshold), 6),
        "conflict_score": _round_score(conflict_score),
        "soft_reject_cap": _round_score(soft_reject_cap),
        "delay_seconds": delay_seconds,
        "model_used": model_used,
        "model_action": model_action,
        "model_confidence": _round_score(model_confidence),
        "model_weight": _round_score(model_weight),
        "reason": reason,
        "signals": asdict(signals),
    }


def _public_decision(decision: Any) -> dict[str, Any]:
    payload = asdict(decision)
    return {
        "action": payload["action"],
        "stage": payload["stage"],
        "participation_score": payload["participation_score"],
        "final_score": payload["final_score"],
        "theta": payload["theta"],
        "low_threshold": payload["low_threshold"],
        "high_threshold": payload["high_threshold"],
        "conflict_score": payload["conflict_score"],
        "soft_reject_cap": payload["soft_reject_cap"],
        "delay_seconds": payload["delay_seconds"],
        "model_used": payload["model_used"],
        "model_action": payload["model_action"],
        "model_confidence": payload["model_confidence"],
        "model_weight": payload["model_weight"],
        "reason": payload["reason"],
    }


def _simulate_decision(
    kwargs: dict[str, Any],
    param_diff: dict[str, Any],
) -> dict[str, Any]:
    signal_kwargs = {
        name: value
        for name, value in kwargs.items()
        if name not in {
            "min_interval_active",
            "min_interval_remaining",
            "model_hint",
        }
    }
    signals = _apply_signal_overrides(extract_signals(**signal_kwargs), param_diff)
    base_score = _float_param(param_diff, "BASE_SCORE", live_timing.BASE_SCORE)
    direct_weight = _float_param(param_diff, "DIRECT_WEIGHT", live_timing.DIRECT_WEIGHT)
    suppress_weight = _float_param(
        param_diff,
        "SUPPRESS_WEIGHT",
        live_timing.SUPPRESS_WEIGHT,
    )
    margin = _float_param(param_diff, "DECISION_MARGIN", live_timing.DECISION_MARGIN)
    conflict_threshold = _float_param(
        param_diff,
        "CONFLICT_THRESHOLD",
        live_timing.CONFLICT_THRESHOLD,
    )
    model_weight_scale = _float_param(
        param_diff,
        "MODEL_WEIGHT_SCALE",
        live_timing.MODEL_WEIGHT_SCALE,
    )
    bot_soft_reject_gamma = _float_param(
        param_diff,
        "BOT_SOFT_REJECT_GAMMA",
        live_timing.BOT_SOFT_REJECT_GAMMA,
    )

    rule_score = _round_score(
        base_score
        + direct_weight * signals.direct_score
        - suppress_weight * signals.suppress_score
    )
    theta = live_timing.select_theta(signals, is_private=bool(kwargs.get("is_private")))
    low = round(max(0.0, theta - margin), 6)
    high = round(min(1.0, theta + margin), 6)
    kappa = min(signals.direct_score, signals.suppress_score)
    conflict_score = _round_score(kappa)
    soft_reject_cap = _soft_reject_cap_limit(signals, bot_soft_reject_gamma)
    model_hint = kwargs.get("model_hint")
    model_action = str(model_hint.action if model_hint else "" or "").strip().lower()
    model_confidence = _clip01(float(model_hint.confidence if model_hint else 0.0))
    model_weight = _round_score(model_confidence * model_weight_scale)
    min_interval_active = bool(kwargs.get("min_interval_active", False))
    min_interval_remaining = float(kwargs.get("min_interval_remaining", 0.0) or 0.0)

    if kappa >= conflict_threshold:
        if _valid_model_hint(model_hint):
            final_score = _soft_reject_cap(
                (1.0 - model_weight) * rule_score
                + model_weight * live_timing.compute_model_prior(model_action),
                signals,
                bot_soft_reject_gamma,
            )
            participate = final_score >= theta
            action, delay, readiness_reason = _action_after_readiness(
                participate=participate,
                wait_signal=signals.wait_signal,
                min_interval_active=min_interval_active,
                min_interval_remaining=min_interval_remaining,
                model_action=model_action,
            )
            return _decision_payload(
                action=action,
                stage="model_assisted_conflict",
                participation_score=rule_score,
                final_score=final_score,
                theta=theta,
                low_threshold=low,
                high_threshold=high,
                conflict_score=conflict_score,
                soft_reject_cap=soft_reject_cap,
                delay_seconds=delay,
                model_used=True,
                model_action=model_action,
                model_confidence=model_confidence,
                model_weight=model_weight,
                signals=signals,
                reason=f"conflict_kappa={round(kappa, 3)}; {readiness_reason}",
            )
        participate = rule_score >= theta
        action, delay, readiness_reason = _action_after_readiness(
            participate=participate,
            wait_signal=signals.wait_signal,
            min_interval_active=min_interval_active,
            min_interval_remaining=min_interval_remaining,
            model_action="",
        )
        return _decision_payload(
            action=action,
            stage="rule_fallback",
            participation_score=rule_score,
            final_score=rule_score,
            theta=theta,
            low_threshold=low,
            high_threshold=high,
            conflict_score=conflict_score,
            soft_reject_cap=soft_reject_cap,
            delay_seconds=delay,
            model_used=False,
            model_action=model_action,
            model_confidence=model_confidence,
            model_weight=0.0,
            signals=signals,
            reason=f"conflict_model_unavailable; {readiness_reason}",
        )

    if rule_score >= high:
        action, delay, readiness_reason = _action_after_readiness(
            participate=True,
            wait_signal=signals.wait_signal,
            min_interval_active=min_interval_active,
            min_interval_remaining=min_interval_remaining,
            model_action="",
        )
        return _decision_payload(
            action=action,
            stage="rule_shortcut",
            participation_score=rule_score,
            final_score=rule_score,
            theta=theta,
            low_threshold=low,
            high_threshold=high,
            conflict_score=conflict_score,
            soft_reject_cap=soft_reject_cap,
            delay_seconds=delay,
            model_used=False,
            model_action="",
            model_confidence=0.0,
            model_weight=0.0,
            signals=signals,
            reason=f"rule_score_above_high; {readiness_reason}",
        )

    if rule_score <= low:
        return _decision_payload(
            action="no_reply",
            stage="rule_shortcut",
            participation_score=rule_score,
            final_score=rule_score,
            theta=theta,
            low_threshold=low,
            high_threshold=high,
            conflict_score=conflict_score,
            soft_reject_cap=soft_reject_cap,
            delay_seconds=None,
            model_used=False,
            model_action="",
            model_confidence=0.0,
            model_weight=0.0,
            signals=signals,
            reason="rule_score_below_low",
        )

    if _valid_model_hint(model_hint):
        final_score = _soft_reject_cap(
            (1.0 - model_weight) * rule_score
            + model_weight * live_timing.compute_model_prior(model_action),
            signals,
            bot_soft_reject_gamma,
        )
        participate = final_score >= theta
        action, delay, readiness_reason = _action_after_readiness(
            participate=participate,
            wait_signal=signals.wait_signal,
            min_interval_active=min_interval_active,
            min_interval_remaining=min_interval_remaining,
            model_action=model_action,
        )
        return _decision_payload(
            action=action,
            stage="model_assisted",
            participation_score=rule_score,
            final_score=final_score,
            theta=theta,
            low_threshold=low,
            high_threshold=high,
            conflict_score=conflict_score,
            soft_reject_cap=soft_reject_cap,
            delay_seconds=delay,
            model_used=True,
            model_action=model_action,
            model_confidence=model_confidence,
            model_weight=model_weight,
            signals=signals,
            reason=f"fuzzy_band; {readiness_reason}",
        )

    participate = rule_score >= theta
    action, delay, readiness_reason = _action_after_readiness(
        participate=participate,
        wait_signal=signals.wait_signal,
        min_interval_active=min_interval_active,
        min_interval_remaining=min_interval_remaining,
        model_action="",
    )
    return _decision_payload(
        action=action,
        stage="rule_fallback",
        participation_score=rule_score,
        final_score=rule_score,
        theta=theta,
        low_threshold=low,
        high_threshold=high,
        conflict_score=conflict_score,
        soft_reject_cap=soft_reject_cap,
        delay_seconds=delay,
        model_used=False,
        model_action=model_action,
        model_confidence=model_confidence,
        model_weight=0.0,
        signals=signals,
        reason=f"model_unavailable; {readiness_reason}",
    )


def _risk_tag(expected: str, before: str, after: str) -> str:
    if expected and after == expected and before != expected:
        return "expected_improved"
    if expected and before == expected and after != expected:
        return "regression_risk"
    return "neutral_flip"


def simulate_timing_candidates(
    cases: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    audit_samples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    flips: list[dict[str, Any]] = []
    aggregates: list[dict[str, Any]] = []
    sources = {
        "eval_case_count": len(cases),
        "audit_sample_count": 0,
        "skipped_audit_sample_count": 0,
    }
    replay_cases: list[dict[str, Any]] = [
        {
            "case": case,
            "source_type": "eval_case",
            "log_id": None,
            "signal_name": "",
        }
        for case in cases
        if isinstance(case, dict)
    ]
    for sample in audit_samples or []:
        if not isinstance(sample, dict):
            sources["skipped_audit_sample_count"] += 1
            continue
        sample_case = _audit_sample_case(sample)
        if sample_case is None:
            sources["skipped_audit_sample_count"] += 1
            continue
        sources["audit_sample_count"] += 1
        replay_cases.append(
            {
                "case": sample_case,
                "source_type": "timing_signal_audit_sample",
                "log_id": sample.get("log_id"),
                "signal_name": str(sample.get("signal_name") or ""),
            }
        )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("id") or "")
        param_diff = (
            candidate.get("param_diff")
            if isinstance(candidate.get("param_diff"), dict)
            else {}
        )
        risk_counts = {
            "expected_improved": 0,
            "regression_risk": 0,
            "neutral_flip": 0,
        }
        for item in replay_cases:
            case = item["case"]
            kwargs = _decision_kwargs(case)
            before = _public_decision(decide_timing(**kwargs))
            after = _simulate_decision(kwargs, param_diff)
            if before["action"] == after["action"]:
                continue
            expected = _expected_action(case)
            risk_tag = _risk_tag(expected, before["action"], after["action"])
            risk_counts[risk_tag] += 1
            flips.append(
                {
                    "candidate_id": candidate_id,
                    "case_id": _case_id(case),
                    "source_type": item["source_type"],
                    "source_ref": str(case.get("source_ref") or ""),
                    "expected_action": expected,
                    "before": before,
                    "after": {
                        key: value
                        for key, value in after.items()
                        if key != "signals"
                    },
                    "signals": after["signals"],
                    "risk_tag": risk_tag,
                    "risk_level": str(candidate.get("risk_level") or "unknown"),
                    "trigger_reason": str(
                        (case.get("input") or {}).get("trigger_reason")
                        if isinstance(case.get("input"), dict)
                        else ""
                    ),
                    "log_id": item["log_id"],
                    "signal_name": item["signal_name"],
                }
            )
        flip_count = sum(risk_counts.values())
        aggregates.append(
            {
                "candidate_id": candidate_id,
                "case_count": len(replay_cases),
                "flip_count": flip_count,
                "expected_improved_count": risk_counts["expected_improved"],
                "regression_risk_count": risk_counts["regression_risk"],
                "neutral_flip_count": risk_counts["neutral_flip"],
                "risk_level": str(candidate.get("risk_level") or "unknown"),
            }
        )
    return {
        "case_count": len(cases),
        "candidate_count": len(candidates),
        "flip_count": len(flips),
        "sources": sources,
        "flips": flips,
        "aggregates": aggregates,
    }
