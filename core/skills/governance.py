"""Skill 版本级调用、成本与评测治理。"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
import uuid

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from core.db.models.skill import SkillEvaluationRow, SkillInvocationRow
from core.skills.contracts import RuntimeSkillLockEntry, SkillContractError


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _identifier(value: object, field: str, max_chars: int) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > max_chars
        or any(ord(char) < 32 for char in normalized)
    ):
        raise SkillContractError(f"{field} 无效")
    return normalized


def _optional_identifier(value: object, field: str, max_chars: int) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > max_chars or any(ord(char) < 32 for char in normalized):
        raise SkillContractError(f"{field} 无效")
    return normalized


def _nonnegative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise SkillContractError(f"{field} 必须是非负整数")
    return value


@dataclass(frozen=True, slots=True)
class SkillVersionGovernanceSnapshot:
    package_id: str
    skill_name: str
    version: str
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    prompt_tokens: int = 0
    resource_bytes: int = 0
    last_used_at: str = ""
    evaluation_count: int = 0
    latest_evaluation_passed: bool | None = None
    latest_score_micros: int | None = None
    evaluation_prompt_tokens: int = 0
    evaluation_cost_microunits: int = 0
    latest_evaluated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        success_rate = (
            round(self.success_count / self.call_count, 6)
            if self.call_count
            else None
        )
        return {
            "package_id": self.package_id,
            "skill_name": self.skill_name,
            "version": self.version,
            "call_count": self.call_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": success_rate,
            "prompt_tokens": self.prompt_tokens,
            "resource_bytes": self.resource_bytes,
            "cost_basis": "prompt_tokens_and_resource_bytes",
            "last_used_at": self.last_used_at,
            "evaluation_count": self.evaluation_count,
            "latest_evaluation_passed": self.latest_evaluation_passed,
            "latest_score": (
                self.latest_score_micros / 1_000_000
                if self.latest_score_micros is not None
                else None
            ),
            "evaluation_prompt_tokens": self.evaluation_prompt_tokens,
            "evaluation_cost_microunits": self.evaluation_cost_microunits,
            "latest_evaluated_at": self.latest_evaluated_at,
        }


class SkillGovernanceService:
    def __init__(self, db: Session) -> None:
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        self._db = db

    def record_invocation(
        self,
        entry: RuntimeSkillLockEntry,
        *,
        lock_sha256: str,
        status: str,
        result_kind: str,
        prompt_tokens: int,
        resource_bytes: int,
        latency_ms: int,
        error_code: str = "",
        run_id: str = "",
        trace_id: str = "",
    ) -> str:
        if status not in {"succeeded", "failed"}:
            raise SkillContractError("Skill invocation status 无效")
        if result_kind not in {"body", "resource"}:
            raise SkillContractError("Skill invocation result_kind 无效")
        digest = str(lock_sha256 or "").strip().lower()
        if not _SHA256_PATTERN.fullmatch(digest):
            raise SkillContractError("Skill invocation lock_sha256 无效")
        invocation_id = f"skillcall_{uuid.uuid4().hex}"
        self._db.add(
            SkillInvocationRow(
                invocation_id=invocation_id,
                package_id=entry.package_id,
                skill_name=entry.name,
                version=entry.version,
                scope=entry.scope.value,
                lock_sha256=digest,
                status=status,
                result_kind=result_kind,
                prompt_tokens=_nonnegative_int(prompt_tokens, "prompt_tokens"),
                resource_bytes=_nonnegative_int(resource_bytes, "resource_bytes"),
                latency_ms=_nonnegative_int(latency_ms, "latency_ms"),
                error_code=_optional_identifier(error_code, "error_code", 128),
                run_id=_optional_identifier(run_id, "run_id", 128),
                trace_id=_optional_identifier(trace_id, "trace_id", 128),
            )
        )
        self._db.flush()
        return invocation_id

    def record_evaluation(
        self,
        entry: RuntimeSkillLockEntry,
        *,
        suite_id: str,
        evaluator_id: str,
        evaluator_version: str,
        passed: bool,
        score: float,
        prompt_tokens: int,
        cost_microunits: int,
        evidence_sha256: str,
        actor_id: str,
    ) -> str:
        if type(passed) is not bool:
            raise SkillContractError("passed 必须是 bool")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise SkillContractError("score 必须是 0 到 1 的数字")
        normalized_score = float(score)
        if not math.isfinite(normalized_score) or not 0 <= normalized_score <= 1:
            raise SkillContractError("score 必须是 0 到 1 的数字")
        evidence = str(evidence_sha256 or "").strip().lower()
        if not _SHA256_PATTERN.fullmatch(evidence):
            raise SkillContractError("evidence_sha256 无效")
        evaluation_id = f"skilleval_{uuid.uuid4().hex}"
        self._db.add(
            SkillEvaluationRow(
                evaluation_id=evaluation_id,
                package_id=entry.package_id,
                skill_name=entry.name,
                version=entry.version,
                bundle_sha256=entry.bundle_sha256,
                suite_id=_identifier(suite_id, "suite_id", 128),
                evaluator_id=_identifier(evaluator_id, "evaluator_id", 128),
                evaluator_version=_identifier(
                    evaluator_version,
                    "evaluator_version",
                    64,
                ),
                passed=passed,
                score_micros=int(round(normalized_score * 1_000_000)),
                prompt_tokens=_nonnegative_int(prompt_tokens, "prompt_tokens"),
                cost_microunits=_nonnegative_int(
                    cost_microunits,
                    "cost_microunits",
                ),
                evidence_sha256=evidence,
                created_by=_identifier(actor_id, "actor_id", 255),
            )
        )
        self._db.flush()
        return evaluation_id

    def version_metrics(self) -> tuple[SkillVersionGovernanceSnapshot, ...]:
        usage_rows = self._db.execute(
            select(
                SkillInvocationRow.package_id,
                SkillInvocationRow.skill_name,
                SkillInvocationRow.version,
                func.count(SkillInvocationRow.invocation_id),
                func.sum(
                    case((SkillInvocationRow.status == "succeeded", 1), else_=0)
                ),
                func.sum(
                    case((SkillInvocationRow.status == "failed", 1), else_=0)
                ),
                func.sum(SkillInvocationRow.prompt_tokens),
                func.sum(SkillInvocationRow.resource_bytes),
                func.max(SkillInvocationRow.occurred_at),
            ).group_by(
                SkillInvocationRow.package_id,
                SkillInvocationRow.skill_name,
                SkillInvocationRow.version,
            )
        ).all()
        evaluation_rows = self._db.execute(
            select(
                SkillEvaluationRow.package_id,
                SkillEvaluationRow.skill_name,
                SkillEvaluationRow.version,
                func.count(SkillEvaluationRow.evaluation_id),
                func.sum(SkillEvaluationRow.prompt_tokens),
                func.sum(SkillEvaluationRow.cost_microunits),
            ).group_by(
                SkillEvaluationRow.package_id,
                SkillEvaluationRow.skill_name,
                SkillEvaluationRow.version,
            )
        ).all()
        latest_rows = self._db.execute(
            select(SkillEvaluationRow).order_by(
                SkillEvaluationRow.occurred_at.desc(),
                SkillEvaluationRow.evaluation_id.desc(),
            )
        ).scalars()

        values: dict[str, dict[str, object]] = {}
        for row in usage_rows:
            values[str(row[0])] = {
                "package_id": str(row[0]),
                "skill_name": str(row[1]),
                "version": str(row[2]),
                "call_count": int(row[3] or 0),
                "success_count": int(row[4] or 0),
                "failure_count": int(row[5] or 0),
                "prompt_tokens": int(row[6] or 0),
                "resource_bytes": int(row[7] or 0),
                "last_used_at": row[8].isoformat() if row[8] else "",
            }
        for row in evaluation_rows:
            item = values.setdefault(
                str(row[0]),
                {
                    "package_id": str(row[0]),
                    "skill_name": str(row[1]),
                    "version": str(row[2]),
                },
            )
            item.update(
                {
                    "evaluation_count": int(row[3] or 0),
                    "evaluation_prompt_tokens": int(row[4] or 0),
                    "evaluation_cost_microunits": int(row[5] or 0),
                }
            )
        seen_latest: set[str] = set()
        for row in latest_rows:
            package_id = str(row.package_id)
            if package_id in seen_latest:
                continue
            seen_latest.add(package_id)
            item = values.setdefault(
                package_id,
                {
                    "package_id": package_id,
                    "skill_name": str(row.skill_name),
                    "version": str(row.version),
                },
            )
            item.update(
                {
                    "latest_evaluation_passed": bool(row.passed),
                    "latest_score_micros": int(row.score_micros),
                    "latest_evaluated_at": (
                        row.occurred_at.isoformat() if row.occurred_at else ""
                    ),
                }
            )
        return tuple(
            SkillVersionGovernanceSnapshot(**values[package_id])
            for package_id in sorted(values)
        )


__all__ = ["SkillGovernanceService", "SkillVersionGovernanceSnapshot"]
