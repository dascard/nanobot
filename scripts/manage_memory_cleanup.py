#!/usr/bin/env python3
"""生产记忆清洗的显式 preview/apply CLI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, NoReturn


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.database import SessionLocal  # noqa: E402
from core.memory_cleanup import (  # noqa: E402
    MemoryCleanupError,
    apply_memory_cleanup,
    load_cleanup_bundle,
    preview_memory_cleanup,
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


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        _emit({
            "schema_version": _SCHEMA_VERSION,
            "ok": False,
            "error": {
                "code": "invalid_arguments",
                "message": str(message),
                "retryable": False,
            },
        }, error=True)
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="生产记忆清洗维护入口")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("preview", "apply"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--bundle-dir", type=Path, required=True)
        subparser.add_argument("--manifest-sha256", required=True)
        if command == "apply":
            subparser.add_argument("--confirm-sha256", required=True)
            subparser.add_argument("--actor", default="cli")
    return parser


def _execute(args: argparse.Namespace) -> dict[str, Any]:
    bundle = load_cleanup_bundle(
        args.bundle_dir,
        expected_manifest_sha256=args.manifest_sha256,
    )
    db = SessionLocal()
    try:
        if args.command == "preview":
            result = preview_memory_cleanup(db, bundle)
            db.rollback()
            return result
        confirmation = str(args.confirm_sha256 or "").strip().lower()
        if confirmation != bundle.manifest_sha256:
            raise MemoryCleanupError("apply_confirmation_mismatch")
        return apply_memory_cleanup(db, bundle, actor=args.actor)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = _execute(args)
    except (MemoryCleanupError, OSError, ValueError) as exc:
        _emit({
            "schema_version": _SCHEMA_VERSION,
            "ok": False,
            "command": args.command,
            "error": {
                "code": type(exc).__name__,
                "message": str(exc),
                "retryable": False,
            },
        }, error=True)
        return 1
    _emit({
        "schema_version": _SCHEMA_VERSION,
        "ok": True,
        "command": args.command,
        "result": result,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
