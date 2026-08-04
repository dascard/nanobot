from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

from core.database import (
    AgentRun,
    Base,
    LLMApiRequestLog,
    PromptRenderLog,
    ReplyContractCheckLog,
    RunCheckpointRow,
    RunRecoveryOperation,
    RunSideEffectReceipt,
    RunLedgerErasureAuthorization,
    RunLedgerErasureReceipt,
    RunLedgerEventRow,
    RunLedgerStreamHead,
    RuntimeTelemetryEvent,
    ToolCall,
)
from core.run_ledger.contracts import RunLedgerEventDraft, RunLedgerIdentity
from core.run_ledger.governance import (
    RunEvidenceAccessDenied,
    RunEvidenceConflict,
    RunEvidenceOwner,
    RunEvidencePolicyDenied,
    RunEvidencePrincipal,
    RunEvidenceRetentionPolicy,
    RunEvidenceRole,
)
from core.run_ledger.governance_service import RunEvidenceGovernanceService
from core.run_ledger.persistence import SqlAlchemyRunEventLedger
from core.telemetry.contracts import TelemetryCorrelation


TERMINAL_AT = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
NOW = TERMINAL_AT + timedelta(days=31)
POLICY = RunEvidenceRetentionPolicy(
    succeeded_days=30,
    failed_days=90,
    ambiguous_days=365,
)


def _principal_admin() -> RunEvidencePrincipal:
    return RunEvidencePrincipal(
        role=RunEvidenceRole.ADMIN,
        principal_id="admin",
    )


def _principal_owner(owner_id: str) -> RunEvidencePrincipal:
    return RunEvidencePrincipal(
        role=RunEvidenceRole.OWNER,
        principal_id=f"principal-{owner_id}",
        owner=RunEvidenceOwner(
            platform="qq",
            owner_type="user",
            owner_id=owner_id,
        ),
    )


def _ledger_event(
    *,
    run_id: str,
    event_id: str,
    event_type: str,
    status: str,
    owner_id: str,
    occurred_at: datetime,
    payload: dict[str, object] | None = None,
) -> RunLedgerEventDraft:
    return RunLedgerEventDraft(
        event_id=event_id,
        run_id=run_id,
        event_type=event_type,
        occurred_at=occurred_at,
        source="test.run_evidence",
        correlation=TelemetryCorrelation(
            request_id=f"request-{run_id}",
            session_id=f"session-{owner_id}",
            trace_id=f"trace-{run_id}",
            run_id=run_id,
        ),
        identity=RunLedgerIdentity(
            actor_type="user",
            actor_id=owner_id,
            owner_platform="qq",
            owner_type="user",
            owner_id=owner_id,
        ),
        status=status,
        payload=payload or {},
    )


