"""Skill 经验候选、来源 trajectory 与独立评测的严格合同。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping, Sequence

from core.evolution_control.contracts import canonical_json, sha256_json
from core.skills import SkillContractError, SkillScopeTarget, parse_skill_bundle


SKILL_CANDIDATE_SCHEMA_VERSION = 1
MAX_SOURCE_RUNS = 20
MAX_PATTERNS = 32
MAX_COST_INCREASE_BASIS_POINTS = 2_000
NO_SKILL_BASELINE_SHA256 = sha256_json({
    "kind": "absent_skill",
    "schema_version": SKILL_CANDIDATE_SCHEMA_VERSION,
})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,127}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,159}$")
_PATTERN_KIND_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


class SkillCandidateContractError(ValueError):
    """经验候选或其治理证据不满足稳定边界。"""


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SkillCandidateContractError(f"{name} 必须是 JSON 对象")
    if any(not isinstance(key, str) for key in value):
        raise SkillCandidateContractError(f"{name} 的键必须是字符串")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SkillCandidateContractError(f"{name} 必须是 JSON 数组")
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
        raise SkillCandidateContractError(
            f"{name} 缺少字段: {', '.join(missing)}"
        )
    if unknown:
        raise SkillCandidateContractError(
            f"{name} 包含未允许字段: {', '.join(unknown)}"
        )


def _schema(value: object, name: str) -> int:
    if type(value) is not int or value != SKILL_CANDIDATE_SCHEMA_VERSION:
        raise SkillCandidateContractError(f"{name} 不受支持")
    return value


def _text(
    value: object,
    name: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise SkillCandidateContractError(f"{name} 必须是字符串")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise SkillCandidateContractError(f"{name} 不能为空")
    if len(normalized) > maximum:
        raise SkillCandidateContractError(f"{name} 不能超过 {maximum} 个字符")
    if any(ord(char) < 32 and char not in {"\t", "\n"} for char in normalized):
        raise SkillCandidateContractError(f"{name} 包含控制字符")
    return normalized


def _identifier(value: object, name: str) -> str:
    normalized = _text(value, name, maximum=128)
    if _ID_RE.fullmatch(normalized) is None:
        raise SkillCandidateContractError(f"{name} 必须是安全标识符")
    return normalized


def _run_id(value: object, name: str) -> str:
    normalized = _text(value, name, maximum=160)
    if _RUN_ID_RE.fullmatch(normalized) is None:
        raise SkillCandidateContractError(f"{name} 必须是安全 Run 标识符")
    return normalized


def _sha256(value: object, name: str, *, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized and allow_empty:
        return ""
    if _SHA256_RE.fullmatch(normalized) is None:
        raise SkillCandidateContractError(f"{name} 必须是 SHA-256")
    return normalized


def _revision(value: object, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if _REVISION_RE.fullmatch(normalized) is None:
        raise SkillCandidateContractError(f"{name} 必须是完整 Git revision")
    return normalized


def _timestamp(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise SkillCandidateContractError(f"{name} 必须是 ISO 8601 字符串")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SkillCandidateContractError(f"{name} 不是有效时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SkillCandidateContractError(f"{name} 必须包含时区")
    return parsed.astimezone(timezone.utc).isoformat()


def _nonnegative_int(value: object, name: str, *, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise SkillCandidateContractError(f"{name} 必须位于 0..{maximum}")
    return value


def _sha_tuple(
    value: Sequence[object],
    name: str,
    *,
    maximum: int = 128,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    items = tuple(sorted(_sha256(item, name) for item in value))
    if (not items and not allow_empty) or len(items) > maximum:
        raise SkillCandidateContractError(
            f"{name} 数量必须位于 {0 if allow_empty else 1}..{maximum}"
        )
    if len(items) != len(set(items)):
        raise SkillCandidateContractError(f"{name} 不能重复")
    return items


def _run_id_tuple(
    value: Sequence[object],
    name: str,
) -> tuple[str, ...]:
    items = tuple(sorted(_run_id(item, name) for item in value))
    if not items or len(items) > MAX_SOURCE_RUNS or len(items) != len(set(items)):
        raise SkillCandidateContractError(
            f"{name} 必须非空、唯一且不超过 {MAX_SOURCE_RUNS} 项"
        )
    return items


@dataclass(frozen=True, slots=True)
class SourceRunEvidence:
    run_id: str
    outcome: str
    run_view_sha256: str
    trajectory_sha256: str
    span_count: int
    failure_count: int
    redaction_count: int
    evidence_sha256s: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _run_id(self.run_id, "source.run_id"))
        if self.outcome not in {"succeeded", "failed"}:
            raise SkillCandidateContractError(
                "source.outcome 必须是 succeeded 或 failed"
            )
        for name in ("run_view_sha256", "trajectory_sha256"):
            object.__setattr__(
                self,
                name,
                _sha256(getattr(self, name), f"source.{name}"),
            )
        object.__setattr__(
            self,
            "span_count",
            _nonnegative_int(self.span_count, "source.span_count", maximum=20_000),
        )
        if self.span_count < 1:
            raise SkillCandidateContractError("source.span_count 必须大于 0")
        for name in ("failure_count", "redaction_count"):
            object.__setattr__(
                self,
                name,
                _nonnegative_int(
                    getattr(self, name),
                    f"source.{name}",
                    maximum=20_000,
                ),
            )
        object.__setattr__(
            self,
            "evidence_sha256s",
            _sha_tuple(self.evidence_sha256s, "source.evidence_sha256s", maximum=32),
        )
        if self.run_view_sha256 not in self.evidence_sha256s:
            raise SkillCandidateContractError(
                "source.evidence_sha256s 必须包含 run_view_sha256"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "outcome": self.outcome,
            "run_view_sha256": self.run_view_sha256,
            "trajectory_sha256": self.trajectory_sha256,
            "span_count": self.span_count,
            "failure_count": self.failure_count,
            "redaction_count": self.redaction_count,
            "evidence_sha256s": list(self.evidence_sha256s),
        }

    @classmethod
    def from_dict(cls, value: object) -> "SourceRunEvidence":
        payload = _mapping(value, "source run")
        _exact_keys(
            payload,
            name="source run",
            required=frozenset({
                "run_id",
                "outcome",
                "run_view_sha256",
                "trajectory_sha256",
                "span_count",
                "failure_count",
                "redaction_count",
                "evidence_sha256s",
            }),
        )
        return cls(
            run_id=payload["run_id"],
            outcome=payload["outcome"],
            run_view_sha256=payload["run_view_sha256"],
            trajectory_sha256=payload["trajectory_sha256"],
            span_count=payload["span_count"],
            failure_count=payload["failure_count"],
            redaction_count=payload["redaction_count"],
            evidence_sha256s=tuple(
                _sequence(payload["evidence_sha256s"], "source.evidence_sha256s")
            ),
        )


@dataclass(frozen=True, slots=True)
class ExperienceProcessStep:
    position: int
    kind: str
    name: str
    supporting_run_ids: tuple[str, ...]
    pattern_sha256: str = ""

    def __post_init__(self) -> None:
        position = _nonnegative_int(self.position, "process.position", maximum=MAX_PATTERNS)
        if position < 1:
            raise SkillCandidateContractError("process.position 必须大于 0")
        normalized_kind = str(self.kind or "").strip().lower()
        if _PATTERN_KIND_RE.fullmatch(normalized_kind) is None:
            raise SkillCandidateContractError("process.kind 无效")
        object.__setattr__(self, "kind", normalized_kind)
        object.__setattr__(self, "name", _text(self.name, "process.name", maximum=160))
        object.__setattr__(
            self,
            "supporting_run_ids",
            _run_id_tuple(self.supporting_run_ids, "process.supporting_run_ids"),
        )
        digest = sha256_json(self._payload())
        declared = _sha256(
            self.pattern_sha256,
            "process.pattern_sha256",
            allow_empty=True,
        )
        if declared and declared != digest:
            raise SkillCandidateContractError("process.pattern_sha256 与内容不匹配")
        object.__setattr__(self, "pattern_sha256", digest)

    def _payload(self) -> dict[str, object]:
        return {
            "position": self.position,
            "kind": self.kind,
            "name": self.name,
            "supporting_run_ids": list(self.supporting_run_ids),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "pattern_sha256": self.pattern_sha256}

    @classmethod
    def from_dict(cls, value: object) -> "ExperienceProcessStep":
        payload = _mapping(value, "process step")
        _exact_keys(
            payload,
            name="process step",
            required=frozenset({
                "position",
                "kind",
                "name",
                "supporting_run_ids",
                "pattern_sha256",
            }),
        )
        return cls(
            position=payload["position"],
            kind=payload["kind"],
            name=payload["name"],
            supporting_run_ids=tuple(
                _sequence(payload["supporting_run_ids"], "process.supporting_run_ids")
            ),
            pattern_sha256=payload["pattern_sha256"],
        )


@dataclass(frozen=True, slots=True)
class ExperienceFailurePattern:
    kind: str
    name: str
    code: str
    error_type: str
    retryable: bool
    occurrence_count: int
    supporting_run_ids: tuple[str, ...]
    pattern_sha256: str = ""

    def __post_init__(self) -> None:
        normalized_kind = str(self.kind or "").strip().lower()
        if _PATTERN_KIND_RE.fullmatch(normalized_kind) is None:
            raise SkillCandidateContractError("failure.kind 无效")
        object.__setattr__(self, "kind", normalized_kind)
        for name, maximum, allow_empty in (
            ("name", 160, False),
            ("code", 128, False),
            ("error_type", 128, True),
        ):
            object.__setattr__(
                self,
                name,
                _text(
                    getattr(self, name),
                    f"failure.{name}",
                    maximum=maximum,
                    allow_empty=allow_empty,
                ),
            )
        if type(self.retryable) is not bool:
            raise SkillCandidateContractError("failure.retryable 必须是 bool")
        count = _nonnegative_int(
            self.occurrence_count,
            "failure.occurrence_count",
            maximum=20_000,
        )
        if count < 1:
            raise SkillCandidateContractError("failure.occurrence_count 必须大于 0")
        object.__setattr__(
            self,
            "supporting_run_ids",
            _run_id_tuple(self.supporting_run_ids, "failure.supporting_run_ids"),
        )
        digest = sha256_json(self._payload())
        declared = _sha256(
            self.pattern_sha256,
            "failure.pattern_sha256",
            allow_empty=True,
        )
        if declared and declared != digest:
            raise SkillCandidateContractError("failure.pattern_sha256 与内容不匹配")
        object.__setattr__(self, "pattern_sha256", digest)

    def _payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "code": self.code,
            "error_type": self.error_type,
            "retryable": self.retryable,
            "occurrence_count": self.occurrence_count,
            "supporting_run_ids": list(self.supporting_run_ids),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "pattern_sha256": self.pattern_sha256}

    @classmethod
    def from_dict(cls, value: object) -> "ExperienceFailurePattern":
        payload = _mapping(value, "failure pattern")
        _exact_keys(
            payload,
            name="failure pattern",
            required=frozenset({
                "kind",
                "name",
                "code",
                "error_type",
                "retryable",
                "occurrence_count",
                "supporting_run_ids",
                "pattern_sha256",
            }),
        )
        return cls(
            kind=payload["kind"],
            name=payload["name"],
            code=payload["code"],
            error_type=payload["error_type"],
            retryable=payload["retryable"],
            occurrence_count=payload["occurrence_count"],
            supporting_run_ids=tuple(
                _sequence(payload["supporting_run_ids"], "failure.supporting_run_ids")
            ),
            pattern_sha256=payload["pattern_sha256"],
        )


@dataclass(frozen=True, slots=True)
class SkillExperienceCandidate:
    schema_version: int
    candidate_id: str
    created_at: str
    generator_id: str
    generator_version: str
    source_revision: str
    target_scope: str
    target_scope_key: str
    baseline_bundle_sha256: str
    draft_skill_md: str
    source_runs: tuple[SourceRunEvidence, ...]
    process_steps: tuple[ExperienceProcessStep, ...]
    failure_patterns: tuple[ExperienceFailurePattern, ...]
    raw_production_content_access: bool
    network_access: bool
    repository_operations: str
    generation_cost_microunits: int
    redaction_count: int
    dedup_sha256: str = ""
    candidate_sha256: str = ""

    def __post_init__(self) -> None:
        _schema(self.schema_version, "candidate.schema_version")
        object.__setattr__(
            self,
            "candidate_id",
            _identifier(self.candidate_id, "candidate.candidate_id"),
        )
        object.__setattr__(
            self,
            "created_at",
            _timestamp(self.created_at, "candidate.created_at"),
        )
        object.__setattr__(
            self,
            "generator_id",
            _identifier(self.generator_id, "candidate.generator_id"),
        )
        object.__setattr__(
            self,
            "generator_version",
            _text(
                self.generator_version,
                "candidate.generator_version",
                maximum=128,
            ),
        )
        object.__setattr__(
            self,
            "source_revision",
            _revision(self.source_revision, "candidate.source_revision"),
        )
        try:
            target = SkillScopeTarget(self.target_scope, self.target_scope_key)
        except (SkillContractError, ValueError) as exc:
            raise SkillCandidateContractError("candidate.target 无效") from exc
        if target.scope.value == "builtin":
            raise SkillCandidateContractError("经验候选不能发布到 builtin scope")
        object.__setattr__(self, "target_scope", target.scope.value)
        object.__setattr__(self, "target_scope_key", target.scope_key)
        object.__setattr__(
            self,
            "baseline_bundle_sha256",
            _sha256(
                self.baseline_bundle_sha256,
                "candidate.baseline_bundle_sha256",
            ),
        )
        skill_md = _text(
            self.draft_skill_md,
            "candidate.draft_skill_md",
            maximum=512 * 1024,
        )
        try:
            bundle = parse_skill_bundle(skill_md.encode("utf-8"))
        except (SkillContractError, UnicodeEncodeError) as exc:
            raise SkillCandidateContractError("candidate Skill 草案无效") from exc
        object.__setattr__(self, "draft_skill_md", skill_md)
        sources = tuple(sorted(self.source_runs, key=lambda item: item.run_id))
        if any(not isinstance(item, SourceRunEvidence) for item in sources):
            raise SkillCandidateContractError("candidate.source_runs 包含无效项")
        if not 2 <= len(sources) <= MAX_SOURCE_RUNS:
            raise SkillCandidateContractError(
                f"candidate.source_runs 必须包含 2..{MAX_SOURCE_RUNS} 个 Run"
            )
        if len({item.run_id for item in sources}) != len(sources):
            raise SkillCandidateContractError("candidate.source_runs 不能重复")
        if {item.outcome for item in sources} != {"succeeded", "failed"}:
            raise SkillCandidateContractError(
                "候选必须同时基于成功和失败 trajectory"
            )
        if any(
            item.outcome == "failed" and item.failure_count < 1
            for item in sources
        ):
            raise SkillCandidateContractError(
                "失败来源 Run 必须保留至少一个失败证据"
            )
        object.__setattr__(self, "source_runs", sources)
        source_run_ids = {item.run_id for item in sources}
        successful_run_ids = {
            item.run_id for item in sources if item.outcome == "succeeded"
        }
        steps = tuple(self.process_steps)
        if not steps or len(steps) > MAX_PATTERNS:
            raise SkillCandidateContractError(
                f"candidate.process_steps 必须包含 1..{MAX_PATTERNS} 项"
            )
        if any(not isinstance(item, ExperienceProcessStep) for item in steps):
            raise SkillCandidateContractError("candidate.process_steps 包含无效项")
        if tuple(item.position for item in steps) != tuple(range(1, len(steps) + 1)):
            raise SkillCandidateContractError("candidate.process_steps position 必须连续")
        if any(
            not set(item.supporting_run_ids) <= successful_run_ids
            for item in steps
        ):
            raise SkillCandidateContractError(
                "流程步骤只能引用候选中的成功来源 Run"
            )
        object.__setattr__(self, "process_steps", steps)
        failures = tuple(sorted(self.failure_patterns, key=lambda item: item.pattern_sha256))
        if not failures or len(failures) > MAX_PATTERNS:
            raise SkillCandidateContractError(
                f"candidate.failure_patterns 必须包含 1..{MAX_PATTERNS} 项"
            )
        if any(not isinstance(item, ExperienceFailurePattern) for item in failures):
            raise SkillCandidateContractError("candidate.failure_patterns 包含无效项")
        if len({item.pattern_sha256 for item in failures}) != len(failures):
            raise SkillCandidateContractError("candidate.failure_patterns 不能重复")
        if any(
            not set(item.supporting_run_ids) <= source_run_ids
            for item in failures
        ):
            raise SkillCandidateContractError(
                "失败模式只能引用候选中的来源 Run"
            )
        object.__setattr__(self, "failure_patterns", failures)
        for name in ("raw_production_content_access", "network_access"):
            if type(getattr(self, name)) is not bool or getattr(self, name):
                raise SkillCandidateContractError(
                    "候选生成禁止读取原始生产正文或访问网络"
                )
        if self.repository_operations != "forbidden":
            raise SkillCandidateContractError(
                "候选生成禁止修改、提交、tag 或 push 仓库"
            )
        for name, maximum in (
            ("generation_cost_microunits", 10**12),
            ("redaction_count", 1_000_000),
        ):
            object.__setattr__(
                self,
                name,
                _nonnegative_int(
                    getattr(self, name),
                    f"candidate.{name}",
                    maximum=maximum,
                ),
            )
        if self.redaction_count != sum(item.redaction_count for item in sources):
            raise SkillCandidateContractError(
                "candidate.redaction_count 与来源证据不一致"
            )
        if bundle.dependencies:
            raise SkillCandidateContractError(
                "经验候选不能自动引入 Skill 依赖"
            )
        expected_permissions = tuple(
            sorted(f"tool:{item}" for item in bundle.allowed_tools)
        )
        if bundle.required_permissions != expected_permissions:
            raise SkillCandidateContractError(
                "经验候选权限必须且只能对应已声明工具"
            )
        observed_tools = {
            item.name for item in steps if item.kind == "tool"
        } | {
            item.name for item in failures if item.kind == "tool"
        }
        if not set(bundle.allowed_tools) <= observed_tools:
            raise SkillCandidateContractError(
                "经验候选不能申请来源 trajectory 未观察到的工具"
            )
        corpus_sha256 = sha256_json({
            "source_trajectory_sha256s": sorted(
                item.trajectory_sha256 for item in sources
            ),
            "process_pattern_sha256s": sorted(
                item.pattern_sha256 for item in steps
            ),
            "failure_pattern_sha256s": sorted(
                item.pattern_sha256 for item in failures
            ),
        })
        if bundle.metadata.get("nanobot.experience-corpus-sha256") != corpus_sha256:
            raise SkillCandidateContractError(
                "Skill 草案未绑定当前来源 trajectory 语料摘要"
            )
        dedup_payload = {
            "target_scope": self.target_scope,
            "target_scope_key": self.target_scope_key,
            "baseline_bundle_sha256": self.baseline_bundle_sha256,
            "bundle_sha256": bundle.bundle_sha256,
            "source_trajectory_sha256s": sorted(
                item.trajectory_sha256 for item in sources
            ),
            "pattern_sha256s": sorted(
                [item.pattern_sha256 for item in steps]
                + [item.pattern_sha256 for item in failures]
            ),
        }
        dedup = sha256_json(dedup_payload)
        declared_dedup = _sha256(
            self.dedup_sha256,
            "candidate.dedup_sha256",
            allow_empty=True,
        )
        if declared_dedup and declared_dedup != dedup:
            raise SkillCandidateContractError("candidate.dedup_sha256 与内容不匹配")
        object.__setattr__(self, "dedup_sha256", dedup)
        digest = sha256_json(self._payload())
        declared = _sha256(
            self.candidate_sha256,
            "candidate.candidate_sha256",
            allow_empty=True,
        )
        if declared and declared != digest:
            raise SkillCandidateContractError("candidate.candidate_sha256 与内容不匹配")
        object.__setattr__(self, "candidate_sha256", digest)

    @property
    def parsed_bundle(self):
        return parse_skill_bundle(self.draft_skill_md.encode("utf-8"))

    @property
    def target(self) -> SkillScopeTarget:
        return SkillScopeTarget(self.target_scope, self.target_scope_key)

    @property
    def source_trajectory_sha256s(self) -> tuple[str, ...]:
        return tuple(sorted(item.trajectory_sha256 for item in self.source_runs))

    @property
    def pattern_sha256s(self) -> tuple[str, ...]:
        return tuple(sorted(
            [item.pattern_sha256 for item in self.process_steps]
            + [item.pattern_sha256 for item in self.failure_patterns]
        ))

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "created_at": self.created_at,
            "generator_id": self.generator_id,
            "generator_version": self.generator_version,
            "source_revision": self.source_revision,
            "target_scope": self.target_scope,
            "target_scope_key": self.target_scope_key,
            "baseline_bundle_sha256": self.baseline_bundle_sha256,
            "draft_skill_md": self.draft_skill_md,
            "source_runs": [item.to_dict() for item in self.source_runs],
            "process_steps": [item.to_dict() for item in self.process_steps],
            "failure_patterns": [item.to_dict() for item in self.failure_patterns],
            "raw_production_content_access": self.raw_production_content_access,
            "network_access": self.network_access,
            "repository_operations": self.repository_operations,
            "generation_cost_microunits": self.generation_cost_microunits,
            "redaction_count": self.redaction_count,
            "dedup_sha256": self.dedup_sha256,
        }

    def to_dict(self) -> dict[str, object]:
        bundle = self.parsed_bundle
        return {
            **self._payload(),
            "candidate_sha256": self.candidate_sha256,
            "skill_name": bundle.name,
            "version": bundle.version,
            "skill_md_sha256": bundle.skill_md_sha256,
            "bundle_sha256": bundle.bundle_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SkillExperienceCandidate":
        payload = _mapping(value, "skill candidate")
        required = frozenset({
            "schema_version",
            "candidate_id",
            "created_at",
            "generator_id",
            "generator_version",
            "source_revision",
            "target_scope",
            "target_scope_key",
            "baseline_bundle_sha256",
            "draft_skill_md",
            "source_runs",
            "process_steps",
            "failure_patterns",
            "raw_production_content_access",
            "network_access",
            "repository_operations",
            "generation_cost_microunits",
            "redaction_count",
            "dedup_sha256",
            "candidate_sha256",
            "skill_name",
            "version",
            "skill_md_sha256",
            "bundle_sha256",
        })
        _exact_keys(payload, name="skill candidate", required=required)
        candidate = cls(
            schema_version=payload["schema_version"],
            candidate_id=payload["candidate_id"],
            created_at=payload["created_at"],
            generator_id=payload["generator_id"],
            generator_version=payload["generator_version"],
            source_revision=payload["source_revision"],
            target_scope=payload["target_scope"],
            target_scope_key=payload["target_scope_key"],
            baseline_bundle_sha256=payload["baseline_bundle_sha256"],
            draft_skill_md=payload["draft_skill_md"],
            source_runs=tuple(
                SourceRunEvidence.from_dict(item)
                for item in _sequence(payload["source_runs"], "candidate.source_runs")
            ),
            process_steps=tuple(
                ExperienceProcessStep.from_dict(item)
                for item in _sequence(payload["process_steps"], "candidate.process_steps")
            ),
            failure_patterns=tuple(
                ExperienceFailurePattern.from_dict(item)
                for item in _sequence(
                    payload["failure_patterns"],
                    "candidate.failure_patterns",
                )
            ),
            raw_production_content_access=payload["raw_production_content_access"],
            network_access=payload["network_access"],
            repository_operations=payload["repository_operations"],
            generation_cost_microunits=payload["generation_cost_microunits"],
            redaction_count=payload["redaction_count"],
            dedup_sha256=payload["dedup_sha256"],
            candidate_sha256=payload["candidate_sha256"],
        )
        bundle = candidate.parsed_bundle
        declared_projection = {
            "skill_name": payload["skill_name"],
            "version": payload["version"],
            "skill_md_sha256": payload["skill_md_sha256"],
            "bundle_sha256": payload["bundle_sha256"],
        }
        actual_projection = {
            "skill_name": bundle.name,
            "version": bundle.version,
            "skill_md_sha256": bundle.skill_md_sha256,
            "bundle_sha256": bundle.bundle_sha256,
        }
        if declared_projection != actual_projection:
            raise SkillCandidateContractError("candidate Skill 投影与正文不一致")
        return candidate


@dataclass(frozen=True, slots=True)
class SkillCandidateEvaluationEvidence:
    schema_version: int
    candidate_sha256: str
    candidate_bundle_sha256: str
    baseline_bundle_sha256: str
    source_revision: str
    harness_registry_sha256: str
    dataset_sha256: str
    suite_id: str
    evaluator_id: str
    evaluator_version: str
    started_at: str
    finished_at: str
    safety_passed: bool
    safety_failure_count: int
    baseline_score_micros: int
    candidate_score_micros: int
    baseline_cost_microunits: int
    candidate_cost_microunits: int
    approved_cost_microunits: int
    source_trajectory_sha256s: tuple[str, ...]
    validated_pattern_sha256s: tuple[str, ...]
    artifact_sha256s: tuple[str, ...]
    evidence_sha256: str
    evaluation_sha256: str = ""

    def __post_init__(self) -> None:
        _schema(self.schema_version, "evaluation.schema_version")
        for name in (
            "candidate_sha256",
            "candidate_bundle_sha256",
            "baseline_bundle_sha256",
            "harness_registry_sha256",
            "dataset_sha256",
            "evidence_sha256",
        ):
            object.__setattr__(
                self,
                name,
                _sha256(getattr(self, name), f"evaluation.{name}"),
            )
        object.__setattr__(
            self,
            "source_revision",
            _revision(self.source_revision, "evaluation.source_revision"),
        )
        for name in ("suite_id", "evaluator_id"):
            object.__setattr__(
                self,
                name,
                _identifier(getattr(self, name), f"evaluation.{name}"),
            )
        object.__setattr__(
            self,
            "evaluator_version",
            _text(
                self.evaluator_version,
                "evaluation.evaluator_version",
                maximum=128,
            ),
        )
        started = _timestamp(self.started_at, "evaluation.started_at")
        finished = _timestamp(self.finished_at, "evaluation.finished_at")
        if datetime.fromisoformat(finished) < datetime.fromisoformat(started):
            raise SkillCandidateContractError(
                "evaluation.finished_at 不能早于 started_at"
            )
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "finished_at", finished)
        if type(self.safety_passed) is not bool:
            raise SkillCandidateContractError("evaluation.safety_passed 必须是 bool")
        for name, maximum in (
            ("safety_failure_count", 1_000_000),
            ("baseline_score_micros", 1_000_000),
            ("candidate_score_micros", 1_000_000),
            ("baseline_cost_microunits", 10**12),
            ("candidate_cost_microunits", 10**12),
            ("approved_cost_microunits", 10**12),
        ):
            object.__setattr__(
                self,
                name,
                _nonnegative_int(
                    getattr(self, name),
                    f"evaluation.{name}",
                    maximum=maximum,
                ),
            )
        object.__setattr__(
            self,
            "source_trajectory_sha256s",
            _sha_tuple(
                self.source_trajectory_sha256s,
                "evaluation.source_trajectory_sha256s",
                maximum=MAX_SOURCE_RUNS,
            ),
        )
        object.__setattr__(
            self,
            "validated_pattern_sha256s",
            _sha_tuple(
                self.validated_pattern_sha256s,
                "evaluation.validated_pattern_sha256s",
                maximum=MAX_PATTERNS * 2,
            ),
        )
        artifacts = _sha_tuple(
            self.artifact_sha256s,
            "evaluation.artifact_sha256s",
            maximum=128,
        )
        if self.evidence_sha256 not in artifacts:
            raise SkillCandidateContractError(
                "evaluation.artifact_sha256s 必须包含 evidence_sha256"
            )
        object.__setattr__(self, "artifact_sha256s", artifacts)
        digest = sha256_json(self._payload())
        declared = _sha256(
            self.evaluation_sha256,
            "evaluation.evaluation_sha256",
            allow_empty=True,
        )
        if declared and declared != digest:
            raise SkillCandidateContractError(
                "evaluation.evaluation_sha256 与内容不匹配"
            )
        object.__setattr__(self, "evaluation_sha256", digest)

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate_sha256": self.candidate_sha256,
            "candidate_bundle_sha256": self.candidate_bundle_sha256,
            "baseline_bundle_sha256": self.baseline_bundle_sha256,
            "source_revision": self.source_revision,
            "harness_registry_sha256": self.harness_registry_sha256,
            "dataset_sha256": self.dataset_sha256,
            "suite_id": self.suite_id,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "safety_passed": self.safety_passed,
            "safety_failure_count": self.safety_failure_count,
            "baseline_score_micros": self.baseline_score_micros,
            "candidate_score_micros": self.candidate_score_micros,
            "baseline_cost_microunits": self.baseline_cost_microunits,
            "candidate_cost_microunits": self.candidate_cost_microunits,
            "approved_cost_microunits": self.approved_cost_microunits,
            "source_trajectory_sha256s": list(self.source_trajectory_sha256s),
            "validated_pattern_sha256s": list(self.validated_pattern_sha256s),
            "artifact_sha256s": list(self.artifact_sha256s),
            "evidence_sha256": self.evidence_sha256,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._payload(), "evaluation_sha256": self.evaluation_sha256}

    @classmethod
    def from_dict(cls, value: object) -> "SkillCandidateEvaluationEvidence":
        payload = _mapping(value, "skill candidate evaluation")
        required = frozenset({
            "schema_version",
            "candidate_sha256",
            "candidate_bundle_sha256",
            "baseline_bundle_sha256",
            "source_revision",
            "harness_registry_sha256",
            "dataset_sha256",
            "suite_id",
            "evaluator_id",
            "evaluator_version",
            "started_at",
            "finished_at",
            "safety_passed",
            "safety_failure_count",
            "baseline_score_micros",
            "candidate_score_micros",
            "baseline_cost_microunits",
            "candidate_cost_microunits",
            "approved_cost_microunits",
            "source_trajectory_sha256s",
            "validated_pattern_sha256s",
            "artifact_sha256s",
            "evidence_sha256",
            "evaluation_sha256",
        })
        _exact_keys(
            payload,
            name="skill candidate evaluation",
            required=required,
        )
        return cls(
            schema_version=payload["schema_version"],
            candidate_sha256=payload["candidate_sha256"],
            candidate_bundle_sha256=payload["candidate_bundle_sha256"],
            baseline_bundle_sha256=payload["baseline_bundle_sha256"],
            source_revision=payload["source_revision"],
            harness_registry_sha256=payload["harness_registry_sha256"],
            dataset_sha256=payload["dataset_sha256"],
            suite_id=payload["suite_id"],
            evaluator_id=payload["evaluator_id"],
            evaluator_version=payload["evaluator_version"],
            started_at=payload["started_at"],
            finished_at=payload["finished_at"],
            safety_passed=payload["safety_passed"],
            safety_failure_count=payload["safety_failure_count"],
            baseline_score_micros=payload["baseline_score_micros"],
            candidate_score_micros=payload["candidate_score_micros"],
            baseline_cost_microunits=payload["baseline_cost_microunits"],
            candidate_cost_microunits=payload["candidate_cost_microunits"],
            approved_cost_microunits=payload["approved_cost_microunits"],
            source_trajectory_sha256s=tuple(
                _sequence(
                    payload["source_trajectory_sha256s"],
                    "evaluation.source_trajectory_sha256s",
                )
            ),
            validated_pattern_sha256s=tuple(
                _sequence(
                    payload["validated_pattern_sha256s"],
                    "evaluation.validated_pattern_sha256s",
                )
            ),
            artifact_sha256s=tuple(
                _sequence(
                    payload["artifact_sha256s"],
                    "evaluation.artifact_sha256s",
                )
            ),
            evidence_sha256=payload["evidence_sha256"],
            evaluation_sha256=payload["evaluation_sha256"],
        )


def skill_candidate_catalog_payload() -> dict[str, object]:
    return {
        "schema_version": SKILL_CANDIDATE_SCHEMA_VERSION,
        "source": "offline_redacted_run_view",
        "required_outcomes": ["succeeded", "failed"],
        "candidate_area": "isolated_content_addressed_store",
        "publication": {
            "evaluation": "independent_blocking_gate",
            "approval": "human_exact_hash_single_use_token",
            "existing_skill": "stage_version_without_activation",
            "new_skill": "install_to_explicit_user_scope_after_gate_and_human_approval",
        },
        "blocked_inputs": [
            "prompt_body",
            "message_body",
            "tool_arguments",
            "tool_results",
            "sandbox_command",
            "sandbox_output",
            "hidden_reasoning",
            "credentials",
        ],
        "blocked_actions": [
            "network_generation",
            "raw_production_content_access",
            "repository_write",
            "self_evaluation",
            "self_approval",
            "automatic_existing_skill_activation",
            "broad_scope_new_skill_activation",
        ],
        "limits": {
            "max_source_runs": MAX_SOURCE_RUNS,
            "max_process_steps": MAX_PATTERNS,
            "max_failure_patterns": MAX_PATTERNS,
            "max_cost_increase_basis_points": MAX_COST_INCREASE_BASIS_POINTS,
        },
        "no_skill_baseline_sha256": NO_SKILL_BASELINE_SHA256,
    }


__all__ = [
    "MAX_COST_INCREASE_BASIS_POINTS",
    "MAX_PATTERNS",
    "MAX_SOURCE_RUNS",
    "NO_SKILL_BASELINE_SHA256",
    "SKILL_CANDIDATE_SCHEMA_VERSION",
    "ExperienceFailurePattern",
    "ExperienceProcessStep",
    "SkillCandidateContractError",
    "SkillCandidateEvaluationEvidence",
    "SkillExperienceCandidate",
    "SourceRunEvidence",
    "canonical_json",
    "skill_candidate_catalog_payload",
]
