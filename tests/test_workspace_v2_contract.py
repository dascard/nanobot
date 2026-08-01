from __future__ import annotations

from hashlib import sha256

import pytest

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from nanobot_kt.tools.sandbox import _validate_arguments
from tests.test_sandboxd_api import WORKSPACE_ID, _runtime


def _service(tmp_path):
    _token, runtime = _runtime(tmp_path)
    service = runtime.workspace_files
    service.layout.ensure_roots()
    service.ensure_workspace(WORKSPACE_ID)
    return service


def _read_bytes(service, path: str) -> bytes:
    filesystem = service.filesystem(WORKSPACE_ID)
    size = filesystem.regular_file_size(path)
    return filesystem.read_bytes(path, offset=0, limit=max(1, size))


def test_workspace_read_uses_line_offsets_without_splitting_utf8(tmp_path):
    service = _service(tmp_path)
    service.write_file(
        WORKSPACE_ID,
        path="project/src/demo.py",
        content="第一行\n第二行🙂\nthird\n",
        overwrite=False,
        quota_bytes=1024 * 1024,
    )

    page = service.read_file(
        WORKSPACE_ID,
        cwd="project",
        path="src/demo.py",
        offset=1,
        limit=1,
    )

    assert page["content"] == "     2\t第二行🙂"
    assert page["start_offset"] == 1
    assert page["returned_lines"] == 1
    assert page["next_offset"] == 2
    assert page["total_lines"] == 3
    assert page["eof"] is False
    assert page["binary"] is False

    with pytest.raises(SandboxServiceError) as escaped:
        service.read_file(
            WORKSPACE_ID,
            cwd="../outside",
            path="demo.py",
            offset=0,
            limit=1,
        )
    assert escaped.value.code is SandboxErrorCode.INVALID_PATH


def test_workspace_text_editor_preserves_exact_content_and_rejects_stale_save(
    tmp_path,
):
    service = _service(tmp_path)
    original = "第一行\n第二行🙂\n"
    service.write_file(
        WORKSPACE_ID,
        path="notes/editor.txt",
        content=original,
        overwrite=False,
        quota_bytes=1024 * 1024,
    )

    snapshot = service.read_text_file(
        WORKSPACE_ID,
        path="notes/editor.txt",
    )

    assert snapshot["content"] == original
    assert snapshot["sha256"] == sha256(original.encode("utf-8")).hexdigest()
    service.write_file(
        WORKSPACE_ID,
        path="notes/editor.txt",
        content="首次更新\n",
        overwrite=True,
        expected_sha256=snapshot["sha256"],
        quota_bytes=1024 * 1024,
    )
    refreshed = service.read_text_file(
        WORKSPACE_ID,
        path="notes/editor.txt",
    )
    assert refreshed["sha256"] == sha256("首次更新\n".encode("utf-8")).hexdigest()

    with pytest.raises(SandboxServiceError) as stale:
        service.write_file(
            WORKSPACE_ID,
            path="notes/editor.txt",
            content="过期编辑不得覆盖\n",
            overwrite=True,
            expected_sha256=snapshot["sha256"],
            quota_bytes=1024 * 1024,
        )
    assert stale.value.code is SandboxErrorCode.EDIT_CONFLICT
    assert _read_bytes(service, "notes/editor.txt") == "首次更新\n".encode("utf-8")


def test_workspace_search_supports_regex_ignore_case_gitignore_and_modes(
    tmp_path,
):
    service = _service(tmp_path)
    for path, content in {
        ".gitignore": "ignored.txt\nignored-dir/\n",
        "src/a.py": "Alpha\nneedle 42\n",
        "src/b.TXT": "NEEDLE 99\n",
        "ignored.txt": "needle 100\n",
        "ignored-dir/hidden.py": "needle 101\n",
        "node_modules/pkg/index.py": "needle 102\n",
    }.items():
        service.write_file(
            WORKSPACE_ID,
            path=path,
            content=content,
            overwrite=False,
            quota_bytes=1024 * 1024,
        )

    content_result = service.search_files(
        WORKSPACE_ID,
        mode="content",
        pattern=r"needle\s+\d+",
        path="",
        glob="*.py",
        limit=20,
        ignore_case=True,
    )
    assert [
        (item["path"], item["line"])
        for item in content_result["items"]
    ] == [("src/a.py", 2)]
    assert content_result["skipped_ignored_files"] >= 3
    assert content_result["truncated"] is False

    file_result = service.search_files(
        WORKSPACE_ID,
        mode="files",
        pattern="*.TXT",
        path="",
        glob="",
        limit=20,
    )
    assert [item["path"] for item in file_result["items"]] == [
        "src/b.TXT"
    ]

    tree_result = service.search_files(
        WORKSPACE_ID,
        mode="tree",
        pattern="",
        path="src",
        glob="",
        limit=20,
        max_depth=1,
    )
    assert {item["path"] for item in tree_result["items"]} == {
        "src/a.py",
        "src/b.TXT",
    }

    with pytest.raises(SandboxServiceError) as invalid:
        service.search_files(
            WORKSPACE_ID,
            mode="content",
            pattern="(",
            path="",
            glob="",
            limit=20,
        )
    assert invalid.value.code is SandboxErrorCode.INVALID_PATTERN


