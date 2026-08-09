"""统一离线 Run Viewer 的 SQLAlchemy 只读 Adapter。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.context_engine import ContextManifestError, validate_context_manifest
from core.db.models.observability import (
    AgentRun,
    LLMApiRequestLog,
    PromptRenderLog,
    RuntimeTelemetryEvent,
    ToolCall,
)
from core.db.models.run_recovery import (
    RunCheckpointRow,
    RunRecoveryOperation,
    RunSideEffectReceipt,
)
from core.db.models.sandbox import Asset, SandboxRun, WorkspaceAsset
from core.observability.run_view import RunViewSource, build_run_view
from core.run_ledger.persistence import SqlAlchemyRunEventLedger
from core.run_ledger.read_model import load_authoritative_run_view
from core.tracing import row_to_dict


_MAX_ROWS_PER_SOURCE = 2_000


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _context_manifest_view(
    prompt_logs: Sequence[PromptRenderLog],
    ledger_projection: Mapping[str, Any] | None,
) -> dict[str, Any]:
    fingerprint = dict(
        (ledger_projection or {}).get("context_manifest") or {}
    )
    invalid_count = 0
    for row in reversed(tuple(prompt_logs)):
        raw = str(getattr(row, "context_manifest_json", "") or "")
        if not raw or raw == "{}":
            continue
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ContextManifestError("Context Manifest 必须是对象")
            validate_context_manifest(parsed)
        except (
            ContextManifestError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            invalid_count += 1
            continue
        return {
            "available": True,
            "source": "prompt_render_log",
            "prompt_render_log_id": int(row.id or 0),
            "manifest": parsed,
            "fingerprint": fingerprint,
            "invalid_record_count": invalid_count,
        }
    return {
        "available": False,
        "source": "run_ledger_fingerprint" if fingerprint else "not_recorded",
        "prompt_render_log_id": None,
        "manifest": {},
        "fingerprint": fingerprint,
        "invalid_record_count": invalid_count,
    }


def _tool_call(row: ToolCall) -> dict[str, Any]:
    return {
        "tool_call_id": str(row.tool_call_id or ""),
        "trace_id": str(row.trace_id or ""),
        "tool_name": str(row.tool_name or ""),
        "status": str(row.status or ""),
        "latency_ms": int(row.latency_ms or 0),
        "error_present": bool(str(row.error or "")),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


def _prompt_log(row: PromptRenderLog) -> dict[str, Any]:
    return {
        "id": int(row.id or 0),
        "trace_id": str(row.trace_id or ""),
        "prompt_key": str(row.prompt_key or ""),
        "mode": str(row.mode or ""),
        "prompt_source": str(row.prompt_source or ""),
        "prompt_sha256": str(row.prompt_sha256 or ""),
        "token_estimate": int(row.token_estimate or 0),
        "error_present": bool(str(row.error or "")),
        "created_at": _iso(row.created_at),
    }


def _llm_request(row: LLMApiRequestLog) -> dict[str, Any]:
    return {
        "id": int(row.id or 0),
        "trace_id": str(row.trace_id or ""),
        "source": str(row.source or ""),
        "phase": str(row.phase or ""),
        "round_index": int(row.round_index or 0),
        "route_attempt_index": int(row.route_attempt_index or 0),
        "provider": str(row.provider or ""),
        "model": str(row.model or ""),
        "status": str(row.status or ""),
        "error_category": str(row.error_category or ""),
        "cache_status": str(row.cache_status or ""),
        "cache_hit": row.cache_hit,
        "cache_hit_tokens": int(row.cache_hit_tokens or 0),
        "cache_miss_tokens": int(row.cache_miss_tokens or 0),
        "cache_write_tokens": int(row.cache_write_tokens or 0),
        "input_tokens": int(row.input_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "first_token_latency_ms": int(row.first_token_latency_ms or 0),
        "cost_microusd": int(row.cost_microusd or 0),
        "cost_source": str(row.cost_source or ""),
        "latency_ms": int(row.latency_ms or 0),
        "created_at": _iso(row.created_at),
        "finished_at": _iso(row.finished_at),
    }


def _runtime_event(row: RuntimeTelemetryEvent) -> dict[str, Any]:
    return {
        "event_id": str(row.event_id or ""),
        "name": str(row.name or ""),
        "domain": str(row.domain or ""),
        "phase": str(row.phase or ""),
        "occurred_at": _iso(row.occurred_at),
        "turn_id": str(row.turn_id or ""),
        "trace_id": str(row.trace_id or ""),
        "run_id": str(row.run_id or ""),
        "task_id": str(row.task_id or ""),
        "task_run_id": str(row.task_run_id or ""),
        "job_id": str(row.job_id or ""),
        "tool_call_id": str(row.tool_call_id or ""),
        "delivery_id": str(row.delivery_id or ""),
        "parent_job_id": str(row.parent_job_id or ""),
        "registry_generation": int(row.registry_generation or 0),
        "registry_sha256": str(row.registry_sha256 or ""),
        "module_id": str(row.module_id or ""),
        "module_version": str(row.module_version or ""),
        "artifact_revision": str(row.artifact_revision or ""),
        "failure_code": str(row.failure_code or ""),
        "attributes": str(row.attributes_json or "{}"),
    }


def _sandbox_run(row: SandboxRun) -> dict[str, Any]:
    return {
        "run_id": str(row.run_id or ""),
        "trace_id": str(row.trace_id or ""),
        "tool_call_id": str(row.tool_call_id or ""),
        "profile_id": str(row.profile_id or ""),
        "execution_mode": str(row.execution_mode or ""),
        "image_digest": str(row.image_digest or ""),
        "status": str(row.status or ""),
        "termination_reason": str(row.termination_reason or ""),
        "cpu_time_ms": int(row.cpu_time_ms or 0),
        "peak_memory_bytes": int(row.peak_memory_bytes or 0),
        "stdout_bytes": int(row.stdout_bytes or 0),
        "stderr_bytes": int(row.stderr_bytes or 0),
        "stdout_truncated": bool(row.stdout_truncated),
        "stderr_truncated": bool(row.stderr_truncated),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "created_at": _iso(row.created_at),
    }


def _checkpoint(row: RunCheckpointRow) -> dict[str, Any]:
    return {
        "checkpoint_id": str(row.checkpoint_id or ""),
        "sequence": int(row.sequence or 0),
        "schema_version": int(row.schema_version or 0),
        "boundary": str(row.boundary or ""),
        "turn_id": str(row.turn_id or ""),
        "runtime_id": str(row.runtime_id or ""),
        "runtime_protocol_version": str(row.runtime_protocol_version or ""),
        "resumable": bool(row.resumable),
        "model_step": int(row.model_step or 0),
        "tool_round": int(row.tool_round or 0),
        "side_effect_frontier": int(row.side_effect_frontier or 0),
        "version_proofs_sha256": str(row.version_proofs_sha256 or ""),
        "created_at": _iso(row.created_at),
    }


def _side_effect(row: RunSideEffectReceipt) -> dict[str, Any]:
    return {
        "receipt_id": str(row.receipt_id or ""),
        "tool_call_id": str(row.tool_call_id or ""),
        "tool_name": str(row.tool_name or ""),
        "effect_class": str(row.effect_class or ""),
        "state": str(row.state or ""),
        "request_sha256": str(row.request_sha256 or ""),
        "result_sha256": str(row.result_sha256 or ""),
        "result_size_bytes": int(row.result_size_bytes or 0),
        "error_code": str(row.error_code or ""),
        "prepared_at": _iso(row.prepared_at),
        "settled_at": _iso(row.settled_at),
    }


def _recovery(row: RunRecoveryOperation) -> dict[str, Any]:
    return {
        "operation_id": str(row.operation_id or ""),
        "operation_kind": str(row.operation_kind or ""),
        "restored_checkpoint_id": str(row.restored_checkpoint_id or ""),
        "source_head_sequence": int(row.source_head_sequence or 0),
        "status": str(row.status or ""),
        "error_code": str(row.error_code or ""),
        "prepared_at": _iso(row.prepared_at),
        "updated_at": _iso(row.updated_at),
        "finished_at": _iso(row.finished_at),
    }


def _ledger_record(record: object) -> dict[str, Any]:
    event = getattr(record, "event", None)
    correlation = getattr(event, "correlation", None)
    return {
        "sequence": int(getattr(record, "sequence", 0) or 0),
        "event_type": str(getattr(event, "event_type", "") or ""),
        "status": str(getattr(event, "status", "") or ""),
        "occurred_at": _iso(getattr(event, "occurred_at", None)),
        "trace_id": str(getattr(correlation, "trace_id", "") or ""),
        "turn_id": str(getattr(correlation, "turn_id", "") or ""),
    }


class OfflineRunViewRepository:
    """只读取当前数据库快照，不重放任务，也不调用模型。"""

    def __init__(self, db: Session) -> None:
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        self._db = db

    def build(
        self,
        *,
        run_id: str,
        run: Mapping[str, Any],
        ledger_projection: Mapping[str, Any] | None,
        ledger_records: Sequence[object],
        tool_calls: Sequence[ToolCall],
        prompt_logs: Sequence[PromptRenderLog],
        llm_requests: Sequence[LLMApiRequestLog],
    ) -> dict[str, Any]:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run_id 不能为空")
        runtime_events = (
            self._db.query(RuntimeTelemetryEvent)
            .filter(or_(
                RuntimeTelemetryEvent.run_id == normalized_run_id,
                RuntimeTelemetryEvent.task_run_id == normalized_run_id,
            ))
            .order_by(RuntimeTelemetryEvent.occurred_at.asc())
            .limit(_MAX_ROWS_PER_SOURCE)
            .all()
        )
        sandbox_runs = (
            self._db.query(SandboxRun)
            .filter(SandboxRun.agent_run_id == normalized_run_id)
            .order_by(SandboxRun.created_at.asc())
            .limit(_MAX_ROWS_PER_SOURCE)
            .all()
        )
        artifact_rows = (
            self._db.query(WorkspaceAsset, Asset.size_bytes)
            .outerjoin(Asset, Asset.sha256 == WorkspaceAsset.asset_sha256)
            .filter(WorkspaceAsset.source_run_id == normalized_run_id)
            .order_by(WorkspaceAsset.created_at.asc())
            .limit(_MAX_ROWS_PER_SOURCE)
            .all()
        )
        checkpoints = (
            self._db.query(RunCheckpointRow)
            .filter(RunCheckpointRow.run_id == normalized_run_id)
            .order_by(RunCheckpointRow.sequence.asc())
            .limit(_MAX_ROWS_PER_SOURCE)
            .all()
        )
        side_effects = (
            self._db.query(RunSideEffectReceipt)
            .filter(RunSideEffectReceipt.run_id == normalized_run_id)
            .order_by(RunSideEffectReceipt.prepared_at.asc())
            .limit(_MAX_ROWS_PER_SOURCE)
            .all()
        )
        recoveries = (
            self._db.query(RunRecoveryOperation)
            .filter(RunRecoveryOperation.run_id == normalized_run_id)
            .order_by(RunRecoveryOperation.prepared_at.asc())
            .limit(_MAX_ROWS_PER_SOURCE)
            .all()
        )
        artifacts = [
            {
                "artifact_id": str(row.artifact_id or ""),
                "asset_sha256": str(row.asset_sha256 or ""),
                "version": int(row.version or 0),
                "source_kind": str(row.source_kind or ""),
                "size_bytes": int(size_bytes or 0),
                "created_at": _iso(row.created_at),
            }
            for row, size_bytes in artifact_rows
        ]
        return build_run_view(RunViewSource(
            run_id=normalized_run_id,
            run=dict(run),
            ledger_projection=(
                dict(ledger_projection) if ledger_projection else None
            ),
            ledger_records=tuple(_ledger_record(item) for item in ledger_records),
            tool_calls=tuple(_tool_call(item) for item in tool_calls),
            prompt_logs=tuple(_prompt_log(item) for item in prompt_logs),
            llm_requests=tuple(_llm_request(item) for item in llm_requests),
            runtime_events=tuple(_runtime_event(item) for item in runtime_events),
            sandbox_runs=tuple(_sandbox_run(item) for item in sandbox_runs),
            artifacts=tuple(artifacts),
            checkpoints=tuple(_checkpoint(item) for item in checkpoints),
            side_effects=tuple(_side_effect(item) for item in side_effects),
            recovery_operations=tuple(_recovery(item) for item in recoveries),
            context_manifest=_context_manifest_view(
                prompt_logs,
                ledger_projection,
            ),
        ))

    def build_persisted(self, run_id: str) -> dict[str, Any]:
        """按 Run ID 读取完整脱敏 Viewer；不依赖 API 私有函数。"""

        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run_id 不能为空")
        legacy = self._db.query(AgentRun).filter(
            AgentRun.run_id == normalized_run_id
        ).first()
        ledger_view = load_authoritative_run_view(
            SqlAlchemyRunEventLedger(self._db),
            normalized_run_id,
        )
        if legacy is None and ledger_view is None:
            raise LookupError("Agent Run 不存在")
        run: dict[str, Any] = row_to_dict(legacy) if legacy is not None else {}
        ledger_projection: dict[str, Any] | None = None
        ledger_records: Sequence[object] = ()
        if ledger_view is not None:
            accepted = ledger_view.accepted.event
            projection = ledger_view.projection
            ledger_projection = projection.to_dict()
            ledger_records = ledger_view.records
            if legacy is None:
                run.update({
                    "run_id": projection.run_id,
                    "trace_id": accepted.correlation.trace_id,
                    "session_id": accepted.correlation.session_id,
                    "user_id": (
                        accepted.identity.actor_id
                        if accepted.identity.actor_type == "user"
                        else ""
                    ),
                    "chat_type": str(accepted.payload.get("chat_type") or ""),
                    "group_id": (
                        accepted.identity.owner_id
                        if accepted.identity.owner_type == "group"
                        else ""
                    ),
                    "run_type": str(accepted.payload.get("run_type") or ""),
                    "meta_json": "{}",
                })
            run.update({
                "status": projection.status,
                "started_at": (
                    projection.started_at.isoformat()
                    if projection.started_at
                    else None
                ),
                "finished_at": (
                    projection.finished_at.isoformat()
                    if projection.finished_at
                    else None
                ),
                "prompt_mode": projection.prompt_mode
                or str(run.get("prompt_mode") or ""),
                "prompt_key": projection.prompt_key
                or str(run.get("prompt_key") or ""),
                "prompt_sha256": projection.prompt_sha256
                or str(run.get("prompt_sha256") or ""),
            })
            if projection.model_ids:
                run["model"] = projection.model_ids[-1]
        tool_calls = (
            self._db.query(ToolCall)
            .filter(ToolCall.run_id == normalized_run_id)
            .order_by(ToolCall.started_at.asc())
            .limit(_MAX_ROWS_PER_SOURCE)
            .all()
        )
        prompt_logs = (
            self._db.query(PromptRenderLog)
            .filter(PromptRenderLog.run_id == normalized_run_id)
            .order_by(PromptRenderLog.created_at.asc())
            .limit(_MAX_ROWS_PER_SOURCE)
            .all()
        )
        llm_requests = (
            self._db.query(LLMApiRequestLog)
            .filter(LLMApiRequestLog.run_id == normalized_run_id)
            .order_by(LLMApiRequestLog.created_at.asc())
            .limit(_MAX_ROWS_PER_SOURCE)
            .all()
        )
        return self.build(
            run_id=normalized_run_id,
            run=run,
            ledger_projection=ledger_projection,
            ledger_records=ledger_records,
            tool_calls=tool_calls,
            prompt_logs=prompt_logs,
            llm_requests=llm_requests,
        )


__all__ = ["OfflineRunViewRepository"]
