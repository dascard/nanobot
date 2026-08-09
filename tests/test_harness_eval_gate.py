from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3

import pytest

from evals.harness_gate import (
    CheckExecutionEvidence,
    HarnessGateError,
    main,
    run_offline_gate,
    sample_online_readonly,
    validate_external_evidence,
)
from evals.harness_registry import (
    EVAL_HARNESS_REGISTRY,
    EvaluationLane,
    EvidenceAuthority,
    harness_catalog_payload,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class _FakeExecutor:
    def __init__(self, *, skipped_check: str = "") -> None:
        self.calls: list[str] = []
        self.skipped_check = skipped_check

    def execute(self, descriptor):
        self.calls.append(descriptor.check_id)
        skipped = 1 if descriptor.check_id == self.skipped_check else 0
        return CheckExecutionEvidence(
            check_id=descriptor.check_id,
            return_code=0,
            timed_out=False,
            tests=2,
            passed=2 - skipped,
            failures=0,
            errors=0,
            skipped=skipped,
            duration_ms=25,
            stdout_sha256=_sha("stdout"),
            stdout_bytes=100,
            stderr_sha256=_sha("stderr"),
            stderr_bytes=0,
        )


def _real_model_evidence() -> dict[str, object]:
    return {
        "schema_version": 1,
        "lane": "real_model_benchmark",
        "suite_id": "real_model_end_to_end",
        "run_id": "benchmark-run-001",
        "source_revision": "a" * 40,
        "dataset_sha256": _sha("benchmark-dataset"),
        "started_at": "2026-08-09T08:00:00+00:00",
        "finished_at": "2026-08-09T08:10:00+00:00",
        "metrics": {
            "case_count": 20,
            "completion_rate": 0.9,
            "quality_score": 0.85,
            "input_tokens": 20000,
            "output_tokens": 5000,
            "p95_latency_ms": 3500.0,
        },
        "model_call_count": 40,
        "production_write_count": 0,
        "production_data_access": False,
        "readonly": True,
        "explicit_opt_in": True,
        "approved_cost_microunits": 2_000_000,
        "actual_cost_microunits": 1_200_000,
        "raw_content_persisted": False,
        "artifact_sha256s": [_sha("benchmark-report")],
    }


def test_harness_registry_separates_three_lanes_and_evidence_authority():
    descriptors = tuple(EVAL_HARNESS_REGISTRY)
    offline = [
        item
        for item in descriptors
        if item.lane is EvaluationLane.OFFLINE_DETERMINISTIC
    ]
    real = EVAL_HARNESS_REGISTRY.require("real_model_end_to_end")
    online = EVAL_HARNESS_REGISTRY.require(
        "online_readonly_runtime_sample"
    )

    assert len(offline) == 5
    assert all(item.blocking for item in offline)
    assert all(item.authority is EvidenceAuthority.BLOCKING_GATE for item in offline)
    assert all(not item.allows_network for item in offline)
    assert all(not item.allows_model_calls for item in offline)
    assert all(item.production_data_mode == "forbidden" for item in offline)
    assert {
        domain for item in offline for domain in item.domains
    } >= {
        "runtime_contract",
        "native_runtime",
        "kt_adapter",
        "prompt_stability",
        "prefix_cache",
        "context_compaction",
        "memory_injection",
        "skill_selection",
        "mcp",
        "permission",
        "recovery",
        "cost",
        "long_task",
        "completion_rate",
        "collaboration_cost",
        "failure_propagation",
    }
    assert real.blocking is False
    assert real.authority is EvidenceAuthority.BENCHMARK_ONLY
    assert real.explicit_opt_in_required is True
    assert real.cost_budget_required is True
    assert online.blocking is False
    assert online.authority is EvidenceAuthority.READONLY_SIGNAL
    assert online.production_data_mode == "readonly"
    assert online.allows_model_calls is False
    skill_check = next(
        item
        for item in EVAL_HARNESS_REGISTRY.require(
            "offline_memory_skill_mcp"
        ).checks
        if item.check_id == "skill_selection"
    )
    assert "tests/test_skill_candidates.py" in skill_check.selectors


def test_harness_catalog_exposes_registry_hash_and_lane_authority():
    catalog = harness_catalog_payload()

    assert catalog["registry"]["sha256"] == EVAL_HARNESS_REGISTRY.sha256
    assert catalog["lane_authority"] == {
        "offline_deterministic": "blocking_gate",
        "real_model_benchmark": "benchmark_only",
        "online_readonly_sampling": "readonly_signal",
    }
    assert len(catalog["suites"]) == 7


def test_offline_gate_runs_fixed_checks_and_requires_zero_skips():
    executor = _FakeExecutor()
    report = run_offline_gate(
        suite_ids=("offline_runtime_equivalence",),
        executor=executor,
    )

    assert report["passed"] is True
    assert report["blocking"] is True
    assert report["network_allowed"] is False
    assert report["model_calls_allowed"] is False
    assert report["production_data_mode"] == "forbidden"
    assert report["summary"] == {
        "total_suites": 1,
        "passed_suites": 1,
        "total_checks": 3,
        "passed_checks": 3,
        "total_tests": 6,
        "passed_tests": 6,
        "pass_rate": 1.0,
        "wall_time_ms": 75,
    }
    assert executor.calls == [
        "runtime_contract_equivalence",
        "native_runtime_behavior",
        "kt_adapter_behavior",
    ]

    failed = run_offline_gate(
        suite_ids=("offline_runtime_equivalence",),
        executor=_FakeExecutor(skipped_check="kt_adapter_behavior"),
    )
    assert failed["passed"] is False
    assert failed["summary"]["passed_checks"] == 2
    assert failed["suites"][0]["checks"][2]["skipped"] == 1


def test_offline_gate_rejects_unknown_or_duplicate_suite():
    with pytest.raises(HarnessGateError, match="未知离线 suite"):
        run_offline_gate(suite_ids=("missing",), executor=_FakeExecutor())
    with pytest.raises(HarnessGateError, match="不能重复"):
        run_offline_gate(
            suite_ids=(
                "offline_runtime_equivalence",
                "offline_runtime_equivalence",
            ),
            executor=_FakeExecutor(),
        )


def test_real_model_evidence_requires_opt_in_cost_and_never_becomes_blocking():
    valid = validate_external_evidence(_real_model_evidence())

    assert valid["valid"] is True
    assert valid["blocking_eligible"] is False
    assert valid["authority"] == "benchmark_only"
    assert valid["policy_errors"] == []

    over_budget = _real_model_evidence()
    over_budget["actual_cost_microunits"] = 2_000_001
    report = validate_external_evidence(over_budget)
    assert report["valid"] is False
    assert report["policy_errors"] == ["cost_budget_exceeded"]

    invalid_rate = _real_model_evidence()
    invalid_rate["metrics"]["completion_rate"] = 1.01
    with pytest.raises(HarnessGateError, match="必须位于 0..1"):
        validate_external_evidence(invalid_rate)


def test_external_evidence_rejects_offline_import_and_raw_fields():
    offline = _real_model_evidence()
    offline["lane"] = "offline_deterministic"
    offline["suite_id"] = "offline_runtime_equivalence"
    with pytest.raises(HarnessGateError, match="必须实际执行"):
        validate_external_evidence(offline)

    raw = _real_model_evidence()
    raw["prompt"] = "不应进入证据"
    with pytest.raises(HarnessGateError, match="未允许字段"):
        validate_external_evidence(raw)


def test_online_evidence_forbids_model_calls_writes_and_raw_content():
    evidence = _real_model_evidence()
    evidence.update({
        "lane": "online_readonly_sampling",
        "suite_id": "online_readonly_runtime_sample",
        "metrics": {
            "sample_count": 10,
            "success_rate": 0.9,
            "recovery_rate": 1.0,
            "cache_hit_rate": 0.5,
            "average_cost_microunits": 12.0,
        },
        "model_call_count": 1,
        "production_data_access": True,
        "explicit_opt_in": False,
        "approved_cost_microunits": 0,
        "actual_cost_microunits": 0,
        "raw_content_persisted": True,
    })

    report = validate_external_evidence(evidence)

    assert report["valid"] is False
    assert report["authority"] == "readonly_signal"
    assert report["blocking_eligible"] is False
    assert report["policy_errors"] == [
        "raw_content_persisted",
        "model_calls_forbidden",
    ]


def test_online_sampler_uses_readonly_aggregates_without_content(tmp_path):
    database = tmp_path / "runtime.db"
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE agent_runs (status TEXT, started_at TEXT);
        CREATE TABLE run_recovery_operations (status TEXT, prepared_at TEXT);
        CREATE TABLE llm_api_request_logs (
            id INTEGER PRIMARY KEY,
            cache_hit INTEGER,
            cost_microusd INTEGER
        );
        INSERT INTO agent_runs VALUES ('succeeded', '2026-08-09T00:00:00');
        INSERT INTO agent_runs VALUES ('failed', '2026-08-08T00:00:00');
        INSERT INTO run_recovery_operations
            VALUES ('succeeded', '2026-08-09T00:00:00');
        INSERT INTO run_recovery_operations
            VALUES ('failed', '2026-08-08T00:00:00');
        INSERT INTO llm_api_request_logs VALUES (1, 1, 100);
        INSERT INTO llm_api_request_logs VALUES (2, 0, 300);
    """)
    connection.commit()
    connection.close()
    times = iter((
        datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 9, 8, 0, 1, tzinfo=timezone.utc),
    ))

    report = sample_online_readonly(
        database,
        limit=100,
        clock=lambda: next(times),
    )

    evidence = report["evidence"]
    assert report["validation"]["valid"] is True
    assert report["validation"]["blocking_eligible"] is False
    assert evidence["metrics"] == {
        "sample_count": 2,
        "success_rate": 0.5,
        "recovery_rate": 0.5,
        "cache_hit_rate": 0.5,
        "average_cost_microunits": 200.0,
    }
    assert evidence["model_call_count"] == 0
    assert evidence["production_write_count"] == 0
    assert evidence["raw_content_persisted"] is False
    check = sqlite3.connect(database)
    assert check.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 2
    check.close()
    assert str(report).find(str(database)) == -1


def test_harness_cli_catalog_and_evidence_validation(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    assert main(["catalog", "--output", str(catalog_path)]) == 0
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert catalog["lane_authority"]["offline_deterministic"] == (
        "blocking_gate"
    )

    evidence_path = tmp_path / "evidence.json"
    report_path = tmp_path / "evidence-report.json"
    evidence_path.write_text(
        json.dumps(_real_model_evidence()),
        encoding="utf-8",
    )
    assert main([
        "validate-evidence",
        "--input",
        str(evidence_path),
        "--output",
        str(report_path),
    ]) == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))[
        "blocking_eligible"
    ] is False


def test_admin_harness_catalog_exposes_same_frozen_registry(
    client,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.get(
        "/api/v1/admin/evals/harness/catalog",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["registry"]["sha256"] == EVAL_HARNESS_REGISTRY.sha256
    assert payload["lane_authority"]["real_model_benchmark"] == (
        "benchmark_only"
    )
