#!/usr/bin/env python3
"""写入本地 Runtime 构建／部署身份，不采集或输出凭据。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile


_SHA256_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_CONTEXT_SHA = re.compile(r"^[0-9a-f]{64}$")


class RuntimeBuildEvidenceError(ValueError):
    """本地构建证据字段不满足不可变身份合同。"""


def _parse_service_images(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        service, separator, image_id = value.partition("=")
        if (
            not separator
            or not service
            or _SHA256_ID.fullmatch(image_id) is None
        ):
            raise RuntimeBuildEvidenceError(
                "--service-image 必须是 service=sha256:<64位摘要>"
            )
        if service in result:
            raise RuntimeBuildEvidenceError(f"服务重复：{service}")
        result[service] = image_id
    return result


def build_runtime_evidence(args: argparse.Namespace) -> dict[str, object]:
    if _GIT_SHA.fullmatch(args.git_full_commit or "") is None:
        raise RuntimeBuildEvidenceError("git_full_commit 必须是 40 位 SHA")
    if _CONTEXT_SHA.fullmatch(args.build_context_sha256 or "") is None:
        raise RuntimeBuildEvidenceError(
            "build_context_sha256 必须是 64 位 SHA-256"
        )
    if _SHA256_ID.fullmatch(args.image_id or "") is None:
        raise RuntimeBuildEvidenceError("image_id 必须是不可变 IMAGE ID")
    if args.rollback_image_id and _SHA256_ID.fullmatch(
        args.rollback_image_id
    ) is None:
        raise RuntimeBuildEvidenceError(
            "rollback_image_id 必须是不可变 IMAGE ID"
        )
    services = _parse_service_images(list(args.service_image))
    if services and set(services.values()) != {args.image_id}:
        raise RuntimeBuildEvidenceError(
            "固定 Runtime 服务没有使用同一 IMAGE ID"
        )
    deployed = args.deployment_status == "deployed"
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 2,
        "artifact": "nanobot-runtime",
        "source": {
            "git_full_commit": args.git_full_commit,
            "git_dirty": args.git_dirty,
            "build_context_sha256": args.build_context_sha256,
            "build_context_manifest": args.build_context_manifest,
        },
        "image": {
            "reference": args.image_reference,
            "image_id": args.image_id,
            "registry_digest": args.registry_digest,
            "rollback_image_id": args.rollback_image_id,
        },
        "build": {
            "built_at": args.built_at or timestamp,
            "deployment_status": args.deployment_status,
            "deployed_at": timestamp if deployed else "",
        },
        "runtime_services": services,
        "smoke": {
            "compose_wait": "PASSED" if deployed else "NOT_RUN",
            "health_endpoint": "PASSED" if deployed else "NOT_RUN",
            "chat_interface": "BLOCKED_NOT_RUN",
            "agent_link_roundtrip": "BLOCKED_NOT_RUN",
            "task_worker": "BLOCKED_NOT_RUN",
            "prompt_runtime_hash": "BLOCKED_NOT_RUN",
            "sandbox_matrix": "NOT_REQUIRED_BY_RUNTIME_BUILD",
        },
    }


def write_runtime_evidence(path: Path, payload: dict[str, object]) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(
                payload,
                output,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-full-commit", required=True)
    parser.add_argument(
        "--git-dirty",
        required=True,
        choices=("true", "false", "unknown"),
    )
    parser.add_argument("--build-context-sha256", required=True)
    parser.add_argument("--build-context-manifest", required=True)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--registry-digest", default="")
    parser.add_argument("--rollback-image-id", default="")
    parser.add_argument("--built-at", default="")
    parser.add_argument(
        "--deployment-status",
        choices=("built_only", "deployed"),
        required=True,
    )
    parser.add_argument("--service-image", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    payload = build_runtime_evidence(args)
    write_runtime_evidence(args.output, payload)
    print(f"Runtime 构建证据已写入：{args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeBuildEvidenceError as exc:
        raise SystemExit(str(exc)) from exc
