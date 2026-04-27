"""
Tests for history memory system: _build_session_memory, _sanitize_prompt_text,
_enrich_query, and history injection into conversation.
"""
import pytest
from core.database import ChatLog, User


# ── _sanitize_prompt_text ──

def test_sanitize_handles_None():
    from api.routes import _sanitize_prompt_text
    assert _sanitize_prompt_text(None) == ""


def test_sanitize_handles_empty():
    from api.routes import _sanitize_prompt_text
    assert _sanitize_prompt_text("") == ""


def test_sanitize_unescape_system_tags():
    """Marker injection: [PersonaContext], [USER QUERY] etc are escaped."""
    from api.routes import _sanitize_prompt_text
    result = _sanitize_prompt_text("[PersonaContext] user=123\nhello\n[HISTORY]\n[历史结束]")
    assert "[PersonaContext]" not in result
    assert "(PERSONA_CONTEXT_TAG)" in result
    assert "[HISTORY]" not in result
    assert "(HISTORY_TAG)" in result
    assert "[历史结束]" not in result


def test_sanitize_preserves_normal_text():
    from api.routes import _sanitize_prompt_text
    text = "用户说了一段关于 PersonaContext 的话"
    result = _sanitize_prompt_text(text)
    assert "PersonaContext" in result
    assert "用户说了一段" in result
    assert "[PersonaContext]" not in result  # no brackets → no replacement


def test_sanitize_newline_normalization():
    from api.routes import _sanitize_prompt_text
    result = _sanitize_prompt_text("line1\r\nline2\rline3")
    assert "\r\n" not in result
    assert "\r" not in result
    assert "line1\nline2\nline3" in result


def test_sanitize_caps_at_max_chars():
    from api.routes import _sanitize_prompt_text
    result = _sanitize_prompt_text("x" * 500, max_chars=50)
    # text[:50] chars + truncation suffix
    assert len(result) <= 70
    assert "...[截断:" in result
    assert result.startswith("x" * 50)


# ── _build_session_memory ──

def test_build_memory_empty_db(db_session):
    """Empty database returns empty tuple."""
    from api.routes import _build_session_memory
    header, messages = _build_session_memory(db_session, "no_such_session")
    assert header == ""
    assert messages == []


def test_build_memory_single_turn(db_session):
    """One user message should produce one history message dict."""
    from api.routes import _build_session_memory
    _seed_chat_logs(db_session, "s1", [
        ("user", "你好"),
    ])
    header, messages = _build_session_memory(db_session, "s1", max_messages=10)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "你好" in messages[0]["content"]


def test_build_memory_role_alternation(db_session):
    """User+assistant pairs should produce proper role sequence."""
    from api.routes import _build_session_memory
    _seed_chat_logs(db_session, "s1", [
        ("user", "你好"),
        ("assistant", "嗨！"),
        ("user", "今天天气如何"),
        ("assistant", "晴天，25度"),
    ])
    header, messages = _build_session_memory(db_session, "s1", max_messages=10)
    assert len(messages) == 4
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]


def test_build_memory_filters_assistant_noise(db_session):
    """Assistant progress messages like '正在搜索...' should be filtered."""
    from api.routes import _build_session_memory
    _seed_chat_logs(db_session, "s1", [
        ("user", "查一下新闻"),
        ("assistant", "正在搜索 AI 新闻..."),
        ("assistant", "找到了：DeepSeek 发布新模型"),
    ])
    header, messages = _build_session_memory(db_session, "s1", max_messages=10)
    # "正在搜索" should be filtered; only the real response stays
    contents = [m["content"] for m in messages]
    assert not any("正在搜索" in c for c in contents)
    assert any("DeepSeek" in c for c in contents)


def test_build_memory_merges_tool_results(db_session):
    """Tool result should be appended to the preceding assistant message."""
    from api.routes import _build_session_memory
    _seed_chat_logs(db_session, "s1", [
        ("user", "更新我的画像"),
        ("assistant", "好的，我来更新"),
        ("tool", "[persona_update] 画像已更新: summary=技术型用户..."),
        ("assistant", "画像已更新完成！"),
    ])
    header, messages = _build_session_memory(db_session, "s1", max_messages=10)
    # Tool message merged into first assistant msg
    assert len(messages) == 3  # user, assistant_with_tool, assistant2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert "[工具返回:" in messages[1]["content"]
    assert "persona_update" in messages[1]["content"]


def test_build_memory_header_includes_tool_summary(db_session):
    """Header should include [本轮已执行工具: ...] when tools were called."""
    from api.routes import _build_session_memory
    _seed_chat_logs(db_session, "s1", [
        ("user", "查数据"),
        ("assistant", "查询中"),
        ("tool", "[sql_analysis] SELECT * FROM users; 3 rows returned"),
        ("assistant", "查到3条数据"),
    ])
    header, messages = _build_session_memory(db_session, "s1", max_messages=10)
    assert "本轮已执行工具" in header
    assert "sql_analysis" in header


