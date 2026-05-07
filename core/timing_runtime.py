"""TimingGate 状态机——server 端 per-group 状态管理。

从 QQbot timing_state.py 搬入，QQbot 退化为纯收发层：
  新消息 → POST /group_timing → process_message()
  wait到期 → POST /group_timing (timer_fired=true) → handle_timer_fired()
"""

import asyncio
import logging
import math
import time as _time

logger = logging.getLogger("nanobot.timing_runtime")

MIN_INTERVAL = 15          # 同群两次 gate 最小间隔秒
MAX_PENDING = 5            # 最多合并消息数
MAX_WAIT_SEC = 60          # 累计等待上限秒
MAX_RETRIES = 3            # wait 重试上限
MAX_AGE_SEC = 120          # 消息最大年龄秒
IDLE_CLEANUP_SEC = 600     # 10 分钟无活动清理 state
BOT_REPLY_COOLDOWN_SEC = 30  # bot 刚回复后降低插话概率
_DIRECT_TRIGGERS = {"bot_name_mentioned", "mentioned", "reply_to_bot", "direct_call", "at_bot"}


class PendingMessage:
    __slots__ = ("sender_id", "sender_name", "message", "message_id", "ts", "is_reply_to_bot")

    def __init__(self, sender_id: str, sender_name: str, message: str,
                 message_id: str = "", ts: float | None = None,
                 is_reply_to_bot: bool = False):
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.message = message
        self.message_id = message_id
        self.ts = ts or _time.time()
        self.is_reply_to_bot = is_reply_to_bot

    def to_dict(self) -> dict:
        return {
            "sender_id": self.sender_id, "sender_name": self.sender_name,
            "message": self.message, "message_id": self.message_id, "ts": self.ts,
            "is_reply_to_bot": self.is_reply_to_bot,
        }


def _pending_payload(pending: list[PendingMessage]) -> dict:
    """把待处理群消息转换成 Maibot planner 风格文本，供主回复链路使用。"""
    from datetime import datetime
    from core.context_builder import format_group_planner_message

    blocks: list[str] = []
    source_ids: list[str] = []
    for msg in pending:
        if msg.message_id:
            source_ids.append(msg.message_id)
        block = format_group_planner_message(
            sender_name=msg.sender_name or msg.sender_id or "未知用户",
            content=msg.message,
            timestamp=datetime.fromtimestamp(msg.ts),
            message_id=msg.message_id,
        )
        if block.strip():
            blocks.append(block)
    return {
        "pending_text": "\n\n".join(blocks),
        "source_message_ids": source_ids,
    }


class GateState:
    """一个群的 TimingGate 状态。"""

    def __init__(self):
        self.pending: list[PendingMessage] = []
        self.generation: int = 0
        self.last_gate_completed_ts: float = 0.0
        self.running: bool = False
        self.wait_count: int = 0
        self.total_wait_s: float = 0.0
        self.last_bot_reply_ts: float = 0.0
        self.last_active_ts: float = _time.time()
        self.created_at: float = _time.time()
        self.session_name: str = ""
        self.bot_aliases: list[str] = []

    def _touch(self):
        self.last_active_ts = _time.time()

    def add_message(self, msg: PendingMessage):
        now = _time.time()
        self.pending = [m for m in self.pending if now - m.ts < MAX_AGE_SEC]
        self.pending.append(msg)
        if len(self.pending) > MAX_PENDING:
            self.pending = self.pending[-MAX_PENDING:]
        self.generation += 1
        self._touch()

    def take_snapshot(self) -> list[PendingMessage]:
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
        self.wait_count = 0
        self.total_wait_s = 0.0
        self._touch()

    def handle_no_reply(self):
        self.pending.clear()
        self.wait_count = 0
        self.total_wait_s = 0.0
        self._touch()

    def try_wait(self, delay: float) -> bool:
        self.wait_count += 1
        self.total_wait_s += delay
        if self.is_wait_exhausted():
            self.pending.clear()
            return False
        return True

    def is_wait_exhausted(self) -> bool:
        return self.wait_count > MAX_RETRIES or self.total_wait_s >= MAX_WAIT_SEC

    def is_idle(self) -> bool:
        return _time.time() - self.last_active_ts > IDLE_CLEANUP_SEC

    def bot_reply_ago(self) -> float:
        return (_time.time() - self.last_bot_reply_ts) if self.last_bot_reply_ts > 0 else 0

    def note_bot_replied(self):
        self.last_bot_reply_ts = _time.time()
        self._touch()