def test_workspace_search_does_not_silently_stop_at_one_mib(tmp_path):
    service = _service(tmp_path)
    content = b"x" * (1024 * 1024 + 64) + b" TARGET_AFTER_1_MIB\n"
    service.filesystem(WORKSPACE_ID).write_bytes(
        "large.txt",
        content,
        overwrite=False,
        max_bytes=2 * 1024 * 1024,
    )

    result = service.search_files(
        WORKSPACE_ID,
        mode="content",
        pattern="TARGET_AFTER_1_MIB",
        path="",
        glob="*.txt",
        limit=10,
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["path"] == "large.txt"
    assert result["items"][0]["line"] == 1
    assert result["items"][0]["truncated"] is True
    assert result["scanned_bytes"] > 1024 * 1024
    assert result["truncated"] is False


def test_workspace_edit_exact_replacement_is_strict_and_batch_prevalidated(
    tmp_path,
):
    service = _service(tmp_path)
    service.write_file(
        WORKSPACE_ID,
        path="project/a.txt",
        content="same same\n",
        overwrite=False,
        quota_bytes=1024 * 1024,
    )
    service.write_file(
        WORKSPACE_ID,
        path="project/b.txt",
        content="before\n",
        overwrite=False,
        quota_bytes=1024 * 1024,
    )

    with pytest.raises(SandboxServiceError) as ambiguous:
        service.edit_files(
            WORKSPACE_ID,
            cwd="project",
            operations=[{
                "path": "a.txt",
                "old": "same",
                "new": "changed",
                "replace_all": False,
            }],
            quota_bytes=1024 * 1024,
        )
    assert ambiguous.value.code is SandboxErrorCode.EDIT_CONFLICT
    assert _read_bytes(service, "project/a.txt") == b"same same\n"

    with pytest.raises(SandboxServiceError) as missing:
        service.edit_files(
            WORKSPACE_ID,
            cwd="project",
            operations=[
                {
                    "path": "a.txt",
                    "old": "same",
                    "new": "changed",
                    "replace_all": True,
                },
                {
                    "path": "b.txt",
                    "old": "not-present",
                    "new": "after",
                },
            ],
            quota_bytes=1024 * 1024,
        )
    assert missing.value.code is SandboxErrorCode.EDIT_CONFLICT
    assert _read_bytes(service, "project/a.txt") == b"same same\n"
    assert _read_bytes(service, "project/b.txt") == b"before\n"

    result = service.edit_files(
        WORKSPACE_ID,
        cwd="project",
        operations=[
            {
                "path": "a.txt",
                "old": "same",
                "new": "changed",
                "replace_all": True,
            },
            {
                "path": "b.txt",
                "old": "before",
                "new": "after",
            },
        ],
        quota_bytes=1024 * 1024,
    )

    assert _read_bytes(service, "project/a.txt") == b"changed changed\n"
    assert _read_bytes(service, "project/b.txt") == b"after\n"
    assert result["file_count"] == 2
    assert {
        item["path"]: item["replacement_count"]
        for item in result["files"]
    } == {
        "project/a.txt": 2,
        "project/b.txt": 1,
    }
    assert all(len(item["old_sha256"]) == 64 for item in result["files"])
    assert all(len(item["new_sha256"]) == 64 for item in result["files"])


def test_workspace_edit_applies_multi_file_diff_as_one_batch(tmp_path):
    service = _service(tmp_path)
    for path, content in {
        "a.txt": "one\ntwo\n",
        "b.txt": "red\nblue\n",
    }.items():
        service.write_file(
            WORKSPACE_ID,
            path=path,
            content=content,
            overwrite=False,
            quota_bytes=1024 * 1024,
        )
    diff = (
        "diff --git a/a.txt b/a.txt\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " one\n"
        "-two\n"
        "+TWO\n"
        "diff --git a/b.txt b/b.txt\n"
        "--- a/b.txt\n"
        "+++ b/b.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " red\n"
        "-blue\n"
        "+BLUE\n"
    )

    result = service.edit_files(
        WORKSPACE_ID,
        operations=[{"diff": diff}],
        quota_bytes=1024 * 1024,
    )

    assert _read_bytes(service, "a.txt") == b"one\nTWO\n"
    assert _read_bytes(service, "b.txt") == b"red\nBLUE\n"
    assert result["file_count"] == 2
    assert sum(item["hunks_applied"] for item in result["files"]) == 2


def test_workspace_edit_rolls_back_all_files_when_write_phase_fails(
    tmp_path,
    monkeypatch,
):
    service = _service(tmp_path)
    for path, content in {"a.txt": "a\n", "b.txt": "b\n"}.items():
        service.write_file(
            WORKSPACE_ID,
            path=path,
            content=content,
            overwrite=False,
            quota_bytes=1024 * 1024,
        )
    original_write = service._write_bytes_locked
    calls = 0

    def fail_second_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "注入写入失败",
            )
        return original_write(*args, **kwargs)

    monkeypatch.setattr(
        service,
        "_write_bytes_locked",
        fail_second_write,
    )

    with pytest.raises(SandboxServiceError):
        service.edit_files(
            WORKSPACE_ID,
            operations=[
                {"path": "a.txt", "old": "a", "new": "A"},
                {"path": "b.txt", "old": "b", "new": "B"},
            ],
            quota_bytes=1024 * 1024,
        )

    assert _read_bytes(service, "a.txt") == b"a\n"
    assert _read_bytes(service, "b.txt") == b"b\n"
    assert not [
        entry
        for entry in service._runtime_filesystem(
            WORKSPACE_ID
        ).list_entries("")
        if entry.path.startswith(".nanobot-workspace-edit-")
    ]


