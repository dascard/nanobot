"""TimingGate 评分体系纯函数。

本模块只实现信号、规则分数和模型融合，不直接替换现有 GroupRuntime 行为。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


BASE_SCORE = 0.10
DIRECT_WEIGHT = 0.85
SUPPRESS_WEIGHT = 0.85
DECISION_MARGIN = 0.18
CONFLICT_THRESHOLD = 0.35
MODEL_WEIGHT_SCALE = 0.875
BOT_SOFT_REJECT_GAMMA = 0.80


_ACK_WORDS = {
    "嗯",
    "嗯嗯",
    "哦",
    "噢",
    "好",
    "好的",
    "好滴",
    "收到",
    "ok",
    "okay",
    "行",
    "可以",
    "哈哈",
    "哈哈哈",
    "hh",
}
_REQUEST_MARKERS = (
    "帮我",
    "查下",
    "查一下",
    "看看",
    "看下",
    "继续",
    "总结",
    "怎么",
    "为什么",
    "能不能",
    "可以不",
    "发我",
    "给我",
    "解释",
    "分析",
)
_WAIT_MARKERS = (
    "等下",
    "等一下",
    "稍等",
    "我发图",
    "发图",
    "我发代码",
    "发代码",
    "还有",
    "下一段",
    "后面还有",
)
_SECRET_MARKERS = (
    "token",
    "secret",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "access_key",
    "private key",
    "-----begin",
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_HEX_BLOB_RE = re.compile(r"^[0-9a-fA-F]{64,}$")
_BASE64_BLOB_RE = re.compile(r"^[A-Za-z0-9+/_=-]{96,}$")


@dataclass(frozen=True)
class TimingSignals:
    explicit_direct_score: float
    linger_score: float
    direct_score: float
    wait_signal: float
    suppress_score: float
    sub_signals: dict[str, Any]


@dataclass(frozen=True)
class TimingModelHint:
    action: str
    confidence: float
    raw: str = ""
    reason: str = ""


@dataclass(frozen=True)
class TimingDecision:
    action: str
    stage: str
    participation_score: float
    final_score: float
    theta: float
    low_threshold: float
    high_threshold: float
    conflict_score: float
    soft_reject_cap: float
    delay_seconds: int | None
    model_used: bool
    model_action: str
    model_confidence: float
    model_weight: float
    signals: TimingSignals
    reason: str


def _clip01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _round_score(value: float) -> float:
    return round(_clip01(value), 6)


def _noisy_or(*values: float) -> float:
    product = 1.0
    for value in values:
        product *= 1.0 - _clip01(value)
    return _round_score(1.0 - product)


def _compact_for_ack(text: str) -> str:
    return re.sub(r"[\s,，。.!！?？~～…]+", "", str(text or "").strip().lower())


def _has_url(text: str) -> bool:
    return bool(_URL_RE.search(text or ""))


def _has_code_block(text: str) -> bool:
    stripped = str(text or "").strip()
    return "```" in stripped or stripped.startswith(("    ", "\t"))


def _looks_like_blob(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped or any(ch.isspace() for ch in stripped):
        return False
    if _has_url(stripped):
        return False
    return bool(_HEX_BLOB_RE.fullmatch(stripped) or _BASE64_BLOB_RE.fullmatch(stripped))


def _is_pure_ack(text: str, *, has_files: bool) -> bool:
    if has_files:
        return False
    raw = str(text or "").strip()
    if not raw:
        return False
    if _has_url(raw) or _has_code_block(raw):
        return False
    if any(marker in raw for marker in _REQUEST_MARKERS):
        return False
    if any(mark in raw for mark in ("?", "？")):
        return False
    compact = _compact_for_ack(raw)
    return compact in _ACK_WORDS


def _transport_signal(text: str, *, has_files: bool) -> tuple[float, str]:
    if has_files:
        return 0.0, ""
    raw = str(text or "").strip()
    if not raw:
        return 0.0, ""
    lowered = raw.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return 0.95, "secret"
    if _looks_like_blob(raw):
        return 0.95, "blob"
    if _has_url(raw):
        return 0.75, "url"
    if _has_code_block(raw):
        return 0.65, "codeblock"
    if len(raw) >= 800 and not any(mark in raw for mark in ("?", "？")):
        return 0.55, "long_dump"
    return 0.0, ""


def _wait_marker_signal(text: str) -> float:
    raw = str(text or "")
    return 1.0 if any(marker in raw for marker in _WAIT_MARKERS) else 0.0


def _incomplete_signal(text: str) -> float:
    stripped = str(text or "").rstrip()
    if not stripped:
        return 0.0
    return 0.35 if stripped.endswith((":", "：", ",", "，", "、")) else 0.0


def _explicit_direct_score(
    *,
    is_private: bool,
    is_at_bot: bool,
    is_reply_to_bot: bool,
    bot_name_mentioned: bool,
    direct_call: bool,
) -> float:
    scores = [0.75 if is_private else 0.0]
    if is_reply_to_bot:
        scores.append(1.0)
    if is_at_bot:
        scores.append(0.95)
    if bot_name_mentioned:
        scores.append(0.75)
    if direct_call:
        scores.append(0.65)
    return max(scores)


def extract_signals(
    text: str,
    *,
    is_group: bool = True,
    is_private: bool = False,
    is_at_bot: bool = False,
    is_reply_to_bot: bool = False,
    bot_name_mentioned: bool = False,
    direct_call: bool = False,
    is_directed_to_other: bool = False,
    has_other_recipient: bool = False,
    is_other_bot: bool = False,
    has_files: bool = False,
    linger_score: float = 0.0,
    force_direct_score: float = 0.0,
) -> TimingSignals:
    """从结构化上下文提取 TimingGate scoring 信号。"""
    private_context = bool(is_private or not is_group)
    d0 = _explicit_direct_score(
        is_private=private_context,
        is_at_bot=is_at_bot,
        is_reply_to_bot=is_reply_to_bot,
        bot_name_mentioned=bot_name_mentioned,
        direct_call=direct_call,
    )
    d0 = max(d0, _clip01(force_direct_score))
    linger = _clip01(linger_score)
    direct = max(d0, linger)

    s_ack = 0.85 if _is_pure_ack(text, has_files=has_files) else 0.0
    s_transport, transport_tier = _transport_signal(text, has_files=has_files)
    s_other = 0.75 if (is_directed_to_other or has_other_recipient) else 0.0
    s_bot = 0.70 if is_other_bot else 0.0
    suppress = _noisy_or(s_ack, s_transport, s_other, s_bot)

    w_marker = _wait_marker_signal(text)
    w_file = 0.45 if has_files else 0.0
    w_incomplete = _incomplete_signal(text)
    wait = _noisy_or(w_marker, w_file, w_incomplete)

    return TimingSignals(
        explicit_direct_score=_round_score(d0),
        linger_score=_round_score(linger),
        direct_score=_round_score(direct),
        wait_signal=wait,
        suppress_score=suppress,
        sub_signals={
            "s_ack": s_ack,
            "s_transport": s_transport,
            "s_transport_tier": transport_tier,
            "s_other": s_other,
            "s_bot": s_bot,
            "w_marker": w_marker,
            "w_file": w_file,
            "w_incomplete": w_incomplete,
            "is_private": private_context,
        },
    )


def compute_rule_score(signals: TimingSignals) -> float:
    return _round_score(
        BASE_SCORE
        + DIRECT_WEIGHT * signals.direct_score
        - SUPPRESS_WEIGHT * signals.suppress_score
    )


def select_theta(signals: TimingSignals, *, is_private: bool = False) -> float:
    if is_private or signals.sub_signals.get("is_private"):
        return 0.40
    d0 = signals.explicit_direct_score
    if d0 >= 0.90:
        return 0.30
    if d0 >= 0.65:
        return 0.42
    return 0.62


def compute_model_prior(action: str) -> float:
    normalized = str(action or "").strip().lower()
    if normalized == "no_reply":
        return 0.05
    if normalized == "wait":
        return 0.55
    if normalized in {"reply_now", "continue"}:
        return 0.95
    return 0.0


def compute_model_weight(confidence: float) -> float:
    return round(_clip01(confidence) * MODEL_WEIGHT_SCALE, 6)


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


def _clip_delay(value: int) -> int:
    return max(3, min(15, int(value)))


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


def _soft_reject_cap(score: float, signals: TimingSignals) -> float:
    return _round_score(min(score, _soft_reject_cap_limit(signals)))


def _soft_reject_cap_limit(signals: TimingSignals) -> float:
    s_bot = _clip01(float(signals.sub_signals.get("s_bot", 0.0) or 0.0))
    if s_bot <= 0:
        return 1.0
    return _round_score(1.0 - BOT_SOFT_REJECT_GAMMA * s_bot)


def decide_timing(
    text: str,
    *,
    is_group: bool = True,
    is_private: bool = False,
    is_at_bot: bool = False,
    is_reply_to_bot: bool = False,
    bot_name_mentioned: bool = False,
    direct_call: bool = False,
    is_directed_to_other: bool = False,
    has_other_recipient: bool = False,
    is_other_bot: bool = False,
    has_files: bool = False,
    linger_score: float = 0.0,
    force_direct_score: float = 0.0,
    min_interval_active: bool = False,
    min_interval_remaining: float = 0.0,
    model_hint: TimingModelHint | None = None,
    margin: float = DECISION_MARGIN,
) -> TimingDecision:
    signals = extract_signals(
        text,
        is_group=is_group,
        is_private=is_private,
        is_at_bot=is_at_bot,
        is_reply_to_bot=is_reply_to_bot,
        bot_name_mentioned=bot_name_mentioned,
        direct_call=direct_call,
        is_directed_to_other=is_directed_to_other,
        has_other_recipient=has_other_recipient,
        is_other_bot=is_other_bot,
        has_files=has_files,
        linger_score=linger_score,
        force_direct_score=force_direct_score,
    )
    rule_score = compute_rule_score(signals)
    theta = select_theta(signals, is_private=is_private)
    low = round(max(0.0, theta - margin), 6)
    high = round(min(1.0, theta + margin), 6)
    kappa = min(signals.direct_score, signals.suppress_score)
    conflict_score = _round_score(kappa)
    soft_reject_cap = _soft_reject_cap_limit(signals)

    model_action = str(model_hint.action if model_hint else "" or "").strip().lower()
    model_confidence = _clip01(float(model_hint.confidence if model_hint else 0.0))
    model_weight = compute_model_weight(model_confidence)

    if kappa >= CONFLICT_THRESHOLD:
        if _valid_model_hint(model_hint):
            final_score = _soft_reject_cap(
                (1.0 - model_weight) * rule_score
                + model_weight * compute_model_prior(model_action),
                signals,
            )
            participate = final_score >= theta
            action, delay, readiness_reason = _action_after_readiness(
                participate=participate,
                wait_signal=signals.wait_signal,
                min_interval_active=min_interval_active,
                min_interval_remaining=min_interval_remaining,
                model_action=model_action,
            )
            return TimingDecision(
                action=action,
                stage="model_assisted_conflict",
                participation_score=rule_score,
                final_score=_round_score(final_score),
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
        return TimingDecision(
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
        return TimingDecision(
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
        return TimingDecision(
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
            + model_weight * compute_model_prior(model_action),
            signals,
        )
        participate = final_score >= theta
        action, delay, readiness_reason = _action_after_readiness(
            participate=participate,
            wait_signal=signals.wait_signal,
            min_interval_active=min_interval_active,
            min_interval_remaining=min_interval_remaining,
            model_action=model_action,
        )
        return TimingDecision(
            action=action,
            stage="model_assisted",
            participation_score=rule_score,
            final_score=_round_score(final_score),
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
    return TimingDecision(
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