class GroupRuntime:
    """管理所有群的 TimingGate 状态。"""

    def __init__(self):
        self._states: dict[str, GateState] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _should_cooldown(state: GateState, trigger_reason: str) -> bool:
        ago = state.bot_reply_ago()
        if not (0 < ago < BOT_REPLY_COOLDOWN_SEC):
            return False
        is_direct = (
            any(p.is_reply_to_bot for p in state.pending)
            or str(trigger_reason or "").strip() in _DIRECT_TRIGGERS
        )
        return not is_direct

    async def process_message(
        self, group_id: str, msg: dict, *,
        trigger_reason: str = "", session_name: str = "",
        bot_aliases: list[str] | None = None,
    ) -> dict:
        """处理新消息——添加、判断是否触发 gate、返回结果。"""
        pm = PendingMessage(
            sender_id=str(msg.get("sender_id", "")),
            sender_name=str(msg.get("sender_name", "")),
            message=str(msg.get("message", "")),
            message_id=str(msg.get("message_id", "")),
            is_reply_to_bot=msg.get("is_reply_to_bot", False),
        )
        ctx = {
            "session_name": session_name,
            "bot_aliases": bot_aliases or [],
        }

        async with self._lock:
            state = self._states.setdefault(group_id, GateState())
            state.add_message(pm)
            state.session_name = session_name
            state.bot_aliases = list(bot_aliases or [])
            if state.last_bot_reply_ts > 0:
                ctx["last_bot_reply_ago"] = _time.time() - state.last_bot_reply_ts

            # 硬 cooldown：bot 刚回复过且非直接互动 → 不调 gate，直接 wait
            if self._should_cooldown(state, trigger_reason):
                ago = state.bot_reply_ago()
                return {
                    "action": "wait", "delay_seconds": max(1, math.ceil(BOT_REPLY_COOLDOWN_SEC - ago)),
                    "generation": state.generation,
                    "cooldown_ago": round(ago, 1),
                    "reason": "bot 刚回复过，冷却中",
                }

            if not state.can_trigger_gate():
                return {
                    "action": "wait", "delay_seconds": state.next_gate_delay(),
                    "generation": state.generation,
                    "cooldown_ago": state.bot_reply_ago(),
                    "reason": "rate limited / gate in progress",
                }

            state.mark_gate_start()
            snapshot = state.take_snapshot()
            gen = state.generation
            ctx_snapshot = dict(ctx)

        result = await self._call_gate(group_id, snapshot, ctx_snapshot, trigger_reason)

        async with self._lock:
            state = self._states.get(group_id)
            if not state:
                return {"action": "no_reply", "delay_seconds": None, "reason": "state cleaned up"}

            if gen != state.generation:
                state.mark_gate_done()
                logger.info("[timing_runtime] gen mismatch %d!=%d for %s",
                            gen, state.generation, group_id)
                return {"action": "no_reply", "delay_seconds": None,
                        "generation": state.generation,
                        "cooldown_ago": state.bot_reply_ago(),
                        "reason": "generation mismatch, new messages arrived during gate"}

            return self._apply_gate_result(state, result)

    async def handle_timer_fired(self, group_id: str, generation: int,
                                 trigger_reason: str = "") -> dict:
        """wait timer 到期——校验 generation 后重新 gate 判断。"""
        async with self._lock:
            state = self._states.get(group_id)
            if not state:
                return {"action": "no_reply", "delay_seconds": None, "reason": "state cleaned up"}

            if generation != state.generation:
                logger.info("[timing_runtime] timer gen mismatch %d!=%d for %s",
                            generation, state.generation, group_id)
                return {"action": "no_reply", "delay_seconds": None,
                        "generation": state.generation,
                        "reason": "generation mismatch, timer expired"}

            # 硬 cooldown 同样适用于 timer 回调
            if self._should_cooldown(state, trigger_reason):
                ago = state.bot_reply_ago()
                return {"action": "wait", "delay_seconds": max(1, math.ceil(BOT_REPLY_COOLDOWN_SEC - ago)),
                        "generation": state.generation,
                        "cooldown_ago": round(ago, 1),
                        "reason": "bot 刚回复过，冷却中"}

            if not state.can_trigger_gate():
                return {"action": "wait", "delay_seconds": state.next_gate_delay(),
                        "generation": state.generation,
                        "cooldown_ago": round(state.bot_reply_ago(), 1),
                        "reason": "rate limited"}

            state.mark_gate_start()
            snapshot = state.take_snapshot()
            gen = state.generation
            ctx_saved = {
                "session_name": state.session_name,
                "bot_aliases": list(state.bot_aliases),
            }
            if state.last_bot_reply_ts > 0:
                ctx_saved["last_bot_reply_ago"] = _time.time() - state.last_bot_reply_ts

        result = await self._call_gate(group_id, snapshot, ctx_saved, trigger_reason)

        async with self._lock:
            state = self._states.get(group_id)
            if not state:
                return {"action": "no_reply", "delay_seconds": None, "reason": "state cleaned up"}
            if gen != state.generation:
                state.mark_gate_done()
                return {"action": "no_reply", "delay_seconds": None,
                        "generation": state.generation,
                        "reason": "state changed during timer gate"}

            return self._apply_gate_result(state, result)

    def _apply_gate_result(self, state: GateState, result: dict) -> dict:
        """统一的 gate 结果处理——process_message 和 handle_timer_fired 共用。"""
        action = result.get("action", "no_reply")
        delay = None
        payload = _pending_payload(state.pending)

        if action == "continue":
            state.handle_continue()
        elif action == "no_reply":
            state.handle_no_reply()
        elif action == "wait":
            delay = max(3, min(30, int(result.get("delay_seconds", 5) or 5)))
            if not state.try_wait(delay):
                state.handle_no_reply()
                action = "no_reply"
                delay = None
        else:
            state.handle_no_reply()
            action = "no_reply"

        cooldown = round(state.bot_reply_ago(), 1)
        state.mark_gate_done()
        response = {"action": action, "delay_seconds": delay,
                    "generation": state.generation,
                    "cooldown_ago": cooldown,
                    "reason": result.get("reason", "")}
        if action == "continue":
            response.update(payload)
        return response

    def note_bot_replied(self, group_id: str):
        state = self._states.get(group_id)
        if state:
            state.note_bot_replied()

    def cleanup_idle(self):
        stale = [gid for gid, s in self._states.items() if s.is_idle()]
        for gid in stale:
            del self._states[gid]
        if stale:
            logger.info("[timing_runtime] cleaned %d idle states", len(stale))

    async def _call_gate(self, group_id: str, pending: list[PendingMessage],
                         ctx: dict, trigger_reason: str) -> dict:
        """调用 TimingGate 模型判断——to_thread 避免阻塞 event loop。"""
        from clients.classifier_client import get_timing_gate

        gate = get_timing_gate()
        context = self._build_timing_context(
            pending=pending, trigger_reason=trigger_reason, **ctx,
        )
        return await asyncio.to_thread(gate.judge, context)

    @staticmethod
    def _build_timing_context(
        *, pending: list[PendingMessage], trigger_reason: str = "",
        session_name: str = "", bot_aliases: list[str] | None = None,
        last_bot_reply_ago: float | None = None,
    ) -> str:
        """构造 TimingGate prompt context——不依赖 api.routes。"""
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

        msgs = pending[:MAX_PENDING]
        for p in msgs:
            lines.append(
                _pending_payload([p])["pending_text"]
            )
        if len(pending) > MAX_PENDING:
            lines.append(f"...[pending 截断: 原{len(pending)}条]")
        lines.append("</timing_context>")

        return sanitize_prompt_text("\n".join(lines), 1200)


_runtime: GroupRuntime | None = None


def get_group_runtime() -> GroupRuntime:
    global _runtime
    if _runtime is None:
        _runtime = GroupRuntime()
    return _runtime
