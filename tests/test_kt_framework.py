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
        result = asyncio.run(tool.execute({"sql": "SELECT count(*) FROM chat_logs"}))
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

    @patch("creatures.nanobot.prompts.skills.news_search.tool.search_and_extract_news")
    def test_execute_success(self, mock_search):
        from creatures.nanobot.prompts.skills.news_search.tool import NewsSearchTool
        mock_search.return_value = "AI news: GPT-5 released"

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
        assert isinstance(captured["event"].content, list)
        assert captured["event"].content[0].type == "text"
        image_parts = [part for part in captured["event"].content if getattr(part, "type", "") == "image_url"]
        assert len(image_parts) == 1
        assert image_parts[0].url.startswith("data:image/jpeg;base64,")

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
                    "history_header": "[近30分钟内对话历史，仅用于理解语境。]",
                    "history_messages": [{"role": "user", "content": "旧消息"}],
                },
            )

        asyncio.run(_run())
        mock_conv.append.assert_any_call("system", "[近30分钟内对话历史，仅用于理解语境。]")


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
