"""
Test suite for KT framework integration.

Tests the new adapter layer: BaseTool subclasses, BufferedOutput, NanobotBridge.
Uses mocks to avoid requiring real APIs or KT agent infrastructure.
"""

import asyncio
from tests.async_helpers import run_async
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


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
    def test_execute_success(self, MockSandbox):
        from creatures.nanobot.prompts.skills.python_sandbox.tool import PythonSandboxTool
        mock_instance = MockSandbox.return_value
        mock_instance.execute_python_analysis.return_value = "result: 42"

        tool = PythonSandboxTool()
        result = run_async(tool.execute({"code": "print(42)"}))
        assert result.success
        assert "42" in result.output


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
        mock_conv.get_messages.return_value = [{"role": "tool", "content": reply_output}]
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
            return await bridge.handle_message("test query", user_id="u1")

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
        mock_conv.get_messages.return_value = [{"role": "tool", "content": reply_output}]
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
        mock_conv.get_messages.return_value = [{"role": "tool", "content": reply_output}]
        mock_conv.to_messages.return_value = []
        mock_conv.find_last_user_index.return_value = -1
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")))

        captured = {}

        async def fake_process(event):
            captured["event"] = event

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
        ) as mock_prepare:
            bridge = NanobotBridge()

            async def _run():
                await bridge.start()
                return await bridge.handle_message(
                    "看看这张图",
                    user_id="u1",
                    metadata={"files": ["https://example.com/a.png", "https://example.com/b.png"]},
                )

            result = run_async(_run())

        assert result == "ok"
        mock_prepare.assert_called_once()

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
        mock_conv.get_messages.return_value = [{"role": "tool", "content": reply_output}]
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
        mock_conv.get_messages.return_value = [
            {"role": "tool", "content": reply_output},
        ]
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

    @patch("nanobot_kt.bridge.registry")
    @patch("nanobot_kt.bridge.NewAPIClient")
    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_reply_model_uses_settings_override(
        self, MockAgent, mock_load, MockClient, mock_registry, monkeypatch
    ):
        """settings.get("model.reply") 覆盖 > LLM_MODEL_REPLY > 默认值"""
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        from nanobot_kt.bridge import NanobotBridge
        import json

        # settings 返回 override-model，LLM_MODEL_REPLY 是 env-model
        fake_settings = {"model.reply": "override-model"}
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: fake_settings.get(key, default),
        )
        monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "env-model", raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_INTEL_FLOOR", 12, raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_INTEL_BOOST", 2, raising=False)
        monkeypatch.setattr("nanobot_kt.bridge.REPLY_MODEL_MAX_COST", 10.0, raising=False)

        # registry 确认 override-model 存在且 enabled
        mock_registry.get_model_info = MagicMock(return_value={
            "id": "override-model", "enabled": True, "provider": "new-api",
            "intelligence": 12, "cost_input_1m": 0.4, "tier": "smart",
        })
        mock_registry.get_models_by_provider.return_value = [{"id": "override-model"}]

        auto_called = []

        route_client = MagicMock()
        route_client.sync_models_to_registry = AsyncMock()
        route_client.estimate_complexity.return_value = 3
        route_client.get_ordered_candidates = MagicMock(side_effect=lambda **kw: auto_called.append(True) or [
            {"id": "auto-model", "intelligence": 12, "cost_input_1m": 0.5, "context_window": 128000},
        ])
        MockClient.return_value = route_client
        MockClient.get_failure_tracker.return_value = MagicMock(
            record_success=AsyncMock(),
            record_failure=AsyncMock(),
        )

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        reply_output = json.dumps({REPLY_MARKER: {"content": "覆盖测试"}}, ensure_ascii=False)
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = [
            {"role": "tool", "content": reply_output},
        ]
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
        assert result == "覆盖测试"
        # 手动模型路径：不应调用 get_ordered_candidates
        assert not auto_called, "settings override should use manual model, not auto-routing"

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
        mock_conv.get_messages.return_value = [
            {"role": "tool", "content": reply_output},
        ]
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
        mock_conv.get_messages.return_value = [{"role": "tool", "content": reply_output}]
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
        mock_conv.get_messages.return_value = [{"role": "tool", "content": reply_output}]
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
        mock_conv.get_messages.return_value = [{"role": "tool", "content": reply_output}]
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

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_prefers_ai_daily_wrapped_html_over_plaintext_rewrite(self, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge
        from creatures.nanobot.prompts.skills.reply.tool import build_reply_output

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
            {"role": "tool", "content": build_reply_output(html)},
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
            return await bridge.handle_message("给我最新 AI 新闻", user_id="u1")

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
        ga_messages = [
            {
                "role": "tool",
                "content": (
                    "[group_analysis]\n"
                    "<!DOCTYPE html><html><body class=\"group-analysis-report\">"
                    "<h1>群聊分析卡片</h1></body></html>"
                ),
            },
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
            return await bridge.handle_message("分析第二团体这个群的消息", user_id="u1")

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

        tool_msg = MagicMock()
        tool_msg.role = "tool"
        tool_msg.content = group_html
        mock_conv = MagicMock()
        mock_conv._messages = [tool_msg]
        mock_conv.get_messages.return_value = [tool_msg]
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
            return await bridge.handle_message("分析这个群的消息", user_id="u1")

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
        mock_conv.get_messages.return_value = [
            {"role": "tool", "content": '{"NANOBOT_REPLY_OUTPUT": {"content": "ok"}}'},
        ]
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
        mock_conv.get_messages.return_value = [
            {"role": "tool", "content": '{"NANOBOT_REPLY_OUTPUT": {"content": "这是给用户的回复"}}'},
        ]
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
            return await bridge.handle_message("你好", user_id="u1")

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
        mock_conv.get_messages.return_value = [
            {
                "role": "tool",
                "content": (
                    "[group_analysis]\n"
                    "<!DOCTYPE html><html><body class=\"group-analysis-report\">"
                    "<h1>群聊日报</h1></body></html>"
                ),
            },
        ]
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
            return await bridge.handle_message("群日报", user_id="u1")

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
            return await bridge.handle_message("你好", user_id="u1")

        result = run_async(_run())
        assert not result or result == ""

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
                messages.append({"role": "tool", "content": '{"NANOBOT_REPLY_OUTPUT": {"content": "重试后的回复"}}'})

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
    def test_no_tool_call_retry_plain_text_is_repaired(self, MockAgent, mock_load, db_session, monkeypatch):
        """重试后仍没有工具但产出普通文本 → 作为明文修复回复，避免随机空回复。"""
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

        assert result == "还是直接输出普通文本"
        assert mock_agent._process_event.await_count == 2
        assert bridge.is_no_tool_call("s-suppress") is False
        logs = db_session.query(ReplyContractCheckLog).order_by(ReplyContractCheckLog.attempt.asc()).all()
        assert [log.result for log in logs] == ["no_tool_call", "retry_plain_text_repair"]

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_no_tool_call_retry_fake_tool_claim_stays_suppressed(self, MockAgent, mock_load, db_session, monkeypatch):
        """重试后仍假称已调用 reply 工具 → 继续抑制，避免把契约错误发给用户。"""
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

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_no_tool_call_retry_plain_marker_json_is_repaired(self, MockAgent, mock_load, db_session, monkeypatch):
        """重试后把 reply marker JSON 当普通文本输出 → 提取 content，不把 JSON 发给用户。"""
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

        assert result == "修复后的回复"
        logs = db_session.query(ReplyContractCheckLog).order_by(ReplyContractCheckLog.attempt.asc()).all()
        assert [log.result for log in logs] == ["no_tool_call", "retry_marker_json_repair"]

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
            return await bridge.handle_message("你好", user_id="u1")

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
        mock_conv.get_messages.return_value = [
            {"role": "tool", "content": '{"NANOBOT_REPLY_OUTPUT": {"content": "群聊回复"}}'},
        ]
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
        mock_conv.get_messages.return_value = [
            {"role": "tool", "content": '{"NANOBOT_REPLY_OUTPUT": {"content": "私聊回复"}}'},
        ]
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
        mock_conv.get_messages.return_value = [
            {"role": "tool", "content": '{"NANOBOT_REPLY_OUTPUT": {"content": "dry-run 回复"}}'},
        ]
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
        mock_conv.get_messages.return_value = [
            {"role": "tool", "content": '{"NANOBOT_REPLY_OUTPUT": {"content": "回复"}}'},
        ]
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
