from __future__ import annotations

import math
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.time_utils import db_now_naive

from core.group_runtime.constants import (
    IDLE_CLEANUP_SEC,
    LINGER_BASE_WEIGHT,
    LINGER_MAX_MESSAGES,
    LINGER_TIMEOUT_SECONDS,
    MAX_AGE_SEC,
    MAX_PENDING,
    MAX_RETRIES,
    MAX_WAIT_SEC,
    MIN_INTERVAL,
    TIMING_WAIT_DELAY_MAX,
    TIMING_WAIT_DELAY_MIN,
    _DIRECTED_SUPPRESS_BYPASS_TRIGGERS,
)


def _clip_timing_wait_delay(value: object, *, default: int = 5) -> int:
    try:
        delay = int(value if value is not None else default)
    except (TypeError, ValueError, OverflowError):
        delay = default
    return max(TIMING_WAIT_DELAY_MIN, min(TIMING_WAIT_DELAY_MAX, delay))


def _model_confidence_from_gate_result(result: dict) -> float:
    """从 TimingGate 结果提取模型置信度，兼容旧 parser 输出。"""
    raw_confidence = result.get("model_confidence")
    if raw_confidence is not None:
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        if not math.isfinite(confidence):
            return 0.0
        return max(0.0, min(1.0, confidence))

    parse_quality = str(result.get("parse_quality") or "").strip().lower()
    if parse_quality == "legacy":
        return 0.5
    if parse_quality in {"invalid", "network_error"}:
        return 0.0
    return 0.8


def should_suppress_directed_to_other(pending_msgs: list[GroupPendingMessage]) -> bool:
    """全部 pending 都指向其他用户且没有指向 bot → 不应插嘴。"""
    if not pending_msgs:
        return False
    if any(
        m.is_at_bot
        or m.is_reply_to_bot
        or m.trigger_reason in _DIRECTED_SUPPRESS_BYPASS_TRIGGERS
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
    is_other_bot: bool = False
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
            "is_other_bot": self.is_other_bot,
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
            timestamp=(
                datetime.fromtimestamp(msg.ts, tz=timezone.utc).astimezone().replace(tzinfo=None)
                if msg.ts > 0 else db_now_naive()
            ),
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


def _has_directed_to_other_signal(pending: list[GroupPendingMessage]) -> bool:
    return any(msg.is_directed_to_other for msg in pending)


def _has_other_recipient_signal(pending: list[GroupPendingMessage]) -> bool:
    return any(
        bool((msg.directed or {}).get("at_others"))
        or bool((msg.directed or {}).get("reply_to_others"))
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
    platform: str = "qq"
    linger_active_until: float = 0.0
    linger_reply_count: int = 0
    linger_source_user_id: str = ""
    linger_started_by: str = ""
    linger_last_reply_ts: float = 0.0
    last_active_ts: float = 0.0
    created_at: float = 0.0
    proactive_ts_window: list[float] = field(default_factory=list)  # 主动发言时间戳滑动窗口

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

    def proactive_budget_available(self, window_sec: float, max_per_window: int) -> bool:
        """滑动窗口预算:剪掉窗口外的时间戳,判断剩余是否小于上限。"""
        now = _time.time()
        self.proactive_ts_window = [t for t in self.proactive_ts_window if now - t < window_sec]
        return len(self.proactive_ts_window) < max(1, int(max_per_window))

    def record_proactive(self):
        """记录一次主动发言,消费预算。"""
        self.proactive_ts_window.append(_time.time())
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
        """设置强制指向标记——下一轮以 d0=1.0 进入评分/门控。"""
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
