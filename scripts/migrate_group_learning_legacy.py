#!/usr/bin/env python3
"""旧表达、黑话和群体记忆的显式 dry-run/apply 迁移入口。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.group_learning.legacy_migration_service import (  # noqa: E402
    GroupLearningLegacyMigrationError,
    build_group_learning_legacy_migration_service,
)
from core.database import SessionLocal  # noqa: E402


_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "审计或迁移旧 Expression/Jargon/GroupMemory；"
            "省略 --apply 时只执行 dry-run"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="按已确认的两份审计 SHA 执行迁移",
    )
    parser.add_argument("--expected-source-sha256", default="")
    parser.add_argument("--expected-planned-sha256", default="")
    parser.add_argument("--actor", default="")
    parser.add_argument("--chat-stream-id", default=None)
    return parser


def _required_sha256(value: object, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} 必须是 64 位小写 SHA-256")
    return normalized


def _audit_payload(audit: Any) -> dict[str, Any]:
    return {
        "source_count": audit.source_count,
        "valid_count": audit.valid_count,
        "planned_count": audit.planned_count,
        "duplicate_count": audit.duplicate_count,
        "conflict_count": audit.conflict_count,
        "invalid_identity_count": audit.invalid_identity_count,
        "unsupported_type_count": audit.unsupported_type_count,
        "checked_without_human_proof_count": (
            audit.checked_without_human_proof_count
        ),
        "source_counts": [
            {"source": source, "count": count}
            for source, count in audit.source_counts
        ],
        "source_sha256": audit.source_sha256,
        "planned_sha256": audit.planned_sha256,
        "planned": [
            asdict(item)
            for item in audit.planned
        ],
    }


def _execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.apply:
        source_sha256 = _required_sha256(
            args.expected_source_sha256,
            "expected_source_sha256",
        )
        planned_sha256 = _required_sha256(
            args.expected_planned_sha256,
            "expected_planned_sha256",
        )
        actor = str(args.actor or "").strip()
        if not actor:
            raise ValueError("actor 不能为空")
    else:
        source_sha256 = ""
        planned_sha256 = ""
        actor = ""

    db = SessionLocal()
    try:
        service = build_group_learning_legacy_migration_service(db)
        if not args.apply:
            audit = service.audit(
                chat_stream_id=args.chat_stream_id,
            )
            db.rollback()
            return {
                "mode": "dry_run",
                "audit": _audit_payload(audit),
            }
        result = service.apply(
            expected_source_sha256=source_sha256,
            expected_planned_sha256=planned_sha256,
            actor=actor,
            chat_stream_id=args.chat_stream_id,
        )
        return {
            "mode": "apply",
            "result": asdict(result),
        }
    finally:
        db.close()


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _execute(args)
    except (
        GroupLearningLegacyMigrationError,
        OSError,
        ValueError,
    ) as exc:
        _emit(
            {
                "schema_version": _SCHEMA_VERSION,
                "ok": False,
                "mode": "apply" if args.apply else "dry_run",
                "error": {
                    "code": type(exc).__name__,
                    "message": str(exc),
                    "retryable": False,
                },
            },
            error=True,
        )
        return 1
    _emit({
        "schema_version": _SCHEMA_VERSION,
        "ok": True,
        **payload,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
