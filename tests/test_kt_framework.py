"""
Test suite for KT framework integration.

Tests the new adapter layer: BaseTool subclasses, BufferedOutput, NanobotBridge.
Uses mocks to avoid requiring real APIs or KT agent infrastructure.
"""

import asyncio
from types import SimpleNamespace
from tests.async_helpers import run_async
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.fixture(autouse=True)
def _isolate_model_failure_state(monkeypatch, tmp_path):
    import clients.model_registry as model_registry

    monkeypatch.setattr(
        model_registry,
        "_FAILURE_STATE_PATH",
        str(tmp_path / "model_failures.json"),
    )


def _tool_exchange(
    tool_name: str,
    output: str,
    *,
    call_id: str | None = None,
    as_objects: bool = False,
):
    call_id = call_id or f"call_{tool_name}"
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": "{}"},
            }],
        },
        {
            "role": "tool",
            "name": tool_name,
            "tool_call_id": call_id,
            "content": output,
        },
    ]
    if as_objects:
        return [SimpleNamespace(**message) for message in messages]
    return messages


def _rich_tool_exchange(
    tool_name: str,
    html: str,
    *,
    call_id: str | None = None,
    as_objects: bool = False,
):
    from nanobot_kt.reply_contract import build_rich_output

    return _tool_exchange(
        tool_name,
        build_rich_output(html, report_kind=tool_name),
        call_id=call_id,
        as_objects=as_objects,
    )


@pytest.fixture(autouse=True)
def _stub_session_guidance_resolver(monkeypatch):
    """框架单元测试不依赖数据库，统一模拟未配置会话指导。"""
    from core.session_guidance import SessionGuidanceResolution

    def resolve(_db, *, platform, chat_type, session_id):
        del session_id
        return SessionGuidanceResolution(
            chat_stream_id=f"{platform}:test:{chat_type}",
            text="",
            configured=False,
            chars=0,
            sha256="",
            updated_at=None,
            status="missing",
        )

    monkeypatch.setattr("nanobot_kt.bridge.resolve_session_guidance", resolve)


# ── BufferedOutput Tests ──

class TestBufferedOutput:
    """Test the BufferedOutput module that collects LLM response for programmatic access."""

    def test_write_and_get(self):
        from nanobot_kt.output import BufferedOutput
        output = BufferedOutput()
        run_async(output.write("hello"))
        run_async(output.write(" world"))
        assert output.get_response() == "hello world"

    def test_clear(self):
        from nanobot_kt.output import BufferedOutput
        output = BufferedOutput()
        run_async(output.write("test"))
        output.clear()
        assert output.get_response() == ""

    def test_processing_lifecycle(self):
        from nanobot_kt.output import BufferedOutput
        output = BufferedOutput()

        run_async(output.write("old"))
        assert output.get_response() == "old"

        # on_processing_start should clear buffer
        run_async(output.on_processing_start())
        assert output.get_response() == ""

        run_async(output.write_stream("new "))
        run_async(output.write_stream("data"))
        assert output.get_response() == "new data"

    def test_on_activity_does_not_crash(self):
        from nanobot_kt.output import BufferedOutput
        output = BufferedOutput()
        output.on_activity("tool_start", "sql_analysis")  # Should not raise

    def test_tool_error_streams_progress_without_polluting_final_response(self):
        from nanobot_kt.output import BufferedOutput

        async def _run():
            output = BufferedOutput()
            queue = asyncio.Queue()
            output.enable_stream(queue)
            output.on_activity("tool_error", "[schedule_task] ERROR: 推送失败")
            event = await queue.get()
            return output.get_response(), event

        response, event = run_async(_run())
        assert response == ""
        assert event["status"] == "progress"
        assert "工具失败" in event["text"]

# ── BaseTool Adapter Tests ──

class TestSQLAnalysisTool:
    """Test the SQLAnalysisTool BaseTool adapter."""

    def test_tool_metadata(self):
        from creatures.nanobot.prompts.skills.sql_analysis.tool import SQLAnalysisTool
        tool = SQLAnalysisTool()
        assert tool.tool_name == "sql_analysis"
        assert "SQL" in tool.description or "sql" in tool.description.lower()

    @patch("creatures.nanobot.prompts.skills.sql_analysis.tool.AnalysisSandbox")
    def test_execute_success(self, MockSandbox):
        from creatures.nanobot.prompts.skills.sql_analysis.tool import SQLAnalysisTool
        mock_instance = MockSandbox.return_value
        mock_instance.run_query.return_value = "id|count\n1|42"

        tool = SQLAnalysisTool()
        result = run_async(tool.execute({"sql": "SELECT count(id) AS count FROM chat_logs LIMIT 1"}))
        assert result.success
        assert "42" in result.output

    @patch("creatures.nanobot.prompts.skills.sql_analysis.tool.AnalysisSandbox")
    def test_execute_empty_sql(self, MockSandbox):
        from creatures.nanobot.prompts.skills.sql_analysis.tool import SQLAnalysisTool
        tool = SQLAnalysisTool()
        result = run_async(tool.execute({"sql": ""}))
        assert not result.success


class TestPythonSandboxTool:
    """Test the PythonSandboxTool BaseTool adapter."""

    def test_tool_metadata(self):
        from creatures.nanobot.prompts.skills.python_sandbox.tool import PythonSandboxTool
        tool = PythonSandboxTool()
        assert tool.tool_name == "python_sandbox"

    @patch("creatures.nanobot.prompts.skills.python_sandbox.tool.AnalysisSandbox")
    def test_execute_is_hard_disabled(self, MockSandbox):
        from creatures.nanobot.prompts.skills.python_sandbox.tool import PythonSandboxTool

        tool = PythonSandboxTool()
        result = run_async(tool.execute({"code": "print(42)"}))
        assert not result.success
        assert "disabled" in str(result.error).lower()
        MockSandbox.assert_not_called()


class TestAiDailyTool:
    """Test the AiDailyTool BaseTool adapter."""

    def test_tool_metadata(self):
        from creatures.nanobot.prompts.skills.news_search.tool import AiDailyTool
        tool = AiDailyTool()
        assert tool.tool_name == "ai_daily"

    @patch("creatures.nanobot.prompts.skills.news_search.tool._run_news_daily_pipeline")
    def test_execute_success(self, mock_daily):
        from creatures.nanobot.prompts.skills.news_search.tool import AiDailyTool
        mock_daily.return_value = "<article>AI news: GPT-5 released</article>"

        tool = AiDailyTool()
        result = run_async(tool.execute({"query": "AI news"}))
        assert result.success
        assert "GPT-5" in result.output


# ── NanobotBridge Tests ──

class TestNanobotBridge:
    """Test the NanobotBridge lifecycle manager."""

    @pytest.mark.parametrize(
        "process_outcome",
        ["empty", "system_error"],
        ids=["empty", "system-error"],
    )
    def test_single_candidate_terminal_failure_records_failure(self, process_outcome):
        """最后一个候选的空响应或系统错误也必须按失败记账。"""
        from types import SimpleNamespace

        from nanobot_kt.bridge import NanobotBridge

        bridge = NanobotBridge()
        bridge._agent = SimpleNamespace(controller=SimpleNamespace())
        bridge._extract_last_rich_tool_output = MagicMock(return_value=None)
        bridge._extract_reply_from_tool_output = MagicMock(return_value="")

        tracker = MagicMock(
            record_failure=AsyncMock(),
            record_success=AsyncMock(),
        )

        async def process_event(_agent, _event):
            if process_outcome == "system_error":
                raise RuntimeError("模型调用失败")
            return None

        result = run_async(bridge._run_model_loop(
            candidate_models=[{"id": "only-model"}],
            route_plan=SimpleNamespace(),
            event_content="你好",
            query="你好",
            session_id="session-1",
            meta={"stream": False},
            tracker=tracker,
            trace_id="trace-1",
            run_id="run-1",
            reply_llm_source="replyer.private_chat",
            create_user_event=lambda content, stream: (content, stream),
            process_event=process_event,
        ))

        if process_outcome == "empty":
            assert result.response == ""
        else:
            assert "[系统内部错误]" in result.response
        tracker.record_failure.assert_awaited_once_with("only-model")
        tracker.record_success.assert_not_awaited()

    @pytest.mark.parametrize(
        "terminal_kind",
        ["reply", "html", "no_reply"],
        ids=["reply", "html", "no-reply"],
    )
    def test_model_loop_legal_tool_terminal_records_success_without_fallback(
        self,
        terminal_kind,
    ):
        """合法 reply、富 HTML 和 no_reply 都应由当前模型成功终止。"""
        import json
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge

        bridge = NanobotBridge()
        messages = [SimpleNamespace(role="user", content="你好")]
        conversation = MagicMock()
        conversation.get_messages.side_effect = lambda: list(messages)
        conversation.find_last_user_index.return_value = 0
        conversation.truncate_from.side_effect = lambda index: messages.__delitem__(
            slice(index, None)
        )
        bridge._agent = SimpleNamespace(
            controller=SimpleNamespace(conversation=conversation),
        )

        if terminal_kind == "reply":
            tool_output = json.dumps(
                {REPLY_MARKER: {"content": "合法回复"}},
                ensure_ascii=False,
            )
        elif terminal_kind == "html":
            tool_output = '<article class="news-brief">合法富文本</article>'
        else:
            tool_output = json.dumps(
                {
                    REPLY_MARKER: {
                        "content": "",
                        "no_reply": True,
                        "reason": "无需回复",
                    }
                },
                ensure_ascii=False,
            )

        async def process_event(_agent, _event):
            if terminal_kind == "html":
                messages.extend(_rich_tool_exchange(
                    "ai_daily",
                    tool_output,
                    as_objects=True,
                ))
            else:
                tool_name = "reply" if terminal_kind == "reply" else "no_reply"
                messages.extend(_tool_exchange(
                    tool_name,
                    tool_output,
                    as_objects=True,
                ))

        process_event_mock = AsyncMock(side_effect=process_event)
        tracker = MagicMock(
            record_failure=AsyncMock(),
            record_success=AsyncMock(),
        )

        result = run_async(bridge._run_model_loop(
            candidate_models=[{"id": "model-a"}, {"id": "model-b"}],
            route_plan=SimpleNamespace(),
            event_content="你好",
            query="你好",
            session_id="session-terminal",
            meta={"stream": False},
            tracker=tracker,
            trace_id="trace-terminal",
            run_id="run-terminal",
            reply_llm_source="replyer.private_chat",
            create_user_event=lambda content, stream: SimpleNamespace(
                content=content,
                stream=stream,
            ),
            process_event=process_event_mock,
        ))

        assert result.target_model == "model-a"
        assert result.attempts == 1
        process_event_mock.assert_awaited_once()
        tracker.record_success.assert_awaited_once_with("model-a")
        tracker.record_failure.assert_not_awaited()
        if terminal_kind == "no_reply":
            assert bridge.is_no_reply_session("session-terminal") is True

    @pytest.mark.parametrize(
        ("raw_output", "expected_agent_result"),
        [
            ("普通文本但没有调用最终工具", "no_tool_call"),
            ("我已经调用 reply 工具发送了", "fake_tool_call_claim"),
        ],
        ids=["plain", "fake-tool-claim"],
    )
    def test_suppressed_contract_output_does_not_record_success_before_validation(
        self,
        raw_output,
        expected_agent_result,
    ):
        """最终被 contract 抑制的非空输出不能提前清除模型失败记录。"""
        from types import SimpleNamespace

        from nanobot_kt.bridge import NanobotBridge

        bridge = NanobotBridge()
        bridge._agent = SimpleNamespace(controller=SimpleNamespace())
        bridge._extract_last_rich_tool_output = MagicMock(return_value=None)
        bridge._extract_reply_from_tool_output = MagicMock(return_value="")
        bridge._record_reply_contract_check = MagicMock()
        tracker = MagicMock(
            record_failure=AsyncMock(),
            record_success=AsyncMock(),
        )

        async def process_event(_agent, _event):
            bridge._output._buffer.append(raw_output)

        def create_user_event(content, stream):
            return SimpleNamespace(content=content, stream=stream)
        model_loop = run_async(bridge._run_model_loop(
            candidate_models=[{"id": "only-model"}],
            route_plan=SimpleNamespace(),
            event_content="你好",
            query="你好",
            session_id="session-suppressed",
            meta={"stream": False},
            tracker=tracker,
            trace_id="trace-suppressed",
            run_id="run-suppressed",
            reply_llm_source="replyer.private_chat",
            create_user_event=create_user_event,
            process_event=process_event,
        ))

        resolution = run_async(bridge._check_reply_contract(
            session_id="session-suppressed",
            response=model_loop.response,
            result=model_loop.result,
            terminal_output=model_loop.terminal_output,
            target_model=model_loop.target_model,
            query="你好",
            meta={
                "stream": False,
                "enable_reply_contract_retry": False,
            },
            event_content="你好",
            create_user_event=create_user_event,
            process_event=process_event,
            trace_id="trace-suppressed",
            run_id="run-suppressed",
            reply_llm_source="replyer.private_chat",
        ))

        assert resolution.finish_status == "suppressed"
        assert resolution.agent_result == expected_agent_result
        tracker.record_success.assert_not_awaited()

    def test_bridge_trace_finalizer_finishes_once(self, monkeypatch):
        from nanobot_kt.bridge import BridgeTraceFinalizer, NanobotBridge

        bridge = NanobotBridge.__new__(NanobotBridge)
        restore_calls = []
        bridge._restore_saved_tools = lambda: restore_calls.append(True)
        finish_calls = []
        reset_trace_calls = []
        reset_final_tools_calls = []
        reset_tool_plan_calls = []

        monkeypatch.setattr(
            "core.tracing.RunTracer.finish_run",
            lambda *args, **kwargs: finish_calls.append((args, kwargs)),
        )
        monkeypatch.setattr(
            "core.tracing_context.reset_trace_context",
            lambda token: reset_trace_calls.append(token),
        )
        monkeypatch.setattr(
            "core.final_tools.reset_current_final_tools",
            lambda token: reset_final_tools_calls.append(token),
        )
        monkeypatch.setattr(
            "core.tool_plan.reset_current_tool_plan",
            lambda token: reset_tool_plan_calls.append(token),
        )

        finalizer = BridgeTraceFinalizer(
            bridge=bridge,
            run_id="run-1",
            trace_tokens="trace-token",
            run_meta={"message_id": "m1"},
            started_at=100.0,
            now=lambda: 100.2,
            final_tools_token="final-token",
            tool_plan_token="tool-token",
        )

        finalizer.finish(status="success", output_preview="ok", model="model-a")
        finalizer.finish(status="error", error="late")

        assert len(finish_calls) == 1
        assert restore_calls == [True]
        assert reset_trace_calls == ["trace-token"]
        assert reset_final_tools_calls == ["final-token"]
        assert reset_tool_plan_calls == ["tool-token"]

    @pytest.mark.parametrize("failing_step", [None, "restore", "finish_run"])
    def test_bridge_trace_finalizer_runs_all_cleanup_steps_best_effort(
        self,
        monkeypatch,
        failing_step,
    ):
        from nanobot_kt.bridge import BridgeTraceFinalizer, NanobotBridge

        events = []
        bridge = NanobotBridge.__new__(NanobotBridge)

        def restore_saved_tools():
            events.append("restore-tools")
            if failing_step == "restore":
                raise RuntimeError("restore failed")

        def finish_run(*_args, **_kwargs):
            events.append("finish-run")
            if failing_step == "finish_run":
                raise RuntimeError("finish failed")

        bridge._restore_saved_tools = restore_saved_tools
        monkeypatch.setattr("core.tracing.RunTracer.finish_run", finish_run)
        monkeypatch.setattr(
            "core.tool_plan.reset_current_tool_plan",
            lambda _token: events.append("reset-tool-plan"),
        )
        monkeypatch.setattr(
            "core.final_tools.reset_current_final_tools",
            lambda _token: events.append("reset-final-tools"),
        )
        monkeypatch.setattr(
            "core.tracing_context.reset_trace_context",
            lambda _token: events.append("reset-trace"),
        )

        finalizer = BridgeTraceFinalizer(
            bridge=bridge,
            run_id="run-best-effort",
            trace_tokens="trace-token",
            run_meta={},
            started_at=10.0,
            now=lambda: 10.1,
            final_tools_token="final-token",
            tool_plan_token="tool-token",
        )

        finalizer.finish("success", output_preview="ok")
        finalizer.finish("error", error="late")

        assert events == [
            "restore-tools",
            "finish-run",
            "reset-tool-plan",
            "reset-final-tools",
            "reset-trace",
        ]
        assert finalizer.closed is True
        assert finalizer.tool_plan_token is None
        assert finalizer.final_tools_token is None
        assert finalizer.trace_tokens is None

    def test_remove_system_contexts_cleans_effort_and_retry_prompts(self):
        from nanobot_kt.bridge import NanobotBridge

        class Msg:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        conv = MagicMock()
        conv._messages = [
            Msg("system", "base prompt"),
            Msg("system", "本轮简短处理。先给判断"),
            Msg("system", "本轮认真处理。可以使用工具"),
            Msg("system", "<reply_contract_retry>\nretry\n</reply_contract_retry>"),
            Msg("system", "<runtime_context>\nold\n</runtime_context>"),
            Msg(
                "system",
                "<session_guidance>\n旧会话指导\n</session_guidance>",
            ),
        ]

        bridge = NanobotBridge.__new__(NanobotBridge)
        bridge._remove_system_contexts(conv, NanobotBridge.DYNAMIC_SYSTEM_PREFIXES)

        assert [m.content for m in conv._messages] == ["base prompt"]

    def test_strip_kt_framework_prompt_sections_keeps_project_prompt_only(self):
        from nanobot_kt.bridge import _strip_kt_framework_prompt_sections

        prompt = (
            "## 交互定位\n\n项目提示\n\n"
            "## Available Functions\n\n- `reply`: 回复\n\n"
            "## Skills\n\n- `test`: skill\n\n"
            "## Tool Usage\n\nTools are called via API"
        )

        assert _strip_kt_framework_prompt_sections(prompt) == "## 交互定位\n\n项目提示"

    def test_get_bridge_singleton(self):
        # Reset module-level singleton between tests
        import nanobot_kt.bridge as bridge_mod
        bridge_mod._bridge = None
        b1 = bridge_mod.get_bridge()
        b2 = bridge_mod.get_bridge()
        assert b1 is b2
        bridge_mod._bridge = None  # cleanup

    def test_bridge_pool_allows_different_sessions_to_run_concurrently(self, monkeypatch):
        import nanobot_kt.bridge as bridge_mod

        active = 0
        max_active = 0
        gate = asyncio.Event()

        class FakeBridge:
            def __init__(self, _creature_path="creatures/nanobot"):
                pass

            async def start(self):
                pass

            async def stop(self):
                pass

            async def handle_message(self, query, *, session_id="", **_kwargs):
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                if active == 2:
                    gate.set()
                await gate.wait()
                await asyncio.sleep(0.01)
                active -= 1
                return session_id

        monkeypatch.setattr(bridge_mod, "NanobotBridge", FakeBridge)

        async def _run():
            pool = bridge_mod.NanobotBridgePool()
            await pool.start()
            results = await asyncio.gather(
                pool.handle_message("a", session_id="session-a"),
                pool.handle_message("b", session_id="session-b"),
            )
            await pool.stop()
            return results

        results = run_async(_run())
        assert sorted(results) == ["session-a", "session-b"]
        assert max_active == 2

    def test_bridge_pool_ttl_does_not_stop_inflight_bridge(self, monkeypatch):
        import time
        import nanobot_kt.bridge as bridge_mod

        entered = asyncio.Event()
        release = asyncio.Event()

        class FakeBridge:
            def __init__(self, _creature_path="creatures/nanobot"):
                self.stop_count = 0

            async def start(self):
                pass

            async def stop(self):
                self.stop_count += 1

            async def handle_message(self, query, *, session_id="", **_kwargs):
                if session_id == "busy":
                    entered.set()
                    await release.wait()
                return session_id

        monkeypatch.setattr(bridge_mod, "NanobotBridge", FakeBridge)

        async def _run():
            pool = bridge_mod.NanobotBridgePool()
            pool.BRIDGE_TTL_SECONDS = 0.01
            await pool.start()

            busy_task = asyncio.create_task(pool.handle_message("a", session_id="busy"))
            await entered.wait()
            busy_bridge = pool._bridges["busy"]
            pool._bridge_last_used["busy"] = time.time() - 10

            fresh_result = await pool.handle_message("b", session_id="fresh")
            await asyncio.sleep(0)

            assert fresh_result == "fresh"
            assert busy_bridge.stop_count == 0
            assert pool._bridges["busy"] is busy_bridge

            release.set()
            assert await busy_task == "busy"
            await pool.stop()

        run_async(_run())

    def test_bridge_pool_stop_waits_for_inflight_bridge(self, monkeypatch):
        import nanobot_kt.bridge as bridge_mod

        entered = asyncio.Event()
        release = asyncio.Event()
        stop_entered = asyncio.Event()

        class FakeBridge:
            def __init__(self, _creature_path="creatures/nanobot"):
                self.stop_count = 0

            async def start(self):
                pass

            async def stop(self):
                self.stop_count += 1
                stop_entered.set()

            async def handle_message(self, query, *, session_id="", **_kwargs):
                if session_id == "busy":
                    entered.set()
                    await release.wait()
                return session_id

        monkeypatch.setattr(bridge_mod, "NanobotBridge", FakeBridge)

        async def _run():
            pool = bridge_mod.NanobotBridgePool()
            await pool.start()

            busy_task = asyncio.create_task(pool.handle_message("a", session_id="busy"))
            await entered.wait()
            busy_bridge = pool._bridges["busy"]

            stop_task = asyncio.create_task(pool.stop())
            try:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_entered.wait(), timeout=0.05)
                assert busy_bridge.stop_count == 0

                release.set()
                assert await busy_task == "busy"
                await asyncio.wait_for(stop_task, timeout=1)
                assert busy_bridge.stop_count == 1
            finally:
                release.set()
                await asyncio.gather(busy_task, return_exceptions=True)
                await asyncio.gather(stop_task, return_exceptions=True)

        run_async(_run())

    def test_bridge_pool_stop_forces_after_inflight_timeout(self, monkeypatch, caplog):
        """E2: inflight 永久卡死时，stop 不应无限挂起；超时后强制 stop 全部 bridge。"""
        import logging
        import nanobot_kt.bridge as bridge_mod

        entered = asyncio.Event()
        stop_entered = asyncio.Event()

        class FakeBridge:
            def __init__(self, _creature_path="creatures/nanobot"):
                self.stop_count = 0

            async def start(self):
                pass

            async def stop(self):
                self.stop_count += 1
                stop_entered.set()

            async def handle_message(self, query, *, session_id="", **_kwargs):
                if session_id == "busy":
                    entered.set()
                    # 永不返回——模拟 in-flight 请求卡死
                    await asyncio.sleep(3600)
                return session_id

        monkeypatch.setattr(bridge_mod, "NanobotBridge", FakeBridge)
        caplog.set_level(logging.WARNING, logger="nanobot.bridge")

        async def _run():
            pool = bridge_mod.NanobotBridgePool()
            pool.BRIDGE_STOP_TIMEOUT_SECONDS = 0.1  # 收紧超时便于测试
            await pool.start()

            busy_task = asyncio.create_task(pool.handle_message("a", session_id="busy"))
            await entered.wait()
            busy_bridge = pool._bridges["busy"]

            stop_task = asyncio.create_task(pool.stop())
            try:
                # 超时后 stop 应强制完成（不永久挂起），并 stop 卡死的 bridge
                await asyncio.wait_for(stop_task, timeout=2)
                assert busy_bridge.stop_count == 1
                assert stop_entered.is_set()
                # 应记录超时 warning
                assert any("inflight" in r.message.lower() or "timeout" in r.message.lower()
                           for r in caplog.records)
            finally:
                busy_task.cancel()
                await asyncio.gather(busy_task, return_exceptions=True)
                await asyncio.gather(stop_task, return_exceptions=True)

        run_async(_run())

    def test_bridge_pool_tracks_stale_stop_task_until_finished(self, monkeypatch, caplog):
        import time
        import nanobot_kt.bridge as bridge_mod

        release_stop = asyncio.Event()
        entered_stop = asyncio.Event()

        class FakeBridge:
            def __init__(self, _creature_path="creatures/nanobot"):
                self.stop_count = 0

            async def start(self):
                pass

            async def stop(self):
                self.stop_count += 1
                entered_stop.set()
                await release_stop.wait()

            async def handle_message(self, query, *, session_id="", **_kwargs):
                return session_id

        monkeypatch.setattr(bridge_mod, "NanobotBridge", FakeBridge)

        async def _run():
            pool = bridge_mod.NanobotBridgePool()
            pool.BRIDGE_TTL_SECONDS = 0.01
            await pool.start()
            assert await pool.handle_message("a", session_id="stale") == "stale"
            stale_bridge = pool._bridges["stale"]
            pool._bridge_last_used["stale"] = time.time() - 10

            assert await pool.handle_message("b", session_id="fresh") == "fresh"
            await entered_stop.wait()
            assert stale_bridge.stop_count == 1
            assert len(pool._stop_tasks) == 1

            release_stop.set()
            for task in list(pool._stop_tasks):
                await task
            await asyncio.sleep(0)
            assert pool._stop_tasks == set()

            await pool.stop()

        with caplog.at_level("WARNING", logger="nanobot.kt"):
            run_async(_run())
        assert "Task exception was never retrieved" not in caplog.text

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_bridge_start(self, MockAgent, mock_load):
        """Test that bridge.start() creates a KT Agent."""
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test-agent"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = ["sql_analysis", "python_sandbox"]
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge("creatures/nanobot")
        run_async(bridge.start())

        mock_load.assert_called_once_with("creatures/nanobot")
        MockAgent.assert_called_once()
        assert bridge.agent is not None

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_returns_output(self, MockAgent, mock_load):
        """Test that handle_message() returns reply tool output."""
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge
        import json

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        reply_output = json.dumps({REPLY_MARKER: {"content": "Hello from KT!"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")))

        async def fake_process(event):
            pass

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "test query",
                user_id="u1",
                session_id="private_u1",
            )

        result = run_async(_run())
        assert result == "Hello from KT"

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_serializes_same_session(self, MockAgent, mock_load, monkeypatch):
        """同一个 session 的模型处理必须串行，避免共享 conversation 串扰。"""
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge
        import json

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        monkeypatch.setattr(
            "clients.classifier_client.resolve_model_route",
            lambda _key: {"base_url": "http://unit/v1", "api_key": "", "provider_id": "", "timeout": 1},
        )
        monkeypatch.setattr(
            "clients.classifier_client.ensure_model_route_enabled",
            lambda _key, route=None: route or {},
        )
        monkeypatch.setattr(
            "nanobot_kt.bridge.NewAPIClient.sync_models_to_registry",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "nanobot_kt.bridge.NewAPIClient.estimate_complexity",
            lambda *_args, **_kwargs: 1,
        )
        monkeypatch.setattr(
            "nanobot_kt.bridge.NewAPIClient.get_ordered_candidates",
            lambda *_args, **_kwargs: [{"id": "unit-model", "intelligence": 1, "context_window": 128000}],
        )

        reply_output = json.dumps({REPLY_MARKER: {"content": "ok"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.registry._tools = {}
        mock_agent.controller = MagicMock(
            conversation=mock_conv,
            llm=MagicMock(config=MagicMock(model="test-model")),
        )

        active = 0
        max_active = 0

        async def fake_process(_event):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.03)
            active -= 1

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        async def _run():
            bridge = NanobotBridge()
            await bridge.start()
            return await asyncio.gather(
                bridge.handle_message("a", user_id="u1", session_id="same-session", metadata={"complexity": 1}),
                bridge.handle_message("b", user_id="u1", session_id="same-session", metadata={"complexity": 1}),
            )

        assert run_async(_run()) == ["ok", "ok"]
        assert max_active == 1

    def test_handle_message_without_init(self):
        """Test that handle_message returns error if agent not started."""
        from nanobot_kt.bridge import NanobotBridge
        bridge = NanobotBridge()
        result = run_async(bridge.handle_message("test"))
        assert "not initialized" in result.lower() or "Error" in result

    @pytest.mark.asyncio
    async def test_prepare_event_payload_builds_multimodal_capabilities(self, monkeypatch):
        from kohakuterrarium.llm.message import ImagePart
        from nanobot_kt.bridge import NanobotBridge

        image = ImagePart(
            url="data:image/jpeg;base64,ZmFrZQ==",
            detail="low",
            source_type="qq",
            source_name="attachment_1",
        )

        def fake_prepare_image_parts(files, **kwargs):
            assert files == ["https://example.com/a.png"]
            assert kwargs == {
                "source_type": "qq",
                "source_name_prefix": "attachment",
                "detail": "low",
            }
            return [image]

        monkeypatch.setattr("nanobot_kt.bridge.prepare_image_parts", fake_prepare_image_parts)

        bridge = NanobotBridge.__new__(NanobotBridge)
        payload = await bridge._prepare_event_payload(
            prompt_event_content="看看图",
            files=["https://example.com/a.png"],
            tool_schemas=[{"name": "reply"}],
        )

        assert payload.image_parts == [image]
        assert payload.required_capabilities == {
            "supports_stream": True,
            "supports_image": True,
            "supports_tools": True,
        }
        assert payload.event_content != "看看图"

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_uses_multimodal_event_for_files(self, MockAgent, mock_load):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge
        from kohakuterrarium.llm.message import ImagePart
        import json

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        reply_output = json.dumps({REPLY_MARKER: {"content": "ok"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")))

        captured = {}
        to_thread_calls = []

        async def fake_process(event):
            captured["event"] = event

        async def fake_to_thread(func, *args, **kwargs):
            to_thread_calls.append((func, args, kwargs))
            return func(*args, **kwargs)

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent
        with patch(
            "nanobot_kt.bridge.prepare_image_parts",
            return_value=[
                ImagePart(
                    url="data:image/jpeg;base64,ZmFrZQ==",
                    detail="low",
                    source_type="qq",
                    source_name="attachment_1",
                )
            ],
        ) as mock_prepare, patch("nanobot_kt.bridge.asyncio.to_thread", new=fake_to_thread):
            bridge = NanobotBridge()

            async def _run():
                await bridge.start()
                return await bridge.handle_message(
                    "看看这张图",
                    user_id="u1",
                    session_id="private_u1",
                    metadata={"files": ["https://example.com/a.png", "https://example.com/b.png"]},
                )

            result = run_async(_run())

        assert result == "ok"
        mock_prepare.assert_called_once()
        assert to_thread_calls
        func, args, kwargs = to_thread_calls[0]
        assert func is mock_prepare
        assert args == (["https://example.com/a.png", "https://example.com/b.png"],)
        assert kwargs == {
            "source_type": "qq",
            "source_name_prefix": "attachment",
            "detail": "low",
        }

    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_with_files_requests_vision_candidates(
        self, MockAgent, mock_load, MockClient, mock_registry, monkeypatch
    ):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from kohakuterrarium.llm.message import ImagePart
        from nanobot_kt.bridge import NanobotBridge
        import json

        monkeypatch.setattr("core.settings_service.settings.get", lambda key, default=None: default)
        monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "", raising=False)
        mock_registry.get_models_by_provider.return_value = [{"id": "vision-model"}]

        captured = {}
        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3

        def fake_candidates(**kwargs):
            captured.update(kwargs)
            return [{
                "id": "vision-model",
                "supports_image": True,
                "supports_tools": True,
                "supports_stream": True,
                "intelligence": 7,
                "cost_input_1m": 0.0,
                "context_window": 128000,
            }]

        route_client.get_ordered_candidates.side_effect = fake_candidates
        MockClient.return_value = route_client
        MockClient.get_failure_tracker.return_value = MagicMock(
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        reply_output = json.dumps({REPLY_MARKER: {"content": "视觉回复"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(
            conversation=mock_conv,
            llm=MagicMock(config=MagicMock(model="old-model")),
        )
        mock_agent._process_event = AsyncMock(return_value=None)
        MockAgent.return_value = mock_agent

        with patch(
            "nanobot_kt.bridge.prepare_image_parts",
            return_value=[
                ImagePart(
                    url="data:image/jpeg;base64,ZmFrZQ==",
                    detail="low",
                    source_type="qq",
                    source_name="attachment_1",
                )
            ],
        ):
            bridge = NanobotBridge()

            async def _run():
                await bridge.start()
                return await bridge.handle_message(
                    "看看这张图",
                    user_id="u1",
                    session_id="private_u1",
                    metadata={
                        "complexity": 3,
                        "files": ["https://example.com/a.png"],
                    },
                )

            result = run_async(_run())

        assert result == "视觉回复"
        assert captured["required_capabilities"]["supports_image"] is True
        assert captured["required_capabilities"]["supports_stream"] is True

    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_with_files_degrades_to_text_without_vision_candidate(
        self, MockAgent, mock_load, MockClient, mock_registry, monkeypatch
    ):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from kohakuterrarium.llm.message import ImagePart
        from nanobot_kt.bridge import NanobotBridge
        import json

        monkeypatch.setattr("core.settings_service.settings.get", lambda key, default=None: default)
        monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "", raising=False)
        mock_registry.get_models_by_provider.return_value = [{"id": "text-model"}]

        route_calls = []
        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3

        def fake_candidates(**kwargs):
            route_calls.append(kwargs)
            required = kwargs.get("required_capabilities") or {}
            if required.get("supports_image"):
                return []
            return [{
                "id": "text-model",
                "supports_image": False,
                "supports_tools": True,
                "supports_stream": True,
                "intelligence": 7,
                "cost_input_1m": 0.0,
                "context_window": 128000,
            }]

        route_client.get_ordered_candidates.side_effect = fake_candidates
        MockClient.return_value = route_client
        MockClient.get_failure_tracker.return_value = MagicMock(
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        reply_output = json.dumps({REPLY_MARKER: {"content": "降级回复"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1

        captured_events = []

        async def fake_process(event):
            captured_events.append(event)

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(
            conversation=mock_conv,
            llm=MagicMock(config=MagicMock(model="old-model")),
        )
        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        with patch(
            "nanobot_kt.bridge.prepare_image_parts",
            return_value=[
                ImagePart(
                    url="data:image/jpeg;base64,ZmFrZQ==",
                    detail="low",
                    source_type="qq",
                    source_name="attachment_1",
                )
            ],
        ):
            bridge = NanobotBridge()

            async def _run():
                await bridge.start()
                return await bridge.handle_message(
                    "看看这张图",
                    user_id="u1",
                    session_id="private_u1",
                    metadata={
                        "complexity": 3,
                        "files": ["https://example.com/a.png"],
                    },
                )

            result = run_async(_run())

        assert result == "降级回复"
        assert route_calls[0]["required_capabilities"]["supports_image"] is True
        assert "supports_image" not in route_calls[1]["required_capabilities"]
        assert captured_events
        event_content = captured_events[-1].content
        assert isinstance(event_content, str)
        assert "图片内容未被读取" in event_content
        assert "image_url" not in event_content

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_passes_runtime_context_to_prompt_runtime(self, MockAgent, mock_load, monkeypatch):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge
        from nanobot_kt.prompt_runtime import PromptRuntimeResult
        import json

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        reply_output = json.dumps({REPLY_MARKER: {"content": "ok"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")))

        async def fake_process(event):
            pass

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient", lambda **_kwargs: route_client)
        monkeypatch.setattr(
            "core.tool_plan.build_tool_plan",
            lambda **_kwargs: MagicMock(
                enabled={},
                disabled={},
                executable_tool_names=set(),
                runtime_tool_prompt="",
                sha256="test-tool-plan",
            ),
        )
        monkeypatch.setattr("core.runtime_tool_service.record_runtime_tool_decision", lambda **_kwargs: False)

        captured = {}

        async def fake_build_prompt_runtime(prompt_input):
            captured["prompt_input"] = prompt_input
            return PromptRuntimeResult(
                prompt_key=prompt_input.prompt_key,
                prompt_mode=prompt_input.prompt_mode,
                prompt_source="test",
                prompt_runtime_path="",
                prompt_default_path="",
                prompt_sha256="test-sha",
                pre_event_messages=[{"role": "system", "content": "base"}],
                event_content=prompt_input.user_input,
                meta_update={"prompt_engine": "prompt"},
            )

        monkeypatch.setattr("nanobot_kt.prompt_runtime.build_prompt_runtime", fake_build_prompt_runtime)

        with patch("nanobot_kt.bridge._current_time_label", return_value="2026-05-01 09:30:00 CST"):
            bridge = NanobotBridge()

            async def _run():
                await bridge.start()
                return await bridge.handle_message(
                    "test query",
                    user_id="u1",
                    session_id="private-u1",
                    sender_name="雀",
                    metadata={
                        "prompt_runtime_engine_override": "v1",
                        "user_id": "u1",
                        "message_id": "msg-1",
                    },
                )

            result = run_async(_run())

        assert result == "ok"
        prompt_input = captured["prompt_input"]
        assert prompt_input.prompt_engine == "prompt"
        assert prompt_input.prompt_mode == "prompt"
        assert prompt_input.prompt_key == "chat_private"
        assert prompt_input.chat_type == "private"
        assert prompt_input.runtime_chat_type == "private"
        assert prompt_input.session_id == "private-u1"
        assert prompt_input.user_id == "u1"
        assert prompt_input.sender_name == "雀"
        assert prompt_input.current_message_id == "msg-1"
        assert prompt_input.user_input == "test query"
        system_messages = [call.args[1] for call in mock_conv.append.call_args_list if call.args[0] == "system"]
        assert system_messages == ["base"]

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_passes_platform_to_tool_plan_and_decision(self, MockAgent, mock_load, monkeypatch):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from core.tool_plan import ToolPlan
        from nanobot_kt.bridge import NanobotBridge
        from nanobot_kt.prompt_runtime import PromptRuntimeResult
        import json

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        reply_output = json.dumps({REPLY_MARKER: {"content": "ok"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")))

        async def fake_process(event):
            pass

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient", lambda **_kwargs: route_client)

        captured: dict[str, str] = {}

        def fake_build_tool_plan(**kwargs):
            captured["tool_plan_platform"] = kwargs.get("platform")
            return ToolPlan.from_effective_tools(
                enabled={"reply": True, "no_reply": True},
                disabled={},
                chat_type=kwargs.get("chat_type", "private"),
                tool_schemas=[],
            )

        def fake_record_runtime_tool_decision(**kwargs):
            captured["decision_platform"] = kwargs.get("platform")
            return False

        monkeypatch.setattr("core.tool_plan.build_tool_plan", fake_build_tool_plan)
        monkeypatch.setattr("core.runtime_tool_service.record_runtime_tool_decision", fake_record_runtime_tool_decision)

        async def fake_build_prompt_runtime(prompt_input):
            captured["prompt_platform"] = prompt_input.platform
            return PromptRuntimeResult(
                prompt_key=prompt_input.prompt_key,
                prompt_mode=prompt_input.prompt_mode,
                prompt_source="test",
                prompt_runtime_path="",
                prompt_default_path="",
                prompt_sha256="test-sha",
                pre_event_messages=[{"role": "system", "content": "base"}],
                event_content=prompt_input.user_input,
                meta_update={"prompt_engine": "prompt"},
            )

        monkeypatch.setattr("nanobot_kt.prompt_runtime.build_prompt_runtime", fake_build_prompt_runtime)

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "test query",
                user_id="u1",
                session_id="private-u1",
                metadata={"platform": "web", "user_id": "u1", "message_id": "msg-platform"},
            )

        result = run_async(_run())

        assert result == "ok"
        assert captured["tool_plan_platform"] == "web"
        assert captured["decision_platform"] == "web"
        assert captured["prompt_platform"] == "web"

    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_uses_reply_model_intel_floor(
        self, MockAgent, mock_load, MockClient, mock_registry, monkeypatch
    ):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge
        import json

        monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "", raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_INTEL_FLOOR", 12, raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_INTEL_BOOST", 2, raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_MAX_COST", 10.0, raising=False)
        # settings.get("model.reply") 优先于 LLM_MODEL_REPLY，mock 为空以触发 auto-routing
        monkeypatch.setattr("core.settings_service.settings.get", lambda key, default=None: default)

        captured = {}

        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3

        def fake_candidates(**kwargs):
            captured.update(kwargs)
            return [{"id": "smart-reply", "intelligence": 12, "cost_input_1m": 0.4, "context_window": 128000}]

        route_client.get_ordered_candidates.side_effect = fake_candidates
        MockClient.return_value = route_client
        MockClient.get_failure_tracker.return_value = MagicMock(
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )
        mock_registry.get_models_by_provider.return_value = [{"id": "smart-reply"}]

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        reply_output = json.dumps({REPLY_MARKER: {"content": "高智回复"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=MagicMock(config=MagicMock(model="old-model")))
        mock_agent._process_event = AsyncMock(return_value=None)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message("你好", user_id="u1", session_id="private_u1", metadata={"complexity": 3})

        result = run_async(_run())
        assert result == "高智回复"
        assert captured["intel_floor"] == 12
        assert captured["max_cost"] == 10.0

    @pytest.mark.parametrize(
        ("configured_model", "preferred_known", "expected_attempts_by_request"),
        [
            (
                "override-model",
                True,
                [["override-model", "auto-model"], ["auto-model"]],
            ),
            ("unknown-model", False, [["auto-model"]]),
        ],
        ids=["circuit-disabled-on-next-request", "unknown-preferred"],
    )
    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_bridge_routes_only_eligible_preferred_with_real_failure_tracker(
        self,
        MockAgent,
        mock_load,
        MockClient,
        mock_registry,
        monkeypatch,
        configured_model,
        preferred_known,
        expected_attempts_by_request,
    ):
        """Bridge 仅尝试合格首选，并通过真实熔断状态过滤后续候选。"""
        from types import SimpleNamespace

        from clients.model_registry import ModelFailureTracker
        from clients.new_api_client import NewAPIClient as RealNewAPIClient
        from nanobot_kt.bridge import NanobotBridge, ReplyResolution

        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: (
                configured_model if key == "model.reply" else default
            ),
        )
        monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "env-model", raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_INTEL_FLOOR", 12, raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_INTEL_BOOST", 2, raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_MAX_COST", 10.0, raising=False)
        monkeypatch.setattr(ModelFailureTracker, "_load", lambda self: None)
        monkeypatch.setattr(ModelFailureTracker, "_save", lambda self: None)

        preferred_model = {
            "id": "override-model", "enabled": True, "provider": "new-api",
            "intelligence": 12, "cost_input_1m": 0.4, "tier": "smart",
            "supports_stream": True,
        }
        auto_model = {
            "id": "auto-model", "enabled": True, "provider": "new-api",
            "intelligence": 12, "cost_input_1m": 0.5, "tier": "smart",
            "supports_stream": True,
        }
        models = [auto_model]
        if preferred_known:
            models.insert(0, preferred_model)
        mock_registry.get_model_info.side_effect = lambda model_id: (
            preferred_model
            if preferred_known and model_id == configured_model
            else None
        )
        mock_registry.get_models_by_provider.return_value = models
        mock_registry.compute_priority_score.side_effect = (
            lambda model: model["cost_input_1m"]
        )
        monkeypatch.setattr("clients.new_api_client.registry", mock_registry)

        failure_tracker = ModelFailureTracker(max_failures=1)
        route_client = RealNewAPIClient(
            api_key="test",
            base_url="http://test",
            registry_provider="new-api",
        )
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity = MagicMock(return_value=3)
        monkeypatch.setattr(
            route_client,
            "_safe_get_failure_tracker",
            lambda: failure_tracker,
        )
        real_get_ordered_candidates = route_client.get_ordered_candidates
        routed_candidates_by_request = []

        def get_ordered_candidates(**kwargs):
            candidates = real_get_ordered_candidates(**kwargs)
            routed_candidates_by_request.append(
                [candidate["id"] for candidate in candidates]
            )
            return candidates

        route_client.get_ordered_candidates = MagicMock(
            side_effect=get_ordered_candidates,
        )
        MockClient.return_value = route_client
        MockClient.get_failure_tracker.return_value = failure_tracker

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        user_msg = SimpleNamespace(role="user", content="你好")
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = [user_msg]
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = 0

        attempts_by_request = []
        llm = MagicMock(config=MagicMock(model="old-model"))

        async def fake_process(_event):
            attempts_by_request[-1].append(llm.config.model)
            if llm.config.model == "auto-model":
                bridge._output._buffer.append("自动降级回复")
            return None

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(
            conversation=mock_conv,
            llm=llm,
        )
        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()
        bridge._check_reply_contract = AsyncMock(return_value=ReplyResolution(
            response="自动降级回复",
            agent_result="reply",
            no_reply=False,
            no_tool_call=False,
            output_preview="自动降级回复",
            finish_status="success",
        ))

        async def _run_requests():
            await bridge.start()
            results = []
            for request_index in range(len(expected_attempts_by_request)):
                attempts_by_request.append([])
                results.append(await bridge.handle_message(
                    "你好",
                    user_id="u1",
                    session_id=f"private_u1_{request_index}",
                    metadata={"complexity": 3},
                ))
            return results

        results = run_async(_run_requests())

        assert results == ["自动降级回复"] * len(expected_attempts_by_request)
        assert attempts_by_request == expected_attempts_by_request
        assert routed_candidates_by_request == expected_attempts_by_request
        assert failure_tracker.sync_is_disabled(configured_model) is preferred_known
        assert failure_tracker.sync_is_disabled("auto-model") is False

    @pytest.mark.parametrize(
        (
            "configured_model",
            "model_info",
            "controller_model",
            "circuit_disabled",
        ),
        [
            ("unknown-model", None, "unknown-model", False),
            (
                "disabled-model",
                {
                    "id": "disabled-model",
                    "enabled": False,
                    "supports_stream": True,
                    "supports_tools": True,
                },
                "disabled-model",
                False,
            ),
            (
                "incapable-model",
                {
                    "id": "incapable-model",
                    "enabled": True,
                    "supports_stream": False,
                    "supports_tools": True,
                },
                "incapable-model",
                False,
            ),
            (
                "circuit-model",
                {
                    "id": "circuit-model",
                    "enabled": True,
                    "supports_stream": True,
                    "supports_tools": True,
                },
                "circuit-model",
                True,
            ),
            ("", None, "", False),
            ("", None, "unknown-fallback", False),
        ],
        ids=[
            "unknown-preferred",
            "disabled-preferred",
            "incapable-preferred",
            "circuit-disabled-preferred",
            "hardcoded-fallback",
            "unknown-controller-fallback",
        ],
    )
    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_bridge_does_not_attempt_ineligible_preferred_or_final_fallback(
        self,
        MockAgent,
        mock_load,
        MockClient,
        mock_registry,
        monkeypatch,
        configured_model,
        model_info,
        controller_model,
        circuit_disabled,
    ):
        """不合格首选和无健康候选时的临时 fallback 都不得发起模型调用。"""
        from nanobot_kt.bridge import NanobotBridge, ReplyResolution

        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: (
                configured_model if key == "model.reply" else default
            ),
        )
        monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "", raising=False)

        def get_model_info(model_id):
            if configured_model and model_id == configured_model:
                return model_info
            return None

        mock_registry.get_model_info.side_effect = get_model_info
        mock_registry.get_models_by_provider.return_value = [{"id": "registry-seed"}]

        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3
        route_client.get_ordered_candidates.return_value = []
        MockClient.return_value = route_client

        tracker = MagicMock(
            sync_is_disabled=MagicMock(
                side_effect=lambda model_id: (
                    circuit_disabled and model_id == configured_model
                )
            ),
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )
        MockClient.get_failure_tracker.return_value = tracker

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        conversation = MagicMock()
        conversation._messages = []
        conversation.get_messages.return_value = []
        conversation.to_messages.return_value = []
        conversation.find_last_user_index.return_value = -1
        llm = MagicMock(config=MagicMock(model=controller_model))
        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(conversation=conversation, llm=llm)
        mock_agent._process_event = AsyncMock(return_value=None)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()
        bridge._check_reply_contract = AsyncMock(return_value=ReplyResolution(
            response="",
            agent_result="suppressed",
            no_reply=False,
            no_tool_call=True,
            output_preview="",
            finish_status="suppressed",
        ))

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好",
                user_id="u1",
                session_id="private_u1",
                metadata={
                    "complexity": 3,
                    "enable_reply_contract_retry": False,
                },
            )

        result = run_async(_run())

        assert result == ""
        mock_agent._process_event.assert_not_awaited()
        tracker.record_success.assert_not_awaited()

    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_reply_model_disabled_falls_back_to_auto(
        self, MockAgent, mock_load, MockClient, mock_registry, monkeypatch
    ):
        """settings 返回 disabled 模型 → 回退到自动路由"""
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge
        import json

        # settings 返回 disabled-model，registry 说 enabled=False
        fake_settings = {"model.reply": "disabled-model"}
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: fake_settings.get(key, default),
        )
        monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "env-model", raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_INTEL_FLOOR", 12, raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_INTEL_BOOST", 2, raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_MAX_COST", 10.0, raising=False)

        mock_registry.get_model_info = MagicMock(return_value={
            "id": "disabled-model", "enabled": False, "provider": "new-api",
            "intelligence": 12, "cost_input_1m": 0.4, "tier": "smart",
        })
        mock_registry.get_models_by_provider.return_value = [{"id": "auto-model"}]

        auto_kwargs = {}

        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3

        def fake_candidates(**kwargs):
            auto_kwargs.update(kwargs)
            return [{"id": "auto-model", "intelligence": 12, "cost_input_1m": 0.5, "context_window": 128000}]

        route_client.get_ordered_candidates.side_effect = fake_candidates
        MockClient.return_value = route_client
        MockClient.get_failure_tracker.return_value = MagicMock(
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        reply_output = json.dumps({REPLY_MARKER: {"content": "自动回退"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(
            conversation=mock_conv,
            llm=MagicMock(config=MagicMock(model="old-model")),
        )
        mock_agent._process_event = AsyncMock(return_value=None)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好", user_id="u1", session_id="private_u1",
                metadata={"complexity": 3},
            )

        result = run_async(_run())
        assert result == "自动回退"
        # disabled 模型应触发自动路由
        assert auto_kwargs.get("intel_floor") == 12, f"Expected auto-routing, got kwargs={auto_kwargs}"

    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_reply_model_lacking_required_capability_falls_back_to_auto(
        self, MockAgent, mock_load, MockClient, mock_registry, monkeypatch
    ):
        """settings 返回不支持流式的模型 → 回退到自动路由。"""
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge
        import json

        fake_settings = {"model.reply": "no-stream-model"}
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: fake_settings.get(key, default),
        )
        monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "env-model", raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_INTEL_FLOOR", 12, raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_INTEL_BOOST", 2, raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_MAX_COST", 10.0, raising=False)

        mock_registry.get_model_info = MagicMock(return_value={
            "id": "no-stream-model",
            "enabled": True,
            "provider": "new-api",
            "intelligence": 12,
            "cost_input_1m": 0.4,
            "tier": "smart",
            "supports_stream": False,
            "supports_tools": True,
            "supports_image": False,
        })
        mock_registry.get_models_by_provider.return_value = [{"id": "auto-model"}]

        auto_kwargs = {}

        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3

        def fake_candidates(**kwargs):
            auto_kwargs.update(kwargs)
            return [{
                "id": "auto-model",
                "intelligence": 12,
                "cost_input_1m": 0.5,
                "context_window": 128000,
                "supports_stream": True,
            }]

        route_client.get_ordered_candidates.side_effect = fake_candidates
        MockClient.return_value = route_client
        MockClient.get_failure_tracker.return_value = MagicMock(
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        reply_output = json.dumps({REPLY_MARKER: {"content": "能力回退"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1

        llm = MagicMock(config=MagicMock(model="old-model"))
        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=llm)
        mock_agent._process_event = AsyncMock(return_value=None)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好",
                user_id="u1",
                session_id="private_u1",
                metadata={"complexity": 3},
            )

        result = run_async(_run())

        assert result == "能力回退"
        assert auto_kwargs["required_capabilities"]["supports_stream"] is True
        assert llm.config.model == "auto-model"

    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_reply_route_uses_route_provider_for_registry_candidates(
        self, MockAgent, mock_load, MockClient, mock_registry, monkeypatch
    ):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge
        import json

        values = {
            "model.reply": "",
            "model.route.reply.provider": "openrouter",
            "model.providers.openrouter.base_url": "http://openrouter.test/v1",
            "model.providers.openrouter.api_key": "route-key",
            "model.providers.openrouter.enabled": True,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )
        monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "", raising=False)

        client_kwargs = {}
        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3
        route_client.get_ordered_candidates.return_value = [
            {"id": "openrouter-model", "intelligence": 12, "cost_input_1m": 0.5, "context_window": 128000},
        ]

        def fake_client(*args, **kwargs):
            client_kwargs.update(kwargs)
            return route_client

        MockClient.side_effect = fake_client
        MockClient.get_failure_tracker.return_value = MagicMock(
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )
        mock_registry.get_models_by_provider.return_value = [{"id": "openrouter-model"}]

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        reply_output = json.dumps({REPLY_MARKER: {"content": "openrouter 回复"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1

        llm = MagicMock(config=MagicMock(model="old-model"))
        llm.base_url = "http://old-provider.test/v1"
        llm._api_key = "old-key"
        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=llm)
        mock_agent._process_event = AsyncMock(return_value=None)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message("你好", user_id="u1", session_id="private_u1", metadata={"complexity": 3})

        result = run_async(_run())

        assert result == "openrouter 回复"
        assert client_kwargs["registry_provider"] == "openrouter"
        mock_registry.get_models_by_provider.assert_called_with("openrouter")
        assert route_client.get_ordered_candidates.call_args.kwargs["provider"] == "openrouter"
        assert llm.provider_name == "openrouter"

    @patch("nanobot_kt.bridge.AsyncOpenAI")
    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_reply_route_rebuilds_controller_client_when_api_key_changes(
        self, MockAgent, mock_load, MockClient, mock_registry, MockAsyncOpenAI, monkeypatch
    ):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge
        import json

        values = {
            "model.reply": "manual-model",
            "model.route.reply.provider": "newapi",
            "model.providers.newapi.base_url": "http://same-provider.test/v1",
            "model.providers.newapi.api_key": "new-key",
            "model.providers.newapi.enabled": True,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )
        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3
        MockClient.return_value = route_client
        MockClient.get_failure_tracker.return_value = MagicMock(
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )
        mock_registry.get_model_info.return_value = {"id": "manual-model", "enabled": True}
        mock_registry.get_models_by_provider.return_value = [{"id": "manual-model"}]

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        reply_output = json.dumps({REPLY_MARKER: {"content": "换 key 回复"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1

        llm = MagicMock(config=MagicMock(model="old-model"))
        llm.base_url = "http://same-provider.test/v1"
        llm._api_key = "old-key"
        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=llm)
        mock_agent._process_event = AsyncMock(return_value=None)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message("你好", user_id="u1", session_id="private_u1", metadata={"complexity": 3})

        result = run_async(_run())

        assert result == "换 key 回复"
        assert llm._api_key == "new-key"
        MockAsyncOpenAI.assert_called_once()
        assert MockAsyncOpenAI.call_args.kwargs["api_key"] == "new-key"

    @patch("nanobot_kt.bridge.AsyncOpenAI")
    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_reply_route_syncs_controller_model_params(
        self, MockAgent, mock_load, MockClient, mock_registry, MockAsyncOpenAI, monkeypatch
    ):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge
        import json

        values = {
            "model.reply": "manual-model",
            "model.route.reply.provider": "newapi",
            "model.route.reply.timeout": 88,
            "model.route.reply.temperature": 0.2,
            "model.route.reply.max_tokens": 1234,
            "model.providers.newapi.base_url": "http://same-provider.test/v1",
            "model.providers.newapi.api_key": "same-key",
            "model.providers.newapi.enabled": True,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )
        install_calls = []
        monkeypatch.setattr(
            "nanobot_kt.bridge.install_openai_chat_completion_tracer",
            lambda llm_arg, **kwargs: install_calls.append((llm_arg, kwargs)) or True,
        )

        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3
        MockClient.return_value = route_client
        MockClient.get_failure_tracker.return_value = MagicMock(
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )
        mock_registry.get_model_info.return_value = {"id": "manual-model", "enabled": True}
        mock_registry.get_models_by_provider.return_value = [{"id": "manual-model"}]

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        reply_output = json.dumps({REPLY_MARKER: {"content": "参数同步回复"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = _tool_exchange("reply", reply_output)
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1

        llm = MagicMock(config=MagicMock(model="old-model", temperature=0.7, max_tokens=None))
        llm.base_url = "http://same-provider.test/v1"
        llm._api_key = "same-key"
        llm._timeout = 120.0
        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=llm)
        mock_agent._process_event = AsyncMock(return_value=None)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message("你好", user_id="u1", session_id="private_u1", metadata={"complexity": 3})

        result = run_async(_run())

        assert result == "参数同步回复"
        assert llm.config.temperature == 0.2
        assert llm.config.max_tokens == 1234
        assert llm._timeout == 88
        assert llm.provider_name == "newapi"
        assert install_calls
        assert install_calls[0][0] is llm
        assert install_calls[0][1]["provider"] == "newapi"
        assert install_calls[0][1]["base_url"] == "http://same-provider.test/v1"
        MockAsyncOpenAI.assert_called_once()
        assert MockAsyncOpenAI.call_args.kwargs["timeout"] == 88

    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_retries_next_model_after_empty_response(
        self, MockAgent, mock_load, MockClient, mock_registry, monkeypatch
    ):
        from types import SimpleNamespace
        import json

        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge

        monkeypatch.setattr("core.settings_service.settings.get", lambda key, default=None: default)
        monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "", raising=False)
        mock_registry.get_models_by_provider.return_value = [{"id": "model-a"}, {"id": "model-b"}]

        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3
        route_client.get_ordered_candidates.return_value = [
            {"id": "model-a", "intelligence": 10, "context_window": 128000},
            {"id": "model-b", "intelligence": 10, "context_window": 128000},
        ]
        MockClient.return_value = route_client

        failure_tracker = MagicMock(record_success=AsyncMock(), record_failure=AsyncMock())
        MockClient.get_failure_tracker.return_value = failure_tracker

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        reply_output = json.dumps({REPLY_MARKER: {"content": "第二个模型回复"}}, ensure_ascii=False)
        process_count = {"value": 0}
        message_calls = []
        user_msg = SimpleNamespace(role="user", content="你好")
        assistant_msg = SimpleNamespace(role="assistant", content="")
        reply_messages = _tool_exchange("reply", reply_output)

        def fake_get_messages():
            message_calls.append(len(message_calls) + 1)
            if len(message_calls) == 3:
                return [user_msg, assistant_msg]
            if process_count["value"] >= 2:
                return reply_messages
            return []

        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.side_effect = fake_get_messages
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = 0

        async def fake_process(_event):
            process_count["value"] += 1
            return None

        llm = MagicMock(config=MagicMock(model="old-model"))
        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=llm)
        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好",
                user_id="u1",
                session_id="private_u1",
                metadata={"complexity": 3},
            )

        result = run_async(_run())

        assert result == "第二个模型回复"
        assert mock_agent._process_event.await_count == 2
        failure_tracker.record_failure.assert_awaited_once_with("model-a")
        failure_tracker.record_success.assert_awaited_once_with("model-b")
        assert llm.config.model == "model-b"
        mock_conv.truncate_from.assert_called_once_with(0)

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_prefers_ai_daily_wrapped_html_over_plaintext_rewrite(self, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        mock_conv._messages = []
        html = '<article class="news-brief"><h1>HTML资讯卡片</h1></article>'
        tool_messages = [
            *_rich_tool_exchange("ai_daily", html),
            {"role": "assistant", "content": "我给你整理了几条新闻"},
        ]
        mock_conv.to_messages.return_value = tool_messages
        mock_conv.get_messages.return_value = tool_messages
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")))

        async def fake_process(_event):
            bridge._output._buffer.append("我给你整理了几条新闻")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "给我最新 AI 新闻",
                user_id="u1",
                session_id="private_u1",
            )

        result = run_async(_run())

        assert result.startswith("<article")
        assert '\\"' not in result

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_prefers_group_analysis_html_over_plaintext_rewrite(self, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        mock_conv._messages = []
        group_html = (
            "<!DOCTYPE html><html><body class=\"group-analysis-report\">"
            "<h1>群聊分析卡片</h1></body></html>"
        )
        ga_messages = [
            *_rich_tool_exchange("group_analysis", group_html),
            {"role": "assistant", "content": "我给你总结一下这个群"},
        ]
        mock_conv.to_messages.return_value = ga_messages
        mock_conv.get_messages.return_value = ga_messages
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")))

        async def fake_process(_event):
            bridge._output._buffer.append("我给你总结一下这个群")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "分析第二团体这个群的消息",
                user_id="u1",
                session_id="private_u1",
            )

        result = run_async(_run())

        assert result.startswith("<!DOCTYPE html>")
        assert "group-analysis-report" in result

    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_returns_group_html_when_processing_times_out_after_tool(
        self, MockAgent, mock_load, MockClient, mock_registry
    ):
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 5
        route_client.get_ordered_candidates.return_value = [
            {"id": "model-a", "intelligence": 9, "cost_input_1m": 0.0, "context_window": 128000}
        ]
        MockClient.return_value = route_client
        MockClient.get_failure_tracker.return_value = MagicMock(
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )
        mock_registry.get_models_by_provider.return_value = [{"id": "model-a"}]

        group_html = "<!DOCTYPE html><html><body class=\"group-analysis-report\"><h1>群聊分析卡片</h1></body></html>"

        tool_messages = _rich_tool_exchange(
            "group_analysis",
            group_html,
            as_objects=True,
        )
        mock_conv = MagicMock()
        mock_conv._messages = tool_messages
        mock_conv.get_messages.return_value = tool_messages
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")))
        mock_agent._process_event = AsyncMock(side_effect=asyncio.TimeoutError())
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "分析这个群的消息",
                user_id="u1",
                session_id="private_u1",
            )

        result = run_async(_run())

        assert result == group_html

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_injects_history_header(self, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")))

        async def fake_process(_event):
            bridge._output._buffer.append("ok")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "test query",
                user_id="u1",
                session_id="private_u1",
                metadata={
                    "history_header": "[最近若干条对话历史，仅用于理解语境，已按行数和 token 预算裁剪。]",
                    "history_messages": [{"role": "user", "content": "旧消息"}],
                },
            )

        run_async(_run())
        mock_conv.append.assert_any_call(
            "system",
            "[最近若干条对话历史，仅用于理解语境，已按行数和 token 预算裁剪。]",
        )

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_group_restriction_allows_sticker_search(self, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.registry._tools = {"reply": object(), "sticker_search": object()}
        mock_conv = MagicMock()
        messages = []
        mock_conv.append.side_effect = lambda role, content: messages.append(
            {"role": role, "content": content}
        )
        mock_conv.get_messages.return_value = _tool_exchange(
            "reply",
            '{"NANOBOT_REPLY_OUTPUT": {"content": "ok"}}',
        )
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1
        mock_controller = MagicMock()
        mock_controller.conversation = mock_conv
        mock_controller.llm = MagicMock(config=MagicMock(model="test-model"))
        mock_controller.max_attempts = 1
        mock_agent.controller = mock_controller

        async def fake_process(_event):
            pass

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "发个表情",
                user_id="group_123",
                session_id="group_123",
                metadata={"is_group": True, "group_id": "123", "runtime_preset": "lightweight"},
            )

        assert run_async(_run()) == "ok"
        assert "sticker_search" in mock_agent.registry._tools
        restriction_text = "\n".join(msg["content"] for msg in messages)
        assert "sticker_search：" not in restriction_text
        assert "真实可调用工具以 API tools schema 为准" in restriction_text


# ── Creature Config Loading Test ──

class TestCreatureConfig:
    """Test that the creature config.yaml loads correctly via KT."""

    def test_config_loads(self):
        """Verify creatures/nanobot/config.yaml can be parsed by KT."""
        import os
        config_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "creatures", "nanobot"
        )
        if not os.path.exists(config_dir):
            pytest.skip("creatures/nanobot not found")

        from kohakuterrarium.core.config import load_agent_config
        config = load_agent_config(config_dir)
        assert config.name == "nanobot"
        assert config.tool_format in ["bracket", "native"]
        assert len(config.tools) >= 3
        tool_names = {tool.name for tool in config.tools}
        assert "image_summary" in tool_names


# ── Reply contract 锁死测试 ──


class TestReplyContract:
    """锁死 reply() 行为——确保不因重构引入泄漏口。"""

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_reply_tool_json_content_is_sent(self, MockAgent, mock_load):
        """reply() JSON 结构化输出 → 正确提取返回。"""
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        mock_conv.get_messages.return_value = _tool_exchange(
            "reply",
            '{"NANOBOT_REPLY_OUTPUT": {"content": "这是给用户的回复"}}',
        )
        mock_agent.controller = MagicMock(
            conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")),
        )

        async def fake_process(_event):
            bridge._output._buffer.append("ok")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好",
                user_id="u1",
                session_id="private_u1",
            )

        result = run_async(_run())
        assert result == "这是给用户的回复"

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_html_tool_output_bypasses_reply(self, MockAgent, mock_load):
        """HTML 工具输出 → 直出，不经 reply()。"""
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        group_html = (
            "<!DOCTYPE html><html><body class=\"group-analysis-report\">"
            "<h1>群聊日报</h1></body></html>"
        )
        mock_conv.get_messages.return_value = _rich_tool_exchange(
            "group_analysis",
            group_html,
        )
        mock_agent.controller = MagicMock(
            conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")),
        )

        async def fake_process(_event):
            bridge._output._buffer.append("some irrelevant text")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "群日报",
                user_id="u1",
                session_id="private_u1",
            )

        result = run_async(_run())
        assert result.startswith("<!DOCTYPE html>")
        assert "group-analysis-report" in result

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_direct_assistant_text_is_not_sent(self, MockAgent, mock_load):
        """无 reply() → conversation 中的 assistant 纯文本不应泄漏为用户回复。"""
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        # conversation 里有 assistant 文本但没有 reply() tool 消息
        mock_conv.get_messages.return_value = [
            {"role": "assistant", "content": "我来分析一下这个群的消息..."},
        ]
        mock_controller = MagicMock()
        mock_controller.conversation = mock_conv
        mock_controller.llm = MagicMock(config=MagicMock(model="test-model"))
        mock_controller.max_attempts = 1
        mock_conv.find_last_user_index.return_value = -1  # 跳过回滚逻辑
        mock_agent.controller = mock_controller

        # output buffer 保持空——模型没有通过 reply() 产出回复
        async def fake_process(_event):
            pass

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好",
                user_id="u1",
                session_id="private_u1",
            )

        result = run_async(_run())
        assert not result or result == ""

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_structured_final_action_without_tool_is_suppressed(self, MockAgent, mock_load, monkeypatch):
        """assistant 自报的结构化 no_reply 不能替代真实 no_reply 工具。"""
        import json

        from nanobot_kt.bridge import NanobotBridge

        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.get_failure_tracker", classmethod(lambda cls: None))

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        structured = json.dumps(
            {"final_action": "no_reply", "reason": "not addressed to bot"},
            ensure_ascii=False,
        )
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = [{"role": "assistant", "content": structured}]
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.controller = MagicMock(
            conversation=mock_conv,
            llm=MagicMock(config=MagicMock(model="test-model")),
        )

        async def fake_process(_event):
            bridge._output._buffer.append(structured)

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "ambient",
                user_id="u1",
                session_id="private_u1",
                metadata={"enable_reply_contract_retry": False},
            )

        result = run_async(_run())

        assert result == ""
        reply_meta = bridge.pop_last_reply_meta("private_u1")
        assert reply_meta["_agent_result"] == "no_tool_call"
        assert reply_meta["_no_tool_call"] is True
        assert "_no_reply" not in reply_meta

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_no_tool_call_retries_once_and_sends_reply(self, MockAgent, mock_load, db_session, monkeypatch):
        """第一次无 reply/no_reply → bridge 追加纠正 prompt 重试一次，成功后发送 reply。"""
        from core.database import ReplyContractCheckLog
        from nanobot_kt.bridge import NanobotBridge

        monkeypatch.setattr("clients.classifier_client.resolve_model_route", lambda key: {"base_url": "http://llm.test/v1", "api_key": "k", "provider_id": "newapi"})
        monkeypatch.setattr("clients.classifier_client.ensure_model_route_enabled", lambda key, route: None)
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.sync_models_to_registry", AsyncMock(return_value=None))
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.estimate_complexity", lambda self, messages, tools=None: 1)
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.get_ordered_candidates", lambda self, **kwargs: [{"id": "test-model", "intelligence": 7, "cost_input_1m": 0}])
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.get_failure_tracker", classmethod(lambda cls: None))
        monkeypatch.setattr("nanobot_kt.bridge.registry.get_models_by_provider", lambda provider: [{"id": "test-model"}])

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        messages = []
        mock_conv = MagicMock()
        mock_conv._messages = messages
        mock_conv.append.side_effect = lambda role, content: messages.append({"role": role, "content": content})
        mock_conv.get_messages.side_effect = lambda: messages
        mock_conv.to_messages.side_effect = lambda: messages
        mock_conv.find_last_user_index.return_value = -1

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.registry._tools = {"reply": object(), "no_reply": object()}
        mock_agent.controller = MagicMock(
            conversation=mock_conv,
            llm=MagicMock(config=MagicMock(model="test-model")),
            max_attempts=1,
        )

        process_calls = []

        async def fake_process(_event):
            process_calls.append(getattr(_event, "content", ""))
            if len(process_calls) == 1:
                bridge._output._buffer.append("我会直接回复，但没有调用工具")
            else:
                messages.extend(_tool_exchange(
                    "reply",
                    '{"NANOBOT_REPLY_OUTPUT": {"content": "重试后的回复"}}',
                    call_id="call_retry_reply",
                ))

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message("你好", user_id="u1", session_id="s-retry")

        result = run_async(_run())

        assert result == "重试后的回复"
        assert mock_agent._process_event.await_count == 2
        assert "你刚才没有调用 reply 或 no_reply 工具" in process_calls[1]
        assert "<reply_contract_retry>" in process_calls[1]
        logs = db_session.query(ReplyContractCheckLog).order_by(ReplyContractCheckLog.attempt.asc()).all()
        assert [log.attempt for log in logs] == [0, 1]
        assert logs[0].result == "no_tool_call"
        assert logs[1].result == "retry_success"

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_no_tool_call_retry_plain_text_is_suppressed(self, MockAgent, mock_load, db_session, monkeypatch):
        """重试后仍无真实工具调用时，普通文本不得成为最终回复。"""
        from core.database import ReplyContractCheckLog
        from nanobot_kt.bridge import NanobotBridge

        monkeypatch.setattr("clients.classifier_client.resolve_model_route", lambda key: {"base_url": "http://llm.test/v1", "api_key": "k", "provider_id": "newapi"})
        monkeypatch.setattr("clients.classifier_client.ensure_model_route_enabled", lambda key, route: None)
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.sync_models_to_registry", AsyncMock(return_value=None))
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.estimate_complexity", lambda self, messages, tools=None: 1)
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.get_ordered_candidates", lambda self, **kwargs: [{"id": "test-model", "intelligence": 7, "cost_input_1m": 0}])
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.get_failure_tracker", classmethod(lambda cls: None))
        monkeypatch.setattr("nanobot_kt.bridge.registry.get_models_by_provider", lambda provider: [{"id": "test-model"}])

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        messages = []
        mock_conv = MagicMock()
        mock_conv._messages = messages
        mock_conv.append.side_effect = lambda role, content: messages.append({"role": role, "content": content})
        mock_conv.get_messages.side_effect = lambda: messages
        mock_conv.to_messages.side_effect = lambda: messages
        mock_conv.find_last_user_index.return_value = -1

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.registry._tools = {"reply": object(), "no_reply": object()}
        mock_agent.controller = MagicMock(
            conversation=mock_conv,
            llm=MagicMock(config=MagicMock(model="test-model")),
            max_attempts=1,
        )

        async def fake_process(_event):
            bridge._output._buffer.append("还是直接输出普通文本")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message("你好", user_id="u1", session_id="s-suppress")

        result = run_async(_run())

        assert result == ""
        assert mock_agent._process_event.await_count == 2
        assert bridge.is_no_tool_call("s-suppress") is True
        logs = db_session.query(ReplyContractCheckLog).order_by(ReplyContractCheckLog.attempt.asc()).all()
        assert [log.result for log in logs] == ["no_tool_call", "suppressed"]

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_no_tool_call_retry_fake_tool_claim_stays_suppressed(self, MockAgent, mock_load, db_session, monkeypatch):
        """重试后仍假称已调用 reply 工具 → 继续抑制，避免把契约错误发给用户。"""
        from core.database import ReplyContractCheckLog
        from nanobot_kt.bridge import NanobotBridge

        monkeypatch.setattr("clients.classifier_client.resolve_model_route", lambda key: {"base_url": "http://llm.test/v1", "api_key": "k", "provider_id": "newapi"})
        monkeypatch.setattr("clients.classifier_client.ensure_model_route_enabled", lambda key, route: None)
        monkeypatch.setattr("core.settings_service.settings.get", lambda key, default=None: default)
        monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "", raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.sync_models_to_registry", AsyncMock(return_value=None))
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.estimate_complexity", lambda self, messages, tools=None: 1)
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.get_ordered_candidates", lambda self, **kwargs: [{"id": "test-model", "intelligence": 7, "cost_input_1m": 0}])
        failure_tracker = MagicMock(
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )
        monkeypatch.setattr(
            "nanobot_kt.bridge.NewAPIClient.get_failure_tracker",
            classmethod(lambda cls: failure_tracker),
        )
        monkeypatch.setattr("nanobot_kt.bridge.registry.get_models_by_provider", lambda provider: [{"id": "test-model"}])

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        messages = []
        mock_conv = MagicMock()
        mock_conv._messages = messages
        mock_conv.append.side_effect = lambda role, content: messages.append({"role": role, "content": content})
        mock_conv.get_messages.side_effect = lambda: messages
        mock_conv.to_messages.side_effect = lambda: messages
        mock_conv.find_last_user_index.return_value = -1

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.registry._tools = {"reply": object(), "no_reply": object()}
        mock_agent.controller = MagicMock(
            conversation=mock_conv,
            llm=MagicMock(config=MagicMock(model="test-model")),
            max_attempts=1,
        )

        async def fake_process(_event):
            bridge._output._buffer.append("我已经调用 reply 工具发送了")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message("你好", user_id="u1", session_id="s-fake-claim")

        result = run_async(_run())

        assert result == ""
        assert bridge.is_no_tool_call("s-fake-claim") is True
        logs = db_session.query(ReplyContractCheckLog).order_by(ReplyContractCheckLog.attempt.asc()).all()
        assert [log.result for log in logs] == ["fake_tool_call_claim", "suppressed"]
        failure_tracker.record_success.assert_not_awaited()
        failure_tracker.record_failure.assert_awaited_once_with("test-model")

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_no_tool_call_retry_plain_marker_json_is_suppressed(self, MockAgent, mock_load, db_session, monkeypatch):
        """assistant 输出 reply marker JSON 仍不具备真实工具来源。"""
        from core.database import ReplyContractCheckLog
        from nanobot_kt.bridge import NanobotBridge

        monkeypatch.setattr("clients.classifier_client.resolve_model_route", lambda key: {"base_url": "http://llm.test/v1", "api_key": "k", "provider_id": "newapi"})
        monkeypatch.setattr("clients.classifier_client.ensure_model_route_enabled", lambda key, route: None)
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.sync_models_to_registry", AsyncMock(return_value=None))
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.estimate_complexity", lambda self, messages, tools=None: 1)
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.get_ordered_candidates", lambda self, **kwargs: [{"id": "test-model", "intelligence": 7, "cost_input_1m": 0}])
        monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient.get_failure_tracker", classmethod(lambda cls: None))
        monkeypatch.setattr("nanobot_kt.bridge.registry.get_models_by_provider", lambda provider: [{"id": "test-model"}])

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        messages = []
        mock_conv = MagicMock()
        mock_conv._messages = messages
        mock_conv.append.side_effect = lambda role, content: messages.append({"role": role, "content": content})
        mock_conv.get_messages.side_effect = lambda: messages
        mock_conv.to_messages.side_effect = lambda: messages
        mock_conv.find_last_user_index.return_value = -1

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.registry._tools = {"reply": object(), "no_reply": object()}
        mock_agent.controller = MagicMock(
            conversation=mock_conv,
            llm=MagicMock(config=MagicMock(model="test-model")),
            max_attempts=1,
        )
        outputs = [
            "普通文本",
            '{"NANOBOT_REPLY_OUTPUT": {"content": "修复后的回复"}}',
        ]

        async def fake_process(_event):
            bridge._output._buffer.append(outputs.pop(0))

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message("你好", user_id="u1", session_id="s-marker-repair")

        result = run_async(_run())

        assert result == ""
        assert bridge.is_no_tool_call("s-marker-repair") is True
        logs = db_session.query(ReplyContractCheckLog).order_by(ReplyContractCheckLog.attempt.asc()).all()
        assert [log.result for log in logs] == ["no_tool_call", "suppressed"]

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_reasoning_content_never_sent(self, MockAgent, mock_load):
        """reasoning_content 绝不能泄漏为用户可见回复。"""
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        # conversation 有 reasoning_content 但没有 reply() tool 消息
        mock_conv.get_messages.return_value = [
            {"role": "assistant", "content": "", "reasoning_content": "用户问的是群聊情况..."},
        ]
        mock_controller = MagicMock()
        mock_controller.conversation = mock_conv
        mock_controller.llm = MagicMock(config=MagicMock(model="test-model"))
        mock_controller.max_attempts = 1
        mock_conv.find_last_user_index.return_value = -1
        mock_agent.controller = mock_controller

        async def fake_process(_event):
            pass

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好",
                user_id="u1",
                session_id="private_u1",
            )

        result = run_async(_run())
        assert not result or result == ""
        assert "调 group_analysis" not in (result or "")


class TestNoteBotReplied:
    """验证 note_bot_replied() 自身行为——bridge 层集成测试另测。"""

    def test_updates_timestamp(self):
        from core.timing_runtime import get_group_runtime, GateState
        rt = get_group_runtime()
        rt._states["group_g_test"] = GateState()
        assert rt._states["group_g_test"].last_bot_reply_ts == 0.0
        rt.note_bot_replied("g_test")
        assert rt._states["group_g_test"].last_bot_reply_ts > 0

    def test_nonexistent_no_error(self):
        from core.timing_runtime import get_group_runtime
        rt = get_group_runtime()
        rt.note_bot_replied("nonexistent")  # 不抛异常


class TestNoteBotRepliedBridge:
    """bridge 层验证 note_bot_replied 调用链。"""

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    @patch("core.timing_runtime.GroupRuntime.note_bot_replied")
    def test_group_response_calls_note(self, mock_note, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        mock_conv.get_messages.return_value = _tool_exchange(
            "reply",
            '{"NANOBOT_REPLY_OUTPUT": {"content": "群聊回复"}}',
        )
        mock_conv.find_last_user_index.return_value = -1
        mock_controller = MagicMock()
        mock_controller.conversation = mock_conv
        mock_controller.llm = MagicMock(config=MagicMock(model="test-model"))
        mock_controller.max_attempts = 1
        mock_agent.controller = mock_controller

        async def fake_process(_event):
            bridge._output._buffer.append("群聊回复")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好", user_id="u1", session_id="group_123",
                metadata={"is_group": True},
            )

        result = run_async(_run())
        assert result == "群聊回复"
        mock_note.assert_called_once()  # 核心：确认 bridge 调用了 note_bot_replied

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    @patch("core.timing_runtime.GroupRuntime.note_bot_replied")
    def test_private_chat_skips_note(self, mock_note, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        mock_conv.get_messages.return_value = _tool_exchange(
            "reply",
            '{"NANOBOT_REPLY_OUTPUT": {"content": "私聊回复"}}',
        )
        mock_conv.find_last_user_index.return_value = -1
        mock_controller = MagicMock()
        mock_controller.conversation = mock_conv
        mock_controller.llm = MagicMock(config=MagicMock(model="test-model"))
        mock_controller.max_attempts = 1
        mock_agent.controller = mock_controller

        async def fake_process(_event):
            bridge._output._buffer.append("私聊回复")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好", user_id="u1", session_id="private_u1",
                metadata={"is_group": False},
            )

        result = run_async(_run())
        assert result == "私聊回复"
        mock_note.assert_not_called()

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    @patch("core.timing_runtime.GroupRuntime.note_bot_replied")
    def test_group_dry_run_skips_note(self, mock_note, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        mock_conv.get_messages.return_value = _tool_exchange(
            "reply",
            '{"NANOBOT_REPLY_OUTPUT": {"content": "dry-run 回复"}}',
        )
        mock_conv.find_last_user_index.return_value = -1
        mock_controller = MagicMock()
        mock_controller.conversation = mock_conv
        mock_controller.llm = MagicMock(config=MagicMock(model="test-model"))
        mock_controller.max_attempts = 1
        mock_agent.controller = mock_controller

        async def fake_process(_event):
            bridge._output._buffer.append("dry-run 回复")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好", user_id="u1", session_id="group_123",
                metadata={"is_group": True, "dry_run": True},
            )

        result = run_async(_run())
        assert result == "dry-run 回复"
        mock_note.assert_not_called()

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    @patch("core.timing_runtime.GroupRuntime.note_bot_replied")
    def test_empty_response_skips_note(self, mock_note, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        mock_conv.get_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1
        mock_controller = MagicMock()
        mock_controller.conversation = mock_conv
        mock_controller.llm = MagicMock(config=MagicMock(model="test-model"))
        mock_controller.max_attempts = 1
        mock_agent.controller = mock_controller

        async def fake_process(_event):
            pass

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好", user_id="u1", session_id="group_123",
                metadata={"is_group": True},
            )

        run_async(_run())
        mock_note.assert_not_called()

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    @patch("core.timing_runtime.GroupRuntime.note_bot_replied")
    def test_note_exception_preserves_response(self, mock_note, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge

        mock_note.side_effect = RuntimeError("boom")

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        mock_conv.get_messages.return_value = _tool_exchange(
            "reply",
            '{"NANOBOT_REPLY_OUTPUT": {"content": "回复"}}',
        )
        mock_conv.find_last_user_index.return_value = -1
        mock_controller = MagicMock()
        mock_controller.conversation = mock_conv
        mock_controller.llm = MagicMock(config=MagicMock(model="test-model"))
        mock_controller.max_attempts = 1
        mock_agent.controller = mock_controller

        async def fake_process(_event):
            bridge._output._buffer.append("回复")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message(
                "你好", user_id="u1", session_id="group_123",
                metadata={"is_group": True},
            )

        result = run_async(_run())
        assert result == "回复"
