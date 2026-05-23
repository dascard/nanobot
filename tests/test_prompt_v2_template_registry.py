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


@pytest.mark.parametrize("bad_key", ["../secret", "/abs/path", "tools\\bad", "tools//bad", ""])
def test_prompt_v2_registry_rejects_unsafe_template_keys(bad_key):
    from core.prompt_v2.template_registry import resolve_template_key

    with pytest.raises(ValueError):
        resolve_template_key(bad_key)