def _seed_run(
    db,
    *,
    run_id: str = "run-governed",
    owner_id: str = "user-1",
    status: str = "succeeded",
    include_legacy: bool = True,
) -> None:
    ledger = SqlAlchemyRunEventLedger(db)
    ledger.append(_ledger_event(
        run_id=run_id,
        event_id=f"{run_id}-accepted",
        event_type="run.accepted",
        status="accepted",
        owner_id=owner_id,
        occurred_at=TERMINAL_AT - timedelta(minutes=2),
        payload={"model": "safe-model"},
    ))
    if status == "running":
        ledger.append(_ledger_event(
            run_id=run_id,
            event_id=f"{run_id}-running",
            event_type="run.status_changed",
            status="running",
            owner_id=owner_id,
            occurred_at=TERMINAL_AT - timedelta(minutes=1),
        ))
    else:
        ledger.append(_ledger_event(
            run_id=run_id,
            event_id=f"{run_id}-terminal",
            event_type="run.terminated",
            status=status,
            owner_id=owner_id,
            occurred_at=TERMINAL_AT,
            payload={"output_sha256": "a" * 64, "output_bytes": 18},
        ))
    if include_legacy:
        secret = "不得出现在导出清单中的旧诊断正文"
        db.add_all([
            AgentRun(
                run_id=run_id,
                trace_id=f"trace-{run_id}",
                session_id=f"session-{owner_id}",
                user_id=owner_id,
                status="success" if status == "succeeded" else status,
                input_preview=secret,
                output_preview=secret,
                error=secret,
                started_at=TERMINAL_AT - timedelta(minutes=2),
                finished_at=TERMINAL_AT if status != "running" else None,
            ),
            ToolCall(
                tool_call_id=f"tool-{run_id}",
                trace_id=f"trace-{run_id}",
                run_id=run_id,
                tool_name="secret_tool",
                args_json=json.dumps({"secret": secret}, ensure_ascii=False),
                result_preview=secret,
                error=secret,
                status="success",
            ),
            PromptRenderLog(
                trace_id=f"trace-{run_id}",
                run_id=run_id,
                prompt_key="chat_private",
                variables_json=json.dumps({"secret": secret}, ensure_ascii=False),
                rendered_preview=secret,
            ),
            LLMApiRequestLog(
                trace_id=f"trace-{run_id}",
                run_id=run_id,
                provider="test",
                model="safe-model",
                request_json=json.dumps({"secret": secret}, ensure_ascii=False),
                headers_json=json.dumps({"Authorization": secret}),
                response_json=json.dumps({"secret": secret}, ensure_ascii=False),
            ),
            ReplyContractCheckLog(
                trace_id=f"trace-{run_id}",
                run_id=run_id,
                session_id=f"session-{owner_id}",
                raw_output_preview=secret,
                result="valid",
            ),
            RuntimeTelemetryEvent(
                event_id=f"telemetry-{run_id}",
                name="run.test",
                domain="runtime",
                phase="end",
                occurred_at=TERMINAL_AT,
                run_id=run_id,
                registry_generation=1,
                registry_sha256="b" * 64,
                module_id="test",
                module_version="1",
            ),
        ])
    db.commit()


def _service(db) -> RunEvidenceGovernanceService:
    return RunEvidenceGovernanceService(db, policy=POLICY, now=NOW)


def _seed_recovery_evidence(db, run_id: str = "run-governed") -> None:
    checkpoint_id = f"checkpoint-{run_id}"
    db.add_all([
        RunCheckpointRow(
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            sequence=1,
            schema_version=1,
            boundary="turn_completed",
            turn_id=f"turn-{run_id}",
            correlation_id=f"trace-{run_id}",
            actor_type="user",
            actor_id="user-1",
            owner_platform="qq",
            owner_type="user",
            owner_id="user-1",
            runtime_id="native:test",
            runtime_protocol_version="1.0",
            resumable=True,
            manifest_sha256="1" * 64,
            prompt_sha256="2" * 64,
            model_route_sha256="3" * 64,
            tool_plan_sha256="4" * 64,
            workspace_sha256="5" * 64,
            artifact_set_sha256="6" * 64,
            security_sha256="7" * 64,
            version_proofs_sha256="8" * 64,
            file_proofs_sha256="9" * 64,
            artifact_proofs_sha256="a" * 64,
            payload_encoding="json+gzip",
            payload_blob=b"x",
            payload_size_bytes=1,
            payload_sha256="b" * 64,
            state_sha256="c" * 64,
            ledger_sequence=1,
            ledger_event_sha256="d" * 64,
            created_at=TERMINAL_AT,
        ),
        RunSideEffectReceipt(
            receipt_id=f"effect-{run_id}",
            run_id=run_id,
            tool_call_id=f"call-{run_id}",
            tool_name="schedule_task",
            execution_port_id="tool.schedule_task.execute",
            effect_class="local_write",
            state="completed",
            idempotency_key_sha256="e" * 64,
            request_sha256="f" * 64,
            result_sha256="0" * 64,
            result_size_bytes=1,
            checkpoint_before_id=checkpoint_id,
            checkpoint_after_id=checkpoint_id,
            prepared_ledger_sequence=1,
            terminal_ledger_sequence=2,
            prepared_at=TERMINAL_AT,
            settled_at=TERMINAL_AT,
        ),
        RunRecoveryOperation(
            operation_id=f"recovery-{run_id}",
            request_id_sha256="1" * 64,
            request_fingerprint_sha256="2" * 64,
            operation_kind="fork",
            run_id=run_id,
            restored_checkpoint_id=checkpoint_id,
            source_run_id_sha256="3" * 64,
            source_checkpoint_id_sha256="4" * 64,
            source_checkpoint_sha256="5" * 64,
            source_head_sequence=2,
            source_head_sha256="6" * 64,
            owner_platform="qq",
            owner_type="user",
            owner_id="user-1",
            status="succeeded",
            prepared_at=TERMINAL_AT,
            updated_at=TERMINAL_AT,
            finished_at=TERMINAL_AT,
        ),
    ])
    db.commit()


