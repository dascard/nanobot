from __future__ import annotations
"""GroupRuntime — per-group stateful runtime。

保留 GroupRuntime.process_message() / handle_timer_fired() API，
内部升级为 GroupChatState：force_next_continue + talk_value + message_cache。
"""

import asyncio
import logging
import math
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import datetime

logger = logging.getLogger("nanobot.group_runtime")

MIN_INTERVAL = 15
MAX_PENDING = 5
MAX_WAIT_SEC = 60
MAX_RETRIES = 3
MAX_AGE_SEC = 120
IDLE_CLEANUP_SEC = 600
BOT_REPLY_COOLDOWN_SEC = 30
LINGER_TIMEOUT_SECONDS = 180
LINGER_MAX_MESSAGES = 3
LINGER_BASE_WEIGHT = 0.70
LINGER_MIN_INTERVAL_SECS = 10
BOT_FOLLOWUP_WINDOW_SEC = LINGER_TIMEOUT_SECONDS
_DIRECT_TRIGGERS = {"at_bot", "reply_to_bot", "bot_name_mentioned", "direct_call", "mentioned"}
_COOLDOWN_BYPASS_TRIGGERS = _DIRECT_TRIGGERS | {"recent_bot_followup"}


def should_suppress_directed_to_other(pending_msgs: list[GroupPendingMessage]) -> bool:
    """全部 pending 都指向其他用户且没有指向 bot → 不应插嘴。"""
    if not pending_msgs:
        return False
    if any(
        m.is_at_bot
        or m.is_reply_to_bot
        or m.trigger_reason in _DIRECT_TRIGGERS
        for m in pending_msgs
    ):
        return False
    return all(m.is_directed_to_other for m in pending_msgs)


@dataclass
class GroupPendingMessage:
    sender_id: str
    sender_name: str
    message: str
    message_id: str = ""
    ts: float = 0.0
    is_at_bot: bool = False
    is_reply_to_bot: bool = False
    trigger_reason: str = "ambient"
    # ── 结构化字段 (Batch 1) ──
    segments: list[dict] | None = None
    raw_message: str = ""
    mentions: list[dict] | None = None
    reply_to: dict | None = None
    directed: dict | None = None
    is_directed_to_other: bool = False
    self_id: str = ""
    bot_id: str = ""
    bot_name: str = ""

    def __post_init__(self):
        if self.segments is None:
            self.segments = []
        if self.mentions is None:
            self.mentions = []
        if self.directed is None:
            self.directed = {}
        if self.directed.get("directed_to_other") and not (
            self.is_at_bot or self.is_reply_to_bot
        ):
            self.is_directed_to_other = True

    def to_dict(self) -> dict:
        return {
            "sender_id": self.sender_id, "sender_name": self.sender_name,
            "message": self.message, "message_id": self.message_id, "ts": self.ts,
            "is_reply_to_bot": self.is_reply_to_bot,
            "is_at_bot": self.is_at_bot, "trigger_reason": self.trigger_reason,
            "segments": self.segments or [], "mentions": self.mentions or [],
            "reply_to": self.reply_to, "directed": self.directed or {},
            "is_directed_to_other": self.is_directed_to_other,
            "self_id": self.self_id, "bot_id": self.bot_id,
            "bot_name": self.bot_name,
        }


def _format_direction_for_pending(msg: GroupPendingMessage) -> list[str]:
    """生成 [指向性] 和 [引用] 行。"""
    lines: list[str] = []
    d = msg.directed or {}
    if d.get("at_bot"):
        lines.append("[指向性] @bot")
    if d.get("reply_to_bot"):
        lines.append("[指向性] 回复bot")
    if d.get("at_others"):
        names = [
            m.get("nickname") or m.get("user_id", "") or "?"
            for m in (msg.mentions or [])
            if not m.get("is_bot")
        ]
        if names:
            lines.append(f"[指向性] @其他人: {', '.join(names[:5])}")
        else:
            lines.append("[指向性] @其他人")
    if d.get("reply_to_others"):
        lines.append("[指向性] 回复其他人")
    if msg.reply_to:
        sender = msg.reply_to.get("sender_name") or msg.reply_to.get("sender_id") or "未知用户"
        content = str(msg.reply_to.get("content") or "")
        if content:
            lines.append(f"[引用] {sender}: {content[:160]}")
    return lines


