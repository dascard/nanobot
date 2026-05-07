"""Prompt contract tests — verify generated prompt.md invariants."""
import os
import sys
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.build_nanobot_prompt import build_prompt, _iter_fragments


@pytest.fixture(scope="module")
def prompt_text():
    return build_prompt()


@pytest.fixture(scope="module")
def fragments():
    return dict(_iter_fragments())


# ── Build integrity ──

def test_prompt_is_not_empty(prompt_text):
    assert len(prompt_text) > 200


def test_prompt_has_required_fragments(fragments):
    required = {
        "00_identity.md",
        "05_core.md",
        "10_chat_style.md",
        "20_group_rules.md",
        "25_context_control.md",
        "27_tool_routing.md",
        "30_tool_discipline.md",
        "40_memory_policy.md",
        "60_artifact_passthrough.md",
    }
    missing = required - set(fragments.keys())
    assert not missing, f"Missing fragments: {missing}"


def test_generated_matches_committed():
    """CI guard: committed prompt.md must equal build output."""
    import scripts.build_nanobot_prompt as bp
    generated = bp.build_prompt()
    with open(bp.OUTPUT_FILE, "r", encoding="utf-8") as fh:
        committed = fh.read()
    assert generated == committed, (
        "prompt.md is stale. Run: python scripts/build_nanobot_prompt.py"
    )


# ── Key rules present ──

def test_contains_reply_contract(prompt_text):
    assert "reply(content)" in prompt_text
    assert '禁止用 assistant 普通文本作为最终回复' in prompt_text


def test_contains_html_passthrough(prompt_text):
    assert "news_search" in prompt_text
    assert "group_analysis" in prompt_text
    assert "HTML" in prompt_text


def test_contains_tool_discipline(prompt_text):
    assert "工具调用纪律" in prompt_text
    assert "禁止用不同参数反复调同一个工具" in prompt_text


def test_contains_chat_style(prompt_text):
    assert 'meta 话术' in prompt_text
    assert "换行即分段" in prompt_text


def test_contains_memory_policy(prompt_text):
    assert "history_clear_at" not in prompt_text  # implementation detail, not user-facing
    assert "sql_analysis" in prompt_text  # tool is mentioned


def test_contains_structured_runtime_context(prompt_text):
    assert "<runtime_context>" in prompt_text
    assert "<group_memory_context" in prompt_text
    assert "[GroupProfileContext]" not in prompt_text


def test_group_prompt_aligns_maibot_planner_and_replyer(prompt_text):
    assert "planner 职责" in prompt_text
    assert "先判断当前聊天节奏" in prompt_text
    assert "把真正要发出去的普通文本放进 `reply(content)`" in prompt_text
    assert "recent context" not in prompt_text.lower()


# ── Sanity: no duplicates ──

def test_no_duplicate_tool_descriptions(prompt_text):
    """工具描述段不应重复出现。工具名可被其他 section 交叉引用。"""
    assert prompt_text.count("chat_logs 表包含完整历史") == 1
    assert prompt_text.count("直接调用本地 Qwen 视觉模型") == 1


def test_no_outdated_markers(prompt_text):
    """不应包含过时的标记或内部实现细节。"""
    forbidden = [
        "PlannerAgent",
        "ReplyerAgent",
        "extract_reply",
        "[REPLY]...[/REPLY]",
    ]
    for term in forbidden:
        assert term not in prompt_text, f"Leaked internal term: {term}"


def test_no_conflicting_instructions(prompt_text):
    """不应存在互相矛盾的指令。"""
    assert "HTML" in prompt_text
    assert "口语化" in prompt_text or "短" in prompt_text
    assert "markdown" in prompt_text.lower()


# ── Fragment isolation ──

def test_identity_fragment_is_first(fragments):
    names = list(fragments.keys())
    assert names[0].startswith("00_")


def test_tool_descriptions_only_in_tool_fragment(fragments):
    """工具的长描述（含参数/用法说明）只在 30_tool_discipline 中定义。"""
    tool_frag = fragments.get("30_tool_discipline.md", "")
    for fname, content in fragments.items():
        if "tool" in fname.lower():
            continue
        assert "直接调用本地 Qwen 视觉模型" not in content, (
            f"{fname} contains image_summary description"
        )