def test_workspace_edit_recovers_partial_journal_before_next_write(
    tmp_path,
):
    service = _service(tmp_path)
    for path, content in {"a.txt": "a\n", "b.txt": "b\n"}.items():
        service.write_file(
            WORKSPACE_ID,
            path=path,
            content=content,
            overwrite=False,
            quota_bytes=1024 * 1024,
        )
    originals = {"a.txt": b"a\n", "b.txt": b"b\n"}
    finals = {"a.txt": b"A\n", "b.txt": b"B\n"}
    service._write_edit_journal(
        WORKSPACE_ID,
        originals=originals,
        finals=finals,
    )
    service.filesystem(WORKSPACE_ID).write_bytes(
        "a.txt",
        finals["a.txt"],
        overwrite=True,
        max_bytes=1024,
    )

    result = service.edit_files(
        WORKSPACE_ID,
        operations=[{
            "path": "a.txt",
            "old": "a",
            "new": "A2",
        }],
        quota_bytes=1024 * 1024,
    )

    assert result["recovery_status"] == "rolled_back"
    assert _read_bytes(service, "a.txt") == b"A2\n"
    assert _read_bytes(service, "b.txt") == b"b\n"


def test_workspace_edit_schema_rejects_nested_unknown_fields():
    from core.tool_schema_preview import STATIC_TOOL_SCHEMAS

    schema = STATIC_TOOL_SCHEMAS["workspace_edit"]["parameters"]
    valid = _validate_arguments(
        {
            "operations": [{
                "path": "a.txt",
                "old": "a",
                "new": "b",
            }],
        },
        schema,
    )
    assert valid["operations"][0]["path"] == "a.txt"

    with pytest.raises(SandboxServiceError) as invalid:
        _validate_arguments(
            {
                "operations": [{
                    "path": "a.txt",
                    "old": "a",
                    "new": "b",
                    "workspace_id": WORKSPACE_ID,
                }],
            },
            schema,
        )
    assert invalid.value.code is SandboxErrorCode.AUTHORIZATION_FAILED


def test_workspace_edit_hashes_match_persisted_content(tmp_path):
    service = _service(tmp_path)
    service.write_file(
        WORKSPACE_ID,
        path="hash.txt",
        content="old\n",
        overwrite=False,
        quota_bytes=1024 * 1024,
    )

    result = service.edit_files(
        WORKSPACE_ID,
        operations=[{
            "path": "hash.txt",
            "old": "old",
            "new": "new",
        }],
        quota_bytes=1024 * 1024,
    )

    item = result["files"][0]
    assert item["old_sha256"] == sha256(b"old\n").hexdigest()
    assert item["new_sha256"] == sha256(b"new\n").hexdigest()
