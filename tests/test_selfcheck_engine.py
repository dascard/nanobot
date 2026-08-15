"""确定性自检引擎、持久结果与 Worker 心跳测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


ALLOWED_RESULT_STATUSES = {
    "passed",
    "degraded",
    "failed",
    "inconclusive",
    "skipped",
}


def _agent_descriptor():
    from core.agent_runtime.registry import AgentRuntimeDescriptor

    return AgentRuntimeDescriptor(
        agent_id="testbot",
        display_name="TestBot",
        description="自检引擎测试 Agent",
        adapter="native",
        source_ref="creatures/testbot",
        source_sha256="2" * 64,
        runtime_policy_sha256="3" * 64,
        allowed_entrypoints=("chat", "agent_link"),
        default=True,
    )


def test_probe_registry_is_frozen_and_covers_broad_runtime_surfaces():
    from core.selfcheck.probes import SELFCHECK_PROBE_REGISTRY

    snapshot = SELFCHECK_PROBE_REGISTRY
    check_ids = {probe.check_id for probe in snapshot}

    assert snapshot.namespace == "selfcheck_probe"
    assert snapshot.generation == 1
    assert len(snapshot.sha256) == 64
    assert len(snapshot) >= 30
    assert {
        "api.openapi_contracts",
        "webui.route_manifest",
        "webui.critical-operation-bindings",
        "database.connectivity",
        "database.integrity",
        "session.database_only_default",
        "rag.group-memory.runtime",
        "rag.group-memory.smoke",
        "rag.group-analysis.runtime",
        "rag.group-analysis.smoke",
        "rag.all.runtime",
        "rag.all.smoke",
        "rag.index_queue",
        "rag.debug_history",
        "worker.outbound-delivery-worker.liveness",
        "worker.selfcheck-watchdog.liveness",
        "schedule.daily_digest",
        "schedule.proactive_outreach",
        "observability.model_calls",
        "memory.summary_quality",
        "storage.workspace_assets",
    } <= check_ids


def test_worker_heartbeat_upsert_preserves_counts_and_safe_error_code(db_session):
    from core.selfcheck.heartbeat import record_worker_cycle
    from core.db.models.selfcheck import WorkerHeartbeat

    now = datetime(2026, 8, 15, 12, 0, 0)
    record_worker_cycle(
        db_session,
        worker_id="semantic-index-worker",
        instance_id="worker-test-a",
        mode="external",
        success=True,
        now=now,
        metadata={"processed": 2},
    )
    record_worker_cycle(
        db_session,
        worker_id="semantic-index-worker",
        instance_id="worker-test-a",
        mode="external",
        success=False,
        now=now + timedelta(seconds=10),
        error_code="semantic_cycle_failed",
        metadata={"processed": 0},
    )
    db_session.commit()

    row = db_session.get(WorkerHeartbeat, "semantic-index-worker")
    assert row is not None
    assert row.instance_id == "worker-test-a"
    assert row.cycle_count == 2
    assert row.success_count == 1
    assert row.failure_count == 1
    assert row.last_success_at == now
    assert row.last_error_at == now + timedelta(seconds=10)
    assert row.last_error_code == "semantic_cycle_failed"
    assert "traceback" not in row.metadata_json.lower()


def test_deterministic_engine_runs_many_checks_and_persists_results(
    client,
    db_session,
):
    from core.db.models.selfcheck import SelfcheckResultRow, SelfcheckRunRow
    from core.selfcheck.engine import SelfcheckEngine

    engine = SelfcheckEngine(
        app=client.app,
        db=db_session,
        testing=True,
        agent_descriptors=(_agent_descriptor(),),
        now_provider=lambda: datetime(2026, 8, 15, 12, 0, 0),
    )
    report = engine.run(trigger="manual", requested_by="pytest")

    assert report.run_id
    assert report.status in {"passed", "degraded", "inconclusive"}, [
        (item.check_id, item.detail_code)
        for item in report.results
        if item.status == "failed"
    ]
    assert len(report.results) >= 30
    assert {item.status for item in report.results} <= ALLOWED_RESULT_STATUSES
    assert all(item.detail_code for item in report.results)
    assert all(item.duration_ms >= 0 for item in report.results)
    assert db_session.get(SelfcheckRunRow, report.run_id) is not None
    assert (
        db_session.query(SelfcheckResultRow)
        .filter(SelfcheckResultRow.run_id == report.run_id)
        .count()
        == len(report.results)
    )
    assert report.summary["total"] == len(report.results)
    assert sum(report.summary[status] for status in ALLOWED_RESULT_STATUSES) == len(
        report.results
    )


def test_engine_exposes_stale_worker_and_fallback_summary_regressions(
    client,
    db_session,
):
    from core.db.models import RollingSessionSummary
    from core.selfcheck.engine import SelfcheckEngine
    from core.selfcheck.heartbeat import record_worker_cycle

    now = datetime(2026, 8, 15, 12, 0, 0)
    record_worker_cycle(
        db_session,
        worker_id="outbound-delivery-worker",
        instance_id="stale-worker",
        mode="external",
        success=True,
        now=now - timedelta(minutes=30),
    )
    for index in range(4):
        db_session.add(RollingSessionSummary(
            session_id=f"session-{index}",
            summary_kind="deterministic_fallback",
            llm_status="failed",
            created_at=now - timedelta(hours=1),
        ))
    db_session.commit()

    report = SelfcheckEngine(
        app=client.app,
        db=db_session,
        testing=False,
        agent_descriptors=(_agent_descriptor(),),
        now_provider=lambda: now,
    ).run(
        trigger="manual",
        requested_by="pytest",
        check_ids=(
            "worker.outbound-delivery-worker.liveness",
            "memory.summary_quality",
        ),
    )
    results = {item.check_id: item for item in report.results}

    assert results[
        "worker.outbound-delivery-worker.liveness"
    ].status == "failed"
    assert results["memory.summary_quality"].status in {
        "degraded",
        "failed",
    }
    assert results["memory.summary_quality"].metrics["fallback_rate"] == 1.0


def test_scheduled_tasks_probe_compares_utc_naive_fields_with_utc_clock(
    client,
    db_session,
):
    from core.db.models import ScheduledTask
    from core.selfcheck.engine import SelfcheckEngine

    local_now = datetime(
        2026,
        8,
        15,
        23,
        48,
        tzinfo=timezone(timedelta(hours=8)),
    )
    db_session.add(ScheduledTask(
        name="UTC 时区回归任务",
        cron_expr="*/5 * * * *",
        schedule_kind="cron",
        schedule_spec="",
        next_fire_at=datetime(2026, 8, 15, 15, 50),
        enabled=1,
    ))
    db_session.commit()

    report = SelfcheckEngine(
        app=client.app,
        db=db_session,
        testing=False,
        now_provider=lambda: local_now,
    ).run(
        trigger="manual",
        requested_by="pytest",
        check_ids=("schedule.scheduled_tasks",),
    )

    result = report.results[0]
    assert result.status == "passed"
    assert result.detail_code == "scheduled_tasks_healthy"
    assert result.metrics == {
        "enabled": 1,
        "overdue": 0,
        "stale_running": 0,
    }


def test_capability_registry_links_only_implemented_probes(client):
    from core.selfcheck.capabilities import (
        build_capability_registry,
        capability_coverage_summary,
    )

    snapshot = build_capability_registry(
        client.app,
        agent_descriptors=(_agent_descriptor(),),
    )
    summary = capability_coverage_summary(snapshot)
    by_source = {(item.kind, item.source_id): item for item in snapshot}

    assert summary["covered"] > 0
    assert summary["unverified"] > 0
    assert "rag.group-memory.smoke" in by_source[
        ("rag_source", "group_memory")
    ].probe_ids
    assert "webui.critical-operation-bindings" in by_source[
        ("webui", "/rag-debug")
    ].probe_ids
    assert "webui.critical-operation-bindings" in by_source[
        ("webui", "/self-check")
    ].probe_ids
    assert "worker.outbound-delivery-worker.liveness" in by_source[
        ("worker", "outbound-delivery-worker")
    ].probe_ids


def test_every_probe_has_executor_and_model_probe_is_explicitly_gated():
    from core.selfcheck.engine import _EXECUTORS
    from core.selfcheck.probes import SELFCHECK_PROBE_REGISTRY

    assert all(
        probe.executor_key in _EXECUTORS
        for probe in SELFCHECK_PROBE_REGISTRY
    )
    model_probes = [
        probe for probe in SELFCHECK_PROBE_REGISTRY if probe.requires_model
    ]
    assert [probe.check_id for probe in model_probes] == [
        "model.reply-canary.functional"
    ]
    assert all(not probe.destructive for probe in SELFCHECK_PROBE_REGISTRY)


def test_probe_failure_is_isolated_and_later_results_are_persisted(
    client,
    db_session,
    monkeypatch,
):
    from core.db.models.selfcheck import SelfcheckResultRow
    from core.selfcheck import engine as selfcheck_engine

    def injected_failure(_context, _probe):
        raise RuntimeError("故障注入正文不得进入证据")

    monkeypatch.setitem(
        selfcheck_engine._EXECUTORS,
        "database.connectivity",
        injected_failure,
    )
    report = selfcheck_engine.SelfcheckEngine(
        app=client.app,
        db=db_session,
        testing=True,
    ).run(
        trigger="manual",
        requested_by="pytest",
        check_ids=("database.connectivity", "database.integrity"),
    )

    assert [item.status for item in report.results] == ["failed", "passed"]
    assert report.results[0].detail_code == "probe_execution_error"
    assert report.results[0].evidence == {"error_type": "RuntimeError"}
    assert (
        db_session.query(SelfcheckResultRow)
        .filter(SelfcheckResultRow.run_id == report.run_id)
        .count()
        == 2
    )
