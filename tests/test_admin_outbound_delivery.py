from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from core.database import (
    AdminAuditLog,
    Base,
    OutboundDeliveryAttempt,
    OutboundDeliveryCircuit,
    OutboundDeliveryControl,
    OutboundDeliveryOutbox,
    OutboundRun,
    ProactiveOutreachLog,
    get_db,
)
from core.outbound_delivery import (
    acquire_or_renew_delivery_writer,
    claim_due_outbox,
    endpoint_circuit_fingerprint,
    mark_delivery_request_started,
    settle_delivery_attempt,
)
from server import app


NOW = datetime(2026, 7, 15, 12, 0, 0)
ENDPOINT_KEY = "qq_push"
CONFIG_REVISION = "admin-test-revision"
PAYLOAD_CONTRACT_FINGERPRINT = "qq-envelope-v1"
RAW_SOURCE_ID = "source-target-secret"
RAW_TARGET = "target-secret-value"
RAW_PAYLOAD = "payload-secret-message"
RAW_RESPONSE = "response-body-secret"
RAW_WRITER_TOKEN = "writer-token-secret"
RAW_LEASE_TOKEN = "lease-token-secret"


def _canonical_json(value: dict) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AdminOutboundHarness:
    client: TestClient
    session_factory: sessionmaker


@pytest.fixture
def admin_outbound(tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(
        "api.admin.outbound_delivery_routes._utc_now",
        lambda: NOW,
    )
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-outbound.db'}",
        connect_args={"check_same_thread": False},
    )
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield AdminOutboundHarness(client, testing_session)
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _route_entries():
    def _iter_routes(routes, prefix: str = ""):
        for route in routes:
            endpoint = getattr(route, "endpoint", None)
            path = getattr(route, "path", None)
            if endpoint is not None and path is not None:
                yield prefix + path, route
                continue
            original_router = getattr(route, "original_router", None)
            if original_router is None:
                continue
            include_context = getattr(route, "include_context", None)
            include_prefix = getattr(include_context, "prefix", "")
            yield from _iter_routes(original_router.routes, prefix + include_prefix)

    return list(_iter_routes(app.routes))