def test_run_evidence_acl_is_exact_and_ownerless_legacy_is_admin_only(db_session):
    _seed_run(db_session)
    service = _service(db_session)

    assert service.export_manifest(
        "run-governed",
        _principal_owner("user-1"),
    ).snapshot.owner.owner_id == "user-1"
    with pytest.raises(RunEvidenceAccessDenied):
        service.export_manifest(
            "run-governed",
            _principal_owner("user-2"),
        )

    scoped_service = RunEvidencePrincipal(
        role=RunEvidenceRole.SERVICE,
        principal_id="export-worker",
        scoped_run_id="run-governed",
    )
    assert service.export_manifest(
        "run-governed",
        scoped_service,
    ).snapshot.run_id == "run-governed"
    with pytest.raises(RunEvidenceAccessDenied, match="Service"):
        service.erasure_preview(
            run_id="run-governed",
            reason="privacy_request",
            principal=scoped_service,
        )

    db_session.add(AgentRun(
        run_id="legacy-ownerless",
        user_id="untrusted-owner-field",
        status="success",
        finished_at=TERMINAL_AT,
    ))
    db_session.commit()
    with pytest.raises(RunEvidenceAccessDenied):
        service.export_manifest(
            "legacy-ownerless",
            _principal_owner("untrusted-owner-field"),
        )
    assert service.export_manifest(
        "legacy-ownerless",
        _principal_admin(),
    ).snapshot.owner.declared is False


def test_export_manifest_is_stable_verifiable_and_contains_no_raw_bodies(db_session):
    _seed_run(db_session)
    service = _service(db_session)

    first = service.export_manifest("run-governed", _principal_admin()).to_dict()
    second = service.export_manifest("run-governed", _principal_admin()).to_dict()
    serialized = json.dumps(first, ensure_ascii=False, sort_keys=True)

    assert first == second
    assert len(first["manifest_sha256"]) == 64
    assert "不得出现在导出清单中的旧诊断正文" not in serialized
    assert "Authorization" not in serialized
    assert "payload_json" not in serialized
    assert first["safety"] == {
        "ledger_payloads_exported": False,
        "legacy_rows_exported": False,
        "hidden_reasoning_exported": False,
        "secret_values_exported": False,
    }
    assert first["legacy_evidence"]["counts"] == {
        "agent_runs": 1,
        "llm_api_request_logs": 1,
        "prompt_render_logs": 1,
        "reply_contract_check_logs": 1,
        "run_checkpoints": 0,
            "run_recovery_operations": 0,
            "run_side_effect_receipts": 0,
            "run_task_controls": 0,
            "runtime_telemetry_events": 1,
        "tool_calls": 1,
    }
    assert all(
        len(value) == 64
        for value in first["legacy_evidence"]["aggregate_sha256"].values()
    )


