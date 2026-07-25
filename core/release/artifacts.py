"""可验证的构建 Artifact 与原子 Release 清单合同。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Literal

from core.registry import (
    RegistryBuilder,
    RegistryGeneration,
    RegistrySnapshot,
)
from core.registry.validation import canonical_json
from core.release.impact import FIXED_RUNTIME_SERVICES


ArtifactKind = Literal["oci_image", "host_service", "static_bundle"]
ArtifactProvenance = Literal["built", "observed"]

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SHA256_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_REFERENCE_PATTERN = re.compile(
    r"^[^@\s]+@(sha256:[0-9a-f]{64})$"
)


class ArtifactManifestError(ValueError):
    """Artifact 清单不满足不可变发布合同。"""


class ReleaseManifestError(ValueError):
    """Release 清单无效、被篡改或无法读取。"""


@dataclass(frozen=True, slots=True)
class BuildProfile:
    """一个代码所有的可构建 Artifact 类型。"""

    registry_id: str
    artifact_kind: ArtifactKind
    dependency_inputs: tuple[str, ...]
    required_input_keys: tuple[str, ...]
    fixed_services: tuple[str, ...] = ()
    registry_namespace: str = field(
        default="build_profile",
        init=False,
    )
    registry_dependencies: tuple[str, ...] = field(
        default=(),
        init=False,
    )

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "artifact_kind": self.artifact_kind,
            "dependency_inputs": self.dependency_inputs,
            "required_input_keys": self.required_input_keys,
            "fixed_services": self.fixed_services,
        }


_BUILD_PROFILES = (
    BuildProfile(
        registry_id="nanobot-runtime",
        artifact_kind="oci_image",
        dependency_inputs=(
            "requirements-prod.lock",
            "webui/package-lock.json",
            "prompts.v2.default",
            "vendor/KohakuTerrarium",
        ),
        required_input_keys=(
            "prompt_defaults",
            "python_lock",
            "web_lock",
        ),
        fixed_services=FIXED_RUNTIME_SERVICES,
    ),
    BuildProfile(
        registry_id="nanobot-sandbox-python",
        artifact_kind="oci_image",
        dependency_inputs=(
            "docker/sandbox/python/requirements.lock",
        ),
        required_input_keys=("python_lock",),
    ),
    BuildProfile(
        registry_id="sandboxd",
        artifact_kind="host_service",
        dependency_inputs=("requirements-sandboxd.lock",),
        required_input_keys=("python_lock",),
    ),
    BuildProfile(
        registry_id="webui",
        artifact_kind="static_bundle",
        dependency_inputs=("webui/package-lock.json",),
        required_input_keys=("web_lock",),
    ),
)


def _build_profile_registry() -> RegistrySnapshot[BuildProfile]:
    generation = RegistryGeneration[BuildProfile]("build_profile")

    def configure(builder: RegistryBuilder[BuildProfile]) -> None:
        for profile in _BUILD_PROFILES:
            builder.register(profile)

    return generation.rebuild(configure)


BUILD_PROFILE_REGISTRY = _build_profile_registry()


def _validate_commit(value: str, *, field_name: str, required: bool) -> str:
    if not value and not required:
        return ""
    if _GIT_COMMIT_PATTERN.fullmatch(value) is None:
        raise ArtifactManifestError(
            f"{field_name} 必须是 40 位小写 Git commit"
        )
    return value


def _validate_sha256(value: str, *, field_name: str) -> str:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ArtifactManifestError(
            f"{field_name} 必须是 64 位小写 SHA-256"
        )
    return value


def _validate_image_id(value: str) -> str:
    if _SHA256_ID_PATTERN.fullmatch(value) is None:
        raise ArtifactManifestError(
            "oci_image_id 必须是完整 sha256 Image ID"
        )
    return value


def _image_digest(image_reference: str) -> str:
    match = _IMAGE_REFERENCE_PATTERN.fullmatch(image_reference)
    if match is None:
        raise ArtifactManifestError(
            "oci_image_reference 必须是仓库@sha256:<64位摘要>"
        )
    return match.group(1)


def _validate_aware_datetime(value: str, *, field_name: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ArtifactManifestError(
            f"{field_name} 必须是带时区的 ISO 8601 时间"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ArtifactManifestError(
            f"{field_name} 必须包含时区偏移"
        )
    return value


def _validate_nonempty(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactManifestError(f"{field_name} 不能为空")
    if any(character in value for character in ("\0", "\r", "\n")):
        raise ArtifactManifestError(f"{field_name} 包含非法控制字符")
    return value


@dataclass(frozen=True, slots=True)
class ArtifactSource:
    """Artifact 的源码身份；不记录源码正文或凭据。"""

    git_full_commit: str
    git_dirty: bool | None
    kt_commit: str

    def __post_init__(self) -> None:
        _validate_commit(
            self.git_full_commit,
            field_name="source.git_full_commit",
            required=True,
        )
        _validate_commit(
            self.kt_commit,
            field_name="source.kt_commit",
            required=False,
        )
        if (
            self.git_dirty is not None
            and type(self.git_dirty) is not bool
        ):
            raise ArtifactManifestError(
                "source.git_dirty 必须是 bool 或 null"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "git_full_commit": self.git_full_commit,
            "git_dirty": self.git_dirty,
            "kt_commit": self.kt_commit,
        }


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """一个构建产物的不可变证据清单。"""

    profile_id: str
    artifact_id: str
    artifact_kind: ArtifactKind
    provenance: ArtifactProvenance
    source: ArtifactSource
    input_hashes: Mapping[str, str]
    schema_migration_head: str
    oci_image_reference: str
    oci_image_digest: str
    oci_image_id: str
    sbom_path: str
    dependency_manifest_path: str
    verification_suites: tuple[str, ...]
    verification_results_sha256: str
    built_at: str
    builder_version: str
    canonical_json: str
    sha256: str

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "profile_id": self.profile_id,
            "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind,
            "provenance": self.provenance,
            "source": self.source.to_dict(),
            "input_hashes": dict(self.input_hashes),
            "schema_migration_head": self.schema_migration_head,
            "oci_image_reference": self.oci_image_reference,
            "oci_image_digest": self.oci_image_digest,
            "oci_image_id": self.oci_image_id,
            "sbom_path": self.sbom_path,
            "dependency_manifest_path": self.dependency_manifest_path,
            "verification_suites": list(self.verification_suites),
            "verification_results_sha256": (
                self.verification_results_sha256
            ),
            "built_at": self.built_at,
            "builder_version": self.builder_version,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "sha256": self.sha256}


def build_artifact_manifest(
    *,
    profile_id: str,
    provenance: ArtifactProvenance,
    source: ArtifactSource,
    input_hashes: Mapping[str, str],
    schema_migration_head: str,
    oci_image_reference: str,
    oci_image_id: str,
    sbom_path: str,
    dependency_manifest_path: str,
    verification_suites: Iterable[str],
    verification_results_sha256: str,
    built_at: str,
    builder_version: str,
) -> ArtifactManifest:
    """校验输入并构建内容寻址的 Artifact 清单。"""

    try:
        profile = BUILD_PROFILE_REGISTRY.require(profile_id)
    except KeyError as exc:
        raise ArtifactManifestError(
            f"未知 BuildProfile: {profile_id}"
        ) from exc
    if provenance not in {"built", "observed"}:
        raise ArtifactManifestError("provenance 必须是 built 或 observed")
    if not isinstance(source, ArtifactSource):
        raise ArtifactManifestError("source 必须是 ArtifactSource")

    normalized_hashes: dict[str, str] = {}
    for key, value in sorted(input_hashes.items()):
        _validate_nonempty(str(key), field_name="input_hashes key")
        normalized_hashes[str(key)] = _validate_sha256(
            str(value),
            field_name=f"input_hashes.{key}",
        )
    requested_suites = tuple(verification_suites)
    suites = tuple(sorted(set(requested_suites)))
    if len(suites) != len(requested_suites):
        raise ArtifactManifestError(
            "verification_suites 不能包含重复项"
        )
    for suite in suites:
        _validate_nonempty(suite, field_name="verification_suite")

    image_reference = ""
    image_digest = ""
    image_id = ""
    if profile.artifact_kind == "oci_image":
        image_reference = oci_image_reference
        image_id = _validate_image_id(oci_image_id)
        if (
            provenance == "observed"
            and image_reference == image_id
        ):
            image_digest = image_id
        else:
            image_digest = _image_digest(image_reference)
    elif any((oci_image_reference, oci_image_id)):
        raise ArtifactManifestError(
            "非 OCI Artifact 不能声明镜像引用或 Image ID"
        )

    _validate_aware_datetime(built_at, field_name="built_at")
    _validate_nonempty(builder_version, field_name="builder_version")

    if provenance == "built":
        if source.git_dirty is not False:
            raise ArtifactManifestError(
                "正式 Artifact 必须来自 clean Git 工作树"
            )
        if profile_id == "nanobot-runtime" and not source.kt_commit:
            raise ArtifactManifestError(
                "nanobot-runtime 必须记录 KT 固定提交"
            )
        if not normalized_hashes:
            raise ArtifactManifestError(
                "正式 Artifact 必须记录输入 Hash"
            )
        missing_inputs = sorted(
            set(profile.required_input_keys) - set(normalized_hashes)
        )
        if missing_inputs:
            raise ArtifactManifestError(
                "正式 Artifact 缺少必需输入 Hash: "
                + ", ".join(missing_inputs)
            )
        if not suites:
            raise ArtifactManifestError(
                "正式 Artifact 必须记录已执行验证 suite"
            )
        _validate_nonempty(sbom_path, field_name="sbom_path")
        _validate_nonempty(
            dependency_manifest_path,
            field_name="dependency_manifest_path",
        )
        _validate_sha256(
            verification_results_sha256,
            field_name="verification_results_sha256",
        )
        if profile_id == "nanobot-runtime":
            _validate_nonempty(
                schema_migration_head,
                field_name="schema_migration_head",
            )
    else:
        if any((
            normalized_hashes,
            schema_migration_head,
            sbom_path,
            dependency_manifest_path,
            suites,
            verification_results_sha256,
        )):
            raise ArtifactManifestError(
                "observed Artifact 只能记录可从容器验证的证据"
            )

    content = {
        "schema_version": 1,
        "profile_id": profile_id,
        "artifact_id": profile_id,
        "artifact_kind": profile.artifact_kind,
        "provenance": provenance,
        "source": source.to_dict(),
        "input_hashes": normalized_hashes,
        "schema_migration_head": schema_migration_head,
        "oci_image_reference": image_reference,
        "oci_image_digest": image_digest,
        "oci_image_id": image_id,
        "sbom_path": sbom_path,
        "dependency_manifest_path": dependency_manifest_path,
        "verification_suites": list(suites),
        "verification_results_sha256": verification_results_sha256,
        "built_at": built_at,
        "builder_version": builder_version,
    }
    encoded = canonical_json(content)
    return ArtifactManifest(
        profile_id=profile_id,
        artifact_id=profile_id,
        artifact_kind=profile.artifact_kind,
        provenance=provenance,
        source=source,
        input_hashes=MappingProxyType(normalized_hashes),
        schema_migration_head=schema_migration_head,
        oci_image_reference=image_reference,
        oci_image_digest=image_digest,
        oci_image_id=image_id,
        sbom_path=sbom_path,
        dependency_manifest_path=dependency_manifest_path,
        verification_suites=suites,
        verification_results_sha256=verification_results_sha256,
        built_at=built_at,
        builder_version=builder_version,
        canonical_json=encoded,
        sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )


def build_observed_runtime_artifact(
    *,
    image_reference: str,
    image_id: str,
    revision: str,
    observed_at: str,
) -> ArtifactManifest:
    """为迁移前已经运行的 Runtime 生成受限回滚证据。"""

    immutable_reference = (
        image_reference
        if _IMAGE_REFERENCE_PATTERN.fullmatch(image_reference)
        is not None
        else _validate_image_id(image_id)
    )
    return build_artifact_manifest(
        profile_id="nanobot-runtime",
        provenance="observed",
        source=ArtifactSource(
            git_full_commit=revision,
            git_dirty=None,
            kt_commit="",
        ),
        input_hashes={},
        schema_migration_head="",
        oci_image_reference=immutable_reference,
        oci_image_id=image_id,
        sbom_path="",
        dependency_manifest_path="",
        verification_suites=(),
        verification_results_sha256="",
        built_at=observed_at,
        builder_version="observed-runtime-v1",
    )


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """同一次发布中全部 Artifact 与固定服务绑定。"""

    release_id: str
    created_at: str
    artifacts: tuple[ArtifactManifest, ...]
    fixed_service_artifacts: Mapping[str, str]
    canonical_json: str
    sha256: str

    @property
    def runtime_artifact(self) -> ArtifactManifest:
        for artifact in self.artifacts:
            if artifact.artifact_id == "nanobot-runtime":
                return artifact
        raise ReleaseManifestError(
            "ReleaseManifest 缺少 nanobot-runtime"
        )

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "release_id": self.release_id,
            "created_at": self.created_at,
            "artifacts": [
                artifact.to_dict() for artifact in self.artifacts
            ],
            "fixed_service_artifacts": dict(
                self.fixed_service_artifacts
            ),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "sha256": self.sha256}


def build_release_manifest(
    *,
    artifacts: Iterable[ArtifactManifest],
    created_at: str,
) -> ReleaseManifest:
    """将 Artifact 冻结为四服务不可拆分的 Release。"""

    _validate_aware_datetime(created_at, field_name="created_at")
    candidates = tuple(artifacts)
    if not candidates or any(
        not isinstance(artifact, ArtifactManifest)
        for artifact in candidates
    ):
        raise ReleaseManifestError(
            "ReleaseManifest 至少需要一个 ArtifactManifest"
        )
    ordered = tuple(sorted(
        candidates,
        key=lambda artifact: artifact.artifact_id,
    ))
    artifact_ids = [artifact.artifact_id for artifact in ordered]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ReleaseManifestError(
            "ReleaseManifest 不能重复引用 Artifact"
        )
    if "nanobot-runtime" not in artifact_ids:
        raise ReleaseManifestError(
            "ReleaseManifest 必须包含 nanobot-runtime"
        )
    bindings = {
        service: "nanobot-runtime"
        for service in FIXED_RUNTIME_SERVICES
    }
    identity_content = {
        "schema_version": 1,
        "created_at": created_at,
        "artifact_hashes": {
            artifact.artifact_id: artifact.sha256
            for artifact in ordered
        },
        "fixed_service_artifacts": bindings,
    }
    identity = hashlib.sha256(
        canonical_json(identity_content).encode("utf-8")
    ).hexdigest()
    release_id = f"release_{identity[:24]}"
    content = {
        "schema_version": 1,
        "release_id": release_id,
        "created_at": created_at,
        "artifacts": [
            artifact.to_dict() for artifact in ordered
        ],
        "fixed_service_artifacts": bindings,
    }
    encoded = canonical_json(content)
    return ReleaseManifest(
        release_id=release_id,
        created_at=created_at,
        artifacts=ordered,
        fixed_service_artifacts=MappingProxyType(bindings),
        canonical_json=encoded,
        sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )


def _require_object(
    value: object,
    *,
    field_name: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ReleaseManifestError(f"{field_name} 必须是 JSON object")
    return value


def _require_exact_keys(
    payload: Mapping[str, object],
    *,
    expected: frozenset[str],
    field_name: str,
) -> None:
    unknown = sorted(set(payload) - expected)
    missing = sorted(expected - set(payload))
    if unknown:
        raise ReleaseManifestError(
            f"{field_name} 包含未知字段: {', '.join(unknown)}"
        )
    if missing:
        raise ReleaseManifestError(
            f"{field_name} 缺少字段: {', '.join(missing)}"
        )


_ARTIFACT_FIELDS = frozenset({
    "schema_version",
    "profile_id",
    "artifact_id",
    "artifact_kind",
    "provenance",
    "source",
    "input_hashes",
    "schema_migration_head",
    "oci_image_reference",
    "oci_image_digest",
    "oci_image_id",
    "sbom_path",
    "dependency_manifest_path",
    "verification_suites",
    "verification_results_sha256",
    "built_at",
    "builder_version",
    "sha256",
})
_SOURCE_FIELDS = frozenset({
    "git_full_commit",
    "git_dirty",
    "kt_commit",
})
_RELEASE_FIELDS = frozenset({
    "schema_version",
    "release_id",
    "created_at",
    "artifacts",
    "fixed_service_artifacts",
    "sha256",
})


def _artifact_from_dict(value: object) -> ArtifactManifest:
    payload = _require_object(value, field_name="artifact")
    _require_exact_keys(
        payload,
        expected=_ARTIFACT_FIELDS,
        field_name="ArtifactManifest",
    )
    try:
        if payload["schema_version"] != 1:
            raise ReleaseManifestError(
                "ArtifactManifest schema_version 无效"
            )
        source_payload = _require_object(
            payload["source"],
            field_name="artifact.source",
        )
        _require_exact_keys(
            source_payload,
            expected=_SOURCE_FIELDS,
            field_name="ArtifactManifest source",
        )
        source = ArtifactSource(
            git_full_commit=str(source_payload["git_full_commit"]),
            git_dirty=source_payload["git_dirty"],
            kt_commit=str(source_payload["kt_commit"]),
        )
        artifact = build_artifact_manifest(
            profile_id=str(payload["profile_id"]),
            provenance=str(payload["provenance"]),
            source=source,
            input_hashes=_require_object(
                payload["input_hashes"],
                field_name="artifact.input_hashes",
            ),
            schema_migration_head=str(
                payload["schema_migration_head"]
            ),
            oci_image_reference=str(
                payload["oci_image_reference"]
            ),
            oci_image_id=str(payload["oci_image_id"]),
            sbom_path=str(payload["sbom_path"]),
            dependency_manifest_path=str(
                payload["dependency_manifest_path"]
            ),
            verification_suites=tuple(
                payload["verification_suites"]
            ),
            verification_results_sha256=str(
                payload["verification_results_sha256"]
            ),
            built_at=str(payload["built_at"]),
            builder_version=str(payload["builder_version"]),
        )
        expected_sha = str(payload["sha256"])
    except (
        ArtifactManifestError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise ReleaseManifestError(
            "ArtifactManifest 字段无效"
        ) from exc
    if artifact.artifact_id != payload.get("artifact_id"):
        raise ReleaseManifestError("Artifact ID 与 BuildProfile 不一致")
    if artifact.artifact_kind != payload.get("artifact_kind"):
        raise ReleaseManifestError("Artifact kind 与 BuildProfile 不一致")
    if artifact.oci_image_digest != payload.get("oci_image_digest"):
        raise ReleaseManifestError("Artifact 镜像 digest 不一致")
    if artifact.sha256 != expected_sha:
        raise ReleaseManifestError("ArtifactManifest Hash 校验失败")
    return artifact


def release_manifest_from_dict(value: object) -> ReleaseManifest:
    payload = _require_object(value, field_name="release")
    _require_exact_keys(
        payload,
        expected=_RELEASE_FIELDS,
        field_name="ReleaseManifest",
    )
    try:
        if payload["schema_version"] != 1:
            raise ReleaseManifestError(
                "ReleaseManifest schema_version 无效"
            )
        artifacts_value = payload["artifacts"]
        if not isinstance(artifacts_value, list):
            raise ReleaseManifestError(
                "ReleaseManifest artifacts 必须是数组"
            )
        release = build_release_manifest(
            artifacts=tuple(
                _artifact_from_dict(artifact)
                for artifact in artifacts_value
            ),
            created_at=str(payload["created_at"]),
        )
        expected_bindings = _require_object(
            payload["fixed_service_artifacts"],
            field_name="fixed_service_artifacts",
        )
        expected_release_id = str(payload["release_id"])
        expected_sha = str(payload["sha256"])
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ReleaseManifestError):
            raise
        raise ReleaseManifestError(
            "ReleaseManifest 字段缺失或类型无效"
        ) from exc
    if dict(release.fixed_service_artifacts) != expected_bindings:
        raise ReleaseManifestError("固定服务 Artifact 绑定无效")
    if release.release_id != expected_release_id:
        raise ReleaseManifestError("Release ID 校验失败")
    if release.sha256 != expected_sha:
        raise ReleaseManifestError("ReleaseManifest Hash 校验失败")
    return release


def load_release_manifest(path: Path) -> ReleaseManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(
            f"无法读取 ReleaseManifest：{path}"
        ) from exc
    return release_manifest_from_dict(payload)


def load_artifact_manifest(path: Path) -> ArtifactManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactManifestError(
            f"无法读取 ArtifactManifest：{path}"
        ) from exc
    try:
        return _artifact_from_dict(payload)
    except ReleaseManifestError as exc:
        raise ArtifactManifestError(
            f"ArtifactManifest 无效：{path}"
        ) from exc


def dump_artifact_manifest(
    path: Path,
    artifact: ArtifactManifest,
) -> None:
    if not isinstance(artifact, ArtifactManifest):
        raise ArtifactManifestError(
            "artifact 必须是 ArtifactManifest"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = (
        json.dumps(
            artifact.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def dump_release_manifest(
    path: Path,
    release: ReleaseManifest,
) -> None:
    """以同目录原子替换写入 ReleaseManifest。"""

    if not isinstance(release, ReleaseManifest):
        raise ReleaseManifestError("release 必须是 ReleaseManifest")
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = (
        json.dumps(
            release.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
