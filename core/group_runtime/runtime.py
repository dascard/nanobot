"""GroupRuntime — per-group stateful runtime。

保留 GroupRuntime.process_message() / handle_timer_fired() API，
内部升级为 GroupChatState：force_next_continue + talk_value + message_cache。
"""

from __future__ import annotations

import asyncio
import logging
import math
import time as _time

from core.group_runtime.constants import (
    BOT_FOLLOWUP_WINDOW_SEC as BOT_FOLLOWUP_WINDOW_SEC,
    BOT_REPLY_COOLDOWN_SEC,
    IDLE_CLEANUP_SEC as IDLE_CLEANUP_SEC,
    LINGER_BASE_WEIGHT as LINGER_BASE_WEIGHT,
    MAX_AGE_SEC as MAX_AGE_SEC,
    LINGER_MIN_INTERVAL_SECS as LINGER_MIN_INTERVAL_SECS,
    MAX_PENDING,
    MAX_RETRIES as MAX_RETRIES,
    MAX_WAIT_SEC as MAX_WAIT_SEC,
    MIN_INTERVAL as MIN_INTERVAL,
    LINGER_TIMEOUT_SECONDS as LINGER_TIMEOUT_SECONDS,
    LINGER_MAX_MESSAGES,
    TIMING_WAIT_DELAY_MAX as TIMING_WAIT_DELAY_MAX,
    TIMING_WAIT_DELAY_MIN as TIMING_WAIT_DELAY_MIN,
    _COOLDOWN_BYPASS_TRIGGERS as _COOLDOWN_BYPASS_TRIGGERS,
    _DIRECTED_SUPPRESS_BYPASS_TRIGGERS as _DIRECTED_SUPPRESS_BYPASS_TRIGGERS,
    _DIRECT_TRIGGERS,
)
from core.group_runtime.scoring import GroupRuntimeScoringMixin
from core.group_runtime.state import (
    GroupChatState,
    GroupPendingMessage,
    _clip_timing_wait_delay,
    _format_direction_for_pending as _format_direction_for_pending,
    _has_directed_to_other_signal as _has_directed_to_other_signal,
    _has_file_segments as _has_file_segments,
    _has_other_recipient_signal as _has_other_recipient_signal,
    _model_confidence_from_gate_result as _model_confidence_from_gate_result,
    _pending_payload,
    _pending_text_for_scoring as _pending_text_for_scoring,
    should_suppress_directed_to_other,
)

logger = logging.getLogger("nanobot.group_runtime")