def _seed_control(
    db: Session,
    *,
    source_type: str = "scheduled_task",
    mode: str = "outbox_active",
    epoch: int = 1,
    writer_version: int = 0,
    effective_from: datetime | None = None,
    with_writer_secret: bool = True,
) -> OutboundDeliveryControl:
    row = OutboundDeliveryControl(
        source_type=source_type,
        mode=mode,
        cutover_epoch=epoch,
        effective_from=effective_from or NOW - timedelta(hours=1),
        protocol_version=2,
        writer_version=writer_version,
        writer_owner="writer-owner-secret" if with_writer_secret else None,
        writer_token=RAW_WRITER_TOKEN if with_writer_secret else None,
        writer_lease_expires_at=(
            NOW - timedelta(minutes=1) if with_writer_secret else None
        ),
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def _seed_ledger(
    db: Session,
    *,
    status: str = "ambiguous",
    with_attempt: bool = True,
) -> tuple[OutboundRun, OutboundDeliveryOutbox]:
    _seed_control(db)
    snapshot = _canonical_json({"target_id": RAW_TARGET, "prompt": "source-secret"})
    contract = _canonical_json({"destination": RAW_TARGET, "token": RAW_WRITER_TOKEN})
    run_status = {
        "pending": "queued",
        "retry_wait": "queued",
        "blocked": "blocked",
        "ambiguous": "ambiguous",
        "delivered": "succeeded",
    }[status]
    run = OutboundRun(
        source_type="scheduled_task",
        source_id=RAW_SOURCE_ID,
        occurrence_key="admin-outbound-occurrence",
        source_revision="source-revision-secret",
        source_snapshot_json=snapshot,
        source_snapshot_sha256=_sha256(snapshot),
        delivery_contract_json=contract,
        delivery_contract_sha256=_sha256(contract),
        writer_owner="producer-owner-secret",
        writer_token=RAW_WRITER_TOKEN,
        writer_protocol_version=2,
        task_kind="ai_digest",
        scheduled_for=NOW,
        trigger_type="cron",
        status=run_status,
        attempted_at=NOW,
        generated_at=NOW,
        succeeded_at=NOW if status == "delivered" else None,
        failure_type="read_timeout" if status == "ambiguous" else "",
        failure_summary=RAW_RESPONSE,
        has_ambiguous_ancestor=status == "ambiguous",
        delivery_mode="outbox",
        cutover_epoch=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(run)
    db.flush()
    payload_json = _canonical_json({"content": RAW_PAYLOAD, "target": RAW_TARGET})
    outbox = OutboundDeliveryOutbox(
        run_id=int(run.id),
        idempotency_key="idempotency-secret",
        destination_snapshot_json=_canonical_json({"target_id": RAW_TARGET}),
        destination_fingerprint="destination-fingerprint-safe",
        target_type="private",
        endpoint_key=ENDPOINT_KEY,
        payload_json=payload_json,
        payload_sha256=_sha256(payload_json),
        status=status,
        next_attempt_at=(
            NOW + timedelta(minutes=5) if status == "retry_wait" else None
        ),
        allocated_attempt_count=1 if with_attempt else 0,
        request_started_count=1 if with_attempt else 0,
        max_attempts=3,
        retry_deadline_at=NOW + timedelta(days=1),
        last_error_type="read_timeout" if status == "ambiguous" else "",
        last_error_summary=RAW_RESPONSE,
        delivered_at=NOW if status == "delivered" else None,
        replay_sequence=0,
        replay_request_sha256="",
        cutover_epoch=1,
        endpoint_config_revision=CONFIG_REVISION,
        payload_contract_fingerprint=PAYLOAD_CONTRACT_FINGERPRINT,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(outbox)
    db.flush()
    run.active_outbox_id = int(outbox.id)
    if with_attempt:
        db.add(OutboundDeliveryAttempt(
            outbox_id=int(outbox.id),
            attempt_no=1,
            worker_owner="worker-owner-secret",
            lease_token=RAW_LEASE_TOKEN,
            status="ambiguous",
            transport_phase="read",
            request_started=True,
            endpoint_config_revision=CONFIG_REVISION,
            result_category="ambiguous",
            error_type="read_timeout",
            safe_summary=RAW_RESPONSE,
            settlement_request_sha256="a" * 64,
            started_at=NOW,
            request_started_at=NOW,
            completed_at=NOW,
            created_at=NOW,
        ))
    db.flush()
    return run, outbox


def _seed_open_circuit(db: Session) -> OutboundDeliveryCircuit:
    row = OutboundDeliveryCircuit(
        scope_type="endpoint",
        scope_fingerprint=endpoint_circuit_fingerprint(ENDPOINT_KEY),
        config_revision=CONFIG_REVISION,
        status="open",
        reason_type="unauthorized",
        opened_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def _assert_no_secret(response_text: str) -> None:
    for secret in (
        RAW_SOURCE_ID,
        RAW_TARGET,
        RAW_PAYLOAD,
        RAW_RESPONSE,
        RAW_WRITER_TOKEN,
        RAW_LEASE_TOKEN,
        "writer-owner-secret",
        "worker-owner-secret",
        "idempotency-secret",
    ):
        assert secret not in response_text


def test_admin_outbound_delivery_routes_are_registered():
    expected = {
        ("GET", "/api/v1/admin/outbound-delivery/runs"),
        ("GET", "/api/v1/admin/outbound-delivery/runs/{run_id}"),
        ("GET", "/api/v1/admin/outbound-delivery/outboxes"),
        ("GET", "/api/v1/admin/outbound-delivery/outboxes/{outbox_id}"),
        ("GET", "/api/v1/admin/outbound-delivery/outboxes/{outbox_id}/attempts"),
        ("GET", "/api/v1/admin/outbound-delivery/circuits"),
        ("GET", "/api/v1/admin/outbound-delivery/controls"),
        ("GET", "/api/v1/admin/outbound-delivery/legacy-proactive"),
        ("POST", "/api/v1/admin/outbound-delivery/outboxes/{outbox_id}/replay"),
        ("POST", "/api/v1/admin/outbound-delivery/outboxes/{outbox_id}/cancel"),
        ("POST", "/api/v1/admin/outbound-delivery/circuits/{circuit_id}/reset"),
        ("POST", "/api/v1/admin/outbound-delivery/controls/{source_type}/transition"),
        ("POST", "/api/v1/admin/outbound-delivery/legacy-proactive/{log_id}/resolve"),
    }
    entries = _route_entries()
    for method, path in expected:
        matches = [
            route
            for route_path, route in entries
            if route_path == path and method in getattr(route, "methods", set())
        ]
        assert matches, f"缺少路由: {method} {path}"
        assert {route.endpoint.__module__ for route in matches} == {
            "api.admin.outbound_delivery_routes"
        }


def test_admin_outbound_delivery_requires_valid_admin_token(admin_outbound):
    path = "/api/v1/admin/outbound-delivery/runs"
    assert admin_outbound.client.get(path).status_code == 401
    assert admin_outbound.client.get(
        path,
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401


def test_admin_outbound_queries_use_exact_redacted_field_allowlists(admin_outbound):
    with admin_outbound.session_factory() as db:
        run, outbox = _seed_ledger(db)
        circuit = _seed_open_circuit(db)
        db.commit()
        run_id = int(run.id)
        outbox_id = int(outbox.id)
        circuit_id = int(circuit.id)

    responses = {
        "runs": admin_outbound.client.get(
            "/api/v1/admin/outbound-delivery/runs",
            headers=_auth(),
        ),
        "run": admin_outbound.client.get(
            f"/api/v1/admin/outbound-delivery/runs/{run_id}",
            headers=_auth(),
        ),
        "outboxes": admin_outbound.client.get(
            "/api/v1/admin/outbound-delivery/outboxes",
            headers=_auth(),
        ),
        "outbox": admin_outbound.client.get(
            f"/api/v1/admin/outbound-delivery/outboxes/{outbox_id}",
            headers=_auth(),
        ),
        "attempts": admin_outbound.client.get(
            f"/api/v1/admin/outbound-delivery/outboxes/{outbox_id}/attempts",
            headers=_auth(),
        ),
        "circuits": admin_outbound.client.get(
            "/api/v1/admin/outbound-delivery/circuits",
            headers=_auth(),
        ),
        "controls": admin_outbound.client.get(
            "/api/v1/admin/outbound-delivery/controls",
            headers=_auth(),
        ),
    }
    for response in responses.values():
        assert response.status_code == 200, response.text
        _assert_no_secret(response.text)

    run_keys = {
        "id", "source_type", "source_id_fingerprint", "status", "task_kind",
        "trigger_type", "scheduled_for", "attempted_at", "generated_at",
        "succeeded_at", "failure_type", "active_outbox_id",
        "has_ambiguous_ancestor", "delivery_mode", "cutover_epoch",
        "created_at", "updated_at",
    }
    outbox_keys = {
        "id", "run_id", "source_type", "status", "target_type", "endpoint_key",
        "destination_fingerprint", "payload_sha256_prefix",
        "allocated_attempt_count", "request_started_count", "max_attempts",
        "retry_deadline_at", "next_attempt_at", "last_error_type", "delivered_at",
        "cancelled_at", "cancel_reason_type", "replay_of_outbox_id",
        "replay_sequence", "cutover_epoch", "endpoint_config_revision",
        "created_at", "updated_at",
    }
    attempt_keys = {
        "id", "outbox_id", "attempt_no", "status", "transport_phase",
        "request_started", "endpoint_config_revision", "http_status",
        "result_category", "error_type", "duration_ms", "settlement_retry_at",
        "started_at", "request_started_at", "completed_at", "created_at",
    }
    circuit_keys = {
        "id", "scope_type", "scope_fingerprint", "config_revision", "status",
        "reason_type", "opened_at", "opened_by_attempt_id", "created_at",
        "updated_at",
    }
    control_keys = {
        "source_type", "mode", "cutover_epoch", "effective_from",
        "protocol_version", "writer_version", "writer_lease_expires_at",
        "created_at", "updated_at",
    }
    assert set(responses["runs"].json()["items"][0]) == run_keys
    assert set(responses["run"].json()) == run_keys
    assert set(responses["outboxes"].json()["items"][0]) == outbox_keys
    assert set(responses["outbox"].json()) == outbox_keys
    assert set(responses["attempts"].json()["items"][0]) == attempt_keys
    assert set(responses["circuits"].json()["items"][0]) == circuit_keys
    assert set(responses["controls"].json()["items"][0]) == control_keys
    assert responses["outbox"].json()["payload_sha256_prefix"] == _sha256(
        _canonical_json({"content": RAW_PAYLOAD, "target": RAW_TARGET})
    )[:12]
    assert circuit_id > 0


def test_replay_requires_confirmation_and_closed_current_circuit(admin_outbound):
    with admin_outbound.session_factory() as db:
        _run, outbox = _seed_ledger(db, with_attempt=False)
        db.commit()
        outbox_id = int(outbox.id)
    body = {
        "manual_request_key": "operator-request-1",
        "confirm_duplicate_risk": False,
        "reason": "人工确认未知投递风险",
        "max_attempts": 2,
        "retry_deadline_at": (NOW + timedelta(days=1)).isoformat(),
    }
    rejected = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/outboxes/{outbox_id}/replay",
        json=body,
        headers=_auth(),
    )
    assert rejected.status_code == 409

    with admin_outbound.session_factory() as db:
        _seed_open_circuit(db)
        db.commit()
    body["confirm_duplicate_risk"] = True
    blocked = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/outboxes/{outbox_id}/replay",
        json=body,
        headers=_auth(),
    )
    assert blocked.status_code == 409


def test_replay_uses_route_clock_instead_of_core_default(
    admin_outbound,
    monkeypatch,
):
    from api.admin import outbound_delivery_routes
    from core import outbound_delivery as outbound_delivery_state

    with admin_outbound.session_factory() as db:
        _run, parent = _seed_ledger(db, with_attempt=False)
        db.commit()
        parent_id = int(parent.id)

    real_utc_naive = outbound_delivery_state._utc_naive

    def shifted_utc_naive(value=None):
        if value is None:
            return NOW + timedelta(days=2)
        return real_utc_naive(value)

    monkeypatch.setattr(
        outbound_delivery_state,
        "_utc_naive",
        shifted_utc_naive,
    )
    monkeypatch.setattr(
        outbound_delivery_routes,
        "_utc_now",
        lambda: NOW,
        raising=False,
    )

    response = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/outboxes/{parent_id}/replay",
        json={
            "manual_request_key": "operator-request-fixed-clock",
            "confirm_duplicate_risk": True,
            "reason": "验证管理路由显式时钟",
            "max_attempts": 2,
            "retry_deadline_at": (NOW + timedelta(days=1)).isoformat(),
        },
        headers=_auth(),
    )

    assert response.status_code == 200, response.text


def test_replay_reads_revision_without_worker_push_token(
    admin_outbound,
    monkeypatch,
):
    monkeypatch.delenv("NANOBOT_PUSH_TOKEN", raising=False)
    monkeypatch.setenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", CONFIG_REVISION)
    with admin_outbound.session_factory() as db:
        _run, parent = _seed_ledger(db, with_attempt=False)
        db.commit()
        parent_id = int(parent.id)

    response = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/outboxes/{parent_id}/replay",
        json={
            "manual_request_key": "operator-request-server-config",
            "confirm_duplicate_risk": True,
            "reason": "验证管理端只依赖出站配置版本",
            "max_attempts": 2,
            "retry_deadline_at": (NOW + timedelta(days=1)).isoformat(),
        },
        headers=_auth(),
    )

    assert response.status_code == 200, response.text
    with admin_outbound.session_factory() as db:
        child = db.get(
            OutboundDeliveryOutbox,
            int(response.json()["outbox_id"]),
        )
        assert child is not None
        assert child.endpoint_config_revision == CONFIG_REVISION


def test_replay_creates_child_without_changing_ambiguous_parent(admin_outbound):
    with admin_outbound.session_factory() as db:
        run, parent = _seed_ledger(db, with_attempt=False)
        db.commit()
        run_id = int(run.id)
        parent_id = int(parent.id)
    response = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/outboxes/{parent_id}/replay",
        json={
            "manual_request_key": "operator-request-2",
            "confirm_duplicate_risk": True,
            "reason": "人工确认重复投递风险",
            "max_attempts": 2,
            "retry_deadline_at": (NOW + timedelta(days=1)).isoformat(),
        },
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    _assert_no_secret(response.text)
    child_id = response.json()["outbox_id"]
    with admin_outbound.session_factory() as db:
        parent = db.get(OutboundDeliveryOutbox, parent_id)
        child = db.get(OutboundDeliveryOutbox, child_id)
        run = db.get(OutboundRun, run_id)
        audits = db.query(AdminAuditLog).filter_by(
            action="replay_outbound_delivery",
        ).all()
        assert parent is not None and parent.status == "ambiguous"
        assert child is not None and child.replay_of_outbox_id == parent_id
        assert child.replay_sequence == 1
        assert run is not None and run.active_outbox_id == child_id
        assert len(audits) == 1
        assert "operator-request-2" not in audits[0].detail_json


def test_idempotent_replay_reports_existing_delivered_child_and_risk_status(
    admin_outbound,
):
    with admin_outbound.session_factory() as db:
        _run, parent = _seed_ledger(db, with_attempt=False)
        db.commit()
        parent_id = int(parent.id)
    body = {
        "manual_request_key": "operator-request-settled",
        "confirm_duplicate_risk": True,
        "reason": "人工确认重复投递风险",
        "max_attempts": 2,
        "retry_deadline_at": (NOW + timedelta(days=1)).isoformat(),
    }
    created = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/outboxes/{parent_id}/replay",
        json=body,
        headers=_auth(),
    )
    assert created.status_code == 200, created.text
    child_id = int(created.json()["outbox_id"])

    with admin_outbound.session_factory() as db:
        delivery = claim_due_outbox(
            db,
            worker_owner="worker-settle-replay",
            lease_seconds=30,
            endpoint_config_revision=CONFIG_REVISION,
            now=NOW + timedelta(seconds=1),
        )
        assert delivery is not None and delivery.outbox_id == child_id
        mark_delivery_request_started(
            db,
            outbox_id=child_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            now=NOW + timedelta(seconds=2),
        )
        settle_delivery_attempt(
            db,
            outbox_id=child_id,
            attempt_id=delivery.attempt_id,
            worker_owner=delivery.worker_owner,
            lease_token=delivery.lease_token,
            outcome="succeeded",
            transport_phase="response_received",
            http_status=204,
            result_category="success",
            error_type="",
            safe_summary="",
            duration_ms=10,
            now=NOW + timedelta(seconds=3),
        )
        db.commit()

    repeated = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/outboxes/{parent_id}/replay",
        json=body,
        headers=_auth(),
    )

    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["created"] is False
    assert repeated.json()["outbox_id"] == child_id
    assert repeated.json()["status"] == "delivered"
    assert repeated.json()["run_status"] == (
        "succeeded_after_ambiguous_replay"
    )
    with admin_outbound.session_factory() as db:
        rows = db.query(OutboundDeliveryOutbox).order_by(
            OutboundDeliveryOutbox.replay_sequence
        ).all()
        assert [row.id for row in rows] == [parent_id, child_id]


def test_delivered_outbox_cannot_be_replayed(admin_outbound):
    with admin_outbound.session_factory() as db:
        _run, outbox = _seed_ledger(db, status="delivered", with_attempt=False)
        db.commit()
        outbox_id = int(outbox.id)
    response = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/outboxes/{outbox_id}/replay",
        json={
            "manual_request_key": "operator-request-delivered",
            "confirm_duplicate_risk": True,
            "reason": "错误尝试",
            "max_attempts": 1,
            "retry_deadline_at": (NOW + timedelta(days=1)).isoformat(),
        },
        headers=_auth(),
    )
    assert response.status_code == 409


def test_circuit_reset_requires_reason_and_expected_updated_at(admin_outbound):
    with admin_outbound.session_factory() as db:
        circuit = _seed_open_circuit(db)
        db.commit()
        circuit_id = int(circuit.id)
    empty_reason = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/circuits/{circuit_id}/reset",
        json={"expected_updated_at": NOW.isoformat(), "reason": "   "},
        headers=_auth(),
    )
    assert empty_reason.status_code == 422
    stale = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/circuits/{circuit_id}/reset",
        json={
            "expected_updated_at": (NOW - timedelta(seconds=1)).isoformat(),
            "reason": "配置已经修复",
        },
        headers=_auth(),
    )
    assert stale.status_code == 409
    response = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/circuits/{circuit_id}/reset",
        json={"expected_updated_at": NOW.isoformat(), "reason": "配置已经修复"},
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    with admin_outbound.session_factory() as db:
        assert db.get(OutboundDeliveryCircuit, circuit_id).status == "closed"
        assert db.query(AdminAuditLog).filter_by(
            action="reset_outbound_delivery_circuit",
        ).count() == 1


def test_cancel_only_changes_safe_unleased_leaf_and_writes_audit(admin_outbound):
    with admin_outbound.session_factory() as db:
        run, outbox = _seed_ledger(db, status="pending", with_attempt=False)
        db.commit()
        run_id = int(run.id)
        outbox_id = int(outbox.id)
    response = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/outboxes/{outbox_id}/cancel",
        json={"reason": "管理员明确取消"},
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    with admin_outbound.session_factory() as db:
        assert db.get(OutboundDeliveryOutbox, outbox_id).status == "cancelled"
        assert db.get(OutboundRun, run_id).status == "failed"
        assert db.query(AdminAuditLog).filter_by(
            action="cancel_outbound_delivery",
        ).count() == 1


def test_audit_failure_rolls_back_outbox_cancel(admin_outbound):
    with admin_outbound.session_factory() as db:
        run, outbox = _seed_ledger(db, status="pending", with_attempt=False)
        db.commit()
        run_id = int(run.id)
        outbox_id = int(outbox.id)

    def fail_admin_audit(session, _flush_context, _instances):
        if any(isinstance(item, AdminAuditLog) for item in session.new):
            raise RuntimeError("audit-write-failed-secret")

    event.listen(Session, "before_flush", fail_admin_audit)
    try:
        response = admin_outbound.client.post(
            f"/api/v1/admin/outbound-delivery/outboxes/{outbox_id}/cancel",
            json={"reason": "管理员明确取消"},
            headers=_auth(),
        )
    finally:
        event.remove(Session, "before_flush", fail_admin_audit)
    assert response.status_code == 500
    assert "audit-write-failed-secret" not in response.text
    with admin_outbound.session_factory() as db:
        assert db.get(OutboundDeliveryOutbox, outbox_id).status == "pending"
        assert db.get(OutboundRun, run_id).status == "queued"
        assert db.query(AdminAuditLog).count() == 0


def test_control_transition_uses_server_writer_identity_and_reason(admin_outbound):
    current = NOW
    boundary = current + timedelta(minutes=5)
    with admin_outbound.session_factory() as db:
        _seed_control(
            db,
            source_type="proactive_outreach",
            mode="legacy_direct",
            epoch=0,
            effective_from=current - timedelta(hours=1),
            with_writer_secret=False,
        )
        db.commit()
    body = {
        "expected_mode": "legacy_direct",
        "new_mode": "outbox_hold",
        "expected_writer_version": 0,
        "effective_from": boundary.isoformat(),
        "reason": "开始安全切换",
    }
    response = admin_outbound.client.post(
        "/api/v1/admin/outbound-delivery/controls/proactive_outreach/transition",
        json=body,
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    assert "token" not in response.text.lower()
    assert "owner" not in response.text.lower()
    with admin_outbound.session_factory() as db:
        producer = acquire_or_renew_delivery_writer(
            db,
            source_type="proactive_outreach",
            owner="real-producer",
            token="real-producer-token",
            protocol_version=2,
            lease_seconds=60,
            now=current + timedelta(seconds=1),
        )
        assert producer.acquired is True
        assert producer.writer_version == response.json()["writer_version"] + 1
        control = db.get(OutboundDeliveryControl, "proactive_outreach")
        assert control is not None and control.mode == "outbox_hold"
        assert control.cutover_epoch == 1
        assert control.writer_owner == "real-producer"
        assert control.writer_token == "real-producer-token"
        audit = db.query(AdminAuditLog).filter_by(
            action="transition_outbound_delivery_control",
        ).one()
        assert RAW_WRITER_TOKEN not in audit.detail_json


def test_control_transition_rejects_writer_version_change_during_acquire(
    admin_outbound,
    monkeypatch,
):
    from api.admin import outbound_delivery_routes

    current = NOW
    boundary = current + timedelta(minutes=5)
    with admin_outbound.session_factory() as db:
        _seed_control(
            db,
            source_type="proactive_outreach",
            mode="legacy_direct",
            epoch=0,
            effective_from=current - timedelta(hours=1),
            with_writer_secret=False,
        )
        db.commit()

    real_acquire = (
        outbound_delivery_routes.outbound_delivery
        .acquire_or_renew_delivery_writer
    )

    def acquire_after_interleaved_version_change(db, **kwargs):
        control = db.get(OutboundDeliveryControl, kwargs["source_type"])
        assert control is not None
        control.writer_version = int(control.writer_version) + 1
        db.flush()
        return real_acquire(db, **kwargs)

    monkeypatch.setattr(
        outbound_delivery_routes.outbound_delivery,
        "acquire_or_renew_delivery_writer",
        acquire_after_interleaved_version_change,
    )
    response = admin_outbound.client.post(
        "/api/v1/admin/outbound-delivery/controls/proactive_outreach/transition",
        json={
            "expected_mode": "legacy_direct",
            "new_mode": "outbox_hold",
            "expected_writer_version": 0,
            "effective_from": boundary.isoformat(),
            "reason": "验证并发 CAS",
        },
        headers=_auth(),
    )

    assert response.status_code == 409
    with admin_outbound.session_factory() as db:
        control = db.get(OutboundDeliveryControl, "proactive_outreach")
        assert control is not None and control.mode == "legacy_direct"
        assert control.writer_version == 0
        assert db.query(AdminAuditLog).count() == 0


def test_legacy_ambiguous_resolve_cancels_without_replay(admin_outbound):
    with admin_outbound.session_factory() as db:
        row = ProactiveOutreachLog(
            user_id=RAW_TARGET,
            idempotency_key="legacy-hold-admin",
            grounding_json=_canonical_json({"secret": RAW_PAYLOAD}),
            judge_should=True,
            judge_reason="legacy-private-reason",
            message=RAW_PAYLOAD,
            status="legacy_ambiguous_hold",
            outbound_run_id=None,
            created_at=NOW,
        )
        db.add(row)
        db.add_all([
            ProactiveOutreachLog(
                user_id="older-target",
                idempotency_key="legacy-hold-older",
                grounding_json="{}",
                judge_should=True,
                judge_reason="older hold",
                message="older message",
                status="legacy_ambiguous_hold",
                outbound_run_id=None,
                created_at=NOW - timedelta(minutes=1),
            ),
            ProactiveOutreachLog(
                user_id="cancelled-target",
                idempotency_key="legacy-already-cancelled",
                grounding_json="{}",
                judge_should=True,
                judge_reason="cancelled",
                message="cancelled message",
                status="cancelled",
                outbound_run_id=None,
                created_at=NOW + timedelta(minutes=1),
            ),
        ])
        db.flush()
        log_id = int(row.id)
        db.commit()

    listed = admin_outbound.client.get(
        "/api/v1/admin/outbound-delivery/legacy-proactive?page=1&limit=1",
        headers=_auth(),
    )
    assert listed.status_code == 200, listed.text
    _assert_no_secret(listed.text)
    payload = listed.json()
    assert set(payload) == {"total", "items", "page", "limit"}
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["limit"] == 1
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert set(item) == {
        "id",
        "source_type",
        "status",
        "created_at",
        "source_revision",
    }
    assert item["id"] == log_id
    assert item["source_type"] == "proactive_outreach"
    assert item["status"] == "legacy_ambiguous_hold"

    response = admin_outbound.client.post(
        f"/api/v1/admin/outbound-delivery/legacy-proactive/{log_id}/resolve",
        json={
            "resolution": "cancel_without_replay",
            "reason": "无法证明历史消息已投递",
            "expected_created_at": item["created_at"],
            "expected_source_revision": item["source_revision"],
        },
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    _assert_no_secret(response.text)
    with admin_outbound.session_factory() as db:
        resolved = db.get(ProactiveOutreachLog, log_id)
        assert resolved is not None and resolved.status == "cancelled"
        assert resolved.message == RAW_PAYLOAD
        assert resolved.outbound_run_id is None
        assert db.query(OutboundRun).count() == 0
        assert db.query(OutboundDeliveryOutbox).count() == 0
        assert db.query(AdminAuditLog).filter_by(
            action="resolve_legacy_ambiguous_outreach",
        ).count() == 1