def _pending_payload(pending: list[GroupPendingMessage]) -> dict:
    from core.context_builder import format_group_planner_message, sanitize_prompt_text

    blocks: list[str] = []
    source_ids: list[str] = []
    for msg in pending:
        if msg.message_id:
            source_ids.append(msg.message_id)
        direction_lines = _format_direction_for_pending(msg)
        block = format_group_planner_message(
            sender_name=msg.sender_name or msg.sender_id or "未知用户",
            content=msg.message,
            timestamp=datetime.fromtimestamp(msg.ts) if msg.ts > 0 else datetime.now(),
            message_id=msg.message_id,
        )
        if direction_lines:
            block = "\n".join(direction_lines + [block])
        cleaned = sanitize_prompt_text(block, 500).strip()
        if cleaned:
            blocks.append(cleaned)
    return {
        "pending_text": "\n\n".join(blocks),
        "source_message_ids": source_ids,
    }


def _pending_text_for_scoring(pending: list[GroupPendingMessage]) -> str:
    return "\n".join(str(msg.message or "") for msg in pending if str(msg.message or "").strip())


def _has_file_segments(pending: list[GroupPendingMessage]) -> bool:
    file_types = {"image", "file"}
    return any(
        any(str(seg.get("type") or "").lower() in file_types for seg in (msg.segments or []))
        for msg in pending
    )


