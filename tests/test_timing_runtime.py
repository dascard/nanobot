"""GroupRuntime 状态机测试——不依赖网络/模型。"""

import asyncio
import time as _time

import pytest

from core.timing_runtime import (
    GateState, PendingMessage, GroupRuntime, MAX_PENDING, MAX_AGE_SEC, MAX_RETRIES, MAX_WAIT_SEC,
)


def _gate_relevant_state(state):
    from core.group_runtime.state import GateStateSnapshot

    return GateStateSnapshot.capture(state)


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

    def test_start_wait_exhausted_by_retries(self):
        s = GateState()
        for _ in range(MAX_RETRIES + 1):
            s.start_wait(5, "test")
        assert s.is_wait_exhausted()

    def test_start_wait_exhausted_by_time(self):
        s = GateState()
        s.total_wait_s = MAX_WAIT_SEC + 1
        assert s.is_wait_exhausted()

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

    def test_same_nonempty_message_id_is_idempotent_while_pending(self):
        s = GateState()
        s.add_message(PendingMessage("u1", "A", "first", message_id="m1"))

        s.add_message(PendingMessage("u1", "A", "retry", message_id="m1"))

        assert s.generation == 1
        assert [message.message for message in s.pending] == ["first"]
        assert [message.message for message in s.message_cache] == ["first"]

    def test_add_message_returns_added_and_duplicate_has_zero_state_mutation(
        self,
        monkeypatch,
    ):
        import core.group_runtime.state as state_module

        now = 1000.0
        monkeypatch.setattr(state_module._time, "time", lambda: now)
        state = GateState(group_id="group_g1")
        first = PendingMessage("u1", "A", "first", message_id="m1")
        duplicate = PendingMessage("u1", "A", "retry", message_id="m1")

        assert state.add_message(first) is True
        before = (
            list(state.pending),
            list(state.message_cache),
            state.generation,
            state.last_active_ts,
        )
        now = 1001.0

        assert state.add_message(duplicate) is False
        assert (
            state.pending,
            state.message_cache,
            state.generation,
            state.last_active_ts,
        ) == before

    def test_gate_state_snapshot_restores_every_gate_relevant_field(self):
        from core.group_runtime.state import GateStateSnapshot

        state = GateState(group_id="group_g1")
        state.add_message(PendingMessage(
            "u1",
            "A",
            "hello",
            message_id="m1",
            segments=[{"type": "text", "data": {"text": "hello"}}],
        ))
        state.start_wait(9, "等待更多")
        state.receive_during_wait({
            "sender_name": "B",
            "message": "new",
            "details": {"source": "wait"},
        })
        state.activate_linger("u1", "at_bot")
        state.record_proactive()
        state.force_next_continue = True
        state.force_reason = "at_bot"
        state.last_gate_completed_ts = 123.0
        snapshot = GateStateSnapshot.capture(state)

        state.pending[0].segments[0]["data"]["text"] = "mutated"
        state.pending.clear()
        state._reset_wait_state()
        state.linger_reply_count = 99
        state.proactive_ts_window.clear()
        state.force_next_continue = False
        state.force_reason = ""
        state.running = True
        state.last_gate_completed_ts = 999.0
        snapshot.restore_exact(state)

        assert GateStateSnapshot.capture(state) == snapshot
        assert state.pending[0].segments[0]["data"]["text"] == "hello"
        assert state.new_messages_during_wait[0]["details"] == {"source": "wait"}


