def _custom_reply_schema(description="自定义 reply schema"):
    return {
        "type": "function",
        "function": {
            "name": "reply",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "最终回复正文",
                    },
                    "tone": {
                        "type": "string",
                        "enum": ["plain", "warm"],
                    },
                },
                "required": ["content"],
            },
        },
    }


def test_tool_schema_override_applies_to_tool_plan(db_session):
    from core.tool_plan import build_tool_plan
    from core.tool_schema_preview import build_tool_schema, save_tool_schema_override

    save_tool_schema_override(db_session, "reply", _custom_reply_schema())
    db_session.commit()

    schema = build_tool_schema("reply", db=db_session)
    reply_fn = schema["function"]
    assert reply_fn["name"] == "reply"
    assert "tone" in reply_fn["parameters"]["properties"]
    assert schema["source"] == "runtime_override"

    plan = build_tool_plan(chat_type="private", runtime_preset="full", db=db_session)
    reply_schema = next(s for s in plan.sent_tool_schemas if s["function"]["name"] == "reply")
    assert "tone" in reply_schema["function"]["parameters"]["properties"]


def test_tool_schema_override_rejects_name_mismatch(db_session):
    import pytest
    from core.tool_schema_preview import save_tool_schema_override

    schema = _custom_reply_schema()
    schema["function"]["name"] = "no_reply"

    with pytest.raises(ValueError) as exc:
        save_tool_schema_override(db_session, "reply", schema)

    assert "function.name" in str(exc.value)


def test_news_search_is_not_exposed_as_tool_schema():
    import pytest

    from core.runtime_tool_service import resolve_effective_tools
    from core.tool_schema_preview import build_effective_tool_schemas, build_tool_schema_config

    enabled, _disabled = resolve_effective_tools(chat_type="private", runtime_preset="full")
    assert "ai_daily" in enabled
    assert "web_search" in enabled
    assert "news_search" not in enabled

    schemas = build_effective_tool_schemas({"ai_daily": True, "news_search": True})
    names = [schema["function"]["name"] for schema in schemas]
    assert names == ["ai_daily"]
    with pytest.raises(ValueError):
        build_tool_schema_config(None, "news_search")


def test_web_search_tool_schema_is_exposed():
    from core.tool_registry import get_tool_def
    from core.tool_schema_preview import build_tool_schema

    tool_def = get_tool_def("web_search")
    assert tool_def is not None
    assert "搜索" in tool_def.description

    schema = build_tool_schema("web_search")
    assert schema["function"]["name"] == "web_search"
    props = schema["function"]["parameters"]["properties"]
    assert {"query", "limit", "provider"} <= set(props)
    assert "第一个相关结果" in props["provider"]["description"]
    assert "低相关" in props["provider"]["description"]
    assert schema["category"] == "data"


def test_memory_query_description_declares_unsummarized_window_boundary():
    from core.tool_registry import get_tool_def
    from core.tool_schema_preview import build_tool_schema

    tool_def = get_tool_def("memory_query")
    assert tool_def is not None
    assert "未摘要" in tool_def.description
    assert "sql_analysis" in tool_def.description

    schema = build_tool_schema("memory_query")
    description = schema["function"]["description"]
    assert "未摘要" in description
    assert "sql_analysis" in description
    props = schema["function"]["parameters"]["properties"]
    assert props["source"]["enum"] == ["digest", "session_summary", "all"]
