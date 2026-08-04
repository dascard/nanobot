"""Checkpoint 与副作用回执的 SQLAlchemy 权威协调器。"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.agent_runtime import (
    RuntimeCheckpointBoundary,
    RuntimeCheckpointCapture,
    RuntimeCheckpointReference,
    RuntimePlanKind,
    RuntimeRecoveryPort,
    RuntimeSideEffectGuard,
    RuntimeSideEffectReceiptReference,
    RuntimeSideEffectState,
    RuntimeToolCall,
    RuntimeToolEffectClass,
    RuntimeToolExecutionResult,
    runtime_model_route_sha256,
)
from core.db.models import (
    Asset,
    RunCheckpointRow,
    RunSideEffectReceipt,
)
from core.run_ledger import (
    RunLedgerAuthorityError,
    RunLedgerConflictError,
    RunLedgerEventDraft,
    RunLedgerIdentity,
)
from core.run_ledger.persistence import SqlAlchemyRunEventLedger
from core.run_recovery.contracts import (
    RUN_CHECKPOINT_PAYLOAD_ENCODING,
    RUN_CHECKPOINT_SCHEMA_VERSION,
    RunRecoveryArtifactProof,
    RunRecoveryConflict,
    RunRecoveryFileProof,
    RunRecoveryIntegrityError,
    canonical_json_bytes,
    canonical_sha256,
    checkpoint_document_artifact_proofs,
    checkpoint_document_file_proofs,
    checkpoint_state_document,
    decode_checkpoint_document,
    encode_checkpoint_document,
    sanitize_checkpoint_value,
    version_proof_mapping,
)
from core.sqlite_retry import run_sqlite_locked_retry
from core.telemetry.contracts import TelemetryCorrelation


logger = logging.getLogger("nanobot.run_recovery")

_REQUIRED_VERSION_PROOFS = frozenset({
    RuntimePlanKind.MANIFEST,
    RuntimePlanKind.PROMPT,
    RuntimePlanKind.MODEL,
    RuntimePlanKind.TOOL,
    RuntimePlanKind.WORKSPACE,
    RuntimePlanKind.ARTIFACT,
    RuntimePlanKind.SECURITY,
})


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _event_identity(capture: RuntimeCheckpointCapture) -> RunLedgerIdentity:
    identity = capture.identity
    return RunLedgerIdentity(
        actor_type=identity.actor.actor_type.value,
        actor_id=identity.actor.actor_id,
        parent_actor_id=identity.actor.parent_actor_id,
        owner_platform=identity.owner.platform,
        owner_type=identity.owner.owner_type.value,
        owner_id=identity.owner.owner_id,
    )


def _event_correlation(
    capture: RuntimeCheckpointCapture,
    *,
    tool_call_id: str = "",
) -> TelemetryCorrelation:
    identity = capture.identity
    return TelemetryCorrelation(
        request_id=identity.run_id,
        turn_id=identity.turn_id,
        trace_id=identity.correlation_id,
        run_id=identity.run_id,
        tool_call_id=tool_call_id,
    )


def _plan(capture: RuntimeCheckpointCapture, kind: RuntimePlanKind):
    return next((item for item in capture.plans if item.kind is kind), None)


def _workspace_id_from_plans(plans: Sequence[Any]) -> str:
    reference = next(
        (item for item in plans if item.kind is RuntimePlanKind.WORKSPACE),
        None,
    )
    if reference is None:
        return ""
    identity = str(reference.identity or "")
    prefix = "workspace:"
    return identity[len(prefix):] if identity.startswith(prefix) else ""


def _is_sha256(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    return len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return value
    return ()


def _proofs_from_result(
    result: RuntimeToolExecutionResult | None,
    *,
    workspace_id: str,
    db: Session | None = None,
) -> tuple[tuple[RunRecoveryFileProof, ...], tuple[RunRecoveryArtifactProof, ...]]:
    if result is None or not result.success or not workspace_id:
        return (), ()
    output = _mapping(result.output)
    data = _mapping(output.get("data"))
    file_values: dict[str, RunRecoveryFileProof] = {}

    def add_file(path: object, digest: object) -> None:
        if not _is_sha256(digest):
            return
        try:
            proof = RunRecoveryFileProof(
                workspace_id=workspace_id,
                virtual_path=str(path or ""),
                sha256=str(digest).lower(),
            )
        except ValueError:
            return
        file_values[proof.virtual_path] = proof

    if result.tool_call.name == "workspace_write":
        arguments = _mapping(result.tool_call.arguments)
        content = str(arguments.get("content") or "").encode("utf-8")
        add_file(
            data.get("path") or arguments.get("path"),
            data.get("sha256") or hashlib.sha256(content).hexdigest(),
        )
    else:
        add_file(
            data.get("path"),
            data.get("new_sha256") or data.get("sha256"),
        )
        for item in _sequence(data.get("files")):
            row = _mapping(item)
            add_file(
                row.get("path"),
                row.get("new_sha256") or row.get("sha256"),
            )

    artifact_values: dict[str, RunRecoveryArtifactProof] = {}
    candidates = list(_sequence(output.get("artifacts")))
    if data.get("ref"):
        candidates.append(data)
    prefix = "asset://sha256/"
    for item in candidates:
        row = _mapping(item)
        reference = str(row.get("ref") or "")
        if not reference.startswith(prefix):
            continue
        digest = reference[len(prefix):].lower()
        if not _is_sha256(digest):
            continue
        asset = db.get(Asset, digest) if db is not None else None
        try:
            proof = RunRecoveryArtifactProof(
                workspace_id=workspace_id,
                artifact_id=reference,
                sha256=digest,
                size_bytes=int(
                    getattr(asset, "size_bytes", row.get("size_bytes") or 0)
                ),
                media_type=str(
                    getattr(
                        asset,
                        "media_type",
                        row.get("media_type") or "application/octet-stream",
                    )
                ),
            )
        except (TypeError, ValueError):
            continue
        artifact_values[proof.artifact_id] = proof
    return (
        tuple(file_values[key] for key in sorted(file_values)),
        tuple(artifact_values[key] for key in sorted(artifact_values)),
    )


def _merge_file_proofs(
    current: Sequence[RunRecoveryFileProof],
    incoming: Sequence[RunRecoveryFileProof],
) -> tuple[RunRecoveryFileProof, ...]:
    values = {item.virtual_path: item for item in current}
    values.update({item.virtual_path: item for item in incoming})
    return tuple(values[key] for key in sorted(values))


def _merge_artifact_proofs(
    current: Sequence[RunRecoveryArtifactProof],
    incoming: Sequence[RunRecoveryArtifactProof],
) -> tuple[RunRecoveryArtifactProof, ...]:
    values = {item.artifact_id: item for item in current}
    values.update({item.artifact_id: item for item in incoming})
    return tuple(values[key] for key in sorted(values))


def _checkpoint_reference(row: RunCheckpointRow) -> RuntimeCheckpointReference:
    return RuntimeCheckpointReference(
        checkpoint_id=str(row.checkpoint_id),
        run_id=str(row.run_id),
        sequence=int(row.sequence),
        boundary=RuntimeCheckpointBoundary(str(row.boundary)),
        payload_sha256=str(row.payload_sha256),
        resumable=bool(row.resumable),
    )


class SqlAlchemyRuntimeRecoveryCoordinator(RuntimeRecoveryPort):
    """把恢复事实与 Run Ledger 放在同一短事务中提交。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory 必须可调用")
        self._session_factory = session_factory

    @staticmethod
    def _validate_capture(
        capture: RuntimeCheckpointCapture,
        ledger: SqlAlchemyRunEventLedger,
    ) -> None:
        head = ledger.head(capture.identity.run_id)
        if head is None or head.terminal_sequence is not None:
            raise RunRecoveryConflict("Checkpoint 只能写入已接纳且未终止的 Run")
        accepted = ledger.read(capture.identity.run_id, limit=1)[0]
        owner = capture.identity.owner
        declared_owner = (
            accepted.event.identity.owner_platform,
            accepted.event.identity.owner_type,
            accepted.event.identity.owner_id,
        )
        captured_owner = (owner.platform, owner.owner_type.value, owner.owner_id)
        if not all(declared_owner) or declared_owner != captured_owner:
            raise RunRecoveryConflict("Checkpoint owner 与 Run 接纳事实不一致")
        kinds = {item.kind for item in capture.plans}
        missing = sorted(
            (kind.value for kind in _REQUIRED_VERSION_PROOFS - kinds),
        )
        if missing:
            raise RunRecoveryConflict(
                "Checkpoint 缺少版本证明：" + ", ".join(missing)
            )
        model = _plan(capture, RuntimePlanKind.MODEL)
        if capture.model_route is None or model is None:
            raise RunRecoveryConflict("Checkpoint 缺少冻结模型路由")
        if model.sha256 != runtime_model_route_sha256(capture.model_route):
            raise RunRecoveryConflict("模型路由证明与 Runtime 路由不一致")

    @staticmethod
    def _load_previous_proofs(
        row: RunCheckpointRow | None,
    ) -> tuple[
        tuple[RunRecoveryFileProof, ...],
        tuple[RunRecoveryArtifactProof, ...],
    ]:
        if row is None:
            return (), ()
        document = decode_checkpoint_document(
            bytes(row.payload_blob),
            expected_payload_sha256=str(row.payload_sha256),
            expected_state_sha256=str(row.state_sha256),
        )
        return (
            checkpoint_document_file_proofs(document),
            checkpoint_document_artifact_proofs(document),
        )

    async def save_checkpoint(
        self,
        capture: RuntimeCheckpointCapture,
    ) -> RuntimeCheckpointReference:
        if not isinstance(capture, RuntimeCheckpointCapture):
            raise TypeError("capture 必须是 RuntimeCheckpointCapture")
        checkpoint_id = f"checkpoint-{uuid.uuid4().hex}"
        created_at = datetime.now(timezone.utc)
        db = self._session_factory()
        try:
            def operation() -> RuntimeCheckpointReference:
                ledger = SqlAlchemyRunEventLedger(db)
                self._validate_capture(capture, ledger)
                previous = (
                    db.query(RunCheckpointRow)
                    .filter(RunCheckpointRow.run_id == capture.identity.run_id)
                    .order_by(RunCheckpointRow.sequence.desc())
                    .first()
                )
                previous_files, previous_artifacts = self._load_previous_proofs(
                    previous
                )
                workspace_id = _workspace_id_from_plans(capture.plans)
                result_files, result_artifacts = _proofs_from_result(
                    capture.last_tool_result,
                    workspace_id=workspace_id,
                    db=db,
                )
                file_proofs = _merge_file_proofs(previous_files, result_files)
                artifact_proofs = _merge_artifact_proofs(
                    previous_artifacts,
                    result_artifacts,
                )
                receipts = (
                    db.query(RunSideEffectReceipt)
                    .filter(
                        RunSideEffectReceipt.run_id == capture.identity.run_id,
                    )
                    .order_by(
                        RunSideEffectReceipt.prepared_ledger_sequence.asc(),
                        RunSideEffectReceipt.receipt_id.asc(),
                    )
                    .all()
                )
                receipt_ids = tuple(str(item.receipt_id) for item in receipts)
                requested_receipts = tuple(capture.side_effect_receipt_ids)
                if requested_receipts and not set(requested_receipts).issubset(
                    set(receipt_ids)
                ):
                    raise RunRecoveryIntegrityError(
                        "Checkpoint 引用了不存在的副作用回执"
                    )
                unresolved = any(
                    str(item.state) in {
                        RuntimeSideEffectState.PREPARED.value,
                        RuntimeSideEffectState.AMBIGUOUS.value,
                    }
                    for item in receipts
                )
                resumable = bool(capture.resumable and not unresolved)
                document = checkpoint_state_document(
                    identity=capture.identity,
                    boundary=capture.boundary,
                    runtime_id=capture.runtime_id,
                    runtime_protocol_version=capture.runtime_protocol_version,
                    messages=capture.messages,
                    plans=capture.plans,
                    model_route=capture.model_route,
                    model_step=capture.model_step,
                    tool_round=capture.tool_round,
                    file_proofs=file_proofs,
                    artifact_proofs=artifact_proofs,
                    side_effect_receipt_ids=receipt_ids,
                    side_effect_frontier=len(receipts),
                    resumable=resumable,
                )
                payload_blob, payload_sha256, state_sha256 = (
                    encode_checkpoint_document(document)
                )
                sequence = int(previous.sequence if previous is not None else 0) + 1
                proofs = version_proof_mapping(capture.plans)
                version_proofs_sha256 = canonical_sha256(dict(proofs))
                event = RunLedgerEventDraft(
                    event_id=f"checkpoint:{checkpoint_id}",
                    run_id=capture.identity.run_id,
                    event_type="run.checkpoint_saved",
                    occurred_at=created_at,
                    source="run_recovery.coordinator",
                    correlation=_event_correlation(capture),
                    identity=_event_identity(capture),
                    status="resumable" if resumable else "blocked",
                    payload={
                        "checkpoint_id": checkpoint_id,
                        "checkpoint_sequence": sequence,
                        "boundary": capture.boundary.value,
                        "checkpoint_sha256": payload_sha256,
                        "state_sha256": state_sha256,
                        "version_proofs_sha256": version_proofs_sha256,
                        "file_proof_count": len(file_proofs),
                        "artifact_proof_count": len(artifact_proofs),
                        "side_effect_count": len(receipts),
                        "resumable": resumable,
                    },
                )
                ledger_record = ledger.append(event)
                row = RunCheckpointRow(
                    checkpoint_id=checkpoint_id,
                    run_id=capture.identity.run_id,
                    sequence=sequence,
                    schema_version=RUN_CHECKPOINT_SCHEMA_VERSION,
                    boundary=capture.boundary.value,
                    parent_checkpoint_id=(
                        str(previous.checkpoint_id) if previous is not None else ""
                    ),
                    turn_id=capture.identity.turn_id,
                    correlation_id=capture.identity.correlation_id,
                    actor_type=capture.identity.actor.actor_type.value,
                    actor_id=capture.identity.actor.actor_id,
                    parent_actor_id=capture.identity.actor.parent_actor_id,
                    owner_platform=capture.identity.owner.platform,
                    owner_type=capture.identity.owner.owner_type.value,
                    owner_id=capture.identity.owner.owner_id,
                    runtime_id=capture.runtime_id,
                    runtime_protocol_version=capture.runtime_protocol_version,
                    resumable=resumable,
                    model_step=capture.model_step,
                    tool_round=capture.tool_round,
                    side_effect_frontier=len(receipts),
                    manifest_sha256=proofs[RuntimePlanKind.MANIFEST.value],
                    prompt_sha256=proofs[RuntimePlanKind.PROMPT.value],
                    model_route_sha256=proofs[RuntimePlanKind.MODEL.value],
                    tool_plan_sha256=proofs[RuntimePlanKind.TOOL.value],
                    workspace_sha256=proofs[RuntimePlanKind.WORKSPACE.value],
                    artifact_set_sha256=proofs[RuntimePlanKind.ARTIFACT.value],
                    security_sha256=proofs[RuntimePlanKind.SECURITY.value],
                    version_proofs_sha256=version_proofs_sha256,
                    file_proofs_sha256=canonical_sha256(
                        [item.to_dict() for item in file_proofs]
                    ),
                    artifact_proofs_sha256=canonical_sha256(
                        [item.to_dict() for item in artifact_proofs]
                    ),
                    payload_encoding=RUN_CHECKPOINT_PAYLOAD_ENCODING,
                    payload_blob=payload_blob,
                    payload_size_bytes=len(payload_blob),
                    payload_sha256=payload_sha256,
                    state_sha256=state_sha256,
                    ledger_sequence=ledger_record.sequence,
                    ledger_event_sha256=ledger_record.event_sha256,
                    created_at=_utc_naive(created_at),
                )
                db.add(row)
                for receipt in receipts:
                    if (
                        str(receipt.state) != RuntimeSideEffectState.PREPARED.value
                        and not str(receipt.checkpoint_after_id or "")
                    ):
                        receipt.checkpoint_after_id = checkpoint_id
                db.commit()
                return _checkpoint_reference(row)

            return run_sqlite_locked_retry(
                operation,
                rollback=db.rollback,
                label="run_checkpoint_save",
                logger=logger,
            )
        except BaseException as exc:
            db.rollback()
            recovered = self._read_checkpoint(checkpoint_id)
            if recovered is not None:
                return recovered
            if isinstance(exc, (RunRecoveryConflict, RunRecoveryIntegrityError)):
                raise
            if isinstance(exc, RunLedgerConflictError):
                raise RunRecoveryConflict(str(exc)) from exc
            raise RunLedgerAuthorityError(
                "Checkpoint 与 Ledger 事实无法确认提交",
                run_id=capture.identity.run_id,
                event_type="run.checkpoint_saved",
                code="checkpoint_write_unconfirmed",
            ) from exc
        finally:
            db.close()

    def _read_checkpoint(
        self,
        checkpoint_id: str,
    ) -> RuntimeCheckpointReference | None:
        db = self._session_factory()
        try:
            row = db.get(RunCheckpointRow, checkpoint_id)
            return _checkpoint_reference(row) if row is not None else None
        finally:
            db.close()

    async def prepare_tool_effect(
        self,
        *,
        identity,
        tool_call: RuntimeToolCall,
        execution_port_id: str,
        idempotency_key: str,
        effect_class: RuntimeToolEffectClass,
        checkpoint: RuntimeCheckpointReference,
    ) -> RuntimeSideEffectGuard | None:
        effect = RuntimeToolEffectClass(effect_class)
        if not effect.requires_receipt:
            return None
        receipt_id = f"effect-{uuid.uuid4().hex}"
        prepared_at = datetime.now(timezone.utc)
        request_sha256 = canonical_sha256({
            "run_id": identity.run_id,
            "tool_call_id": tool_call.call_id,
            "tool_name": tool_call.name,
            "arguments": sanitize_checkpoint_value(tool_call.arguments),
            "execution_port_id": str(execution_port_id),
            "idempotency_key_sha256": hashlib.sha256(
                str(idempotency_key).encode("utf-8")
            ).hexdigest(),
            "checkpoint_sha256": checkpoint.payload_sha256,
        })
        idempotency_sha256 = hashlib.sha256(
            str(idempotency_key).encode("utf-8")
        ).hexdigest()
        db = self._session_factory()
        try:
            def operation() -> RuntimeSideEffectGuard:
                if checkpoint.run_id != identity.run_id:
                    raise RunRecoveryConflict("副作用回执与 Checkpoint Run 不一致")
                checkpoint_row = db.get(RunCheckpointRow, checkpoint.checkpoint_id)
                if (
                    checkpoint_row is None
                    or str(checkpoint_row.run_id) != identity.run_id
                    or str(checkpoint_row.payload_sha256) != checkpoint.payload_sha256
                ):
                    raise RunRecoveryConflict("副作用前 Checkpoint 不存在或已漂移")
                duplicate = (
                    db.query(RunSideEffectReceipt)
                    .filter(
                        RunSideEffectReceipt.run_id == identity.run_id,
                        (
                            (RunSideEffectReceipt.tool_call_id == tool_call.call_id)
                            | (
                                RunSideEffectReceipt.idempotency_key_sha256
                                == idempotency_sha256
                            )
                        ),
                    )
                    .first()
                )
                if duplicate is not None:
                    raise RunRecoveryConflict(
                        "副作用工具调用已有回执，禁止自动重放"
                    )
                capture = RuntimeCheckpointCapture(
                    identity=identity,
                    boundary=RuntimeCheckpointBoundary.TOOL_READY,
                    runtime_id=str(checkpoint_row.runtime_id),
                    runtime_protocol_version=str(
                        checkpoint_row.runtime_protocol_version
                    ),
                    messages=(),
                    plans=(),
                    model_route=None,
                    resumable=False,
                )
                event = RunLedgerEventDraft(
                    event_id=f"side-effect:{receipt_id}:prepared",
                    run_id=identity.run_id,
                    event_type="tool.side_effect_prepared",
                    occurred_at=prepared_at,
                    source="run_recovery.coordinator",
                    correlation=_event_correlation(
                        capture,
                        tool_call_id=tool_call.call_id,
                    ),
                    identity=_event_identity(capture),
                    status=RuntimeSideEffectState.PREPARED.value,
                    payload={
                        "receipt_id": receipt_id,
                        "checkpoint_id": checkpoint.checkpoint_id,
                        "tool_call_id": tool_call.call_id,
                        "tool_name": tool_call.name,
                        "effect_class": effect.value,
                        "request_sha256": request_sha256,
                    },
                )
                record = SqlAlchemyRunEventLedger(db).append(event)
                db.add(RunSideEffectReceipt(
                    receipt_id=receipt_id,
                    run_id=identity.run_id,
                    tool_call_id=tool_call.call_id,
                    tool_name=tool_call.name,
                    execution_port_id=str(execution_port_id),
                    effect_class=effect.value,
                    state=RuntimeSideEffectState.PREPARED.value,
                    idempotency_key_sha256=idempotency_sha256,
                    request_sha256=request_sha256,
                    result_sha256="",
                    result_size_bytes=0,
                    error_code="",
                    checkpoint_before_id=checkpoint.checkpoint_id,
                    checkpoint_after_id="",
                    file_proofs_json="[]",
                    artifact_proofs_json="[]",
                    prepared_ledger_sequence=record.sequence,
                    terminal_ledger_sequence=None,
                    prepared_at=_utc_naive(prepared_at),
                    settled_at=None,
                ))
                db.commit()
                return RuntimeSideEffectGuard(
                    receipt_id=receipt_id,
                    run_id=identity.run_id,
                    tool_call_id=tool_call.call_id,
                    tool_name=tool_call.name,
                    effect_class=effect,
                    request_sha256=request_sha256,
                )

            return run_sqlite_locked_retry(
                operation,
                rollback=db.rollback,
                label="run_side_effect_prepare",
                logger=logger,
            )
        except BaseException as exc:
            db.rollback()
            recovered = self._read_guard(receipt_id)
            if recovered is not None:
                return recovered
            if isinstance(exc, (RunRecoveryConflict, RunRecoveryIntegrityError)):
                raise
            raise RunLedgerAuthorityError(
                "副作用回执无法在工具调用前确认提交",
                run_id=identity.run_id,
                event_type="tool.side_effect_prepared",
                code="side_effect_prepare_unconfirmed",
            ) from exc
        finally:
            db.close()

    def _read_guard(self, receipt_id: str) -> RuntimeSideEffectGuard | None:
        db = self._session_factory()
        try:
            row = db.get(RunSideEffectReceipt, receipt_id)
            if row is None:
                return None
            return RuntimeSideEffectGuard(
                receipt_id=str(row.receipt_id),
                run_id=str(row.run_id),
                tool_call_id=str(row.tool_call_id),
                tool_name=str(row.tool_name),
                effect_class=RuntimeToolEffectClass(str(row.effect_class)),
                request_sha256=str(row.request_sha256),
            )
        finally:
            db.close()

    async def settle_tool_effect(
        self,
        guard: RuntimeSideEffectGuard,
        *,
        state: RuntimeSideEffectState,
        result: RuntimeToolExecutionResult | None = None,
        error_code: str = "",
    ) -> RuntimeSideEffectReceiptReference:
        terminal_state = RuntimeSideEffectState(state)
        if not terminal_state.terminal:
            raise ValueError("副作用结算状态不能是 prepared")
        result_document = {
            "state": terminal_state.value,
            "result": sanitize_checkpoint_value(
                {
                    "tool_call": (
                        {
                            "call_id": result.tool_call.call_id,
                            "name": result.tool_call.name,
                            "status": result.tool_call.status.value,
                            "result": result.tool_call.result,
                        }
                        if result is not None
                        else None
                    ),
                    "error_code": error_code,
                }
            ),
        }
        encoded_result = canonical_json_bytes(result_document)
        result_sha256 = hashlib.sha256(encoded_result).hexdigest()
        settled_at = datetime.now(timezone.utc)
        db = self._session_factory()
        try:
            def operation() -> RuntimeSideEffectReceiptReference:
                row = db.get(RunSideEffectReceipt, guard.receipt_id)
                if row is None:
                    raise RunRecoveryIntegrityError("副作用回执不存在")
                if (
                    str(row.run_id) != guard.run_id
                    or str(row.tool_call_id) != guard.tool_call_id
                    or str(row.tool_name) != guard.tool_name
                    or str(row.request_sha256) != guard.request_sha256
                ):
                    raise RunRecoveryIntegrityError("副作用回执身份不一致")
                if str(row.state) != RuntimeSideEffectState.PREPARED.value:
                    if (
                        str(row.state) == terminal_state.value
                        and str(row.result_sha256) == result_sha256
                    ):
                        return RuntimeSideEffectReceiptReference(
                            receipt_id=str(row.receipt_id),
                            state=terminal_state,
                            result_sha256=result_sha256,
                        )
                    raise RunRecoveryConflict("副作用回执已经以不同结果结算")
                checkpoint = db.get(
                    RunCheckpointRow,
                    str(row.checkpoint_before_id),
                )
                if checkpoint is None:
                    raise RunRecoveryIntegrityError("副作用前 Checkpoint 已丢失")
                workspace_id = _workspace_id_from_plans(
                    _plans_from_checkpoint(checkpoint)
                )
                file_proofs, artifact_proofs = _proofs_from_result(
                    result,
                    workspace_id=workspace_id,
                    db=db,
                )
                ledger = SqlAlchemyRunEventLedger(db)
                prepared_record = ledger.get(
                    f"side-effect:{guard.receipt_id}:prepared"
                )
                if (
                    prepared_record is None
                    or prepared_record.run_id != guard.run_id
                    or prepared_record.sequence
                    != int(row.prepared_ledger_sequence)
                    or prepared_record.event_type
                    != "tool.side_effect_prepared"
                ):
                    raise RunRecoveryIntegrityError(
                        "副作用 prepared Ledger 事实不存在或已漂移"
                    )
                event = RunLedgerEventDraft(
                    event_id=f"side-effect:{guard.receipt_id}:{terminal_state.value}",
                    run_id=guard.run_id,
                    event_type=f"tool.side_effect_{terminal_state.value}",
                    occurred_at=settled_at,
                    source="run_recovery.coordinator",
                    correlation=prepared_record.event.correlation,
                    identity=prepared_record.event.identity,
                    status=terminal_state.value,
                    payload={
                        "receipt_id": guard.receipt_id,
                        "tool_call_id": guard.tool_call_id,
                        "tool_name": guard.tool_name,
                        "effect_class": guard.effect_class.value,
                        "result_sha256": result_sha256,
                        "file_proof_count": len(file_proofs),
                        "artifact_proof_count": len(artifact_proofs),
                        "file_proofs_sha256": canonical_sha256(
                            [item.to_dict() for item in file_proofs]
                        ),
                        "artifact_proofs_sha256": canonical_sha256(
                            [item.to_dict() for item in artifact_proofs]
                        ),
                        "error_code": str(error_code or "")[:128],
                    },
                )
                record = ledger.append(event)
                row.state = terminal_state.value
                row.result_sha256 = result_sha256
                row.result_size_bytes = len(encoded_result)
                row.error_code = str(error_code or "")[:128]
                row.file_proofs_json = json.dumps(
                    [item.to_dict() for item in file_proofs],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                row.artifact_proofs_json = json.dumps(
                    [item.to_dict() for item in artifact_proofs],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                row.terminal_ledger_sequence = record.sequence
                row.settled_at = _utc_naive(settled_at)
                db.commit()
                return RuntimeSideEffectReceiptReference(
                    receipt_id=guard.receipt_id,
                    state=terminal_state,
                    result_sha256=result_sha256,
                )

            return run_sqlite_locked_retry(
                operation,
                rollback=db.rollback,
                label="run_side_effect_settle",
                logger=logger,
            )
        except BaseException as exc:
            db.rollback()
            recovered = self._read_terminal_receipt(
                guard.receipt_id,
                state=terminal_state,
                result_sha256=result_sha256,
            )
            if recovered is not None:
                return recovered
            if isinstance(exc, (RunRecoveryConflict, RunRecoveryIntegrityError)):
                raise
            raise RunLedgerAuthorityError(
                "副作用已经调用，但终结回执无法确认提交",
                run_id=guard.run_id,
                event_type=f"tool.side_effect_{terminal_state.value}",
                code="side_effect_receipt_unconfirmed",
            ) from exc
        finally:
            db.close()

    def _read_terminal_receipt(
        self,
        receipt_id: str,
        *,
        state: RuntimeSideEffectState,
        result_sha256: str,
    ) -> RuntimeSideEffectReceiptReference | None:
        db = self._session_factory()
        try:
            row = db.get(RunSideEffectReceipt, receipt_id)
            if (
                row is None
                or str(row.state) != state.value
                or str(row.result_sha256) != result_sha256
            ):
                return None
            return RuntimeSideEffectReceiptReference(
                receipt_id=receipt_id,
                state=state,
                result_sha256=result_sha256,
            )
        finally:
            db.close()


def _plans_from_checkpoint(row: RunCheckpointRow):
    document = decode_checkpoint_document(
        bytes(row.payload_blob),
        expected_payload_sha256=str(row.payload_sha256),
        expected_state_sha256=str(row.state_sha256),
    )
    from core.run_recovery.contracts import checkpoint_document_plans

    return checkpoint_document_plans(document)


def default_runtime_recovery_port() -> SqlAlchemyRuntimeRecoveryCoordinator:
    """延迟加载 SessionLocal，避免稳定 Runtime 合同依赖数据库。"""

    from core import database

    return SqlAlchemyRuntimeRecoveryCoordinator(lambda: database.SessionLocal())


__all__ = [
    "SqlAlchemyRuntimeRecoveryCoordinator",
    "default_runtime_recovery_port",
]
