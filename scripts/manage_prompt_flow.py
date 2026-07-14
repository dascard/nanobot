#!/usr/bin/env python3
"""检查和回滚 Prompt Runtime flow。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.prompt_v2.flow import runtime_flow_path  # noqa: E402
from core.prompt_v2.flow_migrations import (  # noqa: E402
    PromptFlowMigrationError,
    default_session_guidance_flow_backup_dir,
    list_session_guidance_flow_backups,
    migrate_session_guidance_flow,
    rollback_session_guidance_flow,
)
from core.prompt_v2.flow_storage import (  # noqa: E402
    FlowStorageError,
    assert_no_symlink_components,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="管理 Prompt Runtime flow 迁移备份")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "check-session-guidance",
        help="只读检查 session_guidance flow 是否需要迁移",
    )
    subparsers.add_parser(
        "list-session-guidance-backups",
        help="列出 session_guidance flow 备份元数据",
    )
    rollback = subparsers.add_parser(
        "rollback-session-guidance",
        help="显式选择备份并原子回滚 session_guidance flow",
    )
    rollback.add_argument("--backup-name", required=True, help="严格合法的备份文件名")
    return parser


def _check(runtime_path: Path) -> dict[str, object]:
    try:
        assert_no_symlink_components(runtime_path)
    except FlowStorageError as exc:
        raise PromptFlowMigrationError(
            f"runtime flow 路径包含符号链接或不安全组件: {exc}"
        ) from exc
    if not runtime_path.is_file():
        raise PromptFlowMigrationError("runtime flow 不存在或不是安全的普通文件")
    try:
        value = json.loads(runtime_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptFlowMigrationError(f"runtime flow 读取失败: {exc}") from exc
    if not isinstance(value, dict):
        raise PromptFlowMigrationError("runtime flow 顶层必须是 JSON object")
    _migrated, changed = migrate_session_guidance_flow(value)
    return {
        "runtime_flow_path": str(runtime_path),
        "needs_migration": changed,
        "valid": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtime_path = runtime_flow_path()
    backup_dir = default_session_guidance_flow_backup_dir(runtime_path)
    try:
        if args.command == "check-session-guidance":
            result: object = _check(runtime_path)
        elif args.command == "list-session-guidance-backups":
            result = {
                "backups": list_session_guidance_flow_backups(
                    backup_dir=backup_dir,
                )
            }
        else:
            restored = rollback_session_guidance_flow(
                runtime_path,
                backup_dir=backup_dir,
                backup_name=args.backup_name,
            )
            result = {"restored": True, "runtime_flow_path": str(restored)}
    except (PromptFlowMigrationError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
