import os
import socket
from uuid import UUID

import pytest
from sqlalchemy.orm import sessionmaker

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.identity import Principal
from core.sandbox.paths import SafeWorkspaceFilesystem, SandboxStorageLayout
from core.sandbox.workspace_service import (
    WorkspacePolicy,
    WorkspaceService,
)


def _principal(owner_id: str) -> Principal:
    return Principal(platform="qq", owner_type="user", owner_id=owner_id)


def test_default_workspace_is_idempotent_and_owner_id_is_not_a_directory_name(
    db_session,
    tmp_path,
):
    service = WorkspaceService(db_session)
    first = service.ensure_default(_principal("owner-A"))
    second = service.ensure_default(_principal("owner-A"))

    assert first.id == second.id
    assert str(UUID(first.id)) == first.id
    assert db_session.query(type(first)).count() == 1

    layout = SandboxStorageLayout(tmp_path / "sandbox-data")
    workspace_path = layout.ensure_workspace(first.id)
    assert "owner-A" not in str(workspace_path)
    assert workspace_path.name == "data"


def test_workspace_owner_acl_does_not_reveal_foreign_workspace(db_session):
    service = WorkspaceService(db_session)
    workspace_a = service.ensure_default(_principal("owner-A"))
    service.ensure_default(_principal("owner-B"))

    with pytest.raises(SandboxServiceError) as raised:
        service.require_owned(_principal("owner-B"), workspace_a.id)

    assert raised.value.code is SandboxErrorCode.AUTHORIZATION_FAILED
    assert workspace_a.id not in str(raised.value)


def test_workspace_usage_delta_atomically_rejects_out_of_bounds_projection(db_session):
    policy = WorkspacePolicy(
        default_quota_bytes=100,
        total_quota_bytes=150,
        disk_max_percent=80,
        disk_min_free_bytes=50,
    )
    service = WorkspaceService(db_session, policy=policy)
    workspace = service.ensure_default(_principal("owner-A"))
    service.record_usage_delta(
        workspace.id,
        delta_bytes=90,
        observed_used_bytes=90,
    )

    with pytest.raises(SandboxServiceError) as quota_error:
        service.record_usage_delta(
            workspace.id,
            delta_bytes=11,
            observed_used_bytes=101,
        )
    assert quota_error.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE

def test_workspace_usage_delta_does_not_lose_stale_session_updates(db_session):
    policy = WorkspacePolicy(
        default_quota_bytes=100,
        total_quota_bytes=1000,
        disk_min_free_bytes=0,
    )
    workspace = WorkspaceService(db_session, policy=policy).ensure_default(
        _principal("owner-A"),
    )
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False)
    first = factory()
    second = factory()
    try:
        first.get(type(workspace), workspace.id)
        second.get(type(workspace), workspace.id)
        WorkspaceService(first, policy=policy).record_usage_delta(
            workspace.id,
            delta_bytes=10,
            observed_used_bytes=10,
        )
        first.commit()
        WorkspaceService(second, policy=policy).record_usage_delta(
            workspace.id,
            delta_bytes=15,
            observed_used_bytes=25,
        )
        second.commit()
    finally:
        first.close()
        second.close()

    db_session.expire_all()
    assert db_session.get(type(workspace), workspace.id).used_bytes == 25


def test_safe_workspace_file_operations_reject_traversal_and_special_files(tmp_path):
    # AF_UNIX 的宿主路径通常限制为 108 字节，使用 pytest 层级中的短目录名。
    root = tmp_path.parent / "ws-special"
    root.mkdir()
    filesystem = SafeWorkspaceFilesystem(root)

    written = filesystem.write_bytes(
        "results/report.txt",
        "中文内容".encode(),
        overwrite=False,
        max_bytes=1024,
    )
    assert written.path == "results/report.txt"
    assert filesystem.read_bytes("results/report.txt", offset=0, limit=1024) == "中文内容".encode()

    os.symlink(root / "results" / "report.txt", root / "link.txt")
    os.mkfifo(root / "pipe")
    unix_socket = socket.socket(socket.AF_UNIX)
    unix_socket.bind(str(root / "socket"))
    try:
        invalid_paths = ["../../etc/passwd", "/etc/passwd", "link.txt", "pipe", "socket"]
        for candidate in invalid_paths:
            with pytest.raises(SandboxServiceError) as raised:
                filesystem.read_bytes(candidate, offset=0, limit=16)
            assert str(root) not in str(raised.value)
            assert raised.value.code in {
                SandboxErrorCode.INVALID_PATH,
                SandboxErrorCode.UNSUPPORTED_FILE_TYPE,
            }
    finally:
        unix_socket.close()

    listed = {entry.path: entry.type for entry in filesystem.list_entries()}
    assert listed["link.txt"] == "symlink"
    assert listed["pipe"] == "other"
    assert listed["socket"] == "other"


def test_atomic_write_does_not_follow_parent_symlink(tmp_path):
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    os.symlink(outside, root / "escape")
    filesystem = SafeWorkspaceFilesystem(root)

    with pytest.raises(SandboxServiceError) as raised:
        filesystem.write_bytes(
            "escape/payload.txt",
            b"blocked",
            overwrite=True,
            max_bytes=1024,
        )

    assert raised.value.code is SandboxErrorCode.INVALID_PATH
    assert not (outside / "payload.txt").exists()