class TestGroupRuntime:
    @pytest.mark.asyncio
    async def test_directed_scoring_shortcut_base_exception_restores_full_snapshot(
        self,
        monkeypatch,
    ):
        from core.timing_score import decide_timing

        class ApplyFatal(BaseException):
            pass

        runtime = GroupRuntime()
        state = runtime._states.setdefault(
            "group_1",
            GateState(group_id="group_1"),
        )
        state.add_message(
            PendingMessage(
                "seed",
                "种子",
                "@其他人 保留",
                message_id="seed",
                is_directed_to_other=True,
                directed={
                    "at_others": True,
                    "directed_to_other": True,
                },
            )
        )
        state.start_wait(8, "原 wait")
        state.end_wait()
        state.activate_linger("seed", "at_bot")
        state.record_proactive()
        state.last_gate_completed_ts = 123.0
        error = ApplyFatal("shortcut apply fatal")
        shortcut_decision = decide_timing(
            "@其他人 你看",
            is_directed_to_other=True,
            has_other_recipient=True,
        )
        assert shortcut_decision.stage == "rule_shortcut"
        monkeypatch.setattr(
            runtime,
            "_score_timing",
            lambda *_args, **_kwargs: shortcut_decision,
        )

        original_apply = runtime._apply_scoring_shortcut

        def apply_then_fail(state_arg, decision, **kwargs):
            original_apply(state_arg, decision, **kwargs)
            raise error

        monkeypatch.setattr(runtime, "_apply_scoring_shortcut", apply_then_fail)
        original_begin = runtime._begin_gate
        recorded = {}

        def recording_begin(group_id, state_arg):
            transaction = original_begin(group_id, state_arg)
            recorded["snapshot"] = transaction.snapshot
            return transaction

        monkeypatch.setattr(runtime, "_begin_gate", recording_begin)

        with pytest.raises(ApplyFatal) as raised:
            await runtime.process_message(
                "group_1",
                {
                    "sender_id": "u1",
                    "sender_name": "A",
                    "message": "@B 你看",
                    "message_id": "m1",
                    "is_directed_to_other": True,
                    "directed": {
                        "at_others": True,
                        "directed_to_other": True,
                    },
                },
                trigger_reason="ambient",
                talk_value=1.0,
            )

        assert raised.value is error
        assert _gate_relevant_state(state) == recorded["snapshot"]

    @pytest.mark.asyncio
    async def test_timer_failure_restores_wait_linger_budget_pending_and_timestamp(
        self,
        monkeypatch,
    ):
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda *_args: TimingModelPolicy("enabled", "test")
        )
        state = runtime._states.setdefault(
            "group_g1",
            GateState(group_id="group_g1"),
        )
        state.add_message(
            PendingMessage("u1", "A", "hello", message_id="m1")
        )
        state.start_wait(5, "等待续句")
        state.activate_linger("u1", "at_bot")
        state.record_proactive()
        state.last_gate_completed_ts = 77.0
        before = _gate_relevant_state(state)
        error = RuntimeError("timer failed")

        async def fail_gate(*_args, **_kwargs):
            raise error

        monkeypatch.setattr(runtime, "_call_gate", fail_gate)
        with pytest.raises(RuntimeError) as raised:
            await runtime.handle_timer_fired(
                "g1",
                state.generation,
                trigger_reason="retry_test",
            )

        assert raised.value is error
        assert _gate_relevant_state(state) == before

    def test_runtime_has_one_gate_start_boundary_and_scoring_has_none(self):
        import inspect

        import core.group_runtime.runtime as runtime_module
        import core.group_runtime.scoring as scoring_module

        runtime_source = inspect.getsource(runtime_module)
        scoring_source = inspect.getsource(scoring_module)
        assert runtime_source.count(".mark_gate_start(") == 1
        assert ".mark_gate_start(" not in scoring_source
        assert ".mark_gate_done(" not in scoring_source

    @pytest.mark.asyncio
    async def test_duplicate_message_during_wait_does_not_refresh_deadline_or_generation(
        self,
        monkeypatch,
    ):
        import core.group_runtime.runtime as runtime_module
        import core.group_runtime.state as state_module

        now = 1000.0
        monkeypatch.setattr(runtime_module._time, "time", lambda: now)
        monkeypatch.setattr(state_module._time, "time", lambda: now)
        runtime = GroupRuntime()
        state = runtime._states.setdefault("group_g1", GateState(group_id="group_g1"))
        message = PendingMessage("u1", "A", "first", message_id="m1")
        assert state.add_message(message) is True
        state.start_wait(8, "wait for more")
        state.talk_value = 0.8
        state.platform = "qq"
        state.last_trigger_reason = "original_trigger"
        state.session_name = "原群名"
        state.bot_aliases = ["原别名"]
        state.bot_id = "original-bot"
        state.bot_name = "原机器人"
        before = (
            state.wait_until,
            list(state.new_messages_during_wait),
            state.generation,
            state.last_active_ts,
            state.talk_value,
            state.platform,
            state.last_trigger_reason,
            state.session_name,
            list(state.bot_aliases),
            state.bot_id,
            state.bot_name,
        )
        now = 1001.0

        result = await runtime.process_message(
            "g1",
            {
                "sender_id": "u1",
                "sender_name": "A",
                "message": "retry",
                "message_id": "m1",
                "bot_id": "changed-bot",
                "bot_name": "变更机器人",
            },
            trigger_reason="at_bot",
            session_name="变更群名",
            bot_aliases=["变更别名"],
            talk_value=0.2,
            platform="discord",
        )

        assert result["action"] == "wait"
        assert (
            state.wait_until,
            state.new_messages_during_wait,
            state.generation,
            state.last_active_ts,
            state.talk_value,
            state.platform,
            state.last_trigger_reason,
            state.session_name,
            state.bot_aliases,
            state.bot_id,
            state.bot_name,
        ) == before

    @pytest.mark.asyncio
    async def test_duplicate_message_after_wait_deadline_retries_existing_pending(
        self,
        monkeypatch,
    ):
        import core.group_runtime.runtime as runtime_module
        import core.group_runtime.state as state_module
        from core.timing_model_policy import TimingModelPolicy

        now = 1000.0
        monkeypatch.setattr(runtime_module._time, "time", lambda: now)
        monkeypatch.setattr(state_module._time, "time", lambda: now)
        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda *_args: TimingModelPolicy("enabled", "test")
        )
        state = runtime._states.setdefault(
            "group_g1",
            GateState(group_id="group_g1"),
        )
        assert state.add_message(
            PendingMessage("u1", "A", "first", message_id="m1")
        ) is True
        state.start_wait(8, "wait for more")
        calls: list[list[str]] = []

        async def gate(_group_id, pending, _ctx, _reason):
            calls.append([item.message_id for item in pending])
            return {
                "action": "no_reply",
                "reason": "expired duplicate retried",
                "model_confidence": 1.0,
            }

        monkeypatch.setattr(runtime, "_call_gate", gate)
        now = 1009.0

        result = await runtime.process_message(
            "g1",
            {
                "sender_id": "u1",
                "sender_name": "A",
                "message": "retry",
                "message_id": "m1",
            },
            trigger_reason="retry_test",
        )

        assert calls == [["m1"]]
        assert result["action"] == "no_reply"
        assert state.generation == 1
        assert state.waiting_for_more is False
        assert state.new_messages_during_wait == []

    @pytest.mark.asyncio
    async def test_process_message_rate_limited_returns_wait(self, monkeypatch):
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "continue", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        r1 = await runtime.process_message("g1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "Nanobot 你看 https://example.com",
        }, trigger_reason="bot_name_mentioned")
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
            runtime._states["group_g1"].add_message(
                PendingMessage("u2", "B", "interrupt"))
            return {"action": "continue", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        r = await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        })
        assert r["action"] == "no_reply"
        assert "mismatch" in r["reason"]
        # running 必须已清除
        assert not runtime._states["group_g1"].running

    @pytest.mark.asyncio
    async def test_direct_message_during_running_gate_does_not_start_second_gate(
        self,
        monkeypatch,
    ):
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda *_args: TimingModelPolicy("enabled", "test")
        )
        first_gate_entered = asyncio.Event()
        release_first_gate = asyncio.Event()
        gate_calls = 0

        async def controlled_gate(*_args, **_kwargs):
            nonlocal gate_calls
            gate_calls += 1
            if gate_calls != 1:
                raise AssertionError("同一群已有 gate 运行时不得启动第二个 gate")
            first_gate_entered.set()
            await release_first_gate.wait()
            return {"action": "continue", "reason": "first gate returned"}

        monkeypatch.setattr(runtime, "_call_gate", controlled_gate)
        first_task = asyncio.create_task(runtime.process_message(
            "g1",
            {
                "sender_id": "u1",
                "sender_name": "A",
                "message": "first",
                "message_id": "m1",
            },
            trigger_reason="retry_test",
        ))
        try:
            await asyncio.wait_for(first_gate_entered.wait(), timeout=1)

            second_result = await runtime.process_message(
                "g1",
                {
                    "sender_id": "u2",
                    "sender_name": "B",
                    "message": "@bot https://example.com/second",
                    "message_id": "m2",
                    "is_at_bot": True,
                },
                trigger_reason="at_bot",
            )

            state = runtime._states["group_g1"]
            assert second_result["action"] == "wait"
            assert "gate in progress" in second_result["reason"]
            assert gate_calls == 1
            assert state.running is True
            assert [item.message_id for item in state.pending] == ["m1", "m2"]

            release_first_gate.set()
            first_result = await first_task

            assert first_result["action"] == "no_reply"
            assert "generation mismatch" in first_result["reason"]
            assert state.running is False
            assert state.force_next_continue is True
            assert state.force_reason == "at_bot"
        finally:
            release_first_gate.set()
            if not first_task.done():
                first_task.cancel()
            await asyncio.gather(first_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_gate_error_aborts_without_cooldown_and_same_message_retries(self, monkeypatch):
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda _session_id, _platform: TimingModelPolicy("enabled", "test")
        )
        gate_error = RuntimeError("timing boom")
        calls: list[list[str]] = []

        async def flaky_gate(_gid, pending, _ctx, _tr):
            calls.append([message.message_id for message in pending])
            if len(calls) == 1:
                raise gate_error
            return {"action": "wait", "delay_seconds": 5, "reason": "retry reached gate"}

        monkeypatch.setattr(runtime, "_call_gate", flaky_gate)
        message = {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "hello",
            "message_id": "m1",
        }

        with pytest.raises(RuntimeError) as raised:
            await runtime.process_message("g1", message, trigger_reason="retry_test")

        assert raised.value is gate_error
        state = runtime._states["group_g1"]
        state_after_error = (
            state.running,
            state.last_gate_completed_ts,
            state.generation,
            len(state.pending),
        )

        result = await runtime.process_message("g1", message, trigger_reason="retry_test")

        assert state_after_error == (False, 0.0, 1, 1)
        assert calls == [["m1"], ["m1"]]
        assert "rate limited" not in result["reason"]
        assert state.generation == 1

    @pytest.mark.asyncio
    async def test_process_cancel_while_reacquiring_lock_restores_gate_state(self, monkeypatch):
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda _session_id, _platform: TimingModelPolicy("enabled", "test")
        )
        state = runtime._states.setdefault("group_g1", GateState(group_id="group_g1"))
        previous_completed_ts = 123.456
        state.last_gate_completed_ts = previous_completed_ts
        gate_entered = asyncio.Event()
        release_gate = asyncio.Event()
        observed: dict[str, BaseException] = {}

        async def controlled_gate(*_args, **_kwargs):
            gate_entered.set()
            await release_gate.wait()
            return {"action": "continue", "reason": "model returned"}

        async def invoke():
            try:
                return await runtime.process_message(
                    "g1",
                    {
                        "sender_id": "u1",
                        "sender_name": "A",
                        "message": "hello",
                        "message_id": "cancel-reacquire",
                    },
                    trigger_reason="retry_test",
                )
            except BaseException as exc:
                observed["error"] = exc
                raise

        monkeypatch.setattr(runtime, "_call_gate", controlled_gate)
        task = asyncio.create_task(invoke())
        lock_held = False
        try:
            await asyncio.wait_for(gate_entered.wait(), timeout=1)
            await runtime._lock.acquire()
            lock_held = True
            release_gate.set()
            await asyncio.sleep(0)
            assert not task.done()

            task.cancel("cancel while reacquiring runtime lock")
            with pytest.raises(asyncio.CancelledError) as raised:
                await task

            assert raised.value is observed["error"]
            assert state.running is False
            assert state.last_gate_completed_ts == previous_completed_ts
        finally:
            release_gate.set()
            if lock_held:
                runtime._lock.release()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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
        runtime._states["group_g1"].last_gate_completed_ts = 0
        captured_ctx.clear()
        await runtime.handle_timer_fired("g1", generation=1)
        assert captured_ctx.get("session_name") == "测试群"

    @pytest.mark.asyncio
    async def test_timer_linger_active_bypasses_talk_value_gate(self, monkeypatch):
        """linger 激活时,timer 路径应像消息路径一样豁免 talk_value 频率门。

        回归:消息路径(:265)在 linger 激活时豁免 talk_value gate,但 timer 路径(:381)
        曾漏掉同一豁免,导致余韵窗口内的 timer 被频率门错误拦成 wait,让 bot 静默。
        """
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "continue", "reason": "gate reached"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        # 建 state:一条 ambient 消息,talk_value 低使频率门阈值 > 当前 pending
        await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "随便说一句",
        }, trigger_reason="ambient", talk_value=0.2)

        state = runtime._states["group_g1"]
        # 模拟刚 @过 bot:linger 激活;并绕开限流,让 timer 能进入 gate 判断
        state.activate_linger("u1", "at_bot")
        state.last_trigger_reason = "ambient"
        state.last_gate_completed_ts = 0.0
        gen = state.generation

        assert runtime._active_linger_score(state) > 0  # 前置:linger 确实激活

        r = await runtime.handle_timer_fired("g1", gen, trigger_reason="ambient")

        # 修复后:豁免 talk_value gate,继续走 gate(fake 返回 continue)
        assert not str(r.get("reason", "")).startswith("talk_value gate"), (
            f"linger 激活时 timer 仍被 talk_value gate 拦下: {r.get('reason')}"
        )
        assert r["action"] == "continue"

    @pytest.mark.asyncio
    async def test_timer_talk_value_gate_still_applies_without_linger(self, monkeypatch):
        """无 linger 时,timer 的 talk_value 频率门仍应生效(不能误放行)。"""
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "continue", "reason": "gate reached"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "随便说一句",
        }, trigger_reason="ambient", talk_value=0.2)

        state = runtime._states["group_g1"]
        state.last_trigger_reason = "ambient"
        state.last_gate_completed_ts = 0.0
        gen = state.generation

        assert runtime._active_linger_score(state) == 0  # 前置:无 linger

        r = await runtime.handle_timer_fired("g1", gen, trigger_reason="ambient")

        # 无 linger 时频率门照常拦截
        assert r["action"] == "wait"
        assert str(r.get("reason", "")).startswith("talk_value gate")

    @pytest.mark.asyncio
    async def test_timer_gate_error_aborts_without_success_cooldown(self, monkeypatch):
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda _session_id, _platform: TimingModelPolicy("enabled", "test")
        )
        state = runtime._states.setdefault("group_g1", GateState(group_id="group_g1"))
        state.add_message(PendingMessage("u1", "A", "hello", message_id="m1"))
        state.last_trigger_reason = "retry_test"
        gate_error = RuntimeError("timer timing boom")

        async def fail_gate(*_args, **_kwargs):
            raise gate_error

        monkeypatch.setattr(runtime, "_call_gate", fail_gate)

        with pytest.raises(RuntimeError) as raised:
            await runtime.handle_timer_fired(
                "g1",
                state.generation,
                trigger_reason="retry_test",
            )

        assert raised.value is gate_error
        assert state.running is False
        assert state.last_gate_completed_ts == 0.0

    @pytest.mark.asyncio
    async def test_timer_apply_base_exception_restores_previous_completed_timestamp(
        self,
        monkeypatch,
    ):
        from core.timing_model_policy import TimingModelPolicy

        class TimerApplyFatal(BaseException):
            pass

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda _session_id, _platform: TimingModelPolicy("enabled", "test")
        )
        state = runtime._states.setdefault("group_g1", GateState(group_id="group_g1"))
        state.add_message(PendingMessage("u1", "A", "hello", message_id="timer-apply"))
        state.last_trigger_reason = "retry_test"
        previous_completed_ts = 234.567
        state.last_gate_completed_ts = previous_completed_ts
        apply_error = TimerApplyFatal("timer apply fatal")

        async def successful_gate(*_args, **_kwargs):
            return {"action": "continue", "reason": "model returned"}

        def fail_after_done(state_arg, *_args, **_kwargs):
            state_arg.mark_gate_done()
            raise apply_error

        monkeypatch.setattr(runtime, "_call_gate", successful_gate)
        monkeypatch.setattr(runtime, "_apply_gate_result", fail_after_done)

        with pytest.raises(TimerApplyFatal) as raised:
            await runtime.handle_timer_fired(
                "g1",
                state.generation,
                trigger_reason="retry_test",
            )

        assert raised.value is apply_error
        assert state.running is False
        assert state.last_gate_completed_ts == previous_completed_ts

    # ── 主动发言(proactive)──

    @staticmethod
    def _patch_proactive_settings(monkeypatch, *, enabled=True, floor=3, ceiling=20,
                                  window=1800, max_per=2):
        """固定 proactive 设置,避免依赖真实 DB。"""
        from core.settings_service import settings
        ints = {
            "timing_gate.proactive.activity_floor": floor,
            "timing_gate.proactive.activity_ceiling": ceiling,
            "timing_gate.proactive.window_sec": window,
            "timing_gate.proactive.max_per_window": max_per,
        }
        monkeypatch.setattr(settings, "get_bool", lambda k, d=False: enabled if k == "timing_gate.proactive.enabled" else d)
        monkeypatch.setattr(settings, "get_int", lambda k, d=0: ints.get(k, d))

    @staticmethod
    def _cold_active_state(runtime, n_msgs=3):
        """构造冷启动 + 活跃群 state:n 条普通 ambient 消息(无 bot 信号)。"""
        state = runtime._states.setdefault("group_g1", GateState(group_id="group_g1"))
        for i in range(n_msgs):
            state.add_message(PendingMessage(f"u{i}", f"用户{i}", f"闲聊内容{i}"))
        return state

    def test_proactive_precheck_passes_on_cold_active_group(self, monkeypatch):
        runtime = GroupRuntime()
        self._patch_proactive_settings(monkeypatch)
        state = self._cold_active_state(runtime, n_msgs=3)
        assert runtime._proactive_precheck(state, state.take_snapshot()) is True

    def test_proactive_precheck_blocked_when_disabled(self, monkeypatch):
        runtime = GroupRuntime()
        self._patch_proactive_settings(monkeypatch, enabled=False)
        state = self._cold_active_state(runtime, n_msgs=3)
        assert runtime._proactive_precheck(state, state.take_snapshot()) is False

    def test_proactive_precheck_blocked_when_bot_signal(self, monkeypatch):
        runtime = GroupRuntime()
        self._patch_proactive_settings(monkeypatch)
        state = self._cold_active_state(runtime, n_msgs=2)
        state.add_message(PendingMessage("u9", "小明", "在吗", is_at_bot=True))
        assert runtime._proactive_precheck(state, state.take_snapshot()) is False

    def test_proactive_precheck_blocked_when_linger_active(self, monkeypatch):
        runtime = GroupRuntime()
        self._patch_proactive_settings(monkeypatch)
        state = self._cold_active_state(runtime, n_msgs=3)
        state.activate_linger(state.take_snapshot()[-1].sender_id, "at_bot")
        assert runtime._proactive_precheck(state, state.take_snapshot()) is False

    def test_proactive_precheck_blocked_when_dead_group(self, monkeypatch):
        runtime = GroupRuntime()
        self._patch_proactive_settings(monkeypatch, floor=3)
        state = self._cold_active_state(runtime, n_msgs=1)  # msg_5m=1 < floor
        assert runtime._proactive_precheck(state, state.take_snapshot()) is False

    def test_proactive_precheck_blocked_when_firehose(self, monkeypatch):
        runtime = GroupRuntime()
        self._patch_proactive_settings(monkeypatch, ceiling=20)
        state = self._cold_active_state(runtime, n_msgs=25)  # msg_1m=25 >= ceiling
        assert runtime._proactive_precheck(state, state.take_snapshot()) is False

    def test_proactive_precheck_blocked_when_budget_exhausted(self, monkeypatch):
        runtime = GroupRuntime()
        self._patch_proactive_settings(monkeypatch, max_per=2)
        state = self._cold_active_state(runtime, n_msgs=3)
        state.record_proactive()
        state.record_proactive()  # 用满 2 次
        assert runtime._proactive_precheck(state, state.take_snapshot()) is False

    @pytest.mark.asyncio
    async def test_process_message_public_path_runs_proactive_and_commits_once(
        self,
        monkeypatch,
    ):
        runtime = GroupRuntime()
        self._patch_proactive_settings(monkeypatch, floor=3)
        state = runtime._states.setdefault(
            "group_g1",
            GateState(group_id="group_g1"),
        )
        state.add_message(
            PendingMessage("u0", "用户0", "闲聊0", message_id="m0")
        )
        state.add_message(
            PendingMessage("u1", "用户1", "闲聊1", message_id="m1")
        )
        monkeypatch.setattr(
            "core.group_runtime.runtime.judge_group_proactive",
            lambda _context: {
                "should_speak": True,
                "reason": "可补充",
                "error_type": None,
            },
        )
        original_commit = runtime._commit_gate
        committed = []

        def recording_commit(transaction):
            committed.append(transaction)
            original_commit(transaction)

        monkeypatch.setattr(runtime, "_commit_gate", recording_commit)

        result = await runtime.process_message(
            "g1",
            {
                "sender_id": "u2",
                "sender_name": "用户2",
                "message": "闲聊2",
                "message_id": "m2",
            },
            trigger_reason="ambient",
            talk_value=1.0,
        )

        assert result["action"] == "continue"
        assert result["proactive"]["should_speak"] is True
        assert len(committed) == 1
        assert committed[0].active is False
        assert state.running is False
        assert len(state.proactive_ts_window) == 1
        assert state.pending == []

    @pytest.mark.asyncio
    async def test_two_group_gate_transactions_isolate_failure_and_success(
        self,
        monkeypatch,
    ):
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda *_args: TimingModelPolicy("enabled", "test")
        )
        both_entered = asyncio.Event()
        release = asyncio.Event()
        entered: set[str] = set()
        failure = RuntimeError("group one failed")
        tasks: list[asyncio.Task] = []

        async def gate(group_id, *_args):
            entered.add(group_id)
            if len(entered) == 2:
                both_entered.set()
            await release.wait()
            if group_id == "group_1":
                raise failure
            return {
                "action": "continue",
                "reason": "group two succeeds",
                "model_confidence": 1.0,
            }

        monkeypatch.setattr(runtime, "_call_gate", gate)
        try:
            tasks = [
                asyncio.create_task(runtime.process_message(
                    "1",
                    {
                        "sender_id": "u1",
                        "sender_name": "A",
                        "message": "@bot @B one",
                        "message_id": "m1",
                        "is_at_bot": True,
                        "directed": {
                            "at_bot": True,
                            "at_others": True,
                            "directed_to_other": False,
                        },
                    },
                    trigger_reason="at_bot",
                )),
                asyncio.create_task(runtime.process_message(
                    "2",
                    {
                        "sender_id": "u2",
                        "sender_name": "B",
                        "message": "@bot @C two",
                        "message_id": "m2",
                        "is_at_bot": True,
                        "directed": {
                            "at_bot": True,
                            "at_others": True,
                            "directed_to_other": False,
                        },
                    },
                    trigger_reason="at_bot",
                )),
            ]
            await asyncio.wait_for(both_entered.wait(), timeout=1)
            runtime._states["group_1"].add_message(
                PendingMessage("u3", "C", "new", message_id="m3")
            )
            release.set()

            results = await asyncio.gather(*tasks, return_exceptions=True)

            assert results[0] is failure
            assert results[1]["action"] == "continue"
            assert [
                item.message_id
                for item in runtime._states["group_1"].pending
            ] == ["m1", "m3"]
            assert runtime._states["group_1"].running is False
            assert runtime._states["group_1"].force_next_continue is True
            assert runtime._states["group_1"].force_reason == "at_bot"
            assert runtime._states["group_2"].running is False
            assert runtime._states["group_2"].force_next_continue is False
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_gate_state_replacement_does_not_mutate_new_state(
        self,
        monkeypatch,
    ):
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda *_args: TimingModelPolicy("enabled", "test")
        )
        gate_entered = asyncio.Event()
        release = asyncio.Event()

        async def gate(*_args, **_kwargs):
            gate_entered.set()
            await release.wait()
            return {"action": "continue", "reason": "old state result"}

        monkeypatch.setattr(runtime, "_call_gate", gate)
        task = asyncio.create_task(runtime.process_message(
            "1",
            {
                "sender_id": "u1",
                "sender_name": "A",
                "message": "old",
                "message_id": "old-m1",
            },
            trigger_reason="retry_test",
        ))
        try:
            await asyncio.wait_for(gate_entered.wait(), timeout=1)
            replacement = GateState(group_id="group_1")
            replacement.add_message(
                PendingMessage("u2", "B", "new", message_id="new-m1")
            )
            replacement.start_wait(9, "replacement wait")
            replacement.running = True
            replacement.last_gate_completed_ts = 987.0
            before = _gate_relevant_state(replacement)
            runtime._states["group_1"] = replacement
            release.set()

            result = await task

            assert result["action"] == "no_reply"
            assert "replaced" in result["reason"]
            assert _gate_relevant_state(replacement) == before
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_run_proactive_speak_produces_continue_and_linger(self, monkeypatch):
        runtime = GroupRuntime()
        state = self._cold_active_state(runtime, n_msgs=3)

        def fake_judge(_ctx):
            return {"should_speak": True, "reason": "有可贡献的内容", "error_type": None}

        monkeypatch.setattr("core.group_runtime.runtime.judge_group_proactive", fake_judge)
        transaction = runtime._begin_gate("group_g1", state)
        gen = state.generation
        r = await runtime._run_proactive(
            "group_g1",
            state.take_snapshot(),
            {},
            gen,
            "u2",
            transaction=transaction,
        )

        assert r["action"] == "continue"
        assert r["proactive"]["should_speak"] is True
        assert state.linger_source_user_id == "u2"
        assert runtime._active_linger_score(state) > 0
        assert len(state.proactive_ts_window) == 1
        assert state.running is False  # gate 已释放

    @pytest.mark.asyncio
    async def test_run_proactive_declined_keeps_no_reply(self, monkeypatch):
        runtime = GroupRuntime()
        state = self._cold_active_state(runtime, n_msgs=3)

        monkeypatch.setattr("core.group_runtime.runtime.judge_group_proactive",
                            lambda _c: {"should_speak": False, "reason": "无需插话", "error_type": None})
        transaction = runtime._begin_gate("group_g1", state)
        gen = state.generation
        r = await runtime._run_proactive(
            "group_g1",
            state.take_snapshot(),
            {},
            gen,
            "u2",
            transaction=transaction,
        )

        assert r["action"] == "no_reply"
        assert len(state.proactive_ts_window) == 0  # 未消费预算
        assert state.running is False

    @pytest.mark.asyncio
    async def test_run_proactive_gen_mismatch_dropped(self, monkeypatch):
        runtime = GroupRuntime()
        state = self._cold_active_state(runtime, n_msgs=3)

        monkeypatch.setattr("core.group_runtime.runtime.judge_group_proactive",
                            lambda _c: {"should_speak": True, "reason": "x", "error_type": None})
        transaction = runtime._begin_gate("group_g1", state)
        gen = state.generation
        state.add_message(PendingMessage("u5", "新人", "新消息"))  # generation 变化

        r = await runtime._run_proactive(
            "group_g1",
            state.take_snapshot(),
            {},
            gen,
            "u2",
            transaction=transaction,
        )
        assert r["action"] == "no_reply"
        assert "mismatch" in r["reason"]
        assert len(state.proactive_ts_window) == 0

    @pytest.mark.asyncio
    async def test_run_proactive_llm_error_defaults_silent(self, monkeypatch):
        runtime = GroupRuntime()
        state = self._cold_active_state(runtime, n_msgs=3)

        monkeypatch.setattr("core.group_runtime.runtime.judge_group_proactive",
                            lambda _c: {"should_speak": False, "reason": "裁判不可用", "error_type": "network_error"})
        transaction = runtime._begin_gate("group_g1", state)
        gen = state.generation
        r = await runtime._run_proactive(
            "group_g1",
            state.take_snapshot(),
            {},
            gen,
            "u2",
            transaction=transaction,
        )

        assert r["action"] == "no_reply"
        assert r["proactive"]["error_type"] == "network_error"

    @pytest.mark.asyncio
    async def test_run_proactive_cancel_aborts_without_success_cooldown(self, monkeypatch):
        runtime = GroupRuntime()
        state = self._cold_active_state(runtime, n_msgs=3)
        cancellation = asyncio.CancelledError("proactive cancelled")

        def cancel_proactive(_context):
            raise cancellation

        monkeypatch.setattr("core.group_runtime.runtime.judge_group_proactive", cancel_proactive)
        transaction = runtime._begin_gate("group_g1", state)

        with pytest.raises(asyncio.CancelledError) as raised:
            await runtime._run_proactive(
                "group_g1",
                state.take_snapshot(),
                {},
                state.generation,
                "u2",
                transaction=transaction,
            )

        assert raised.value is cancellation
        assert state.running is False
        assert state.last_gate_completed_ts == 0.0

    @pytest.mark.asyncio
    async def test_run_proactive_normalize_base_exception_uses_explicit_transaction(
        self,
        monkeypatch,
    ):
        class ProactiveNormalizeFatal(BaseException):
            pass

        runtime = GroupRuntime()
        state = self._cold_active_state(runtime, n_msgs=3)
        previous_completed_ts = 456.789
        state.last_gate_completed_ts = previous_completed_ts
        transaction = runtime._begin_gate("group_g1", state)
        normalize_error = ProactiveNormalizeFatal("proactive normalize fatal")

        def fail_normalize(_group_id):
            raise normalize_error

        monkeypatch.setattr(runtime, "_norm_group_id", fail_normalize)

        with pytest.raises(ProactiveNormalizeFatal) as raised:
            await runtime._run_proactive(
                "group_g1",
                state.take_snapshot(),
                {},
                state.generation,
                "u2",
                transaction=transaction,
            )

        assert raised.value is normalize_error
        assert state.running is False
        assert state.last_gate_completed_ts == previous_completed_ts

    @pytest.mark.asyncio
    async def test_proactive_post_done_base_exception_restores_timestamp_and_primary(
        self,
        monkeypatch,
    ):
        from core.group_runtime.state import GateStateSnapshot

        class ProactiveApplyFatal(BaseException):
            pass

        runtime = GroupRuntime()
        state = self._cold_active_state(runtime, n_msgs=3)
        previous_completed_ts = 345.678
        state.last_gate_completed_ts = previous_completed_ts
        transaction = runtime._begin_gate("group_g1", state)
        apply_error = ProactiveApplyFatal("proactive apply fatal")
        cleanup_error = RuntimeError("abort cleanup failed")
        logging_error = RuntimeError("gate abort logging failed")
        original_done = state.mark_gate_done
        original_restore = GateStateSnapshot.restore_exact

        class BrokenLogger:
            def exception(self, *_args, **_kwargs):
                raise logging_error

        def done_then_fail():
            original_done()
            raise apply_error

        def restore_then_fail(snapshot, state_arg):
            original_restore(snapshot, state_arg)
            raise cleanup_error

        monkeypatch.setattr(
            "core.group_runtime.runtime.judge_group_proactive",
            lambda _context: {
                "should_speak": False,
                "reason": "declined",
                "error_type": None,
            },
        )
        monkeypatch.setattr(state, "mark_gate_done", done_then_fail)
        monkeypatch.setattr(
            GateStateSnapshot,
            "restore_exact",
            restore_then_fail,
        )
        monkeypatch.setattr("core.group_runtime.runtime.logger", BrokenLogger())

        with pytest.raises(ProactiveApplyFatal) as raised:
            await runtime._run_proactive(
                "group_g1",
                state.take_snapshot(),
                {},
                state.generation,
                "u2",
                transaction=transaction,
            )

        assert raised.value is apply_error
        assert state.running is False
        assert state.last_gate_completed_ts == previous_completed_ts

    @pytest.mark.asyncio
    async def test_timer_fired_gen_mismatch_rejected(self, monkeypatch):
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "wait", "delay_seconds": 5, "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        r1 = await runtime.process_message("g1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "Nanobot 你看 https://example.com",
        }, trigger_reason="bot_name_mentioned")
        assert r1["action"] == "wait"
        gen = r1["generation"]

        # 新消息到来 → generation 变化
        runtime._states["group_g1"].add_message(PendingMessage("u2", "B", "new"))

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
            "sender_id": "u1",
            "sender_name": "A",
            "message": "Nanobot 你看 https://example.com",
        }, trigger_reason="bot_name_mentioned")
        assert r["action"] == "continue"
        assert r["delay_seconds"] is None

        async def fake_no_reply(_gid, _p, _ctx, _tr):
            return {"action": "no_reply", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_no_reply)
        # reset state to allow gate
        runtime._states["group_g1"].last_gate_completed_ts = 0
        r2 = await runtime.process_message("g1", {
            "sender_id": "u2",
            "sender_name": "B",
            "message": "Nanobot 再看下 https://example.com/2",
        }, trigger_reason="bot_name_mentioned")
        assert r2["action"] == "no_reply"
        assert r2["delay_seconds"] is None

    @pytest.mark.asyncio
    async def test_continue_returns_pending_payload_for_chat_pipeline(self, monkeypatch):
        """continue 结果携带 pending 文本和 message_id，供 bridge 构造当前输入并落库。"""
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "continue", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)
        r = await runtime.process_message("g1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "Nanobot 你怎么看这个方案 https://example.com",
            "message_id": "m100",
        }, trigger_reason="bot_name_mentioned")

        assert r["action"] == "continue"
        assert "[用户名]A" in r["pending_text"]
        assert "[发言内容]Nanobot 你怎么看这个方案 https://example.com" in r["pending_text"]
        assert r["source_message_ids"] == ["m100"]

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
        runtime._states["group_g1"].last_gate_completed_ts = 0
        await runtime.handle_timer_fired("g1", generation=1)
        assert captured.get("session_name") == "测试群"
        assert captured.get("bot_aliases") == ["testbot"]

    @pytest.mark.asyncio
    async def test_idle_cleanup_removes_old_states(self):
        runtime = GroupRuntime()
        runtime._states["group_g_old"] = GateState()
        runtime._states["group_g_old"].last_active_ts = _time.time() - 9999
        runtime._states["group_g_new"] = GateState()

        runtime.cleanup_idle()
        assert "group_g_old" not in runtime._states
        assert "group_g_new" in runtime._states

    def test_build_timing_context_includes_all_fields(self):
        """_build_timing_context 覆盖 session_name/bot_aliases/reply_to_bot。"""
        pending = [
            PendingMessage("u1", "A", "hello", is_reply_to_bot=True),
        ]
        ctx = GroupRuntime._build_timing_context(
            pending=pending, session_name="测试群",
            bot_aliases=["testbot"], trigger_reason="mentioned",
        )
        assert "测试群" in ctx
        assert "testbot" in ctx
        assert "回复bot" in ctx
        assert "mentioned" in ctx

    def test_note_bot_replied_updates_timestamp(self):
        """note_bot_replied 更新 last_bot_reply_ts。"""
        runtime = GroupRuntime()
        runtime._states["group_g1"] = GateState()
        assert runtime._states["group_g1"].last_bot_reply_ts == 0.0
        runtime.note_bot_replied("g1")
        assert runtime._states["group_g1"].last_bot_reply_ts > 0

    def test_note_bot_replied_nonexistent_no_error(self):
        """不存在的 group 调用 note_bot_replied 不抛异常。"""
        runtime = GroupRuntime()
        runtime.note_bot_replied("nonexistent")  # 不应抛异常

    def test_snapshot_states_exposes_linger_debug_fields(self, monkeypatch):
        """群运行时快照暴露余韵调试字段，便于 WebUI 排查追问状态。"""
        import core.group_runtime.runtime as runtime_module

        now = 1000.0
        monkeypatch.setattr(runtime_module._time, "time", lambda: now)
        runtime = GroupRuntime()
        state = GateState(group_id="group_g1")
        runtime._states["group_g1"] = state

        state.activate_linger("u1", "at_bot")
        state.note_bot_replied()
        now += 30

        snapshot = runtime.snapshot_states()["group_g1"]

        assert snapshot["linger_active"] is True
        assert snapshot["linger_reply_count"] == 1
        assert snapshot["linger_time_remaining"] == pytest.approx(150.0)

    def test_build_timing_context_includes_cooldown(self):
        """last_bot_reply_ago 注入 TimingGate context。"""
        ctx = GroupRuntime._build_timing_context(
            pending=[PendingMessage("u1", "A", "hi")],
            last_bot_reply_ago=12.3,
        )
        assert "bot上次发言: 12秒前" in ctx

    @pytest.mark.asyncio
    async def test_note_bot_replied_passes_cooldown_to_gate(self, monkeypatch):
        """直接触发通过 force_next_continue 返回，跳过 cooldown。"""
        runtime = GroupRuntime()

        gate_calls = []
        async def fake_gate(_gid, _p, _ctx, _tr):
            gate_calls.append(1)
            return {"action": "no_reply", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        runtime._states["group_g1"] = GateState()
        runtime._states["group_g1"].last_gate_completed_ts = 0
        runtime.note_bot_replied("g1")

        r = await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hi",
        }, trigger_reason="bot_name_mentioned")
        assert r["action"] == "continue"
        assert "direct trigger" in r.get("reason", "")
        assert len(gate_calls) == 0

    @pytest.mark.asyncio
    async def test_direct_at_bot_with_image_waits_via_scoring_without_gate(self, monkeypatch):
        """@bot + 图片只应进入就绪度 wait，不应由 force_next_continue 直接 continue。"""
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            raise AssertionError("@bot + 图片应由规则评分短路为 wait，不应调用 TimingGate")

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        r = await runtime.process_message("g1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "@bot 看这个",
            "is_at_bot": True,
            "segments": [{"type": "image", "url": "https://example.com/a.png"}],
        }, trigger_reason="at_bot")

        assert r["action"] == "wait"
        assert r["delay_seconds"] == 5
        assert r["timing_scoring"]["stage"] == "rule_shortcut"
        signals = r["timing_scoring"]["signals"]
        assert signals["explicit_direct_score"] == 1.0
        assert signals["sub_signals"]["w_file"] == 0.45
        assert signals["sub_signals"]["s_transport"] == 0.0

    @pytest.mark.asyncio
    async def test_wait_timeout_direct_message_model_continue_survives_scoring(
        self, monkeypatch,
    ):
        """@bot+图片 wait 超时后模型 continue 不得被静态 wait 信号推翻成沉默。"""
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda *_args: TimingModelPolicy("enabled", "test")
        )

        async def noop_gate(_gid, _p, _ctx, _tr):
            raise AssertionError("首轮应由评分短路,不调用 gate")

        monkeypatch.setattr(runtime, "_call_gate", noop_gate)
        first = await runtime.process_message("g1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "@bot 看这个",
            "is_at_bot": True,
            "segments": [{"type": "image", "url": "https://example.com/a.png"}],
        }, trigger_reason="at_bot")
        assert first["action"] == "wait"

        state = runtime._states["group_g1"]
        assert state.wait_count == 1
        assert state.new_messages_during_wait == []
        state.last_gate_completed_ts = 0  # 绕过 MIN_INTERVAL 频控

        async def continue_gate(_gid, _p, ctx, _tr):
            assert ctx.get("wait_timeout") is True
            return {
                "action": "continue",
                "reason": "wait timed out, answer now",
                "parse_quality": "json",
                "model_confidence": 0.9,
            }

        monkeypatch.setattr(runtime, "_call_gate", continue_gate)
        result = await runtime.handle_timer_fired(
            "g1", generation=state.generation, trigger_reason="at_bot",
        )

        assert result["action"] == "continue"
        assert runtime._states["group_g1"].pending == []

    @pytest.mark.asyncio
    async def test_rules_only_timer_wait_timeout_direct_message_continues(
        self, monkeypatch,
    ):
        """rules_only 下 wait 超时重判不得再次 wait(死循环),直呼消息应 continue。"""
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda *_args: TimingModelPolicy("rules_only", "test")
        )

        async def forbidden_gate(_gid, _p, _ctx, _tr):
            raise AssertionError("rules_only 不应调用模型 gate")

        monkeypatch.setattr(runtime, "_call_gate", forbidden_gate)
        state = runtime._states.setdefault("group_g1", GateState(group_id="group_g1"))
        state.add_message(PendingMessage(
            "u1", "A", "@bot 等一下我发代码", is_at_bot=True,
        ))
        state.last_trigger_reason = "at_bot"
        state.start_wait(8, "wait marker")
        assert state.wait_count == 1
        state.last_gate_completed_ts = 0

        result = await runtime.handle_timer_fired(
            "g1", generation=state.generation, trigger_reason="at_bot",
        )

        assert result["action"] == "continue"
        assert state.pending == []
        assert state.waiting_for_more is False

    @pytest.mark.asyncio
    async def test_direct_at_bot_transport_conflict_reaches_gate(self, monkeypatch):
        """@bot + URL 是参与/抑制冲突，应进入 TimingGate 而不是被强制 continue。"""
        runtime = GroupRuntime()
        gate_calls = []

        async def fake_gate(group_id, pending, _ctx, trigger_reason):
            gate_calls.append((group_id, pending[0].message, trigger_reason))
            return {"action": "no_reply", "reason": "transport conflict"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        r = await runtime.process_message("g1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "@bot https://example.com/secret",
            "is_at_bot": True,
        }, trigger_reason="at_bot")

        assert r["action"] == "no_reply"
        assert gate_calls == [("group_g1", "@bot https://example.com/secret", "at_bot")]
        assert r["timing_scoring"]["stage"] == "model_assisted_conflict"

    @pytest.mark.asyncio
    async def test_other_bot_at_bot_uses_s_bot_scoring(self, monkeypatch):
        """其他 bot sender 即使 @bot，也应通过 s_bot soft reject 进入 scoring。"""
        runtime = GroupRuntime()
        gate_calls = []

        async def fake_gate(group_id, pending, _ctx, trigger_reason):
            gate_calls.append((group_id, pending[0].is_other_bot, trigger_reason))
            return {
                "action": "continue",
                "reason": "other bot explicitly asked",
                "parse_quality": "json",
                "model_confidence": 0.8,
            }

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        result = await runtime.process_message("g1", {
            "sender_id": "bot-a",
            "sender_name": "BotA",
            "message": "@bot 请确认",
            "is_at_bot": True,
            "is_other_bot": True,
        }, trigger_reason="at_bot")

        assert gate_calls == [("group_g1", True, "at_bot")]
        scoring = result["timing_scoring"]
        assert scoring["signals"]["sub_signals"]["s_bot"] == 0.70
        assert scoring["soft_reject_cap"] == 0.44
        assert scoring["stage"] == "model_assisted_conflict"
        assert scoring["model_used"] is True

    @pytest.mark.asyncio
    async def test_legacy_cooldown_scoring_reports_cooldown(self, monkeypatch):
        """legacy 空 trigger 的 cooldown scoring 返回也带正确 cooldown_ago。"""
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "continue", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        })
        runtime.note_bot_replied("g1")

        r = await runtime.process_message("g1", {
            "sender_id": "u2", "sender_name": "B", "message": "hi",
        })
        assert r["action"] == "no_reply"
        assert r["reason"].startswith("cooldown scoring shortcut:")
        assert r["cooldown_ago"] >= 0  # round 可能导致 0.001→0.0

    @pytest.mark.asyncio
    async def test_cooldown_blocks_ambient_after_bot_reply(self):
        """legacy 空 trigger 的 cooldown 应先走 scoring，而不是硬 wait。"""
        runtime = GroupRuntime()
        rt_calls = []

        async def fake_gate(*_a, **_kw):
            rt_calls.append(1)
            return {"action": "continue"}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        # bot 刚回复
        runtime._states["group_g1"] = GateState()
        runtime.note_bot_replied("g1")

        r = await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        })
        assert r["action"] == "no_reply"
        assert r["reason"].startswith("cooldown scoring shortcut:")
        assert r["timing_scoring"]["stage"] == "rule_shortcut"
        assert r["timing_scoring"]["action"] == "no_reply"
        assert len(rt_calls) == 0  # gate 未被调用

    @pytest.mark.asyncio
    async def test_cooldown_allows_reply_to_bot(self, monkeypatch):
        """bot 刚回复后，但用户回复 bot → 正常 gate。"""
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "continue", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)
        runtime._states["group_g1"] = GateState()
        runtime.note_bot_replied("g1")

        r = await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
            "is_reply_to_bot": True,
        })
        assert r["action"] == "continue"  # cooldown 不拦回复 bot

    @pytest.mark.asyncio
    async def test_cooldown_allows_mention(self, monkeypatch):
        """bot 刚回复后，但 trigger_reason 非空 → 正常 gate。"""
        runtime = GroupRuntime()

        async def fake_gate(_gid, _p, _ctx, _tr):
            return {"action": "continue", "reason": "test"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)
        runtime._states["group_g1"] = GateState()
        runtime.note_bot_replied("g1")

        r = await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        }, trigger_reason="bot_name_mentioned")
        assert r["action"] == "continue"  # cooldown 不拦 mention

    @pytest.mark.asyncio
    async def test_ambient_trigger_does_not_bypass_cooldown(self):
        """trigger_reason="ambient" 的 cooldown 应先走 scoring，而不是硬 wait。"""
        runtime = GroupRuntime()
        rt_calls = []

        async def fake_gate(*_a, **_kw):
            rt_calls.append(1)
            return {"action": "continue"}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        runtime._states["group_g1"] = GateState()
        runtime.note_bot_replied("g1")

        r = await runtime.process_message("g1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        }, trigger_reason="ambient")
        assert r["action"] == "no_reply"
        assert r["reason"].startswith("cooldown scoring shortcut:")
        assert r["timing_scoring"]["stage"] == "rule_shortcut"
        assert r["timing_scoring"]["action"] == "no_reply"
        assert len(rt_calls) == 0  # gate 未被调用

    @pytest.mark.asyncio
    async def test_timer_respects_cooldown(self):
        """timer 在 cooldown 未结束时先走 scoring，而不是硬 wait。"""
        runtime = GroupRuntime()
        rt_calls = []

        async def fake_gate(*_a, **_kw):
            rt_calls.append(1)
            return {"action": "continue"}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        runtime._states["group_g1"] = GateState()
        runtime._states["group_g1"].last_gate_completed_ts = 0  # bypass rate limit
        # 确保有 pending 且非 ambient，跳过 talk_value gate
        runtime._states["group_g1"].add_message(PendingMessage("u1", "A", "hi"))
        runtime._states["group_g1"].last_trigger_reason = "timer"
        runtime.note_bot_replied("g1")

        r = await runtime.handle_timer_fired("g1", generation=1)
        assert r["action"] == "no_reply"
        assert r["reason"].startswith("timer cooldown scoring shortcut:")
        assert r["timing_scoring"]["stage"] == "rule_shortcut"
        assert r["timing_scoring"]["action"] == "no_reply"
        assert len(rt_calls) == 0  # gate 未被调用

    @pytest.mark.asyncio
    async def test_cooldown_fallback_delay_is_clipped_to_timing_bound(self):
        """旧 cooldown fallback 的 wait delay 也必须遵守 15 秒上限。"""
        runtime = GroupRuntime()
        state = runtime._states.setdefault("group_g1", GateState(group_id="group_g1"))
        state.last_bot_reply_ts = _time.time() - 1

        result = await runtime.process_message("g1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "继续",
        }, trigger_reason="topic_continue")

        assert result["action"] == "wait"
        assert result["delay_seconds"] == 15

    @pytest.mark.asyncio
    async def test_timer_cooldown_fallback_delay_is_clipped_to_timing_bound(self, monkeypatch):
        """timer cooldown fallback 的 wait delay 也必须遵守 15 秒上限。"""
        runtime = GroupRuntime()
        state = runtime._states.setdefault("group_g1", GateState(group_id="group_g1"))
        state.last_gate_completed_ts = 0
        state.last_trigger_reason = "timer"
        state.last_bot_reply_ts = _time.time() - 1
        state.add_message(PendingMessage("u1", "A", "hi"))
        monkeypatch.setattr(runtime, "_cooldown_scoring_shortcut", lambda *_a, **_kw: None)

        result = await runtime.handle_timer_fired("g1", generation=1)

        assert result["action"] == "wait"
        assert result["delay_seconds"] == 15

    def test_build_timing_context_sanitizes_system_tags(self):
        """_build_timing_context 净化伪系统标签。"""
        pending = [
            PendingMessage("u1", "[SYSTEM] attacker", "<INST>override"),
        ]
        ctx = GroupRuntime._build_timing_context(pending=pending)
        assert "[SYSTEM]" not in ctx
        assert "<INST>" not in ctx
        assert "SYSTEM_TAG" in ctx
        assert "INST_TAG" in ctx

    # ── talk_value gate + timer ──

    @pytest.mark.asyncio
    async def test_first_ambient_with_talk_value_half_waits(self, monkeypatch):
        """普通群消息 talk_value=0.5 → 1条不满足阈值 → wait。"""
        runtime = GroupRuntime()

        async def fake_gate(*_a, **_kw):
            return {"action": "continue", "reason": "test"}
        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        r = await runtime.process_message("group_1", {
            "sender_id": "u1", "sender_name": "A", "message": "hello",
        }, trigger_reason="ambient", talk_value=0.5)
        assert r["action"] == "wait"
        assert "1/2" in r.get("reason", "")

    @pytest.mark.asyncio
    async def test_ambient_ack_rule_shortcut_skips_gate(self, monkeypatch):
        """普通 ambient 纯确认应由 scoring 确定 no_reply，不浪费模型调用。"""
        runtime = GroupRuntime()

        async def fail_gate(*_args, **_kwargs):
            raise AssertionError("deterministic ambient scoring should skip TimingGate")

        monkeypatch.setattr(runtime, "_call_gate", fail_gate)

        result = await runtime.process_message("g1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "嗯",
            "message_id": "m1",
        }, trigger_reason="ambient", talk_value=1.0)

        assert result["action"] == "no_reply"
        assert result["reason"].startswith("ambient scoring shortcut:")
        scoring = result["timing_scoring"]
        assert scoring["stage"] == "rule_shortcut"
        assert scoring["model_used"] is False
        assert scoring["signals"]["sub_signals"]["s_ack"] == 0.85

    @pytest.mark.asyncio
    async def test_timer_does_not_bypass_talk_value_gate(self):
        """timer 回调也检查 talk_value——ambient 一条不能绕过。"""
        rt = GroupRuntime()

        rt._states["group_1"] = GateState()
        rt._states["group_1"].talk_value = 0.5
        rt._states["group_1"].last_trigger_reason = "ambient"
        rt._states["group_1"].add_message(PendingMessage("u1", "A", "msg1"))

        r = await rt.handle_timer_fired("group_1", 1)
        assert r["action"] == "wait"
        assert "1/2" in r.get("reason", "")

    @pytest.mark.asyncio
    async def test_recent_bot_followup_bypasses_talk_value_gate(self, monkeypatch):
        """bot 刚参与过对话后，短追问不应被 ambient talk_value 门挡住。"""
        runtime = GroupRuntime()
        captured = {}

        state = runtime._states.setdefault("group_1", GateState(group_id="group_1"))
        state.note_bot_replied()

        async def fake_gate(group_id, pending, ctx, trigger_reason):
            captured["group_id"] = group_id
            captured["trigger_reason"] = trigger_reason
            captured["pending_trigger"] = pending[0].trigger_reason
            return {"action": "continue", "reason": "followup"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        r = await runtime.process_message("group_1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "再看看呢",
            "message_id": "m1",
        }, trigger_reason="ambient", talk_value=0.5)

        assert r["action"] == "wait"
        assert captured["trigger_reason"] == "recent_bot_followup"
        assert captured["pending_trigger"] == "recent_bot_followup"
        scoring = r["timing_scoring"]
        assert scoring["signals"]["linger_score"] > 0
        assert scoring["signals"]["direct_score"] >= scoring["signals"]["linger_score"]
        assert scoring["reason"].endswith("min_interval")

    @pytest.mark.asyncio
    async def test_active_linger_ambient_followup_uses_linger_without_keyword(self, monkeypatch):
        """已激活余韵的普通后续消息不依赖文本关键词才进入 scoring。"""
        import core.group_runtime.runtime as runtime_module

        now = 1000.0
        monkeypatch.setattr(runtime_module._time, "time", lambda: now)
        runtime = GroupRuntime()
        captured = {}
        state = runtime._states.setdefault("group_1", GateState(group_id="group_1"))
        state.activate_linger("u1", "at_bot")
        state.last_bot_reply_ts = now - 31

        async def fake_gate(group_id, pending, _ctx, trigger_reason):
            captured["group_id"] = group_id
            captured["trigger_reason"] = trigger_reason
            captured["pending_trigger"] = pending[0].trigger_reason
            return {"action": "continue", "reason": "active linger followup"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        result = await runtime.process_message(
            "group_1",
            {
                "sender_id": "u1",
                "sender_name": "A",
                "message": "然后",
                "message_id": "m1",
            },
            trigger_reason="ambient",
            talk_value=0.5,
        )

        assert result["action"] == "continue"
        assert captured["trigger_reason"] == "ambient"
        assert captured["pending_trigger"] == "ambient"
        scoring = result["timing_scoring"]
        assert scoring["signals"]["linger_score"] > 0
        assert scoring["signals"]["direct_score"] >= scoring["signals"]["linger_score"]
        assert scoring["stage"] == "model_assisted"

    @pytest.mark.asyncio
    async def test_model_failure_uses_rule_fallback_action_not_raw_no_reply(self, monkeypatch):
        """模型失败时，runtime 应采用 scoring rule_fallback 的最终动作。"""
        runtime = GroupRuntime()
        state = runtime._states.setdefault("group_1", GateState(group_id="group_1"))
        state.activate_linger("u1", "at_bot")

        async def failed_gate(*_args, **_kwargs):
            return {
                "action": "no_reply",
                "error_type": "network_error",
                "reason": "timeout",
                "raw": "",
            }

        monkeypatch.setattr(runtime, "_call_gate", failed_gate)

        result = await runtime.process_message("group_1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "继续看看呢",
            "message_id": "m1",
        }, trigger_reason="recent_bot_followup", talk_value=1.0)

        assert result["action"] == "continue"
        assert result["fallback_action"] == "continue"
        assert result["error_type"] == "network_error"
        assert result["source_message_ids"] == ["m1"]
        assert result["timing_scoring"]["stage"] == "rule_fallback"
        assert result["timing_scoring"]["action"] == "continue"
        assert result["timing_scoring"]["model_used"] is False
        assert result["reason"].startswith("rule_fallback after network_error:")

    @pytest.mark.asyncio
    async def test_normal_model_result_uses_scoring_blend_action(self, monkeypatch):
        """正常模型返回也应采用 scoring blend 后的最终动作。"""
        runtime = GroupRuntime()
        state = runtime._states.setdefault("group_1", GateState(group_id="group_1"))
        state.activate_linger("u1", "at_bot")
        state.last_bot_reply_ts = _time.time() - 20

        async def low_confidence_gate(*_args, **_kwargs):
            return {
                "action": "continue",
                "reason": "legacy parser says continue",
                "raw": "是,7",
                "parse_quality": "legacy",
                "model_confidence": 0.5,
            }

        monkeypatch.setattr(runtime, "_call_gate", low_confidence_gate)

        result = await runtime.process_message("group_1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "@B 继续看看",
            "message_id": "m1",
            "is_directed_to_other": True,
            "directed": {"at_others": True, "directed_to_other": True},
            "mentions": [{"user_id": "u2", "nickname": "B"}],
        }, trigger_reason="ambient", talk_value=1.0)

        assert result["action"] == "no_reply"
        assert result["reason"].startswith("scoring blend:")
        assert result["timing_scoring"]["stage"] == "model_assisted_conflict"
        assert result["timing_scoring"]["action"] == "no_reply"
        assert result["timing_scoring"]["model_used"] is True
        assert result["timing_scoring"]["model_action"] == "continue"
        assert result["timing_scoring"]["model_confidence"] == 0.5

    @pytest.mark.asyncio
    async def test_recent_followup_uses_decayed_linger_score(self, monkeypatch):
        """有显式召唤上下文时，followup 的 shadow scoring 使用余韵衰减分。"""
        import core.group_runtime.runtime as runtime_module

        now = 1000.0
        monkeypatch.setattr(runtime_module._time, "time", lambda: now)
        runtime = GroupRuntime()

        await runtime.process_message("group_1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "Nanobot 你看看",
            "message_id": "m0",
        }, trigger_reason="bot_name_mentioned")
        runtime.note_bot_replied("group_1")
        now += 90

        async def fake_gate(*_a, **_kw):
            return {"action": "continue", "reason": "followup"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        r = await runtime.process_message("group_1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "再看看呢",
            "message_id": "m1",
        }, trigger_reason="ambient", talk_value=0.5)

        assert r["action"] == "continue"
        signals = r["timing_scoring"]["signals"]
        linger_score = signals["linger_score"]
        assert 0 < linger_score < 0.70
        assert linger_score == pytest.approx(0.47, abs=0.02)
        assert signals["linger_active"] is True
        assert signals["linger_reply_count"] == 1
        assert signals["linger_time_remaining"] == pytest.approx(90.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_recent_followup_shadow_waits_during_linger_min_interval(self, monkeypatch):
        """余韵期刚回复后，shadow scoring 应建议等到最小间隔结束。"""
        import core.group_runtime.runtime as runtime_module

        now = 1000.0
        monkeypatch.setattr(runtime_module._time, "time", lambda: now)
        runtime = GroupRuntime()

        await runtime.process_message("group_1", {
            "sender_id": "u1",
            "sender_name": "A",
            "message": "Nanobot 你看看",
            "message_id": "m0",
        }, trigger_reason="bot_name_mentioned")
        runtime.note_bot_replied("group_1")
        now += 3

        state = runtime._states["group_1"]
        state.add_message(PendingMessage(
            "u1",
            "A",
            "继续呢",
            message_id="m1",
            trigger_reason="recent_bot_followup",
        ))

        scoring = runtime._shadow_scoring(
            state,
            "recent_bot_followup",
            model_result={"action": "continue", "reason": "followup"},
        )

        assert scoring["action"] == "wait"
        assert scoring["delay_seconds"] == 7
        assert "min_interval" in scoring["reason"]

    @pytest.mark.asyncio
    async def test_timing_model_policy_rules_only_skips_gate_for_fuzzy_linger(self, monkeypatch):
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda session_id, platform: TimingModelPolicy("rules_only", f"session:{session_id}")
        )

        async def fail_gate(*_args, **_kwargs):
            raise AssertionError("rules_only 不应调用 TimingGate 模型")

        monkeypatch.setattr(runtime, "_call_gate", fail_gate)
        state = runtime._states.setdefault("group_1", GateState(group_id="group_1"))
        state.activate_linger("u1", "at_bot")
        state.note_bot_replied()

        result = await runtime.process_message(
            "group_1",
            {
                "sender_id": "u1",
                "sender_name": "A",
                "message": "继续看看",
                "message_id": "m1",
            },
            trigger_reason="recent_bot_followup",
            talk_value=1.0,
        )

        assert result["timing_model_policy"]["mode"] == "rules_only"
        assert result["timing_model_policy"]["source"] == "session:group_1"
        assert result["timing_scoring"]["stage"] == "rule_fallback"
        assert result["timing_scoring"]["model_used"] is False

    @pytest.mark.asyncio
    async def test_timing_model_policy_shadow_calls_gate_but_uses_rule_decision(self, monkeypatch):
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda session_id, platform: TimingModelPolicy("shadow", f"platform:{platform}")
        )
        calls = []

        async def fake_gate(*_args, **_kwargs):
            calls.append(1)
            return {"action": "no_reply", "reason": "model shadow says no"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)
        state = runtime._states.setdefault("group_1", GateState(group_id="group_1"))
        state.activate_linger("u1", "at_bot")
        state.note_bot_replied()

        result = await runtime.process_message(
            "group_1",
            {
                "sender_id": "u1",
                "sender_name": "A",
                "message": "继续看看",
                "message_id": "m1",
            },
            trigger_reason="recent_bot_followup",
            talk_value=1.0,
            platform="web",
        )

        assert calls == [1]
        assert result["timing_model_policy"]["mode"] == "shadow"
        assert result["timing_model_policy"]["source"] == "platform:web"
        assert result["timing_scoring"]["model_used"] is False
        assert result["timing_model_shadow_scoring"]["model_action"] == "no_reply"

    @pytest.mark.asyncio
    async def test_timing_model_policy_rules_only_skips_timer_gate(self, monkeypatch):
        from core.timing_model_policy import TimingModelPolicy

        runtime = GroupRuntime()
        runtime.timing_model_policy_resolver = (
            lambda session_id, platform: TimingModelPolicy("rules_only", f"platform:{platform}")
        )

        async def fail_gate(*_args, **_kwargs):
            raise AssertionError("rules_only timer 不应调用 TimingGate 模型")

        monkeypatch.setattr(runtime, "_call_gate", fail_gate)
        state = runtime._states.setdefault("group_1", GateState(group_id="group_1"))
        state.platform = "web"
        state.last_gate_completed_ts = 0
        state.add_message(PendingMessage("u1", "A", "继续看看", trigger_reason="recent_bot_followup"))
        state.activate_linger("u1", "at_bot")
        state.last_trigger_reason = "recent_bot_followup"

        result = await runtime.handle_timer_fired(
            "group_1",
            state.generation,
            trigger_reason="recent_bot_followup",
        )

        assert result["timing_model_policy"]["mode"] == "rules_only"
        assert result["timing_model_policy"]["source"] == "platform:web"
        assert result["timing_scoring"]["stage"] == "rule_fallback"
        assert result["timing_scoring"]["model_used"] is False

    @pytest.mark.asyncio
    async def test_group_id_normalized_in_state_key(self, monkeypatch):
        """不同格式的 group_id 映射到同一个 runtime state。"""
        runtime = GroupRuntime()

        async def fail_gate(*_args, **_kwargs):
            raise AssertionError("group_id normalize 测试不应调用 TimingGate 模型")

        monkeypatch.setattr(runtime, "_call_gate", fail_gate)
        await runtime.process_message("123456", {
            "sender_id": "u1", "sender_name": "A", "message": "hi",
        }, trigger_reason="ambient")
        await runtime.process_message("group_123456", {
            "sender_id": "u2", "sender_name": "B", "message": "hello",
        }, trigger_reason="ambient")
        # 两个请求应对同一 state
        assert "group_123456" in runtime._states
        assert "123456" not in runtime._states
        assert len(runtime._states) == 1


