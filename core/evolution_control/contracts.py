"""受控自进化的不可变数据集、候选和目标合同。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence


EVOLUTION_SCHEMA_VERSION = 1
DATASET_SPLIT_ROLES = ("baseline", "training", "validation", "test")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_REVISION_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{1,127}")
_RESOURCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")


class EvolutionContractError(ValueError):
    """候选、数据集或授权合同不满足受控进化边界。"""


class EvolutionTargetKind(str, Enum):
    PROMPT = "prompt"
    SKILL = "skill"
    ROUTING = "routing"
    MANIFEST = "manifest"


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvolutionContractError(f"{name} 必须是 JSON 对象")
    if any(not isinstance(key, str) for key in value):
        raise EvolutionContractError(f"{name} 的键必须是字符串")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise EvolutionContractError(f"{name} 必须是 JSON 数组")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    *,
    name: str,
    required: frozenset[str],
) -> None:
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required)
    if missing:
        raise EvolutionContractError(f"{name} 缺少字段: {', '.join(missing)}")
    if unknown:
        raise EvolutionContractError(
            f"{name} 包含未允许字段: {', '.join(unknown)}"
        )


def _required_text(value: object, name: str, *, maximum: int = 512) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise EvolutionContractError(f"{name} 不能为空")
    if len(normalized) > maximum:
        raise EvolutionContractError(f"{name} 不能超过 {maximum} 个字符")
    return normalized


def _identifier(value: object, name: str) -> str:
    normalized = _required_text(value, name, maximum=128)
    if _ID_RE.fullmatch(normalized) is None:
        raise EvolutionContractError(f"{name} 必须是安全标识符")
    return normalized


def _resource_id(value: object, name: str) -> str:
    normalized = _required_text(value, name, maximum=256)
    if (
        _RESOURCE_RE.fullmatch(normalized) is None
        or ".." in normalized.split("/")
        or normalized.startswith(("/", "."))
    ):
        raise EvolutionContractError(f"{name} 必须是安全资源标识符")
    return normalized


def _sha256(value: object, name: str, *, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized and allow_empty:
        return ""
    if _SHA256_RE.fullmatch(normalized) is None:
        raise EvolutionContractError(f"{name} 必须是 SHA-256")
    return normalized


def _source_revision(value: object, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if _REVISION_RE.fullmatch(normalized) is None:
        raise EvolutionContractError(f"{name} 必须是完整 Git revision")
    return normalized


def _nonnegative_int(value: object, name: str, *, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise EvolutionContractError(f"{name} 必须位于 0..{maximum}")
    return value


def _aware_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise EvolutionContractError(f"{name} 必须是 ISO 8601 字符串")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvolutionContractError(f"{name} 不是有效 ISO 8601 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvolutionContractError(f"{name} 必须包含时区")
    return parsed.astimezone(timezone.utc)


def _schema_version(value: object, name: str) -> None:
    if type(value) is not int or value != EVOLUTION_SCHEMA_VERSION:
        raise EvolutionContractError(f"{name} 不受支持")


def _freeze_json_mapping(
    value: Mapping[str, Any],
    *,
    name: str,
    maximum_bytes: int = 131_072,
) -> Mapping[str, Any]:
    try:
        encoded = canonical_json(value).encode("utf-8")
        copied = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EvolutionContractError(f"{name} 必须是可序列化 JSON") from exc
    if len(encoded) > maximum_bytes:
        raise EvolutionContractError(f"{name} 超过 {maximum_bytes} 字节")
    if not isinstance(copied, dict):
        raise EvolutionContractError(f"{name} 必须是 JSON 对象")
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class FrozenDatasetSplit:
    role: str
    source_id: str
    revision: str
    license_id: str
    artifact_sha256: str
    expected_count: int
    answers_visible_to_generator: bool

    def __post_init__(self) -> None:
        if self.role not in DATASET_SPLIT_ROLES:
            raise EvolutionContractError("dataset split role 无效")
        object.__setattr__(
            self,
            "source_id",
            _resource_id(self.source_id, f"splits.{self.role}.source_id"),
        )
        object.__setattr__(
            self,
            "revision",
            _required_text(
                self.revision,
                f"splits.{self.role}.revision",
                maximum=128,
            ),
        )
        object.__setattr__(
            self,
            "license_id",
            _required_text(
                self.license_id,
                f"splits.{self.role}.license_id",
                maximum=128,
            ),
        )
        object.__setattr__(
            self,
            "artifact_sha256",
            _sha256(
                self.artifact_sha256,
                f"splits.{self.role}.artifact_sha256",
            ),
        )
        object.__setattr__(
            self,
            "expected_count",
            _nonnegative_int(
                self.expected_count,
                f"splits.{self.role}.expected_count",
                maximum=10_000_000,
            ),
        )
        if self.expected_count < 1:
            raise EvolutionContractError(
                f"splits.{self.role}.expected_count 必须大于 0"
            )
        if type(self.answers_visible_to_generator) is not bool:
            raise EvolutionContractError(
                f"splits.{self.role}.answers_visible_to_generator 必须是 bool"
            )
        expected_visibility = self.role in {"baseline", "training"}
        if self.answers_visible_to_generator is not expected_visibility:
            raise EvolutionContractError(
                "只有 baseline/training 可向候选生成器暴露答案"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "source_id": self.source_id,
            "revision": self.revision,
            "license_id": self.license_id,
            "artifact_sha256": self.artifact_sha256,
            "expected_count": self.expected_count,
            "answers_visible_to_generator": self.answers_visible_to_generator,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FrozenDatasetSplit":
        payload = _mapping(value, "dataset split")
        _exact_keys(
            payload,
            name="dataset split",
            required=frozenset({
                "role",
                "source_id",
                "revision",
                "license_id",
                "artifact_sha256",
                "expected_count",
                "answers_visible_to_generator",
            }),
        )
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class FrozenDatasetManifest:
    schema_version: int
    dataset_id: str
    revision: str
    source_revision: str
    created_at: str
    splits: tuple[FrozenDatasetSplit, ...]
    dataset_sha256: str = ""

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, "dataset.schema_version")
        object.__setattr__(
            self,
            "dataset_id",
            _identifier(self.dataset_id, "dataset.dataset_id"),
        )
        object.__setattr__(
            self,
            "revision",
            _required_text(self.revision, "dataset.revision", maximum=128),
        )
        object.__setattr__(
            self,
            "source_revision",
            _source_revision(self.source_revision, "dataset.source_revision"),
        )
        created = _aware_timestamp(self.created_at, "dataset.created_at")
        object.__setattr__(self, "created_at", created.isoformat())
        splits = tuple(sorted(self.splits, key=lambda item: DATASET_SPLIT_ROLES.index(item.role)))
        if any(not isinstance(item, FrozenDatasetSplit) for item in splits):
            raise EvolutionContractError("dataset.splits 包含无效项")
        if tuple(item.role for item in splits) != DATASET_SPLIT_ROLES:
            raise EvolutionContractError(
                "dataset.splits 必须且只能包含 baseline/training/validation/test"
            )
        object.__setattr__(self, "splits", splits)
        digest = sha256_json(self._payload())
        declared = _sha256(
            self.dataset_sha256,
            "dataset.dataset_sha256",
            allow_empty=True,
        )
        if declared and declared != digest:
            raise EvolutionContractError("dataset.dataset_sha256 与内容不匹配")
        object.__setattr__(self, "dataset_sha256", digest)

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "revision": self.revision,
            "source_revision": self.source_revision,
            "created_at": self.created_at,
            "splits": [item.to_dict() for item in self.splits],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "dataset_sha256": self.dataset_sha256}

    def split(self, role: str) -> FrozenDatasetSplit:
        return next(item for item in self.splits if item.role == role)

    def generation_view(self) -> dict[str, object]:
        """候选生成器只获得训练侧元数据；held-out 集保持 sealed。"""

        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "revision": self.revision,
            "dataset_sha256": self.dataset_sha256,
            "available_splits": [
                item.to_dict()
                for item in self.splits
                if item.answers_visible_to_generator
            ],
            "sealed_splits": [
                {
                    "role": item.role,
                    "artifact_sha256": item.artifact_sha256,
                    "expected_count": item.expected_count,
                    "answers_visible_to_generator": False,
                }
                for item in self.splits
                if not item.answers_visible_to_generator
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> "FrozenDatasetManifest":
        payload = _mapping(value, "dataset")
        _exact_keys(
            payload,
            name="dataset",
            required=frozenset({
                "schema_version",
                "dataset_id",
                "revision",
                "source_revision",
                "created_at",
                "splits",
                "dataset_sha256",
            }),
        )
        return cls(
            schema_version=payload["schema_version"],
            dataset_id=payload["dataset_id"],
            revision=payload["revision"],
            source_revision=payload["source_revision"],
            created_at=payload["created_at"],
            splits=tuple(
                FrozenDatasetSplit.from_dict(item)
                for item in _sequence(payload["splits"], "dataset.splits")
            ),
            dataset_sha256=payload["dataset_sha256"],
        )


@dataclass(frozen=True, slots=True)
class EvolutionGenerationProof:
    lane: str
    actor_kind: str
    generator_id: str
    generator_version: str
    source_revision: str
    seed: int
    parent_bundle_sha256: str
    production_data_access: bool
    network_access: bool
    repository_write_access: bool

    def __post_init__(self) -> None:
        if self.lane != "offline_deterministic":
            raise EvolutionContractError("候选只能在 offline_deterministic lane 生成")
        if self.actor_kind != "offline_generator":
            raise EvolutionContractError("生产 Agent 或模型不能充当候选生成 actor")
        object.__setattr__(
            self,
            "generator_id",
            _identifier(self.generator_id, "generation.generator_id"),
        )
        object.__setattr__(
            self,
            "generator_version",
            _required_text(
                self.generator_version,
                "generation.generator_version",
                maximum=128,
            ),
        )
        object.__setattr__(
            self,
            "source_revision",
            _source_revision(
                self.source_revision,
                "generation.source_revision",
            ),
        )
        object.__setattr__(
            self,
            "seed",
            _nonnegative_int(self.seed, "generation.seed", maximum=2**63 - 1),
        )
        object.__setattr__(
            self,
            "parent_bundle_sha256",
            _sha256(
                self.parent_bundle_sha256,
                "generation.parent_bundle_sha256",
                allow_empty=True,
            ),
        )
        for name in (
            "production_data_access",
            "network_access",
            "repository_write_access",
        ):
            if type(getattr(self, name)) is not bool:
                raise EvolutionContractError(f"generation.{name} 必须是 bool")
            if getattr(self, name):
                raise EvolutionContractError(
                    "候选生成禁止生产数据、网络和仓库写入"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "lane": self.lane,
            "actor_kind": self.actor_kind,
            "generator_id": self.generator_id,
            "generator_version": self.generator_version,
            "source_revision": self.source_revision,
            "seed": self.seed,
            "parent_bundle_sha256": self.parent_bundle_sha256,
            "production_data_access": self.production_data_access,
            "network_access": self.network_access,
            "repository_write_access": self.repository_write_access,
        }

    @classmethod
    def from_dict(cls, value: object) -> "EvolutionGenerationProof":
        payload = _mapping(value, "generation")
        _exact_keys(
            payload,
            name="generation",
            required=frozenset({
                "lane",
                "actor_kind",
                "generator_id",
                "generator_version",
                "source_revision",
                "seed",
                "parent_bundle_sha256",
                "production_data_access",
                "network_access",
                "repository_write_access",
            }),
        )
        return cls(**payload)


_TARGET_CHANGE_FIELDS: dict[EvolutionTargetKind, frozenset[str]] = {
    EvolutionTargetKind.PROMPT: frozenset({
        "bundle_id",
        "version",
        "content_sha256",
        "artifact_uri",
    }),
    EvolutionTargetKind.SKILL: frozenset({
        "package_id",
        "skill_name",
        "version",
        "bundle_sha256",
    }),
    EvolutionTargetKind.ROUTING: frozenset({
        "route_key",
        "ordered_model_ids",
        "required_capabilities",
        "max_cost_microunits",
    }),
    EvolutionTargetKind.MANIFEST: frozenset({
        "model.route_key",
        "model.required_capabilities",
        "prompt.bundle_id",
        "prompt.version",
        "prompt.content_sha256",
        "extensions.skill_refs",
    }),
}


def _immutable_artifact_uri(value: object, digest: str, name: str) -> str:
    normalized = _required_text(value, name, maximum=256)
    if normalized not in {
        f"asset://sha256/{digest}",
        f"artifact://sha256/{digest}",
    }:
        raise EvolutionContractError(
            f"{name} 必须引用与内容摘要一致的 immutable asset/artifact"
        )
    return normalized


def _identifier_list(
    value: object,
    name: str,
    *,
    maximum: int = 32,
) -> list[str]:
    items = [_resource_id(item, name) for item in _sequence(value, name)]
    if not items or len(items) > maximum or len(items) != len(set(items)):
        raise EvolutionContractError(f"{name} 必须非空、唯一且不超过 {maximum} 项")
    return items


def _normalize_target_changes(
    kind: EvolutionTargetKind,
    value: object,
) -> Mapping[str, Any]:
    payload = _mapping(value, "target.changes")
    allowed = _TARGET_CHANGE_FIELDS[kind]
    if not payload:
        raise EvolutionContractError("target.changes 不能为空")
    unknown = sorted(payload.keys() - allowed)
    if unknown:
        raise EvolutionContractError(
            f"{kind.value} 候选包含禁止修改的字段: {', '.join(unknown)}"
        )
    normalized: dict[str, Any] = {}
    if kind is EvolutionTargetKind.PROMPT:
        if set(payload) != allowed:
            raise EvolutionContractError("prompt 候选必须完整声明 immutable bundle")
        content_sha = _sha256(payload["content_sha256"], "target.changes.content_sha256")
        normalized = {
            "bundle_id": _resource_id(payload["bundle_id"], "target.changes.bundle_id"),
            "version": _required_text(payload["version"], "target.changes.version", maximum=128),
            "content_sha256": content_sha,
            "artifact_uri": _immutable_artifact_uri(
                payload["artifact_uri"],
                content_sha,
                "target.changes.artifact_uri",
            ),
        }
    elif kind is EvolutionTargetKind.SKILL:
        if set(payload) != allowed:
            raise EvolutionContractError("skill 候选必须完整声明 immutable package")
        normalized = {
            "package_id": _identifier(payload["package_id"], "target.changes.package_id"),
            "skill_name": _identifier(payload["skill_name"], "target.changes.skill_name"),
            "version": _required_text(payload["version"], "target.changes.version", maximum=128),
            "bundle_sha256": _sha256(
                payload["bundle_sha256"],
                "target.changes.bundle_sha256",
            ),
        }
    elif kind is EvolutionTargetKind.ROUTING:
        if "route_key" not in payload or "ordered_model_ids" not in payload:
            raise EvolutionContractError("routing 候选必须声明 route_key 和 ordered_model_ids")
        normalized = {
            "route_key": _identifier(payload["route_key"], "target.changes.route_key"),
            "ordered_model_ids": _identifier_list(
                payload["ordered_model_ids"],
                "target.changes.ordered_model_ids",
                maximum=16,
            ),
        }
        if "required_capabilities" in payload:
            normalized["required_capabilities"] = _identifier_list(
                payload["required_capabilities"],
                "target.changes.required_capabilities",
                maximum=16,
            )
        if "max_cost_microunits" in payload:
            normalized["max_cost_microunits"] = _nonnegative_int(
                payload["max_cost_microunits"],
                "target.changes.max_cost_microunits",
                maximum=10**12,
            )
    else:
        for field_name, field_value in payload.items():
            if field_name in {
                "model.required_capabilities",
                "extensions.skill_refs",
            }:
                normalized[field_name] = _identifier_list(
                    field_value,
                    f"target.changes.{field_name}",
                    maximum=32,
                )
            elif field_name == "prompt.content_sha256":
                normalized[field_name] = _sha256(
                    field_value,
                    f"target.changes.{field_name}",
                )
            else:
                normalized[field_name] = _resource_id(
                    field_value,
                    f"target.changes.{field_name}",
                )
    return _freeze_json_mapping(normalized, name="target.changes")


@dataclass(frozen=True, slots=True)
class EvolutionTarget:
    kind: EvolutionTargetKind
    resource_id: str
    base_sha256: str
    changes: Mapping[str, Any]

    def __post_init__(self) -> None:
        try:
            kind = EvolutionTargetKind(self.kind)
        except ValueError as exc:
            raise EvolutionContractError("target.kind 无效") from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "resource_id",
            _resource_id(self.resource_id, "target.resource_id"),
        )
        object.__setattr__(
            self,
            "base_sha256",
            _sha256(self.base_sha256, "target.base_sha256"),
        )
        object.__setattr__(
            self,
            "changes",
            _normalize_target_changes(kind, self.changes),
        )
        if (
            kind is EvolutionTargetKind.ROUTING
            and self.changes["route_key"] != self.resource_id
        ):
            raise EvolutionContractError(
                "routing target.resource_id 必须等于 changes.route_key"
            )

    @property
    def target_key(self) -> str:
        return f"{self.kind.value}:{self.resource_id}"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "resource_id": self.resource_id,
            "base_sha256": self.base_sha256,
            "changes": dict(self.changes),
        }

    @classmethod
    def from_dict(cls, value: object) -> "EvolutionTarget":
        payload = _mapping(value, "target")
        _exact_keys(
            payload,
            name="target",
            required=frozenset({
                "kind",
                "resource_id",
                "base_sha256",
                "changes",
            }),
        )
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class EvolutionCandidateBundle:
    schema_version: int
    candidate_id: str
    created_at: str
    dataset_sha256: str
    generation: EvolutionGenerationProof
    target: EvolutionTarget
    rationale: str
    evidence_sha256s: tuple[str, ...]
    repository_operations: str = "forbidden"
    candidate_sha256: str = ""

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, "candidate.schema_version")
        object.__setattr__(
            self,
            "candidate_id",
            _identifier(self.candidate_id, "candidate.candidate_id"),
        )
        created = _aware_timestamp(self.created_at, "candidate.created_at")
        object.__setattr__(self, "created_at", created.isoformat())
        object.__setattr__(
            self,
            "dataset_sha256",
            _sha256(self.dataset_sha256, "candidate.dataset_sha256"),
        )
        if not isinstance(self.generation, EvolutionGenerationProof):
            raise EvolutionContractError("candidate.generation 无效")
        if not isinstance(self.target, EvolutionTarget):
            raise EvolutionContractError("candidate.target 无效")
        object.__setattr__(
            self,
            "rationale",
            _required_text(self.rationale, "candidate.rationale", maximum=4096),
        )
        evidence = tuple(
            _sha256(item, "candidate.evidence_sha256")
            for item in self.evidence_sha256s
        )
        if not evidence or len(evidence) > 64 or len(evidence) != len(set(evidence)):
            raise EvolutionContractError(
                "candidate.evidence_sha256s 必须非空、唯一且不超过 64 项"
            )
        object.__setattr__(self, "evidence_sha256s", tuple(sorted(evidence)))
        if self.repository_operations != "forbidden":
            raise EvolutionContractError(
                "候选不能修改、提交、tag、push 或回退主干仓库"
            )
        digest = sha256_json(self._payload())
        declared = _sha256(
            self.candidate_sha256,
            "candidate.candidate_sha256",
            allow_empty=True,
        )
        if declared and declared != digest:
            raise EvolutionContractError("candidate.candidate_sha256 与内容不匹配")
        object.__setattr__(self, "candidate_sha256", digest)

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "created_at": self.created_at,
            "dataset_sha256": self.dataset_sha256,
            "generation": self.generation.to_dict(),
            "target": self.target.to_dict(),
            "rationale": self.rationale,
            "evidence_sha256s": list(self.evidence_sha256s),
            "repository_operations": self.repository_operations,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "candidate_sha256": self.candidate_sha256}

    @classmethod
    def from_dict(cls, value: object) -> "EvolutionCandidateBundle":
        payload = _mapping(value, "candidate")
        _exact_keys(
            payload,
            name="candidate",
            required=frozenset({
                "schema_version",
                "candidate_id",
                "created_at",
                "dataset_sha256",
                "generation",
                "target",
                "rationale",
                "evidence_sha256s",
                "repository_operations",
                "candidate_sha256",
            }),
        )
        return cls(
            schema_version=payload["schema_version"],
            candidate_id=payload["candidate_id"],
            created_at=payload["created_at"],
            dataset_sha256=payload["dataset_sha256"],
            generation=EvolutionGenerationProof.from_dict(payload["generation"]),
            target=EvolutionTarget.from_dict(payload["target"]),
            rationale=payload["rationale"],
            evidence_sha256s=tuple(
                _sequence(
                    payload["evidence_sha256s"],
                    "candidate.evidence_sha256s",
                )
            ),
            repository_operations=payload["repository_operations"],
            candidate_sha256=payload["candidate_sha256"],
        )


def evolution_catalog_payload() -> dict[str, object]:
    return {
        "schema_version": EVOLUTION_SCHEMA_VERSION,
        "generation_lane": "offline_deterministic",
        "dataset_splits": list(DATASET_SPLIT_ROLES),
        "target_change_fields": {
            kind.value: sorted(fields)
            for kind, fields in _TARGET_CHANGE_FIELDS.items()
        },
        "approval": {
            "reviewer_kind": "human",
            "target_environment": "canary",
            "credential": "single_use_server_token",
        },
        "blocked_actions": [
            "production_data_access",
            "network_generation",
            "repository_write",
            "git_commit",
            "git_tag",
            "git_push",
            "destructive_revert",
            "evaluator_mutation",
            "permission_mutation",
            "self_approval",
            "production_activation",
        ],
    }


__all__ = [
    "DATASET_SPLIT_ROLES",
    "EVOLUTION_SCHEMA_VERSION",
    "EvolutionCandidateBundle",
    "EvolutionContractError",
    "EvolutionGenerationProof",
    "EvolutionTarget",
    "EvolutionTargetKind",
    "FrozenDatasetManifest",
    "FrozenDatasetSplit",
    "canonical_json",
    "evolution_catalog_payload",
    "sha256_json",
]