def test_retention_policy_and_legal_hold_fail_closed(db_session):
    _seed_run(db_session, run_id="run-active", status="running")
    _seed_run(db_session, run_id="run-success")
    active_service = _service(db_session)

    with pytest.raises(RunEvidencePolicyDenied, match="活跃"):
        active_service.erasure_preview(
            run_id="run-active",
            reason="privacy_request",
            principal=_principal_admin(),
        )

    hold = active_service.place_legal_hold(
        run_id="run-success",
        hold_id="hold-1",
        reason_code="legal",
        principal=_principal_admin(),
    )
    assert hold["active"] is True
    with pytest.raises(RunEvidencePolicyDenied, match="法律保留"):
        active_service.erasure_preview(
            run_id="run-success",
            reason="privacy_request",
            principal=_principal_admin(),
        )
    released = active_service.release_legal_hold(
        run_id="run-success",
        hold_id="hold-1",
        principal=_principal_admin(),
    )
    assert released["active"] is False
    preview = active_service.erasure_preview(
        run_id="run-success",
        reason="retention_expired",
        principal=_principal_admin(),
    )
    assert preview["deletion_performed"] is False
    assert preview["retention"]["expired"] is True

    not_expired = RunEvidenceGovernanceService(
        db_session,
        policy=POLICY,
        now=TERMINAL_AT + timedelta(days=29),
    )
    with pytest.raises(RunEvidencePolicyDenied, match="尚未超过"):
        not_expired.erasure_preview(
            run_id="run-success",
            reason="retention_expired",
            principal=_principal_admin(),
        )
    assert not_expired.erasure_preview(
        run_id="run-success",
        reason="privacy_request",
        principal=_principal_admin(),
    )["reason_code"] == "privacy_request"


def test_erasure_removes_ledger_and_legacy_evidence_and_replays_receipt(db_session):
    _seed_run(db_session)
    _seed_recovery_evidence(db_session)
    service = _service(db_session)
    preview = service.erasure_preview(
        run_id="run-governed",
        reason="privacy_request",
        principal=_principal_admin(),
    )

    result = service.erase(
        run_id="run-governed",
        request_id="erase-request-1",
        confirm_run_id="run-governed",
        reason="privacy_request",
        expected_manifest_sha256=preview["expected_manifest_sha256"],
        principal=_principal_admin(),
    )
    assert result.idempotent_replay is False
    assert result.ledger_event_count == 2
    assert result.run_id_sha256 != "run-governed"
    assert db_session.query(RunLedgerEventRow).count() == 0
    assert db_session.query(RunLedgerStreamHead).count() == 0
    assert db_session.query(RunLedgerErasureAuthorization).count() == 0
    assert db_session.query(AgentRun).count() == 0
    assert db_session.query(ToolCall).count() == 0
    assert db_session.query(PromptRenderLog).count() == 0
    assert db_session.query(LLMApiRequestLog).count() == 0
    assert db_session.query(ReplyContractCheckLog).count() == 0
    assert db_session.query(RuntimeTelemetryEvent).count() == 0
    assert db_session.query(RunCheckpointRow).count() == 0
    assert db_session.query(RunSideEffectReceipt).count() == 0
    assert db_session.query(RunRecoveryOperation).count() == 0
    assert result.legacy_counts["run_checkpoints"] == 1
    assert result.legacy_counts["run_side_effect_receipts"] == 1
    assert result.legacy_counts["run_recovery_operations"] == 1

    receipt = db_session.query(RunLedgerErasureReceipt).one()
    assert not hasattr(receipt, "request_id")
    assert receipt.run_id_sha256 == result.run_id_sha256
    assert "run-governed" not in json.dumps(
        {
            column.name: str(getattr(receipt, column.name))
            for column in receipt.__table__.columns
        },
        ensure_ascii=False,
    )

    replay = service.erase(
        run_id="run-governed",
        request_id="erase-request-1",
        confirm_run_id="run-governed",
        reason="privacy_request",
        expected_manifest_sha256=preview["expected_manifest_sha256"],
        principal=_principal_admin(),
    )
    assert replay.idempotent_replay is True
    assert replay.receipt_id == result.receipt_id
    with pytest.raises(RunEvidenceConflict, match="request_id"):
        service.erase(
            run_id="run-governed",
            request_id="erase-request-1",
            confirm_run_id="run-governed",
            reason="retention_expired",
            expected_manifest_sha256=preview["expected_manifest_sha256"],
            principal=_principal_admin(),
        )


