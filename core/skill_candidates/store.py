"""Skill 经验候选的隔离 Artifact、门禁、人工批准和发布回执存储。"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
from hmac import compare_digest
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any
import uuid

from sqlalchemy.orm import Session

from core.evolution_control.contracts import canonical_json, sha256_json

from .contracts import (
    SkillCandidateContractError,
    SkillCandidateEvaluationEvidence,
    SkillExperienceCandidate,
)
from .gates import evaluate_skill_candidate
from .publishing import (
    SkillCandidatePublicationIntent,
    commit_candidate_publication_intent,
    list_candidate_publication_intents,
    load_candidate_publication_intent,
    set_candidate_publication_projection_state,
    stage_candidate_to_skill_registry,
    validate_candidate_publication_receipt,
)


MAX_APPROVAL_TTL_SECONDS = 7 * 24 * 60 * 60
_MAX_JSON_BYTES = 2 * 1024 * 1024
_SHA256_CHARS = frozenset("0123456789abcdef")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SkillCandidateContractError("时间必须包含时区")
    return value.astimezone(timezone.utc).isoformat()


def _sha256(value: object, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(char not in _SHA256_CHARS for char in normalized):
        raise SkillCandidateContractError(f"{name} 必须是 SHA-256")
    return normalized


def _text(value: object, name: str, *, maximum: int) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(char) < 32 for char in normalized)
    ):
        raise SkillCandidateContractError(f"{name} 无效")
    return normalized


def _public_approval(value: Mapping[str, Any]) -> dict[str, object]:
    return {
        key: item
        for key, item in value.items()
        if key != "token_sha256"
    }


class SkillCandidateStore:
    """候选区独立于正式 Skill Registry；发布时才调用受管生命周期。"""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.root = Path(root).resolve(strict=False)
        self._clock = clock or _utc_now
        self._token_factory = token_factory or (
            lambda: secrets.token_urlsafe(32)
        )

    def _now(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise SkillCandidateContractError(
                "clock 必须返回包含时区的 datetime"
            )
        return value.astimezone(timezone.utc)

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or not self.root.is_dir():
            raise SkillCandidateContractError("Skill 候选根目录必须是普通目录")
        for name in (
            "candidates",
            "dedup",
            "evaluation_evidence",
            "gate_reports",
            "approvals",
            "publications",
            "publication_by_approval",
        ):
            path = self.root / name
            path.mkdir(exist_ok=True, mode=0o700)
            if path.is_symlink() or not path.is_dir():
                raise SkillCandidateContractError(f"Skill 候选目录无效: {name}")

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self._ensure_root()
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.root / ".control.lock", flags, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _json_bytes(value: object) -> bytes:
        try:
            encoded = (canonical_json(value) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SkillCandidateContractError(
                "Skill 候选 Artifact 必须是 JSON"
            ) from exc
        if len(encoded) > _MAX_JSON_BYTES:
            raise SkillCandidateContractError("Skill 候选 Artifact 超过 2 MiB")
        return encoded

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise SkillCandidateContractError("Skill 候选 Artifact 不存在") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SkillCandidateContractError("Skill 候选 Artifact 必须是普通文件")
        if metadata.st_size > _MAX_JSON_BYTES:
            raise SkillCandidateContractError("Skill 候选 Artifact 超过 2 MiB")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillCandidateContractError(
                "Skill 候选 Artifact 无法读取"
            ) from exc
        if not isinstance(value, dict):
            raise SkillCandidateContractError("Skill 候选 Artifact 必须是对象")
        return value

    def _write_immutable(self, path: Path, value: object) -> None:
        self._ensure_root()
        encoded = self._json_bytes(value)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            existing = self._read_json(path)
            if canonical_json(existing) != canonical_json(value):
                raise SkillCandidateContractError(
                    "不可变 Skill 候选 Artifact 内容冲突"
                )
            return
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                path.unlink()
            except OSError:
                pass
            raise

    def put_candidate(
        self,
        candidate: SkillExperienceCandidate,
    ) -> dict[str, object]:
        if not isinstance(candidate, SkillExperienceCandidate):
            raise TypeError("candidate 必须是 SkillExperienceCandidate")
        with self._exclusive_lock():
            pointer_path = self.root / "dedup" / f"{candidate.dedup_sha256}.json"
            if pointer_path.exists():
                pointer = self._read_json(pointer_path)
                existing_sha = _sha256(
                    pointer.get("candidate_sha256"),
                    "dedup.candidate_sha256",
                )
                existing = self.get_candidate(existing_sha)
                if existing.dedup_sha256 != candidate.dedup_sha256:
                    raise SkillCandidateContractError("Skill 候选去重索引已损坏")
                return {**existing.to_dict(), "deduplicated": True}
            self._write_immutable(
                self.root / "candidates" / f"{candidate.candidate_sha256}.json",
                candidate.to_dict(),
            )
            self._write_immutable(pointer_path, {
                "dedup_sha256": candidate.dedup_sha256,
                "candidate_sha256": candidate.candidate_sha256,
            })
            return {**candidate.to_dict(), "deduplicated": False}

    def get_candidate(self, candidate_sha256: str) -> SkillExperienceCandidate:
        digest = _sha256(candidate_sha256, "candidate_sha256")
        return SkillExperienceCandidate.from_dict(
            self._read_json(self.root / "candidates" / f"{digest}.json")
        )

    def evaluate(
        self,
        evidence: SkillCandidateEvaluationEvidence,
        *,
        current_harness_registry_sha256: str,
    ) -> dict[str, object]:
        if not isinstance(evidence, SkillCandidateEvaluationEvidence):
            raise TypeError("evidence 必须是 SkillCandidateEvaluationEvidence")
        candidate = self.get_candidate(evidence.candidate_sha256)
        report = evaluate_skill_candidate(
            candidate,
            evidence,
            current_harness_registry_sha256=current_harness_registry_sha256,
        )
        with self._exclusive_lock():
            self._write_immutable(
                self.root
                / "evaluation_evidence"
                / f"{evidence.evaluation_sha256}.json",
                evidence.to_dict(),
            )
            self._write_immutable(
                self.root
                / "gate_reports"
                / f"{report['gate_report_sha256']}.json",
                report,
            )
        return report

    def get_gate_report(
        self,
        gate_report_sha256: str,
        *,
        current_harness_registry_sha256: str,
    ) -> dict[str, object]:
        digest = _sha256(gate_report_sha256, "gate_report_sha256")
        stored = self._read_json(
            self.root / "gate_reports" / f"{digest}.json"
        )
        evaluation_sha = _sha256(
            stored.get("evaluation_sha256"),
            "gate.evaluation_sha256",
        )
        evidence = SkillCandidateEvaluationEvidence.from_dict(
            self._read_json(
                self.root
                / "evaluation_evidence"
                / f"{evaluation_sha}.json"
            )
        )
        candidate = self.get_candidate(str(stored.get("candidate_sha256") or ""))
        rebuilt = evaluate_skill_candidate(
            candidate,
            evidence,
            current_harness_registry_sha256=current_harness_registry_sha256,
        )
        if (
            rebuilt.get("gate_report_sha256") != digest
            or canonical_json(rebuilt) != canonical_json(stored)
        ):
            raise SkillCandidateContractError(
                "Skill 候选门禁报告已被篡改或证据已漂移"
            )
        return rebuilt

    def approve(
        self,
        *,
        candidate_sha256: str,
        gate_report_sha256: str,
        confirm_candidate_sha256: str,
        reviewer: str,
        reviewer_kind: str,
        reason: str,
        expected_binding_generation: int,
        expires_in_seconds: int,
        current_harness_registry_sha256: str,
    ) -> dict[str, object]:
        candidate_digest = _sha256(candidate_sha256, "candidate_sha256")
        if _sha256(
            confirm_candidate_sha256,
            "confirm_candidate_sha256",
        ) != candidate_digest:
            raise SkillCandidateContractError("人工确认的 candidate hash 不一致")
        if reviewer_kind != "human":
            raise SkillCandidateContractError("Skill 候选只能由人工批准")
        reviewer_id = _text(reviewer, "reviewer", maximum=128)
        approval_reason = _text(reason, "reason", maximum=2_000)
        if (
            type(expected_binding_generation) is not int
            or not 0 <= expected_binding_generation <= 2**31 - 1
        ):
            raise SkillCandidateContractError(
                "expected_binding_generation 必须是非负整数"
            )
        if (
            type(expires_in_seconds) is not int
            or not 60 <= expires_in_seconds <= MAX_APPROVAL_TTL_SECONDS
        ):
            raise SkillCandidateContractError("人工批准 TTL 无效")
        with self._exclusive_lock():
            candidate = self.get_candidate(candidate_digest)
            report = self.get_gate_report(
                gate_report_sha256,
                current_harness_registry_sha256=current_harness_registry_sha256,
            )
            if report.get("passed") is not True:
                raise SkillCandidateContractError("未通过独立门禁的候选不能批准")
            if report.get("candidate_sha256") != candidate.candidate_sha256:
                raise SkillCandidateContractError("门禁报告未绑定当前候选")
            if reviewer_id in {
                candidate.generator_id,
                str(report.get("evaluator_id") or ""),
            }:
                raise SkillCandidateContractError(
                    "人工 reviewer 必须独立于生成器和评测器"
                )
            raw_token = self._token_factory()
            if (
                not isinstance(raw_token, str)
                or not 32 <= len(raw_token) <= 512
                or any(ord(char) < 33 for char in raw_token)
            ):
                raise SkillCandidateContractError("token_factory 返回无效令牌")
            now = self._now()
            approval_id = f"skillapproval_{uuid.uuid4().hex}"
            approval = {
                "schema_version": 1,
                "approval_id": approval_id,
                "candidate_sha256": candidate.candidate_sha256,
                "gate_report_sha256": str(report["gate_report_sha256"]),
                "reviewer": reviewer_id,
                "reviewer_kind": "human",
                "reason": approval_reason,
                "target_scope": candidate.target_scope,
                "target_scope_key": candidate.target_scope_key,
                "expected_binding_generation": expected_binding_generation,
                "issued_at": _iso(now),
                "expires_at": _iso(now + timedelta(seconds=expires_in_seconds)),
                "token_sha256": hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            }
            self._write_immutable(
                self.root / "approvals" / f"{approval_id}.json",
                approval,
            )
            return {
                **_public_approval(approval),
                "approval_token": raw_token,
            }

    def get_approval(self, approval_id: str) -> dict[str, object]:
        normalized = _text(approval_id, "approval_id", maximum=128)
        return _public_approval(
            self._read_json(self.root / "approvals" / f"{normalized}.json")
        )

    @staticmethod
    def _validate_publication_result(
        result: Mapping[str, Any],
        *,
        candidate: SkillExperienceCandidate,
    ) -> dict[str, object]:
        required = {
            "package_id",
            "binding_id",
            "binding_generation",
            "active_package_id",
            "active_version",
            "bundle_sha256",
            "publication_mode",
            "previous_active_package_id",
            "previous_active_version",
            "rollback_action",
            "evaluation_id",
        }
        if set(result) != required:
            raise SkillCandidateContractError("Skill Registry 发布结果字段无效")
        normalized = {
            name: str(result.get(name) or "").strip()
            for name in required
            if name != "binding_generation"
        }
        generation = result.get("binding_generation")
        if type(generation) is not int or generation < 1:
            raise SkillCandidateContractError("发布后的 binding_generation 无效")
        if normalized["bundle_sha256"] != candidate.parsed_bundle.bundle_sha256:
            raise SkillCandidateContractError("发布结果未绑定候选 bundle")
        if normalized["publication_mode"] not in {
            "installed_active",
            "version_staged",
        }:
            raise SkillCandidateContractError("publication_mode 无效")
        if normalized["rollback_action"] not in {
            "skill.uninstall",
            "none_runtime_unchanged",
        }:
            raise SkillCandidateContractError("rollback_action 无效")
        for name in (
            "package_id",
            "binding_id",
            "active_package_id",
            "active_version",
            "evaluation_id",
        ):
            if not normalized[name] or len(normalized[name]) > 160:
                raise SkillCandidateContractError(f"发布结果 {name} 无效")
        return {**normalized, "binding_generation": generation}

    def publish(
        self,
        *,
        candidate_sha256: str,
        approval_id: str,
        approval_token: str,
        current_harness_registry_sha256: str,
        db: Session,
    ) -> dict[str, object]:
        """原子提交正式 Skill 与发布意图，再物化可重建文件回执。"""

        candidate_digest = _sha256(candidate_sha256, "candidate_sha256")
        normalized_approval_id = _text(
            approval_id,
            "approval_id",
            maximum=128,
        )
        token = str(approval_token or "")
        if not token or len(token) > 512:
            raise SkillCandidateContractError("approval_token 无效")
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        with self._exclusive_lock():
            pointer_path = (
                self.root
                / "publication_by_approval"
                / f"{normalized_approval_id}.json"
            )
            approval = self._read_json(
                self.root / "approvals" / f"{normalized_approval_id}.json"
            )
            if approval.get("candidate_sha256") != candidate_digest:
                raise SkillCandidateContractError("批准未绑定当前候选")
            expected_token_sha = str(approval.get("token_sha256") or "")
            actual_token_sha = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if not compare_digest(expected_token_sha, actual_token_sha):
                raise SkillCandidateContractError("人工批准令牌无效")
            candidate = self.get_candidate(candidate_digest)
            existing_intent = load_candidate_publication_intent(
                db,
                approval_id=normalized_approval_id,
            )
            if existing_intent is not None:
                self._validate_existing_intent(
                    existing_intent,
                    candidate=candidate,
                    approval=approval,
                    approval_token_sha256=actual_token_sha,
                )
                return self._materialize_publication_intent(
                    db,
                    existing_intent,
                )
            legacy_receipt = self._legacy_receipt_for_approval(
                normalized_approval_id,
                pointer_path=pointer_path,
            )
            if legacy_receipt is not None:
                self._validate_legacy_receipt(
                    db,
                    receipt=legacy_receipt,
                    candidate=candidate,
                    approval=approval,
                )
                adopted = commit_candidate_publication_intent(
                    db,
                    approval_token_sha256=actual_token_sha,
                    receipt=legacy_receipt,
                )
                return self._materialize_publication_intent(db, adopted)
            try:
                expires_at = datetime.fromisoformat(str(approval["expires_at"]))
            except (KeyError, ValueError) as exc:
                raise SkillCandidateContractError("人工批准时间已损坏") from exc
            if self._now() >= expires_at.astimezone(timezone.utc):
                raise SkillCandidateContractError("人工批准已过期")
            report = self.get_gate_report(
                str(approval.get("gate_report_sha256") or ""),
                current_harness_registry_sha256=current_harness_registry_sha256,
            )
            if report.get("passed") is not True:
                raise SkillCandidateContractError("门禁不再有效，拒绝发布")
            publication_id = f"skillpub_{uuid.uuid4().hex}"
            try:
                result = self._validate_publication_result(
                    stage_candidate_to_skill_registry(
                        db,
                        candidate,
                        report,
                        _public_approval(approval),
                    ),
                    candidate=candidate,
                )
                receipt = self._build_publication_receipt(
                    publication_id=publication_id,
                    approval_id=normalized_approval_id,
                    candidate=candidate,
                    report=report,
                    approval=approval,
                    result=result,
                )
                intent = commit_candidate_publication_intent(
                    db,
                    approval_token_sha256=actual_token_sha,
                    receipt=receipt,
                )
            except BaseException:
                db.rollback()
                raise
            return self._materialize_publication_intent(db, intent)

    @staticmethod
    def _validate_existing_intent(
        intent: SkillCandidatePublicationIntent,
        *,
        candidate: SkillExperienceCandidate,
        approval: Mapping[str, Any],
        approval_token_sha256: str,
    ) -> None:
        if (
            intent.candidate_sha256 != candidate.candidate_sha256
            or intent.gate_report_sha256
            != str(approval.get("gate_report_sha256") or "")
            or not compare_digest(
                intent.approval_token_sha256,
                approval_token_sha256,
            )
        ):
            raise SkillCandidateContractError("人工批准已绑定不同的发布意图")

    def _legacy_receipt_for_approval(
        self,
        approval_id: str,
        *,
        pointer_path: Path,
    ) -> dict[str, object] | None:
        if pointer_path.exists():
            pointer = self._read_json(pointer_path)
            if str(pointer.get("approval_id") or "") != approval_id:
                raise SkillCandidateContractError("旧发布索引与人工批准不一致")
            publication_id = _text(
                pointer.get("publication_id"),
                "publication_id",
                maximum=128,
            )
            receipt = self.get_publication(publication_id)
            if str(pointer.get("publication_sha256") or "") != str(
                receipt.get("publication_sha256") or ""
            ):
                raise SkillCandidateContractError("旧发布索引摘要不一致")
            return receipt
        matches: list[dict[str, object]] = []
        for path in sorted((self.root / "publications").glob("*.json")):
            value = self.get_publication(path.stem)
            if str(value.get("approval_id") or "") == approval_id:
                matches.append(value)
        if len(matches) > 1:
            raise SkillCandidateContractError("人工批准存在多个旧发布回执")
        return matches[0] if matches else None

    def _validate_legacy_receipt(
        self,
        db: Session,
        *,
        receipt: Mapping[str, object],
        candidate: SkillExperienceCandidate,
        approval: Mapping[str, Any],
    ) -> None:
        if (
            str(receipt.get("candidate_sha256") or "")
            != candidate.candidate_sha256
            or str(receipt.get("gate_report_sha256") or "")
            != str(approval.get("gate_report_sha256") or "")
            or str(receipt.get("approval_id") or "")
            != str(approval.get("approval_id") or "")
        ):
            raise SkillCandidateContractError("旧发布回执与人工批准不一致")
        required = {
            name: receipt.get(name)
            for name in (
                "package_id",
                "binding_id",
                "binding_generation",
                "active_package_id",
                "active_version",
                "bundle_sha256",
                "publication_mode",
                "previous_active_package_id",
                "previous_active_version",
                "rollback_action",
                "evaluation_id",
            )
        }
        self._validate_publication_result(required, candidate=candidate)
        validate_candidate_publication_receipt(
            db,
            candidate=candidate,
            receipt=receipt,
        )

    def _build_publication_receipt(
        self,
        *,
        publication_id: str,
        approval_id: str,
        candidate: SkillExperienceCandidate,
        report: Mapping[str, Any],
        approval: Mapping[str, Any],
        result: Mapping[str, object],
    ) -> dict[str, object]:
        if result["rollback_action"] == "skill.uninstall":
            rollback: dict[str, object] = {
                "required_for_runtime_revert": True,
                "method": "POST",
                "path": "/api/v1/admin/skills/uninstall",
                "body": {
                    "scope": candidate.target_scope,
                    "scope_key": candidate.target_scope_key,
                    "skill_name": candidate.parsed_bundle.name,
                    "expected_generation": result["binding_generation"],
                },
            }
        else:
            rollback = {
                "required_for_runtime_revert": False,
                "method": "",
                "path": "",
                "body": {},
                "reason": "候选版本仅暂存，运行时仍使用原激活版本",
            }
        payload: dict[str, object] = {
            "schema_version": 1,
            "publication_id": publication_id,
            "candidate_sha256": candidate.candidate_sha256,
            "candidate_id": candidate.candidate_id,
            "gate_report_sha256": str(report["gate_report_sha256"]),
            "approval_id": approval_id,
            "reviewer": approval["reviewer"],
            "reviewer_kind": "human",
            "target_scope": candidate.target_scope,
            "target_scope_key": candidate.target_scope_key,
            "skill_name": candidate.parsed_bundle.name,
            "version": candidate.parsed_bundle.version,
            "source_run_ids": [item.run_id for item in candidate.source_runs],
            "source_trajectory_sha256s": list(
                candidate.source_trajectory_sha256s
            ),
            "baseline_bundle_sha256": candidate.baseline_bundle_sha256,
            "dataset_sha256": report["dataset_sha256"],
            "baseline_score_micros": report["baseline_score_micros"],
            "candidate_score_micros": report["candidate_score_micros"],
            "quality_delta_micros": report["quality_delta_micros"],
            "baseline_cost_microunits": report["baseline_cost_microunits"],
            "candidate_cost_microunits": report["candidate_cost_microunits"],
            "approved_cost_microunits": report["approved_cost_microunits"],
            "published_at": _iso(self._now()),
            "repository_operations": "forbidden",
            "rollback": rollback,
            **result,
        }
        return {**payload, "publication_sha256": sha256_json(payload)}

    def _materialize_publication_intent(
        self,
        db: Session,
        intent: SkillCandidatePublicationIntent,
    ) -> dict[str, object]:
        try:
            self._write_immutable(
                self.root
                / "publications"
                / f"{intent.publication_id}.json",
                intent.receipt,
            )
            self._write_immutable(
                self.root
                / "publication_by_approval"
                / f"{intent.approval_id}.json",
                {
                    "approval_id": intent.approval_id,
                    "publication_id": intent.publication_id,
                    "publication_sha256": intent.publication_sha256,
                },
            )
        except Exception as exc:
            ambiguous = (
                intent.status == "ambiguous"
                or isinstance(exc, SkillCandidateContractError)
            )
            try:
                set_candidate_publication_projection_state(
                    db,
                    intent=intent,
                    status="ambiguous" if ambiguous else "pending",
                    error_code=(
                        "projection_conflict"
                        if ambiguous
                        else "projection_write_failed"
                    ),
                )
            except Exception:
                db.rollback()
            if ambiguous:
                raise SkillCandidateContractError(
                    "Skill 发布投影冲突，需人工处置"
                ) from exc
            raise SkillCandidateContractError(
                "Skill 发布投影尚未完成，已保留事务意图，待重试"
            ) from exc
        try:
            finalized = set_candidate_publication_projection_state(
                db,
                intent=intent,
                status="finalized",
            )
        except Exception as exc:
            db.rollback()
            raise SkillCandidateContractError(
                "Skill 发布回执已落盘但确认尚未完成，待重试"
            ) from exc
        return dict(finalized.receipt)

    def reconcile_publications(self, db: Session) -> dict[str, int]:
        """重放 pending 文件投影；冲突只标记 ambiguous，不改写事实。"""

        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        counts = {"finalized": 0, "pending": 0, "ambiguous": 0}
        with self._exclusive_lock():
            intents = list_candidate_publication_intents(
                db,
                statuses=frozenset({"pending"}),
            )
            for intent in intents:
                try:
                    self._materialize_publication_intent(db, intent)
                except SkillCandidateContractError:
                    current = load_candidate_publication_intent(
                        db,
                        approval_id=intent.approval_id,
                    )
                    status = current.status if current is not None else "pending"
                    counts[status] += 1
                else:
                    counts["finalized"] += 1
        return counts

    def get_publication(self, publication_id: str) -> dict[str, object]:
        normalized = _text(publication_id, "publication_id", maximum=128)
        value = self._read_json(
            self.root / "publications" / f"{normalized}.json"
        )
        declared = _sha256(
            value.get("publication_sha256"),
            "publication_sha256",
        )
        payload = {
            key: item
            for key, item in value.items()
            if key != "publication_sha256"
        }
        if sha256_json(payload) != declared:
            raise SkillCandidateContractError("Skill 发布回执已被篡改")
        return value

    def state(self, db: Session | None = None) -> dict[str, object]:
        self._ensure_root()

        def names(directory: str) -> list[str]:
            return sorted(path.stem for path in (self.root / directory).glob("*.json"))

        result: dict[str, object] = {
            "candidate_sha256s": names("candidates"),
            "gate_report_sha256s": names("gate_reports"),
            "approval_ids": names("approvals"),
            "publication_ids": names("publications"),
            "token_material_exposed": False,
        }
        if db is not None:
            result["publication_intents"] = [
                {
                    "publication_id": item.publication_id,
                    "approval_id": item.approval_id,
                    "candidate_sha256": item.candidate_sha256,
                    "status": item.status,
                    "reconcile_attempts": item.reconcile_attempts,
                    "last_error_code": item.last_error_code,
                }
                for item in list_candidate_publication_intents(db)
            ]
        return result


__all__ = [
    "MAX_APPROVAL_TTL_SECONDS",
    "SkillCandidateStore",
]
