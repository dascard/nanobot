#!/usr/bin/env python3
"""从仓库内 canonical catalog 生成绑定真实镜像摘要的部署 manifest。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(REPO_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(REPO_ROOT))

from core.sandbox.profile_catalog import (  # noqa: E402
    load_profile_catalog,
    parse_profile_catalog,
)


_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
_GENERATION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")


def _image_id(value: str) -> str:
    normalized = str(value or "").lower()
    if _IMAGE_ID_RE.fullmatch(normalized) is None:
        raise argparse.ArgumentTypeError("镜像 ID 必须是完整 sha256 IMAGE ID")
    return normalized


def _image_reference(value: str) -> str:
    normalized = str(value or "")
    if (
        not normalized
        or len(normalized) > 255
        or normalized.endswith(":latest")
        or any(character.isspace() for character in normalized)
    ):
        raise argparse.ArgumentTypeError("镜像引用无效或使用了 latest")
    return normalized


def _generation(value: str) -> str:
    normalized = str(value or "")
    if _GENERATION_RE.fullmatch(normalized) is None:
        raise argparse.ArgumentTypeError("catalog generation 无效")
    return normalized


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成固定 Restricted、Developer 与代理镜像的部署 manifest。",
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generation", type=_generation, required=True)
    parser.add_argument(
        "--restricted-reference",
        type=_image_reference,
        required=True,
    )
    parser.add_argument("--restricted-image-id", type=_image_id, required=True)
    parser.add_argument(
        "--developer-reference",
        type=_image_reference,
        required=True,
    )
    parser.add_argument("--developer-image-id", type=_image_id, required=True)
    parser.add_argument(
        "--proxy-reference",
        type=_image_reference,
        required=True,
    )
    parser.add_argument("--proxy-image-id", type=_image_id, required=True)
    return parser.parse_args()


def _load_raw(path: Path) -> dict[str, object]:
    # 先走严格 loader，拒绝符号链接、重复 JSON 键和未知策略字段。
    load_profile_catalog(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("canonical Profile manifest 根节点不是对象")
    return value


def render_manifest(arguments: argparse.Namespace) -> tuple[str, str]:
    raw = _load_raw(arguments.source)
    profiles = raw.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("canonical Profile manifest 缺少 profiles")

    by_id = {
        str(profile.get("profile_id") or ""): profile
        for profile in profiles
        if isinstance(profile, dict)
    }
    if set(by_id) != {"restricted", "developer", "trusted_developer"}:
        raise ValueError("canonical Profile 集合不完整")

    developer = by_id["developer"]
    source_proxy_reference = str(
        developer.get("network_proxy_image_reference") or ""
    )
    source_proxy_allowlist = developer.get("network_proxy_image_allowlist")
    if (
        arguments.proxy_reference != source_proxy_reference
        or source_proxy_allowlist != []
    ):
        raise ValueError(
            "代理镜像引用必须匹配，且 canonical 代理 IMAGE ID 必须留空"
        )

    raw["catalog_generation"] = arguments.generation
    restricted = by_id["restricted"]
    restricted["image_reference"] = arguments.restricted_reference
    restricted["image_allowlist"] = [arguments.restricted_image_id]

    developer["image_reference"] = arguments.developer_reference
    developer["image_allowlist"] = [arguments.developer_image_id]
    developer["network_proxy_image_reference"] = arguments.proxy_reference
    developer["network_proxy_image_allowlist"] = [arguments.proxy_image_id]

    trusted = by_id["trusted_developer"]
    trusted["image_reference"] = arguments.developer_reference
    trusted["image_allowlist"] = []
    trusted["grantable"] = False

    catalog = parse_profile_catalog(raw)
    encoded = (
        json.dumps(
            raw,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    output = arguments.output
    output.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)

    file_sha256 = hashlib.sha256(encoded).hexdigest()
    return catalog.policy_sha256, file_sha256


def main() -> int:
    arguments = _arguments()
    policy_sha256, file_sha256 = render_manifest(arguments)
    print(f"policy_sha256={policy_sha256}")
    print(f"manifest_sha256={file_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