class GroupRuntime(GroupRuntimeScoringMixin):
    """管理所有群的运行时状态——兼容旧 timing_runtime API。"""

    def __init__(self):
        from core.timing_model_policy import resolve_timing_model_policy

        self._states: dict[str, GroupChatState] = {}
        self._lock = asyncio.Lock()
        self.timing_model_policy_resolver = resolve_timing_model_policy

    @staticmethod
    def _norm_group_id(group_id: str) -> str:
        """所有入口强制 normalize——防止不同格式的 group_id 创建多个 state。"""
        from core.group_runtime.ids import normalize_group_session_id
        return normalize_group_session_id(group_id)

    async def process_message(
        self, group_id: str, msg: dict, *,
        trigger_reason: str = "", session_name: str = "",
        bot_aliases: list[str] | None = None,
        recent_context: str = "",
        talk_value: float = 0.5,
        platform: str = "qq",
    ) -> dict:
        """处理新消息——添加、判断是否触发 gate、返回结果。"""
        from core.group_runtime.ids import normalize_group_stream_id

        group_id = self._norm_group_id(group_id)
        stream_id = normalize_group_stream_id(group_id)

        pm = GroupPendingMessage(
            sender_id=str(msg.get("sender_id", "")),
            sender_name=str(msg.get("sender_name", "")),
            message=str(msg.get("message", "")),
            message_id=str(msg.get("message_id", "")),
            ts=_time.time(),
            is_at_bot=bool(msg.get("is_at_bot", False)),
            is_reply_to_bot=bool(msg.get("is_reply_to_bot", False)),
            trigger_reason=str(trigger_reason or ""),
            segments=msg.get("segments") if isinstance(msg.get("segments"), list) else [],
            mentions=msg.get("mentions") if isinstance(msg.get("mentions"), list) else [],
            reply_to=msg.get("reply_to") if isinstance(msg.get("reply_to"), dict) else None,
            directed=msg.get("directed") if isinstance(msg.get("directed"), dict) else None,
            is_directed_to_other=bool(msg.get("is_directed_to_other", False)),
            is_other_bot=bool(msg.get("is_other_bot", False)),
            self_id=str(msg.get("self_id", "")),
            bot_id=str(msg.get("bot_id", "")),
            bot_name=str(msg.get("bot_name", "")),
        )

        async with self._lock:
            state = self._states.setdefault(group_id, GroupChatState(
                group_id=group_id,
                stream_id=stream_id,
            ))
            state.talk_value = talk_value
            state.platform = str(platform or "qq").strip() or "qq"
            tr = str(trigger_reason or "").strip()
            if tr == "ambient" and self._looks_like_recent_bot_followup(state, pm):
                tr = "recent_bot_followup"
                pm.trigger_reason = tr
            state.last_trigger_reason = tr
            state.session_name = session_name
            state.bot_aliases = list(bot_aliases or [])
            state.bot_id = pm.bot_id or state.bot_id
            state.bot_name = pm.bot_name or state.bot_name
            state.add_message(pm)

            # wait 期间收到新消息：累积 + 刷新计时器，不调用 TimingGate
            if state.waiting_for_more:
                wait_msg = {
                    "sender_name": pm.sender_name,
                    "message": pm.message[:200],
                    "trigger_reason": pm.trigger_reason,
                }
                state.receive_during_wait(wait_msg)
                refreshed = state.refresh_wait()
                if refreshed:
                    delay = max(1, int(state.wait_until - _time.time()))
                    logger.info("[GroupRuntime] wait refresh group=%s delay=%d new_msgs=%d",
                                group_id, delay, len(state.new_messages_during_wait))
                    return self._attach_shadow_scoring({
                        "action": "wait",
                        "delay_seconds": delay,
                        "generation": state.generation,
                        "reason": f"waiting_for_more: {len(state.new_messages_during_wait)} new msgs",
                        "waiting_for_more": True,
                        "new_messages_during_wait": len(state.new_messages_during_wait),
                    }, state, tr)
                else:
                    # max_wait_until 到期 → 结束 wait 继续正常 gate
                    state.end_wait()
                    logger.info("[GroupRuntime] wait max_exceeded group=%s proceeding to gate", group_id)

            ctx = {
                "session_name": session_name,
                "bot_aliases": state.bot_aliases,
                "recent_context": recent_context,
            }
            if state.last_bot_reply_ts > 0:
                ctx["last_bot_reply_ago"] = _time.time() - state.last_bot_reply_ts

            # 直接触发 → arm force_next_continue
            if tr in _DIRECT_TRIGGERS or pm.is_at_bot or pm.is_reply_to_bot:
                direct_reason = tr or ("reply_to_bot" if pm.is_reply_to_bot else "at_bot")
                state.activate_linger(pm.sender_id, direct_reason)
                state.arm_force_continue(direct_reason)

            # directed_to_other：降级为 scoring 抑制信号，独自成立时仍会规则短路 no_reply
            if should_suppress_directed_to_other(state.pending):
                snapshot = state.take_snapshot()
                payload = _pending_payload(snapshot)
                try:
                    scoring_decision = self._score_timing(
                        state,
                        tr,
                        pending=snapshot,
                    )
                except Exception as exc:
                    logger.debug("[GroupRuntime] directed_to_other scoring failed: %s", exc, exc_info=True)
                    return self._attach_shadow_scoring({
                        "action": "no_reply",
                        "generation": state.generation,
                        "reason": "directed_to_other scoring unavailable",
                        "pending_count": len(snapshot),
                        "trigger_reason": tr,
                        "directed_to_other": True,
                        **payload,
                    }, state, tr, pending=snapshot)
                if scoring_decision.stage == "rule_shortcut":
                    response = self._apply_scoring_shortcut(
                        state,
                        scoring_decision,
                        pending=snapshot,
                        reason_prefix="directed_to_other scoring shortcut",
                    )
                    response.update({
                        "pending_count": len(snapshot),
                        "trigger_reason": tr,
                        "directed_to_other": True,
                        **payload,
                    })
                    return response

            # 硬 cooldown：bot 刚回复过且非直接互动 → wait
            has_active_linger = self._active_linger_score(state) > 0
            if self._should_cooldown(state, tr, has_active_linger=has_active_linger):
                ago = state.bot_reply_ago()
                if tr in {"", "ambient"}:
                    snapshot = state.take_snapshot()
                    scoring_response = self._cooldown_scoring_shortcut(
                        state,
                        tr,
                        pending=snapshot,
                        reason_prefix="cooldown scoring shortcut",
                    )
                    if scoring_response is not None:
                        return scoring_response
                return self._attach_shadow_scoring({
                    "action": "wait",
                    "delay_seconds": _clip_timing_wait_delay(math.ceil(BOT_REPLY_COOLDOWN_SEC - ago)),
                    "generation": state.generation,
                    "cooldown_ago": round(ago, 1),
                    "reason": "bot 刚回复过，冷却中",
                }, state, tr)

            gate_prepared = False
            if state.force_next_continue:
                force_reason = state.force_reason or tr
                snapshot = state.take_snapshot()
                try:
                    scoring_decision = self._score_timing(
                        state,
                        force_reason,
                        pending=snapshot,
                        force_direct_score=1.0,
                    )
                except Exception as exc:
                    scoring_decision = None
                    logger.debug("[GroupRuntime] force scoring failed: %s", exc, exc_info=True)
                state.force_next_continue = False
                if scoring_decision is not None and scoring_decision.stage == "rule_shortcut":
                    logger.info("[GroupRuntime] force_scoring_shortcut group=%s reason=%s action=%s pending=%d",
                                group_id, force_reason, scoring_decision.action, len(snapshot))
                    return self._apply_scoring_shortcut(
                        state,
                        scoring_decision,
                        pending=snapshot,
                        reason_prefix=f"direct trigger: {force_reason}",
                    )
                policy = self._resolve_timing_model_policy(state, group_id)
                if policy.mode == "rules_only":
                    return self._apply_policy_scoring_decision(
                        state,
                        policy,
                        force_reason,
                        pending=snapshot,
                        reason_prefix="timing model rules_only",
                        force_direct_score=1.0,
                    )
                state.mark_gate_start()
                gen = state.generation
                ctx_snapshot = dict(ctx)
                tr = force_reason
                gate_prepared = True
                gate_policy = policy
                gate_force_direct_score = 1.0
                logger.info("[GroupRuntime] force_gate group=%s reason=%s pending=%d",
                            group_id, force_reason, len(snapshot))

            if not gate_prepared:
                # talk_value gate：普通 ambient 消息按频率控制
                if (
                    tr == "ambient"
                    and not has_active_linger
                    and not self._should_gate_by_frequency(state, talk_value)
                ):
                    threshold = max(1, round(1.0 / max(talk_value, 0.05)))
                    return self._attach_shadow_scoring({
                        "action": "wait",
                        "delay_seconds": state.next_gate_delay(),
                        "generation": state.generation,
                        "cooldown_ago": state.bot_reply_ago(),
                        "reason": (
                            f"talk_value gate: {len(state.pending)}/{threshold} pending "
                            f"(talk={talk_value:.2f}, msg_1m={state.recent_message_count(60)}, "
                            f"msg_5m={state.recent_message_count(300)})"
                        ),
                    }, state, tr)

                if not state.can_trigger_gate():
                    return self._attach_shadow_scoring({
                        "action": "wait",
                        "delay_seconds": state.next_gate_delay(),
                        "generation": state.generation,
                        "cooldown_ago": state.bot_reply_ago(),
                        "reason": "rate limited / gate in progress",
                    }, state, tr)

                snapshot = state.take_snapshot()
                if tr == "ambient":
                    try:
                        scoring_decision = self._score_timing(
                            state,
                            tr,
                            pending=snapshot,
                        )
                    except Exception as exc:
                        scoring_decision = None
                        logger.debug("[GroupRuntime] ambient scoring failed: %s", exc, exc_info=True)
                    if scoring_decision is not None and scoring_decision.stage == "rule_shortcut":
                        logger.info("[GroupRuntime] ambient_scoring_shortcut group=%s action=%s pending=%d",
                                    group_id, scoring_decision.action, len(snapshot))
                        return self._apply_scoring_shortcut(
                            state,
                            scoring_decision,
                            pending=snapshot,
                            reason_prefix="ambient scoring shortcut",
                        )

                policy = self._resolve_timing_model_policy(state, group_id)
                if policy.mode == "rules_only":
                    return self._apply_policy_scoring_decision(
                        state,
                        policy,
                        tr,
                        pending=snapshot,
                        reason_prefix="timing model rules_only",
                    )
                state.mark_gate_start()
                gen = state.generation
                ctx_snapshot = dict(ctx)
                gate_policy = policy
                gate_force_direct_score = 0.0

        result = await self._call_gate(group_id, snapshot, ctx_snapshot, tr)

        async with self._lock:
            state = self._states.get(group_id)
            if not state:
                return {"action": "no_reply", "delay_seconds": None, "reason": "state cleaned up"}

            if gen != state.generation:
                state.mark_gate_done()
                logger.info("[GroupRuntime] gen mismatch %d!=%d for %s",
                            gen, state.generation, group_id)
                return self._attach_shadow_scoring({
                    "action": "no_reply", "delay_seconds": None,
                    "generation": state.generation,
                    "cooldown_ago": state.bot_reply_ago(),
                    "reason": "generation mismatch, new messages arrived during gate",
                }, state, tr, pending=snapshot, model_result=result)

            if getattr(gate_policy, "mode", "enabled") == "shadow":
                shadow_scoring = self._shadow_scoring(
                    state,
                    tr,
                    pending=snapshot,
                    model_result=result,
                    force_direct_score=gate_force_direct_score,
                )
                return self._apply_policy_scoring_decision(
                    state,
                    gate_policy,
                    tr,
                    pending=snapshot,
                    reason_prefix="timing model shadow",
                    force_direct_score=gate_force_direct_score,
                    shadow_scoring=shadow_scoring,
                )

            return self._apply_gate_result(
                state,
                result,
                trigger_reason=tr,
                pending=snapshot,
                force_direct_score=gate_force_direct_score,
            )

    async def handle_timer_fired(self, group_id: str, generation: int,
                                 trigger_reason: str = "",
                                 recent_context: str = "") -> dict:
        """wait timer 到期——校验 generation 后重新 gate 判断。"""
        group_id = self._norm_group_id(group_id)
        async with self._lock:
            state = self._states.get(group_id)
            if not state:
                return {"action": "no_reply", "delay_seconds": None, "reason": "state cleaned up"}

            # 检查 talk_value gate——timer 也不能绕过
            if (state.last_trigger_reason == "ambient"
                    and not self._should_gate_by_frequency(state, state.talk_value)):
                threshold = max(1, round(1.0 / max(state.talk_value, 0.05)))
                return self._attach_shadow_scoring({
                    "action": "wait",
                    "delay_seconds": state.next_gate_delay(),
                    "generation": state.generation,
                    "cooldown_ago": state.bot_reply_ago(),
                    "reason": (
                        f"talk_value gate: {len(state.pending)}/{threshold} pending "
                        f"(talk={state.talk_value:.2f}, msg_1m={state.recent_message_count(60)}, "
                        f"msg_5m={state.recent_message_count(300)})"
                    ),
                }, state, trigger_reason or state.last_trigger_reason or "timer")

            if generation != state.generation:
                logger.info("[GroupRuntime] timer gen mismatch %d!=%d for %s",
                            generation, state.generation, group_id)
                return self._attach_shadow_scoring({
                    "action": "no_reply", "delay_seconds": None,
                    "generation": state.generation,
                    "reason": "generation mismatch, timer expired",
                }, state, trigger_reason or state.last_trigger_reason or "timer")

            if self._should_cooldown(state, trigger_reason or "timer"):
                ago = state.bot_reply_ago()
                snapshot = state.take_snapshot()
                scoring_response = self._cooldown_scoring_shortcut(
                    state,
                    trigger_reason or state.last_trigger_reason or "timer",
                    pending=snapshot,
                    reason_prefix="timer cooldown scoring shortcut",
                )
                if scoring_response is not None:
                    return scoring_response
                return self._attach_shadow_scoring({
                    "action": "wait",
                    "delay_seconds": _clip_timing_wait_delay(math.ceil(BOT_REPLY_COOLDOWN_SEC - ago)),
                    "generation": state.generation,
                    "cooldown_ago": round(ago, 1),
                    "reason": "bot 刚回复过，冷却中",
                }, state, trigger_reason or "timer")

            if not state.can_trigger_gate():
                return self._attach_shadow_scoring({
                    "action": "wait",
                    "delay_seconds": state.next_gate_delay(),
                    "generation": state.generation,
                    "cooldown_ago": round(state.bot_reply_ago(), 1),
                    "reason": "rate limited",
                }, state, trigger_reason or state.last_trigger_reason or "timer")

            # timer 触发时退出 wait 状态
            was_waiting = state.waiting_for_more
            if was_waiting:
                state.end_wait()

            snapshot = state.take_snapshot()
            policy = self._resolve_timing_model_policy(state, group_id)
            timer_trigger_reason = trigger_reason or state.last_trigger_reason or "timer"
            if policy.mode == "rules_only":
                return self._apply_policy_scoring_decision(
                    state,
                    policy,
                    timer_trigger_reason,
                    pending=snapshot,
                    reason_prefix="timing model rules_only",
                )

            state.mark_gate_start()
            gen = state.generation
            ctx_saved = {
                "session_name": state.session_name,
                "bot_aliases": list(state.bot_aliases),
                "recent_context": recent_context,
            }
            if state.last_bot_reply_ts > 0:
                ctx_saved["last_bot_reply_ago"] = _time.time() - state.last_bot_reply_ts
            # 注入 wait 元信息
            if was_waiting or state.previous_gate_action == "wait":
                ctx_saved["previous_gate_action"] = "wait"
                ctx_saved["wait_count"] = state.wait_count
                ctx_saved["new_messages_during_wait"] = len(state.new_messages_during_wait)
                ctx_saved["wait_reason"] = state.wait_reason
                ctx_saved["wait_timeout"] = not state.new_messages_during_wait

        result = await self._call_gate(group_id, snapshot, ctx_saved, timer_trigger_reason)

        async with self._lock:
            state = self._states.get(group_id)
            if not state:
                return {"action": "no_reply", "delay_seconds": None, "reason": "state cleaned up"}
            if gen != state.generation:
                state.mark_gate_done()
                return self._attach_shadow_scoring({
                    "action": "no_reply", "delay_seconds": None,
                    "generation": state.generation,
                    "reason": "state changed during timer gate",
                }, state, timer_trigger_reason, pending=snapshot, model_result=result)

            if getattr(policy, "mode", "enabled") == "shadow":
                shadow_scoring = self._shadow_scoring(
                    state,
                    timer_trigger_reason,
                    pending=snapshot,
                    model_result=result,
                )
                return self._apply_policy_scoring_decision(
                    state,
                    policy,
                    timer_trigger_reason,
                    pending=snapshot,
                    reason_prefix="timing model shadow",
                    shadow_scoring=shadow_scoring,
                )

            return self._apply_gate_result(
                state,
                result,
                trigger_reason=timer_trigger_reason,
                pending=snapshot,
            )

    def _apply_gate_result(
        self,
        state: GroupChatState,
        result: dict,
        *,
        trigger_reason: str = "",
        pending: list[GroupPendingMessage] | None = None,
        force_direct_score: float = 0.0,
    ) -> dict:
        action = result.get("action", "no_reply")
        delay = None
        pending_msgs = list(pending if pending is not None else state.pending)
        payload = _pending_payload(pending_msgs)
        scoring = self._shadow_scoring(
            state,
            str(result.get("trigger_reason") or trigger_reason or state.last_trigger_reason or ""),
            pending=pending_msgs,
            model_result=result,
            force_direct_score=force_direct_score,
        )
        scoring_reason = ""
        scoring_delay = None
        if scoring:
            scoring_action = str(scoring.get("action") or "").strip()
            if scoring_action in {"continue", "wait", "no_reply"}:
                action = scoring_action
                scoring_delay = scoring.get("delay_seconds")
                if result.get("error_type") and scoring.get("stage") == "rule_fallback":
                    scoring_reason = (
                        f"rule_fallback after {result.get('error_type')}: "
                        f"{scoring.get('reason', '')}"
                    )
                else:
                    scoring_reason = f"scoring blend: {scoring.get('reason', '')}"

        if action == "continue":
            state.handle_continue()
        elif action == "no_reply":
            state.handle_no_reply()
        elif action == "wait":
            delay_source = scoring_delay if scoring_delay is not None else result.get("delay_seconds", 5)
            delay = _clip_timing_wait_delay(delay_source or 5)
            # 防死循环：wait_count>=1 且无新消息 → force no_reply
            if state.wait_count >= 1 and not state.new_messages_during_wait:
                logger.info("[GroupRuntime] anti-loop: wait_count=%d no_new_msgs → force no_reply",
                            state.wait_count)
                state.handle_no_reply()
                action = "no_reply"
                delay = None
            elif state.wait_count >= 2:
                logger.info("[GroupRuntime] anti-loop: wait_count>=2 → force no_reply")
                state.handle_no_reply()
                action = "no_reply"
                delay = None
            elif state.is_wait_exhausted():
                state.handle_no_reply()
                action = "no_reply"
                delay = None
            else:
                state.start_wait(delay, scoring_reason or result.get("reason", ""))
        else:
            state.handle_no_reply()
            action = "no_reply"

        cooldown = round(state.bot_reply_ago(), 1)
        state.mark_gate_done()
        response = {
            "action": action, "delay_seconds": delay,
            "generation": state.generation,
            "cooldown_ago": cooldown,
            "reason": scoring_reason or result.get("reason", ""),
        }
        if scoring:
            response["timing_scoring"] = scoring
        for key in (
            "raw", "error_type", "latency_ms", "context", "context_chars",
            "pending_count", "trigger_reason", "parse_quality", "model_confidence",
        ):
            if key in result:
                response[key] = result.get(key)
        if response.get("error_type"):
            response["fallback_action"] = action
        if action == "continue":
            response.update(payload)
        return response

    def note_bot_replied(self, group_id: str):
        state = self._states.get(self._norm_group_id(group_id))
        if state:
            state.note_bot_replied()

    def cleanup_idle(self):
        stale = [gid for gid, s in self._states.items() if s.is_idle()]
        for gid in stale:
            del self._states[gid]
        if stale:
            logger.info("[GroupRuntime] cleaned %d idle states", len(stale))

    def snapshot_states(self) -> dict[str, dict]:
        """返回 Admin WebUI 可读的轻量运行时快照。"""
        now = _time.time()
        out: dict[str, dict] = {}
        for gid, state in self._states.items():
            pending = state.take_snapshot()
            linger_time_remaining = max(0.0, state.linger_active_until - now)
            out[gid] = {
                "group_id": state.group_id,
                "stream_id": state.stream_id,
                "session_name": state.session_name,
                "generation": state.generation,
                "running": state.running,
                "pending_count": len(pending),
                "pending_messages": [m.to_dict() for m in pending[-10:]],
                "has_pending_timer": bool(pending and state.wait_count > 0),
                "wait_count": state.wait_count,
                "total_wait_s": round(state.total_wait_s, 1),
                "waiting_for_more": state.waiting_for_more,
                "new_messages_during_wait": len(state.new_messages_during_wait),
                "wait_reason": state.wait_reason[:120],
                "last_trigger_reason": state.last_trigger_reason,
                "platform": state.platform,
                "last_bot_reply_ago": round(state.bot_reply_ago(), 1),
                "linger_active": bool(
                    linger_time_remaining > 0 and state.linger_reply_count < LINGER_MAX_MESSAGES
                ),
                "linger_reply_count": state.linger_reply_count,
                "linger_time_remaining": round(linger_time_remaining, 1),
                "linger_started_by": state.linger_started_by,
                "msg_1m": state.recent_message_count(60),
                "msg_5m": state.recent_message_count(300),
                "talk_value": state.talk_value,
                "last_active_ago": round(now - state.last_active_ts, 1),
            }
        return out

    async def _call_gate(self, group_id: str, pending: list[GroupPendingMessage],
                         ctx: dict, trigger_reason: str) -> dict:
        """调用 TimingGate 模型判断——to_thread 避免阻塞 event loop。"""
        from clients.classifier_client import get_timing_gate

        gate = get_timing_gate()
        context = self._build_timing_context(
            pending=pending, trigger_reason=trigger_reason, **ctx,
        )
        t0 = _time.time()
        result = await asyncio.to_thread(gate.judge, context)
        result.setdefault("latency_ms", int((_time.time() - t0) * 1000))
        result["context"] = context
        result["context_chars"] = len(context)
        result["pending_count"] = len(pending)
        result["trigger_reason"] = trigger_reason
        return result

    @staticmethod
    def _build_timing_context(
        *, pending: list[GroupPendingMessage], trigger_reason: str = "",
        session_name: str = "", bot_aliases: list[str] | None = None,
        last_bot_reply_ago: float | None = None,
        recent_context: str = "",
        **kwargs,
    ) -> str:
        """构造 TimingGate prompt context。"""
        from core.context_builder import sanitize_prompt_text

        lines: list[str] = ["<timing_context>"]
        sn = sanitize_prompt_text(session_name, 80)
        if sn:
            lines.append(f"群: {sn}")
        if any(p.is_reply_to_bot for p in pending):
            lines.append("注意:这条消息是回复bot的,说明用户在跟bot对话")
        if last_bot_reply_ago is not None and last_bot_reply_ago > 0:
            secs = int(last_bot_reply_ago)
            lines.append(f"bot上次发言: {secs}秒前")
        tr = sanitize_prompt_text(trigger_reason, 60)
        if tr:
            lines.append(f"触发原因: {tr}")
        aliases = [sanitize_prompt_text(str(a), 40) for a in (bot_aliases or [])[:8] if str(a).strip()]
        if aliases:
            lines.append(f"bot别名: {', '.join(aliases)}")

        # wait 元信息：让 TimingGate 知道这是 wait 结束后的重新判断
        previous_action = str(kwargs.get("previous_gate_action", "")).strip()
        if previous_action == "wait":
            wait_count = kwargs.get("wait_count", 0)
            new_msgs = kwargs.get("new_messages_during_wait", 0)
            wait_timeout = kwargs.get("wait_timeout", False)
            wait_reason = str(kwargs.get("wait_reason", ""))[:120]
            lines.append(f"上一次判定: wait (第{wait_count}次)")
            lines.append(f"等待期间新消息: {new_msgs}条")
            if wait_timeout:
                lines.append("等待超时且无新消息——禁止再次 wait")
            if wait_reason:
                lines.append(f"wait原因: {wait_reason}")

        rc = str(recent_context or "").strip()
        if rc:
            lines.append(rc)

        msgs = pending[:MAX_PENDING]
        for p in msgs:
            lines.append(
                _pending_payload([p])["pending_text"]
            )
        if len(pending) > MAX_PENDING:
            lines.append(f"...[pending 截断: 原{len(pending)}条]")
        lines.append("</timing_context>")

        return sanitize_prompt_text("\n".join(lines), 1600)


# ── 全局单例 ──

_runtime: GroupRuntime | None = None


def get_group_runtime() -> GroupRuntime:
    global _runtime
    if _runtime is None:
        _runtime = GroupRuntime()
    return _runtime
