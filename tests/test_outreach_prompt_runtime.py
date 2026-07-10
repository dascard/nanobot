from pathlib import Path

import pytest


OUTREACH_TASK_KEYS = (
    "outreach_extract",
    "outreach_judge",
    "outreach_generate",
    "proactive_research",
)


@pytest.mark.parametrize("task_key", OUTREACH_TASK_KEYS)
def test_outreach_task_templates_exist_in_default_and_runtime(
    task_key,
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    runtime_dir = tmp_path / "prompt_runtime"
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    init_prompt_v2_runtime_dir()
    default_path = Path("prompts.v2.default/tasks") / f"{task_key}.md"
    runtime_path = runtime_dir / "tasks" / f"{task_key}.md"

    assert default_path.exists()
    assert runtime_path.exists()
    assert default_path.read_text(encoding="utf-8") == runtime_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("task_key", OUTREACH_TASK_KEYS)
def test_outreach_task_templates_are_registered_and_variable_safe(task_key):
    from core.prompt_v2.template_loader import load_template
    from core.prompt_v2.template_registry import classify_template, list_template_keys
    from core.prompt_v2.variables import validate_scoped_template

    canonical_key = f"tasks/{task_key}"
    assert canonical_key in list_template_keys()
    template = load_template(task_key)
    validate_scoped_template(template.prompt_key, template.body)
    record = classify_template(template.prompt_key, template.frontmatter)
    assert record.kind == "task"


def test_outreach_judge_prompt_declares_strict_contract_and_research_choice():
    from core.prompt_v2.template_loader import load_template

    body = load_template("outreach_judge").body
    assert "should_reach_out" in body
    assert "next_check_in_hours" in body
    assert "outreach_kind" in body
    assert "research_query" in body
    assert "只输出" in body and "JSON" in body


def test_proactive_research_prompt_requires_real_tools_and_untrusted_web_content():
    from core.prompt_v2.template_loader import load_template

    body = load_template("proactive_research").body
    assert "web_search" in body
    assert "knowledge_query" not in body
    assert "memory_query" not in body
    assert "不可信" in body
    assert "真实" in body and "来源" in body
    assert "context_summary" in body and "私密内容" in body
    assert "工具 query" in body
    assert "裸域名" in body
    assert "{{ pending_text }}" in body


@pytest.mark.parametrize("task_key", OUTREACH_TASK_KEYS)
def test_outreach_templates_treat_runtime_content_as_untrusted_data(task_key):
    from core.prompt_v2.template_loader import load_template

    body = load_template(task_key).body
    assert "系统提示" in body
    assert "资料" in body or "素材" in body
