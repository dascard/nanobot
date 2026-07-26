"""Sandbox 环境准备模板与 Server 侧环境事实校验。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError


ENVIRONMENT_FINGERPRINT_NAME = ".nanobot-environment.json"
ENVIRONMENT_FINGERPRINT_FIELDS = frozenset({
    "profile_id",
    "catalog_generation",
    "policy_sha256",
    "image_digest",
    "setup_definition_sha256",
    "maintenance_definition_sha256",
    "selected_lockfile_hashes",
    "last_setup_at",
    "last_maintenance_at",
})

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IMAGE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_PROFILE_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_CATALOG_GENERATION_RE = re.compile(
    r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}"
)
_ENVIRONMENT_ACTIONS = frozenset({"setup", "maintenance", "unchanged"})

_DEVELOPER_LOCKFILES = (
    "requirements.lock",
    "requirements.txt",
    "pyproject.toml",
    "uv.lock",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)

# 这些命令只验证由 sandboxd 创建的固定 Runtime 目录。它们不读取或执行
# Workspace 中的脚本、package lifecycle hook、Makefile 或其他仓库内容。
_DEVELOPER_SETUP_COMMANDS = (
    "set -eu; "
    "test -d /runtime/home; "
    "test -d /runtime/cache; "
    "test -d /runtime/venvs; "
    "test -d /runtime/pip-cache; "
    "test -d /runtime/npm-cache; "
    "test -d /runtime/pycache",
)
_DEVELOPER_MAINTENANCE_COMMANDS = (
    "set -eu; "
    "test -d /runtime/home; "
    "test -d /runtime/cache; "
    "test -d /runtime/venvs; "
    "test -d /runtime/pip-cache; "
    "test -d /runtime/npm-cache; "
    "test -d /runtime/pycache",
)


def _definition_sha256(
    *,
    template_id: str,
    phase: str,
    commands: tuple[str, ...],
) -> str:
    encoded = json.dumps(
        {
            "schema": "nanobot-sandbox-environment-command.v1",
            "template_id": template_id,
            "phase": phase,
            "commands": list(commands),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class EnvironmentTemplate:
    """由 Server 与 sandboxd 共同审定的固定环境模板。"""

    template_id: str
    profile_id: str
    shell_path: str
    setup_commands: tuple[str, ...]
    maintenance_commands: tuple[str, ...]
    lockfile_paths: tuple[str, ...]
    setup_definition_sha256: str
    maintenance_definition_sha256: str


def _template(
    *,
    template_id: str,
    profile_id: str,
    shell_path: str,
    setup_commands: tuple[str, ...],
    maintenance_commands: tuple[str, ...],
    lockfile_paths: tuple[str, ...],
) -> EnvironmentTemplate:
    return EnvironmentTemplate(
        template_id=template_id,
        profile_id=profile_id,
        shell_path=shell_path,
        setup_commands=setup_commands,
        maintenance_commands=maintenance_commands,
        lockfile_paths=lockfile_paths,
        setup_definition_sha256=_definition_sha256(
            template_id=template_id,
            phase="setup",
            commands=setup_commands,
        ),
        maintenance_definition_sha256=_definition_sha256(
            template_id=template_id,
            phase="maintenance",
            commands=maintenance_commands,
        ),
    )


_ENVIRONMENT_TEMPLATES = MappingProxyType({
    "developer": _template(
        template_id="developer-runtime-v1",
        profile_id="developer",
        shell_path="/bin/bash",
        setup_commands=_DEVELOPER_SETUP_COMMANDS,
        maintenance_commands=_DEVELOPER_MAINTENANCE_COMMANDS,
        lockfile_paths=_DEVELOPER_LOCKFILES,
    ),
})


def _runtime_unavailable(summary: str) -> SandboxServiceError:
    return SandboxServiceError(
        SandboxErrorCode.RUNTIME_UNAVAILABLE,
        summary,
        retryable=True,
        stop=False,
    )


def _require_sha256(value: object, *, field_name: str) -> str:
    normalized = str(value or "").lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise _runtime_unavailable(
            f"Sandbox 环境事实中的 {field_name} 无效"
        )
    return normalized


def _require_timestamp(
    value: object,
    *,
    field_name: str,
    optional: bool,
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > 64:
        raise _runtime_unavailable(
            f"Sandbox 环境事实中的 {field_name} 无效"
        )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise _runtime_unavailable(
            f"Sandbox 环境事实中的 {field_name} 无效"
        ) from exc
    if parsed.tzinfo is None:
        raise _runtime_unavailable(
            f"Sandbox 环境事实中的 {field_name} 无效"
        )
    return value


class SandboxEnvironmentService:
    """校验 sandboxd 返回的环境事实是否匹配 Server 审定模板。"""

    def template(self, profile_id: str) -> EnvironmentTemplate:
        normalized = str(profile_id or "")
        try:
            return _ENVIRONMENT_TEMPLATES[normalized]
        except KeyError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "当前 Sandbox Profile 没有可用的环境准备模板",
            ) from exc

    def require_ready(
        self,
        fact: object,
        *,
        profile_id: str,
        catalog_generation: str,
        policy_sha256: str,
        image_digest: str,
    ) -> dict[str, Any]:
        """拒绝模板、策略、镜像或 lockfile 摘要格式发生漂移的事实。"""

        template = self.template(profile_id)
        if not isinstance(fact, Mapping):
            raise _runtime_unavailable("sandboxd 未返回有效的环境准备事实")

        normalized_profile_id = str(fact.get("profile_id") or "")
        normalized_catalog = str(fact.get("catalog_generation") or "")
        normalized_policy = str(fact.get("policy_sha256") or "").lower()
        normalized_image = str(fact.get("image_digest") or "").lower()
        if (
            normalized_profile_id != template.profile_id
            or normalized_profile_id != str(profile_id or "")
            or normalized_catalog != str(catalog_generation or "")
            or normalized_policy != str(policy_sha256 or "").lower()
            or normalized_image != str(image_digest or "").lower()
        ):
            raise _runtime_unavailable(
                "Sandbox 环境事实与 Server 请求不一致"
            )
        if (
            _PROFILE_ID_RE.fullmatch(normalized_profile_id) is None
            or _CATALOG_GENERATION_RE.fullmatch(normalized_catalog) is None
            or _SHA256_RE.fullmatch(normalized_policy) is None
            or _IMAGE_DIGEST_RE.fullmatch(normalized_image) is None
        ):
            raise _runtime_unavailable("Sandbox 环境事实格式无效")

        setup_sha256 = _require_sha256(
            fact.get("setup_definition_sha256"),
            field_name="setup_definition_sha256",
        )
        maintenance_sha256 = _require_sha256(
            fact.get("maintenance_definition_sha256"),
            field_name="maintenance_definition_sha256",
        )
        if (
            setup_sha256 != template.setup_definition_sha256
            or maintenance_sha256
            != template.maintenance_definition_sha256
        ):
            raise _runtime_unavailable(
                "Sandbox 环境准备模板与 Server 审定版本不一致"
            )

        selected = fact.get("selected_lockfile_hashes")
        if not isinstance(selected, Mapping) or len(selected) > len(
            template.lockfile_paths
        ):
            raise _runtime_unavailable("Sandbox lockfile 摘要事实无效")
        normalized_hashes: dict[str, str] = {}
        for path, digest in selected.items():
            if (
                not isinstance(path, str)
                or path not in template.lockfile_paths
                or path in normalized_hashes
            ):
                raise _runtime_unavailable(
                    "Sandbox lockfile 摘要事实无效"
                )
            normalized_hashes[path] = _require_sha256(
                digest,
                field_name="selected_lockfile_hashes",
            )

        action = str(fact.get("action") or "")
        if action not in _ENVIRONMENT_ACTIONS or fact.get("ready") is not True:
            raise _runtime_unavailable("Sandbox 环境尚未准备完成")

        last_setup_at = _require_timestamp(
            fact.get("last_setup_at"),
            field_name="last_setup_at",
            optional=False,
        )
        last_maintenance_at = _require_timestamp(
            fact.get("last_maintenance_at"),
            field_name="last_maintenance_at",
            optional=True,
        )
        return {
            "ready": True,
            "action": action,
            "profile_id": normalized_profile_id,
            "catalog_generation": normalized_catalog,
            "policy_sha256": normalized_policy,
            "image_digest": normalized_image,
            "setup_definition_sha256": setup_sha256,
            "maintenance_definition_sha256": maintenance_sha256,
            "selected_lockfile_hashes": normalized_hashes,
            "last_setup_at": last_setup_at,
            "last_maintenance_at": last_maintenance_at,
        }


__all__ = [
    "ENVIRONMENT_FINGERPRINT_FIELDS",
    "ENVIRONMENT_FINGERPRINT_NAME",
    "EnvironmentTemplate",
    "SandboxEnvironmentService",
]
