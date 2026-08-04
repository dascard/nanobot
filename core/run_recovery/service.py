"""Checkpoint 读取、恢复前检、lineage 创建与实际继续执行。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from hmac import compare_digest
from typing import Any, Protocol

from sqlalchemy.orm import Session

from core.agent_runtime import (
    AgentRuntimeAmbiguousError,
    AgentRuntimePort,
    AgentTurnRequest,
    AgentTurnResult,
    RuntimeCheckpointBoundary,
    RuntimeCheckpointReference,
    RuntimeCapability,
    RuntimeLifecycleState,
    RuntimeModelRoute,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
    RuntimeRecoveryOperationKind,
    RuntimeRunEvent,
    RuntimeRunEventHandler,
    RuntimeRunIdentity,
    RuntimeTurnKind,
    runtime_model_route_sha256,
)
from core.db.models import (
    AgentRun,
    Asset,
    RunCheckpointRow,
    RunRecoveryOperation,
    RunSideEffectReceipt,
    WorkspaceAsset,
)
from core.run_ledger import (
    RunLedgerEventDraft,
    RunLedgerIdentity,
    load_authoritative_run_view,
)
from core.run_ledger.adapters import (
    run_status_changed_event,
    run_terminated_event,
)
from core.run_ledger.persistence import SqlAlchemyRunEventLedger
from core.run_recovery.contracts import (
    RUN_CHECKPOINT_PAYLOAD_ENCODING,
    RUN_CHECKPOINT_SCHEMA_VERSION,
    RunCheckpointState,
    RunRecoveryAccessDenied,
    RunRecoveryArtifactProof,
    RunRecoveryConflict,
    RunRecoveryFileProof,
    RunRecoveryIntegrityError,
    RunRecoveryNotFound,
    RunRecoveryPreflight,
    RunRecoveryPreflightDenied,
    RunRecoveryPreparedOperation,
    canonical_sha256,
    checkpoint_document_artifact_proofs,
    checkpoint_document_file_proofs,
    checkpoint_document_identity,
    checkpoint_document_messages,
    checkpoint_document_model_route,
    checkpoint_document_plans,
    checkpoint_state_document,
    decode_checkpoint_document,
    encode_checkpoint_document,
    sha256_text,
    version_proof_mapping,
)
from core.telemetry.contracts import TelemetryCorrelation


class RunRecoveryFileVerifier(Protocol):
    def verify(self, proof: RunRecoveryFileProof) -> bool: ...


def _utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RunRecoveryIntegrityError(f"Checkpoint {name} 无效")
    return value


def _identity(identity: RuntimeRunIdentity) -> RunLedgerIdentity:
    return RunLedgerIdentity(
        actor_type=identity.actor.actor_type.value,
        actor_id=identity.actor.actor_id,
        parent_actor_id=identity.actor.parent_actor_id,
        owner_platform=identity.owner.platform,
        owner_type=identity.owner.owner_type.value,
        owner_id=identity.owner.owner_id,
    )


def _correlation(identity: RuntimeRunIdentity) -> TelemetryCorrelation:
    return TelemetryCorrelation(
        request_id=identity.run_id,
        turn_id=identity.turn_id,
        trace_id=identity.correlation_id,
        run_id=identity.run_id,
    )


def _reference(row: RunCheckpointRow) -> RuntimeCheckpointReference:
    return RuntimeCheckpointReference(
        checkpoint_id=str(row.checkpoint_id),
        run_id=str(row.run_id),
        sequence=int(row.sequence),
        boundary=RuntimeCheckpointBoundary(str(row.boundary)),
        payload_sha256=str(row.payload_sha256),
        resumable=bool(row.resumable),
    )


def _owner_tuple(principal: RuntimePrincipal) -> tuple[str, str, str]:
    return principal.platform, principal.owner_type.value, principal.owner_id


def _row_owner(row: RunCheckpointRow) -> tuple[str, str, str]:
    return str(row.owner_platform), str(row.owner_type), str(row.owner_id)


def _plan_map(plans: Sequence[RuntimePlanRef]) -> dict[str, str]:
    return {item.kind.value: item.sha256 for item in plans}


def _is_sha256(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    return len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )


def _noop_handler(_event: RuntimeRunEvent) -> None:
    return None


class SqlAlchemyRunRecoveryService:
    """读取权威 Checkpoint，并以新 Run 执行 Resume/Fork/Rewind。"""

    def __init__(
        self,
        db: Session,
        *,
        file_verifier: RunRecoveryFileVerifier | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        self._db = db
        self._file_verifier = file_verifier
        self._now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _authorize(
        row: RunCheckpointRow,
        principal: RuntimePrincipal,
        *,
        admin: bool,
    ) -> None:
        if not admin and _row_owner(row) != _owner_tuple(principal):
            raise RunRecoveryAccessDenied("当前身份无权访问 Checkpoint")

    def list_checkpoints(
        self,
        run_id: str,
        principal: RuntimePrincipal,
        *,
        admin: bool = False,
    ) -> tuple[RuntimeCheckpointReference, ...]:
        rows = (
            self._db.query(RunCheckpointRow)
            .filter(RunCheckpointRow.run_id == str(run_id or ""))
            .order_by(RunCheckpointRow.sequence.asc())
            .all()
        )
        if not rows:
            raise RunRecoveryNotFound("Run 没有可用 Checkpoint")
        self._authorize(rows[0], principal, admin=admin)
        if any(_row_owner(row) != _row_owner(rows[0]) for row in rows):
            raise RunRecoveryIntegrityError("同一 Run 的 Checkpoint owner 不一致")
        return tuple(_reference(row) for row in rows)

    def _load_row(self, checkpoint_id: str) -> RunCheckpointRow:
        row = self._db.get(RunCheckpointRow, str(checkpoint_id or ""))
        if row is None:
            raise RunRecoveryNotFound("Checkpoint 不存在")
        return row

    def load_checkpoint(
        self,
        checkpoint_id: str,
        principal: RuntimePrincipal,
        *,
        admin: bool = False,
    ) -> RunCheckpointState:
        row = self._load_row(checkpoint_id)
        self._authorize(row, principal, admin=admin)
        if (
            int(row.schema_version) != RUN_CHECKPOINT_SCHEMA_VERSION
            or str(row.payload_encoding) != RUN_CHECKPOINT_PAYLOAD_ENCODING
            or int(row.payload_size_bytes) != len(bytes(row.payload_blob))
        ):
            raise RunRecoveryIntegrityError("Checkpoint 持久化合同不一致")
        document = decode_checkpoint_document(
            bytes(row.payload_blob),
            expected_payload_sha256=str(row.payload_sha256),
            expected_state_sha256=str(row.state_sha256),
        )
        identity = checkpoint_document_identity(document)
        if (
            identity.run_id != str(row.run_id)
            or identity.turn_id != str(row.turn_id)
            or identity.correlation_id != str(row.correlation_id)
            or _owner_tuple(identity.owner) != _row_owner(row)
        ):
            raise RunRecoveryIntegrityError("Checkpoint identity 与索引列不一致")
        plans = checkpoint_document_plans(document)
        proofs = version_proof_mapping(plans)
        expected_columns = {
            RuntimePlanKind.MANIFEST.value: str(row.manifest_sha256),
            RuntimePlanKind.PROMPT.value: str(row.prompt_sha256),
            RuntimePlanKind.MODEL.value: str(row.model_route_sha256),
            RuntimePlanKind.TOOL.value: str(row.tool_plan_sha256),
            RuntimePlanKind.WORKSPACE.value: str(row.workspace_sha256),
            RuntimePlanKind.ARTIFACT.value: str(row.artifact_set_sha256),
            RuntimePlanKind.SECURITY.value: str(row.security_sha256),
        }
        if any(proofs.get(key) != value for key, value in expected_columns.items()):
            raise RunRecoveryIntegrityError("Checkpoint 版本证明索引不一致")
        if canonical_sha256(dict(proofs)) != str(row.version_proofs_sha256):
            raise RunRecoveryIntegrityError("Checkpoint 版本证明摘要不一致")
        file_proofs = checkpoint_document_file_proofs(document)
        artifact_proofs = checkpoint_document_artifact_proofs(document)
        if canonical_sha256(
            [item.to_dict() for item in file_proofs]
        ) != str(row.file_proofs_sha256):
            raise RunRecoveryIntegrityError("Checkpoint 文件证明摘要不一致")
        if canonical_sha256(
            [item.to_dict() for item in artifact_proofs]
        ) != str(row.artifact_proofs_sha256):
            raise RunRecoveryIntegrityError("Checkpoint Artifact 证明摘要不一致")
        runtime = _mapping(document.get("runtime"), "runtime")
        progress = _mapping(document.get("progress"), "progress")
        if (
            str(runtime.get("runtime_id") or "") != str(row.runtime_id)
            or str(runtime.get("protocol_version") or "")
            != str(row.runtime_protocol_version)
            or int(progress.get("model_step") or 0) != int(row.model_step)
            or int(progress.get("tool_round") or 0) != int(row.tool_round)
            or int(document.get("side_effect_frontier") or 0)
            != int(row.side_effect_frontier)
            or bool(document.get("resumable")) != bool(row.resumable)
            or str(document.get("boundary") or "") != str(row.boundary)
        ):
            raise RunRecoveryIntegrityError("Checkpoint 状态索引不一致")
        ledger = SqlAlchemyRunEventLedger(self._db)
        event = ledger.get(f"checkpoint:{row.checkpoint_id}")
        expected_checkpoint_event = (
            "run.checkpoint_restored"
            if str(row.boundary) == RuntimeCheckpointBoundary.RESTORED.value
            else "run.checkpoint_saved"
        )
        if (
            event is None
            or event.run_id != str(row.run_id)
            or event.sequence != int(row.ledger_sequence)
            or event.event_sha256 != str(row.ledger_event_sha256)
            or event.event_type != expected_checkpoint_event
            or str(event.payload.get("checkpoint_sha256") or "")
            != str(row.payload_sha256)
        ):
            raise RunRecoveryIntegrityError("Checkpoint 缺少对应 Ledger 事实")
        created_at = _utc_aware(row.created_at)
        return RunCheckpointState(
            reference=_reference(row),
            identity=identity,
            runtime_id=str(row.runtime_id),
            runtime_protocol_version=str(row.runtime_protocol_version),
            messages=checkpoint_document_messages(document),
            plans=plans,
            model_route=checkpoint_document_model_route(document),
            model_step=int(row.model_step),
            tool_round=int(row.tool_round),
            file_proofs=file_proofs,
            artifact_proofs=artifact_proofs,
            side_effect_receipt_ids=tuple(
                str(item)
                for item in document.get("side_effect_receipt_ids", ())
            ),
            side_effect_frontier=int(row.side_effect_frontier),
            state_sha256=str(row.state_sha256),
            created_at=created_at,
        )

    def _validate_artifacts(
        self,
        proofs: Sequence[RunRecoveryArtifactProof],
    ) -> tuple[str, ...]:
        blockers: list[str] = []
        for proof in proofs:
            asset = self._db.get(Asset, proof.sha256)
            link = (
                self._db.query(WorkspaceAsset)
                .filter(
                    WorkspaceAsset.workspace_id == proof.workspace_id,
                    WorkspaceAsset.asset_sha256 == proof.sha256,
                )
                .first()
            )
            if (
                asset is None
                or link is None
                or int(asset.size_bytes) != proof.size_bytes
                or str(asset.media_type).lower() != proof.media_type
            ):
                blockers.append(f"artifact_drift:{proof.sha256[:12]}")
        return tuple(blockers)

    def _validate_files(
        self,
        proofs: Sequence[RunRecoveryFileProof],
    ) -> tuple[str, ...]:
        if not proofs:
            return ()
        if self._file_verifier is None:
            return ("file_verifier_unavailable",)
        blockers: list[str] = []
        for proof in proofs:
            try:
                valid = bool(self._file_verifier.verify(proof))
            except Exception:
                valid = False
            if not valid:
                blockers.append(
                    "file_drift:"
                    + hashlib.sha256(
                        proof.virtual_path.encode("utf-8")
                    ).hexdigest()[:12]
                )
        return tuple(blockers)

    @staticmethod
    def _receipt_proofs(
        row: RunSideEffectReceipt,
    ) -> tuple[
        tuple[RunRecoveryFileProof, ...],
        tuple[RunRecoveryArtifactProof, ...],
    ]:
        try:
            raw_files = json.loads(str(row.file_proofs_json or "[]"))
            raw_artifacts = json.loads(str(row.artifact_proofs_json or "[]"))
            if not isinstance(raw_files, list) or not isinstance(
                raw_artifacts,
                list,
            ):
                raise TypeError("proof document 必须是数组")
            file_proofs = tuple(
                RunRecoveryFileProof(
                    workspace_id=str(item["workspace_id"]),
                    virtual_path=str(item["virtual_path"]),
                    sha256=str(item["sha256"]),
                    exists=item.get("exists") is True,
                )
                for item in raw_files
                if isinstance(item, Mapping)
            )
            artifact_proofs = tuple(
                RunRecoveryArtifactProof(
                    workspace_id=str(item["workspace_id"]),
                    artifact_id=str(item["artifact_id"]),
                    sha256=str(item["sha256"]),
                    size_bytes=int(item.get("size_bytes") or 0),
                    media_type=str(
                        item.get("media_type") or "application/octet-stream"
                    ),
                )
                for item in raw_artifacts
                if isinstance(item, Mapping)
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RunRecoveryIntegrityError(
                "副作用回执的文件或 Artifact 证明损坏"
            ) from exc
        if len(file_proofs) != len(raw_files) or len(artifact_proofs) != len(
            raw_artifacts
        ):
            raise RunRecoveryIntegrityError(
                "副作用回执的文件或 Artifact 证明类型无效"
            )
        return file_proofs, artifact_proofs

    def _validate_side_effect_receipts(
        self,
        state: RunCheckpointState,
        *,
        checkpoint_ledger_sequence: int,
    ) -> tuple[str, ...]:
        """把可变协调行逐条锚定到不可变 Ledger 和 Checkpoint。"""

        rows = (
            self._db.query(RunSideEffectReceipt)
            .filter(RunSideEffectReceipt.run_id == state.identity.run_id)
            .order_by(
                RunSideEffectReceipt.prepared_ledger_sequence.asc(),
                RunSideEffectReceipt.receipt_id.asc(),
            )
            .all()
        )
        ledger = SqlAlchemyRunEventLedger(self._db)
        checkpoint_file_proofs = {
            (item.workspace_id, item.virtual_path, item.sha256, item.exists)
            for item in state.file_proofs
        }
        checkpoint_artifact_proofs = {
            (
                item.workspace_id,
                item.artifact_id,
                item.sha256,
                item.size_bytes,
                item.media_type,
            )
            for item in state.artifact_proofs
        }
        checkpoint_receipt_ids: list[str] = []
        blockers: list[str] = []

        for row in rows:
            receipt_id = str(row.receipt_id or "")
            tool_call_id = str(row.tool_call_id or "")
            tool_name = str(row.tool_name or "")
            effect_class = str(row.effect_class or "")
            state_value = str(row.state or "")
            prepared_sequence = int(row.prepared_ledger_sequence or 0)
            if (
                not receipt_id
                or not tool_call_id
                or not tool_name
                or effect_class not in {"local_write", "external"}
                or state_value
                not in {"prepared", "completed", "failed", "ambiguous"}
                or not _is_sha256(row.idempotency_key_sha256)
                or not _is_sha256(row.request_sha256)
                or prepared_sequence <= 0
            ):
                raise RunRecoveryIntegrityError("副作用回执索引字段无效")

            before = self._db.get(
                RunCheckpointRow,
                str(row.checkpoint_before_id or ""),
            )
            prepared = ledger.get(f"side-effect:{receipt_id}:prepared")
            if (
                before is None
                or str(before.run_id) != state.identity.run_id
                or int(before.ledger_sequence) >= prepared_sequence
                or prepared is None
                or prepared.run_id != state.identity.run_id
                or prepared.sequence != prepared_sequence
                or prepared.event_type != "tool.side_effect_prepared"
                or prepared.status != "prepared"
                or prepared.event.correlation.tool_call_id != tool_call_id
                or str(prepared.payload.get("receipt_id") or "") != receipt_id
                or str(prepared.payload.get("checkpoint_id") or "")
                != str(row.checkpoint_before_id)
                or str(prepared.payload.get("tool_call_id") or "")
                != tool_call_id
                or str(prepared.payload.get("tool_name") or "") != tool_name
                or str(prepared.payload.get("effect_class") or "")
                != effect_class
                or str(prepared.payload.get("request_sha256") or "")
                != str(row.request_sha256)
            ):
                raise RunRecoveryIntegrityError(
                    "副作用 prepared 回执与 Ledger 事实不一致"
                )

            if prepared_sequence < checkpoint_ledger_sequence:
                checkpoint_receipt_ids.append(receipt_id)
            else:
                blockers.append("side_effect_after_checkpoint")

            if state_value == "prepared":
                if (
                    row.terminal_ledger_sequence is not None
                    or str(row.result_sha256 or "")
                    or int(row.result_size_bytes or 0) != 0
                    or row.settled_at is not None
                    or str(row.checkpoint_after_id or "")
                ):
                    raise RunRecoveryIntegrityError(
                        "prepared 副作用回执携带了终态字段"
                    )
                blockers.append("side_effect_ambiguous")
                continue

            terminal_sequence = int(row.terminal_ledger_sequence or 0)
            terminal = ledger.get(f"side-effect:{receipt_id}:{state_value}")
            file_proofs, artifact_proofs = self._receipt_proofs(row)
            if (
                terminal_sequence <= prepared_sequence
                or not _is_sha256(row.result_sha256)
                or int(row.result_size_bytes or 0) <= 0
                or row.settled_at is None
                or terminal is None
                or terminal.run_id != state.identity.run_id
                or terminal.sequence != terminal_sequence
                or terminal.event_type != f"tool.side_effect_{state_value}"
                or terminal.status != state_value
                or terminal.event.correlation.tool_call_id != tool_call_id
                or str(terminal.payload.get("receipt_id") or "") != receipt_id
                or str(terminal.payload.get("tool_call_id") or "")
                != tool_call_id
                or str(terminal.payload.get("tool_name") or "") != tool_name
                or str(terminal.payload.get("effect_class") or "")
                != effect_class
                or str(terminal.payload.get("result_sha256") or "")
                != str(row.result_sha256)
                or int(terminal.payload.get("file_proof_count") or 0)
                != len(file_proofs)
                or int(terminal.payload.get("artifact_proof_count") or 0)
                != len(artifact_proofs)
                or str(terminal.payload.get("file_proofs_sha256") or "")
                != canonical_sha256(
                    [item.to_dict() for item in file_proofs]
                )
                or str(terminal.payload.get("artifact_proofs_sha256") or "")
                != canonical_sha256(
                    [item.to_dict() for item in artifact_proofs]
                )
                or str(terminal.payload.get("error_code") or "")
                != str(row.error_code or "")
                or terminal.event.identity.owner_platform
                != state.identity.owner.platform
                or terminal.event.identity.owner_type
                != state.identity.owner.owner_type.value
                or terminal.event.identity.owner_id
                != state.identity.owner.owner_id
            ):
                raise RunRecoveryIntegrityError(
                    "副作用终态回执与 Ledger 事实不一致"
                )

            if state_value == "ambiguous":
                blockers.append("side_effect_ambiguous")
            if terminal_sequence >= checkpoint_ledger_sequence:
                blockers.append("side_effect_after_checkpoint")
                continue

            after = self._db.get(
                RunCheckpointRow,
                str(row.checkpoint_after_id or ""),
            )
            if (
                after is None
                or str(after.run_id) != state.identity.run_id
                or int(after.ledger_sequence) <= terminal_sequence
                or int(after.ledger_sequence) > checkpoint_ledger_sequence
            ):
                raise RunRecoveryIntegrityError(
                    "副作用终态缺少后置 Checkpoint 锚点"
                )
            if any(
                (
                    item.workspace_id,
                    item.virtual_path,
                    item.sha256,
                    item.exists,
                )
                not in checkpoint_file_proofs
                for item in file_proofs
            ) or any(
                (
                    item.workspace_id,
                    item.artifact_id,
                    item.sha256,
                    item.size_bytes,
                    item.media_type,
                )
                not in checkpoint_artifact_proofs
                for item in artifact_proofs
            ):
                raise RunRecoveryIntegrityError(
                    "Checkpoint 未包含副作用结果的版本证明"
                )

        if (
            tuple(checkpoint_receipt_ids) != state.side_effect_receipt_ids
            or len(checkpoint_receipt_ids) != state.side_effect_frontier
        ):
            raise RunRecoveryIntegrityError(
                "Checkpoint 副作用 frontier 与回执事实不一致"
            )
        return tuple(dict.fromkeys(blockers))

    def preflight(
        self,
        *,
        source_run_id: str,
        checkpoint_id: str,
        operation_kind: RuntimeRecoveryOperationKind | str,
        principal: RuntimePrincipal,
        current_runtime_id: str,
        current_runtime_protocol_version: str,
        current_plans: Sequence[RuntimePlanRef],
        current_model_route: RuntimeModelRoute,
        admin: bool = False,
    ) -> RunRecoveryPreflight:
        kind = RuntimeRecoveryOperationKind(operation_kind)
        state = self.load_checkpoint(checkpoint_id, principal, admin=admin)
        if state.identity.run_id != str(source_run_id or ""):
            raise RunRecoveryConflict("Checkpoint 不属于指定源 Run")
        ledger = SqlAlchemyRunEventLedger(self._db)
        view = load_authoritative_run_view(ledger, state.identity.run_id)
        if view is None:
            raise RunRecoveryIntegrityError("源 Run Ledger 不存在")
        blockers: list[str] = []
        if not view.projection.terminal:
            blockers.append("source_run_active")
        if not state.reference.resumable:
            blockers.append("checkpoint_not_resumable")
        latest = (
            self._db.query(RunCheckpointRow)
            .filter(RunCheckpointRow.run_id == state.identity.run_id)
            .order_by(RunCheckpointRow.sequence.desc())
            .first()
        )
        if (
            kind is RuntimeRecoveryOperationKind.RESUME
            and latest is not None
            and str(latest.checkpoint_id) != state.reference.checkpoint_id
        ):
            blockers.append("resume_requires_latest_checkpoint")
        checkpoint_ledger_sequence = int(
            self._load_row(state.reference.checkpoint_id).ledger_sequence
        )
        blockers.extend(self._validate_side_effect_receipts(
            state,
            checkpoint_ledger_sequence=checkpoint_ledger_sequence,
        ))
        if str(current_runtime_id) != state.runtime_id:
            blockers.append("runtime_version_drift")
        if (
            str(current_runtime_protocol_version)
            != state.runtime_protocol_version
        ):
            blockers.append("runtime_protocol_drift")
        if _plan_map(current_plans) != _plan_map(state.plans):
            blockers.append("version_proof_drift")
        if (
            state.model_route is None
            or runtime_model_route_sha256(current_model_route)
            != runtime_model_route_sha256(state.model_route)
        ):
            blockers.append("model_route_drift")
        blockers.extend(self._validate_files(state.file_proofs))
        blockers.extend(self._validate_artifacts(state.artifact_proofs))
        return RunRecoveryPreflight(
            allowed=not blockers,
            operation_kind=kind,
            source_run_id=state.identity.run_id,
            checkpoint_id=state.reference.checkpoint_id,
            source_head_sequence=view.head.last_sequence,
            source_head_sha256=view.head.last_event_sha256,
            blockers=tuple(dict.fromkeys(blockers)),
            warnings=("new_run_lineage",),
        )

    @staticmethod
    def _operation_fingerprint(
        *,
        request_id: str,
        kind: RuntimeRecoveryOperationKind,
        source_run_id: str,
        checkpoint_id: str,
        child_identity: RuntimeRunIdentity,
        source_head_sha256: str,
    ) -> str:
        return canonical_sha256({
            "request_id_sha256": sha256_text(request_id),
            "operation_kind": kind.value,
            "source_run_id_sha256": sha256_text(source_run_id),
            "checkpoint_id_sha256": sha256_text(checkpoint_id),
            "child_run_id": child_identity.run_id,
            "child_turn_id": child_identity.turn_id,
            "child_correlation_id": child_identity.correlation_id,
            "owner": child_identity.owner.canonical_id,
            "source_head_sha256": source_head_sha256,
        })

    @staticmethod
    def _prepared_result(
        row: RunRecoveryOperation,
        *,
        idempotent_replay: bool,
    ) -> RunRecoveryPreparedOperation:
        return RunRecoveryPreparedOperation(
            operation_id=str(row.operation_id),
            operation_kind=RuntimeRecoveryOperationKind(str(row.operation_kind)),
            child_run_id=str(row.run_id),
            restored_checkpoint_id=str(row.restored_checkpoint_id),
            status=str(row.status),
            idempotent_replay=idempotent_replay,
        )

    def prepare_operation(
        self,
        *,
        request_id: str,
        confirm_checkpoint_id: str,
        source_run_id: str,
        checkpoint_id: str,
        operation_kind: RuntimeRecoveryOperationKind | str,
        principal: RuntimePrincipal,
        child_identity: RuntimeRunIdentity,
        current_runtime_id: str,
        current_runtime_protocol_version: str,
        current_plans: Sequence[RuntimePlanRef],
        current_model_route: RuntimeModelRoute,
        admin: bool = False,
    ) -> RunRecoveryPreparedOperation:
        kind = RuntimeRecoveryOperationKind(operation_kind)
        if not compare_digest(str(checkpoint_id), str(confirm_checkpoint_id)):
            raise RunRecoveryConflict("Checkpoint 二次确认不匹配")
        if child_identity.run_id == source_run_id:
            raise RunRecoveryConflict("恢复必须创建新的子 Run")
        if _owner_tuple(child_identity.owner) != _owner_tuple(principal):
            raise RunRecoveryAccessDenied("子 Run owner 必须与请求主体一致")
        preview = self.preflight(
            source_run_id=source_run_id,
            checkpoint_id=checkpoint_id,
            operation_kind=kind,
            principal=principal,
            current_runtime_id=current_runtime_id,
            current_runtime_protocol_version=current_runtime_protocol_version,
            current_plans=current_plans,
            current_model_route=current_model_route,
            admin=admin,
        )
        if not preview.allowed:
            raise RunRecoveryPreflightDenied(
                "恢复前检拒绝：" + ", ".join(preview.blockers)
            )
        request_hash = sha256_text(request_id)
        fingerprint = self._operation_fingerprint(
            request_id=request_id,
            kind=kind,
            source_run_id=source_run_id,
            checkpoint_id=checkpoint_id,
            child_identity=child_identity,
            source_head_sha256=preview.source_head_sha256,
        )
        existing = (
            self._db.query(RunRecoveryOperation)
            .filter(RunRecoveryOperation.request_id_sha256 == request_hash)
            .one_or_none()
        )
        if existing is not None:
            if str(existing.request_fingerprint_sha256) != fingerprint:
                raise RunRecoveryConflict("request_id 已用于不同恢复操作")
            return self._prepared_result(existing, idempotent_replay=True)
        if self._db.get(AgentRun, child_identity.run_id) is not None:
            raise RunRecoveryConflict("child_run_id 已存在")

        source = self.load_checkpoint(checkpoint_id, principal, admin=admin)
        restored_checkpoint_id = f"checkpoint-{uuid.uuid4().hex}"
        operation_id = f"recovery-{uuid.uuid4().hex}"
        now = self._now()
        restored_document = checkpoint_state_document(
            identity=child_identity,
            boundary=RuntimeCheckpointBoundary.RESTORED,
            runtime_id=source.runtime_id,
            runtime_protocol_version=source.runtime_protocol_version,
            messages=source.messages,
            plans=source.plans,
            model_route=source.model_route,
            model_step=source.model_step,
            tool_round=source.tool_round,
            file_proofs=source.file_proofs,
            artifact_proofs=source.artifact_proofs,
            side_effect_receipt_ids=(),
            side_effect_frontier=0,
            resumable=True,
        )
        payload_blob, payload_sha256, state_sha256 = encode_checkpoint_document(
            restored_document
        )
        source_view = load_authoritative_run_view(
            SqlAlchemyRunEventLedger(self._db),
            source_run_id,
        )
        assert source_view is not None
        root_sha256 = sha256_text(source_run_id)
        for record in source_view.records:
            if record.event_type == "run.lineage_declared":
                candidate = str(record.payload.get("root_run_sha256") or "")
                if len(candidate) == 64:
                    root_sha256 = candidate
        accepted = RunLedgerEventDraft(
            event_id=f"run:{child_identity.run_id}:accepted",
            run_id=child_identity.run_id,
            event_type="run.accepted",
            occurred_at=now,
            source="run_recovery.service",
            correlation=_correlation(child_identity),
            identity=_identity(child_identity),
            status="accepted",
            payload={
                "run_type": "recovery",
                "operation_kind": kind.value,
                "source_run_sha256": sha256_text(source_run_id),
                "source_checkpoint_sha256": source.reference.payload_sha256,
                "input_bytes": 0,
                "input_chars": 0,
                "input_sha256": hashlib.sha256(b"").hexdigest(),
            },
        )
        ledger = SqlAlchemyRunEventLedger(self._db)
        ledger.append(accepted, expected_sequence=1)
        lineage = ledger.append(RunLedgerEventDraft(
            event_id=f"run:{child_identity.run_id}:lineage",
            run_id=child_identity.run_id,
            event_type="run.lineage_declared",
            occurred_at=now,
            source="run_recovery.service",
            correlation=_correlation(child_identity),
            identity=_identity(child_identity),
            status="prepared",
            payload={
                "operation_id": operation_id,
                "operation_kind": kind.value,
                "parent_run_sha256": sha256_text(source_run_id),
                "root_run_sha256": root_sha256,
                "source_checkpoint_sha256": source.reference.payload_sha256,
                "source_head_sha256": preview.source_head_sha256,
            },
        ), expected_sequence=2)
        del lineage
        restored_event = ledger.append(RunLedgerEventDraft(
            event_id=f"checkpoint:{restored_checkpoint_id}",
            run_id=child_identity.run_id,
            event_type="run.checkpoint_restored",
            occurred_at=now,
            source="run_recovery.service",
            correlation=_correlation(child_identity),
            identity=_identity(child_identity),
            status="resumable",
            payload={
                "checkpoint_id": restored_checkpoint_id,
                "checkpoint_sequence": 1,
                "boundary": RuntimeCheckpointBoundary.RESTORED.value,
                "checkpoint_sha256": payload_sha256,
                "state_sha256": state_sha256,
                "source_checkpoint_sha256": source.reference.payload_sha256,
                "version_proofs_sha256": canonical_sha256(
                    dict(version_proof_mapping(source.plans))
                ),
                "file_proof_count": len(source.file_proofs),
                "artifact_proof_count": len(source.artifact_proofs),
                "side_effect_count": 0,
                "resumable": True,
            },
        ), expected_sequence=3)
        proofs = version_proof_mapping(source.plans)
        self._db.add(RunCheckpointRow(
            checkpoint_id=restored_checkpoint_id,
            run_id=child_identity.run_id,
            sequence=1,
            schema_version=RUN_CHECKPOINT_SCHEMA_VERSION,
            boundary=RuntimeCheckpointBoundary.RESTORED.value,
            parent_checkpoint_id="",
            turn_id=child_identity.turn_id,
            correlation_id=child_identity.correlation_id,
            actor_type=child_identity.actor.actor_type.value,
            actor_id=child_identity.actor.actor_id,
            parent_actor_id=child_identity.actor.parent_actor_id,
            owner_platform=child_identity.owner.platform,
            owner_type=child_identity.owner.owner_type.value,
            owner_id=child_identity.owner.owner_id,
            runtime_id=source.runtime_id,
            runtime_protocol_version=source.runtime_protocol_version,
            resumable=True,
            model_step=source.model_step,
            tool_round=source.tool_round,
            side_effect_frontier=0,
            manifest_sha256=proofs[RuntimePlanKind.MANIFEST.value],
            prompt_sha256=proofs[RuntimePlanKind.PROMPT.value],
            model_route_sha256=proofs[RuntimePlanKind.MODEL.value],
            tool_plan_sha256=proofs[RuntimePlanKind.TOOL.value],
            workspace_sha256=proofs[RuntimePlanKind.WORKSPACE.value],
            artifact_set_sha256=proofs[RuntimePlanKind.ARTIFACT.value],
            security_sha256=proofs[RuntimePlanKind.SECURITY.value],
            version_proofs_sha256=canonical_sha256(dict(proofs)),
            file_proofs_sha256=canonical_sha256(
                [item.to_dict() for item in source.file_proofs]
            ),
            artifact_proofs_sha256=canonical_sha256(
                [item.to_dict() for item in source.artifact_proofs]
            ),
            payload_encoding=RUN_CHECKPOINT_PAYLOAD_ENCODING,
            payload_blob=payload_blob,
            payload_size_bytes=len(payload_blob),
            payload_sha256=payload_sha256,
            state_sha256=state_sha256,
            ledger_sequence=restored_event.sequence,
            ledger_event_sha256=restored_event.event_sha256,
            created_at=_utc_naive(now),
        ))
        operation = RunRecoveryOperation(
            operation_id=operation_id,
            request_id_sha256=request_hash,
            request_fingerprint_sha256=fingerprint,
            operation_kind=kind.value,
            run_id=child_identity.run_id,
            restored_checkpoint_id=restored_checkpoint_id,
            source_run_id_sha256=sha256_text(source_run_id),
            source_checkpoint_id_sha256=sha256_text(checkpoint_id),
            source_checkpoint_sha256=source.reference.payload_sha256,
            source_head_sequence=preview.source_head_sequence,
            source_head_sha256=preview.source_head_sha256,
            owner_platform=child_identity.owner.platform,
            owner_type=child_identity.owner.owner_type.value,
            owner_id=child_identity.owner.owner_id,
            status="prepared",
            error_code="",
            prepared_at=_utc_naive(now),
            updated_at=_utc_naive(now),
            finished_at=None,
        )
        self._db.add(operation)
        self._db.add(AgentRun(
            run_id=child_identity.run_id,
            trace_id=child_identity.correlation_id,
            session_id="recovery",
            user_id=(
                child_identity.owner.owner_id
                if child_identity.owner.owner_type.value == "user"
                else ""
            ),
            chat_type=child_identity.owner.owner_type.value,
            group_id=(
                child_identity.owner.owner_id
                if child_identity.owner.owner_type.value == "group"
                else ""
            ),
            run_type="recovery",
            prompt_mode="checkpoint",
            prompt_key=next(
                (
                    item.identity
                    for item in source.plans
                    if item.kind is RuntimePlanKind.PROMPT
                ),
                "",
            ),
            prompt_sha256=str(proofs[RuntimePlanKind.PROMPT.value]),
            model=(source.model_route.model_id if source.model_route else ""),
            status="accepted",
            input_preview="",
            output_preview="",
            error="",
            latency_ms=0,
            meta_json=json.dumps({
                "operation_id": operation_id,
                "operation_kind": kind.value,
                "source_run_sha256": sha256_text(source_run_id),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            started_at=_utc_naive(now),
            finished_at=None,
        ))
        try:
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return self._prepared_result(operation, idempotent_replay=False)

    def restore_into_runtime(
        self,
        *,
        operation_id: str,
        principal: RuntimePrincipal,
        runtime: AgentRuntimePort,
    ) -> RunCheckpointState:
        operation = self._db.get(RunRecoveryOperation, str(operation_id or ""))
        if operation is None:
            raise RunRecoveryNotFound("恢复操作不存在")
        if (
            str(operation.owner_platform),
            str(operation.owner_type),
            str(operation.owner_id),
        ) != _owner_tuple(principal):
            raise RunRecoveryAccessDenied("当前身份无权执行恢复操作")
        if str(operation.status) != "prepared":
            raise RunRecoveryConflict("恢复操作不再处于 prepared")
        state = self.load_checkpoint(
            str(operation.restored_checkpoint_id),
            principal,
        )
        if runtime.state is not RuntimeLifecycleState.RUNNING:
            raise RunRecoveryConflict("目标 Runtime 未运行")
        if runtime.runtime_id != state.runtime_id:
            raise RunRecoveryConflict("目标 Runtime 版本与 Checkpoint 不一致")
        capabilities = runtime.runtime_capabilities
        if capabilities.protocol_version != state.runtime_protocol_version:
            raise RunRecoveryConflict("目标 Runtime 协议与 Checkpoint 不一致")
        if not capabilities.supports(RuntimeCapability.CHECKPOINT_RECOVERY):
            raise RunRecoveryConflict("目标 Runtime 未声明安全恢复能力")
        if state.model_route is None:
            raise RunRecoveryIntegrityError("恢复 Checkpoint 缺少模型路由")
        current_state_blockers = (
            *self._validate_files(state.file_proofs),
            *self._validate_artifacts(state.artifact_proofs),
        )
        if current_state_blockers:
            raise RunRecoveryPreflightDenied(
                "恢复执行前状态漂移："
                + ", ".join(dict.fromkeys(current_state_blockers))
            )
        runtime.replace_conversation(state.messages)
        runtime.set_model_route(state.model_route)
        return state

    def _mark_running(
        self,
        operation: RunRecoveryOperation,
        identity: RuntimeRunIdentity,
    ) -> None:
        ledger = SqlAlchemyRunEventLedger(self._db)
        accepted = ledger.read(identity.run_id, limit=1)[0].event
        ledger.append(run_status_changed_event(
            accepted_event=accepted,
            status="running",
            previous_status="accepted",
        ))
        operation.status = "running"
        operation.updated_at = _utc_naive(self._now())
        legacy = self._db.get(AgentRun, identity.run_id)
        if legacy is None:
            raise RunRecoveryIntegrityError("恢复子 Run 兼容投影不存在")
        legacy.status = "running"
        self._db.commit()

    def _mark_terminal(
        self,
        operation_id: str,
        identity: RuntimeRunIdentity,
        *,
        status: str,
        result: AgentTurnResult | None,
        error: BaseException | None,
    ) -> None:
        operation = self._db.get(RunRecoveryOperation, operation_id)
        if operation is None or str(operation.status) != "running":
            raise RunRecoveryIntegrityError("恢复操作终态前状态无效")
        now = self._now()
        output_value = result.raw_result if result is not None else ""
        error_value = str(error or "")
        route_row = self._db.get(AgentRun, identity.run_id)
        model = str(route_row.model if route_row is not None else "")
        SqlAlchemyRunEventLedger(self._db).append(run_terminated_event(
            run_id=identity.run_id,
            trace_id=identity.correlation_id,
            session_id="recovery",
            status=status,
            output_value=output_value,
            error_value=error_value,
            latency_ms=0,
            model=model,
            occurred_at=now,
        ))
        operation.status = status
        operation.error_code = str(getattr(error, "code", "") or "")[:128]
        operation.updated_at = _utc_naive(now)
        operation.finished_at = _utc_naive(now)
        if route_row is not None:
            route_row.status = status
            route_row.output_preview = ""
            route_row.error = error_value[:1000]
            route_row.finished_at = _utc_naive(now)
        self._db.commit()

    async def execute_prepared(
        self,
        *,
        operation_id: str,
        principal: RuntimePrincipal,
        runtime: AgentRuntimePort,
        request: AgentTurnRequest,
        handler: RuntimeRunEventHandler = _noop_handler,
    ) -> AgentTurnResult:
        """恢复状态并真实执行子 Run；不是预览或 shadow replay。"""

        operation = self._db.get(RunRecoveryOperation, str(operation_id or ""))
        if operation is None:
            raise RunRecoveryNotFound("恢复操作不存在")
        state = self.restore_into_runtime(
            operation_id=operation_id,
            principal=principal,
            runtime=runtime,
        )
        if (
            request.kind is not RuntimeTurnKind.CONTINUE
            or request.content not in ("", None)
            or request.context.run_id != str(operation.run_id)
            or _owner_tuple(request.context.principal) != _owner_tuple(principal)
            or _plan_map(request.context.plans) != _plan_map(state.plans)
        ):
            raise RunRecoveryConflict("恢复继续请求与冻结 Checkpoint 不一致")
        self._mark_running(operation, request.context.execution_identity())
        result: AgentTurnResult | None = None
        failure: BaseException | None = None
        status = "succeeded"
        try:
            result = await runtime.run_event(request, handler)
            return result
        except asyncio.CancelledError as exc:
            status = "cancelled"
            failure = exc
            raise
        except TimeoutError as exc:
            status = "timed_out"
            failure = exc
            raise
        except AgentRuntimeAmbiguousError as exc:
            status = "ambiguous"
            failure = exc
            raise
        except BaseException as exc:
            status = "failed"
            failure = exc
            raise
        finally:
            self._mark_terminal(
                operation_id,
                request.context.execution_identity(),
                status=status,
                result=result,
                error=failure,
            )


__all__ = [
    "RunRecoveryFileVerifier",
    "SqlAlchemyRunRecoveryService",
]
