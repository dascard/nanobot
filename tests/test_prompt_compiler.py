import json


def test_prompt_compiler_builds_single_active_message_stack(tmp_path, monkeypatch):
    from core.prompts import PromptManager
    from core.prompt_compiler import PromptCompiler, PromptContext

    prompt_dir = tmp_path / "prompts"
    backup_dir = tmp_path / "backups"
    prompt_dir.mkdir()
    backup_dir.mkdir()
    (prompt_dir / "group_chat.md").write_text(
        """---
name: 群聊回复
optional_vars:
  - user_input
  - history_context
  - persona_text
  - runtime_tool_prompt
  - sender_name
  - session_id
  - runtime_context
  - identity_context
  - persona_reference
---
统一群聊模板 ACTIVE_TEMPLATE_MARKER
必须通过 reply/no_reply 结束。
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "core.prompt_compiler.get_prompt_manager",
        lambda: PromptManager(prompt_dir=prompt_dir, backup_dir=backup_dir),
    )

    compiled = PromptCompiler().compile(
        PromptContext(
            chat_type="group",
            prompt_key="group_chat",
            session_id="group_1001",
            user_id="group_1001",
            group_id="1001",
            sender_name="雀",
            sender_id="0000000000",
            bot_name="nanobot",
            bot_aliases=["bot"],
            user_input="[msg_id]m1\n[发言内容]当前问题",
            current_message_id="m1",
            persona_text="画像文本",
            history_header="<conversation_context>\n历史说明\n</conversation_context>",
            history_messages=[
                {"role": "user", "content": "[msg_id]old\n[发言内容]旧问题"},
                {"role": "assistant", "content": "[发言内容]旧回复"},
            ],
            runtime_tool_prompt="[RuntimeTool]\n  - reply：回复",
            effort_constraint="本轮简短处理。",
        ),
        trace_id="trace-1",
        run_id="run-1",
    )

    contents = [str(m["content"]) for m in compiled.messages]
    joined = "\n".join(contents)
    assert "ACTIVE_TEMPLATE_MARKER" in contents[0]
    assert compiled.messages[-1]["role"] == "user"
    assert compiled.messages[-1]["content"].startswith("<user_input>")
    assert "当前问题" in compiled.messages[-1]["content"]
    assert sum("<user_input>" in c for c in contents) == 1
    assert sum("[RuntimeTool]" in c for c in contents) == 1
    assert "{{" not in joined
    assert compiled.pre_event_messages == compiled.messages[:-1]
    assert compiled.event_content == compiled.messages[-1]["content"]
    assert [m["role"] for m in compiled.messages if "旧问题" in str(m["content"])] == ["user"]
    assert compiled.prompt_source == "PromptManager runtime template"
    assert len(compiled.prompt_sha256) == 64
    assert json.loads(json.dumps(compiled.to_dict(), ensure_ascii=False))["prompt_key"] == "group_chat"


def test_prompt_compiler_private_superuser_uses_private_template(tmp_path, monkeypatch):
    from core.prompts import PromptManager
    from core.prompt_compiler import PromptCompiler, PromptContext

    prompt_dir = tmp_path / "prompts"
    backup_dir = tmp_path / "backups"
    prompt_dir.mkdir()
    backup_dir.mkdir()
    (prompt_dir / "private_chat.md").write_text(
        "---\nname: 私聊回复\n---\nPRIVATE_TEMPLATE_MARKER\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "core.prompt_compiler.get_prompt_manager",
        lambda: PromptManager(prompt_dir=prompt_dir, backup_dir=backup_dir),
    )

    compiled = PromptCompiler().compile(
        PromptContext(
            chat_type="private_superuser",
            session_id="private_0000000000",
            user_id="0000000000",
            sender_name="雀",
            user_input="你好",
        )
    )

    assert "PRIVATE_TEMPLATE_MARKER" in compiled.messages[0]["content"]
    assert compiled.prompt_key == "private_chat"