# ═══════════════════════════════════════════
# Task 1C: directed features 测试
# ═══════════════════════════════════════════

class TestGroupPendingMessageDirected:
    """GroupPendingMessage 包含 directed fields"""

    def test_pending_message_has_directed_fields(self):
        from core.group_runtime.runtime import GroupPendingMessage

        msg = GroupPendingMessage(
            sender_id="111", sender_name="小明", message="@bot hello",
            is_at_bot=True, is_directed_to_other=False,
            segments=[{"type": "at", "data": {"qq": "999"}}],
            mentions=[{"user_id": "999", "is_bot": True}],
            directed={"at_bot": True, "directed_to_other": False},
            self_id="999", bot_id="999", bot_name="Nanobot",
        )
        d = msg.to_dict()
        assert d["is_at_bot"] is True
        assert d["directed"]["at_bot"] is True
        assert len(d["mentions"]) == 1

    def test_to_dict_compat_without_new_fields(self):
        from core.group_runtime.runtime import GroupPendingMessage

        msg = GroupPendingMessage(
            sender_id="111", sender_name="小明", message="hello",
        )
        d = msg.to_dict()
        assert d["segments"] == []
        assert d["mentions"] == []
        assert not d.get("directed", {}).get("directed_to_other")

    def test_directed_to_other_is_derived_from_direction_payload(self):
        from core.group_runtime.runtime import GroupPendingMessage

        msg = GroupPendingMessage(
            sender_id="111", sender_name="小明", message="@3605196653 你看",
            is_at_bot=False,
            is_reply_to_bot=False,
            directed={"at_others": True, "directed_to_other": True},
            mentions=[{"user_id": "3605196653", "nickname": "3605196653"}],
        )

        assert msg.is_directed_to_other is True