def test_erasure_rejects_wrong_confirmation_and_changed_manifest(db_session):
    _seed_run(db_session)
    service = _service(db_session)
    preview = service.erasure_preview(
        run_id="run-governed",
        reason="privacy_request",
        principal=_principal_admin(),
    )
    with pytest.raises(RunEvidenceConflict, match="确认"):
        service.erase(
            run_id="run-governed",
            request_id="erase-request-confirm",
            confirm_run_id="another-run",
            reason="privacy_request",
            expected_manifest_sha256=preview["expected_manifest_sha256"],
            principal=_principal_admin(),
        )

    row = db_session.query(ToolCall).one()
    row.status = "changed"
    db_session.commit()
    with pytest.raises(RunEvidenceConflict, match="清单已变化"):
        service.erase(
            run_id="run-governed",
            request_id="erase-request-stale",
            confirm_run_id="run-governed",
            reason="privacy_request",
            expected_manifest_sha256=preview["expected_manifest_sha256"],
            principal=_principal_admin(),
        )
    assert db_session.query(RunLedgerEventRow).count() == 2
    assert db_session.query(RunLedgerErasureAuthorization).count() == 0


def test_legacy_only_run_uses_same_manifest_and_erasure_policy(db_session):
    db_session.add_all([
        AgentRun(
            run_id="legacy-only-run",
            status="success",
            input_preview="旧输入正文",
            output_preview="旧输出正文",
            started_at=TERMINAL_AT - timedelta(minutes=1),
            finished_at=TERMINAL_AT,
        ),
        ToolCall(
            tool_call_id="legacy-only-tool",
            run_id="legacy-only-run",
            args_json='{"secret":"旧参数正文"}',
            result_preview="旧结果正文",
            status="success",
        ),
    ])
    db_session.commit()
    service = _service(db_session)
    manifest = service.export_manifest(
        "legacy-only-run",
        _principal_admin(),
    )
    serialized = json.dumps(manifest.to_dict(), ensure_ascii=False)
    assert manifest.to_dict()["ledger"]["present"] is False
    assert "旧输入正文" not in serialized
    assert "旧参数正文" not in serialized

    result = service.erase(
        run_id="legacy-only-run",
        request_id="erase-legacy-only",
        confirm_run_id="legacy-only-run",
        reason="privacy_request",
        expected_manifest_sha256=manifest.manifest_sha256,
        principal=_principal_admin(),
    )
    assert result.ledger_event_count == 0
    assert result.legacy_counts["agent_runs"] == 1
    assert result.legacy_counts["tool_calls"] == 1
    assert db_session.query(AgentRun).count() == 0
    assert db_session.query(ToolCall).count() == 0


