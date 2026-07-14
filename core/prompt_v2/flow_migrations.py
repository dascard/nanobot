"""Prompt Runtime flow 的受控迁移、备份与回滚。"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import tempfile
from typing import Any

from core.prompt_v2.flow import (
    PromptFlowError,
    validate_flow,
    validate_runtime_contract,
)
from core.prompt_v2.flow_storage import (
    FlowStorageError,
    assert_no_symlink_components,
    atomic_replace_bytes,
    ensure_directory_without_symlinks,
    flow_write_lock,
    fsync_directory,
)


_BACKUP_NAME_RE = re.compile(
    r"^chat-flow\.(?P<timestamp>\d{8}T\d{12}Z)\."
    r"(?P<sha>[0-9a-f]{12})\.json\.bak$"
)
_SESSION_GUIDANCE_NODE = {
    "id": "session_guidance",
    "type": "runtime",
    "label": "system: session_guidance",
    "runtime_key": "session_guidance",
}


class PromptFlowMigrationError(PromptFlowError):
    """运行时 flow 无法安全迁移或回滚。"""


def _migration_error(message: str, exc: Exception | None = None) -> PromptFlowMigrationError:
    error = PromptFlowMigrationError(message)
    if exc is not None:
        error.__cause__ = exc
    return error


def _validate_base_flow(flow: Any) -> None:
    if not isinstance(flow, dict):
        raise PromptFlowMigrationError("flow 顶层必须是 JSON object")
    try:
        validate_flow(flow)
    except (PromptFlowError, TypeError, ValueError, AttributeError) as exc:
        raise _migration_error(f"flow 基础校验失败: {exc}", exc)


def _validate_migrated_flow(flow: dict[str, Any]) -> None:
    try:
        normalized = validate_flow(flow)
        validate_runtime_contract(normalized)
    except (PromptFlowError, TypeError, ValueError, AttributeError) as exc:
        raise _migration_error(f"flow 运行契约校验失败: {exc}", exc)


def _is_unconditional(edge: dict[str, Any]) -> bool:
    return not edge.get("chat_types") and not edge.get("platforms")


def _validate_existing_guidance_relationships(flow: dict[str, Any]) -> None:
    edges = list(flow.get("edges") or [])
    identity_outgoing = [
        edge for edge in edges if edge.get("from") == "identity_context"
    ]
    direct_edges = [
        edge
        for edge in identity_outgoing
        if edge.get("to") == "session_guidance"
    ]
    guidance_incoming = [
        edge for edge in edges if edge.get("to") == "session_guidance"
    ]
    guidance_outgoing = [
        edge for edge in edges if edge.get("from") == "session_guidance"
    ]

    if (
        len(identity_outgoing) != 1
        or len(direct_edges) != 1
        or not _is_unconditional(direct_edges[0])
    ):
        raise PromptFlowMigrationError(
            "identity_context 必须仅通过一条无条件边直连 session_guidance"
        )
    if len(guidance_incoming) != 1 or guidance_incoming[0] is not direct_edges[0]:
        raise PromptFlowMigrationError(
            "session_guidance 只能接收 identity_context 的唯一入边"
        )
    if not guidance_outgoing:
        raise PromptFlowMigrationError("session_guidance 必须至少有一条下游边")


def migrate_session_guidance_flow(
    flow: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """在 identity 后插入 session guidance，并保留原下游边全部元数据。"""
    migrated = copy.deepcopy(flow)
    _validate_base_flow(migrated)

    nodes = list(migrated.get("nodes") or [])
    identity_indexes = [
        index
        for index, node in enumerate(nodes)
        if node.get("id") == "identity_context"
    ]
    if len(identity_indexes) != 1:
        raise PromptFlowMigrationError("flow 必须且只能包含一个 identity_context 节点")

    guidance_nodes = [
        node
        for node in nodes
        if node.get("id") == "session_guidance"
        or node.get("runtime_key") == "session_guidance"
    ]
    if guidance_nodes:
        if len(guidance_nodes) != 1 or guidance_nodes[0] != {
            **guidance_nodes[0],
            "id": "session_guidance",
            "type": "runtime",
            "runtime_key": "session_guidance",
        }:
            raise PromptFlowMigrationError("session_guidance 节点不是唯一规范节点")
        _validate_existing_guidance_relationships(migrated)
        _validate_migrated_flow(migrated)
        return migrated, False

    edges = list(migrated.get("edges") or [])
    identity_outgoing = [
        edge for edge in edges if edge.get("from") == "identity_context"
    ]
    if not identity_outgoing:
        raise PromptFlowMigrationError("identity_context 缺少可迁移的下游边")

    identity_index = identity_indexes[0]
    nodes.insert(identity_index + 1, copy.deepcopy(_SESSION_GUIDANCE_NODE))
    migrated["nodes"] = nodes

    rewritten_edges: list[dict[str, Any]] = []
    direct_inserted = False
    for edge in edges:
        if edge.get("from") != "identity_context":
            rewritten_edges.append(edge)
            continue
        if not direct_inserted:
            rewritten_edges.append(
                {"from": "identity_context", "to": "session_guidance"}
            )
            direct_inserted = True
        rewritten = copy.deepcopy(edge)
        rewritten["from"] = "session_guidance"
        rewritten_edges.append(rewritten)
    migrated["edges"] = rewritten_edges

    _validate_migrated_flow(migrated)
    return migrated, True


def _read_regular_file(path: Path, *, label: str) -> bytes:
    path = Path(path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _migration_error(f"无法读取{label}: {path}", exc)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PromptFlowMigrationError(f"{label}必须是普通文件且不能是符号链接: {path}")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise PromptFlowMigrationError(f"{label}不是普通文件: {path}")
            return handle.read()
    except PromptFlowMigrationError:
        raise
    except OSError as exc:
        raise _migration_error(f"无法读取{label}: {path}", exc)


def _decode_flow(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _migration_error(f"{label}不是合法 UTF-8 JSON: {exc}", exc)
    if not isinstance(value, dict):
        raise PromptFlowMigrationError(f"{label}顶层必须是 JSON object")
    return value


def _ensure_backup_directory(backup_dir: Path) -> Path:
    try:
        return ensure_directory_without_symlinks(Path(backup_dir))
    except FlowStorageError as exc:
        raise _migration_error(f"备份目录包含符号链接或不安全组件: {exc}", exc)


def _create_exact_backup(data: bytes, *, backup_dir: Path) -> Path:
    backup_dir = _ensure_backup_directory(backup_dir)
    digest = hashlib.sha256(data).hexdigest()
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".chat-flow-backup.",
        suffix=".tmp",
        dir=backup_dir,
    )
    temp_path = Path(temp_name)
    installed_path: Path | None = None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(1000):
            timestamp = datetime.now(timezone.utc) + timedelta(microseconds=attempt)
            name = (
                f"chat-flow.{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}."
                f"{digest[:12]}.json.bak"
            )
            candidate = backup_dir / name
            try:
                os.link(temp_path, candidate, follow_symlinks=False)
            except FileExistsError:
                continue
            installed_path = candidate
            break
        if installed_path is None:
            raise PromptFlowMigrationError("无法生成唯一的 flow 备份文件名")
        fsync_directory(backup_dir)
        return installed_path
    finally:
        temp_path.unlink(missing_ok=True)


def default_session_guidance_flow_backup_dir(runtime_flow_path: Path) -> Path:
    """从 `<runtime>/chat/flow.json` 推导同级隔离备份目录。"""
    runtime_root = Path(runtime_flow_path).parent.parent
    return (
        runtime_root.parent
        / "prompt_template_backups"
        / "session_guidance_flow"
    )


def upgrade_runtime_flow_file(
    runtime_flow_path: Path,
    *,
    backup_dir: Path,
) -> dict[str, Any]:
    """验证并原子升级 runtime flow；已符合合同则保持字节不变。"""
    runtime_flow_path = Path(runtime_flow_path)
    try:
        with flow_write_lock(runtime_flow_path):
            original = _read_regular_file(runtime_flow_path, label="runtime flow")
            flow = _decode_flow(original, label="runtime flow")
            migrated, changed = migrate_session_guidance_flow(flow)
            if not changed:
                return {
                    "flow_migrated": False,
                    "flow_backup_path": "",
                    "runtime_flow_path": str(runtime_flow_path),
                }

            backup_path = _create_exact_backup(
                original,
                backup_dir=Path(backup_dir),
            )
            updated = (
                json.dumps(migrated, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            atomic_replace_bytes(runtime_flow_path, updated)
            return {
                "flow_migrated": True,
                "flow_backup_path": str(backup_path),
                "runtime_flow_path": str(runtime_flow_path),
            }
    except FlowStorageError as exc:
        raise _migration_error(f"runtime flow 存储路径不安全: {exc}", exc)


def list_session_guidance_flow_backups(
    *,
    backup_dir: Path,
) -> list[dict[str, Any]]:
    """列出安全备份元数据，不返回 flow 正文或完整路径。"""
    backup_dir = Path(backup_dir)
    try:
        backup_dir = assert_no_symlink_components(backup_dir)
    except FlowStorageError as exc:
        raise _migration_error(f"备份目录包含符号链接或不安全组件: {exc}", exc)
    if not backup_dir.exists():
        return []
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise PromptFlowMigrationError("备份目录不是安全的普通目录")

    items: list[dict[str, Any]] = []
    for candidate in backup_dir.iterdir():
        match = _BACKUP_NAME_RE.fullmatch(candidate.name)
        if match is None or candidate.is_symlink():
            continue
        try:
            data = _read_regular_file(candidate, label="flow 备份")
        except PromptFlowMigrationError:
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest[:12] != match.group("sha"):
            continue
        created = datetime.strptime(
            match.group("timestamp"),
            "%Y%m%dT%H%M%S%fZ",
        ).replace(tzinfo=timezone.utc)
        items.append(
            {
                "name": candidate.name,
                "created_at": created.isoformat().replace("+00:00", "Z"),
                "size_bytes": len(data),
                "sha256": digest,
            }
        )
    return sorted(items, key=lambda item: item["name"], reverse=True)


def _safe_backup_path(*, backup_dir: Path, backup_name: str) -> Path:
    raw_name = str(backup_name or "")
    if (
        not raw_name
        or "\x00" in raw_name
        or "/" in raw_name
        or "\\" in raw_name
        or raw_name in {".", ".."}
        or Path(raw_name).is_absolute()
        or PureWindowsPath(raw_name).is_absolute()
        or _BACKUP_NAME_RE.fullmatch(raw_name) is None
    ):
        raise PromptFlowMigrationError("backup_name 非法")

    backup_dir = Path(backup_dir)
    try:
        backup_dir = assert_no_symlink_components(backup_dir)
    except FlowStorageError as exc:
        raise _migration_error(f"备份目录包含符号链接或不安全组件: {exc}", exc)
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise PromptFlowMigrationError("备份目录不存在或不是安全目录")
    candidate = backup_dir / raw_name
    if candidate.is_symlink():
        raise PromptFlowMigrationError("flow 备份不能是符号链接")
    return candidate


def rollback_session_guidance_flow(
    runtime_flow_path: Path,
    *,
    backup_dir: Path,
    backup_name: str,
) -> Path:
    """校验指定旧备份，保护当前 bytes 后原子恢复。"""
    runtime_flow_path = Path(runtime_flow_path)
    backup_dir = Path(backup_dir)
    try:
        with flow_write_lock(runtime_flow_path):
            selected = _safe_backup_path(
                backup_dir=backup_dir,
                backup_name=backup_name,
            )
            selected_bytes = _read_regular_file(selected, label="flow 备份")
            match = _BACKUP_NAME_RE.fullmatch(selected.name)
            selected_digest = hashlib.sha256(selected_bytes).hexdigest()
            if match is None or selected_digest[:12] != match.group("sha"):
                raise PromptFlowMigrationError("flow 备份摘要与文件名不一致")
            selected_flow = _decode_flow(selected_bytes, label="flow 备份")
            _validate_base_flow(selected_flow)

            current_bytes = _read_regular_file(
                runtime_flow_path,
                label="runtime flow",
            )
            _create_exact_backup(current_bytes, backup_dir=backup_dir)
            atomic_replace_bytes(runtime_flow_path, selected_bytes)
            return runtime_flow_path
    except FlowStorageError as exc:
        raise _migration_error(f"runtime flow 存储路径不安全: {exc}", exc)