@dataclass
class GroupChatState:
    """单个群的运行时状态——per-group stateful runtime。"""
    group_id: str = ""
    stream_id: str = ""
    session_name: str = ""
    bot_aliases: list[str] = field(default_factory=list)
    bot_id: str = ""
    bot_name: str = ""
    pending: list[GroupPendingMessage] = field(default_factory=list)
    message_cache: list[GroupPendingMessage] = field(default_factory=list)
    generation: int = 0
    running: bool = False
    last_gate_completed_ts: float = 0.0
    last_bot_reply_ts: float = 0.0
    wait_count: int = 0
    total_wait_s: float = 0.0
    waiting_for_more: bool = False          # actively in wait state
    wait_started_at: float = 0.0            # when the current wait cycle started
    wait_until: float = 0.0                 # absolute time when current wait expires
    max_wait_until: float = 0.0             # hard deadline even with new messages
    new_messages_during_wait: list[dict] = field(default_factory=list)
    previous_gate_action: str = ""            # "wait"/"continue"/"no_reply"
    wait_reason: str = ""                     # why we entered wait
    force_next_continue: bool = False
    force_reason: str = ""
    talk_value: float = 0.5
    last_trigger_reason: str = "ambient"
    linger_active_until: float = 0.0
    linger_reply_count: int = 0
    linger_source_user_id: str = ""
    linger_started_by: str = ""
    linger_last_reply_ts: float = 0.0
    last_active_ts: float = 0.0
    created_at: float = 0.0

    def __post_init__(self):
        now = _time.time()
        if not self.last_active_ts:
            self.last_active_ts = now
        if not self.created_at:
            self.created_at = now

    def _touch(self):
        self.last_active_ts = _time.time()

    def add_message(self, msg: GroupPendingMessage):
        now = _time.time()
        # 兼容测试：ts=0 或被设为零值时使用当前时间
        if msg.ts <= 0:
            msg.ts = now
        self.pending = [m for m in self.pending if now - m.ts < MAX_AGE_SEC]
        self.pending.append(msg)
        self.message_cache.append(msg)
        if len(self.pending) > MAX_PENDING:
            self.pending = self.pending[-MAX_PENDING:]
        if len(self.message_cache) > 100:
            self.message_cache = self.message_cache[-100:]
        self.generation += 1
        self._touch()

    def take_snapshot(self) -> list[GroupPendingMessage]:
        return list(self.pending)

    def can_trigger_gate(self) -> bool:
        if self.running:
            return False
        return _time.time() - self.last_gate_completed_ts >= MIN_INTERVAL

    def next_gate_delay(self) -> int:
        if self.running:
            return 3
        remaining = MIN_INTERVAL - (_time.time() - self.last_gate_completed_ts)
        return max(1, min(MIN_INTERVAL, int(remaining) + 1))

    def mark_gate_start(self):
        self.running = True
        self._touch()

    def mark_gate_done(self):
        self.running = False
        self.last_gate_completed_ts = _time.time()
        self._touch()

    def handle_continue(self):
        self.pending.clear()
        self._reset_wait_state()
        self._touch()

    def handle_no_reply(self):
        self.pending.clear()
        self._reset_wait_state()
        self._touch()

    def _reset_wait_state(self):
        self.wait_count = 0
        self.total_wait_s = 0.0
        self.waiting_for_more = False
        self.wait_started_at = 0.0
        self.wait_until = 0.0
        self.max_wait_until = 0.0
        self.new_messages_during_wait.clear()
        self.previous_gate_action = ""
        self.wait_reason = ""

    def start_wait(self, delay: float, reason: str = ""):
        """进入 waiting_for_more 状态。"""
        now = _time.time()
        self.waiting_for_more = True
        self.wait_count += 1
        self.wait_started_at = now
        self.wait_until = now + delay
        self.max_wait_until = max(now, self.max_wait_until) if self.max_wait_until > 0 else (now + MAX_WAIT_SEC)
        self.wait_reason = reason
        self.previous_gate_action = "wait"
        self.new_messages_during_wait.clear()

    def receive_during_wait(self, msg_dict: dict):
        """等待期间收到新消息——只累积，不判断。"""
        self.new_messages_during_wait.append(msg_dict)

    def refresh_wait(self, max_delay: int = 10) -> bool:
        """新消息刷新 wait 计时器，但不超过 max_wait_until。不累加 total_wait_s。返回 True 表示刷新成功。"""
        now = _time.time()
        remaining = self.max_wait_until - now
        if remaining <= 0:
            return False
        new_delay = min(max_delay, remaining)
        self.wait_until = now + new_delay
        return True

    def is_wait_expired(self) -> bool:
        """wait 是否到期（到 wait_until 或已耗尽）。"""
        return _time.time() >= self.wait_until or self.is_wait_exhausted()

    def end_wait(self):
        """退出 waiting_for_more 状态——结算真实等待时长。"""
        if self.wait_started_at > 0:
            elapsed = _time.time() - self.wait_started_at
            self.total_wait_s += elapsed
            self.wait_started_at = 0.0
        self.waiting_for_more = False
        self.wait_until = 0.0
        self.max_wait_until = 0.0

    def is_wait_exhausted(self) -> bool:
        return self.wait_count > MAX_RETRIES or self.total_wait_s >= MAX_WAIT_SEC

    def is_idle(self) -> bool:
        return _time.time() - self.last_active_ts > IDLE_CLEANUP_SEC

    def bot_reply_ago(self) -> float:
        return (_time.time() - self.last_bot_reply_ts) if self.last_bot_reply_ts > 0 else 0

    def activate_linger(self, sender_id: str, trigger_reason: str):
        self.linger_active_until = _time.time() + LINGER_TIMEOUT_SECONDS
        self.linger_reply_count = 0
        self.linger_source_user_id = str(sender_id or "")
        self.linger_started_by = str(trigger_reason or "")
        self.linger_last_reply_ts = 0.0
        self._touch()

    def linger_score_for(self, sender_id: str = "") -> float:
        now = _time.time()
        if now >= self.linger_active_until:
            return 0.0
        if self.linger_reply_count >= LINGER_MAX_MESSAGES:
            return 0.0

        elapsed = max(0.0, LINGER_TIMEOUT_SECONDS - (self.linger_active_until - now))
        time_active = max(0.0, min(1.0, 1.0 - elapsed / LINGER_TIMEOUT_SECONDS))
        quota_active = max(0.0, min(1.0, 1.0 - self.linger_reply_count / LINGER_MAX_MESSAGES))
        same_user_weight = 1.0 if sender_id and sender_id == self.linger_source_user_id else 0.65
        score = (
            LINGER_BASE_WEIGHT
            * (0.55 + 0.45 * time_active)
            * (0.60 + 0.40 * quota_active)
            * same_user_weight
        )
        return max(0.0, min(LINGER_BASE_WEIGHT, score))

    def note_bot_replied(self):
        self.last_bot_reply_ts = _time.time()
        if self.linger_active_until > self.last_bot_reply_ts:
            self.linger_reply_count += 1
            self.linger_last_reply_ts = self.last_bot_reply_ts
        self._touch()

    def arm_force_continue(self, reason: str):
        """设置强制 continue 标记——下一轮跳过 TimingGate。"""
        self.force_next_continue = True
        self.force_reason = reason

    def recent_message_count(self, window_sec: int = 60) -> int:
        now = _time.time()
        return sum(1 for m in self.message_cache if now - m.ts <= window_sec)

    def recent_sender_count(self, window_sec: int = 60) -> int:
        now = _time.time()
        return len({
            m.sender_id
            for m in self.message_cache
            if now - m.ts <= window_sec and m.sender_id
        })


