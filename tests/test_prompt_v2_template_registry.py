import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


def _legacy_flow_without_session_guidance() -> dict:
    flow = json.loads(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8")
    )
    flow["nodes"] = [
        node for node in flow["nodes"] if node["id"] != "session_guidance"
    ]
    flow["edges"] = [
        edge
        for edge in flow["edges"]
        if "session_guidance" not in {edge["from"], edge["to"]}
    ]
    flow["edges"].append(
        {"from": "identity_context", "to": "persona_reference"}
    )
    return flow


def _write_template(path: Path, *, kind: str = "tool", tool_name: str = "group_analysis", body: str = "默认") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {path.stem}\nversion: 1\nkind: {kind}\ntool_name: {tool_name}\n---\n{body}\n",
        encoding="utf-8",
    )


def _copy_runtime_task_templates(runtime_dir: Path) -> None:
    shutil.copytree(
        Path("prompts.v2.default/tasks"),
        runtime_dir / "tasks",
        dirs_exist_ok=True,
    )


def _copy_runtime_tool_templates(runtime_dir: Path) -> None:
    shutil.copytree(
        Path("prompts.v2.default/tools"),
        runtime_dir / "tools",
        dirs_exist_ok=True,
    )


def _copy_active_chat_templates(
    runtime_dir: Path,
    *,
    exclude: set[str] | None = None,
    include_flow: bool = False,
) -> None:
    excluded = exclude or set()
    template_paths = [
        "main.md",
        "branch_group.md",
        "branch_private.md",
        "identity_context.md",
        "platform/qq/common.md",
        "platform/qq/group.md",
    ]
    if include_flow:
        template_paths.append("flow.json")
    for relative_path in template_paths:
        if relative_path in excluded:
            continue
        source = Path("prompts.v2.default/chat") / relative_path
        target = runtime_dir / "chat" / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def test_prompt_v2_registry_resolves_slash_keys_aliases_and_paths(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    _write_template(
        default_dir / "tools" / "group_analysis" / "topics.md",
        body="DEFAULT {{ messages_text }}",
    )
    runtime_path = runtime_dir / "tools" / "group_analysis" / "topics.md"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text("RUNTIME {{ messages_text }}\n", encoding="utf-8")
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_loader import load_template
    from core.prompt_v2.template_registry import list_template_records, resolve_template_key, template_path_for

    assert resolve_template_key("tools/group_analysis/topics") == "tools/group_analysis/topics"
    assert resolve_template_key("group_analysis_topics") == "tools/group_analysis/topics"
    assert template_path_for("group_analysis_topics", runtime=True) == runtime_path

    active = load_template("group_analysis_topics")
    assert active.prompt_key == "tools/group_analysis/topics"
    assert active.path == runtime_path
    assert active.body == "RUNTIME {{ messages_text }}"
    assert active.frontmatter["kind"] == "tool"
    assert active.frontmatter["tool_name"] == "group_analysis"

    records = {item["template_key"]: item for item in list_template_records()}
    assert records["tools/group_analysis/topics"]["category"] == "tools"
    assert records["tools/group_analysis/topics"]["source"] == "runtime"


def test_prompt_v2_init_runtime_dir_copies_missing_files_without_overwrite(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    (default_dir / "chat").mkdir(parents=True)
    (default_dir / "chat" / "main.md").write_text("DEFAULT MAIN\n", encoding="utf-8")
    canonical_flow = Path("prompts.v2.default/chat/flow.json").read_text(
        encoding="utf-8"
    )
    (default_dir / "chat" / "flow.json").write_text(
        canonical_flow,
        encoding="utf-8",
    )
    (runtime_dir / "chat").mkdir(parents=True)
    (runtime_dir / "chat" / "main.md").write_text("RUNTIME MAIN\n", encoding="utf-8")
    _copy_active_chat_templates(runtime_dir, exclude={"main.md"})
    _copy_runtime_task_templates(runtime_dir)
    _copy_runtime_tool_templates(runtime_dir)

    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    assert result["runtime_dir"] == str(runtime_dir)
    assert result["source_dir"] == str(default_dir)
    assert result["copied"] == ["chat/flow.json"]
    assert result["flow_migrated"] is False
    assert result["flow_backup_path"] == ""
    assert (runtime_dir / "chat" / "main.md").read_text(encoding="utf-8") == "RUNTIME MAIN\n"
    assert (
        runtime_dir / "chat" / "flow.json"
    ).read_text(encoding="utf-8") == canonical_flow


def test_prompt_v2_init_runtime_dir_preserves_existing_legacy_flow(
    tmp_path,
    monkeypatch,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    (default_dir / "chat").mkdir(parents=True)
    (default_dir / "chat" / "flow.json").write_text(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    runtime_flow = runtime_dir / "chat" / "flow.json"
    runtime_flow.parent.mkdir(parents=True)
    original = (
        json.dumps(
            _legacy_flow_without_session_guidance(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    runtime_flow.write_text(original, encoding="utf-8")
    _copy_active_chat_templates(runtime_dir)
    _copy_runtime_task_templates(runtime_dir)
    _copy_runtime_tool_templates(runtime_dir)
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    assert result["flow_migrated"] is False
    assert result["flow_backup_path"] == ""
    assert runtime_flow.read_text(encoding="utf-8") == original
    assert not (tmp_path / "prompt_template_backups").exists()


def test_prompt_v2_init_runtime_dir_rejects_broken_flow_symlink_before_copy(
    tmp_path,
    monkeypatch,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    source_flow = default_dir / "chat" / "flow.json"
    source_flow.parent.mkdir(parents=True)
    source_flow.write_text(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    runtime_flow = runtime_dir / "chat" / "flow.json"
    runtime_flow.parent.mkdir(parents=True)
    escaped_target = tmp_path / "escaped-flow.json"
    runtime_flow.symlink_to(escaped_target)
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    with pytest.raises(ValueError, match="符号链接"):
        init_prompt_v2_runtime_dir()

    assert not escaped_target.exists()


def test_prompt_v2_init_runtime_dir_rejects_symlinked_template_parent_before_copy(
    tmp_path,
    monkeypatch,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    source_flow = default_dir / "chat" / "flow.json"
    source_flow.parent.mkdir(parents=True)
    source_flow.write_text(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    runtime_dir.mkdir()
    escaped_dir = tmp_path / "escaped-chat"
    escaped_dir.mkdir()
    (runtime_dir / "chat").symlink_to(escaped_dir, target_is_directory=True)
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    with pytest.raises(ValueError, match="符号链接"):
        init_prompt_v2_runtime_dir()

    assert not (escaped_dir / "flow.json").exists()


def test_prompt_v2_init_runtime_flow_copy_and_admin_save_share_write_lock(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2 import flow as flow_module
    from core.prompt_v2 import template_registry

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    source_flow = default_dir / "chat" / "flow.json"
    source_flow.parent.mkdir(parents=True)
    source_flow.write_text(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    runtime_flow = runtime_dir / "chat" / "flow.json"
    runtime_flow.parent.mkdir(parents=True)
    _copy_active_chat_templates(runtime_dir)
    _copy_runtime_task_templates(runtime_dir)
    _copy_runtime_tool_templates(runtime_dir)
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))

    missing_checked = threading.Event()
    release_copy = threading.Event()
    save_started = threading.Event()
    errors: list[BaseException] = []
    real_exists = Path.exists
    check_paused = False

    def paused_exists(path):
        nonlocal check_paused
        exists = real_exists(path)
        if Path(path) == runtime_flow and not check_paused:
            check_paused = True
            missing_checked.set()
            if not release_copy.wait(timeout=5):
                raise TimeoutError("等待并发保存超时")
        return exists

    monkeypatch.setattr(Path, "exists", paused_exists)

    def run_copy():
        try:
            template_registry.init_prompt_v2_runtime_dir()
        except BaseException as exc:  # pragma: no cover - 仅用于跨线程传递
            errors.append(exc)

    concurrent_flow = json.loads(source_flow.read_text(encoding="utf-8"))
    concurrent_node = next(
        node
        for node in concurrent_flow["nodes"]
        if node["id"] == "session_guidance"
    )
    concurrent_node["concurrent_note"] = "admin-save-must-win"

    def run_save():
        save_started.set()
        try:
            flow_module.save_flow(concurrent_flow)
        except BaseException as exc:  # pragma: no cover - 仅用于跨线程传递
            errors.append(exc)

    copy_thread = threading.Thread(target=run_copy)
    save_thread = threading.Thread(target=run_save)
    copy_thread.start()
    assert missing_checked.wait(timeout=5)
    save_thread.start()
    assert save_started.wait(timeout=5)
    time.sleep(0.05)
    release_copy.set()
    copy_thread.join(timeout=5)
    save_thread.join(timeout=5)

    assert not copy_thread.is_alive()
    assert not save_thread.is_alive()
    assert errors == []
    final_flow = json.loads(runtime_flow.read_text(encoding="utf-8"))
    final_node = next(
        node
        for node in final_flow["nodes"]
        if node["id"] == "session_guidance"
    )
    assert final_node["concurrent_note"] == "admin-save-must-win"


def test_prompt_v2_init_runtime_dir_preserves_legacy_super_user_placeholder(
    tmp_path,
    monkeypatch,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    default_dir.mkdir()
    identity_path = runtime_dir / "chat" / "identity_context.md"
    identity_path.parent.mkdir(parents=True)
    identity_path.write_text(
        "super_user_id: {{ super_user_id }}\n",
        encoding="utf-8",
    )
    _copy_active_chat_templates(
        runtime_dir,
        exclude={"identity_context.md"},
        include_flow=True,
    )
    _copy_runtime_task_templates(runtime_dir)
    _copy_runtime_tool_templates(runtime_dir)
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    assert result["migrated"] == []
    assert identity_path.read_text(encoding="utf-8") == (
        "super_user_id: {{ super_user_id }}\n"
    )


def test_prompt_v2_init_runtime_dir_fails_closed_when_active_tasks_are_missing(
    tmp_path,
    monkeypatch,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    default_dir.mkdir()
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.task_contracts import TaskContractError
    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    with pytest.raises(TaskContractError, match="active task contract missing template"):
        init_prompt_v2_runtime_dir()


@pytest.mark.parametrize("invalid_source", ["runtime", "default"])
def test_prompt_v2_init_runtime_dir_warns_for_inactive_invalid_template(
    tmp_path,
    monkeypatch,
    invalid_source,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    shutil.copytree(Path("prompts.v2.default"), default_dir)
    shutil.copytree(Path("prompts.v2.default"), runtime_dir)
    invalid_path = (
        runtime_dir if invalid_source == "runtime" else default_dir
    ) / "tools" / "never_registered" / "usage.md"
    invalid_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_path.write_bytes(b"\xff\xfeinactive invalid")
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    report = next(
        item
        for item in result["template_audit"]
        if item["template_key"] == "tools/never_registered/usage"
    )
    assert report["drift_status"] == "invalid"
    expected_component = (
        "runtime_content" if invalid_source == "runtime" else "canonical_content"
    )
    assert report["invalid_component"] == expected_component
    if invalid_source == "default":
        assert not (
            runtime_dir / "tools" / "never_registered" / "usage.md"
        ).exists()


@pytest.mark.parametrize(
    "template_key",
    [
        "chat/main",
        "tasks/memory_extract",
        "tools/reply/usage",
        "tools/group_analysis/system",
    ],
)
def test_prompt_v2_init_runtime_dir_fails_closed_for_active_invalid_template(
    tmp_path,
    monkeypatch,
    template_key,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    shutil.copytree(Path("prompts.v2.default"), default_dir)
    shutil.copytree(Path("prompts.v2.default"), runtime_dir)
    (runtime_dir / f"{template_key}.md").write_bytes(b"\xff\xfeactive invalid")
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    from core.prompt_v2.template_baseline import TemplateBaselineError
    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    with pytest.raises(TemplateBaselineError, match=template_key):
        init_prompt_v2_runtime_dir()


def test_prompt_v2_init_runtime_dir_warns_for_force_disabled_tool_template(
    tmp_path,
    monkeypatch,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    shutil.copytree(Path("prompts.v2.default"), default_dir)
    shutil.copytree(Path("prompts.v2.default"), runtime_dir)
    runtime_path = runtime_dir / "tools" / "python_sandbox" / "usage.md"
    runtime_path.write_bytes(b"\xff\xfeforce disabled invalid")
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    report = next(
        item
        for item in result["template_audit"]
        if item["template_key"] == "tools/python_sandbox/usage"
    )
    assert report["drift_status"] == "invalid"
    assert report["invalid_component"] == "runtime_content"


@pytest.mark.parametrize(
    "template_key",
    [
        "chat/main",
        "chat/identity_context",
        "tools/reply/usage",
        "tools/group_analysis/system",
    ],
)
def test_prompt_v2_init_runtime_dir_fails_closed_for_active_template_missing_both_sources(
    tmp_path,
    monkeypatch,
    template_key,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    shutil.copytree(Path("prompts.v2.default"), default_dir)
    shutil.copytree(Path("prompts.v2.default"), runtime_dir)
    (default_dir / f"{template_key}.md").unlink()
    (runtime_dir / f"{template_key}.md").unlink()
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    from core.prompt_v2.template_baseline import TemplateBaselineError
    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    with pytest.raises(TemplateBaselineError, match=template_key):
        init_prompt_v2_runtime_dir()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="平台不支持 FIFO")
@pytest.mark.parametrize(
    "surface",
    [
        "task_startup",
        "template_loader",
        "template_records",
        "template_store_detail",
    ],
)
def test_prompt_template_public_readers_do_not_block_on_fifo(
    tmp_path,
    surface,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    shutil.copytree(Path("prompts.v2.default"), default_dir)
    shutil.copytree(Path("prompts.v2.default"), runtime_dir)

    if surface == "task_startup":
        fifo_path = runtime_dir / "tasks" / "memory_extract.md"
    elif surface == "template_loader":
        fifo_path = runtime_dir / "chat" / "main.md"
    else:
        fifo_path = runtime_dir / "tools" / "custom_fifo" / "usage.md"
        fifo_path.parent.mkdir(parents=True, exist_ok=True)
    fifo_path.unlink(missing_ok=True)
    os.mkfifo(fifo_path)

    probe = """
import os

surface = os.environ["PROBE_SURFACE"]
try:
    if surface == "task_startup":
        from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir
        init_prompt_v2_runtime_dir()
    elif surface == "template_loader":
        from core.prompt_v2.template_loader import load_template
        load_template("chat/main")
    elif surface == "template_records":
        from core.prompt_v2.template_registry import list_template_records
        records = list_template_records()
        if not any(
            item["template_key"] == "tools/custom_fifo/usage"
            for item in records
        ):
            raise SystemExit(2)
        print("completed")
        raise SystemExit(0)
    else:
        from core.prompt_v2.template_store import get_template
        get_template("tools/custom_fifo/usage")
except Exception:
    if surface == "template_records":
        raise
    print("rejected")
    raise SystemExit(0)
raise SystemExit(2)
"""
    env = os.environ.copy()
    env.update(
        {
            "NANOBOT_PROMPT_DEFAULT_DIR": str(default_dir),
            "NANOBOT_PROMPT_RUNTIME_DIR": str(runtime_dir),
            "NANOBOT_PROMPT_TEMPLATE_STATE_DIR": str(state_dir),
            "PROBE_SURFACE": surface,
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    expected = "completed" if surface == "template_records" else "rejected"
    assert result.stdout.strip() == expected


def test_prompt_v2_init_runtime_dir_fails_closed_for_custom_live_flow_template_missing(
    tmp_path,
    monkeypatch,
):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    state_dir = tmp_path / "template-state"
    shutil.copytree(Path("prompts.v2.default"), default_dir)
    shutil.copytree(Path("prompts.v2.default"), runtime_dir)
    for root in (default_dir, runtime_dir):
        flow_path = root / "chat" / "flow.json"
        flow = json.loads(flow_path.read_text(encoding="utf-8"))
        current_index = next(
            index
            for index, node in enumerate(flow["nodes"])
            if node["id"] == "current_user_event"
        )
        flow["nodes"].insert(
            current_index,
            {
                "id": "custom_live_policy",
                "type": "template",
                "label": "system: custom live policy",
                "template_key": "chat/custom_live_policy",
            },
        )
        flow["edges"] = [
            edge
            for edge in flow["edges"]
            if (edge["from"], edge["to"])
            != ("runtime_tool_prompt", "current_user_event")
        ]
        flow["edges"].extend(
            [
                {
                    "from": "runtime_tool_prompt",
                    "to": "custom_live_policy",
                },
                {
                    "from": "custom_live_policy",
                    "to": "current_user_event",
                },
            ]
        )
        flow_path.write_text(
            json.dumps(flow, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_TEMPLATE_STATE_DIR", str(state_dir))

    from core.prompt_v2.template_baseline import TemplateBaselineError
    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    with pytest.raises(TemplateBaselineError, match="chat/custom_live_policy"):
        init_prompt_v2_runtime_dir()


def test_prompt_template_registry_prefers_canonical_env_names(tmp_path, monkeypatch):
    default_dir = tmp_path / "prompt_defaults"
    runtime_dir = tmp_path / "prompt_runtime"
    legacy_default_dir = tmp_path / "legacy_defaults"
    legacy_runtime_dir = tmp_path / "legacy_runtime"
    default_dir.mkdir()
    runtime_dir.mkdir()
    legacy_default_dir.mkdir()
    legacy_runtime_dir.mkdir()

    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(legacy_default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(legacy_runtime_dir))

    from core.prompt_v2.template_registry import default_template_dir, runtime_template_dir

    assert default_template_dir() == default_dir
    assert runtime_template_dir() == runtime_dir


def test_prompt_template_registry_keeps_v2_env_names_as_compat_fallback(tmp_path, monkeypatch):
    default_dir = tmp_path / "legacy_defaults"
    runtime_dir = tmp_path / "legacy_runtime"
    default_dir.mkdir()
    runtime_dir.mkdir()

    monkeypatch.delenv("NANOBOT_PROMPT_DEFAULT_DIR", raising=False)
    monkeypatch.delenv("NANOBOT_PROMPT_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_registry import default_template_dir, runtime_template_dir

    assert default_template_dir() == default_dir
    assert runtime_template_dir() == runtime_dir


def test_prompt_template_frontmatter_uses_canonical_prompt_names():
    roots = [Path("prompts.v2.default"), Path("data/prompts_v2")]
    bad_entries: list[str] = []

    for root in roots:
        for path in sorted(root.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---\n"):
                continue
            try:
                frontmatter = text.split("---\n", 2)[1]
            except IndexError:
                continue
            if "V2" in frontmatter or "Prompt Runtime V2" in frontmatter:
                bad_entries.append(str(path))

    assert bad_entries == []


@pytest.mark.parametrize("bad_key", ["../secret", "/abs/path", "tools\\bad", "tools//bad", ""])
def test_prompt_v2_registry_rejects_unsafe_template_keys(bad_key):
    from core.prompt_v2.template_registry import resolve_template_key

    with pytest.raises(ValueError):
        resolve_template_key(bad_key)


def test_prompt_platform_templates_are_addressable():
    from core.prompt_v2.template_loader import load_template
    from core.prompt_v2.template_registry import resolve_template_key

    assert resolve_template_key("chat/platform/qq/common") == "chat/platform/qq/common"
    assert load_template("chat/platform/qq/common").body
    assert load_template("chat/platform/qq/group").body


def test_prompt_platform_words_are_isolated_to_platform_templates():
    roots = [Path("prompts.v2.default"), Path("data/prompts_v2")]
    forbidden_common = ("QQ", "OneBot", "CQ 码", "NapCat", "@", "群友", "斗图", "表情包")

    for root in roots:
        main = (root / "chat" / "main.md").read_text(encoding="utf-8")
        group = (root / "chat" / "branch_group.md").read_text(encoding="utf-8")
        private = (root / "chat" / "branch_private.md").read_text(encoding="utf-8")
        qq_common = (root / "chat" / "platform" / "qq" / "common.md").read_text(encoding="utf-8")
        qq_group = (root / "chat" / "platform" / "qq" / "group.md").read_text(encoding="utf-8")

        for text in (main, group, private):
            for needle in forbidden_common:
                assert needle not in text, f"{needle} should stay out of generic chat templates under {root}"

        assert "QQ 平台" in qq_common
        assert "OneBot" in qq_common
        assert "QQ 群聊" in qq_group


def test_prompt_tool_usage_avoids_platform_private_message_codes():
    roots = [Path("prompts.v2.default"), Path("data/prompts_v2")]
    usage_paths = [
        "tools/reply/usage.md",
        "tools/sticker_search/usage.md",
        "tools/image_generation/usage.md",
    ]
    forbidden = ("QQ 发送前", "OneBot CQ 码")

    for root in roots:
        for rel_path in usage_paths:
            text = (root / rel_path).read_text(encoding="utf-8")
            for needle in forbidden:
                assert needle not in text, f"{needle} should stay out of platform-neutral tool usage {root / rel_path}"
