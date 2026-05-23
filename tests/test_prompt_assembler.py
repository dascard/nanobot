import json


def test_prompt_assembler_is_marked_v1_fallback_only():
    import core.prompt_assembler as prompt_assembler

    assert prompt_assembler.IS_V1_FALLBACK_ONLY is True
    assert "deprecated" in prompt_assembler.DEPRECATED_REASON.lower()
    assert "V1 rollback" in prompt_assembler.DEPRECATED_REASON


def _write_template(prompt_dir, name: str, body: str) -> None:
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / name).write_text(body, encoding="utf-8")


def test_prompt_assembler_managed_messages_do_not_duplicate_dynamic_context(tmp_path, monkeypatch):
    from core.prompt_assembler import PromptAssembler, PromptBuildContext
    from core.prompts import PromptManager

    prompt_dir = tmp_path / "prompts"
    backup_dir = tmp_path / "backups"
    _write_template(
        prompt_dir,
        "group_chat.md",
        """---
name: 群聊回复
optional_vars:
  - runtime_context
  - history_context
  - persona_text
  - runtime_tool_prompt
  - user_input
---
统一群聊规则 ACTIVE_TEMPLATE_MARKER。
必须通过 reply(content) 或 no_reply(reason) 结束。
""",
    )
    monkeypatch.setattr(
        "core.prompt_assembler.get_prompt_manager",
        lambda: PromptManager(prompt_dir=prompt_dir, backup_dir=backup_dir),
    )

    result = PromptAssembler().build(
        PromptBuildContext(
            mode="managed",
            chat_type="group",
            prompt_key="group_chat",
            session_id="group_1001",
            user_id="group_1001",
            group_id="1001",
            sender_name="雀",
            sender_id="0000000000",
            bot_name="nanobot",
            bot_aliases=["bot"],
            user_input="当前问题",
            current_message_id="m1",
            persona_text="画像文本",
            history_header="<conversation_context>\n历史说明\n</conversation_context>",
            history_messages=[
                {"role": "user", "content": "[msg_id]old\n[发言内容]旧问题"},
                {"role": "assistant", "content": "[发言内容]旧回复"},
            ],
            runtime_tool_prompt="[RuntimeTool]\n- reply：回复\n- no_reply：沉默",
            effort_constraint="本轮简短处理。",
            tool_schemas=[{"type": "function", "function": {"name": "reply"}}],
        ),
        trace_id="trace-1",
        run_id="run-1",
    )

    contents = [str(m["content"]) for m in result.messages]
    joined = "\n".join(contents)
    assert result.messages == result.managed_messages
    assert result.request_json["messages"] == result.messages
    assert result.request_json["tools"] == result.tool_schemas
    assert "ACTIVE_TEMPLATE_MARKER" in contents[0]
    assert result.messages[-1]["role"] == "user"
    assert result.messages[-1]["content"].startswith("<user_input>")
    assert sum("<user_input>" in c for c in contents) == 1
    assert sum("<persona_reference" in c for c in contents) == 1
    assert sum("<runtime_context>" in c for c in contents) == 1
    assert sum("[RuntimeTool]" in c for c in contents) == 1
    assert sum("<conversation_context>" in c for c in contents) == 1
    assert "{{" not in joined
    assert result.pre_event_messages == result.messages[:-1]
    assert result.event_content == result.messages[-1]["content"]
    assert [m["role"] for m in result.messages if "旧问题" in str(m["content"])] == ["user"]
    assert result.prompt_source == "PromptManager runtime template"
    assert len(result.prompt_sha256) == 64
    assert json.loads(json.dumps(result.to_dict(), ensure_ascii=False))["prompt_key"] == "group_chat"


def test_prompt_assembler_shadow_sends_legacy_and_compares_managed(tmp_path, monkeypatch):
    from core.prompt_assembler import PromptAssembler, PromptBuildContext
    from core.prompts import PromptManager

    prompt_dir = tmp_path / "prompts"
    backup_dir = tmp_path / "backups"
    _write_template(
        prompt_dir,
        "private_chat.md",
        "---\nname: 私聊回复\n---\nMANAGED_TEMPLATE_MARKER\nreply(content) / no_reply(reason)\n",
    )
    monkeypatch.setattr(
        "core.prompt_assembler.get_prompt_manager",
        lambda: PromptManager(prompt_dir=prompt_dir, backup_dir=backup_dir),
    )
    monkeypatch.setattr(
        "core.prompt_assembler.read_runtime_or_default_prompt",
        lambda: {
            "content": "LEGACY_TEMPLATE_MARKER\nreply(content) / no_reply(reason)",
            "source": "runtime",
            "output_path": "/runtime/prompt.md",
            "default_path": "/default/prompt.md",
        },
    )

    result = PromptAssembler().build(
        PromptBuildContext(
            mode="shadow",
            chat_type="private",
            session_id="private_u1",
            user_id="u1",
            sender_name="雀",
            user_input="你好",
        )
    )

    active_text = "\n".join(str(m["content"]) for m in result.messages)
    managed_text = "\n".join(str(m["content"]) for m in result.managed_messages)
    assert "LEGACY_TEMPLATE_MARKER" in active_text
    assert "MANAGED_TEMPLATE_MARKER" not in active_text
    assert "MANAGED_TEMPLATE_MARKER" in managed_text
    assert result.legacy_prompt_sha256
    assert result.managed_prompt_sha256
    assert result.prompt_source == "Legacy runtime prompt"
    assert result.diff["messages_equal"] is False


def test_default_managed_templates_keep_group_private_rules_separate():
    group_text = open("prompts.default/group_chat.md", "r", encoding="utf-8").read()
    private_text = open("prompts.default/private_chat.md", "r", encoding="utf-8").read()

    assert "reply(content)" in group_text
    assert "no_reply(reason" in group_text
    assert "## 群聊行为" in group_text
    assert "## 群聊发言时机" in group_text
    assert "## 私聊行为" not in group_text

    assert "reply(content)" in private_text
    assert "no_reply(reason" in private_text
    assert "## 私聊行为" in private_text
    assert "## 群聊行为" not in private_text
    assert "## 群聊发言时机" not in private_text
