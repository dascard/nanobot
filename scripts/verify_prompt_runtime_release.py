#!/usr/bin/env python3
"""为目标 Runtime digest 生成不含 Prompt 正文的生产审计回执。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_ALLOWED_READY_STATUSES = frozenset({"in_sync", "local_override"})
_SAFE_FINDING_FIELDS = (
    "template_key",
    "drift_status",
    "default_sha256",
    "runtime_sha256",
    "baseline_sha256",
    "baseline_version",
    "invalid_component",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="验证外置 Prompt Runtime 与目标 Release 的一致性"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument(
        "--accept-local-override",
        action="append",
        default=[],
        dest="accepted_local_overrides",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_findings(raw_findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            field: finding.get(field)
            for field in _SAFE_FINDING_FIELDS
        }
        for finding in sorted(
            raw_findings,
            key=lambda finding: str(finding.get("template_key") or ""),
        )
    ]


def main(argv: list[str] | None = None) -> int:
    from core.prompt_v2.template_migration import TemplateMigrationService
    from core.registry.validation import canonical_json
    from core.release.artifacts import load_release_manifest
    from scripts.build_release_manifest import hash_repository_path

    args = _parser().parse_args(argv)
    release = load_release_manifest(args.manifest)
    artifact = release.runtime_artifact
    if artifact.oci_image_reference != args.image_reference:
        print("目标镜像与 ReleaseManifest 不一致", file=sys.stderr)
        return 2
    prompt_defaults_sha256 = hash_repository_path(
        ROOT,
        "prompts.v2.default",
    )
    if prompt_defaults_sha256 != artifact.input_hashes.get("prompt_defaults"):
        print("镜像内 canonical Prompt Hash 与 ReleaseManifest 不一致", file=sys.stderr)
        return 2

    accepted = sorted(set(args.accepted_local_overrides))
    if len(accepted) != len(args.accepted_local_overrides):
        print("--accept-local-override 不能重复", file=sys.stderr)
        return 2
    service = TemplateMigrationService.from_environment()
    findings = _safe_findings(service.audit())
    local_overrides = {
        str(finding["template_key"])
        for finding in findings
        if finding["drift_status"] == "local_override"
    }
    unknown_acceptances = sorted(set(accepted) - local_overrides)
    blocked = [
        str(finding["template_key"])
        for finding in findings
        if (
            finding["drift_status"] not in _ALLOWED_READY_STATUSES
            or (
                finding["drift_status"] == "local_override"
                and finding["template_key"] not in accepted
            )
        )
    ]
    passed = not blocked and not unknown_acceptances and bool(findings)
    host_root_sha256 = str(
        os.environ.get("NANOBOT_PROMPT_HOST_ROOT_SHA256") or ""
    ).strip()
    if len(host_root_sha256) != 64:
        print("缺少宿主 Prompt Runtime 路径身份", file=sys.stderr)
        return 2

    payload: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "image_reference": artifact.oci_image_reference,
        "git_full_commit": artifact.source.git_full_commit,
        "prompt_defaults_sha256": prompt_defaults_sha256,
        "host_prompt_root_sha256": host_root_sha256,
        "accepted_local_overrides": accepted,
        "findings": findings,
        "passed": passed,
    }
    payload["sha256"] = _sha256_text(canonical_json(payload))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "passed": passed,
                "finding_count": len(findings),
                "blocked_template_keys": blocked,
                "unknown_acceptances": unknown_acceptances,
                "receipt_sha256": payload["sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
