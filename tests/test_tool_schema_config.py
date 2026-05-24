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
