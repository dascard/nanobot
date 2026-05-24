from pathlib import Path


def _write_tool_template(base: Path, key: str, tool_name: str, body: str) -> None:
    path = base / f"{key}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {key}\nversion: 1\nkind: tool\ntool_name: {tool_name}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_runtime_tool_prompt_lists_v2_tool_template_refs_without_body(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    _write_tool_template(default_dir, "tools/sql_analysis/usage", "sql_analysis", "V2 SQL TEMPLATE MARKER")
    _write_tool_template(default_dir, "tools/python_sandbox/usage", "python_sandbox", "DISABLED TEMPLATE MARKER")
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.runtime_tool_service import build_runtime_tool_prompt

    prompt = build_runtime_tool_prompt(
        enabled={"sql_analysis": True, "python_sandbox": False, "reply": True},
        disabled={"python_sandbox": "测试禁用"},
        chat_type="private",
    )

    assert "[V2ToolTemplateRef:sql_analysis]" in prompt
    assert "工具模板正文已写入 tools schema description" in prompt
    assert "V2 SQL TEMPLATE MARKER" not in prompt
    assert "DISABLED TEMPLATE MARKER" not in prompt
    assert "python_sandbox：测试禁用" in prompt


def test_outgoing_tool_schema_description_uses_v2_tool_template(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    _write_tool_template(default_dir, "tools/sql_analysis/usage", "sql_analysis", "V2 SCHEMA TEMPLATE MARKER")
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.final_tools import FinalToolSet, filter_payload_tools

    payload = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "sql_analysis",
                    "description": "hardcoded description",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    }

    result = filter_payload_tools(payload, FinalToolSet(allowed={"sql_analysis"}, disabled={}))
    description = result["tools"][0]["function"]["description"]

    assert "hardcoded description" in description
    assert "V2 SCHEMA TEMPLATE MARKER" in description