class TestShouldSuppressDirected:
    """should_suppress_directed_to_other hard rule"""

    def test_all_directed_to_other_suppresses(self):
        from core.group_runtime.runtime import (
            GroupPendingMessage, should_suppress_directed_to_other,
        )
        msgs = [
            GroupPendingMessage(
                sender_id="111", sender_name="A", message="@B",
                is_directed_to_other=True, is_at_bot=False, is_reply_to_bot=False,
                mentions=[{"user_id": "222"}],
                directed={"at_others": True, "directed_to_other": True},
            ),
            GroupPendingMessage(
                sender_id="222", sender_name="B", message="reply to A",
                is_directed_to_other=True, is_at_bot=False, is_reply_to_bot=False,
                reply_to={"sender_id": "111"},
                directed={"reply_to_others": True, "directed_to_other": True},
            ),
        ]
        assert should_suppress_directed_to_other(msgs) is True

    def test_one_at_bot_prevents_suppression(self):
        from core.group_runtime.runtime import (
            GroupPendingMessage, should_suppress_directed_to_other,
        )
        msgs = [
            GroupPendingMessage(
                sender_id="111", sender_name="A", message="@B",
                is_directed_to_other=True, is_at_bot=False, is_reply_to_bot=False,
                mentions=[{"user_id": "222"}],
                directed={"at_others": True, "directed_to_other": True},
            ),
            GroupPendingMessage(
                sender_id="333", sender_name="C", message="@bot help",
                is_directed_to_other=False, is_at_bot=True, is_reply_to_bot=False,
                trigger_reason="at_bot",
                mentions=[{"user_id": "999", "is_bot": True}],
                directed={"at_bot": True, "directed_to_other": False},
            ),
        ]
        assert should_suppress_directed_to_other(msgs) is False

    def test_empty_pending_no_suppress(self):
        from core.group_runtime.runtime import should_suppress_directed_to_other
        assert should_suppress_directed_to_other([]) is False

    def test_trigger_reason_at_bot_blocks_suppression(self):
        from core.group_runtime.runtime import (
            GroupPendingMessage, should_suppress_directed_to_other,
        )
        msgs = [
            GroupPendingMessage(
                sender_id="111", sender_name="A", message="@B",
                is_directed_to_other=True, is_at_bot=False, is_reply_to_bot=False,
                mentions=[{"user_id": "222"}],
                directed={"at_others": True, "directed_to_other": True},
            ),
            GroupPendingMessage(
                sender_id="222", sender_name="B", message="@bot 看看",
                is_directed_to_other=False, is_at_bot=True, is_reply_to_bot=False,
                trigger_reason="at_bot",
                mentions=[{"user_id": "999", "is_bot": True}],
                directed={"at_bot": True, "directed_to_other": False},
            ),
        ]
        assert should_suppress_directed_to_other(msgs) is False

    def test_bot_name_mentioned_blocks_suppression(self):
        from core.group_runtime.runtime import (
            GroupPendingMessage, should_suppress_directed_to_other,
        )
        msgs = [
            GroupPendingMessage(
                sender_id="111", sender_name="A", message="@B",
                is_directed_to_other=True, is_at_bot=False, is_reply_to_bot=False,
                mentions=[{"user_id": "222"}],
                directed={"at_others": True, "directed_to_other": True},
            ),
            GroupPendingMessage(
                sender_id="222", sender_name="B", message="Nanobot 你来看看",
                is_directed_to_other=False, is_at_bot=False, is_reply_to_bot=False,
                trigger_reason="bot_name_mentioned",
                directed={"at_bot": False, "directed_to_other": False},
            ),
        ]
        assert should_suppress_directed_to_other(msgs) is False


