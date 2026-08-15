"""定时推送与主动外呼自检质量判定。"""

from __future__ import annotations

from datetime import datetime, timedelta
import json


NOW = datetime(2026, 8, 15, 12, 0, 0)


def _run_check(client, db_session, check_id: str):
    from core.selfcheck.engine import SelfcheckEngine

    report = SelfcheckEngine(
        app=client.app,
        db=db_session,
        testing=False,
        now_provider=lambda: NOW,
    ).run(
        trigger="manual",
        requested_by="pytest",
        check_ids=(check_id,),
    )
    return report.results[0]


def test_scheduled_task_definition_probe_rejects_invalid_enabled_task(
    client,
    db_session,
):
    from core.db.models import ScheduledTask

    db_session.add(ScheduledTask(
        name="损坏的定时任务",
        cron_expr="not-a-cron",
        schedule_kind="cron",
        schedule_spec="",
        enabled=1,
    ))
    db_session.commit()

    result = _run_check(
        client,
        db_session,
        "schedule.task-definitions.functional",
    )

    assert result.status == "failed"
    assert result.metrics["invalid_schedule"] == 1
    assert result.metrics["invalid_snapshot"] == 1


def test_scheduled_delivery_probe_exposes_all_generation_failures(
    client,
    db_session,
):
    from core.db.models import OutboundGenerationAttempt, OutboundRun

    run = OutboundRun(
        source_type="scheduled_task",
        source_id="selfcheck-task-1",
        occurrence_key="selfcheck-occurrence-1",
        source_revision="revision-1",
        source_snapshot_json="{}",
        source_snapshot_sha256="a" * 64,
        delivery_contract_json="{}",
        delivery_contract_sha256="b" * 64,
        writer_owner="selfcheck-writer",
        writer_token="selfcheck-writer-token",
        writer_protocol_version=2,
        task_kind="ai_digest",
        scheduled_for=NOW - timedelta(hours=1),
        trigger_type="cron",
        status="failed",
        attempted_at=NOW - timedelta(hours=1),
        failure_type="generation_failed",
        failure_summary="",
        has_ambiguous_ancestor=False,
        delivery_mode="outbox",
        cutover_epoch=1,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(OutboundGenerationAttempt(
        run_id=int(run.id),
        attempt_no=1,
        owner="selfcheck-producer",
        fencing_token="selfcheck-fencing-token",
        status="failed",
        started_at=NOW - timedelta(hours=1),
        completed_at=NOW - timedelta(hours=1),
        error_type="provider_unavailable",
        error_summary="",
        created_at=NOW - timedelta(hours=1),
    ))
    db_session.commit()

    result = _run_check(
        client,
        db_session,
        "schedule.scheduled-delivery-quality",
    )

    assert result.status == "failed"
    assert result.detail_code == "scheduled_delivery_generation_all_failed"
    assert result.metrics["generation_failed"] == 1


def test_proactive_probe_rejects_forced_fallback_as_success(
    client,
    db_session,
    monkeypatch,
):
    from core.db.models import ProactiveOutreachLog
    from core.settings_service import settings

    original = settings.get_bool
    monkeypatch.setattr(
        settings,
        "get_bool",
        lambda key, default=False: (
            True if key == "proactive_outreach.enabled" else original(key, default)
        ),
    )
    for index in range(3):
        db_session.add(ProactiveOutreachLog(
            user_id=f"selfcheck-user-{index}",
            idempotency_key=f"selfcheck-proactive-{index}",
            grounding_json=json.dumps({
                "forced_fallback": True,
                "generation_error": "provider_unavailable",
            }),
            judge_should=True,
            message="测试 fallback 正文",
            status="sent",
            forced=True,
            created_at=NOW - timedelta(hours=1),
        ))
    db_session.commit()

    result = _run_check(
        client,
        db_session,
        "schedule.proactive_outreach",
    )

    assert result.status == "failed"
    assert result.detail_code == "proactive_outreach_all_forced_fallback"
    assert result.metrics["fallback_rate"] == 1.0
    assert "测试 fallback 正文" not in json.dumps(
        result.to_dict(),
        ensure_ascii=False,
    )
