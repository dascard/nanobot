"""
Test suite for KT framework integration.

Tests the new adapter layer: BaseTool subclasses, BufferedOutput, NanobotBridge.
Uses mocks to avoid requiring real APIs or KT agent infrastructure.
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ── BufferedOutput Tests ──

class TestBufferedOutput:
    """Test the BufferedOutput module that collects LLM response for programmatic access."""

    def test_write_and_get(self):
        from nanobot_kt.output import BufferedOutput
        output = BufferedOutput()
        asyncio.run(output.write("hello"))
        asyncio.run(output.write(" world"))
        assert output.get_response() == "hello world"

    def test_clear(self):
        from nanobot_kt.output import BufferedOutput
        output = BufferedOutput()
        asyncio.run(output.write("test"))
        output.clear()
        assert output.get_response() == ""

    def test_processing_lifecycle(self):
        from nanobot_kt.output import BufferedOutput
        output = BufferedOutput()

        asyncio.run(output.write("old"))
        assert output.get_response() == "old"

        # on_processing_start should clear buffer
        asyncio.run(output.on_processing_start())
        assert output.get_response() == ""

        asyncio.run(output.write_stream("new "))
        asyncio.run(output.write_stream("data"))
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

        response, event = asyncio.run(_run())
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
        result = asyncio.run(tool.execute({"sql": "SELECT count(id) AS count FROM chat_logs LIMIT 1"}))
        assert result.success
        assert "42" in result.output

    @patch("creatures.nanobot.prompts.skills.sql_analysis.tool.AnalysisSandbox")
    def test_execute_empty_sql(self, MockSandbox):
        from creatures.nanobot.prompts.skills.sql_analysis.tool import SQLAnalysisTool
        tool = SQLAnalysisTool()
        result = asyncio.run(tool.execute({"sql": ""}))
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
        result = asyncio.run(tool.execute({"code": "print(42)"}))
        assert result.success
        assert "42" in result.output


class TestNewsSearchTool:
    """Test the NewsSearchTool BaseTool adapter."""

    def test_tool_metadata(self):
        from creatures.nanobot.prompts.skills.news_search.tool import NewsSearchTool
        tool = NewsSearchTool()
        assert tool.tool_name == "news_search"

    @patch("creatures.nanobot.prompts.skills.news_search.tool._run_news_daily_pipeline")
    def test_execute_success(self, mock_daily):
        from creatures.nanobot.prompts.skills.news_search.tool import NewsSearchTool
        mock_daily.return_value = "<article>AI news: GPT-5 released</article>"

        tool = NewsSearchTool()
        result = asyncio.run(tool.execute({"query": "AI news"}))
        assert result.success
        assert "GPT-5" in result.output


# ── NanobotBridge Tests ──

class TestNanobotBridge:
    """Test the NanobotBridge lifecycle manager."""

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

        results = asyncio.run(_run())
        assert sorted(results) == ["session-a", "session-b"]
        assert max_active == 2

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
        asyncio.run(bridge.start())

        mock_load.assert_called_once_with("creatures/nanobot")
        MockAgent.assert_called_once()
        assert bridge.agent is not None

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_returns_output(self, MockAgent, mock_load):
        """Test that handle_message() returns the buffered response."""
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []

        async def fake_process(event):
            # Simulate the agent writing to the output buffer
            bridge._output._buffer.append("Hello from KT!")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        bridge = NanobotBridge()

        async def _run():
            await bridge.start()
            return await bridge.handle_message("test query", user_id="u1")

        result = asyncio.run(_run())
        assert result == "Hello from KT!"

    def test_handle_message_without_init(self):
        """Test that handle_message returns error if agent not started."""
        from nanobot_kt.bridge import NanobotBridge
        bridge = NanobotBridge()
        result = asyncio.run(bridge.handle_message("test"))
        assert "not initialized" in result.lower() or "Error" in result

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_uses_multimodal_event_for_files(self, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge
        from kohakuterrarium.llm.message import ImagePart

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_agent.controller = MagicMock(conversation=mock_conv, llm=MagicMock(config=MagicMock(model="test-model")))

        captured = {}

        async def fake_process(event):
            captured["event"] = event
            bridge._output._buffer.append("ok")

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

            result = asyncio.run(_run())

        assert result == "ok"
        mock_prepare.assert_called_once()

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_injects_runtime_context_system_message(self, MockAgent, mock_load):
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

        async def fake_process(event):
            bridge._output._buffer.append("ok")

        mock_agent._process_event = AsyncMock(side_effect=fake_process)
        MockAgent.return_value = mock_agent

        with patch("nanobot_kt.bridge._current_time_label", return_value="2026-05-01 09:30:00 CST"):
            bridge = NanobotBridge()

            async def _run():
                await bridge.start()
                return await bridge.handle_message("test query", user_id="u1")

            result = asyncio.run(_run())

        assert result == "ok"
        system_messages = [call.args[1] for call in mock_conv.append.call_args_list if call.args[0] == "system"]
        assert any(
            "<runtime_context>" in msg
            and "current_time: 2026-05-01 09:30:00 CST" in msg
            and "chat_type: private" in msg
            for msg in system_messages
        )

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

        result = asyncio.run(_run())
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

        result = asyncio.run(_run())
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

        result = asyncio.run(_run())
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

        result = asyncio.run(_run())

        assert result == "openrouter 回复"
        assert client_kwargs["registry_provider"] == "openrouter"
        mock_registry.get_models_by_provider.assert_called_with("openrouter")
        assert route_client.get_ordered_candidates.call_args.kwargs["provider"] == "openrouter"

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

        result = asyncio.run(_run())

        assert result == "换 key 回复"
        assert llm._api_key == "new-key"
        MockAsyncOpenAI.assert_called_once()
        assert MockAsyncOpenAI.call_args.kwargs["api_key"] == "new-key"

    @patch("nanobot_kt.bridge.load_agent_config")
    @patch("nanobot_kt.bridge.Agent")
    def test_handle_message_prefers_news_tool_html_over_plaintext_rewrite(self, MockAgent, mock_load):
        from nanobot_kt.bridge import NanobotBridge

        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config

        mock_agent = MagicMock()
        mock_agent.start = AsyncMock()
        mock_agent.registry.list_tools.return_value = []
        mock_conv = MagicMock()
        mock_conv._messages = []
        tool_messages = [
            {"role": "tool", "content": "[news_search]\n<article class=\"news-brief\"><h1>HTML资讯卡片</h1></article>"},
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

        result = asyncio.run(_run())

        assert result.startswith("<article")

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

        result = asyncio.run(_run())

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

        result = asyncio.run(_run())

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

        asyncio.run(_run())
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
                metadata={"is_group": True, "group_id": "123", "tool_policy": "limited"},
            )

        assert asyncio.run(_run()) == "ok"
        assert "sticker_search" in mock_agent.registry._tools
        restriction_text = "\n".join(msg["content"] for msg in messages)
        assert "sticker_search" in restriction_text
        assert "本轮只允许 reply/image_summary/python_sandbox/sticker_search" in restriction_text


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

        result = asyncio.run(_run())
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

        result = asyncio.run(_run())
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

        result = asyncio.run(_run())
        assert not result or result == ""

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

        result = asyncio.run(_run())
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

        result = asyncio.run(_run())
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

        result = asyncio.run(_run())
        assert result == "私聊回复"
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

        asyncio.run(_run())
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

        result = asyncio.run(_run())
        assert result == "回复"