def test_governance_migration_keeps_manual_and_expired_authorization_deletes_blocked():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    run_schema_migrations(engine)
    table_names = set(inspect(engine).get_table_names())
    assert {
        "run_ledger_erasure_authorizations",
        "run_ledger_erasure_receipts",
        "run_ledger_legal_holds",
    } <= table_names

    local_factory = sessionmaker(bind=engine)
    with local_factory() as db:
        _seed_run(db, include_legacy=False)
    with engine.begin() as connection:
        with pytest.raises(DatabaseError, match="append_only"):
            connection.execute(text(
                "DELETE FROM run_ledger_events "
                "WHERE run_id = 'run-governed'"
            ))
        with pytest.raises(DatabaseError, match="erasure_guard"):
            connection.execute(text(
                "DELETE FROM run_ledger_stream_heads "
                "WHERE run_id = 'run-governed'"
            ))

    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO run_ledger_erasure_authorizations "
            "(authorization_id, run_id, expected_event_count, requested_by, "
            "created_at, expires_at) VALUES "
            "('expired-auth', 'run-governed', 2, 'test', "
            "'2026-01-01 00:00:00', '2026-01-01 00:01:00')"
        ))
        with pytest.raises(DatabaseError, match="append_only"):
            connection.execute(text(
                "DELETE FROM run_ledger_events "
                "WHERE run_id = 'run-governed'"
            ))
    with engine.connect() as connection:
        version_count = connection.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260804_run_evidence_governance_v1'"
        )).scalar_one()
    assert version_count == 1

    with local_factory() as db:
        service = _service(db)
        preview = service.erasure_preview(
            run_id="run-governed",
            reason="privacy_request",
            principal=_principal_admin(),
        )
        result = service.erase(
            run_id="run-governed",
            request_id="migration-trigger-erasure",
            confirm_run_id="run-governed",
            reason="privacy_request",
            expected_manifest_sha256=preview["expected_manifest_sha256"],
            principal=_principal_admin(),
        )
        assert result.ledger_event_count == 2
        assert db.query(RunLedgerEventRow).count() == 0
        assert db.query(RunLedgerStreamHead).count() == 0
        assert db.query(RunLedgerErasureAuthorization).count() == 0
    engine.dispose()


def test_admin_run_evidence_routes_require_auth_and_enforce_safe_delete(
    client,
    db_session,
    monkeypatch,
):
    _seed_run(db_session)
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    base = "/api/v1/admin/agent-runs/run-governed/evidence"
    unauthorized = client.get(f"{base}/export-manifest")
    assert unauthorized.status_code == 401

    headers = {"Authorization": "Bearer test-token"}
    exported = client.get(f"{base}/export-manifest", headers=headers)
    assert exported.status_code == 200
    assert "不得出现在导出清单中的旧诊断正文" not in exported.text

    preview = client.post(
        f"{base}/erasure-preview",
        headers=headers,
        json={"reason_code": "privacy_request"},
    )
    assert preview.status_code == 200
    manifest_sha256 = preview.json()["expected_manifest_sha256"]

    wrong = client.request(
        "DELETE",
        base,
        headers=headers,
        json={
            "request_id": "admin-erasure-1",
            "confirm_run_id": "wrong-run",
            "reason_code": "privacy_request",
            "expected_manifest_sha256": manifest_sha256,
        },
    )
    assert wrong.status_code == 409
    assert "run-governed" not in json.dumps(wrong.json(), ensure_ascii=False)

    deleted = client.request(
        "DELETE",
        base,
        headers=headers,
        json={
            "request_id": "admin-erasure-1",
            "confirm_run_id": "run-governed",
            "reason_code": "privacy_request",
            "expected_manifest_sha256": manifest_sha256,
        },
    )
    assert deleted.status_code == 200
    assert deleted.json()["ledger_event_count"] == 2
    assert deleted.json()["idempotent_replay"] is False


def test_run_evidence_retention_settings_are_registered_and_ordered():
    from core.config_registry import SETTING_DEFS
    from core.settings_specs import validate_setting_values

    keys = (
        "run_ledger.retention_succeeded_days",
        "run_ledger.retention_failed_days",
        "run_ledger.retention_ambiguous_days",
    )
    assert [SETTING_DEFS[key].default for key in keys] == [30, 90, 365]
    assert all(SETTING_DEFS[key].dangerous for key in keys)
    validate_setting_values(
        SETTING_DEFS,
        dict(zip(keys, (30, 90, 365), strict=True)),
    )
    with pytest.raises(ValueError, match="成功 <= 失败 <= 不确定"):
        validate_setting_values(
            SETTING_DEFS,
            dict(zip(keys, (100, 90, 365), strict=True)),
        )
