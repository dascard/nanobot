from pathlib import Path

import pytest


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
    (default_dir / "chat" / "flow.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
    (runtime_dir / "chat").mkdir(parents=True)
    (runtime_dir / "chat" / "main.md").write_text("RUNTIME MAIN\n", encoding="utf-8")

    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    assert result["runtime_dir"] == str(runtime_dir)
    assert result["source_dir"] == str(default_dir)
    assert result["copied"] == ["chat/flow.json"]
    assert (runtime_dir / "chat" / "main.md").read_text(encoding="utf-8") == "RUNTIME MAIN\n"
    assert (runtime_dir / "chat" / "flow.json").read_text(encoding="utf-8") == '{"nodes": [], "edges": []}\n'


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
