"""GroupRuntime 状态机测试——不依赖网络/模型。"""

import asyncio
import time as _time

import pytest

from core.timing_runtime import (
    GateState, PendingMessage, GroupRuntime, MAX_PENDING, MAX_AGE_SEC, MAX_RETRIES, MAX_WAIT_SEC,
)


class TestGateState:
    def test_add_message_increments_generation(self):
        s = GateState()
        assert s.generation == 0
        s.add_message(PendingMessage("u1", "A", "hello"))
        assert s.generation == 1

    def test_take_snapshot_returns_copy(self):
        s = GateState()
        s.add_message(PendingMessage("u1", "A", "hello"))
        snap = s.take_snapshot()
        assert len(snap) == 1
        snap.clear()
        assert len(s.pending) == 1

    def test_cannot_trigger_when_running(self):
        s = GateState()
        s.mark_gate_start()
        assert not s.can_trigger_gate()

    def test_handle_continue_clears_pending(self):
        s = GateState()
        s.add_message(PendingMessage("u1", "A", "hello"))
        s.handle_continue()
        assert len(s.pending) == 0

    def test_handle_no_reply_clears_pending(self):
        s = GateState()
        s.add_message(PendingMessage("u1", "A", "hello"))
        s.handle_no_reply()
        assert len(s.pending) == 0

    def test_try_wait_exhausted_by_retries(self):
        s = GateState()
        for _ in range(MAX_RETRIES + 1):
            ok = s.try_wait(5)
        assert not ok

    def test_try_wait_exhausted_by_time(self):
        s = GateState()
        ok = s.try_wait(MAX_WAIT_SEC + 1)
        assert not ok

    def test_old_messages_pruned_on_add(self):
        s = GateState()
        s.pending.append(PendingMessage("u1", "A", "old", ts=_time.time() - MAX_AGE_SEC - 10))
        s.add_message(PendingMessage("u1", "A", "new"))
        assert len(s.pending) == 1
        assert s.pending[0].message == "new"

    def test_pending_capped_at_max(self):
        s = GateState()
        for i in range(MAX_PENDING + 3):
            s.add_message(PendingMessage("u1", "A", f"msg{i}"))
        assert len(s.pending) == MAX_PENDING

    def test_generation_mismatch_after_add(self):
        s = GateState()
        s.add_message(PendingMessage("u1", "A", "msg1"))
        gen = s.generation
        s.add_message(PendingMessage("u1", "A", "msg2"))
        assert s.generation == gen + 1


class TestGroupRuntime:
    @pytest.mark.asyncio
    async def test_process_message_rate_limited_returns_wait(self, monkeypatch):
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "continue", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        r1 = await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        })
        assert r1["action"] == "continue"
        assert r1["delay_seconds"] is None  # continue 无 delay

        r2 = await runtime.process_message("g1", {
            "sender_id": "u2", "sender_name": "B", "message": "hi",
        })
        assert r2["action"] == "wait"
        assert isinstance(r2["delay_seconds"], int)
        assert r2["delay_seconds"] > 0

    @pytest.mark.asyncio
    async def test_generation_mismatch_clears_running(self, monkeypatch):
        """gen mismatch 后 running 必须被清掉——否则群永久卡死。"""
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            # 模拟新消息到来
            runtime._states["g1"].add_message(
                PendingMessage("u2", "B", "interrupt"))
            return {"action": "continue", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        r = await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        })
        assert r["action"] == "no_reply"
        assert "mismatch" in r["reason"]
        # running 必须已清除
        assert not runtime._states["g1"].running

    @pytest.mark.asyncio
    async def test_session_name_saved_for_timer_reuse(self, monkeypatch):
        """timer 回调时从 state 取回 session_name/bot_aliases。"""
        runtime = GroupRuntime()
        captured_ctx = {}

        async def fake_gate(_gid, _p, ctx, _tr):
            captured_ctx.update(ctx)
            return {"action": "wait", "delay_seconds": 5, "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        }, session_name="测试群", bot_aliases=["testbot"])
        assert captured_ctx.get("session_name") == "测试群"
        assert "testbot" in captured_ctx.get("bot_aliases", [])

        # bypass rate limit for timer test
        runtime._states["g1"].last_gate_completed_ts = 0
        captured_ctx.clear()
        await runtime.handle_timer_fired("g1", generation=1)
        assert captured_ctx.get("session_name") == "测试群"

    @pytest.mark.asyncio
    async def test_timer_fired_gen_mismatch_rejected(self, monkeypatch):
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "wait", "delay_seconds": 5, "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        r1 = await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        })
        assert r1["action"] == "wait"
        gen = r1["generation"]

        # 新消息到来 → generation 变化
        runtime._states["g1"].add_message(PendingMessage("u2", "B", "new"))

        r2 = await runtime.handle_timer_fired("g1", gen)
        assert r2["action"] == "no_reply"
        assert "mismatch" in r2["reason"]

    @pytest.mark.asyncio
    async def test_rate_limited_wait_has_positive_delay(self, monkeypatch):
        """rate limited 返回的 wait 必须有正整数 delay_seconds。"""
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "continue", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        })
        r2 = await runtime.process_message("g1", {
            "sender_id": "u2", "sender_name": "B", "message": "hi",
        })
        assert r2["action"] == "wait"
        assert isinstance(r2["delay_seconds"], int)
        assert r2["delay_seconds"] > 0

    @pytest.mark.asyncio
    async def test_continue_no_reply_delay_is_none(self, monkeypatch):
        """continue/no_reply 的 delay_seconds 应为 None。"""
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "continue", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)
        r = await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        })
        assert r["action"] == "continue"
        assert r["delay_seconds"] is None

        async def fake_no_reply(_gid, _p, _ctx, _tr):
            return {"action": "no_reply", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_no_reply)
        # reset state to allow gate
        runtime._states["g1"].last_gate_completed_ts = 0
        r2 = await runtime.process_message("g1", {
            "sender_id": "u2", "sender_name": "B", "message": "hi",
        })
        assert r2["action"] == "no_reply"
        assert r2["delay_seconds"] is None

    @pytest.mark.asyncio
    async def test_timer_retains_session_context(self, monkeypatch):
        """timer 回调时 session_name/bot_aliases 从 state 恢复。"""
        runtime = GroupRuntime()
        captured = {}

        async def fake_gate(_gid, _p, ctx, _tr):
            captured.update(ctx)
            return {"action": "wait", "delay_seconds": 5, "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        }, session_name="测试群", bot_aliases=["testbot"])
        assert captured.get("session_name") == "测试群"

        captured.clear()
        runtime._states["g1"].last_gate_completed_ts = 0
        await runtime.handle_timer_fired("g1", generation=1)
        assert captured.get("session_name") == "测试群"

    @pytest.mark.asyncio
    async def test_idle_cleanup_removes_old_states(self):
        runtime = GroupRuntime()
        runtime._states["g_old"] = GateState()
        runtime._states["g_old"].last_active_ts = _time.time() - 9999
        runtime._states["g_new"] = GateState()

        runtime.cleanup_idle()
        assert "g_old" not in runtime._states
        assert "g_new" in runtime._states
