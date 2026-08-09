"""受控自进化不可变 Artifact、人工批准和灰度发布存储。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
from hmac import compare_digest
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from typing import Any, Callable, Iterator, Mapping
import uuid

from .contracts import (
    EVOLUTION_SCHEMA_VERSION,
    EvolutionCandidateBundle,
    EvolutionContractError,
    EvolutionTargetKind,
    FrozenDatasetManifest,
    _aware_timestamp,
    _identifier,
    _nonnegative_int,
    _required_text,
    _resource_id,
    _sha256,
    canonical_json,
    sha256_json,
)
from .gates import EvolutionGateEvidence, evaluate_evolution_candidate


MAX_CANARY_BASIS_POINTS = 2_000
MAX_APPROVAL_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_CANARY_DURATION_SECONDS = 24 * 60 * 60
_MAX_JSON_BYTES = 2 * 1024 * 1024
_OPERATION_SCHEMA_VERSION = 1
_OPERATION_KINDS = frozenset({"activate_canary", "rollback_canary"})
_OPERATION_STATUSES = frozenset({"pending", "ambiguous", "finalized"})
_OPERATION_PHASES = {
    "activate_canary": (
        "prepared",
        "release_written",
        "approval_consumed",
        "active_committed",
        "finalized",
    ),
    "rollback_canary": (
        "prepared",
        "receipt_written",
        "active_committed",
        "finalized",
    ),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvolutionContractError("时间必须包含时区")
    return value.astimezone(timezone.utc).isoformat()


def _public_approval(value: Mapping[str, Any]) -> dict[str, object]:
    return {
        key: item
        for key, item in value.items()
        if key != "token_sha256"
    }


class EvolutionControlStore:
    """文件型控制面；候选和证据不可变，active index 在文件锁内原子替换。"""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.root = Path(root).resolve(strict=False)
        self._clock = clock or _utc_now
        self._token_factory = token_factory or (
            lambda: secrets.token_urlsafe(32)
        )
        self._failure_injector = failure_injector

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime):
            raise EvolutionContractError("clock 必须返回 datetime")
        if now.tzinfo is None or now.utcoffset() is None:
            raise EvolutionContractError("clock 必须返回含时区的 datetime")
        return now.astimezone(timezone.utc)

    def _checkpoint(self, point: str) -> None:
        if self._failure_injector is not None:
            self._failure_injector(point)

    @property
    def _active_path(self) -> Path:
        return self.root / "active" / "index.json"

    @property
    def _operations_dir(self) -> Path:
        return self.root / "operations"

    def _ensure_root(self) -> None:
        root_existed = self.root.exists()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or not self.root.is_dir():
            raise EvolutionContractError("进化控制根目录必须是普通目录")
        if not root_existed:
            self._fsync_directory(self.root.parent)
        for name in (
            "datasets",
            "candidates",
            "gates",
            "approvals",
            "approval_consumptions",
            "releases",
            "rollbacks",
            "active",
            "operations",
        ):
            path = self.root / name
            path_existed = path.exists()
            path.mkdir(exist_ok=True, mode=0o700)
            if path.is_symlink() or not path.is_dir():
                raise EvolutionContractError(f"进化控制目录无效: {name}")
            if not path_existed:
                self._fsync_directory(self.root)

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
        encoded = (canonical_json(value) + "\n").encode("utf-8")
        if len(encoded) > _MAX_JSON_BYTES:
            raise EvolutionContractError("进化控制 JSON 超过 2 MiB")
        return encoded

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise EvolutionContractError("进化控制 Artifact 不存在") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise EvolutionContractError("进化控制 Artifact 必须是普通文件")
        if metadata.st_size > _MAX_JSON_BYTES:
            raise EvolutionContractError("进化控制 Artifact 超过 2 MiB")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvolutionContractError("进化控制 Artifact 无法读取") from exc
        if not isinstance(value, dict):
            raise EvolutionContractError("进化控制 Artifact 必须是 JSON 对象")
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
                raise EvolutionContractError("immutable Artifact 已存在但内容冲突")
            return
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_directory(path.parent)
        except BaseException:
            try:
                path.unlink()
            except OSError:
                pass
            raise

    def _replace_json(self, path: Path, value: object) -> None:
        self._ensure_root()
        encoded = self._json_bytes(value)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(temp_name)
        try:
            os.chmod(temp_path, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            self._fsync_directory(path.parent)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def put_dataset(self, manifest: FrozenDatasetManifest) -> dict[str, object]:
        if not isinstance(manifest, FrozenDatasetManifest):
            raise EvolutionContractError("dataset manifest 无效")
        path = self.root / "datasets" / f"{manifest.dataset_sha256}.json"
        self._write_immutable(path, manifest.to_dict())
        return manifest.to_dict()

    def get_dataset(self, dataset_sha256: str) -> FrozenDatasetManifest:
        digest = _sha256(dataset_sha256, "dataset_sha256")
        return FrozenDatasetManifest.from_dict(
            self._read_json(self.root / "datasets" / f"{digest}.json")
        )

    def put_candidate(self, candidate: EvolutionCandidateBundle) -> dict[str, object]:
        if not isinstance(candidate, EvolutionCandidateBundle):
            raise EvolutionContractError("candidate bundle 无效")
        dataset = self.get_dataset(candidate.dataset_sha256)
        if dataset.source_revision != candidate.generation.source_revision:
            raise EvolutionContractError("候选与冻结数据集 source revision 不一致")
        path = self.root / "candidates" / f"{candidate.candidate_sha256}.json"
        self._write_immutable(path, candidate.to_dict())
        return candidate.to_dict()

    def get_candidate(self, candidate_sha256: str) -> EvolutionCandidateBundle:
        digest = _sha256(candidate_sha256, "candidate_sha256")
        return EvolutionCandidateBundle.from_dict(
            self._read_json(self.root / "candidates" / f"{digest}.json")
        )

    def evaluate_gate(
        self,
        evidence: EvolutionGateEvidence,
        *,
        current_harness_registry_sha256: str,
    ) -> dict[str, object]:
        candidate = self.get_candidate(evidence.candidate_sha256)
        dataset = self.get_dataset(candidate.dataset_sha256)
        report = evaluate_evolution_candidate(
            candidate=candidate,
            dataset=dataset,
            evidence=evidence,
            current_harness_registry_sha256=current_harness_registry_sha256,
        )
        envelope = {
            "schema_version": EVOLUTION_SCHEMA_VERSION,
            "evidence": evidence.to_dict(),
            "report": report,
        }
        path = self.root / "gates" / f"{report['gate_report_sha256']}.json"
        self._write_immutable(path, envelope)
        return report

    def get_gate(self, gate_report_sha256: str) -> dict[str, Any]:
        digest = _sha256(gate_report_sha256, "gate_report_sha256")
        envelope = self._read_json(self.root / "gates" / f"{digest}.json")
        report = envelope.get("report")
        evidence = envelope.get("evidence")
        if not isinstance(report, dict) or not isinstance(evidence, dict):
            raise EvolutionContractError("gate Artifact 结构无效")
        declared = str(report.get("gate_report_sha256") or "")
        content = {
            key: value
            for key, value in report.items()
            if key != "gate_report_sha256"
        }
        if declared != digest or sha256_json(content) != digest:
            raise EvolutionContractError("gate Artifact 摘要不匹配")
        parsed_evidence = EvolutionGateEvidence.from_dict(evidence)
        if report.get("evidence_sha256") != sha256_json(
            parsed_evidence.to_dict()
        ):
            raise EvolutionContractError("gate Artifact 证据摘要不匹配")
        if (
            report.get("candidate_sha256")
            != parsed_evidence.candidate_sha256
            or report.get("dataset_sha256")
            != parsed_evidence.dataset_sha256
        ):
            raise EvolutionContractError("gate Artifact 证据绑定不一致")
        return envelope

    def approve(
        self,
        *,
        candidate_sha256: str,
        gate_report_sha256: str,
        confirm_candidate_sha256: str,
        reviewer: str,
        reviewer_kind: str,
        reason: str,
        risk_scope: tuple[str, ...],
        max_basis_points: int,
        expires_in_seconds: int,
        current_harness_registry_sha256: str,
    ) -> tuple[dict[str, object], str]:
        candidate = self.get_candidate(candidate_sha256)
        confirmed = _sha256(
            confirm_candidate_sha256,
            "confirm_candidate_sha256",
        )
        if confirmed != candidate.candidate_sha256:
            raise EvolutionContractError("人工确认摘要与候选不一致")
        gate_digest = _sha256(gate_report_sha256, "gate_report_sha256")
        current_registry = _sha256(
            current_harness_registry_sha256,
            "current_harness_registry_sha256",
        )
        gate_envelope = self.get_gate(gate_digest)
        gate = gate_envelope["report"]
        if gate.get("candidate_sha256") != candidate.candidate_sha256:
            raise EvolutionContractError("gate 未绑定当前候选")
        if gate.get("passed") is not True:
            raise EvolutionContractError("未通过独立门禁的候选不能批准")
        if gate.get("harness_registry_sha256") != current_registry:
            raise EvolutionContractError("Harness Registry 已漂移，必须重新评测")
        if reviewer_kind != "human":
            raise EvolutionContractError("只有人工管理员可以批准灰度")
        normalized_reviewer = _required_text(
            reviewer,
            "reviewer",
            maximum=128,
        )
        normalized_reason = _required_text(reason, "reason", maximum=2_000)
        normalized_risk = tuple(
            sorted(_identifier(item, "risk_scope") for item in risk_scope)
        )
        if not normalized_risk or len(normalized_risk) > 16:
            raise EvolutionContractError("risk_scope 必须包含 1..16 项")
        if len(normalized_risk) != len(set(normalized_risk)):
            raise EvolutionContractError("risk_scope 不能重复")
        maximum = _nonnegative_int(
            max_basis_points,
            "max_basis_points",
            maximum=MAX_CANARY_BASIS_POINTS,
        )
        if maximum < 1:
            raise EvolutionContractError("max_basis_points 必须大于 0")
        ttl = _nonnegative_int(
            expires_in_seconds,
            "expires_in_seconds",
            maximum=MAX_APPROVAL_TTL_SECONDS,
        )
        if ttl < 60:
            raise EvolutionContractError("人工批准有效期至少为 60 秒")
        now = self._now()
        token = self._token_factory()
        if not isinstance(token, str) or len(token) < 32 or len(token) > 512:
            raise EvolutionContractError("approval token generator 返回无效凭据")
        token_sha = sha256_json({"token": token})
        approval_id = f"evoapproval_{uuid.uuid4().hex}"
        content = {
            "schema_version": EVOLUTION_SCHEMA_VERSION,
            "approval_id": approval_id,
            "candidate_sha256": candidate.candidate_sha256,
            "gate_report_sha256": gate_digest,
            "harness_registry_sha256": current_registry,
            "dataset_sha256": candidate.dataset_sha256,
            "target_kind": candidate.target.kind.value,
            "resource_id": candidate.target.resource_id,
            "target_environment": "canary",
            "reviewer": normalized_reviewer,
            "reviewer_kind": "human",
            "reason": normalized_reason,
            "risk_scope": list(normalized_risk),
            "max_basis_points": maximum,
            "issued_at": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=ttl)),
            "token_sha256": token_sha,
            "status": "issued",
        }
        approval = {**content, "approval_sha256": sha256_json(content)}
        self._write_immutable(
            self.root / "approvals" / f"{approval_id}.json",
            approval,
        )
        return _public_approval(approval), token

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        identifier = _identifier(approval_id, "approval_id")
        approval = self._read_json(
            self.root / "approvals" / f"{identifier}.json"
        )
        declared = _sha256(
            approval.get("approval_sha256"),
            "approval.approval_sha256",
        )
        content = {
            key: value
            for key, value in approval.items()
            if key != "approval_sha256"
        }
        if sha256_json(content) != declared:
            raise EvolutionContractError("approval Artifact 摘要不匹配")
        return approval

    def _active_index(self) -> dict[str, str]:
        if not self._active_path.exists():
            return {}
        payload = self._read_json(self._active_path)
        if payload.get("schema_version") != EVOLUTION_SCHEMA_VERSION:
            raise EvolutionContractError("active index schema 无效")
        active = payload.get("active")
        if not isinstance(active, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in active.items()
        ):
            raise EvolutionContractError("active index 内容无效")
        return dict(active)

    def _write_active_index(self, active: Mapping[str, str]) -> None:
        self._replace_json(
            self._active_path,
            {
                "schema_version": EVOLUTION_SCHEMA_VERSION,
                "active": dict(sorted(active.items())),
            },
        )

    def get_release(self, release_id: str) -> dict[str, Any]:
        identifier = _identifier(release_id, "release_id")
        release = self._read_json(
            self.root / "releases" / f"{identifier}.json"
        )
        declared = _sha256(
            release.get("release_sha256"),
            "release.release_sha256",
        )
        content = {
            key: value
            for key, value in release.items()
            if key != "release_sha256"
        }
        if sha256_json(content) != declared:
            raise EvolutionContractError("release Artifact 摘要不匹配")
        return release

    @staticmethod
    def _operation_id(kind: str, identity: str) -> str:
        if kind not in _OPERATION_KINDS:
            raise EvolutionContractError("进化操作 kind 无效")
        return f"evoop_{sha256_json({'kind': kind, 'identity': identity})}"

    def _operation_path(self, operation_id: str) -> Path:
        identifier = _identifier(operation_id, "operation_id")
        return self._operations_dir / f"{identifier}.json"

    @staticmethod
    def _verify_embedded_artifact(
        value: object,
        *,
        digest_field: str,
        name: str,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise EvolutionContractError(f"{name} 必须是 JSON 对象")
        declared = _sha256(value.get(digest_field), f"{name}.{digest_field}")
        content = {key: item for key, item in value.items() if key != digest_field}
        if sha256_json(content) != declared:
            raise EvolutionContractError(f"{name} 摘要不匹配")
        return dict(value)

    def _validate_operation(
        self,
        value: object,
        *,
        expected_operation_id: str,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise EvolutionContractError("进化操作 journal 必须是 JSON 对象")
        declared = _sha256(
            value.get("operation_sha256"),
            "operation.operation_sha256",
        )
        content = {
            key: item for key, item in value.items() if key != "operation_sha256"
        }
        if sha256_json(content) != declared:
            raise EvolutionContractError("进化操作 journal 摘要不匹配")
        if value.get("schema_version") != _OPERATION_SCHEMA_VERSION:
            raise EvolutionContractError("进化操作 journal schema 无效")
        operation_id = _identifier(value.get("operation_id"), "operation_id")
        if operation_id != expected_operation_id:
            raise EvolutionContractError("进化操作 journal 标识不匹配")
        kind = str(value.get("kind") or "")
        if kind not in _OPERATION_KINDS:
            raise EvolutionContractError("进化操作 journal kind 无效")
        identity_field = "approval_id" if kind == "activate_canary" else "release_id"
        identity = _identifier(value.get(identity_field), identity_field)
        if operation_id != self._operation_id(kind, identity):
            raise EvolutionContractError("进化操作 journal 绑定不一致")
        _sha256(value.get("request_sha256"), "operation.request_sha256")
        status = str(value.get("status") or "")
        phase = str(value.get("phase") or "")
        if status not in _OPERATION_STATUSES:
            raise EvolutionContractError("进化操作 journal 状态无效")
        if phase not in _OPERATION_PHASES[kind]:
            raise EvolutionContractError("进化操作 journal 阶段无效")
        if status == "finalized" and phase != "finalized":
            raise EvolutionContractError("已完成进化操作的 journal 阶段无效")
        if status == "ambiguous":
            _required_text(
                value.get("ambiguity_reason"),
                "operation.ambiguity_reason",
                maximum=512,
            )
        _aware_timestamp(value.get("prepared_at"), "operation.prepared_at")
        _aware_timestamp(value.get("updated_at"), "operation.updated_at")

        target_key = _required_text(
            value.get("target_key"),
            "operation.target_key",
            maximum=512,
        )
        expected_active = str(value.get("expected_active_release_id") or "")
        desired_active = str(value.get("desired_active_release_id") or "")
        if expected_active:
            _identifier(expected_active, "expected_active_release_id")
        if desired_active:
            _identifier(desired_active, "desired_active_release_id")

        if kind == "activate_canary":
            release = self._verify_embedded_artifact(
                value.get("release"),
                digest_field="release_sha256",
                name="operation.release",
            )
            release_id = _identifier(release.get("release_id"), "release_id")
            if (
                release.get("approval_id") != identity
                or release.get("target_key") != target_key
                or desired_active != release_id
                or expected_active != str(release.get("previous_release_id") or "")
            ):
                raise EvolutionContractError("激活 journal 的 release 绑定不一致")
            consumption = self._verify_embedded_artifact(
                value.get("consumption"),
                digest_field="consumption_sha256",
                name="operation.consumption",
            )
            if (
                consumption.get("approval_id") != identity
                or consumption.get("release_id") != release_id
                or _sha256(
                    consumption.get("token_sha256"),
                    "consumption.token_sha256",
                )
                != _sha256(
                    value.get("approval_token_sha256"),
                    "operation.approval_token_sha256",
                )
            ):
                raise EvolutionContractError("激活 journal 的批准消费绑定不一致")
        else:
            receipt = self._verify_embedded_artifact(
                value.get("receipt"),
                digest_field="rollback_sha256",
                name="operation.receipt",
            )
            rollback_id = _identifier(
                receipt.get("rollback_id"),
                "rollback_id",
            )
            if (
                receipt.get("release_id") != identity
                or receipt.get("target_key") != target_key
                or expected_active != identity
                or desired_active != str(receipt.get("restored_release_id") or "")
                or value.get("rollback_id") != rollback_id
            ):
                raise EvolutionContractError("回滚 journal 的 receipt 绑定不一致")
        return dict(value)

    def _read_operation(self, path: Path) -> dict[str, Any]:
        operation_id = _identifier(path.stem, "operation_id")
        return self._validate_operation(
            self._read_json(path),
            expected_operation_id=operation_id,
        )

    def _write_operation(
        self,
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        content = {
            key: item for key, item in value.items() if key != "operation_sha256"
        }
        sealed = {
            **content,
            "operation_sha256": sha256_json(content),
        }
        operation_id = _identifier(sealed.get("operation_id"), "operation_id")
        validated = self._validate_operation(
            sealed,
            expected_operation_id=operation_id,
        )
        self._replace_json(self._operation_path(operation_id), validated)
        return validated

    def _update_operation(
        self,
        operation: Mapping[str, Any],
        **changes: object,
    ) -> dict[str, Any]:
        content = {
            key: item for key, item in operation.items() if key != "operation_sha256"
        }
        content.update(changes)
        content["updated_at"] = _iso(self._now())
        return self._write_operation(content)

    def _advance_operation(
        self,
        operation: dict[str, Any],
        phase: str,
    ) -> dict[str, Any]:
        phases = _OPERATION_PHASES[str(operation["kind"])]
        if phases.index(str(operation["phase"])) >= phases.index(phase):
            return operation
        return self._update_operation(operation, phase=phase)

    def _mark_operation_ambiguous(
        self,
        operation: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        return self._update_operation(
            operation,
            status="ambiguous",
            ambiguity_reason=_required_text(
                reason,
                "ambiguity_reason",
                maximum=512,
            ),
        )

    @staticmethod
    def _activation_request_sha256(
        *,
        candidate_sha256: str,
        approval_id: str,
        approval_token_sha256: str,
        basis_points: int,
        subject_allowlist: tuple[str, ...],
        duration_seconds: int,
        operator: str,
        harness_registry_sha256: str,
    ) -> str:
        return sha256_json(
            {
                "kind": "activate_canary",
                "candidate_sha256": candidate_sha256,
                "approval_id": approval_id,
                "approval_token_sha256": approval_token_sha256,
                "basis_points": basis_points,
                "subject_allowlist": list(subject_allowlist),
                "duration_seconds": duration_seconds,
                "operator": operator,
                "harness_registry_sha256": harness_registry_sha256,
            }
        )

    @staticmethod
    def _rollback_request_sha256(
        *,
        release_id: str,
        operator: str,
        reason: str,
    ) -> str:
        return sha256_json(
            {
                "kind": "rollback_canary",
                "release_id": release_id,
                "operator": operator,
                "reason": reason,
            }
        )

    def _apply_activation_operation_locked(
        self,
        operation: dict[str, Any],
        *,
        inject_failures: bool,
    ) -> dict[str, Any]:
        if operation["status"] != "pending":
            return operation
        release = dict(operation["release"])
        release_id = _identifier(release["release_id"], "release_id")
        release_path = self.root / "releases" / f"{release_id}.json"
        if release_path.exists():
            if canonical_json(self._read_json(release_path)) != canonical_json(release):
                return self._mark_operation_ambiguous(
                    operation,
                    "release Artifact 与 journal 冲突",
                )
        else:
            self._write_immutable(release_path, release)
            if inject_failures:
                self._checkpoint("activation_after_release_written")
        operation = self._advance_operation(operation, "release_written")

        consumption = dict(operation["consumption"])
        approval_id = _identifier(operation["approval_id"], "approval_id")
        consumed_path = self.root / "approval_consumptions" / f"{approval_id}.json"
        if consumed_path.exists():
            if canonical_json(self._read_json(consumed_path)) != canonical_json(
                consumption
            ):
                return self._mark_operation_ambiguous(
                    operation,
                    "approval consumption 与 journal 冲突",
                )
        else:
            self._write_immutable(consumed_path, consumption)
            if inject_failures:
                self._checkpoint("activation_after_approval_consumed")
        operation = self._advance_operation(operation, "approval_consumed")

        active = self._active_index()
        target_key = str(operation["target_key"])
        expected = str(operation["expected_active_release_id"] or "")
        desired = str(operation["desired_active_release_id"] or "")
        current = str(active.get(target_key) or "")
        if current not in {expected, desired}:
            return self._mark_operation_ambiguous(
                operation,
                "active index 已被其他发布修改",
            )
        if current != desired:
            active[target_key] = desired
            self._write_active_index(active)
            if inject_failures:
                self._checkpoint("activation_after_active_committed")
        operation = self._advance_operation(operation, "active_committed")
        operation = self._update_operation(
            operation,
            phase="finalized",
            status="finalized",
        )
        if inject_failures:
            self._checkpoint("activation_after_finalized")
        return operation

    def _apply_rollback_operation_locked(
        self,
        operation: dict[str, Any],
        *,
        inject_failures: bool,
    ) -> dict[str, Any]:
        if operation["status"] != "pending":
            return operation
        receipt = dict(operation["receipt"])
        rollback_id = _identifier(receipt["rollback_id"], "rollback_id")
        receipt_path = self.root / "rollbacks" / f"{rollback_id}.json"
        if receipt_path.exists():
            if canonical_json(self._read_json(receipt_path)) != canonical_json(receipt):
                return self._mark_operation_ambiguous(
                    operation,
                    "rollback receipt 与 journal 冲突",
                )
        else:
            self._write_immutable(receipt_path, receipt)
            if inject_failures:
                self._checkpoint("rollback_after_receipt_written")
        operation = self._advance_operation(operation, "receipt_written")

        active = self._active_index()
        target_key = str(operation["target_key"])
        expected = str(operation["expected_active_release_id"] or "")
        desired = str(operation["desired_active_release_id"] or "")
        current = str(active.get(target_key) or "")
        if current not in {expected, desired}:
            return self._mark_operation_ambiguous(
                operation,
                "active index 已被其他发布修改",
            )
        if current != desired:
            if desired:
                active[target_key] = desired
            else:
                active.pop(target_key, None)
            self._write_active_index(active)
            if inject_failures:
                self._checkpoint("rollback_after_active_committed")
        operation = self._advance_operation(operation, "active_committed")
        operation = self._update_operation(
            operation,
            phase="finalized",
            status="finalized",
        )
        if inject_failures:
            self._checkpoint("rollback_after_finalized")
        return operation

    def _reconcile_operations_locked(self) -> dict[str, int]:
        self._ensure_root()
        operations: list[dict[str, Any]] = []
        for path in sorted(self._operations_dir.glob("*.json")):
            operation = self._read_operation(path)
            if operation["status"] == "pending":
                if operation["kind"] == "activate_canary":
                    operation = self._apply_activation_operation_locked(
                        operation,
                        inject_failures=False,
                    )
                else:
                    operation = self._apply_rollback_operation_locked(
                        operation,
                        inject_failures=False,
                    )
            operations.append(operation)
        return {
            status: sum(item["status"] == status for item in operations)
            for status in ("pending", "ambiguous", "finalized")
        }

    def reconcile_operations(self) -> dict[str, int]:
        """在全局文件锁内完成可安全重放的进化控制操作。"""

        with self._exclusive_lock():
            return self._reconcile_operations_locked()

    def _reconciled_active_index(
        self,
    ) -> tuple[dict[str, str], dict[str, int]]:
        with self._exclusive_lock():
            recovery = self._reconcile_operations_locked()
            if recovery["ambiguous"]:
                raise EvolutionContractError("存在未处置的进化控制歧义操作")
            return self._active_index(), recovery

    def activate_canary(
        self,
        *,
        candidate_sha256: str,
        approval_id: str,
        approval_token: str,
        basis_points: int,
        subject_allowlist: tuple[str, ...],
        duration_seconds: int,
        operator: str,
        current_harness_registry_sha256: str,
    ) -> dict[str, object]:
        candidate = self.get_candidate(candidate_sha256)
        current_registry = _sha256(
            current_harness_registry_sha256,
            "current_harness_registry_sha256",
        )
        approval_identifier = _identifier(approval_id, "approval_id")
        normalized_operator = _required_text(operator, "operator", maximum=128)
        rollout = _nonnegative_int(
            basis_points,
            "basis_points",
            maximum=MAX_CANARY_BASIS_POINTS,
        )
        if rollout < 1:
            raise EvolutionContractError("basis_points 必须大于 0")
        allowlist = tuple(
            sorted(_resource_id(item, "subject_allowlist") for item in subject_allowlist)
        )
        if len(allowlist) > 100 or len(allowlist) != len(set(allowlist)):
            raise EvolutionContractError("subject_allowlist 必须唯一且不超过 100 项")
        duration = _nonnegative_int(
            duration_seconds,
            "duration_seconds",
            maximum=MAX_CANARY_DURATION_SECONDS,
        )
        if duration < 60:
            raise EvolutionContractError("灰度时长至少为 60 秒")
        token = str(approval_token or "")
        if not token:
            raise EvolutionContractError("approval_token 不能为空")
        actual_token_sha = sha256_json({"token": token})
        request_sha256 = self._activation_request_sha256(
            candidate_sha256=candidate.candidate_sha256,
            approval_id=approval_identifier,
            approval_token_sha256=actual_token_sha,
            basis_points=rollout,
            subject_allowlist=allowlist,
            duration_seconds=duration,
            operator=normalized_operator,
            harness_registry_sha256=current_registry,
        )
        operation_id = self._operation_id(
            "activate_canary",
            approval_identifier,
        )

        with self._exclusive_lock():
            recovery = self._reconcile_operations_locked()
            approval = self.get_approval(approval_identifier)
            expected_token_sha = str(approval.get("token_sha256") or "")
            if not compare_digest(actual_token_sha, expected_token_sha):
                raise EvolutionContractError("approval_token 无效")
            operation_path = self._operation_path(operation_id)
            if operation_path.exists():
                operation = self._read_operation(operation_path)
                if operation["request_sha256"] != request_sha256:
                    raise EvolutionContractError("人工批准已绑定其他灰度请求")
                if operation["status"] == "ambiguous":
                    raise EvolutionContractError("灰度激活结果存在歧义，必须人工处置")
                if operation["status"] != "finalized":
                    operation = self._apply_activation_operation_locked(
                        operation,
                        inject_failures=True,
                    )
                if operation["status"] != "finalized":
                    raise EvolutionContractError("灰度激活结果存在歧义，必须人工处置")
                return dict(operation["release"])
            if recovery["ambiguous"]:
                raise EvolutionContractError("存在未处置的进化控制歧义操作")

            now = self._now()
            expires_at = _aware_timestamp(
                approval.get("expires_at"),
                "approval.expires_at",
            )
            if now >= expires_at:
                raise EvolutionContractError("人工批准已过期")
            if approval.get("target_environment") != "canary":
                raise EvolutionContractError("批准不允许生产环境激活")
            if approval.get("harness_registry_sha256") != current_registry:
                raise EvolutionContractError("Harness Registry 已漂移，批准已失效")
            if approval.get("candidate_sha256") != candidate.candidate_sha256:
                raise EvolutionContractError("批准未绑定当前候选")
            if approval.get("target_kind") != candidate.target.kind.value or (
                approval.get("resource_id") != candidate.target.resource_id
            ):
                raise EvolutionContractError("批准目标与候选目标不一致")
            if rollout > int(approval.get("max_basis_points") or 0):
                raise EvolutionContractError("灰度比例超过人工批准范围")
            if now + timedelta(seconds=duration) > expires_at:
                raise EvolutionContractError("灰度有效期不能超过人工批准有效期")
            consumed_path = (
                self.root
                / "approval_consumptions"
                / f"{approval_identifier}.json"
            )
            if consumed_path.exists():
                raise EvolutionContractError("approval_token 已使用")

            active = self._active_index()
            target_key = candidate.target.target_key
            previous_release_id = active.get(target_key, "")
            release_id = f"evorelease_{uuid.uuid4().hex}"
            content = {
                "schema_version": EVOLUTION_SCHEMA_VERSION,
                "release_id": release_id,
                "candidate_sha256": candidate.candidate_sha256,
                "gate_report_sha256": approval["gate_report_sha256"],
                "approval_id": approval_identifier,
                "approval_sha256": approval["approval_sha256"],
                "environment": "canary",
                "target": candidate.target.to_dict(),
                "target_key": target_key,
                "basis_points": rollout,
                "subject_allowlist": list(allowlist),
                "operator": normalized_operator,
                "activated_at": _iso(now),
                "expires_at": _iso(now + timedelta(seconds=duration)),
                "previous_release_id": previous_release_id,
                "rollback_policy": "previous_verified_release",
                "repository_operations": "forbidden",
            }
            release = {**content, "release_sha256": sha256_json(content)}
            consumption_content = {
                "schema_version": EVOLUTION_SCHEMA_VERSION,
                "approval_id": approval_identifier,
                "release_id": release_id,
                "consumed_at": _iso(now),
                "token_sha256": expected_token_sha,
            }
            consumption = {
                **consumption_content,
                "consumption_sha256": sha256_json(consumption_content),
            }
            prepared_at = _iso(now)
            operation = self._write_operation(
                {
                    "schema_version": _OPERATION_SCHEMA_VERSION,
                    "operation_id": operation_id,
                    "kind": "activate_canary",
                    "approval_id": approval_identifier,
                    "approval_token_sha256": expected_token_sha,
                    "request_sha256": request_sha256,
                    "status": "pending",
                    "phase": "prepared",
                    "prepared_at": prepared_at,
                    "updated_at": prepared_at,
                    "target_key": target_key,
                    "expected_active_release_id": previous_release_id,
                    "desired_active_release_id": release_id,
                    "release": release,
                    "consumption": consumption,
                }
            )
            self._checkpoint("activation_after_journal_prepared")
            operation = self._apply_activation_operation_locked(
                operation,
                inject_failures=True,
            )
            if operation["status"] != "finalized":
                raise EvolutionContractError("灰度激活结果存在歧义，必须人工处置")
            return dict(operation["release"])

    @staticmethod
    def _rollout_bucket(release_id: str, subject_id: str) -> int:
        return int(
            sha256_json({"release_id": release_id, "subject_id": subject_id})[:16],
            16,
        ) % 10_000

    def resolve_canary(
        self,
        *,
        target_kind: str,
        resource_id: str,
        subject_id: str,
        current_harness_registry_sha256: str,
    ) -> dict[str, object]:
        try:
            kind = EvolutionTargetKind(target_kind)
        except ValueError as exc:
            raise EvolutionContractError("target_kind 无效") from exc
        resource = _resource_id(resource_id, "resource_id")
        subject = _resource_id(subject_id, "subject_id")
        current_registry = _sha256(
            current_harness_registry_sha256,
            "current_harness_registry_sha256",
        )
        target_key = f"{kind.value}:{resource}"
        active, _ = self._reconciled_active_index()
        release_id = active.get(target_key, "")
        if not release_id:
            return {
                "selected": False,
                "reason": "no_active_release",
                "target_key": target_key,
                "release_id": "",
                "bucket": None,
                "changes": {},
            }
        release = self.get_release(release_id)
        now = self._now()
        expires = _aware_timestamp(release.get("expires_at"), "release.expires_at")
        if now >= expires:
            return {
                "selected": False,
                "reason": "release_expired",
                "target_key": target_key,
                "release_id": release_id,
                "bucket": None,
                "changes": {},
            }
        candidate = self.get_candidate(release["candidate_sha256"])
        approval = self.get_approval(release["approval_id"])
        gate = self.get_gate(release["gate_report_sha256"])["report"]
        if gate.get("passed") is not True:
            raise EvolutionContractError("active release 的门禁未通过")
        if gate.get("harness_registry_sha256") != current_registry:
            raise EvolutionContractError("active release 的 Harness Registry 已漂移")
        if approval.get("harness_registry_sha256") != current_registry:
            raise EvolutionContractError("active release 的批准 Registry 已漂移")
        if (
            approval.get("approval_sha256") != release.get("approval_sha256")
            or approval.get("candidate_sha256") != candidate.candidate_sha256
            or approval.get("gate_report_sha256")
            != release.get("gate_report_sha256")
            or gate.get("candidate_sha256") != candidate.candidate_sha256
            or release.get("target") != candidate.target.to_dict()
            or release.get("target_key") != candidate.target.target_key
        ):
            raise EvolutionContractError("active release 的候选、门禁或批准绑定不一致")
        bucket = self._rollout_bucket(release_id, subject)
        allowlist = set(release.get("subject_allowlist") or [])
        selected = subject in allowlist or bucket < int(release["basis_points"])
        return {
            "selected": selected,
            "reason": (
                "subject_allowlist"
                if subject in allowlist
                else "percentage_rollout" if selected else "baseline_bucket"
            ),
            "target_key": target_key,
            "release_id": release_id,
            "release_sha256": release["release_sha256"],
            "candidate_sha256": release["candidate_sha256"],
            "gate_report_sha256": release["gate_report_sha256"],
            "approval_id": release["approval_id"],
            "bucket": bucket,
            "basis_points": release["basis_points"],
            "expires_at": release["expires_at"],
            "changes": release["target"]["changes"] if selected else {},
        }

    def rollback_canary(
        self,
        *,
        release_id: str,
        operator: str,
        reason: str,
    ) -> dict[str, object]:
        release_identifier = _identifier(release_id, "release_id")
        normalized_operator = _required_text(operator, "operator", maximum=128)
        normalized_reason = _required_text(reason, "reason", maximum=2_000)
        request_sha256 = self._rollback_request_sha256(
            release_id=release_identifier,
            operator=normalized_operator,
            reason=normalized_reason,
        )
        operation_id = self._operation_id(
            "rollback_canary",
            release_identifier,
        )
        with self._exclusive_lock():
            recovery = self._reconcile_operations_locked()
            operation_path = self._operation_path(operation_id)
            if operation_path.exists():
                operation = self._read_operation(operation_path)
                if operation["request_sha256"] != request_sha256:
                    raise EvolutionContractError("发布已绑定其他回滚请求")
                if operation["status"] == "ambiguous":
                    raise EvolutionContractError("灰度回滚结果存在歧义，必须人工处置")
                if operation["status"] != "finalized":
                    operation = self._apply_rollback_operation_locked(
                        operation,
                        inject_failures=True,
                    )
                if operation["status"] != "finalized":
                    raise EvolutionContractError("灰度回滚结果存在歧义，必须人工处置")
                return dict(operation["receipt"])
            if recovery["ambiguous"]:
                raise EvolutionContractError("存在未处置的进化控制歧义操作")

            release = self.get_release(release_identifier)
            active = self._active_index()
            target_key = str(release["target_key"])
            if active.get(target_key) != release_identifier:
                raise EvolutionContractError("只能回滚当前 active 灰度版本")
            previous_id = str(release.get("previous_release_id") or "")
            restored_id = ""
            if previous_id:
                previous = self.get_release(previous_id)
                previous_gate = self.get_gate(
                    previous["gate_report_sha256"]
                )["report"]
                if previous_gate.get("passed") is not True:
                    raise EvolutionContractError(
                        "上一个发布不再具有有效门禁证据"
                    )
                if self._now() < _aware_timestamp(
                    previous.get("expires_at"),
                    "previous_release.expires_at",
                ):
                    restored_id = previous_id
            rollback_id = f"evorollback_{uuid.uuid4().hex}"
            now = self._now()
            content = {
                "schema_version": EVOLUTION_SCHEMA_VERSION,
                "rollback_id": rollback_id,
                "release_id": release_identifier,
                "release_sha256": release["release_sha256"],
                "target_key": target_key,
                "restored_release_id": restored_id,
                "operator": normalized_operator,
                "reason": normalized_reason,
                "rolled_back_at": _iso(now),
                "strategy": "previous_verified_release",
                "repository_operations": "forbidden",
            }
            receipt = {**content, "rollback_sha256": sha256_json(content)}
            prepared_at = _iso(now)
            operation = self._write_operation(
                {
                    "schema_version": _OPERATION_SCHEMA_VERSION,
                    "operation_id": operation_id,
                    "kind": "rollback_canary",
                    "release_id": release_identifier,
                    "rollback_id": rollback_id,
                    "request_sha256": request_sha256,
                    "status": "pending",
                    "phase": "prepared",
                    "prepared_at": prepared_at,
                    "updated_at": prepared_at,
                    "target_key": target_key,
                    "expected_active_release_id": release_identifier,
                    "desired_active_release_id": restored_id,
                    "receipt": receipt,
                }
            )
            self._checkpoint("rollback_after_journal_prepared")
            operation = self._apply_rollback_operation_locked(
                operation,
                inject_failures=True,
            )
            if operation["status"] != "finalized":
                raise EvolutionContractError("灰度回滚结果存在歧义，必须人工处置")
            return dict(operation["receipt"])

    def state(self) -> dict[str, object]:
        active, recovery = self._reconciled_active_index()
        releases = []
        for target_key, release_id in sorted(active.items()):
            release = self.get_release(release_id)
            releases.append({
                "target_key": target_key,
                "release_id": release_id,
                "release_sha256": release["release_sha256"],
                "candidate_sha256": release["candidate_sha256"],
                "basis_points": release["basis_points"],
                "activated_at": release["activated_at"],
                "expires_at": release["expires_at"],
            })
        return {
            "schema_version": EVOLUTION_SCHEMA_VERSION,
            "environment": "canary",
            "active_releases": releases,
            "operation_recovery": recovery,
            "repository_operations": "forbidden",
        }


__all__ = [
    "MAX_APPROVAL_TTL_SECONDS",
    "MAX_CANARY_BASIS_POINTS",
    "MAX_CANARY_DURATION_SECONDS",
    "EvolutionControlStore",
]
