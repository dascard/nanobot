from types import SimpleNamespace


def test_extract_reply_tool_output_returns_reply_meta():
    from nanobot_kt.reply_contract import extract_reply_tool_output

    result = extract_reply_tool_output([
        SimpleNamespace(role="tool", content='{"NANOBOT_REPLY_OUTPUT": {"content": " 你好。", "send_mode": "quote", "mentions": ["123", "bad"]}}'),
    ])

    assert result.reply_text == "你好。"
    assert result.no_reply is False
    assert result.reply_meta == {
        "reply_to_message_id": None,
        "mentions": ["123"],
        "quote": False,
        "at_sender": False,
        "send_mode": "quote",
    }


def test_extract_reply_tool_output_returns_no_reply():
    from nanobot_kt.reply_contract import extract_reply_tool_output

    result = extract_reply_tool_output([
        {"role": "tool", "content": '{"NANOBOT_REPLY_OUTPUT": {"no_reply": true, "reason": "无需插话"}}'},
    ])

    assert result.reply_text == ""
    assert result.no_reply is True
    assert result.no_reply_reason == "无需插话"


def test_parse_structured_final_action_rejects_embedded_json_and_markdown():
    from nanobot_kt.reply_contract import parse_structured_final_action

    assert parse_structured_final_action('{"action": "reply", "content": "你好"}')["content"] == "你好"
    assert parse_structured_final_action('前缀 {"action": "reply", "content": "你好"}') is None
    assert parse_structured_final_action('```json\n{"action": "reply", "content": "你好"}\n```') is None
    assert parse_structured_final_action('{"action": "reply", "content": ""}') is None


def test_detect_agent_result_flags_fake_tool_claims():
    from nanobot_kt.reply_contract import detect_no_tool_call_result

    assert detect_no_tool_call_result("我已经调用 reply 工具发送了") == "fake_tool_call_claim"
    assert detect_no_tool_call_result("普通文本") == "no_tool_call"


def test_count_final_action_tool_calls_counts_real_markers_only():
    from nanobot_kt.reply_contract import count_final_action_tool_calls

    counts = count_final_action_tool_calls([
        {"role": "assistant", "content": "我会调用 reply 工具"},
        {"role": "tool", "content": '{"NANOBOT_REPLY_OUTPUT": {"content": "第一次"}}'},
        {"role": "tool", "content": '{"NANOBOT_REPLY_OUTPUT": {"content": "第二次"}}'},
        {"role": "tool", "content": '{"NANOBOT_REPLY_OUTPUT": {"no_reply": true, "reason": "不插话"}}'},
        {"role": "tool", "content": '{"action": "reply", "content": "fallback"}'},
    ])

    assert counts == {
        "reply_tool_call_count": 2,
        "no_reply_tool_call_count": 1,
        "structured_fallback_count": 1,
        "total_final_action_count": 4,
    }
