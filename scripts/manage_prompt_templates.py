#!/usr/bin/env python3
"""Prompt Runtime 模板基线审计与显式迁移 CLI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, NoReturn


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.prompt_v2.template_baseline import TemplateBaselineError  # noqa: E402
from core.prompt_v2.template_migration import (  # noqa: E402
    TemplateMigrationError,
    TemplateMigrationService,
)


_SCHEMA_VERSION = 1


def _emit(payload: dict[str, Any], *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    stream.write(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _command_from_argv() -> str:
    return str(sys.argv[1] if len(sys.argv) > 1 else "").strip()


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        _emit(
            {
                "schema_version": _SCHEMA_VERSION,
                "ok": False,
                "command": _command_from_argv(),
                "error": {
                    "code": "invalid_arguments",
                    "message": str(message),
                    "retryable": False,
                },
            },
            error=True,
        )
        raise SystemExit(2)


def _add_actor(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--modified-by", default="cli")


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="管理 Prompt Runtime 模板基线")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit")
    audit.add_argument("--template-key", action="append", dest="template_keys")

    plan = subparsers.add_parser("plan")
    plan.add_argument("--template-key", action="append", dest="template_keys")
    _add_actor(plan)

    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--template-key", required=True)
    resolve.add_argument(
        "--strategy",
        required=True,
        choices=(
            "adopt-in-sync",
            "keep-runtime",
            "use-default",
            "merged-file",
        ),
    )
    resolve.add_argument("--merged-file", type=Path)
    _add_actor(resolve)

    apply = subparsers.add_parser("apply")
    apply.add_argument("--plan-id", required=True)

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--operation-id", required=True)
    rollback.add_argument("--reason", required=True)
    _add_actor(rollback)
    return parser


def _execute(args: argparse.Namespace) -> Any:
    service = TemplateMigrationService.from_environment()
    if args.command == "audit":
        return service.audit(args.template_keys)
    if args.command == "plan":
        return service.plan(
            template_keys=args.template_keys,
            modified_by=args.modified_by,
        )
    if args.command == "resolve":
        return service.resolve(
            template_key=args.template_key,
            strategy=args.strategy,
            merged_file=args.merged_file,
            modified_by=args.modified_by,
        )
    if args.command == "apply":
        return service.apply(args.plan_id)
    if args.command == "rollback":
        return service.rollback(
            args.operation_id,
            reason=args.reason,
            modified_by=args.modified_by,
        )
    raise TemplateMigrationError(f"未知命令: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = _execute(args)
    except (TemplateMigrationError, TemplateBaselineError, OSError, ValueError) as exc:
        _emit(
            {
                "schema_version": _SCHEMA_VERSION,
                "ok": False,
                "command": args.command,
                "error": {
                    "code": type(exc).__name__,
                    "message": str(exc),
                    "retryable": False,
                },
            },
            error=True,
        )
        return 1
    _emit(
        {
            "schema_version": _SCHEMA_VERSION,
            "ok": True,
            "command": args.command,
            "result": result,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
