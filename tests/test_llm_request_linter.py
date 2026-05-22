import json


def test_linter_extracts_runtime_tool_and_internal_message_issues():
    from core.llm_request_linter import lint_llm_request

    request = {
        "model": "test-model",
        "messages": [
            {
                "role": "system",
                "content": "[RuntimeTool]\n本轮可调用工具（1个）：\n  - reply：回复\n已禁用工具（1个）：\n  - python_sandbox：测试禁用\n规则：只调用可调用工具。",
            },
            {
                "role": "system",
                "content": "<history_context>\n对话历史说明\n</history_context>",
            },
            {
                "role": "system",
                "content": "<conversation_context>\n统一上下文说明\n</conversation_context>",
            },
            {
                "role": "system",
                "content": "<group_recent_context>\n[msg_id]1\n</group_recent_context>",
            },
            {"role": "system", "content": "## Available Functions\n- `python_sandbox`: execute python"},
            {"role": "user", "content": "[Tool None completed]"},
            {"role": "user", "content": "<reply_contract_retry>\n你刚才没有调用 reply 或 no_reply 工具\n这轮必须只调用一个工具\n</reply_contract_retry>"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "reply", "parameters": {}}},
            {"type": "function", "function": {"name": "python_sandbox", "parameters": {}}},
        ],
    }

    result = lint_llm_request(request)

    assert result["actual_sent_tools"] == ["reply", "python_sandbox"]
    assert result["runtime_enabled_tools"] == ["reply"]
    assert result["runtime_disabled_tools"] == ["python_sandbox"]
    assert result["framework_injected_tools"] == ["available_functions"]
    codes = {issue["code"] for issue in result["issues"]}
    assert "disabled_tool_sent" in codes
    assert "runtime_tool_mismatch" in codes
    assert "kt_framework_tool_docs" in codes
    assert "internal_tool_message_as_user" in codes
    assert "reply_retry_as_user" not in codes
    assert result["severity_counts"]["P0"] >= 3
    assert result["message_sources"][1]["source"] == "history_context_header"
    assert result["message_sources"][2]["source"] == "conversation_context_header"
    assert result["message_sources"][3]["source"] == "group_recent_context"
    assert result["message_sources"][4]["source"] == "kt_framework_tools_doc"
    assert result["message_sources"][6]["source"] == "reply_contract_retry"


def test_record_request_persists_request_lint_fields(db_session):
    from core.database import LLMApiRequestLog
    from core.tracing import LLMRequestTracer

    log_id = LLMRequestTracer.record_request(
        trace_id="trace-lint",
        run_id="run-lint",
        source="replyer",
        provider="newapi",
        model="model-lint",
        url="http://llm.test/v1/chat/completions",
        request={
            "model": "model-lint",
            "messages": [
                {
                    "role": "system",
                    "content": "[RuntimeTool]\n本轮可调用工具（1个）：\n  - reply：回复",
                },
                {"role": "user", "content": "你好"},
            ],
            "tools": [{"type": "function", "function": {"name": "reply"}}],
        },
    )

    row = db_session.query(LLMApiRequestLog).filter_by(id=log_id).one()
    assert json.loads(row.actual_sent_tools_json) == ["reply"]
    assert json.loads(row.runtime_enabled_tools_json) == ["reply"]
    assert json.loads(row.runtime_disabled_tools_json) == []
    lint = json.loads(row.request_lint_json)
    assert lint["actual_sent_tools"] == ["reply"]
    assert lint["runtime_enabled_tools"] == ["reply"]
    sources = json.loads(row.message_sources_json)
    assert sources[0]["source"] == "runtime_tool"


def test_linter_accepts_schema_authoritative_runtime_tool():
    from core.llm_request_linter import lint_llm_request

    request = {
        "model": "test-model",
        "messages": [
            {
                "role": "system",
                "content": (
                    "[RuntimeTool]\n"
                    "本轮真实可调用工具以 API tools schema 为准，本段只做说明和审计。\n"
                    "已禁用工具（1个）：\n"
                    "  - python_sandbox：群聊强制禁用\n"
                    "规则：不要声称调用未出现在 tools schema 中的工具。\n"
                    "如需回复，必须真实调用 reply(content)；不回复则调用 no_reply(reason)。"
                ),
            },
            {"role": "user", "content": "你好"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "reply", "parameters": {}}},
        ],
    }

    result = lint_llm_request(request)

    assert result["actual_sent_tools"] == ["reply"]
    assert result["runtime_enabled_tools"] == ["reply"]
    assert result["runtime_disabled_tools"] == ["python_sandbox"]
    codes = {issue["code"] for issue in result["issues"]}
    assert "runtime_tool_mismatch" not in codes


def test_linter_flags_nanobot_request_without_current_user_input():
    from core.llm_request_linter import lint_llm_request

    request = {
        "model": "test-model",
        "messages": [
            {
                "role": "system",
                "content": "<runtime_context>\nchat_type: group\n</runtime_context>",
            },
            {
                "role": "system",
                "content": (
                    "<conversation_context>\n"
                    "下面紧随的是历史消息\n"
                    "</conversation_context>"
                ),
            },
            {
                "role": "user",
                "content": "[msg_id]old\n[发言内容]上一轮消息",
            },
        ],
        "tools": [],
    }

    result = lint_llm_request(request)

    codes = {issue["code"] for issue in result["issues"]}
    assert "missing_current_user_input" in codes


def test_linter_accepts_nanobot_request_with_current_user_input():
    from core.llm_request_linter import lint_llm_request

    request = {
        "model": "test-model",
        "messages": [
            {
                "role": "system",
                "content": "<runtime_context>\nchat_type: group\n</runtime_context>",
            },
            {
                "role": "user",
                "content": "<user_input>\n当前消息\n</user_input>",
            },
        ],
        "tools": [],
    }

    result = lint_llm_request(request)

    codes = {issue["code"] for issue in result["issues"]}
    assert "missing_current_user_input" not in codes


def test_linter_classifies_runtime_context_sources():
    from core.llm_request_linter import infer_message_sources

    sources = infer_message_sources([
        {"role": "system", "content": "## 交互定位\n\n含 <persona_reference> 的说明"},
        {"role": "system", "content": "<identity_context>\n</identity_context>"},
        {"role": "system", "content": "<persona_reference user_id=\"u\">\n</persona_reference>"},
        {"role": "system", "content": "## 私聊行为\n\n规则"},
        {"role": "system", "content": "本轮认真处理。可以使用工具"},
    ])

    assert [item["source"] for item in sources] == [
        "base_system_prompt",
        "identity_context",
        "persona_reference",
        "private_behavior",
        "effort_constraint",
    ]