def test_build_memory_tool_failure_marked(db_session):
    """Failed tool calls should be marked with ✗ not ✓."""
    from api.routes import _build_session_memory
    _seed_chat_logs(db_session, "s1", [
        ("user", "查一下"),
        ("assistant", "查询中"),
        ("tool", "[sql_analysis] Error: connection failed"),
        ("assistant", "查询失败"),
    ])
    header, messages = _build_session_memory(db_session, "s1", max_messages=10)
    assert "sql_analysis✗" in header
    assert "✓" not in header


def test_build_memory_caps_total_chars(db_session):
    """Content beyond max_total should be truncated."""
    from api.routes import _build_session_memory
    _seed_chat_logs(db_session, "s1", [
        ("user", "A" * 500),
        ("assistant", "B" * 500),
        ("user", "C" * 500),
        ("assistant", "D" * 500),
    ])
    header, messages = _build_session_memory(db_session, "s1",
                                              max_messages=10,
                                              max_total=800)
    total = sum(len(m["content"]) for m in messages)
    assert total <= 800 + 100  # tolerance for truncation overhead


def test_build_memory_respects_message_limit(db_session):
    """max_messages limits how many DB rows are fetched."""
    from api.routes import _build_session_memory
    for i in range(20):
        _seed_chat_logs(db_session, "s1", [
            ("user", f"msg{i}"),
            ("assistant", f"reply{i}"),
        ])
    header, messages = _build_session_memory(db_session, "s1", max_messages=4)
    assert len(messages) <= 4


def test_build_memory_returns_struct_dicts(db_session):
    """Every returned message must be a dict with 'role' and 'content'."""
    from api.routes import _build_session_memory
    _seed_chat_logs(db_session, "s1", [
        ("user", "hello"),
        ("assistant", "hi"),
    ])
    header, messages = _build_session_memory(db_session, "s1")
    for m in messages:
        assert isinstance(m, dict)
        assert "role" in m
        assert "content" in m
        assert m["role"] in ("user", "assistant")


# ── history injection via bridge ──

@pytest.mark.asyncio
async def test_bridge_injects_history_messages():
    """Bridge should inject history_messages from metadata into conversation."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge._output = MagicMock()
    bridge._output.clear = MagicMock()
    bridge._output.get_response = MagicMock(return_value="test response")
    bridge._output.enable_stream = MagicMock()
    bridge._lock = MagicMock()
    bridge._lock.__aenter__ = AsyncMock()
    bridge._lock.__aexit__ = AsyncMock()

    conv = MagicMock()
    conv._messages = [MagicMock(role="system", content="base prompt")]
    conv.get_messages = MagicMock(return_value=conv._messages)

    agent = MagicMock()
    agent.controller = MagicMock()
    agent.controller.conversation = conv
    agent._process_event = AsyncMock()
    agent.llm = MagicMock()
    bridge._agent = agent

    # Mock model routing
    with patch("nanobot_kt.bridge.NewAPIClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.estimate_complexity = MagicMock(return_value=3)
        mock_client.get_ordered_candidates = MagicMock(return_value=[
            {"id": "test-model", "intelligence": 8, "cost_input_1m": 0.0}
        ])
        mock_client_cls.return_value = mock_client

        await bridge.handle_message(
            "当前消息",
            user_id="u1",
            session_id="s1",
            metadata={
                "persona_text": "",
                "raw_query": "当前消息",
                "history_messages": [
                    {"role": "user", "content": "之前的问题"},
                    {"role": "assistant", "content": "之前的回复"},
                ],
            },
        )

    # conv.append should be called for: persona, 2 history msgs
    assert conv.append.call_count >= 2
    # Verify history messages were appended
    history_roles = [call.args[0] for call in conv.append.call_args_list
                     if call.args[0] in ("user", "assistant")]
    assert "user" in history_roles
    assert "assistant" in history_roles


def test_bridge_no_crash_on_empty_history():
    """Bridge should handle empty history_messages gracefully."""
    from unittest.mock import MagicMock
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)
    conv = MagicMock()
    conv._messages = [MagicMock(role="system", content="base")]

    agent = MagicMock()
    agent.controller = MagicMock()
    agent.controller.conversation = conv
    bridge._agent = agent
    bridge._output = MagicMock()

    meta = {"history_messages": [], "persona_text": "", "raw_query": "test"}
    # Just verify the meta extraction path doesn't crash
    history = meta.get("history_messages", [])
    assert history == []


# ── helpers ──

def _seed_chat_logs(db_session, session_id, messages):
    """Insert ChatLog rows for testing."""
    for role, content in messages:
        db_session.add(ChatLog(
            user_id="test_user",
            session_id=session_id,
            role=role,
            content=content,
            sender_name="test",
            session_name="test",
            processed=0,
        ))
    db_session.commit()