class TestProcessMessageDirected:
    """process_message() 集成——directed_to_other scoring shortcut"""

    @pytest.mark.asyncio
    async def test_process_message_directed_to_other_returns_no_reply(self):
        from core.group_runtime.runtime import GroupRuntime
        runtime = GroupRuntime()
        result = await runtime.process_message(
            "group_123",
            {
                "sender_id": "111", "sender_name": "A",
                "message": "@B 你看", "message_id": "m1",
                "is_directed_to_other": True,
                "directed": {"at_others": True, "directed_to_other": True},
                "mentions": [{"user_id": "222", "nickname": "B"}],
            },
            trigger_reason="ambient", talk_value=1.0,
        )
        assert result["action"] == "no_reply"
        assert result["reason"].startswith("directed_to_other scoring shortcut:")
        assert "hard_rule" not in result
        assert result["source_message_ids"] == ["m1"]
        assert result["pending_count"] == 1
        scoring = result["timing_scoring"]
        assert scoring["stage"] == "rule_shortcut"
        assert scoring["action"] == "no_reply"
        assert scoring["signals"]["sub_signals"]["s_other"] == 0.75

    @pytest.mark.asyncio
    async def test_process_message_directed_to_other_with_other_bot_thinking_bypasses_gate(self, monkeypatch):
        from core.group_runtime.runtime import GroupRuntime

        runtime = GroupRuntime()

        async def fail_gate(*_args, **_kwargs):
            raise AssertionError("directed_to_other scoring shortcut should bypass TimingGate")

        monkeypatch.setattr(runtime, "_call_gate", fail_gate)

        result = await runtime.process_message(
            "group_123",
            {
                "sender_id": "111",
                "sender_name": "奶蛙生活艺术家",
                "message": "@3605196653 我现在有20股minimax，请你结合行QQ，判断我要不要增持minimax的股还是接盘智谱",
                "message_id": "486560924",
                "directed": {"at_others": True, "directed_to_other": True},
                "mentions": [{"user_id": "3605196653", "nickname": "3605196653"}],
            },
            trigger_reason="ambient",
            recent_context=(
                "[msg_id]486560924\n"
                "[时间]21:01:48\n"
                "[用户名]奶蛙生活艺术家\n"
                "[发言内容]@3605196653 我现在有20股minimax，请你结合行QQ，判断我要不要增持minimax的股还是接盘智谱\n\n"
                "[msg_id]1167874690\n"
                "[时间]21:01:49\n"
                "[用户名][BOT]Alice\n"
                "[发言内容]我正在思考如何回复你 (Agent模式)..."
            ),
            talk_value=1.0,
        )

        assert result["action"] == "no_reply"
        assert "hard_rule" not in result
        assert result["reason"].startswith("directed_to_other scoring shortcut:")
        assert result["source_message_ids"] == ["486560924"]
        assert result["timing_scoring"]["signals"]["sub_signals"]["s_other"] == 0.75

    @pytest.mark.asyncio
    async def test_process_message_directed_to_other_with_linger_reaches_gate(self, monkeypatch):
        """directed_to_other + 余韵追问不应被 hard rule 裁死，应进入模型/评分路径。"""
        from core.group_runtime.runtime import GroupRuntime

        runtime = GroupRuntime()
        state = runtime._states.setdefault("group_123", GateState(group_id="group_123"))
        state.activate_linger("111", "at_bot")
        state.note_bot_replied()
        captured = {}

        async def fake_gate(group_id, pending, _ctx, trigger_reason):
            captured["group_id"] = group_id
            captured["trigger_reason"] = trigger_reason
            captured["pending_trigger"] = pending[0].trigger_reason
            return {"action": "continue", "reason": "linger followup"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        result = await runtime.process_message(
            "group_123",
            {
                "sender_id": "111",
                "sender_name": "A",
                "message": "@B 继续看看",
                "message_id": "m1",
                "is_directed_to_other": True,
                "directed": {"at_others": True, "directed_to_other": True},
                "mentions": [{"user_id": "222", "nickname": "B"}],
            },
            trigger_reason="ambient",
            talk_value=1.0,
        )

        assert result["action"] == "wait"
        assert "hard_rule" not in result
        assert captured["trigger_reason"] == "recent_bot_followup"
        assert captured["pending_trigger"] == "recent_bot_followup"
        assert result["timing_scoring"]["stage"] == "model_assisted_conflict"

    @pytest.mark.asyncio
    async def test_process_message_directed_to_other_with_active_linger_without_keyword_reaches_gate(self, monkeypatch):
        """active linger + 指向他人即使不是关键词追问，也不应落回 directed hard no_reply。"""
        from core.group_runtime.runtime import GroupRuntime

        runtime = GroupRuntime()
        state = runtime._states.setdefault("group_123", GateState(group_id="group_123"))
        state.activate_linger("111", "at_bot")
        state.last_bot_reply_ts = _time.time() - 31
        captured = {}

        async def fake_gate(group_id, pending, _ctx, trigger_reason):
            captured["group_id"] = group_id
            captured["trigger_reason"] = trigger_reason
            captured["pending_trigger"] = pending[0].trigger_reason
            return {"action": "continue", "reason": "active linger directed conflict"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        result = await runtime.process_message(
            "group_123",
            {
                "sender_id": "111",
                "sender_name": "A",
                "message": "@B 然后",
                "message_id": "m1",
                "is_directed_to_other": True,
                "directed": {"at_others": True, "directed_to_other": True},
                "mentions": [{"user_id": "222", "nickname": "B"}],
            },
            trigger_reason="ambient",
            talk_value=1.0,
        )

        assert result["action"] == "continue"
        assert "hard_rule" not in result
        assert result["reason"].startswith("scoring blend:")
        assert captured["trigger_reason"] == "ambient"
        assert captured["pending_trigger"] == "ambient"
        scoring = result["timing_scoring"]
        assert scoring["stage"] == "model_assisted_conflict"
        assert scoring["signals"]["linger_score"] > 0
        assert scoring["signals"]["sub_signals"]["s_other"] == 0.75

    @pytest.mark.asyncio
    async def test_process_message_at_bot_with_other_mention_reaches_model(self, monkeypatch):
        from core.group_runtime.runtime import GroupRuntime
        runtime = GroupRuntime()
        captured = {}

        async def fake_gate(group_id, pending, _ctx, trigger_reason):
            captured["group_id"] = group_id
            captured["trigger_reason"] = trigger_reason
            captured["pending_count"] = len(pending)
            return {"action": "continue", "reason": "同时点名 bot 和他人"}

        monkeypatch.setattr(runtime, "_call_gate", fake_gate)

        result = await runtime.process_message(
            "group_123",
            {
                "sender_id": "111", "sender_name": "A",
                "message": "@bot @B 你看", "message_id": "m1",
                "is_at_bot": True, "is_directed_to_other": False,
                "directed": {
                    "at_bot": True, "at_others": True,
                    "directed_to_other": False,
                },
            },
            trigger_reason="at_bot", talk_value=1.0,
        )
        assert result["action"] == "continue"
        assert captured["group_id"] == "group_123"
        assert captured["trigger_reason"] == "at_bot"
        assert captured["pending_count"] == 1
        assert result["timing_scoring"]["stage"] == "model_assisted_conflict"
        assert result["timing_scoring"]["signals"]["sub_signals"]["s_other"] == 0.75