class GroupRuntime:
    """管理所有群的运行时状态——兼容旧 timing_runtime API。"""

    def __init__(self):
        self._states: dict[str, GroupChatState] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _norm_group_id(group_id: str) -> str:
        """所有入口强制 normalize——防止不同格式的 group_id 创建多个 state。"""
        from core.group_runtime.ids import normalize_group_session_id
        return normalize_group_session_id(group_id)

    @staticmethod
    def _should_cooldown(state: GroupChatState, trigger_reason: str) -> bool:
        ago = state.bot_reply_ago()
        if not (0 < ago < BOT_REPLY_COOLDOWN_SEC):
            return False
        if state.force_next_continue:
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
    ) -> dict:
        """生成 TimingGate scoring shadow 字段，不参与主决策。"""
        try:
            from core.timing_score import TimingModelHint, decide_timing

            msgs = list(pending if pending is not None else state.pending)
            tr = str(trigger_reason or state.last_trigger_reason or "").strip()
            is_direct = tr in _DIRECT_TRIGGERS
            model_hint = None
            if model_result:
                error_type = str(model_result.get("error_type") or "")
                action = str(model_result.get("action") or "").strip()
                confidence = 0.0 if error_type else 0.8
                if action:
                    model_hint = TimingModelHint(
                        action=action,
                        confidence=confidence,
                        raw=str(model_result.get("raw") or ""),
                        reason=str(model_result.get("reason") or ""),
                    )
            latest_sender_id = msgs[-1].sender_id if msgs else ""
            linger_score = state.linger_score_for(latest_sender_id) if tr == "recent_bot_followup" else 0.0
            if tr == "recent_bot_followup" and linger_score <= 0 and state.linger_active_until <= 0:
                linger_score = LINGER_BASE_WEIGHT
            ago = state.bot_reply_ago()
            linger_interval_active = (
                tr == "recent_bot_followup"
                and linger_score > 0
                and 0 < ago < LINGER_MIN_INTERVAL_SECS
            )
            min_interval_active = linger_interval_active or self._should_cooldown(state, tr)
            min_interval_remaining = (
                LINGER_MIN_INTERVAL_SECS - ago
                if linger_interval_active
                else BOT_REPLY_COOLDOWN_SEC - ago
            )
            decision = decide_timing(
                text=_pending_text_for_scoring(msgs),
                is_group=True,
                is_private=False,
                is_at_bot=any(m.is_at_bot for m in msgs) or tr == "at_bot",
                is_reply_to_bot=any(m.is_reply_to_bot for m in msgs) or tr == "reply_to_bot",
                bot_name_mentioned=tr in {"bot_name_mentioned", "mentioned"},
                direct_call=tr == "direct_call" or is_direct,
                is_directed_to_other=should_suppress_directed_to_other(msgs),
                has_files=_has_file_segments(msgs),
                linger_score=linger_score,
                min_interval_active=min_interval_active,
                min_interval_remaining=max(0.0, min_interval_remaining),
                model_hint=model_hint,
            )
            return asdict(decision)
        except Exception as exc:
            logger.debug("[GroupRuntime] shadow scoring failed: %s", exc, exc_info=True)
            return {}

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

    async def process_message(
        self, group_id: str, msg: dict, *,
        trigger_reason: str = "", session_name: str = "",
        bot_aliases: list[str] | None = None,
        recent_context: str = "",
        talk_value: float = 0.5,
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

            # directed_to_other hard rule：全部指向别人 → no_reply
            if should_suppress_directed_to_other(state.pending):
                payload = _pending_payload(state.pending)
                return self._attach_shadow_scoring({
                    "action": "no_reply",
                    "generation": state.generation,
                    "reason": "directed_to_other_no_bot_target",
                    "hard_rule": "directed_to_other_no_bot_target",
                    "pending_count": len(state.pending),
                    "trigger_reason": tr,
                    "directed_to_other": True,
                    **payload,
                }, state, tr)

            # 硬 cooldown：bot 刚回复过且非直接互动 → wait
            if self._should_cooldown(state, tr):
                ago = state.bot_reply_ago()
                return self._attach_shadow_scoring({
                    "action": "wait",
                    "delay_seconds": max(1, math.ceil(BOT_REPLY_COOLDOWN_SEC - ago)),
                    "generation": state.generation,
                    "cooldown_ago": round(ago, 1),
                    "reason": "bot 刚回复过，冷却中",
                }, state, tr)

            # force_next_continue → 直接 continue
            if state.force_next_continue:
                state.force_next_continue = False
                snapshot = state.take_snapshot()
                gen = state.generation
                payload = _pending_payload(snapshot)
                state.mark_gate_start()
                state.handle_continue()
                state.mark_gate_done()
                logger.info("[GroupRuntime] force_continue group=%s reason=%s pending=%d",
                            group_id, state.force_reason, len(snapshot))
                return self._attach_shadow_scoring({
                    "action": "continue",
                    "generation": gen,
                    "reason": f"direct trigger: {state.force_reason}",
                    **payload,
                }, state, state.force_reason or tr, pending=snapshot)

            # talk_value gate：普通 ambient 消息按频率控制
            if tr == "ambient" and not self._should_gate_by_frequency(state, talk_value):
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

            state.mark_gate_start()
            snapshot = state.take_snapshot()
            gen = state.generation
            ctx_snapshot = dict(ctx)

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

            return self._apply_gate_result(state, result)

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
                return self._attach_shadow_scoring({
                    "action": "wait",
                    "delay_seconds": max(1, math.ceil(BOT_REPLY_COOLDOWN_SEC - ago)),
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

            state.mark_gate_start()
            snapshot = state.take_snapshot()
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

        result = await self._call_gate(group_id, snapshot, ctx_saved, trigger_reason or "timer")

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
                }, state, trigger_reason or "timer", pending=snapshot, model_result=result)

            return self._apply_gate_result(state, result)

    def _apply_gate_result(self, state: GroupChatState, result: dict) -> dict:
        action = result.get("action", "no_reply")
        delay = None
        payload = _pending_payload(state.pending)
        scoring = self._shadow_scoring(
            state,
            str(result.get("trigger_reason") or state.last_trigger_reason or ""),
            model_result=result,
        )

        if action == "continue":
            state.handle_continue()
        elif action == "no_reply":
            state.handle_no_reply()
        elif action == "wait":
            delay = max(3, min(30, int(result.get("delay_seconds", 5) or 5)))
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
                state.start_wait(delay, result.get("reason", ""))
        else:
            state.handle_no_reply()
            action = "no_reply"

        cooldown = round(state.bot_reply_ago(), 1)
        state.mark_gate_done()
        response = {
            "action": action, "delay_seconds": delay,
            "generation": state.generation,
            "cooldown_ago": cooldown,
            "reason": result.get("reason", ""),
        }
        if scoring:
            response["timing_scoring"] = scoring
        for key in (
            "raw", "error_type", "latency_ms", "context", "context_chars",
            "pending_count", "trigger_reason",
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
