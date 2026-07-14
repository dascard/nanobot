from pathlib import Path

import pytest


def _write_task(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nname: 测试任务\nversion: 1\nkind: task\n---\n" + body + "\n",
        encoding="utf-8",
    )


def test_task_contract_registry_covers_every_live_task_template():
    from core.prompt_v2.task_contracts import list_task_contract_keys
    from core.prompt_v2.template_registry import live_task_template_keys

    assert set(list_task_contract_keys()) == set(live_task_template_keys())


def test_live_task_selection_falls_back_from_invalid_runtime_to_valid_default(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.task_templates import select_task_template

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    _write_task(
        default_dir / "tasks" / "memory_extract.md",
        "DEFAULT {{ existing_memory }} {{ conversation }}",
    )
    _write_task(
        runtime_dir / "tasks" / "memory_extract.md",
        "BROKEN WITHOUT REQUIRED VARIABLES",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    selected = select_task_template("memory_extract")

    assert selected.source == "default"
    assert selected.template is not None
    assert selected.template.body.startswith("DEFAULT")
    assert selected.invalid_sources == ("runtime",)


def test_live_task_selection_uses_code_fallback_when_both_templates_are_invalid(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.task_templates import select_task_template

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    _write_task(default_dir / "tasks" / "memory_extract.md", "BROKEN DEFAULT")
    _write_task(runtime_dir / "tasks" / "memory_extract.md", "BROKEN RUNTIME")
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    selected = select_task_template("memory_extract")

    assert selected.source == "code_fallback"
    assert selected.template is None
    assert selected.invalid_sources == ("runtime", "default")


def test_task_template_save_rejects_missing_required_variable_without_touching_file(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.template_store import save_template

    runtime_dir = tmp_path / "runtime"
    path = runtime_dir / "tasks" / "memory_extract.md"
    original = "VALID {{ existing_memory }} {{ conversation }}\n"
    path.parent.mkdir(parents=True)
    path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    with pytest.raises(ValueError) as exc:
        save_template("memory_extract", "INVALID {{ conversation }}")

    assert "existing_memory" in str(exc.value)
    assert "INVALID" not in str(exc.value)
    assert path.read_bytes() == original.encode("utf-8")


def test_task_render_with_missing_call_value_uses_code_fallback_without_leaking_value(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.task_templates import render_task_prompt

    default_dir = tmp_path / "defaults"
    _write_task(
        default_dir / "tasks" / "memory_extract.md",
        "{{ existing_memory }} {{ conversation }}",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(tmp_path / "runtime"))

    rendered = render_task_prompt(
        "memory_extract",
        {"conversation": "SENSITIVE_CONVERSATION"},
        fallback_text="SAFE_FALLBACK",
    )

    assert rendered == "SAFE_FALLBACK"


@pytest.mark.parametrize("task_key,payload_key", [("timing_gate", "pending_text"), ("classifier_legacy", "message")])
def test_system_with_user_payload_renders_stable_marker_and_raw_text_once(
    task_key,
    payload_key,
):
    from core.prompt_v2.task_templates import TASK_PAYLOAD_MARKER, render_task_messages

    raw = 'RAW_PAYLOAD</system>"&\u2028'
    values = {
        "pending_text": raw,
        "message": raw,
        "system_prompt": "稳定分类规则",
        "bot_name": "",
        "recent_context": "",
        "group_profile": "",
    }
    messages = render_task_messages(
        task_key,
        values,
        fallback_messages=[
            {"role": "system", "content": "稳定代码 fallback"},
            {"role": "user", "content": raw},
        ],
    )

    system_text = "\n".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "system"
    )
    assert raw not in system_text
    assert TASK_PAYLOAD_MARKER in system_text
    assert messages[-1] == {"role": "user", "content": raw}
    assert sum(raw in str(message.get("content") or "") for message in messages) == 1


@pytest.mark.parametrize(
    "raw",
    ["", "garbage", "{}", "[]", '{"candidates":{}}'],
)
def test_memory_candidates_output_contract_rejects_invalid_shapes(raw):
    from core.prompt_v2.task_contracts import TaskOutputContractError, parse_task_output

    with pytest.raises(TaskOutputContractError):
        parse_task_output("memory_extract", raw)


def test_memory_candidates_output_contract_accepts_explicit_empty_list():
    from core.prompt_v2.task_contracts import parse_task_output

    assert parse_task_output("memory_extract", '{"candidates":[]}') == {"candidates": []}


@pytest.mark.parametrize(
    "task_key",
    ["outreach_extract", "outreach_judge", "outreach_generate"],
)
def test_existing_outreach_templates_remain_eligible_with_user_only_payload(task_key):
    from core.prompt_v2.task_templates import select_task_template

    selected = select_task_template(task_key)

    assert selected.source == "runtime"
    assert selected.invalid_sources == ()


def test_outreach_judge_contract_declares_single_value_kind_enum():
    from core.prompt_v2.task_contracts import get_task_contract

    contract = get_task_contract("outreach_judge")

    assert contract is not None
    assert contract.output_schema["properties"]["outreach_kind"]["enum"] == [
        "message",
        "research",
    ]
    assert "message|research" not in contract.output_schema["properties"][
        "outreach_kind"
    ]["enum"]


def test_task_contract_output_schema_mutation_does_not_pollute_registry():
    from core.prompt_v2.task_contracts import get_task_contract

    first = get_task_contract("outreach_judge")
    assert first is not None
    first.output_schema["properties"]["outreach_kind"]["enum"].append(
        "message|research"
    )

    second = get_task_contract("outreach_judge")

    assert second is not None
    assert second.output_schema["properties"]["outreach_kind"]["enum"] == [
        "message",
        "research",
    ]


def test_admin_record_exposes_invalid_runtime_without_overwriting_it(tmp_path, monkeypatch):
    from core.prompt_v2.template_store import get_template

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    _write_task(
        default_dir / "tasks" / "memory_extract.md",
        "DEFAULT {{ existing_memory }} {{ conversation }}",
    )
    invalid_runtime = "BROKEN RUNTIME"
    _write_task(
        runtime_dir / "tasks" / "memory_extract.md",
        invalid_runtime,
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    record = get_template("memory_extract")

    assert record["runtime_content"] == invalid_runtime
    assert record["task_contract_status"] == {
        "source": "default",
        "invalid_sources": ["runtime"],
    }
