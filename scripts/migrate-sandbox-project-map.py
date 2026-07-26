#!/usr/bin/env python3
"""把旧 Sandbox project quota TSV 一次性、失败关闭地迁入数据库。"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from uuid import UUID


PROJECT_ID_MIN = 10000
PROJECT_ID_MAX = 2_147_483_647
QUOTA_MIN_BYTES = 1024 * 1024
QUOTA_MAX_BYTES = 1024 * 1024 * 1024 * 1024
MAX_MAP_BYTES = 1024 * 1024
REQUIRED_MIGRATIONS = {
    "20260722_sandbox_control_plane_tables",
    "20260722_sandbox_project_sequence_seed",
    "20260725_sandbox_runtime_project_quotas",
}


class MigrationError(RuntimeError):
    """可安全显示给宿主管理员的迁移失败。"""


@dataclass(frozen=True, slots=True)
class LegacyProjectBinding:
    legacy_owner_id: str
    workspace_id: str
    project_id: int
    quota_bytes: int


def _canonical_workspace_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise MigrationError("TSV 包含无效 workspace_id") from exc
    if str(parsed) != value or parsed.version not in {1, 2, 3, 4, 5}:
        raise MigrationError("TSV workspace_id 必须是小写规范 UUID v1-v5")
    return value


def _bounded_integer(
    value: str,
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if not value.isascii() or not value.isdigit():
        raise MigrationError(f"TSV {label} 必须是十进制整数")
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise MigrationError(f"TSV {label} 超出允许范围")
    return parsed


def parse_project_map(raw: bytes) -> list[LegacyProjectBinding]:
    if len(raw) > MAX_MAP_BYTES:
        raise MigrationError("旧 project map 超过 1 MiB 上限")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise MigrationError("旧 project map 不是合法 UTF-8") from exc

    by_workspace: dict[str, LegacyProjectBinding] = {}
    by_project: dict[int, LegacyProjectBinding] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            raise MigrationError(f"TSV 第 {line_number} 行必须恰好包含四列")
        owner_id, workspace_value, project_value, quota_value = fields
        if not owner_id or len(owner_id) > 255 or "\x00" in owner_id:
            raise MigrationError(f"TSV 第 {line_number} 行 legacy owner 无效")
        workspace_id = _canonical_workspace_id(workspace_value)
        project_id = _bounded_integer(
            project_value,
            minimum=PROJECT_ID_MIN,
            maximum=PROJECT_ID_MAX,
            label="project_id",
        )
        quota_bytes = _bounded_integer(
            quota_value,
            minimum=QUOTA_MIN_BYTES,
            maximum=QUOTA_MAX_BYTES,
            label="quota_bytes",
        )
        item = LegacyProjectBinding(
            legacy_owner_id=owner_id,
            workspace_id=workspace_id,
            project_id=project_id,
            quota_bytes=quota_bytes,
        )
        old_workspace = by_workspace.get(workspace_id)
        if old_workspace is not None and old_workspace != item:
            raise MigrationError("同一 Workspace 在 TSV 中存在冲突映射")
        old_project = by_project.get(project_id)
        if (
            old_project is not None
            and old_project.workspace_id != workspace_id
        ):
            raise MigrationError("同一 project_id 在 TSV 中分配给多个 Workspace")
        by_workspace[workspace_id] = item
        by_project[project_id] = item
    if not by_workspace:
        raise MigrationError("旧 project map 没有可迁移记录")
    return sorted(by_workspace.values(), key=lambda item: item.workspace_id)


def read_project_map(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        file_fd = os.open(path, flags)
        try:
            metadata = os.fstat(file_fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > MAX_MAP_BYTES
            ):
                raise MigrationError("旧 project map 必须是受限大小的普通文件")
            raw = os.read(file_fd, MAX_MAP_BYTES + 1)
            if len(raw) != metadata.st_size:
                raise MigrationError("读取期间旧 project map 发生变化")
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise MigrationError("无法安全读取旧 project map") from exc
    return raw


def backup_project_map(source: Path, raw: bytes) -> Path:
    if os.geteuid() != 0:
        raise MigrationError("实际迁移必须以 root 运行")
    metadata = source.lstat()
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise MigrationError("旧 project map 必须由 root 拥有且禁止组/其他用户访问")
    backup = source.with_name(source.name + ".pre-database-migration.bak")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        file_fd = os.open(backup, flags, 0o600)
    except FileExistsError:
        existing = read_project_map(backup)
        if not hashlib.sha256(existing).digest() == hashlib.sha256(raw).digest():
            raise MigrationError("旧 project map 备份已存在但内容不一致")
        return backup
    except OSError as exc:
        raise MigrationError("无法创建旧 project map root-only 备份") from exc
    try:
        view = memoryview(raw)
        while view:
            written = os.write(file_fd, view)
            view = view[written:]
        os.fsync(file_fd)
        os.fchmod(file_fd, 0o600)
        os.fchown(file_fd, 0, 0)
    except OSError as exc:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass
        raise MigrationError("旧 project map 备份写入失败") from exc
    finally:
        os.close(file_fd)
    return backup


def _workspace_path(data_root: Path, workspace_id: str) -> Path:
    if not data_root.is_absolute():
        raise MigrationError("Sandbox 数据根目录必须是绝对路径")
    try:
        resolved_root = data_root.resolve(strict=True)
    except OSError as exc:
        raise MigrationError("Sandbox 数据根目录不存在") from exc
    path = resolved_root / "workspaces" / workspace_id[:2] / workspace_id / "data"
    try:
        metadata = path.lstat()
        resolved_path = path.resolve(strict=True)
    except OSError as exc:
        raise MigrationError("TSV 对应 Workspace 数据目录不存在") from exc
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink() or resolved_path != path:
        raise MigrationError("TSV 对应 Workspace 数据目录不安全")
    return path


def inspect_project_id(data_root: Path, binding: LegacyProjectBinding) -> int:
    path = _workspace_path(data_root, binding.workspace_id)
    try:
        result = subprocess.run(
            ["/usr/bin/lsattr", "-d", "-p", "--", os.fspath(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            shell=False,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MigrationError("无法回读 Workspace project ID") from exc
    observed = next(
        (
            int(token)
            for token in str(result.stdout or "").split()
            if token.isascii() and token.isdigit()
        ),
        0,
    )
    if result.returncode != 0 or observed != binding.project_id:
        raise MigrationError("宿主 Workspace project ID 与 TSV 不一致")
    return observed


def validate_host_bindings(
    data_root: Path,
    bindings: Iterable[LegacyProjectBinding],
    *,
    inspector: Callable[[Path, LegacyProjectBinding], int] = inspect_project_id,
) -> None:
    for binding in bindings:
        if inspector(data_root, binding) != binding.project_id:
            raise MigrationError("宿主 Workspace project ID 与 TSV 不一致")


def _require_tables(connection: sqlite3.Connection) -> None:
    required = {
        "schema_migrations",
        "workspaces",
        "workspace_quota_bindings",
        "workspace_runtime_quota_bindings",
        "sandbox_project_sequences",
    }
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    available = {str(row[0]) for row in rows}
    if not required <= available:
        raise MigrationError("数据库尚未完成 Sandbox 控制面 Schema 迁移")
    placeholders = ", ".join("?" for _ in REQUIRED_MIGRATIONS)
    applied = {
        str(row[0])
        for row in connection.execute(
            "SELECT version FROM schema_migrations "
            f"WHERE version IN ({placeholders})",
            tuple(sorted(REQUIRED_MIGRATIONS)),
        ).fetchall()
    }
    if applied != REQUIRED_MIGRATIONS:
        raise MigrationError("数据库尚未记录 Sandbox 控制面迁移版本")


def migrate_database(
    database_path: Path,
    bindings: Iterable[LegacyProjectBinding],
    *,
    apply: bool,
) -> tuple[int, int]:
    try:
        metadata = database_path.lstat()
    except OSError as exc:
        raise MigrationError("SQLite 数据库不存在") from exc
    if not stat.S_ISREG(metadata.st_mode) or database_path.is_symlink():
        raise MigrationError("SQLite 数据库路径必须是普通文件且不能是符号链接")

    items = list(bindings)
    inserted = 0
    unchanged = 0
    connection = sqlite3.connect(database_path, timeout=5.0, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        _require_tables(connection)
        for item in items:
            workspace = connection.execute(
                "SELECT quota_bytes, used_bytes, status FROM workspaces WHERE id = ?",
                (item.workspace_id,),
            ).fetchone()
            if workspace is None:
                raise MigrationError("TSV 引用了数据库中不存在的 Workspace")
            if (
                str(workspace[2]) != "active"
                or int(workspace[0]) != item.quota_bytes
                or int(workspace[1]) > item.quota_bytes
            ):
                raise MigrationError("Workspace 状态、逻辑配额或当前占用与 TSV 冲突")
            by_workspace = connection.execute(
                "SELECT project_id, desired_quota_bytes "
                "FROM workspace_quota_bindings WHERE workspace_id = ?",
                (item.workspace_id,),
            ).fetchone()
            by_project = connection.execute(
                "SELECT workspace_id FROM workspace_quota_bindings WHERE project_id = ?",
                (item.project_id,),
            ).fetchone()
            runtime_by_project = connection.execute(
                "SELECT workspace_id FROM workspace_runtime_quota_bindings "
                "WHERE project_id = ?",
                (item.project_id,),
            ).fetchone()
            if by_project is not None and str(by_project[0]) != item.workspace_id:
                raise MigrationError("数据库 project_id 已绑定其他 Workspace")
            if runtime_by_project is not None:
                raise MigrationError(
                    "数据库 project_id 已绑定 Workspace Runtime"
                )
            if by_workspace is not None:
                if (
                    int(by_workspace[0]) != item.project_id
                    or int(by_workspace[1]) != item.quota_bytes
                ):
                    raise MigrationError("数据库 Workspace quota 绑定与 TSV 冲突")
                unchanged += 1
                continue
            inserted += 1
            if apply:
                connection.execute(
                    "INSERT INTO workspace_quota_bindings("
                    "workspace_id, project_id, desired_quota_bytes, "
                    "applied_quota_bytes, status, generation, "
                    "last_error_code, last_error_summary"
                    ") VALUES (?, ?, ?, 0, 'pending', 1, '', '')",
                    (item.workspace_id, item.project_id, item.quota_bytes),
                )

        max_project_id = max(item.project_id for item in items)
        existing_max = connection.execute(
            "SELECT MAX(project_id) FROM ("
            "SELECT project_id FROM workspace_quota_bindings "
            "UNION ALL "
            "SELECT project_id FROM workspace_runtime_quota_bindings"
            ")"
        ).fetchone()
        next_value = max(
            max_project_id,
            int(existing_max[0] or PROJECT_ID_MIN - 1),
        ) + 1
        sequence = connection.execute(
            "SELECT next_value FROM sandbox_project_sequences WHERE name = 'workspace'"
        ).fetchone()
        next_value = max(next_value, int(sequence[0]) if sequence else PROJECT_ID_MIN)
        if apply:
            if sequence is None:
                connection.execute(
                    "INSERT INTO sandbox_project_sequences(name, next_value) "
                    "VALUES ('workspace', ?)",
                    (next_value,),
                )
            else:
                connection.execute(
                    "UPDATE sandbox_project_sequences SET next_value = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE name = 'workspace'",
                    (next_value,),
                )
            connection.commit()
        else:
            connection.rollback()
    except sqlite3.Error as exc:
        connection.rollback()
        raise MigrationError("SQLite quota 迁移事务失败") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return inserted, unchanged


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把旧 Sandbox project quota TSV 失败关闭地迁入 SQLite",
    )
    parser.add_argument(
        "--map",
        type=Path,
        default=Path("/etc/nanobot/sandbox-projects.tsv"),
        help="旧 TSV 路径",
    )
    parser.add_argument("--database", type=Path, required=True, help="SQLite 数据库路径")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("/srv/nanobot"),
        help="Sandbox 数据根目录",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入；省略时只执行完整预检并回滚数据库事务",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(list(sys.argv[1:] if argv is None else argv))
    try:
        raw = read_project_map(args.map)
        bindings = parse_project_map(raw)
        validate_host_bindings(args.data_root, bindings)
        backup = None
        if args.apply:
            backup = backup_project_map(args.map, raw)
        inserted, unchanged = migrate_database(
            args.database,
            bindings,
            apply=bool(args.apply),
        )
    except MigrationError as exc:
        print(f"迁移失败：{exc}", file=sys.stderr)
        return 1

    mode = "已迁移" if args.apply else "预检通过（未写入）"
    print(
        f"{mode}：记录={len(bindings)} 新增={inserted} 幂等={unchanged} "
        "grant_created=0 binding_status=pending"
    )
    if backup is not None:
        print(f"旧 TSV root-only 备份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
