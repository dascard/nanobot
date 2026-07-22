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


def test_task_contract_registry_covers_real_task_files_or_explicit_code_fallback():
    from core.prompt_v2.task_contracts import get_task_contract, list_task_contract_keys

    file_keys = {
        f"tasks/{path.stem}"
        for root in (Path("prompts.v2.default/tasks"), Path("data/prompts_v2/tasks"))
        for path in root.glob("*.md")
    }
    contract_keys = set(list_task_contract_keys())
    code_fallback_keys = {
        key
        for key in contract_keys
        if get_task_contract(key).render_mode == "code_fallback_only"
    }

    assert file_keys <= contract_keys
    assert contract_keys <= file_keys | code_fallback_keys


def test_memory_digest_pair_contract_declares_required_and_non_empty_inputs():
    from core.prompt_v2.task_contracts import get_task_contract

    system_contract = get_task_contract("memory_digest_system")
    user_contract = get_task_contract("memory_digest_user")

    assert system_contract is not None
    assert user_contract is not None
    assert system_contract.render_mode == "paired_messages"
    assert user_contract.render_mode == "paired_messages"
    assert user_contract.required_variables == frozenset(
        {
            "date",
            "session_id",
            "source_id",
            "source_type",
            "source_range",
            "message_count",
            "digest_source",
        }
    )
    assert user_contract.required_call_values == user_contract.required_variables
    assert user_contract.non_empty_call_values == frozenset(
        {
            "session_id",
            "source_id",
            "source_type",
            "source_range",
            "digest_source",
        }
    )


@pytest.mark.parametrize(
    "required_name",
    [
        "date",
        "session_id",
        "source_id",
        "source_type",
        "source_range",
        "message_count",
        "digest_source",
    ],
)
def test_memory_digest_user_template_rejects_each_missing_required_reference(
    required_name,
):
    from core.prompt_v2.task_contracts import TaskContractError, validate_task_template
    from core.prompt_v2.template_loader import split_frontmatter_text

    raw = Path("prompts.v2.default/tasks/memory_digest_user.md").read_text(
        encoding="utf-8"
    )
    _frontmatter, body = split_frontmatter_text(raw)
    mutated = body.replace("{{ " + required_name + " }}", "")

    with pytest.raises(TaskContractError, match=required_name):
        validate_task_template("memory_digest_user", mutated)


@pytest.mark.parametrize(
    ("mode", "invalid_value"),
    [
        ("missing", None),
        ("value", None),
        ("value", ""),
        ("value", "   "),
        ("value", []),
        ("value", {}),
        ("value", ()),
        ("value", set()),
    ],
)
def test_memory_digest_user_call_rejects_missing_none_and_empty_values(
    mode,
    invalid_value,
):
    from core.prompt_v2.task_contracts import (
        TaskContractError,
        validate_task_call_values,
    )

    values = {
        "date": "2026-07-14",
        "session_id": "group_42",
        "source_id": "source-1",
        "source_type": "date_session",
        "source_range": "log_id 1-2",
        "message_count": "2",
        "digest_source": "有效摘要输入",
    }
    if mode == "missing":
        values.pop("digest_source")
    else:
        values["digest_source"] = invalid_value

    with pytest.raises(TaskContractError, match="digest_source"):
        validate_task_call_values("memory_digest_user", values)


def test_task_invocation_manifest_covers_live_tasks_and_pair_wrapper():
    from core.prompt_v2.task_templates import get_task_invocation_manifest
    from core.prompt_v2.template_registry import live_task_template_keys

    manifest = get_task_invocation_manifest()

    assert set(manifest) == set(live_task_template_keys())
    assert manifest["tasks/memory_digest_system"] == "render_task_pair"
    assert manifest["tasks/memory_digest_user"] == "render_task_pair"
    assert manifest["tasks/private_decision"] == "render_task_messages"
    assert manifest["tasks/timing_proactive"] == "render_task_messages"


def test_timing_proactive_output_contract_is_strict():
    from core.prompt_v2.task_contracts import (
        TaskOutputContractError,
        parse_task_output,
    )

    assert parse_task_output(
        "timing_proactive",
        '{"should_speak":true,"reason":"可以补充"}',
    ) == {"should_speak": True, "reason": "可以补充"}

    with pytest.raises(TaskOutputContractError):
        parse_task_output(
            "timing_proactive",
            '{"should_speak":"true","reason":"类型错误"}',
        )
    with pytest.raises(TaskOutputContractError):
        parse_task_output(
            "timing_proactive",
            '{"should_speak":false,"reason":"沉默","extra":1}',
        )


def test_task_invocation_specs_declare_output_parser_owner():
    from core.prompt_v2.task_contracts import list_task_invocation_specs

    specs = list_task_invocation_specs()

    assert specs
    assert all(spec.output_parser_owner.strip() for spec in specs)
    assert any(
        spec.invocation_id == "memory_digest"
        and spec.template_keys
        == ("tasks/memory_digest_system", "tasks/memory_digest_user")
        and spec.render_api == "paired_messages"
        for spec in specs
    )


def test_memory_digest_llm_builder_uses_contract_wrapper_only():
    source = Path("app/memory_digest/llm_builder.py").read_text(encoding="utf-8")

    assert "render_task_pair(" in source
    assert "load_template(" not in source
    assert "render_scoped_template(" not in source


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


def test_fail_closed_task_selection_reports_unavailable_when_sources_are_invalid(
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

    assert selected.source == "unavailable"
    assert selected.template is None
    assert selected.invalid_sources == ("runtime", "default")


def test_task_contract_registry_exposes_owner_domain_precedence_and_editability():
    from core.prompt_v2.task_contracts import (
        get_task_contract,
        task_contract_registry_snapshot,
    )

    memory_extract = get_task_contract("memory_extract")
    code_fallback = get_task_contract("reply_contract_retry")

    assert memory_extract is not None
    assert memory_extract.owner_module == "core.persona_preprocess"
    assert memory_extract.domain == "persona"
    assert memory_extract.source_precedence == ("runtime", "default")
    assert memory_extract.editable is True
    assert code_fallback is not None
    assert code_fallback.source_precedence == ("code_fallback",)
    assert code_fallback.editable is False
    assert all(
        item["owner_module"] and item["domain"]
        for item in task_contract_registry_snapshot()
    )


def test_live_task_audit_fails_closed_when_memory_digest_pair_is_invalid(
    tmp_path,
    monkeypatch,
):
    import shutil

    from core.prompt_v2.task_templates import (
        TaskTemplateUnavailableError,
        inspect_live_task_templates,
    )

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    shutil.copytree(Path("prompts.v2.default/tasks"), default_dir / "tasks")
    shutil.copytree(Path("prompts.v2.default/tasks"), runtime_dir / "tasks")
    for root in (default_dir, runtime_dir):
        _write_task(
            root / "tasks" / "memory_digest_user.md",
            "BROKEN WITHOUT REQUIRED VARIABLES",
        )
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))

    with pytest.raises(TaskTemplateUnavailableError, match="memory_digest_user"):
        inspect_live_task_templates()


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


def test_task_render_with_missing_call_value_fails_closed_without_leaking_value(
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

    from core.prompt_v2.task_contracts import TaskCallValueError

    with pytest.raises(TaskCallValueError) as exc:
        render_task_prompt(
            "memory_extract",
            {"conversation": "SENSITIVE_CONVERSATION"},
            fallback_text="SAFE_FALLBACK",
        )

    assert "existing_memory" in str(exc.value)
    assert "SENSITIVE_CONVERSATION" not in str(exc.value)


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
