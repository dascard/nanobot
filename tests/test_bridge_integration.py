"""bridge.handle_message 完整链路集成测试——覆盖 reply 提取/HTML passthrough/泄漏检测。

mock 策略：绕过 KT agent 的 LLM 调用，直接操控 conversation + output buffer，
验证 bridge 的响应提取和加工逻辑。
"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── helpers ──

def _fake_tool_msg(name: str, output: str) -> dict:
    return {"role": "tool", "content": output, "tool_call_id": f"call_{name}"}


def _fake_assistant_msg(content: str = "", tool_calls: list | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


async def _start_bridge():
    from nanobot_kt.bridge import NanobotBridge
    b = NanobotBridge()
    with patch("nanobot_kt.bridge.Agent") as MockAgent:
        mock_agent = MockAgent.return_value
        mock_agent.registry = MagicMock()
        mock_agent.registry.list_tools.return_value = []
        mock_agent.start = AsyncMock()
        mock_agent.stop = AsyncMock()
        mock_agent._interrupt_requested = False
        mock_agent.controller = MagicMock()
        mock_conv = MagicMock()
        mock_conv._messages = []
        mock_conv.get_messages.return_value = []
        mock_conv.to_messages.return_value = []
        mock_conv.append = MagicMock()
        mock_agent.controller.conversation = mock_conv
        b._output.clear()
        b._agent = mock_agent
    return b


def _set_conversation(bridge, messages: list):
    bridge._agent.controller.conversation.to_messages.return_value = list(messages)
    bridge._agent.controller.conversation.get_messages.return_value = list(messages)


def _set_buffer(bridge, *chunks: str):
    for c in chunks:
        bridge._output._buffer.append(c)


# ── 1. reply 提取 ──

class TestReplyExtraction:
    @pytest.mark.asyncio
    async def test_json_reply_extracted(self):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        b = await _start_bridge()
        reply_output = json.dumps({REPLY_MARKER: {"content": "你好"}}, ensure_ascii=False)
        _set_conversation(b, [
            _fake_tool_msg("reply", reply_output),
        ])
        _set_buffer(b, "")
        result = b._extract_reply_from_tool_output()
        assert result == "你好"

    @pytest.mark.asyncio
    async def test_legacy_reply_extracted(self):
        b = await _start_bridge()
        _set_conversation(b, [
            _fake_tool_msg("reply", "[REPLY]你好[/REPLY]"),
        ])
        result = b._extract_reply_from_tool_output()
        assert result == "你好"

    @pytest.mark.asyncio
    async def test_no_reply_returns_empty(self):
        b = await _start_bridge()
        _set_conversation(b, [
            _fake_assistant_msg("根据分析，结论是..."),
            _fake_tool_msg("sql_analysis", "SELECT result"),
        ])
        result = b._extract_reply_from_tool_output()
        assert result == ""

    @pytest.mark.asyncio
    async def test_reply_overrides_buffer_text(self):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        b = await _start_bridge()
        reply_output = json.dumps({REPLY_MARKER: {"content": "最终回复"}}, ensure_ascii=False)
        _set_conversation(b, [
            _fake_assistant_msg("让我想想...根据分析..."),
            _fake_tool_msg("reply", reply_output),
        ])
        _set_buffer(b, "让我想想...\n根据分析...\n")
        result = b._extract_reply_from_tool_output()
        assert result == "最终回复"


# ── 2. HTML passthrough ──

class TestHtmlPassthrough:
    @pytest.mark.asyncio
    async def test_group_analysis_html_extracted(self):
        b = await _start_bridge()
        html = '<!DOCTYPE html><html><body class="group-analysis-report"><div>日报</div></body></html>'
        _set_conversation(b, [_fake_tool_msg("group_analysis", html)])
        result = b._extract_last_rich_tool_output(("group-analysis-report",))
        assert "group-analysis-report" in result

    @pytest.mark.asyncio
    async def test_news_search_html_extracted(self):
        b = await _start_bridge()
        html = '<article class="news-brief"><h1>AI News</h1></article>'
        _set_conversation(b, [_fake_tool_msg("news_search", html)])
        result = b._extract_last_rich_tool_output(("news-brief",))
        assert "news-brief" in result

    @pytest.mark.asyncio
    async def test_no_html_returns_empty(self):
        b = await _start_bridge()
        _set_conversation(b, [_fake_assistant_msg("没有新闻")])
        result = b._extract_last_rich_tool_output(("news-brief",))
        assert result == ""


# ── 3. 系统提示词防泄漏 ──

class TestNoLeak:
    @pytest.mark.asyncio
    async def test_reply_text_not_contain_system_prompt(self):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        b = await _start_bridge()
        reply_output = json.dumps({REPLY_MARKER: {"content": "今天的日报如下"}}, ensure_ascii=False)
        _set_conversation(b, [_fake_tool_msg("reply", reply_output)])
        _set_buffer(b, "你是 Nanobot。你在 QQ 群里聊天...\n## 工具调用纪律...\n")
        result = b._extract_reply_from_tool_output()
        assert "工具调用纪律" not in result
        assert "你是 Nanobot" not in result
        assert result == "今天的日报如下"

    @pytest.mark.asyncio
    async def test_fallback_does_not_return_reasoning(self):
        b = await _start_bridge()
        b._agent.llm = MagicMock()
        b._agent.llm.last_assistant_extra_fields = {
            "reasoning_content": "你是 Nanobot... ## 工具调用纪律...",
        }
        _set_conversation(b, [])
        result = b._extract_fallback_response()
        assert "工具调用纪律" not in result
        assert "你是 Nanobot" not in result

    @pytest.mark.asyncio
    async def test_reply_marker_not_in_final_output(self):
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER
        b = await _start_bridge()
        reply_output = json.dumps({REPLY_MARKER: {"content": "干净回复"}}, ensure_ascii=False)
        _set_conversation(b, [_fake_tool_msg("reply", reply_output)])
        _set_buffer(b, "[REPLY]干净回复[/REPLY]")
        result = b._extract_reply_from_tool_output()
        assert "[REPLY]" not in result
        assert "[/REPLY]" not in result
        assert result == "干净回复"
