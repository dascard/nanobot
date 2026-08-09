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
    ) -> None:
        self.root = Path(root).resolve(strict=False)
        self._clock = clock or _utc_now
        self._token_factory = token_factory or (
            lambda: secrets.token_urlsafe(32)
        )

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime):
            raise EvolutionContractError("clock 必须返回 datetime")
        if now.tzinfo is None or now.utcoffset() is None:
            raise EvolutionContractError("clock 必须返回含时区的 datetime")
        return now.astimezone(timezone.utc)

    @property
    def _active_path(self) -> Path:
        return self.root / "active" / "index.json"

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or not self.root.is_dir():
            raise EvolutionContractError("进化控制根目录必须是普通目录")
        for name in (
            "datasets",
            "candidates",
            "gates",
            "approvals",
            "approval_consumptions",
            "releases",
            "rollbacks",
            "active",
        ):
            path = self.root / name
            path.mkdir(exist_ok=True, mode=0o700)
            if path.is_symlink() or not path.is_dir():
                raise EvolutionContractError(f"进化控制目录无效: {name}")

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
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

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

        with self._exclusive_lock():
            approval = self.get_approval(approval_identifier)
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
            expected_token_sha = str(approval.get("token_sha256") or "")
            actual_token_sha = sha256_json({"token": token})
            if not compare_digest(actual_token_sha, expected_token_sha):
                raise EvolutionContractError("approval_token 无效")
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
            self._write_immutable(
                self.root / "releases" / f"{release_id}.json",
                release,
            )
            self._write_immutable(
                consumed_path,
                {
                    "schema_version": EVOLUTION_SCHEMA_VERSION,
                    "approval_id": approval_identifier,
                    "release_id": release_id,
                    "consumed_at": _iso(now),
                    "token_sha256": expected_token_sha,
                },
            )
            active[target_key] = release_id
            self._write_active_index(active)
            return release

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
        active = self._active_index()
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
        with self._exclusive_lock():
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
                    active[target_key] = previous_id
                    restored_id = previous_id
                else:
                    active.pop(target_key, None)
            else:
                active.pop(target_key, None)
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
            self._write_immutable(
                self.root / "rollbacks" / f"{rollback_id}.json",
                receipt,
            )
            self._write_active_index(active)
            return receipt

    def state(self) -> dict[str, object]:
        active = self._active_index()
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
            "repository_operations": "forbidden",
        }


__all__ = [
    "MAX_APPROVAL_TTL_SECONDS",
    "MAX_CANARY_BASIS_POINTS",
    "MAX_CANARY_DURATION_SECONDS",
    "EvolutionControlStore",
]
