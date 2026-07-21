import os
from pathlib import Path
from unittest.mock import patch

import pytest


OUTREACH_TASK_KEYS = (
    "outreach_extract",
    "outreach_judge",
    "outreach_generate",
    "proactive_research",
)


@pytest.fixture(scope="module")
def outreach_runtime_dir(tmp_path_factory):
    """整组模板断言共享一次完整 Prompt Runtime 初始化。"""

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    runtime_dir = tmp_path_factory.mktemp("outreach_prompt_runtime")
    with patch.dict(
        os.environ,
        {"NANOBOT_PROMPT_RUNTIME_DIR": str(runtime_dir)},
    ):
        init_prompt_v2_runtime_dir()
    return runtime_dir


@pytest.mark.parametrize("task_key", OUTREACH_TASK_KEYS)
def test_outreach_task_templates_exist_in_default_and_runtime(
    task_key,
    outreach_runtime_dir,
):
    default_path = Path("prompts.v2.default/tasks") / f"{task_key}.md"
    runtime_path = outreach_runtime_dir / "tasks" / f"{task_key}.md"

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


@pytest.mark.asyncio
async def test_build_prompt_runtime_strict_compiles_internal_private(monkeypatch):
    from core.prompt_v2.compiler import compile_prompt_plan as real_compile_prompt_plan
    from nanobot_kt.prompt_runtime import PromptRuntimeInput, build_prompt_runtime

    captured_plans = []

    async def capture_real_compile(request, *, strict_audit=True):
        assert strict_audit is True
        plan = await real_compile_prompt_plan(request, strict_audit=strict_audit)
        captured_plans.append(plan)
        return plan

    monkeypatch.setattr(
        "core.prompt_v2.compiler.compile_prompt_plan",
        capture_real_compile,
    )
    monkeypatch.setattr(
        "core.tracing.PromptTracer.record_render",
        lambda **_kwargs: None,
    )

    result = await build_prompt_runtime(
        PromptRuntimeInput(
            prompt_engine="prompt",
            prompt_mode="prompt",
            prompt_key="chat_private",
            chat_type="private",
            runtime_chat_type="private",
            platform="internal",
            session_id="research_runtime-v2",
            user_id="research-user",
            group_id="",
            sender_name="主动研究任务",
            sender_id="research-user",
            session_name="主动研究",
            trigger_reason="proactive_research",
            timing_decision="continue",
            current_message_id="research-runtime-v2",
            source_message_ids=[],
            self_id="",
            bot_id="",
            bot_name="Nanobot",
            bot_aliases=[],
            user_input="调查 Prompt Flow 的内部研究路径",
            persona_text="无已存储画像",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="[RuntimeTool]\n只允许 web_search/reply/no_reply",
            effort_constraint="",
            trace_id="trace-research-runtime-v2",
            run_id="run-research-runtime-v2",
        )
    )

    assert result.prompt_key == "chat_private"
    assert len(captured_plans) == 1
    assert captured_plans[0].platform == "internal"
    node_ids = [section["node_id"] for section in captured_plans[0].flow_sections]
    assert "base_contract" in node_ids
    assert "private_policy" in node_ids
    assert "qq_common_policy" not in node_ids
    assert "qq_group_policy" not in node_ids
