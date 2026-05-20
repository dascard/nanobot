"""测试旧版 Prompt 运行时分离模块。"""
import os
import tempfile

import pytest


@pytest.fixture
def legacy_env(monkeypatch, tmp_path):
    """设置临时目录作为运行时目录，避免污染真实 data/。"""
    default_dir = tmp_path / "default" / "fragments"
    runtime_dir = tmp_path / "runtime" / "fragments"
    backup_dir = tmp_path / "backups"
    output_path = tmp_path / "output" / "prompt.md"
    default_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    backup_dir.mkdir(parents=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("NANOBOT_LEGACY_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_LEGACY_PROMPT_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("NANOBOT_LEGACY_PROMPT_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("NANOBOT_LEGACY_PROMPT_OUTPUT", str(output_path))

    (default_dir / "00_test.md").write_text("# Test fragment\n")
    (default_dir / "05_core.md").write_text("# Core fragment\n")

    from core.legacy_prompt_runtime import (
        default_fragments_dir,
        runtime_fragments_dir,
        backup_dir as _backup_dir,
        runtime_prompt_output,
    )

    return {
        "default_dir": str(default_dir),
        "runtime_dir": str(runtime_dir),
        "backup_dir": str(backup_dir),
        "output_path": str(output_path),
    }


def test_init_copies_missing_only(legacy_env):
    from core.legacy_prompt_runtime import init_legacy_prompt_runtime_dir

    result = init_legacy_prompt_runtime_dir()
    assert len(result["copied"]) == 2
    assert os.path.exists(os.path.join(legacy_env["runtime_dir"], "00_test.md"))
    assert os.path.exists(os.path.join(legacy_env["runtime_dir"], "05_core.md"))

    result2 = init_legacy_prompt_runtime_dir()
    assert len(result2["copied"]) == 0


def test_init_does_not_overwrite_modified(legacy_env):
    from core.legacy_prompt_runtime import init_legacy_prompt_runtime_dir

    init_legacy_prompt_runtime_dir()
    rp = os.path.join(legacy_env["runtime_dir"], "00_test.md")
    with open(rp, "w") as fh:
        fh.write("# modified")
    result = init_legacy_prompt_runtime_dir()
    assert len(result["copied"]) == 0
    with open(rp) as fh:
        assert fh.read() == "# modified"


def test_list_fragments_with_status(legacy_env):
    from core.legacy_prompt_runtime import init_legacy_prompt_runtime_dir, list_fragments_with_status

    init_legacy_prompt_runtime_dir()
    items = list_fragments_with_status()
    assert len(items) == 2
    for item in items:
        assert item["has_default"] is True
        assert item["has_runtime"] is True
        assert item["is_modified"] is False


def test_save_fragment_writes_runtime_only(legacy_env):
    from core.legacy_prompt_runtime import init_legacy_prompt_runtime_dir, save_fragment, list_fragments_with_status

    init_legacy_prompt_runtime_dir()
    result = save_fragment("00_test.md", "# modified content")
    assert result["saved"] is True
    assert result["runtime_path"].startswith(legacy_env["runtime_dir"])

    dp = os.path.join(legacy_env["default_dir"], "00_test.md")
    with open(dp) as fh:
        assert fh.read() == "# Test fragment\n"

    items = list_fragments_with_status()
    mod = [i for i in items if i["name"] == "00_test.md"][0]
    assert mod["is_modified"] is True


def test_save_backups_old_runtime(legacy_env):
    from core.legacy_prompt_runtime import init_legacy_prompt_runtime_dir, save_fragment

    init_legacy_prompt_runtime_dir()
    # init 已复制默认→运行时，所以第一次 save 也会备份已有的运行时文件
    r1 = save_fragment("00_test.md", "# first save")
    assert r1["backup_name"] != ""
    assert os.path.exists(os.path.join(legacy_env["backup_dir"], r1["backup_name"]))
    r2 = save_fragment("00_test.md", "# second save")
    assert r2["backup_name"] != ""
    assert os.path.exists(os.path.join(legacy_env["backup_dir"], r2["backup_name"]))


def test_build_from_runtime(legacy_env):
    from core.legacy_prompt_runtime import init_legacy_prompt_runtime_dir, build_prompt_from_runtime

    init_legacy_prompt_runtime_dir()
    result = build_prompt_from_runtime()
    assert result["ok"] is True
    assert len(result["fragments_used"]) > 0
    assert os.path.isfile(result["output"])
    with open(legacy_env["output_path"]) as fh:
        assert "# Test fragment" in fh.read()


def test_read_runtime_preferred(legacy_env):
    from core.legacy_prompt_runtime import init_legacy_prompt_runtime_dir, build_prompt_from_runtime, read_runtime_or_default_prompt

    init_legacy_prompt_runtime_dir()
    build_prompt_from_runtime()
    result = read_runtime_or_default_prompt()
    assert result["source"] == "runtime"
    assert result["output_path"] == legacy_env["output_path"]


def test_reset_to_default(legacy_env):
    from core.legacy_prompt_runtime import init_legacy_prompt_runtime_dir, save_fragment, reset_to_default, list_fragments_with_status

    init_legacy_prompt_runtime_dir()
    save_fragment("00_test.md", "# modified")
    items = list_fragments_with_status()
    assert [i for i in items if i["name"] == "00_test.md"][0]["is_modified"] is True

    reset_to_default("00_test.md")
    items2 = list_fragments_with_status()
    assert [i for i in items2 if i["name"] == "00_test.md"][0]["is_modified"] is False


def test_get_default_fragment(legacy_env):
    from core.legacy_prompt_runtime import get_default_fragment

    result = get_default_fragment("00_test.md")
    assert result is not None
    assert "# Test fragment" in result["content"]
    assert get_default_fragment("nonexistent.md") is None


def test_save_rejects_invalid_name(legacy_env):
    from core.legacy_prompt_runtime import save_fragment

    with pytest.raises(ValueError):
        save_fragment("../../../etc/passwd", "bad")


def test_reset_nonexistent_default(legacy_env):
    from core.legacy_prompt_runtime import reset_to_default

    with pytest.raises(FileNotFoundError):
        reset_to_default("nonexistent.md")
