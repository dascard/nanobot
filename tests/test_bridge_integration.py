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
    async def test_legacy_reply_not_extracted(self):
        """旧 [REPLY] 标签格式不再支持——只解析 JSON reply marker。"""
        b = await _start_bridge()
        _set_conversation(b, [
            _fake_tool_msg("reply", "[REPLY]你好[/REPLY]"),
        ])
        result = b._extract_reply_from_tool_output()
        assert result == ""

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
    async def test_ai_daily_raw_html_extracted(self):
        b = await _start_bridge()
        html = '<article class="news-brief"><h1>AI News</h1></article>'
        _set_conversation(b, [_fake_tool_msg("ai_daily", html)])
        result = b._extract_last_rich_tool_output(("news-brief",))
        assert "news-brief" in result

    @pytest.mark.asyncio
    async def test_ai_daily_reply_wrapped_html_extracted_cleanly(self):
        from creatures.nanobot.prompts.skills.reply.tool import build_reply_output

        b = await _start_bridge()
        html = (
            '<!DOCTYPE html><html lang="zh-CN">'
            '<body class="news-brief"><div class="container">AI 日报</div></body>'
            "</html>"
        )
        _set_conversation(b, [_fake_tool_msg("ai_daily", build_reply_output(html))])

        result = b._extract_last_rich_tool_output(("news-brief",))

        assert result == html
        assert '\\"' not in result

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
    async def test_html_extraction_ignores_non_tool_roles(self):
        """_extract_last_rich_tool_output 只看 role=tool 消息——assistant 的 marker 不触发 HTML 提取。"""
        b = await _start_bridge()
        _set_conversation(b, [
            _fake_assistant_msg("这里是 news-brief 的纯文本讨论"),
            _fake_tool_msg("ai_daily", '<article class="news-brief"><h1>日报</h1></article>'),
        ])
        result = b._extract_last_rich_tool_output(("news-brief",))
        assert "news-brief" in result
        assert "纯文本讨论" not in result

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



class TestReplyMeta:
    """Batch 2: reply_meta per-session 隔离和校验"""

    def test_reply_meta_store_popped_isolated(self):
        """NanobotBridge per-session pop"""
        from nanobot_kt.bridge import NanobotBridge

        b = NanobotBridge()
        b._reply_meta_store()["s_A"] = {"send_mode": "quote"}
        b._reply_meta_store()["s_B"] = {"send_mode": "mention"}
        assert b.pop_last_reply_meta("s_A")["send_mode"] == "quote"
        assert b.pop_last_reply_meta("s_B")["send_mode"] == "mention"
        assert b.pop_last_reply_meta("s_A") is None

    def test_reply_meta_store_is_instance_local(self):
        """两个 NanobotBridge 实例不共享 store"""
        from nanobot_kt.bridge import NanobotBridge

        a = NanobotBridge()
        b = NanobotBridge()
        a._reply_meta_store()["s"] = {"send_mode": "quote"}
        assert b.pop_last_reply_meta("s") is None
        assert a.pop_last_reply_meta("s")["send_mode"] == "quote"

    def test_reply_meta_pool_pops_from_child_bridge(self):
        """NanobotBridgePool 暴露与 child bridge 一致的 reply_meta pop 接口"""
        from nanobot_kt.bridge import NanobotBridge, NanobotBridgePool

        pool = NanobotBridgePool()
        child = NanobotBridge()
        pool._bridges["group_100"] = child
        child._reply_meta_store()["group_100"] = {"send_mode": "quote"}

        assert pool.pop_last_reply_meta("group_100")["send_mode"] == "quote"
        assert pool.pop_last_reply_meta("group_100") is None

    @pytest.mark.asyncio
    async def test_reply_tool_invalid_send_mode_normalized(self):
        """ReplyTool._execute() 非法 send_mode → normal"""
        import json
        from creatures.nanobot.prompts.skills.reply.tool import ReplyTool, REPLY_MARKER

        tool = ReplyTool()
        result = await tool._execute({"content": "hello", "send_mode": "invalid"})
        data = json.loads(result.output)
        assert data[REPLY_MARKER]["send_mode"] == "normal"

    @pytest.mark.asyncio
    async def test_reply_tool_mentions_filter_non_digit(self):
        """ReplyTool._execute() 过滤非数字 mentions"""
        import json
        from creatures.nanobot.prompts.skills.reply.tool import ReplyTool, REPLY_MARKER

        tool = ReplyTool()
        result = await tool._execute({
            "content": "hello",
            "mentions": ["12345", "abc", "67890", ""],
        })
        data = json.loads(result.output)
        assert data[REPLY_MARKER]["mentions"] == ["12345", "67890"]
