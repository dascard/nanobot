"""受控自进化的安全、成本和质量门禁。"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    EVOLUTION_SCHEMA_VERSION,
    EvolutionCandidateBundle,
    EvolutionContractError,
    FrozenDatasetManifest,
    _aware_timestamp,
    _exact_keys,
    _identifier,
    _mapping,
    _nonnegative_int,
    _schema_version,
    _sequence,
    _sha256,
    _source_revision,
    sha256_json,
)


MIN_VALIDATION_IMPROVEMENT_MICROS = 1
MAX_COST_INCREASE_BASIS_POINTS = 2_000


@dataclass(frozen=True, slots=True)
class EvolutionSplitResult:
    role: str
    dataset_artifact_sha256: str
    expected_total: int
    correct: int
    incorrect: int
    infrastructure_failure: int
    timeout: int
    explicitly_excluded: int
    missing: int
    baseline_score_micros: int
    candidate_score_micros: int
    domain_regression_count: int

    def __post_init__(self) -> None:
        if self.role not in {"validation", "test"}:
            raise EvolutionContractError(
                "最终门禁只允许 validation/test held-out split"
            )
        object.__setattr__(
            self,
            "dataset_artifact_sha256",
            _sha256(
                self.dataset_artifact_sha256,
                f"split_results.{self.role}.dataset_artifact_sha256",
            ),
        )
        for name, maximum in (
            ("expected_total", 10_000_000),
            ("correct", 10_000_000),
            ("incorrect", 10_000_000),
            ("infrastructure_failure", 10_000_000),
            ("timeout", 10_000_000),
            ("explicitly_excluded", 10_000_000),
            ("missing", 10_000_000),
            ("baseline_score_micros", 1_000_000),
            ("candidate_score_micros", 1_000_000),
            ("domain_regression_count", 10_000_000),
        ):
            object.__setattr__(
                self,
                name,
                _nonnegative_int(
                    getattr(self, name),
                    f"split_results.{self.role}.{name}",
                    maximum=maximum,
                ),
            )
        if self.expected_total < 1:
            raise EvolutionContractError(
                f"split_results.{self.role}.expected_total 必须大于 0"
            )
        denominator = (
            self.correct
            + self.incorrect
            + self.infrastructure_failure
            + self.timeout
            + self.explicitly_excluded
        )
        if self.expected_total != denominator or self.missing != 0:
            raise EvolutionContractError(
                f"split_results.{self.role} 分母不闭合或 missing 非零"
            )

    @property
    def score_delta_micros(self) -> int:
        return self.candidate_score_micros - self.baseline_score_micros

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "dataset_artifact_sha256": self.dataset_artifact_sha256,
            "expected_total": self.expected_total,
            "correct": self.correct,
            "incorrect": self.incorrect,
            "infrastructure_failure": self.infrastructure_failure,
            "timeout": self.timeout,
            "explicitly_excluded": self.explicitly_excluded,
            "missing": self.missing,
            "baseline_score_micros": self.baseline_score_micros,
            "candidate_score_micros": self.candidate_score_micros,
            "domain_regression_count": self.domain_regression_count,
        }

    @classmethod
    def from_dict(cls, value: object) -> "EvolutionSplitResult":
        payload = _mapping(value, "split result")
        _exact_keys(
            payload,
            name="split result",
            required=frozenset({
                "role",
                "dataset_artifact_sha256",
                "expected_total",
                "correct",
                "incorrect",
                "infrastructure_failure",
                "timeout",
                "explicitly_excluded",
                "missing",
                "baseline_score_micros",
                "candidate_score_micros",
                "domain_regression_count",
            }),
        )
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class EvolutionGateEvidence:
    schema_version: int
    candidate_sha256: str
    dataset_sha256: str
    source_revision: str
    harness_registry_sha256: str
    offline_gate_report_sha256: str
    offline_gate_passed: bool
    safety_suite_id: str
    safety_passed: bool
    safety_failures: int
    safety_artifact_sha256: str
    evaluator_id: str
    evaluator_version: str
    started_at: str
    finished_at: str
    split_results: tuple[EvolutionSplitResult, ...]
    approved_cost_microunits: int
    baseline_cost_microunits: int
    candidate_cost_microunits: int
    artifact_sha256s: tuple[str, ...]

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, "gate.schema_version")
        for name in (
            "candidate_sha256",
            "dataset_sha256",
            "harness_registry_sha256",
            "offline_gate_report_sha256",
            "safety_artifact_sha256",
        ):
            object.__setattr__(
                self,
                name,
                _sha256(getattr(self, name), f"gate.{name}"),
            )
        object.__setattr__(
            self,
            "source_revision",
            _source_revision(self.source_revision, "gate.source_revision"),
        )
        for name in ("offline_gate_passed", "safety_passed"):
            if type(getattr(self, name)) is not bool:
                raise EvolutionContractError(f"gate.{name} 必须是 bool")
        object.__setattr__(
            self,
            "safety_suite_id",
            _identifier(self.safety_suite_id, "gate.safety_suite_id"),
        )
        object.__setattr__(
            self,
            "safety_failures",
            _nonnegative_int(
                self.safety_failures,
                "gate.safety_failures",
                maximum=10_000_000,
            ),
        )
        object.__setattr__(
            self,
            "evaluator_id",
            _identifier(self.evaluator_id, "gate.evaluator_id"),
        )
        object.__setattr__(
            self,
            "evaluator_version",
            str(self.evaluator_version or "").strip(),
        )
        if not self.evaluator_version or len(self.evaluator_version) > 128:
            raise EvolutionContractError("gate.evaluator_version 无效")
        started = _aware_timestamp(self.started_at, "gate.started_at")
        finished = _aware_timestamp(self.finished_at, "gate.finished_at")
        if finished < started:
            raise EvolutionContractError("gate.finished_at 不能早于 started_at")
        object.__setattr__(self, "started_at", started.isoformat())
        object.__setattr__(self, "finished_at", finished.isoformat())
        split_results = tuple(
            sorted(self.split_results, key=lambda item: item.role)
        )
        if any(not isinstance(item, EvolutionSplitResult) for item in split_results):
            raise EvolutionContractError("gate.split_results 包含无效项")
        if tuple(item.role for item in split_results) != ("test", "validation"):
            raise EvolutionContractError(
                "gate.split_results 必须且只能包含 validation/test"
            )
        object.__setattr__(self, "split_results", split_results)
        for name in (
            "approved_cost_microunits",
            "baseline_cost_microunits",
            "candidate_cost_microunits",
        ):
            object.__setattr__(
                self,
                name,
                _nonnegative_int(
                    getattr(self, name),
                    f"gate.{name}",
                    maximum=10**15,
                ),
            )
        artifacts = tuple(
            sorted(
                _sha256(item, "gate.artifact_sha256")
                for item in self.artifact_sha256s
            )
        )
        if not artifacts or len(artifacts) > 128 or len(artifacts) != len(set(artifacts)):
            raise EvolutionContractError(
                "gate.artifact_sha256s 必须非空、唯一且不超过 128 项"
            )
        object.__setattr__(self, "artifact_sha256s", artifacts)

    def split(self, role: str) -> EvolutionSplitResult:
        return next(item for item in self.split_results if item.role == role)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate_sha256": self.candidate_sha256,
            "dataset_sha256": self.dataset_sha256,
            "source_revision": self.source_revision,
            "harness_registry_sha256": self.harness_registry_sha256,
            "offline_gate_report_sha256": self.offline_gate_report_sha256,
            "offline_gate_passed": self.offline_gate_passed,
            "safety_suite_id": self.safety_suite_id,
            "safety_passed": self.safety_passed,
            "safety_failures": self.safety_failures,
            "safety_artifact_sha256": self.safety_artifact_sha256,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "split_results": [item.to_dict() for item in self.split_results],
            "approved_cost_microunits": self.approved_cost_microunits,
            "baseline_cost_microunits": self.baseline_cost_microunits,
            "candidate_cost_microunits": self.candidate_cost_microunits,
            "artifact_sha256s": list(self.artifact_sha256s),
        }

    @classmethod
    def from_dict(cls, value: object) -> "EvolutionGateEvidence":
        payload = _mapping(value, "gate evidence")
        _exact_keys(
            payload,
            name="gate evidence",
            required=frozenset({
                "schema_version",
                "candidate_sha256",
                "dataset_sha256",
                "source_revision",
                "harness_registry_sha256",
                "offline_gate_report_sha256",
                "offline_gate_passed",
                "safety_suite_id",
                "safety_passed",
                "safety_failures",
                "safety_artifact_sha256",
                "evaluator_id",
                "evaluator_version",
                "started_at",
                "finished_at",
                "split_results",
                "approved_cost_microunits",
                "baseline_cost_microunits",
                "candidate_cost_microunits",
                "artifact_sha256s",
            }),
        )
        return cls(
            **{
                key: value
                for key, value in payload.items()
                if key != "split_results"
            },
            split_results=tuple(
                EvolutionSplitResult.from_dict(item)
                for item in _sequence(
                    payload["split_results"],
                    "gate.split_results",
                )
            ),
        )


def _gate_error(code: str, message: str, **details: object) -> dict[str, object]:
    return {"code": code, "message": message, **details}


def evaluate_evolution_candidate(
    *,
    candidate: EvolutionCandidateBundle,
    dataset: FrozenDatasetManifest,
    evidence: EvolutionGateEvidence,
    current_harness_registry_sha256: str,
) -> dict[str, object]:
    """独立验证冻结数据、离线安全门禁、质量提升和成本上限。"""

    if not isinstance(candidate, EvolutionCandidateBundle):
        raise EvolutionContractError("candidate 无效")
    if not isinstance(dataset, FrozenDatasetManifest):
        raise EvolutionContractError("dataset 无效")
    if not isinstance(evidence, EvolutionGateEvidence):
        raise EvolutionContractError("evidence 无效")
    registry_sha = _sha256(
        current_harness_registry_sha256,
        "current_harness_registry_sha256",
    )
    errors: list[dict[str, object]] = []
    if candidate.dataset_sha256 != dataset.dataset_sha256:
        errors.append(_gate_error("candidate_dataset_mismatch", "候选未绑定当前冻结数据集"))
    if evidence.candidate_sha256 != candidate.candidate_sha256:
        errors.append(_gate_error("candidate_sha256_mismatch", "评测证据未绑定当前候选"))
    if evidence.dataset_sha256 != dataset.dataset_sha256:
        errors.append(_gate_error("dataset_sha256_mismatch", "评测证据未绑定当前冻结数据集"))
    if evidence.source_revision != candidate.generation.source_revision:
        errors.append(_gate_error("source_revision_mismatch", "候选与评测代码 revision 不一致"))
    if evidence.source_revision != dataset.source_revision:
        errors.append(_gate_error(
            "dataset_source_revision_mismatch",
            "冻结数据集与评测代码 revision 不一致",
        ))
    if evidence.harness_registry_sha256 != registry_sha:
        errors.append(_gate_error("harness_registry_mismatch", "离线门禁 Registry 已漂移"))
    if evidence.evaluator_id == candidate.generation.generator_id:
        errors.append(_gate_error(
            "evaluator_not_independent",
            "候选生成器不能同时充当独立评测器",
        ))
    if not evidence.offline_gate_passed:
        errors.append(_gate_error("offline_gate_failed", "离线确定性门禁未通过"))
    if not evidence.safety_passed or evidence.safety_failures != 0:
        errors.append(_gate_error("safety_gate_failed", "安全门禁未通过"))
    required_artifacts = {
        evidence.offline_gate_report_sha256,
        evidence.safety_artifact_sha256,
    }
    missing_artifacts = sorted(
        required_artifacts - set(evidence.artifact_sha256s)
    )
    if missing_artifacts:
        errors.append(_gate_error(
            "required_artifact_missing",
            "门禁证据未包含离线或安全报告 Artifact",
            missing_sha256s=missing_artifacts,
        ))

    split_metrics: dict[str, object] = {}
    for role in ("validation", "test"):
        result = evidence.split(role)
        frozen = dataset.split(role)
        if result.dataset_artifact_sha256 != frozen.artifact_sha256:
            errors.append(_gate_error(
                "split_artifact_mismatch",
                "held-out split 摘要不匹配",
                role=role,
            ))
        if result.expected_total != frozen.expected_count:
            errors.append(_gate_error(
                "split_denominator_mismatch",
                "held-out split 分母与冻结 manifest 不一致",
                role=role,
            ))
        if result.infrastructure_failure or result.timeout:
            errors.append(_gate_error(
                "infrastructure_result_present",
                "最终门禁不能包含基础设施失败或超时",
                role=role,
            ))
        if result.explicitly_excluded:
            errors.append(_gate_error(
                "excluded_case_present",
                "最终门禁不接受运行时排除样本",
                role=role,
            ))
        if result.domain_regression_count:
            errors.append(_gate_error(
                "domain_regression",
                "关键领域出现负迁移",
                role=role,
                count=result.domain_regression_count,
            ))
        if role == "validation" and (
            result.score_delta_micros < MIN_VALIDATION_IMPROVEMENT_MICROS
        ):
            errors.append(_gate_error(
                "validation_not_improved",
                "validation 未达到严格提升",
                score_delta_micros=result.score_delta_micros,
            ))
        if role == "test" and result.score_delta_micros < 0:
            errors.append(_gate_error(
                "test_regression",
                "test held-out 集发生回退",
                score_delta_micros=result.score_delta_micros,
            ))
        split_metrics[role] = {
            "expected_total": result.expected_total,
            "baseline_score_micros": result.baseline_score_micros,
            "candidate_score_micros": result.candidate_score_micros,
            "score_delta_micros": result.score_delta_micros,
        }

    if evidence.approved_cost_microunits <= 0:
        errors.append(_gate_error("cost_budget_missing", "缺少明确成本预算"))
    if evidence.candidate_cost_microunits > evidence.approved_cost_microunits:
        errors.append(_gate_error("cost_budget_exceeded", "候选评测成本超过批准预算"))
    if evidence.baseline_cost_microunits == 0:
        if evidence.candidate_cost_microunits != 0:
            errors.append(_gate_error(
                "cost_regression",
                "零成本 baseline 不允许候选增加成本",
            ))
    else:
        maximum_cost = (
            evidence.baseline_cost_microunits
            * (10_000 + MAX_COST_INCREASE_BASIS_POINTS)
            // 10_000
        )
        if evidence.candidate_cost_microunits > maximum_cost:
            errors.append(_gate_error(
                "cost_regression",
                "候选成本增幅超过固定上限",
                maximum_cost_microunits=maximum_cost,
            ))

    evidence_payload = evidence.to_dict()
    content = {
        "schema_version": EVOLUTION_SCHEMA_VERSION,
        "candidate_sha256": candidate.candidate_sha256,
        "dataset_sha256": dataset.dataset_sha256,
        "target": {
            "kind": candidate.target.kind.value,
            "resource_id": candidate.target.resource_id,
        },
        "passed": not errors,
        "status": "passed" if not errors else "failed",
        "authority": "independent_evolution_gate",
        "blocking": True,
        "errors": errors,
        "metrics": {
            "splits": split_metrics,
            "approved_cost_microunits": evidence.approved_cost_microunits,
            "baseline_cost_microunits": evidence.baseline_cost_microunits,
            "candidate_cost_microunits": evidence.candidate_cost_microunits,
            "max_cost_increase_basis_points": MAX_COST_INCREASE_BASIS_POINTS,
        },
        "harness_registry_sha256": registry_sha,
        "offline_gate_report_sha256": evidence.offline_gate_report_sha256,
        "evaluator": {
            "id": evidence.evaluator_id,
            "version": evidence.evaluator_version,
        },
        "evidence_sha256": sha256_json(evidence_payload),
        "artifact_sha256s": list(evidence.artifact_sha256s),
    }
    return {**content, "gate_report_sha256": sha256_json(content)}


__all__ = [
    "MAX_COST_INCREASE_BASIS_POINTS",
    "MIN_VALIDATION_IMPROVEMENT_MICROS",
    "EvolutionGateEvidence",
    "EvolutionSplitResult",
    "evaluate_evolution_candidate",
]
