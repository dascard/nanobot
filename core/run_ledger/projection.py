"""Run Ledger 的纯函数只读投影。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.run_ledger.contracts import (
    RunLedgerEventRecord,
    RunLedgerIntegrityError,
    canonical_run_status,
    is_terminal_run_status,
)


@dataclass(frozen=True, slots=True)
class RunLedgerProjection:
    run_id: str
    status: str
    terminal: bool
    event_count: int
    high_water_sequence: int
    started_at: datetime | None
    updated_at: datetime | None
    finished_at: datetime | None
    model_request_count: int
    tool_call_count: int
    permission_decision_count: int
    delivery_attempt_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    cost_microunits: int
    artifact_ids: tuple[str, ...]
    error_codes: tuple[str, ...]
    correction_count: int
    prompt_mode: str
    prompt_key: str
    prompt_sha256: str
    prompt_resolution_sha256: str
    model_ids: tuple[str, ...]
    tool_names: tuple[str, ...]
    checkpoint_count: int
    latest_checkpoint_id: str
    latest_checkpoint_boundary: str
    latest_checkpoint_resumable: bool
    checkpoint_version_proofs_sha256: str
    side_effect_prepared_count: int
    side_effect_completed_count: int
    side_effect_failed_count: int
    side_effect_ambiguous_count: int
    lineage_operation_kind: str
    parent_run_sha256: str
    root_run_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "terminal": self.terminal,
            "event_count": self.event_count,
            "high_water_sequence": self.high_water_sequence,
            "started_at": (
                self.started_at.isoformat() if self.started_at else None
            ),
            "updated_at": (
                self.updated_at.isoformat() if self.updated_at else None
            ),
            "finished_at": (
                self.finished_at.isoformat() if self.finished_at else None
            ),
            "model_request_count": self.model_request_count,
            "tool_call_count": self.tool_call_count,
            "permission_decision_count": self.permission_decision_count,
            "delivery_attempt_count": self.delivery_attempt_count,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "cost_microunits": self.cost_microunits,
            },
            "artifact_ids": list(self.artifact_ids),
            "error_codes": list(self.error_codes),
            "correction_count": self.correction_count,
            "recovery": {
                "checkpoint_count": self.checkpoint_count,
                "latest_checkpoint_id": self.latest_checkpoint_id,
                "latest_checkpoint_boundary": (
                    self.latest_checkpoint_boundary
                ),
                "latest_checkpoint_resumable": (
                    self.latest_checkpoint_resumable
                ),
                "version_proofs_sha256": (
                    self.checkpoint_version_proofs_sha256
                ),
                "side_effect_receipts": {
                    "prepared": self.side_effect_prepared_count,
                    "completed": self.side_effect_completed_count,
                    "failed": self.side_effect_failed_count,
                    "ambiguous": self.side_effect_ambiguous_count,
                },
                "lineage": {
                    "operation_kind": self.lineage_operation_kind,
                    "parent_run_sha256": self.parent_run_sha256,
                    "root_run_sha256": self.root_run_sha256,
                },
            },
            "context_manifest": {
                "prompt_mode": self.prompt_mode,
                "prompt_key": self.prompt_key,
                "prompt_sha256": self.prompt_sha256,
                "prompt_resolution_sha256": (
                    self.prompt_resolution_sha256
                ),
                "model_ids": list(self.model_ids),
                "tool_names": list(self.tool_names),
                "artifact_ids": list(self.artifact_ids),
            },
        }


@dataclass(frozen=True, slots=True)
class RunLedgerReadiness:
    """单个 legacy AgentRun 头与完整 Ledger 投影的一致性报告。"""

    run_id: str
    projection_consistent: bool
    projection_complete: bool
    reason_codes: tuple[str, ...]
    legacy_status: str
    ledger_status: str
    legacy_terminal: bool
    ledger_terminal: bool
    accepted_event_count: int
    terminal_event_count: int
    high_water_sequence: int

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "projection_consistent": self.projection_consistent,
            "projection_complete": self.projection_complete,
            "reason_codes": list(self.reason_codes),
            "legacy_status": self.legacy_status,
            "ledger_status": self.ledger_status,
            "legacy_terminal": self.legacy_terminal,
            "ledger_terminal": self.ledger_terminal,
            "accepted_event_count": self.accepted_event_count,
            "terminal_event_count": self.terminal_event_count,
            "high_water_sequence": self.high_water_sequence,
        }


def verify_run_ledger_chain(
    records: tuple[RunLedgerEventRecord, ...],
) -> None:
    if not records:
        return
    run_id = records[0].run_id
    previous_sha256 = ""
    for expected_sequence, record in enumerate(records, start=1):
        if record.run_id != run_id:
            raise RunLedgerIntegrityError("投影不能混合多个 run_id")
        if record.sequence != expected_sequence:
            raise RunLedgerIntegrityError(
                "Run Ledger sequence 不连续："
                f"期望 {expected_sequence}，实际 {record.sequence}"
            )
        if record.previous_event_sha256 != previous_sha256:
            raise RunLedgerIntegrityError(
                f"Run Ledger 摘要链断裂：{record.event_id}"
            )
        previous_sha256 = record.event_sha256


def project_run_ledger(
    records: tuple[RunLedgerEventRecord, ...],
) -> RunLedgerProjection | None:
    """从完整 Run 事件序列生成可丢弃、可重建的管理视图。"""

    if not records:
        return None
    verify_run_ledger_chain(records)
    status = "unknown"
    terminal = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    model_request_count = 0
    tool_call_ids: set[str] = set()
    permission_decision_count = 0
    delivery_attempt_ids: set[str] = set()
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "cost_microunits": 0,
    }
    artifact_ids: list[str] = []
    error_codes: list[str] = []
    correction_count = 0
    prompt_mode = ""
    prompt_key = ""
    prompt_sha256 = ""
    prompt_resolution_sha256 = ""
    model_ids: list[str] = []
    tool_names: list[str] = []
    checkpoint_count = 0
    latest_checkpoint_id = ""
    latest_checkpoint_boundary = ""
    latest_checkpoint_resumable = False
    checkpoint_version_proofs_sha256 = ""
    side_effect_counts = {
        "prepared": 0,
        "completed": 0,
        "failed": 0,
        "ambiguous": 0,
    }
    lineage_operation_kind = ""
    parent_run_sha256 = ""
    root_run_sha256 = ""

    for record in records:
        event = record.event
        if event.event_type == "run.accepted":
            started_at = started_at or event.occurred_at
            status = event.status or "accepted"
            prompt_mode = str(event.payload.get("prompt_mode") or "")
            prompt_key = str(event.payload.get("prompt_key") or "")
            prompt_sha256 = str(event.payload.get("prompt_sha256") or "")
        elif event.event_type == "run.prompt_resolved":
            prompt_mode = str(event.payload.get("prompt_mode") or prompt_mode)
            prompt_key = str(event.payload.get("prompt_key") or prompt_key)
            prompt_sha256 = str(
                event.payload.get("prompt_sha256") or prompt_sha256
            )
            prompt_resolution_sha256 = str(
                event.payload.get("prompt_resolution_sha256")
                or prompt_resolution_sha256
            )
        elif event.event_type == "run.status_changed":
            status = event.status
        elif event.event_type == "run.terminated":
            status = event.status
            terminal = True
            finished_at = event.occurred_at
        elif event.event_type == "run.event_corrected":
            correction_count += 1
            replacement_status = event.payload.get("replacement_status")
            if isinstance(replacement_status, str) and replacement_status:
                status = replacement_status

        if event.event_type == "model.request.started":
            model_request_count += 1
        elif event.event_type == "tool.execute.started":
            tool_call_ids.add(
                event.correlation.tool_call_id or event.event_id
            )
        elif event.event_type == "tool.activity_recorded":
            tool_call_id = event.payload.get("tool_call_id")
            tool_call_ids.add(
                str(tool_call_id or event.correlation.tool_call_id or event.event_id)
            )
        elif event.event_type == "permission.decided":
            permission_decision_count += 1
        elif event.event_type == "delivery.attempt.started":
            delivery_attempt_ids.add(
                event.correlation.delivery_id or event.event_id
            )
        elif event.event_type == "usage.recorded":
            for key in usage:
                value = event.payload.get(key)
                if type(value) is int and value >= 0:
                    usage[key] += value
        elif event.event_type == "artifact.recorded":
            artifact_id = event.payload.get("artifact_id")
            if isinstance(artifact_id, str) and artifact_id:
                artifact_ids.append(artifact_id)
        elif event.event_type in {
            "run.checkpoint_saved",
            "run.checkpoint_restored",
        }:
            checkpoint_count += 1
            latest_checkpoint_id = str(
                event.payload.get("checkpoint_id") or ""
            )
            latest_checkpoint_boundary = str(
                event.payload.get("boundary") or ""
            )
            latest_checkpoint_resumable = bool(
                event.payload.get("resumable") is True
            )
            checkpoint_version_proofs_sha256 = str(
                event.payload.get("version_proofs_sha256") or ""
            )
        elif event.event_type.startswith("tool.side_effect_"):
            effect_state = event.event_type.removeprefix(
                "tool.side_effect_"
            )
            if effect_state in side_effect_counts:
                side_effect_counts[effect_state] += 1
        elif event.event_type == "run.lineage_declared":
            lineage_operation_kind = str(
                event.payload.get("operation_kind") or ""
            )
            parent_run_sha256 = str(
                event.payload.get("parent_run_sha256") or ""
            )
            root_run_sha256 = str(
                event.payload.get("root_run_sha256") or ""
            )

        model_id = event.payload.get("model")
        if isinstance(model_id, str) and model_id:
            model_ids.append(model_id)
        tool_name = event.payload.get("tool_name")
        if isinstance(tool_name, str) and tool_name:
            tool_names.append(tool_name)

        error_code = event.payload.get("error_code")
        if isinstance(error_code, str) and error_code:
            error_codes.append(error_code)
        failure_code = event.payload.get("failure_code")
        if isinstance(failure_code, str) and failure_code:
            error_codes.append(failure_code)

    return RunLedgerProjection(
        run_id=records[0].run_id,
        status=status,
        terminal=terminal,
        event_count=len(records),
        high_water_sequence=records[-1].sequence,
        started_at=started_at,
        updated_at=records[-1].event.occurred_at,
        finished_at=finished_at,
        model_request_count=model_request_count,
        tool_call_count=len(tool_call_ids),
        permission_decision_count=permission_decision_count,
        delivery_attempt_count=len(delivery_attempt_ids),
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cached_input_tokens=usage["cached_input_tokens"],
        reasoning_tokens=usage["reasoning_tokens"],
        cost_microunits=usage["cost_microunits"],
        artifact_ids=tuple(dict.fromkeys(artifact_ids)),
        error_codes=tuple(dict.fromkeys(error_codes)),
        correction_count=correction_count,
        prompt_mode=prompt_mode,
        prompt_key=prompt_key,
        prompt_sha256=prompt_sha256,
        prompt_resolution_sha256=prompt_resolution_sha256,
        model_ids=tuple(dict.fromkeys(model_ids)),
        tool_names=tuple(dict.fromkeys(tool_names)),
        checkpoint_count=checkpoint_count,
        latest_checkpoint_id=latest_checkpoint_id,
        latest_checkpoint_boundary=latest_checkpoint_boundary,
        latest_checkpoint_resumable=latest_checkpoint_resumable,
        checkpoint_version_proofs_sha256=(
            checkpoint_version_proofs_sha256
        ),
        side_effect_prepared_count=side_effect_counts["prepared"],
        side_effect_completed_count=side_effect_counts["completed"],
        side_effect_failed_count=side_effect_counts["failed"],
        side_effect_ambiguous_count=side_effect_counts["ambiguous"],
        lineage_operation_kind=lineage_operation_kind,
        parent_run_sha256=parent_run_sha256,
        root_run_sha256=root_run_sha256,
    )


def assess_run_ledger_readiness(
    records: tuple[RunLedgerEventRecord, ...],
    *,
    run_id: str = "",
    legacy_status: str,
    legacy_finished_at: datetime | None,
    projection_complete: bool = True,
    high_water_sequence: int | None = None,
) -> RunLedgerReadiness:
    """只读比较 legacy header；不修复、不提升 Ledger 控制权。"""

    resolved_run_id = records[0].run_id if records else str(run_id or "")
    normalized_legacy_status = canonical_run_status(legacy_status)
    legacy_terminal = (
        legacy_finished_at is not None
        or is_terminal_run_status(normalized_legacy_status)
    )
    accepted_count = sum(
        record.event_type == "run.accepted" for record in records
    )
    terminal_count = sum(
        record.event_type == "run.terminated" for record in records
    )
    projection = project_run_ledger(records)
    reasons: list[str] = []
    if not projection_complete:
        reasons.append("projection_incomplete")
    if not records:
        reasons.append("ledger_missing")
    elif records[0].event_type != "run.accepted":
        reasons.append("accepted_not_first")
    if accepted_count != 1:
        reasons.append("accepted_count_mismatch")
    if terminal_count > 1:
        reasons.append("terminal_count_mismatch")

    ledger_status = projection.status if projection is not None else ""
    ledger_terminal = projection.terminal if projection is not None else False
    if projection is not None and ledger_status != normalized_legacy_status:
        reasons.append("status_mismatch")
    if legacy_terminal and not ledger_terminal:
        reasons.append("ledger_terminal_missing")
    elif ledger_terminal and not legacy_terminal:
        reasons.append("legacy_terminal_missing")

    return RunLedgerReadiness(
        run_id=resolved_run_id,
        projection_consistent=not reasons,
        projection_complete=bool(projection_complete),
        reason_codes=tuple(dict.fromkeys(reasons)),
        legacy_status=normalized_legacy_status,
        ledger_status=ledger_status,
        legacy_terminal=legacy_terminal,
        ledger_terminal=ledger_terminal,
        accepted_event_count=accepted_count,
        terminal_event_count=terminal_count,
        high_water_sequence=(
            int(high_water_sequence)
            if high_water_sequence is not None
            else (records[-1].sequence if records else 0)
        ),
    )


def run_ledger_record_to_dict(
    record: RunLedgerEventRecord,
) -> dict[str, object]:
    event = record.event
    return {
        "position": record.position,
        "sequence": record.sequence,
        "event_id": event.event_id,
        "run_id": event.run_id,
        "event_type": event.event_type,
        "schema_version": event.schema_version,
        "occurred_at": event.occurred_at.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
        "source": event.source,
        "source_event_id": event.source_event_id,
        "source_sequence": event.source_sequence,
        "status": event.status,
        "correlation": event.correlation.to_dict(),
        "identity": event.identity.to_dict(),
        "payload": dict(event.payload),
        "dropped_field_count": event.dropped_field_count,
        "correction_of_event_id": event.correction_of_event_id,
        "previous_event_sha256": record.previous_event_sha256,
        "event_sha256": record.event_sha256,
    }


__all__ = [
    "RunLedgerReadiness",
    "RunLedgerProjection",
    "assess_run_ledger_readiness",
    "project_run_ledger",
    "run_ledger_record_to_dict",
    "verify_run_ledger_chain",
]