def test_group_analysis_internal_llm_uses_v2_templates(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    _write_tool_template(
        default_dir,
        "tools/group_analysis/system",
        "group_analysis",
        "V2 GROUP SYSTEM MARKER",
    )
    _write_tool_template(
        default_dir,
        "tools/group_analysis/topics",
        "group_analysis",
        "V2 TOPIC TEMPLATE MARKER\n{{ messages_text }}\n{{ instructions }}",
    )
    _write_tool_template(
        default_dir,
        "tools/group_analysis/titles",
        "group_analysis",
        "用户发言统计\n{{ users_text }}\n{{ messages_text }}",
    )
    _write_tool_template(
        default_dir,
        "tools/group_analysis/quotes",
        "group_analysis",
        "群聊金句\n{{ messages_text }}",
    )
    _write_tool_template(
        default_dir,
        "tools/group_analysis/quality",
        "group_analysis",
        "聊天质量\n{{ messages_text }}",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from creatures.nanobot.prompts.skills.group_analysis import analyzer

    captured: list[tuple[str, str]] = []

    async def fake_call(_client, system_prompt, prompt, max_retries=2, **kwargs):
        captured.append((system_prompt, prompt))
        if "V2 TOPIC TEMPLATE MARKER" in prompt:
            return '{"topics":[{"topic":"模板生效","contributors":["A"],"detail":"ok"}]}'
        if "用户发言统计" in prompt:
            return '{"users":[]}'
        if "聊天质量" in prompt:
            return '{"title":"ok","subtitle":"","dimensions":[],"summary":"ok"}'
        return '{"quotes":[]}'

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr("clients.new_api_client.NewAPIClient", DummyClient)
    monkeypatch.setattr(analyzer, "_call_llm_with_retry", fake_call)

    import asyncio

    result = asyncio.run(
        analyzer.analyze_group(
            {
                "msg_text": "[12:00] [A]: 今天聊 AI",
                "style_msg_text": "[12:00] [A]: 今天聊 AI",
                "users_text": "A | 1 | 8 | 0 | 0",
            },
            instructions="只看 AI",
        )
    )

    assert result["topics"]["topics"][0]["topic"] == "模板生效"
    assert captured[0][0] == "V2 GROUP SYSTEM MARKER"
    assert "V2 TOPIC TEMPLATE MARKER" in captured[0][1]
    assert "今天聊 AI" in captured[0][1]
    assert "只看 AI" in captured[0][1]


def test_tool_execution_template_runtime_body_keeps_default_frontmatter(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    _write_tool_template(
        default_dir,
        "tools/group_analysis/topics",
        "group_analysis",
        "DEFAULT TOPIC {{ messages_text }}",
    )
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "tools" / "group_analysis").mkdir(parents=True, exist_ok=True)
    (runtime_dir / "tools" / "group_analysis" / "topics.md").write_text(
        "RUNTIME TOPIC {{ messages_text }}",
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.tool_templates import render_tool_execution_template

    rendered = render_tool_execution_template(
        "tools/group_analysis/topics",
        {"messages_text": "消息正文"},
        fallback="FALLBACK",
        expected_tool_name="group_analysis",
    )

    assert rendered == "RUNTIME TOPIC 消息正文"


def test_ai_daily_digest_and_image_internal_prompts_use_v2_templates(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    _write_tool_template(default_dir, "tools/ai_daily/digest_system", "ai_daily", "V2 DAILY DIGEST SYSTEM")
    _write_tool_template(
        default_dir,
        "tools/ai_daily/digest_user",
        "ai_daily",
        "V2 DAILY DIGEST USER\n{{ evidence_cards }}\n{{ mode_hint }}",
    )
    _write_tool_template(default_dir, "tools/image_summary/system", "image_summary", "V2 IMAGE SYSTEM")
    _write_tool_template(
        default_dir,
        "tools/image_summary/user",
        "image_summary",
        "V2 IMAGE USER {{ image_count }} {{ focus }}",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from creatures.nanobot.prompts.skills.image_summary.tool import ImageSummaryTool, _build_multimodal_content
    from creatures.nanobot.prompts.skills.news_search.prompts import build_evidence_prompt, get_system_prompt

    assert get_system_prompt() == "V2 DAILY DIGEST SYSTEM"
    news_prompt = build_evidence_prompt(
        [
            {
                "source_id": 1,
                "title": "测试新闻",
                "domain": "example.com",
                "claims": ["发布新模型"],
            }
        ],
        mode="fast",
    )
    assert "V2 DAILY DIGEST USER" in news_prompt
    assert "测试新闻" in news_prompt
    assert "生成 2-3 条 highlights" in news_prompt

    assert ImageSummaryTool._system_prompt() == "V2 IMAGE SYSTEM"
    monkeypatch.setattr(
        "creatures.nanobot.prompts.skills.image_summary.tool.prepare_image_parts",
        lambda *args, **kwargs: [],
    )
    image_content = _build_multimodal_content(["https://example.com/a.png"], "OCR")
    assert "V2 IMAGE USER 1 OCR" in str(image_content)


def test_ai_daily_quality_prompts_use_v2_templates(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    _write_tool_template(default_dir, "tools/ai_daily/quality_system", "ai_daily", "V2 DAILY SYSTEM")
    _write_tool_template(
        default_dir,
        "tools/ai_daily/quality_user",
        "ai_daily",
        "V2 DAILY USER {{ card_count }}\n{{ candidate_cards }}",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from creatures.nanobot.prompts.skills.news_search.news_daily.pipeline.summarize_quality import (
        build_quality_prompt,
        get_quality_system_prompt,
    )

    assert get_quality_system_prompt() == "V2 DAILY SYSTEM"
    prompt = build_quality_prompt([
        {
            "source_id": 1,
            "title": "日报候选",
            "source_name": "测试源",
            "claims": ["发布新 API"],
        }
    ])
    assert "V2 DAILY USER 1" in prompt
    assert "日报候选" in prompt
