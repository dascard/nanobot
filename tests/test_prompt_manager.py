from pathlib import Path

import pytest


def write_template(root: Path, name: str, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(content, encoding="utf-8")


def test_prompt_manager_renders_frontmatter_variables_and_warnings(tmp_path):
    from core.prompts import PromptManager

    prompt_dir = tmp_path / "prompts"
    backup_dir = tmp_path / "backups"
    write_template(
        prompt_dir,
        "group_chat.md",
        """---
name: 群聊回复
version: 1
description: 群聊入口
required_vars:
  - user_input
optional_vars:
  - history_context
---
历史:
{{ history_context }}

用户: {{ user_input }}
""",
    )

    manager = PromptManager(prompt_dir=prompt_dir, backup_dir=backup_dir)
    rendered = manager.render(
        "group_chat",
        {"user_input": "你好", "history_context": "上一轮", "extra": "unused"},
        trace_id="trace-1",
        run_id="run-1",
        mode="shadow",
    )

    assert rendered.prompt_key == "group_chat"
    assert rendered.mode == "shadow"
    assert "用户: 你好" in rendered.content
    assert "上一轮" in rendered.content
    assert rendered.messages == [{"role": "system", "content": rendered.content}]
    assert "extra" in rendered.warnings[0]
    assert rendered.required_vars == ["user_input"]


def test_prompt_manager_missing_required_variable_raises(tmp_path):
    from core.prompts import PromptManager, PromptRenderError

    prompt_dir = tmp_path / "prompts"
    write_template(
        prompt_dir,
        "memory_extract.md",
        """---
name: 记忆提取
required_vars:
  - conversation
---
{{ conversation }}
""",
    )

    manager = PromptManager(prompt_dir=prompt_dir, backup_dir=tmp_path / "backups")

    with pytest.raises(PromptRenderError) as exc:
        manager.render("memory_extract", {})

    assert "conversation" in str(exc.value)


def test_prompt_manager_save_creates_backup_and_history(tmp_path):
    from core.prompts import PromptManager

    prompt_dir = tmp_path / "prompts"
    backup_dir = tmp_path / "backups"
    write_template(
        prompt_dir,
        "timing_gate.md",
        """---
name: Timing Gate
required_vars:
  - pending_text
---
旧内容 {{ pending_text }}
""",
    )

    manager = PromptManager(prompt_dir=prompt_dir, backup_dir=backup_dir)
    result = manager.save_prompt(
        "timing_gate",
        """---
name: Timing Gate
required_vars:
  - pending_text
---
新内容 {{ pending_text }}
""",
        operator="tester",
    )

    assert result["saved"] is True
    assert result["backup_name"]
    backups = manager.history("timing_gate")
    assert len(backups) == 1
    assert backups[0]["prompt_key"] == "timing_gate"
    assert "旧内容" in (backup_dir / backups[0]["name"]).read_text(encoding="utf-8")


def test_prompt_manager_reload_picks_up_file_change(tmp_path):
    from core.prompts import PromptManager

    prompt_dir = tmp_path / "prompts"
    write_template(
        prompt_dir,
        "sql_analysis.md",
        """---
name: SQL
required_vars:
  - question
---
旧 {{ question }}
""",
    )

    manager = PromptManager(prompt_dir=prompt_dir, backup_dir=tmp_path / "backups")
    assert "旧 问题" in manager.render("sql_analysis", {"question": "问题"}).content

    write_template(
        prompt_dir,
        "sql_analysis.md",
        """---
name: SQL
required_vars:
  - question
---
新 {{ question }}
""",
    )
    manager.reload()

    assert "新 问题" in manager.render("sql_analysis", {"question": "问题"}).content


def test_bridge_prompt_render_legacy_and_failure_fallback(tmp_path, monkeypatch):
    from nanobot_kt.bridge import NanobotBridge
    import core.prompts.manager as prompt_manager_module

    prompt_dir = tmp_path / "empty_prompts"
    prompt_dir.mkdir()
    monkeypatch.setenv("NANOBOT_PROMPT_DIR", str(prompt_dir))
    prompt_manager_module._MANAGER = None

    bridge = NanobotBridge()

    assert bridge._render_runtime_prompt(
        prompt_key="missing",
        mode="legacy",
        variables={},
        trace_id="trace",
        run_id="run",
    ) == ""
    assert bridge._render_runtime_prompt(
        prompt_key="missing",
        mode="shadow",
        variables={},
        trace_id="trace",
        run_id="run",
    ) == ""
