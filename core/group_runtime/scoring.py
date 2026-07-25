from __future__ import annotations

import logging
import time as _time
from dataclasses import asdict

from core.group_runtime.constants import (
    BOT_FOLLOWUP_WINDOW_SEC,
    BOT_REPLY_COOLDOWN_SEC,
    LINGER_BASE_WEIGHT,
    LINGER_MAX_MESSAGES,
    LINGER_MIN_INTERVAL_SECS,
    _COOLDOWN_BYPASS_TRIGGERS,
    _DIRECT_TRIGGERS,
)
from core.group_runtime.state import (
    GroupChatState,
    GroupPendingMessage,
    _has_directed_to_other_signal,
    _has_file_segments,
    _has_other_recipient_signal,
    _model_confidence_from_gate_result,
    _pending_payload,
    _pending_text_for_scoring,
)
from core.timing_model_policy import TimingModelPolicy

logger = logging.getLogger("nanobot.group_runtime")


class GroupRuntimeScoringMixin:
    @staticmethod
    def _active_linger_score(
        state: GroupChatState,
        pending: list[GroupPendingMessage] | None = None,
    ) -> float:
        msgs = pending if pending is not None else state.pending
        latest_sender_id = msgs[-1].sender_id if msgs else ""
        return state.linger_score_for(latest_sender_id)

    @staticmethod
    def _should_cooldown(
        state: GroupChatState,
        trigger_reason: str,
        *,
        has_active_linger: bool = False,
    ) -> bool:
        ago = state.bot_reply_ago()
        if not (0 < ago < BOT_REPLY_COOLDOWN_SEC):
            return False
        if state.force_next_continue:
            return False
        if has_active_linger:
            return False
        is_direct = str(trigger_reason or "").strip() in _COOLDOWN_BYPASS_TRIGGERS
        return not is_direct

    @staticmethod
    def _activity_factor(state: GroupChatState) -> float:
        """根据最近群消息密度动态调整插话门槛——群越活跃，bot 越克制。"""
        msg_1m = state.recent_message_count(60)
        msg_5m = state.recent_message_count(300)
        sender_1m = state.recent_sender_count(60)

        per_min = max(msg_1m, msg_5m / 5.0)

        if msg_1m > 40 or msg_5m > 150:
            return 4.0
        if msg_1m > 20 or msg_5m > 80:
            return 2.5
        if msg_1m > 10 or msg_5m > 40:
            return 1.5
        if msg_1m <= 2 and msg_5m <= 6:
            return 0.8
        if sender_1m >= 4 and per_min >= 8:
            return 1.3
        return 1.0

    @classmethod
    def _talk_threshold(cls, state: GroupChatState, talk_value: float = 0.5) -> int:
        base = max(1, round(1.0 / max(talk_value, 0.05)))
        factor = cls._activity_factor(state)
        return max(1, round(base * factor))

    @classmethod
    def _should_gate_by_frequency(cls, state: GroupChatState, talk_value: float = 0.5) -> bool:
        threshold = cls._talk_threshold(state, talk_value)
        return len(state.pending) >= threshold

    def _shadow_scoring(
        self,
        state: GroupChatState,
        trigger_reason: str,
        *,
        pending: list[GroupPendingMessage] | None = None,
        model_result: dict | None = None,
        force_direct_score: float = 0.0,
    ) -> dict:
        """生成 TimingGate scoring shadow 字段，不参与主决策。"""
        try:
            decision = self._score_timing(
                state,
                trigger_reason,
                pending=pending,
                model_result=model_result,
                force_direct_score=force_direct_score,
            )
            return self._timing_scoring_payload(state, decision)
        except Exception as exc:
            logger.debug("[GroupRuntime] shadow scoring failed: %s", exc, exc_info=True)
            return {}

    @staticmethod
    def _timing_scoring_payload(state: GroupChatState, decision) -> dict:
        payload = asdict(decision)
        signals = payload.setdefault("signals", {})
        linger_time_remaining = max(0.0, state.linger_active_until - _time.time())
        signals["linger_active"] = bool(
            linger_time_remaining > 0 and state.linger_reply_count < LINGER_MAX_MESSAGES
        )
        signals["linger_reply_count"] = state.linger_reply_count
        signals["linger_time_remaining"] = round(linger_time_remaining, 1)
        return payload

    def _score_timing(
        self,
        state: GroupChatState,
        trigger_reason: str,
        *,
        pending: list[GroupPendingMessage] | None = None,
        model_result: dict | None = None,
        force_direct_score: float = 0.0,
    ):
        """计算 TimingGate scoring 决策，供 shadow 字段和规则短路复用。"""
        from core.timing_score import TimingModelHint, decide_timing

        msgs = list(pending if pending is not None else state.pending)
        tr = str(trigger_reason or state.last_trigger_reason or "").strip()
        is_direct = tr in _DIRECT_TRIGGERS
        model_hint = None
        if model_result:
            error_type = str(model_result.get("error_type") or "")
            action = str(model_result.get("action") or "").strip()
            confidence = _model_confidence_from_gate_result(model_result)
            if error_type:
                confidence = 0.0
            if action:
                model_hint = TimingModelHint(
                    action=action,
                    confidence=confidence,
                    raw=str(model_result.get("raw") or ""),
                    reason=str(model_result.get("reason") or ""),
                )
        linger_score = self._active_linger_score(state, msgs)
        if tr == "recent_bot_followup" and linger_score <= 0 and state.linger_active_until <= 0:
            linger_score = LINGER_BASE_WEIGHT
        ago = state.bot_reply_ago()
        is_linger_followup = tr not in _DIRECT_TRIGGERS
        linger_interval_active = (
            is_linger_followup
            and force_direct_score <= 0
            and linger_score > 0
            and 0 < ago < LINGER_MIN_INTERVAL_SECS
        )
        min_interval_active = linger_interval_active or self._should_cooldown(
            state,
            tr,
            has_active_linger=linger_score > 0,
        )
        min_interval_remaining = (
            LINGER_MIN_INTERVAL_SECS - ago
            if linger_interval_active
            else BOT_REPLY_COOLDOWN_SEC - ago
        )
        return decide_timing(
            text=_pending_text_for_scoring(msgs),
            is_group=True,
            is_private=False,
            is_at_bot=any(m.is_at_bot for m in msgs) or tr == "at_bot",
            is_reply_to_bot=any(m.is_reply_to_bot for m in msgs) or tr == "reply_to_bot",
            bot_name_mentioned=tr in {"bot_name_mentioned", "mentioned"},
            direct_call=tr == "direct_call" or is_direct,
            is_directed_to_other=_has_directed_to_other_signal(msgs),
            has_other_recipient=_has_other_recipient_signal(msgs),
            is_other_bot=any(m.is_other_bot for m in msgs),
            has_files=_has_file_segments(msgs),
            linger_score=linger_score,
            force_direct_score=force_direct_score,
            min_interval_active=min_interval_active,
            min_interval_remaining=max(0.0, min_interval_remaining),
            model_hint=model_hint,
        )

    def _apply_scoring_shortcut(
        self,
        state: GroupChatState,
        decision,
        *,
        pending: list[GroupPendingMessage],
        reason_prefix: str,
    ) -> dict:
        """应用规则评分短路结果；调用方必须持有 active transaction。"""
        action = decision.action
        delay = decision.delay_seconds
        payload = _pending_payload(pending)
        if action == "continue":
            state.handle_continue()
        elif action == "wait":
            delay = int(delay or 5)
            state.start_wait(delay, decision.reason)
        else:
            action = "no_reply"
            delay = None
            state.handle_no_reply()
        cooldown = round(state.bot_reply_ago(), 1)
        response = {
            "action": action,
            "delay_seconds": delay,
            "generation": state.generation,
            "cooldown_ago": cooldown,
            "reason": f"{reason_prefix}: {decision.reason}",
            "timing_scoring": self._timing_scoring_payload(state, decision),
        }
        if action == "continue":
            response.update(payload)
        return response

    @staticmethod
    def _policy_payload(policy: TimingModelPolicy) -> dict:
        if not isinstance(policy, TimingModelPolicy):
            raise TypeError("timing model policy 不满足类型化合同")
        return {
            "mode": policy.mode.value,
            "source": policy.source,
        }

    def _resolve_timing_model_policy(self, state: GroupChatState, group_id: str):
        session_id = state.group_id or group_id
        platform = state.platform or "qq"
        return self.timing_model_policy_resolver(session_id, platform)

    def _apply_policy_scoring_decision(
        self,
        state: GroupChatState,
        policy,
        trigger_reason: str,
        *,
        pending: list[GroupPendingMessage],
        reason_prefix: str,
        force_direct_score: float = 0.0,
        shadow_scoring: dict | None = None,
    ) -> dict:
        """应用模型策略评分；调用方必须持有 active transaction。"""
        decision = self._score_timing(
            state,
            trigger_reason,
            pending=pending,
            force_direct_score=force_direct_score,
        )
        response = self._apply_scoring_shortcut(
            state,
            decision,
            pending=pending,
            reason_prefix=reason_prefix,
        )
        response["timing_model_policy"] = self._policy_payload(policy)
        if shadow_scoring is not None:
            response["timing_model_shadow_scoring"] = shadow_scoring
        return response

    def _cooldown_scoring_shortcut(
        self,
        state: GroupChatState,
        trigger_reason: str,
        *,
        pending: list[GroupPendingMessage],
        reason_prefix: str,
    ) -> dict | None:
        """在 active transaction 中尝试用 shared scoring 接管 cooldown。"""
        try:
            scoring_decision = self._score_timing(
                state,
                trigger_reason,
                pending=pending,
            )
        except Exception as exc:
            logger.debug("[GroupRuntime] cooldown scoring failed: %s", exc, exc_info=True)
            return None
        if scoring_decision.stage != "rule_shortcut":
            return None
        logger.info("[GroupRuntime] cooldown_scoring_shortcut action=%s pending=%d",
                    scoring_decision.action, len(pending))
        return self._apply_scoring_shortcut(
            state,
            scoring_decision,
            pending=pending,
            reason_prefix=reason_prefix,
        )

    def _attach_shadow_scoring(
        self,
        response: dict,
        state: GroupChatState,
        trigger_reason: str,
        *,
        pending: list[GroupPendingMessage] | None = None,
        model_result: dict | None = None,
    ) -> dict:
        scoring = self._shadow_scoring(
            state,
            trigger_reason,
            pending=pending,
            model_result=model_result,
        )
        if scoring:
            response["timing_scoring"] = scoring
        return response

    @staticmethod
    def _looks_like_recent_bot_followup(state: GroupChatState, msg: GroupPendingMessage) -> bool:
        ago = state.bot_reply_ago()
        if not (0 < ago <= BOT_FOLLOWUP_WINDOW_SEC):
            return False
        text = (msg.message or "").strip()
        if not text or len(text) > 60 or text.startswith(("/", "!", "！")):
            return False
        followup_terms = (
            "再", "继续", "换", "还有", "看看", "来个", "发个", "发张", "发一下",
            "整", "上一个", "刚才", "刚刚", "这个", "那个", "呢", "吗", "怎么",
            "为什么", "啥", "如何", "行不行", "可以吗", "有没有", "表情", "图",
        )
        return text.endswith(("?", "？")) or any(term in text for term in followup_terms)
