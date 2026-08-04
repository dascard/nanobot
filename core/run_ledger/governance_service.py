"""Run Ledger 与旧 Trace 的统一证据治理服务。"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from types import MappingProxyType
from typing import Any, Mapping

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.models import (
    AdminAuditLog,
    AgentRun,
    LLMApiRequestLog,
    PromptRenderLog,
    ReplyContractCheckLog,
    RunLedgerErasureAuthorization,
    RunLedgerErasureReceipt,
    RunLedgerEventRow,
    RunLedgerLegalHold,
    RunLedgerStreamHead,
    RunCheckpointRow,
    RunRecoveryOperation,
    RunSideEffectReceipt,
    RuntimeTelemetryEvent,
    ToolCall,
)
from core.run_ledger.contracts import (
    RUN_LEDGER_SCHEMA_NAME,
    RunLedgerContractError,
    RunLedgerIntegrityError,
    canonical_run_status,
    run_ledger_payload_sha256,
)
from core.run_ledger.governance import (
    RUN_EVIDENCE_MANIFEST_SCHEMA_VERSION,
    RunEvidenceAccessDenied,
    RunEvidenceConflict,
    RunEvidenceErasureReason,
    RunEvidenceIntegrityError,
    RunEvidenceNotFound,
    RunEvidenceOwner,
    RunEvidencePrincipal,
    RunEvidenceRetentionDecision,
    RunEvidenceRetentionPolicy,
    RunEvidenceRole,
    authorize_run_evidence,
    canonical_json_sha256,
    decide_run_evidence_retention,
    immutable_mapping,
    require_erasure_allowed,
    require_run_evidence_identifier,
    require_sha256,
    sha256_text,
)
from core.run_ledger.persistence import SqlAlchemyRunEventLedger
from core.run_ledger.read_model import (
    AuthoritativeRunLedgerView,
    load_authoritative_run_view,
)
from core.settings_service import settings


_LEGAL_HOLD_REASON_CODES = frozenset({
    "audit",
    "incident",
    "legal",
    "user_dispute",
})

_LEGACY_EVIDENCE_MODELS = (
    ("agent_runs", AgentRun),
    ("tool_calls", ToolCall),
    ("prompt_render_logs", PromptRenderLog),
    ("llm_api_request_logs", LLMApiRequestLog),
    ("reply_contract_check_logs", ReplyContractCheckLog),
    ("runtime_telemetry_events", RuntimeTelemetryEvent),
    ("run_checkpoints", RunCheckpointRow),
    ("run_side_effect_receipts", RunSideEffectReceipt),
    ("run_recovery_operations", RunRecoveryOperation),
)


def _utc_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _safe_database_value(value: object) -> object:
    if value is None or type(value) in {str, int, float, bool}:
        return value
    if isinstance(value, datetime):
        normalized = _utc_aware(value)
        return normalized.isoformat() if normalized is not None else None
    if isinstance(value, bytes):
        return {
            "bytes": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    return str(value)


def _row_digest_bytes(row: object) -> bytes:
    values = {
        column.name: _safe_database_value(getattr(row, column.name))
        for column in row.__table__.columns  # type: ignore[attr-defined]
    }
    return json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class LegacyRunEvidenceSummary:
    """旧 Trace 只导出数量和聚合摘要，不暴露任何行内容。"""

    counts: Mapping[str, int]
    aggregate_sha256: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "counts": dict(self.counts),
            "aggregate_sha256": dict(self.aggregate_sha256),
            "raw_rows_exported": False,
        }


@dataclass(frozen=True, slots=True)
class RunEvidenceSnapshot:
    run_id: str
    owner: RunEvidenceOwner
    status: str
    terminal_at: datetime | None
    ledger_view: AuthoritativeRunLedgerView | None
    legacy_run: AgentRun | None


@dataclass(frozen=True, slots=True)
class RunEvidenceManifest:
    document: Mapping[str, object]
    manifest_sha256: str
    snapshot: RunEvidenceSnapshot
    legacy: LegacyRunEvidenceSummary
    retention: RunEvidenceRetentionDecision

    def to_dict(self) -> dict[str, object]:
        return {
            **dict(self.document),
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class RunEvidenceErasureResult:
    receipt_id: str
    request_id: str
    run_id_sha256: str
    manifest_sha256: str
    ledger_event_count: int
    legacy_counts: Mapping[str, int]
    reason_code: str
    policy_version: str
    erased_at: datetime
    idempotent_replay: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "receipt_id": self.receipt_id,
            "request_id": self.request_id,
            "run_id_sha256": self.run_id_sha256,
            "manifest_sha256": self.manifest_sha256,
            "ledger_event_count": self.ledger_event_count,
            "legacy_counts": dict(self.legacy_counts),
            "reason_code": self.reason_code,
            "policy_version": self.policy_version,
            "erased_at": self.erased_at.isoformat(),
            "idempotent_replay": self.idempotent_replay,
        }


def load_run_evidence_retention_policy(
    db: Session,
) -> RunEvidenceRetentionPolicy:
    """从同一数据库事务快照解析保留策略。"""

    try:
        return RunEvidenceRetentionPolicy(
            succeeded_days=int(settings.get_for_session(
                db,
                "run_ledger.retention_succeeded_days",
                30,
            )),
            failed_days=int(settings.get_for_session(
                db,
                "run_ledger.retention_failed_days",
                90,
            )),
            ambiguous_days=int(settings.get_for_session(
                db,
                "run_ledger.retention_ambiguous_days",
                365,
            )),
        )
    except (TypeError, ValueError) as exc:
        raise RunEvidenceIntegrityError("运行证据保留策略无效") from exc


class RunEvidenceGovernanceService:
    """把 Ledger、旧 Trace、ACL 和受控删除组合为一个事务边界。"""

    def __init__(
        self,
        db: Session,
        *,
        policy: RunEvidenceRetentionPolicy | None = None,
        now: datetime | None = None,
    ) -> None:
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        self._db = db
        self._policy = policy or load_run_evidence_retention_policy(db)
        self._now = _utc_aware(now) or datetime.now(timezone.utc)

    @property
    def policy(self) -> RunEvidenceRetentionPolicy:
        return self._policy

    def _load_snapshot(
        self,
        run_id: str,
        principal: RunEvidencePrincipal,
    ) -> RunEvidenceSnapshot:
        normalized_run_id = require_run_evidence_identifier(run_id, "run_id")
        ledger = SqlAlchemyRunEventLedger(self._db)
        try:
            view = load_authoritative_run_view(ledger, normalized_run_id)
        except (RunLedgerContractError, RunLedgerIntegrityError) as exc:
            raise RunEvidenceIntegrityError(
                "运行证据无法形成完整权威投影"
            ) from exc
        legacy_run = self._db.get(AgentRun, normalized_run_id)
        if view is None and legacy_run is None:
            raise RunEvidenceNotFound("运行证据不存在")

        owner = RunEvidenceOwner()
        if view is not None:
            identity = view.accepted.event.identity
            if identity.owner_id:
                owner = RunEvidenceOwner(
                    platform=identity.owner_platform,
                    owner_type=identity.owner_type,
                    owner_id=identity.owner_id,
                )
            status = view.projection.status
            terminal_at = view.projection.finished_at
        else:
            status = canonical_run_status(legacy_run.status)
            terminal_at = legacy_run.finished_at
        authorize_run_evidence(
            principal,
            run_id=normalized_run_id,
            owner=owner,
        )
        return RunEvidenceSnapshot(
            run_id=normalized_run_id,
            owner=owner,
            status=status,
            terminal_at=_utc_aware(terminal_at),
            ledger_view=view,
            legacy_run=legacy_run,
        )

    def _legacy_rows(self, run_id: str, model: type[Any]) -> list[Any]:
        primary_keys = tuple(model.__mapper__.primary_key)
        return (
            self._db.query(model)
            .filter(model.run_id == run_id)
            .order_by(*primary_keys)
            .all()
        )

    def _legacy_summary(self, run_id: str) -> LegacyRunEvidenceSummary:
        counts: dict[str, int] = {}
        digests: dict[str, str] = {}
        for table_name, model in _LEGACY_EVIDENCE_MODELS:
            rows = self._legacy_rows(run_id, model)
            digest = hashlib.sha256()
            for row in rows:
                digest.update(_row_digest_bytes(row))
                digest.update(b"\n")
            counts[table_name] = len(rows)
            digests[table_name] = digest.hexdigest()
        return LegacyRunEvidenceSummary(
            counts=immutable_mapping(counts),
            aggregate_sha256=MappingProxyType(digests),
        )

    def _active_holds(self, run_id: str) -> tuple[RunLedgerLegalHold, ...]:
        return tuple(
            self._db.query(RunLedgerLegalHold)
            .filter(
                RunLedgerLegalHold.run_id == run_id,
                RunLedgerLegalHold.released_at.is_(None),
            )
            .order_by(RunLedgerLegalHold.placed_at.asc())
            .all()
        )

    def _retention(
        self,
        snapshot: RunEvidenceSnapshot,
    ) -> RunEvidenceRetentionDecision:
        return decide_run_evidence_retention(
            self._policy,
            status=snapshot.status,
            terminal_at=snapshot.terminal_at,
            now=self._now,
            legal_hold=bool(self._active_holds(snapshot.run_id)),
        )

    @staticmethod
    def _ledger_document(
        snapshot: RunEvidenceSnapshot,
    ) -> dict[str, object]:
        view = snapshot.ledger_view
        if view is None:
            return {
                "present": False,
                "high_water_sequence": 0,
                "terminal_sequence": None,
                "last_event_sha256": "",
                "events": [],
            }
        return {
            "present": True,
            "high_water_sequence": view.head.last_sequence,
            "terminal_sequence": view.head.terminal_sequence,
            "last_event_sha256": view.head.last_event_sha256,
            "events": [
                {
                    "sequence": record.sequence,
                    "event_id": record.event_id,
                    "event_type": record.event_type,
                    "schema_name": RUN_LEDGER_SCHEMA_NAME,
                    "schema_version": record.event.schema_version,
                    "occurred_at": record.event.occurred_at.isoformat(),
                    "recorded_at": record.recorded_at.isoformat(),
                    "source": record.event.source,
                    "status": record.event.status,
                    "payload_sha256": run_ledger_payload_sha256(
                        record.event.payload
                    ),
                    "dropped_field_count": (
                        record.event.dropped_field_count
                    ),
                    "correction_of_event_id": (
                        record.event.correction_of_event_id
                    ),
                    "previous_event_sha256": (
                        record.previous_event_sha256
                    ),
                    "event_sha256": record.event_sha256,
                }
                for record in view.records
            ],
        }

    def export_manifest(
        self,
        run_id: str,
        principal: RunEvidencePrincipal,
    ) -> RunEvidenceManifest:
        """导出可验证但不含 Ledger payload 或旧 Trace 正文的清单。"""

        snapshot = self._load_snapshot(run_id, principal)
        legacy = self._legacy_summary(snapshot.run_id)
        retention = self._retention(snapshot)
        projection = (
            snapshot.ledger_view.projection.to_dict()
            if snapshot.ledger_view is not None
            else {
                "run_id": snapshot.run_id,
                "status": snapshot.status,
                "terminal": retention.terminal,
                "finished_at": (
                    snapshot.terminal_at.isoformat()
                    if snapshot.terminal_at is not None
                    else None
                ),
                "source": "legacy_compat",
            }
        )
        document: dict[str, object] = {
            "schema_version": RUN_EVIDENCE_MANIFEST_SCHEMA_VERSION,
            "run_id": snapshot.run_id,
            "owner": snapshot.owner.to_dict(),
            "projection": projection,
            "ledger": self._ledger_document(snapshot),
            "legacy_evidence": legacy.to_dict(),
            "retention": retention.to_dict(),
            "safety": {
                "ledger_payloads_exported": False,
                "legacy_rows_exported": False,
                "hidden_reasoning_exported": False,
                "secret_values_exported": False,
            },
        }
        manifest_sha256 = canonical_json_sha256(document)
        return RunEvidenceManifest(
            document=MappingProxyType(document),
            manifest_sha256=manifest_sha256,
            snapshot=snapshot,
            legacy=legacy,
            retention=retention,
        )

    def governance_status(
        self,
        run_id: str,
        principal: RunEvidencePrincipal,
    ) -> dict[str, object]:
        manifest = self.export_manifest(run_id, principal)
        holds = self._active_holds(manifest.snapshot.run_id)
        return {
            "run_id": manifest.snapshot.run_id,
            "owner": manifest.snapshot.owner.to_dict(),
            "manifest_sha256": manifest.manifest_sha256,
            "retention": manifest.retention.to_dict(),
            "active_legal_holds": [
                {
                    "hold_id": str(hold.hold_id),
                    "reason_code": str(hold.reason_code),
                    "placed_at": _utc_aware(hold.placed_at).isoformat(),
                }
                for hold in holds
            ],
            "ledger_event_count": (
                len(manifest.snapshot.ledger_view.records)
                if manifest.snapshot.ledger_view is not None
                else 0
            ),
            "legacy_counts": dict(manifest.legacy.counts),
        }

    @staticmethod
    def _require_admin(principal: RunEvidencePrincipal) -> None:
        if principal.role is not RunEvidenceRole.ADMIN:
            raise RunEvidenceAccessDenied("只有管理员可以管理证据保留状态")

    @staticmethod
    def _reason_code(value: object) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _LEGAL_HOLD_REASON_CODES:
            raise ValueError("法律保留 reason_code 无效")
        return normalized

    def _add_audit(
        self,
        *,
        action: str,
        run_id: str,
        actor: str,
        detail: Mapping[str, object],
        ip_address: str = "",
    ) -> None:
        self._db.add(AdminAuditLog(
            admin_user=actor,
            action=action,
            target_type="run_evidence_sha256",
            target_id=sha256_text(run_id),
            detail_json=json.dumps(
                dict(detail),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            ip_address=str(ip_address or "")[:45],
        ))

    def place_legal_hold(
        self,
        *,
        run_id: str,
        hold_id: str,
        reason_code: str,
        principal: RunEvidencePrincipal,
        ip_address: str = "",
    ) -> dict[str, object]:
        self._require_admin(principal)
        snapshot = self._load_snapshot(run_id, principal)
        normalized_hold_id = require_run_evidence_identifier(hold_id, "hold_id")
        normalized_reason = self._reason_code(reason_code)
        existing = self._db.get(RunLedgerLegalHold, normalized_hold_id)
        if existing is not None:
            if (
                str(existing.run_id) != snapshot.run_id
                or str(existing.reason_code) != normalized_reason
                or str(existing.placed_by) != principal.principal_id
            ):
                raise RunEvidenceConflict("hold_id 已用于不同保留请求")
            return self._hold_result(existing, idempotent_replay=True)

        now_naive = _utc_naive(self._now)
        hold = RunLedgerLegalHold(
            hold_id=normalized_hold_id,
            run_id=snapshot.run_id,
            reason_code=normalized_reason,
            placed_by=principal.principal_id,
            placed_at=now_naive,
            released_by="",
            released_at=None,
        )
        self._db.add(hold)
        self._add_audit(
            action="place_run_evidence_legal_hold",
            run_id=snapshot.run_id,
            actor=principal.principal_id,
            detail={
                "hold_id_sha256": sha256_text(normalized_hold_id),
                "reason_code": normalized_reason,
            },
            ip_address=ip_address,
        )
        try:
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return self._hold_result(hold, idempotent_replay=False)

    @staticmethod
    def _hold_result(
        hold: RunLedgerLegalHold,
        *,
        idempotent_replay: bool,
    ) -> dict[str, object]:
        return {
            "hold_id": str(hold.hold_id),
            "run_id_sha256": sha256_text(str(hold.run_id)),
            "reason_code": str(hold.reason_code),
            "active": hold.released_at is None,
            "placed_at": _utc_aware(hold.placed_at).isoformat(),
            "released_at": (
                _utc_aware(hold.released_at).isoformat()
                if hold.released_at is not None
                else None
            ),
            "idempotent_replay": idempotent_replay,
        }

    def release_legal_hold(
        self,
        *,
        run_id: str,
        hold_id: str,
        principal: RunEvidencePrincipal,
        ip_address: str = "",
    ) -> dict[str, object]:
        self._require_admin(principal)
        snapshot = self._load_snapshot(run_id, principal)
        normalized_hold_id = require_run_evidence_identifier(hold_id, "hold_id")
        hold = self._db.get(RunLedgerLegalHold, normalized_hold_id)
        if hold is None or str(hold.run_id) != snapshot.run_id:
            raise RunEvidenceNotFound("法律保留记录不存在")
        if hold.released_at is not None:
            return self._hold_result(hold, idempotent_replay=True)
        hold.released_by = principal.principal_id
        hold.released_at = _utc_naive(self._now)
        self._add_audit(
            action="release_run_evidence_legal_hold",
            run_id=snapshot.run_id,
            actor=principal.principal_id,
            detail={"hold_id_sha256": sha256_text(normalized_hold_id)},
            ip_address=ip_address,
        )
        try:
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return self._hold_result(hold, idempotent_replay=False)

    def erasure_preview(
        self,
        *,
        run_id: str,
        reason: RunEvidenceErasureReason | str,
        principal: RunEvidencePrincipal,
    ) -> dict[str, object]:
        if principal.role is RunEvidenceRole.SERVICE:
            raise RunEvidenceAccessDenied("Service 身份不能删除运行证据")
        manifest = self.export_manifest(run_id, principal)
        normalized_reason = require_erasure_allowed(manifest.retention, reason)
        return {
            "run_id": manifest.snapshot.run_id,
            "run_id_sha256": sha256_text(manifest.snapshot.run_id),
            "reason_code": normalized_reason.value,
            "expected_manifest_sha256": manifest.manifest_sha256,
            "ledger_event_count": (
                len(manifest.snapshot.ledger_view.records)
                if manifest.snapshot.ledger_view is not None
                else 0
            ),
            "legacy_counts": dict(manifest.legacy.counts),
            "retention": manifest.retention.to_dict(),
            "requires_exact_run_confirmation": True,
            "deletion_performed": False,
        }

    @staticmethod
    def _request_fingerprint(
        *,
        run_id: str,
        reason: RunEvidenceErasureReason,
        expected_manifest_sha256: str,
        requested_by: str,
    ) -> str:
        return canonical_json_sha256({
            "run_id_sha256": sha256_text(run_id),
            "reason_code": reason.value,
            "expected_manifest_sha256": expected_manifest_sha256,
            "requested_by": requested_by,
        })

    @staticmethod
    def _receipt_result(
        receipt: RunLedgerErasureReceipt,
        *,
        request_id: str,
        idempotent_replay: bool,
    ) -> RunEvidenceErasureResult:
        try:
            counts = json.loads(str(receipt.legacy_counts_json or "{}"))
        except json.JSONDecodeError as exc:
            raise RunEvidenceIntegrityError("运行证据删除回执损坏") from exc
        if not isinstance(counts, dict) or any(
            not isinstance(key, str)
            or type(value) is not int
            or value < 0
            for key, value in counts.items()
        ):
            raise RunEvidenceIntegrityError("运行证据删除回执计数无效")
        erased_at = _utc_aware(receipt.erased_at)
        if erased_at is None:
            raise RunEvidenceIntegrityError("运行证据删除回执缺少时间")
        return RunEvidenceErasureResult(
            receipt_id=str(receipt.receipt_id),
            request_id=request_id,
            run_id_sha256=str(receipt.run_id_sha256),
            manifest_sha256=str(receipt.manifest_sha256),
            ledger_event_count=int(receipt.ledger_event_count or 0),
            legacy_counts=immutable_mapping(counts),
            reason_code=str(receipt.reason_code),
            policy_version=str(receipt.policy_version),
            erased_at=erased_at,
            idempotent_replay=idempotent_replay,
        )

    def _existing_receipt(
        self,
        *,
        request_id: str,
        request_fingerprint: str,
        run_id: str,
    ) -> RunEvidenceErasureResult | None:
        receipt = (
            self._db.query(RunLedgerErasureReceipt)
            .filter(
                RunLedgerErasureReceipt.request_id_sha256
                == sha256_text(request_id)
            )
            .one_or_none()
        )
        if receipt is None:
            return None
        if (
            not compare_digest(
                str(receipt.request_fingerprint_sha256),
                request_fingerprint,
            )
            or not compare_digest(str(receipt.run_id_sha256), sha256_text(run_id))
        ):
            raise RunEvidenceConflict("request_id 已用于不同删除请求")
        return self._receipt_result(
            receipt,
            request_id=request_id,
            idempotent_replay=True,
        )

    def _delete_legacy_evidence(
        self,
        run_id: str,
        expected_counts: Mapping[str, int],
    ) -> None:
        deletion_order = tuple(reversed(_LEGACY_EVIDENCE_MODELS))
        for table_name, model in deletion_order:
            deleted = (
                self._db.query(model)
                .filter(model.run_id == run_id)
                .delete(synchronize_session=False)
            )
            if int(deleted or 0) != int(expected_counts[table_name]):
                raise RunEvidenceConflict("旧运行证据在删除前发生变化")

    def erase(
        self,
        *,
        run_id: str,
        request_id: str,
        confirm_run_id: str,
        reason: RunEvidenceErasureReason | str,
        expected_manifest_sha256: str,
        principal: RunEvidencePrincipal,
        ip_address: str = "",
    ) -> RunEvidenceErasureResult:
        """在一个事务中完整删除证据流，并留下不含业务正文的回执。"""

        if principal.role is RunEvidenceRole.SERVICE:
            raise RunEvidenceAccessDenied("Service 身份不能删除运行证据")
        normalized_run_id = require_run_evidence_identifier(run_id, "run_id")
        normalized_request_id = require_run_evidence_identifier(
            request_id,
            "request_id",
        )
        normalized_confirmation = require_run_evidence_identifier(
            confirm_run_id,
            "confirm_run_id",
        )
        if not compare_digest(normalized_run_id, normalized_confirmation):
            raise RunEvidenceConflict("Run 删除确认不匹配")
        normalized_reason = RunEvidenceErasureReason(reason)
        normalized_manifest_sha256 = require_sha256(
            expected_manifest_sha256,
            "expected_manifest_sha256",
        )
        request_fingerprint = self._request_fingerprint(
            run_id=normalized_run_id,
            reason=normalized_reason,
            expected_manifest_sha256=normalized_manifest_sha256,
            requested_by=principal.principal_id,
        )
        replay = self._existing_receipt(
            request_id=normalized_request_id,
            request_fingerprint=request_fingerprint,
            run_id=normalized_run_id,
        )
        if replay is not None:
            return replay

        initial_manifest = self.export_manifest(normalized_run_id, principal)
        require_erasure_allowed(initial_manifest.retention, normalized_reason)
        if not compare_digest(
            initial_manifest.manifest_sha256,
            normalized_manifest_sha256,
        ):
            raise RunEvidenceConflict("运行证据导出清单已变化")

        ledger_event_count = (
            len(initial_manifest.snapshot.ledger_view.records)
            if initial_manifest.snapshot.ledger_view is not None
            else 0
        )
        authorization: RunLedgerErasureAuthorization | None = None
        if ledger_event_count:
            authorization_now = datetime.now(timezone.utc)
            self._db.query(RunLedgerErasureAuthorization).filter(
                RunLedgerErasureAuthorization.run_id == normalized_run_id,
                RunLedgerErasureAuthorization.expires_at
                <= _utc_naive(authorization_now),
            ).delete(synchronize_session=False)
            authorization = RunLedgerErasureAuthorization(
                authorization_id=f"erase-auth-{uuid.uuid4().hex}",
                run_id=normalized_run_id,
                expected_event_count=ledger_event_count,
                requested_by=principal.principal_id,
                created_at=_utc_naive(authorization_now),
                expires_at=_utc_naive(authorization_now + timedelta(minutes=5)),
            )
            self._db.add(authorization)
            try:
                self._db.flush()
            except IntegrityError as exc:
                self._db.rollback()
                raise RunEvidenceConflict("运行证据已有删除操作正在执行") from exc

        try:
            locked_manifest = self.export_manifest(normalized_run_id, principal)
            require_erasure_allowed(locked_manifest.retention, normalized_reason)
            if not compare_digest(
                locked_manifest.manifest_sha256,
                normalized_manifest_sha256,
            ):
                raise RunEvidenceConflict("运行证据导出清单已变化")
            locked_event_count = (
                len(locked_manifest.snapshot.ledger_view.records)
                if locked_manifest.snapshot.ledger_view is not None
                else 0
            )
            if locked_event_count != ledger_event_count:
                raise RunEvidenceConflict("运行证据事件数量已变化")

            self._delete_legacy_evidence(
                normalized_run_id,
                locked_manifest.legacy.counts,
            )
            self._db.query(RunLedgerLegalHold).filter(
                RunLedgerLegalHold.run_id == normalized_run_id,
                RunLedgerLegalHold.released_at.is_not(None),
            ).delete(synchronize_session=False)

            terminal_event_sha256 = ""
            if locked_manifest.snapshot.ledger_view is not None:
                terminal_event_sha256 = next(
                    record.event_sha256
                    for record in locked_manifest.snapshot.ledger_view.records
                    if record.event_type == "run.terminated"
                )
                deleted_events = self._db.query(RunLedgerEventRow).filter(
                    RunLedgerEventRow.run_id == normalized_run_id,
                ).delete(synchronize_session=False)
                if int(deleted_events or 0) != ledger_event_count:
                    raise RunEvidenceConflict("Run Ledger 未被完整删除")
                deleted_heads = self._db.query(RunLedgerStreamHead).filter(
                    RunLedgerStreamHead.run_id == normalized_run_id,
                ).delete(synchronize_session=False)
                if int(deleted_heads or 0) != 1:
                    raise RunEvidenceConflict("Run Ledger 协调头删除失败")
            if authorization is not None:
                self._db.delete(authorization)

            erased_at = _utc_naive(self._now)
            receipt = RunLedgerErasureReceipt(
                receipt_id=f"erase-receipt-{uuid.uuid4().hex}",
                request_id_sha256=sha256_text(normalized_request_id),
                request_fingerprint_sha256=request_fingerprint,
                run_id_sha256=sha256_text(normalized_run_id),
                manifest_sha256=normalized_manifest_sha256,
                terminal_event_sha256=terminal_event_sha256,
                ledger_event_count=ledger_event_count,
                legacy_counts_json=json.dumps(
                    dict(locked_manifest.legacy.counts),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                reason_code=normalized_reason.value,
                requested_by=principal.principal_id,
                policy_version=self._policy.policy_version,
                erased_at=erased_at,
            )
            self._db.add(receipt)
            self._add_audit(
                action="erase_run_evidence",
                run_id=normalized_run_id,
                actor=principal.principal_id,
                detail={
                    "receipt_id": receipt.receipt_id,
                    "manifest_sha256": normalized_manifest_sha256,
                    "ledger_event_count": ledger_event_count,
                    "legacy_counts": dict(locked_manifest.legacy.counts),
                    "reason_code": normalized_reason.value,
                    "policy_version": self._policy.policy_version,
                },
                ip_address=ip_address,
            )
            self._db.commit()
            return self._receipt_result(
                receipt,
                request_id=normalized_request_id,
                idempotent_replay=False,
            )
        except Exception:
            self._db.rollback()
            recovered = self._existing_receipt(
                request_id=normalized_request_id,
                request_fingerprint=request_fingerprint,
                run_id=normalized_run_id,
            )
            if recovered is not None:
                return recovered
            raise

__all__ = [
    "LegacyRunEvidenceSummary",
    "RunEvidenceErasureResult",
    "RunEvidenceGovernanceService",
    "RunEvidenceManifest",
    "RunEvidenceSnapshot",
    "load_run_evidence_retention_policy",
]
