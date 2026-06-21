import logging
from pathlib import Path


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


def test_prompt_manager_missing_required_variable_warns_without_raising(tmp_path):
    from core.prompts import PromptManager

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

    rendered = manager.render("memory_extract", {})

    assert rendered.missing_required_vars == ["conversation"]
    assert any("conversation" in warning for warning in rendered.warnings)


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


def test_prompt_manager_default_fallback_records_source(tmp_path, monkeypatch):
    from core.prompts import PromptManager

    runtime_dir = tmp_path / "runtime_prompts"
    default_dir = tmp_path / "default_prompts"
    runtime_dir.mkdir()
    default_dir.mkdir()
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    (default_dir / "group_chat.md").write_text(
        """---
name: 默认群聊
required_vars:
  - user_input
---
默认 {{ user_input }}
""",
        encoding="utf-8",
    )

    manager = PromptManager(prompt_dir=runtime_dir, backup_dir=tmp_path / "backups")
    rendered = manager.render("group_chat", {"user_input": "你好"}, mode="managed")

    assert rendered.content.strip() == "默认 你好"
    assert rendered.prompt_source == "PromptManager default fallback"
    assert rendered.prompt_runtime_path == str(runtime_dir / "group_chat.md")
    assert rendered.prompt_default_path == str(default_dir / "group_chat.md")
    assert len(rendered.prompt_sha256) == 64

    (runtime_dir / "group_chat.md").write_text(
        """---
name: 运行时群聊
required_vars:
  - user_input
---
运行时 {{ user_input }}
""",
        encoding="utf-8",
    )
    rendered_runtime = manager.render("group_chat", {"user_input": "你好"}, mode="managed")
    assert rendered_runtime.content.strip() == "运行时 你好"
    assert rendered_runtime.prompt_source == "PromptManager runtime template"


def test_prompt_manager_logs_tracer_failure_without_failing_render(tmp_path, monkeypatch, caplog):
    from core.prompts import PromptManager

    prompt_dir = tmp_path / "prompts"
    write_template(
        prompt_dir,
        "group_chat.md",
        """---
name: 群聊回复
required_vars:
  - user_input
---
用户: {{ user_input }}
""",
    )

    def broken_record_render(**_kwargs):
        raise RuntimeError("trace boom")

    monkeypatch.setattr("core.tracing.PromptTracer.record_render", broken_record_render)
    manager = PromptManager(prompt_dir=prompt_dir, backup_dir=tmp_path / "backups")

    with caplog.at_level(logging.DEBUG, logger="nanobot.prompt_manager"):
        rendered = manager.render(
            "group_chat",
            {"user_input": "你好"},
            trace_id="trace-1",
            run_id="run-1",
            mode="shadow",
        )

    assert "用户: 你好" in rendered.content
    assert "trace boom" in caplog.text
    assert "trace-1" in caplog.text
    assert "run-1" in caplog.text
