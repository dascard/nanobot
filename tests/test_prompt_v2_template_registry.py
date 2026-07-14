import json
from pathlib import Path
import threading
import time

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


def test_prompt_v2_init_runtime_dir_migrates_existing_legacy_flow(
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
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    assert result["flow_migrated"] is True
    backup_path = Path(result["flow_backup_path"])
    assert backup_path.parent == (
        tmp_path / "prompt_template_backups" / "session_guidance_flow"
    )
    assert backup_path.read_text(encoding="utf-8") == original
    migrated = json.loads(runtime_flow.read_text(encoding="utf-8"))
    assert sum(
        node["id"] == "session_guidance" for node in migrated["nodes"]
    ) == 1


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


def test_prompt_v2_init_runtime_dir_migrates_legacy_super_user_placeholder(
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
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    assert result["migrated"] == ["chat/identity_context.md"]
    assert identity_path.read_text(encoding="utf-8") == (
        "is_super_user: {{ is_super_user }}\n"
    )


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
