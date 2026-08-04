"""框架无关的 Run 证据访问、保留与删除合同。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from core.run_ledger.contracts import canonical_run_status, is_terminal_run_status


RUN_EVIDENCE_MANIFEST_SCHEMA_VERSION = 1
RUN_EVIDENCE_RETENTION_POLICY_VERSION = "run-evidence-retention.v1"

_IDENTIFIER_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RunEvidenceError(RuntimeError):
    """运行证据治理失败。"""

    code = "run_evidence_error"


class RunEvidenceAccessDenied(RunEvidenceError):
    """调用身份无权访问目标 Run。"""

    code = "run_evidence_access_denied"


class RunEvidenceNotFound(RunEvidenceError):
    """目标 Run 不存在，或其证据已删除。"""

    code = "run_evidence_not_found"


class RunEvidenceConflict(RunEvidenceError):
    """目标证据或幂等请求与预期不一致。"""

    code = "run_evidence_conflict"


class RunEvidencePolicyDenied(RunEvidenceError):
    """保留期、终态或法律保留策略拒绝删除。"""

    code = "run_evidence_policy_denied"


class RunEvidenceIntegrityError(RunEvidenceError):
    """证据无法形成完整、可信的导出视图。"""

    code = "run_evidence_integrity_error"


def require_run_evidence_identifier(value: object, field_name: str) -> str:
    """收敛 API、Service 与数据库之间使用的外部标识符。"""

    normalized = str(value or "").strip()
    if _IDENTIFIER_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} 无效")
    return normalized


def require_sha256(value: object, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} 必须是 SHA-256")
    return normalized


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_sha256(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RunEvidenceRole(StrEnum):
    ADMIN = "admin"
    OWNER = "owner"
    SERVICE = "service"


@dataclass(frozen=True, slots=True)
class RunEvidenceOwner:
    """账本接纳事实中冻结的 owner 作用域。"""

    platform: str = ""
    owner_type: str = ""
    owner_id: str = ""

    def __post_init__(self) -> None:
        values = tuple(str(value or "").strip() for value in (
            self.platform,
            self.owner_type,
            self.owner_id,
        ))
        if any(values) and not all(values):
            raise ValueError("owner 作用域必须完整声明")
        for field_name, value in zip(
            ("platform", "owner_type", "owner_id"),
            values,
            strict=True,
        ):
            if value:
                require_run_evidence_identifier(value, f"owner.{field_name}")
            object.__setattr__(self, field_name, value)

    @property
    def declared(self) -> bool:
        return bool(self.owner_id)

    def to_dict(self) -> dict[str, str]:
        return {
            "platform": self.platform,
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
        }


@dataclass(frozen=True, slots=True)
class RunEvidencePrincipal:
    """证据访问身份；Service 必须显式绑定单个 Run。"""

    role: RunEvidenceRole
    principal_id: str
    owner: RunEvidenceOwner = RunEvidenceOwner()
    scoped_run_id: str = ""

    def __post_init__(self) -> None:
        role = RunEvidenceRole(self.role)
        principal_id = require_run_evidence_identifier(
            self.principal_id,
            "principal_id",
        )
        scoped_run_id = str(self.scoped_run_id or "").strip()
        if scoped_run_id:
            scoped_run_id = require_run_evidence_identifier(
                scoped_run_id,
                "scoped_run_id",
            )
        if role is RunEvidenceRole.OWNER and not self.owner.declared:
            raise ValueError("owner principal 必须声明 owner 作用域")
        if role is RunEvidenceRole.SERVICE and not scoped_run_id:
            raise ValueError("service principal 必须绑定 scoped_run_id")
        if role is RunEvidenceRole.ADMIN and (self.owner.declared or scoped_run_id):
            raise ValueError("admin principal 不接受 owner 或 Run 附加范围")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "principal_id", principal_id)
        object.__setattr__(self, "scoped_run_id", scoped_run_id)


def authorize_run_evidence(
    principal: RunEvidencePrincipal,
    *,
    run_id: str,
    owner: RunEvidenceOwner,
) -> None:
    """按精确身份范围授权；owner 不明的旧记录只允许管理员访问。"""

    normalized_run_id = require_run_evidence_identifier(run_id, "run_id")
    if principal.role is RunEvidenceRole.ADMIN:
        return
    if principal.role is RunEvidenceRole.SERVICE:
        if principal.scoped_run_id == normalized_run_id:
            return
        raise RunEvidenceAccessDenied("调用身份无权访问该运行证据")
    if owner.declared and principal.owner == owner:
        return
    raise RunEvidenceAccessDenied("调用身份无权访问该运行证据")


class RunEvidenceRetentionClass(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    ACTIVE = "active"


class RunEvidenceErasureReason(StrEnum):
    RETENTION_EXPIRED = "retention_expired"
    PRIVACY_REQUEST = "privacy_request"


@dataclass(frozen=True, slots=True)
class RunEvidenceRetentionPolicy:
    """终态证据的差异化保留期。"""

    succeeded_days: int = 30
    failed_days: int = 90
    ambiguous_days: int = 365
    policy_version: str = RUN_EVIDENCE_RETENTION_POLICY_VERSION

    def __post_init__(self) -> None:
        days = (
            self.succeeded_days,
            self.failed_days,
            self.ambiguous_days,
        )
        if any(type(value) is not int or not 1 <= value <= 3650 for value in days):
            raise ValueError("运行证据保留期必须在 1 到 3650 天之间")
        if not self.succeeded_days <= self.failed_days <= self.ambiguous_days:
            raise ValueError("运行证据保留期必须满足成功 <= 失败 <= 不确定")
        require_run_evidence_identifier(self.policy_version, "policy_version")

    def retention_class(self, status: object) -> RunEvidenceRetentionClass:
        normalized = canonical_run_status(status)
        if not is_terminal_run_status(normalized):
            return RunEvidenceRetentionClass.ACTIVE
        if normalized == "succeeded":
            return RunEvidenceRetentionClass.SUCCEEDED
        if normalized == "ambiguous":
            return RunEvidenceRetentionClass.AMBIGUOUS
        return RunEvidenceRetentionClass.FAILED

    def days_for(self, retention_class: RunEvidenceRetentionClass) -> int | None:
        return {
            RunEvidenceRetentionClass.SUCCEEDED: self.succeeded_days,
            RunEvidenceRetentionClass.FAILED: self.failed_days,
            RunEvidenceRetentionClass.AMBIGUOUS: self.ambiguous_days,
            RunEvidenceRetentionClass.ACTIVE: None,
        }[retention_class]

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "succeeded_days": self.succeeded_days,
            "failed_days": self.failed_days,
            "ambiguous_days": self.ambiguous_days,
        }


@dataclass(frozen=True, slots=True)
class RunEvidenceRetentionDecision:
    status: str
    retention_class: RunEvidenceRetentionClass
    terminal: bool
    terminal_at: datetime | None
    delete_after: datetime | None
    expired: bool
    legal_hold: bool
    allowed_reasons: tuple[RunEvidenceErasureReason, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "retention_class": self.retention_class.value,
            "terminal": self.terminal,
            "terminal_at": (
                self.terminal_at.isoformat() if self.terminal_at else None
            ),
            "delete_after": (
                self.delete_after.isoformat() if self.delete_after else None
            ),
            "expired": self.expired,
            "legal_hold": self.legal_hold,
            "allowed_reasons": [reason.value for reason in self.allowed_reasons],
        }


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def decide_run_evidence_retention(
    policy: RunEvidenceRetentionPolicy,
    *,
    status: object,
    terminal_at: datetime | None,
    now: datetime,
    legal_hold: bool,
) -> RunEvidenceRetentionDecision:
    """计算可解释的删除决定；活跃、无终止时间和法律保留均 fail closed。"""

    normalized_status = canonical_run_status(status)
    retention_class = policy.retention_class(normalized_status)
    normalized_now = _as_utc(now)
    if normalized_now is None:
        raise ValueError("now 不能为空")
    normalized_terminal_at = _as_utc(terminal_at)
    days = policy.days_for(retention_class)
    delete_after = (
        normalized_terminal_at + timedelta(days=days)
        if normalized_terminal_at is not None and days is not None
        else None
    )
    expired = bool(delete_after is not None and normalized_now >= delete_after)
    allowed: list[RunEvidenceErasureReason] = []
    terminal = retention_class is not RunEvidenceRetentionClass.ACTIVE
    if terminal and normalized_terminal_at is not None and not legal_hold:
        allowed.append(RunEvidenceErasureReason.PRIVACY_REQUEST)
        if expired:
            allowed.append(RunEvidenceErasureReason.RETENTION_EXPIRED)
    return RunEvidenceRetentionDecision(
        status=normalized_status,
        retention_class=retention_class,
        terminal=terminal,
        terminal_at=normalized_terminal_at,
        delete_after=delete_after,
        expired=expired,
        legal_hold=bool(legal_hold),
        allowed_reasons=tuple(allowed),
    )


def require_erasure_allowed(
    decision: RunEvidenceRetentionDecision,
    reason: RunEvidenceErasureReason | str,
) -> RunEvidenceErasureReason:
    normalized = RunEvidenceErasureReason(reason)
    if normalized not in decision.allowed_reasons:
        if decision.legal_hold:
            message = "运行证据处于法律保留状态"
        elif not decision.terminal:
            message = "活跃运行的证据不能删除"
        elif decision.terminal_at is None:
            message = "运行缺少可信终止时间，不能删除证据"
        elif normalized is RunEvidenceErasureReason.RETENTION_EXPIRED:
            message = "运行证据尚未超过保留期"
        else:
            message = "运行证据删除策略拒绝该请求"
        raise RunEvidencePolicyDenied(message)
    return normalized


def immutable_mapping(value: Mapping[str, int]) -> Mapping[str, int]:
    """为 Service 返回值冻结按表计数。"""

    return MappingProxyType({str(key): int(count) for key, count in value.items()})


__all__ = [
    "RUN_EVIDENCE_MANIFEST_SCHEMA_VERSION",
    "RUN_EVIDENCE_RETENTION_POLICY_VERSION",
    "RunEvidenceAccessDenied",
    "RunEvidenceConflict",
    "RunEvidenceErasureReason",
    "RunEvidenceError",
    "RunEvidenceIntegrityError",
    "RunEvidenceNotFound",
    "RunEvidenceOwner",
    "RunEvidencePolicyDenied",
    "RunEvidencePrincipal",
    "RunEvidenceRetentionClass",
    "RunEvidenceRetentionDecision",
    "RunEvidenceRetentionPolicy",
    "RunEvidenceRole",
    "authorize_run_evidence",
    "canonical_json_sha256",
    "decide_run_evidence_retention",
    "immutable_mapping",
    "require_erasure_allowed",
    "require_run_evidence_identifier",
    "require_sha256",
    "sha256_text",
]
