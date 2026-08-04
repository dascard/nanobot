"""代码所有的 Release Impact Registry 与确定性影响报告。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
import hashlib
from pathlib import PurePosixPath
from typing import Literal

from core.registry import (
    RegistryBuilder,
    RegistryGeneration,
    RegistrySnapshot,
)
from core.registry.validation import canonical_json


ReleaseLifecycle = Literal["active", "deprecated"]

FIXED_RUNTIME_SERVICES = (
    "nanobot-server",
    "session-summary-worker",
    "outbound-delivery-worker",
    "semantic-index-worker",
)
SENTINEL_RUNTIME_SERVICES = (
    "nanobot-server",
    "session-summary-worker",
    "semantic-index-worker",
)

_IGNORED_PATH_PREFIXES = (
    ".tmp/",
    "data/",
    "docs/",
    "evals/",
    "tests/",
    "vendor/",
    "webui/dist/",
    "webui/node_modules/",
)
_LOCAL_METADATA_ROOTS = frozenset({
    ".agents",
    ".claude",
    ".codex",
    ".qoder",
})
_PRODUCTION_SUFFIXES = frozenset({
    ".c",
    ".cc",
    ".css",
    ".go",
    ".h",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".lock",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
})
_ROOT_PRODUCTION_FILES = frozenset({
    ".dockerignore",
    ".env.example",
    "Dockerfile",
    "config.py",
    "docker-compose.prod.yml",
    "docker-compose.yml",
    "pytest.ini",
    "server.py",
})
_ROOT_NON_PRODUCTION_FILES = frozenset({
    "test_bridge_native.py",
    "test_prompt.py",
})


class ReleaseImpactError(RuntimeError):
    """Release Impact 稳定错误基类。"""


class ReleaseImpactPathError(ReleaseImpactError, ValueError):
    """Git diff 路径不满足仓库相对路径合同。"""


class ReleaseImpactOwnershipError(ReleaseImpactError):
    """生产路径未归属任何代码所有 Descriptor。"""


@dataclass(frozen=True, slots=True)
class ReleaseImpactDescriptor:
    """一个代码模块对 Artifact 和验证面的静态影响声明。"""

    registry_id: str
    owner: str
    source_globs: tuple[str, ...]
    affected_services: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    database_migration_checks: tuple[str, ...] = ()
    prompt_runtime_audit_required: bool = False
    web_build_required: bool = False
    real_docker_sandbox_required: bool = False
    verification_suites: tuple[str, ...] = ()
    owns_production_paths: bool = True
    lifecycle: ReleaseLifecycle = "active"
    registry_namespace: str = field(
        default="release_impact",
        init=False,
    )
    registry_dependencies: tuple[str, ...] = field(
        default=(),
        init=False,
    )

    def __post_init__(self) -> None:
        if not self.source_globs:
            raise ValueError("ReleaseImpactDescriptor 必须声明 source_globs")
        for field_name, values in (
            ("source_globs", self.source_globs),
            ("affected_services", self.affected_services),
            ("artifacts", self.artifacts),
            (
                "database_migration_checks",
                self.database_migration_checks,
            ),
            ("verification_suites", self.verification_suites),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} 不能包含重复项")

    def matches(self, path: str) -> bool:
        return any(
            fnmatchcase(path, pattern)
            for pattern in self.source_globs
        )

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "owner": self.owner,
            "source_globs": self.source_globs,
            "affected_services": self.affected_services,
            "artifacts": self.artifacts,
            "database_migration_checks": (
                self.database_migration_checks
            ),
            "prompt_runtime_audit_required": (
                self.prompt_runtime_audit_required
            ),
            "web_build_required": self.web_build_required,
            "real_docker_sandbox_required": (
                self.real_docker_sandbox_required
            ),
            "verification_suites": self.verification_suites,
            "owns_production_paths": self.owns_production_paths,
            "lifecycle": self.lifecycle,
        }


@dataclass(frozen=True, slots=True)
class ReleasePathImpact:
    path: str
    descriptor_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "descriptor_ids": list(self.descriptor_ids),
        }


@dataclass(frozen=True, slots=True)
class ReleaseImpactReport:
    changed_paths: tuple[str, ...]
    path_impacts: tuple[ReleasePathImpact, ...]
    affected_services: tuple[str, ...]
    artifacts: tuple[str, ...]
    database_migration_checks: tuple[str, ...]
    prompt_runtime_audit_required: bool
    web_build_required: bool
    real_docker_sandbox_required: bool
    verification_suites: tuple[str, ...]
    unowned_production_paths: tuple[str, ...]
    registry_generation: int
    registry_sha256: str
    canonical_json: str
    sha256: str

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "changed_paths": list(self.changed_paths),
            "path_impacts": [
                item.to_dict() for item in self.path_impacts
            ],
            "affected_services": list(self.affected_services),
            "artifacts": list(self.artifacts),
            "database_migration_checks": list(
                self.database_migration_checks
            ),
            "prompt_runtime_audit_required": (
                self.prompt_runtime_audit_required
            ),
            "web_build_required": self.web_build_required,
            "real_docker_sandbox_required": (
                self.real_docker_sandbox_required
            ),
            "verification_suites": list(self.verification_suites),
            "unowned_production_paths": list(
                self.unowned_production_paths
            ),
            "registry": {
                "generation": self.registry_generation,
                "sha256": self.registry_sha256,
            },
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self._content_dict(),
            "sha256": self.sha256,
        }

    def require_owned(self) -> "ReleaseImpactReport":
        if self.unowned_production_paths:
            raise ReleaseImpactOwnershipError(
                "存在未归属生产路径: "
                + ", ".join(self.unowned_production_paths)
            )
        return self


def _descriptor(
    registry_id: str,
    *,
    owner: str = "core.release",
    source_globs: tuple[str, ...],
    affected_services: tuple[str, ...] = (),
    artifacts: tuple[str, ...] = (),
    database_migration_checks: tuple[str, ...] = (),
    prompt_runtime_audit_required: bool = False,
    web_build_required: bool = False,
    real_docker_sandbox_required: bool = False,
    verification_suites: tuple[str, ...] = (),
    owns_production_paths: bool = True,
) -> ReleaseImpactDescriptor:
    return ReleaseImpactDescriptor(
        registry_id=registry_id,
        owner=owner,
        source_globs=source_globs,
        affected_services=affected_services,
        artifacts=artifacts,
        database_migration_checks=database_migration_checks,
        prompt_runtime_audit_required=prompt_runtime_audit_required,
        web_build_required=web_build_required,
        real_docker_sandbox_required=real_docker_sandbox_required,
        verification_suites=verification_suites,
        owns_production_paths=owns_production_paths,
    )


_DESCRIPTORS = (
    _descriptor(
        "runtime",
        source_globs=(
            "api/**",
            "app/**",
            "bootstrap/**",
            "clients/**",
            "config.py",
            "core/**",
            "creatures/**",
            "foundation/**",
            "nanobot_kt/**",
            "sandbox.py",
            "server.py",
            "workers/**",
        ),
        affected_services=FIXED_RUNTIME_SERVICES,
        artifacts=("nanobot-runtime",),
        verification_suites=("architecture", "backend-full"),
    ),
    _descriptor(
        "prompt_runtime",
        owner="core.prompt_v2",
        source_globs=(
            "core/prompt_v2/**",
            "creatures/nanobot/config.yaml",
            "creatures/nanobot/prompts/**",
            "data/prompts_v2/**",
            "nanobot_kt/bridge.py",
            "prompt_manifest.json",
            "prompts.v2.default/**",
        ),
        affected_services=FIXED_RUNTIME_SERVICES,
        artifacts=("nanobot-runtime",),
        prompt_runtime_audit_required=True,
        verification_suites=(
            "backend-full",
            "prompt-runtime-audit",
        ),
    ),
    _descriptor(
        "database_schema",
        owner="core.db",
        source_globs=(
            "core/database.py",
            "core/db/**",
            "core/repositories/**",
            "core/schema_migrations.py",
        ),
        affected_services=FIXED_RUNTIME_SERVICES,
        artifacts=("nanobot-runtime",),
        database_migration_checks=(
            "schema-migration-idempotency",
        ),
        verification_suites=(
            "backend-full",
            "database-migration-check",
        ),
    ),
    _descriptor(
        "sandbox",
        owner="core.sandbox",
        source_globs=(
            "api/admin/sandbox_routes.py",
            "api/asset_routes.py",
            "core/asset_tokens.py",
            "core/asset_transport.py",
            "core/sandbox/**",
            "deploy/apparmor/**",
            "deploy/systemd/nanobot-sandboxd*",
            "docker/sandbox/**",
            "requirements-sandboxd.lock",
            "sandboxd/**",
            "scripts/build-sandbox-image.sh",
            "scripts/manage-sandbox-production.sh",
            "scripts/sandbox*",
        ),
        artifacts=(
            "nanobot-sandbox-python",
            "nanobot-sandboxd",
        ),
        real_docker_sandbox_required=True,
        verification_suites=(
            "backend-full",
            "sandbox-real-docker",
        ),
    ),
    _descriptor(
        "runtime_resources",
        owner="core.guardrail",
        source_globs=("sentinel/**",),
        affected_services=SENTINEL_RUNTIME_SERVICES,
        artifacts=("prompt-injection-sentinel",),
        verification_suites=("sentinel-runtime-check",),
    ),
    _descriptor(
        "news_resources",
        owner="core.news",
        source_globs=("resources/news/**",),
        affected_services=FIXED_RUNTIME_SERVICES,
        artifacts=("nanobot-runtime",),
        verification_suites=(
            "backend-full",
            "news-governance",
        ),
    ),
    _descriptor(
        "webui",
        owner="webui",
        source_globs=(
            "webui/*.html",
            "webui/*.js",
            "webui/*.json",
            "webui/*.ts",
            "webui/scripts/**",
            "webui/src/**",
        ),
        affected_services=FIXED_RUNTIME_SERVICES,
        artifacts=("nanobot-runtime", "webui"),
        web_build_required=True,
        verification_suites=("frontend-lint-build",),
    ),
    _descriptor(
        "deployment",
        owner="operations",
        source_globs=(
            ".dockerignore",
            ".env.example",
            "Dockerfile",
            "deploy/**",
            "docker/**",
            "docker-compose*.yml",
            "requirements*.lock",
            "requirements*.txt",
            "scripts/build_release_manifest.py",
            "scripts/deploy-production.sh",
            "scripts/deploy_release.py",
            "scripts/docker-build.sh",
        ),
        affected_services=FIXED_RUNTIME_SERVICES,
        artifacts=("nanobot-runtime",),
        verification_suites=(
            "compose-config",
            "deployment-contract",
        ),
    ),
    _descriptor(
        "operations",
        owner="operations",
        source_globs=(
            ".github/**",
            "commitlint.config.js",
            "config/**",
            "package-lock.json",
            "package.json",
            "pytest.ini",
            "scripts/**",
        ),
        verification_suites=("architecture",),
    ),
    _descriptor(
        "kt_compatibility",
        owner="nanobot_kt",
        source_globs=(
            "nanobot_kt/**",
            "requirements-kt.in",
            "requirements-kt.lock",
        ),
        verification_suites=("backend-full", "kt-compatibility"),
        owns_production_paths=False,
    ),
    _descriptor(
        "evaluation",
        owner="quality",
        source_globs=(
            "evals/**",
            "test_bridge_native.py",
            "test_prompt.py",
        ),
        verification_suites=("eval-gate",),
        owns_production_paths=False,
    ),
    _descriptor(
        "tests",
        owner="quality",
        source_globs=("tests/**",),
        verification_suites=("backend-full",),
        owns_production_paths=False,
    ),
    _descriptor(
        "documentation",
        owner="documentation",
        source_globs=(
            "AGENTS.md",
            "README.md",
            "docs/**",
            "goal.md",
        ),
        owns_production_paths=False,
    ),
    _descriptor(
        "legacy_tooling",
        owner="tooling",
        source_globs=("tampermonkey.js",),
    ),
)


def _build_registry() -> RegistrySnapshot[ReleaseImpactDescriptor]:
    generation = RegistryGeneration[ReleaseImpactDescriptor](
        "release_impact"
    )

    def configure(
        builder: RegistryBuilder[ReleaseImpactDescriptor],
    ) -> None:
        for descriptor in _DESCRIPTORS:
            builder.register(descriptor)

    return generation.rebuild(configure)


RELEASE_IMPACT_REGISTRY = _build_registry()


def normalize_repository_path(path: object) -> str:
    if not isinstance(path, str) or not path:
        raise ReleaseImpactPathError("Release impact path 必须是非空字符串")
    if "\\" in path:
        raise ReleaseImpactPathError(
            f"Release impact path 必须使用 POSIX 分隔符: {path!r}"
        )
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ReleaseImpactPathError(
            f"Release impact path 必须是仓库相对路径: {path!r}"
        )
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise ReleaseImpactPathError("Release impact path 不能为空")
    return normalized


def is_potential_production_path(path: str) -> bool:
    if path.startswith("data/prompts_v2/"):
        return True
    root = path.split("/", 1)[0]
    if root.casefold() in _LOCAL_METADATA_ROOTS:
        return False
    if path.startswith(_IGNORED_PATH_PREFIXES):
        return False
    if path in _ROOT_NON_PRODUCTION_FILES:
        return False
    if path in _ROOT_PRODUCTION_FILES:
        return True
    return PurePosixPath(path).suffix.lower() in _PRODUCTION_SUFFIXES


def matching_release_impacts(
    path: str,
) -> tuple[ReleaseImpactDescriptor, ...]:
    normalized = normalize_repository_path(path)
    return tuple(
        descriptor
        for descriptor in RELEASE_IMPACT_REGISTRY
        if descriptor.lifecycle == "active"
        and descriptor.matches(normalized)
    )


def _ordered_services(values: set[str]) -> tuple[str, ...]:
    fixed = tuple(
        service for service in FIXED_RUNTIME_SERVICES if service in values
    )
    remaining = tuple(sorted(values - set(FIXED_RUNTIME_SERVICES)))
    return fixed + remaining


def build_release_impact_report(
    changed_paths: Iterable[str],
) -> ReleaseImpactReport:
    normalized_paths = tuple(sorted({
        normalize_repository_path(path) for path in changed_paths
    }))
    path_impacts: list[ReleasePathImpact] = []
    services: set[str] = set()
    artifacts: set[str] = set()
    migration_checks: set[str] = set()
    verification_suites: set[str] = set()
    prompt_audit = False
    web_build = False
    real_docker = False
    unowned: list[str] = []

    for path in normalized_paths:
        descriptors = matching_release_impacts(path)
        path_impacts.append(ReleasePathImpact(
            path=path,
            descriptor_ids=tuple(
                descriptor.registry_id for descriptor in descriptors
            ),
        ))
        owned = False
        for descriptor in descriptors:
            services.update(descriptor.affected_services)
            artifacts.update(descriptor.artifacts)
            migration_checks.update(
                descriptor.database_migration_checks
            )
            verification_suites.update(
                descriptor.verification_suites
            )
            prompt_audit = (
                prompt_audit
                or descriptor.prompt_runtime_audit_required
            )
            web_build = web_build or descriptor.web_build_required
            real_docker = (
                real_docker
                or descriptor.real_docker_sandbox_required
            )
            owned = owned or descriptor.owns_production_paths
        if is_potential_production_path(path) and not owned:
            unowned.append(path)

    content = {
        "schema_version": 1,
        "changed_paths": list(normalized_paths),
        "path_impacts": [
            item.to_dict() for item in path_impacts
        ],
        "affected_services": list(_ordered_services(services)),
        "artifacts": sorted(artifacts),
        "database_migration_checks": sorted(migration_checks),
        "prompt_runtime_audit_required": prompt_audit,
        "web_build_required": web_build,
        "real_docker_sandbox_required": real_docker,
        "verification_suites": sorted(verification_suites),
        "unowned_production_paths": sorted(unowned),
        "registry": {
            "generation": RELEASE_IMPACT_REGISTRY.generation,
            "sha256": RELEASE_IMPACT_REGISTRY.sha256,
        },
    }
    encoded = canonical_json(content)
    return ReleaseImpactReport(
        changed_paths=normalized_paths,
        path_impacts=tuple(path_impacts),
        affected_services=_ordered_services(services),
        artifacts=tuple(sorted(artifacts)),
        database_migration_checks=tuple(sorted(migration_checks)),
        prompt_runtime_audit_required=prompt_audit,
        web_build_required=web_build,
        real_docker_sandbox_required=real_docker,
        verification_suites=tuple(sorted(verification_suites)),
        unowned_production_paths=tuple(sorted(unowned)),
        registry_generation=RELEASE_IMPACT_REGISTRY.generation,
        registry_sha256=RELEASE_IMPACT_REGISTRY.sha256,
        canonical_json=encoded,
        sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )
