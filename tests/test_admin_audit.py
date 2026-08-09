"""高风险治理操作的事务审计与跨存储 outbox 测试。"""

from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from api.admin.common import audit
from core.admin_audit import (
    finalize_external_admin_audit,
    load_admin_audit_intent,
    prepare_external_admin_audit,
    reconcile_prepared_admin_audit_intents,
)
from core.database import AdminAuditLog, AdminAuditOutboxRow


def test_legacy_audit_failure_rolls_back_and_keeps_session_usable(db_session):
    def fail_admin_audit(session, _flush_context, _instances):
        if any(isinstance(item, AdminAuditLog) for item in session.new):
            raise RuntimeError("simulated legacy audit failure")

    event.listen(Session, "before_flush", fail_admin_audit)
    try:
        audit(
            db_session,
            "ordinary.telemetry",
            "test",
            "target-1",
            {"safe": True},
        )
    finally:
        event.remove(Session, "before_flush", fail_admin_audit)

    assert db_session.query(AdminAuditLog).count() == 0
    assert db_session.execute(text("SELECT 1")).scalar_one() == 1


def test_restart_promotes_prepared_audit_to_ambiguous_and_allows_recovery(
    db_session,
):
    intent = prepare_external_admin_audit(
        db_session,
        event_id="audit_restart_recovery",
        action="evolution.activate",
        target_type="evolution_release",
        target_id="approval-1",
        detail={"approval_token_recorded": False},
        ip_address="127.0.0.1",
    )

    assert intent.status == "prepared"
    assert reconcile_prepared_admin_audit_intents(db_session) == 1
    assert reconcile_prepared_admin_audit_intents(db_session) == 0
    ambiguous = load_admin_audit_intent(db_session, intent.event_id)
    assert ambiguous is not None
    assert ambiguous.status == "ambiguous"
    assert ambiguous.last_error_code == (
        "process_restart_before_audit_finalization"
    )

    finalized = finalize_external_admin_audit(
        db_session,
        ambiguous,
        target_id="release-1",
        detail={"release_sha256": "a" * 64},
    )
    replay = finalize_external_admin_audit(
        db_session,
        ambiguous,
        target_id="release-1",
        detail={"release_sha256": "a" * 64},
    )

    assert finalized.status == replay.status == "finalized"
    assert db_session.query(AdminAuditOutboxRow).count() == 1
    assert db_session.query(AdminAuditLog).filter(
        AdminAuditLog.event_id.in_((
            "audit_restart_recovery:prepared",
            "audit_restart_recovery:finalized",
        ))
    ).count() == 2


def test_external_audit_commit_uncertainty_reads_back_without_duplicate(
    db_session,
    monkeypatch,
):
    original_commit = db_session.commit
    call_count = 0

    def uncertain_commit():
        nonlocal call_count
        call_count += 1
        original_commit()
        if call_count == 1:
            raise RuntimeError("simulated audit commit uncertainty")

    monkeypatch.setattr(db_session, "commit", uncertain_commit)
    intent = prepare_external_admin_audit(
        db_session,
        event_id="audit_uncertain_prepare",
        action="evolution.approve",
        target_type="evolution_approval",
        target_id="candidate-1",
        detail={"reviewer": "admin"},
    )
    monkeypatch.setattr(db_session, "commit", original_commit)

    assert intent.status == "prepared"
    assert db_session.query(AdminAuditOutboxRow).count() == 1
    assert db_session.query(AdminAuditLog).filter_by(
        event_id="audit_uncertain_prepare:prepared"
    ).count() == 1
