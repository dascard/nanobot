import ast
import asyncio
from copy import deepcopy
import importlib
import importlib.util
import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


REQUIRED_CASE_IDS = {
    "judge_complete",
    "judge_finish_reason_length",
    "judge_partial_json",
    "judge_schema_error",
    "empty_generator",
    "research_zero_sources",
    "research_one_source",
    "research_two_sources",
    "research_budget_exhausted",
    "quiet_hours",
    "min_interval",
    "surge_not_due",
    "surge_due",
    "max_silence",
    "duplicate_key",
    "research_timeout",
    "auto_publish",
    "dry_run",
}

REQUIRED_METRICS = {
    "external_push_count",
    "recorded_publish_count",
    "contract_success_rate",
    "truncation_rate",
    "source_coverage",
    "duplicate_rate",
    "timeout_rate",
}

VOLATILE_REPORT_KEYS = {
    "agent_run_id",
    "artifact_id",
    "generated_at",
    "run_id",
    "trace_id",
    "wall_duration_ms",
}


def _load_simulation_module() -> ModuleType:
    try:
        return importlib.import_module("core.proactive_simulation")
    except ModuleNotFoundError as exc:
        if exc.name != "core.proactive_simulation":
            raise
        pytest.fail(
            "尚未实现 core.proactive_simulation；这是 TDD 红灯的预期原因",
            pytrace=False,
        )


async def _run_simulation() -> dict[str, Any]:
    module = _load_simulation_module()
    runner = getattr(module, "run_accelerated_simulation", None)
    assert callable(runner), "缺少 async run_accelerated_simulation()"
    assert inspect.iscoroutinefunction(runner), "run_accelerated_simulation() 必须是 async 函数"
    report = await runner()
    assert isinstance(report, dict)
    return report


def _case_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = report.get("cases")
    assert isinstance(cases, list), "cases 必须是列表"
    assert all(isinstance(case, dict) for case in cases)
    case_ids = [str(case.get("case_id") or "") for case in cases]
    assert all(case_ids), "每个场景必须有非空 case_id"
    assert len(case_ids) == len(set(case_ids)), "case_id 必须唯一"
    assert REQUIRED_CASE_IDS <= set(case_ids)
    return {str(case["case_id"]): case for case in cases}


def _observations(case: dict[str, Any]) -> dict[str, Any]:
    observations = case.get("observations")
    assert isinstance(observations, dict), f"{case.get('case_id')} 缺少 observations"
    return observations


def _parse_iso(value: Any) -> datetime:
    assert isinstance(value, str) and value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical_report(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                str(key): normalize(nested)
                for key, nested in sorted(item.items(), key=lambda pair: str(pair[0]))
                if str(key) not in VOLATILE_REPORT_KEYS
            }
        if isinstance(item, list):
            return [normalize(nested) for nested in item]
        return item

    return json.dumps(
        normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@pytest.mark.asyncio
async def test_accelerated_simulation_returns_json_report_for_seven_virtual_days_without_sleep(
    monkeypatch,
):
    async def forbidden_sleep(*_args, **_kwargs):
        raise AssertionError("加速模拟不得等待真实时间")

    monkeypatch.setattr(asyncio, "sleep", forbidden_sleep)

    report = await _run_simulation()

    assert {
        "passed",
        "cases",
        "metrics",
        "storage_backend",
        "virtual_start",
        "virtual_end",
    } <= set(report)
    assert report["passed"] is True
    assert report["mode"] == "seven_day_conformance_replay"
    assert report["report_schema_version"] == 2
    assert report["storage_backend"] == "sqlite:///:memory:"
    assert report["policy"]["surge_min_prob"] == pytest.approx(0.1)
    assert report["policy"]["surge_max_prob"] == pytest.approx(0.6)
    assert report["publisher_state"]["external_event_count"] == 0
    json.dumps(report, ensure_ascii=False, allow_nan=False)
    assert _parse_iso(report["virtual_end"]) - _parse_iso(report["virtual_start"]) == timedelta(days=7)
    cases = _case_map(report)
    assert all(cases[case_id].get("passed") is True for case_id in REQUIRED_CASE_IDS)


@pytest.mark.asyncio
async def test_judge_contract_and_empty_generator_cases_fail_closed_and_report_truncation():
    report = await _run_simulation()
    cases = _case_map(report)

    complete = _observations(cases["judge_complete"])
    assert complete["contract_ok"] is True
    assert complete["truncated"] is False

    length = _observations(cases["judge_finish_reason_length"])
    assert length["finish_reason"] == "length"
    assert length["contract_ok"] is False
    assert length["truncated"] is True
    assert length["recorded_publish_count"] == 0

    partial = _observations(cases["judge_partial_json"])
    assert partial["contract_ok"] is False
    assert partial["truncated"] is False
    assert partial["error_type"] == "contract_error"
    assert partial["recorded_publish_count"] == 0

    schema = _observations(cases["judge_schema_error"])
    assert schema["contract_ok"] is False
    assert schema["truncated"] is False
    assert schema["recorded_publish_count"] == 0

    empty_generator = _observations(cases["empty_generator"])
    assert empty_generator["generated_chars"] == 0
    assert empty_generator["recorded_publish_count"] == 0

    assert report["metrics"]["contract_success_rate"] == pytest.approx(1 / 4)
    assert report["metrics"]["truncation_rate"] == pytest.approx(1 / 4)


@pytest.mark.asyncio
async def test_research_cases_report_zero_one_two_sources_and_timeout_without_publishing():
    report = await _run_simulation()
    cases = _case_map(report)

    for case_id, expected_count in (
        ("research_zero_sources", 0),
        ("research_one_source", 1),
        ("research_two_sources", 2),
    ):
        observations = _observations(cases[case_id])
        sources = observations["sources"]
        assert isinstance(sources, list)
        assert observations["source_count"] == expected_count == len(sources)
        assert len({str(item["url"]) for item in sources}) == expected_count
        assert all(str(item["url"]).startswith(("http://", "https://")) for item in sources)

    timeout = _observations(cases["research_timeout"])
    assert timeout["timed_out"] is True
    assert timeout["recorded_publish_count"] == 0
    assert timeout["external_push_count"] == 0

    assert report["metrics"]["source_coverage"] == pytest.approx(0.5)
    assert report["metrics"]["injected_timeout_case_rate"] == pytest.approx(1 / 2)
    assert report["metrics"]["timeout_rate"] == pytest.approx(1 / 2)


@pytest.mark.asyncio
async def test_scheduling_idempotency_and_auto_dry_run_modes_only_use_recording_sink():
    report = await _run_simulation()
    cases = _case_map(report)

    quiet = _observations(cases["quiet_hours"])
    assert cases["quiet_hours"]["status"] == "skipped_quiet_hours"
    assert quiet["judge_calls"] == 0
    assert quiet["research_calls"] == 0
    assert quiet["generator_calls"] == 0
    assert quiet["recorded_publish_count"] == 0

    max_silence = _observations(cases["max_silence"])
    assert max_silence["forced"] is True
    assert max_silence["judge_calls"] == 0
    assert max_silence["recorded_publish_count"] == 1

    duplicate = _observations(cases["duplicate_key"])
    assert duplicate["attempt_count"] == 2
    assert duplicate["unique_key_count"] == 1
    assert duplicate["recorded_publish_count"] == 1
    assert duplicate["duplicate_rate"] == pytest.approx(0.5)

    auto = _observations(cases["auto_publish"])
    assert auto["mode"] == "auto"
    assert auto["would_publish"] is True
    assert auto["recorded_publish_count"] == 1
    assert auto["external_push_count"] == 0

    dry_run = _observations(cases["dry_run"])
    assert dry_run["mode"] == "dry_run"
    assert dry_run["would_publish"] is True
    assert dry_run["recorded_publish_count"] == 0
    assert dry_run["external_push_count"] == 0

    metrics = report["metrics"]
    assert metrics["external_push_count"] == 0
    assert metrics["duplicate_rate"] == pytest.approx(1 / 4)
    assert metrics["recorded_publish_count"] == sum(
        int(_observations(case).get("recorded_publish_count", 0))
        for case in cases.values()
    )


@pytest.mark.asyncio
async def test_simulation_metrics_are_complete_finite_and_bounded():
    report = await _run_simulation()
    metrics = report.get("metrics")

    assert isinstance(metrics, dict)
    assert REQUIRED_METRICS <= set(metrics)
    assert isinstance(metrics["external_push_count"], int)
    assert isinstance(metrics["recorded_publish_count"], int)
    assert metrics["external_push_count"] == 0
    assert metrics["recorded_publish_count"] >= 1
    for name in (
        "contract_success_rate",
        "truncation_rate",
        "source_coverage",
        "duplicate_rate",
        "timeout_rate",
    ):
        assert isinstance(metrics[name], (int, float)), name
        assert 0.0 <= float(metrics[name]) <= 1.0, name


@pytest.mark.asyncio
async def test_two_accelerated_runs_have_identical_canonical_reports():
    first = await _run_simulation()
    second = await _run_simulation()

    assert _canonical_report(first) == _canonical_report(second)


@pytest.mark.asyncio
async def test_simulation_gate_uses_corrected_release_metrics_and_real_virtual_events():
    module = _load_simulation_module()
    report = await _run_simulation()

    assert report["gate"]["passed"] is True
    assert report["metrics"]["unexpected_truncation_rate"] == 0
    assert report["metrics"]["duplicate_publish_rate"] == 0
    assert report["metrics"]["successful_research_source_coverage"] == 1
    assert report["gate"]["checks"]["budget_guard_exercised"] is True
    assert report["gate"]["checks"]["timeout_exercised"] is True
    assert report["gate"]["checks"]["ledger_matches_publisher"] is True
    assert "max_silence" in report["virtual_events"]
    assert "simulation_end" in report["virtual_events"]

    bad_metrics = dict(report["metrics"])
    bad_metrics["external_push_count"] = 1
    gate = module.evaluate_simulation_gate(
        report["cases"],
        bad_metrics,
        ledger_state=report["sqlite_state"],
        publisher_state=report["publisher_state"],
    )
    assert gate["passed"] is False
    assert gate["checks"]["reported_metrics_match_evidence"] is False

    mismatched_cases = json.loads(json.dumps(report["cases"], ensure_ascii=False))
    timeout_case = next(
        case for case in mismatched_cases if case["case_id"] == "research_timeout"
    )
    timeout_case["status"] = "candidate"
    timeout_case["passed"] = True
    mismatch_gate = module.evaluate_simulation_gate(
        mismatched_cases,
        report["metrics"],
        ledger_state=report["sqlite_state"],
        publisher_state=report["publisher_state"],
    )
    assert mismatch_gate["passed"] is False
    assert mismatch_gate["checks"]["all_cases_passed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
async def test_simulation_gate_rejects_missing_or_duplicate_required_cases(mutation):
    module = _load_simulation_module()
    report = await _run_simulation()
    cases = deepcopy(report["cases"])

    if mutation == "missing":
        cases = [case for case in cases if case["case_id"] != "quiet_hours"]
    else:
        duplicate = next(case for case in cases if case["case_id"] == "quiet_hours")
        cases.append(deepcopy(duplicate))

    gate = module.evaluate_simulation_gate(
        cases,
        report["metrics"],
        ledger_state=report["sqlite_state"],
        publisher_state=report["publisher_state"],
    )

    assert gate["passed"] is False
    assert gate["checks"]["required_cases_complete_and_unique"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_id", "field", "forged_value"),
    [
        ("quiet_hours", "status", "candidate"),
        ("max_silence", "forced", False),
    ],
)
async def test_simulation_gate_rejects_tampered_case_status_or_observations(
    case_id,
    field,
    forged_value,
):
    module = _load_simulation_module()
    report = await _run_simulation()
    cases = deepcopy(report["cases"])
    case = next(item for item in cases if item["case_id"] == case_id)
    case["passed"] = True
    if field == "status":
        case["status"] = forged_value
        case.setdefault("expected", {})["status"] = forged_value
    else:
        case["observations"][field] = forged_value
        case.setdefault("expected", {}).setdefault("observations", {})[
            field
        ] = forged_value

    gate = module.evaluate_simulation_gate(
        cases,
        report["metrics"],
        ledger_state=report["sqlite_state"],
        publisher_state=report["publisher_state"],
    )

    assert gate["passed"] is False
    assert gate["checks"]["all_cases_passed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", ["research_zero_sources", "research_one_source"])
async def test_simulation_gate_requires_insufficient_sources_reason_for_low_source_cases(
    case_id,
):
    module = _load_simulation_module()
    report = await _run_simulation()
    cases = deepcopy(report["cases"])
    case = next(item for item in cases if item["case_id"] == case_id)
    case["passed"] = True
    case["observations"]["reason_code"] = ""
    case.setdefault("expected", {})["reason_code"] = ""

    gate = module.evaluate_simulation_gate(
        cases,
        report["metrics"],
        ledger_state=report["sqlite_state"],
        publisher_state=report["publisher_state"],
    )

    assert gate["passed"] is False
    assert gate["checks"]["all_cases_passed"] is False


@pytest.mark.asyncio
async def test_simulation_gate_ignores_forged_safe_metrics_and_uses_publisher_records():
    module = _load_simulation_module()
    report = await _run_simulation()
    forged_safe_metrics = deepcopy(report["metrics"])
    publisher_state = deepcopy(report["publisher_state"])
    publisher_state["records"].append({
        "case_id": "forged-external-push",
        "key": "forged-external-push",
        "message": "不应外发",
        "virtual_at": report["virtual_end"],
        "sink": "external",
        "outcome": "success",
    })

    gate = module.evaluate_simulation_gate(
        report["cases"],
        forged_safe_metrics,
        ledger_state=report["sqlite_state"],
        publisher_state=publisher_state,
    )

    assert gate["passed"] is False
    assert gate["checks"]["no_external_publish"] is False
    assert gate["checks"]["publisher_evidence_consistent"] is False


@pytest.mark.asyncio
async def test_simulation_gate_rejects_publisher_message_not_matching_ledger():
    module = _load_simulation_module()
    report = await _run_simulation()
    publisher_state = deepcopy(report["publisher_state"])
    publisher_state["records"][0]["message"] = "与 SQLite 候选不一致的伪造正文"

    gate = module.evaluate_simulation_gate(
        report["cases"],
        report["metrics"],
        ledger_state=report["sqlite_state"],
        publisher_state=publisher_state,
    )

    assert gate["passed"] is False
    assert gate["checks"]["publisher_evidence_consistent"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["invalid", "mismatched"])
async def test_simulation_gate_rejects_invalid_or_mismatched_publisher_time(mutation):
    module = _load_simulation_module()
    report = await _run_simulation()
    publisher_state = deepcopy(report["publisher_state"])
    record = publisher_state["records"][0]
    if mutation == "invalid":
        record["virtual_at"] = "not-an-iso-time"
    else:
        original = datetime.fromisoformat(record["virtual_at"])
        record["virtual_at"] = (original + timedelta(seconds=1)).isoformat()

    gate = module.evaluate_simulation_gate(
        report["cases"],
        report["metrics"],
        ledger_state=report["sqlite_state"],
        publisher_state=publisher_state,
    )

    assert gate["passed"] is False
    assert gate["checks"]["publisher_evidence_consistent"] is False


@pytest.mark.asyncio
async def test_simulation_gate_rejects_candidate_timezone_awareness_mismatch():
    module = _load_simulation_module()
    report = await _run_simulation()
    ledger_state = deepcopy(report["sqlite_state"])
    candidate = next(
        record
        for record in ledger_state["candidate_records"]
        if record["published_at"]
    )
    candidate["published_at"] = f'{candidate["published_at"]}+00:00'

    gate = module.evaluate_simulation_gate(
        report["cases"],
        report["metrics"],
        ledger_state=ledger_state,
        publisher_state=report["publisher_state"],
    )

    assert gate["passed"] is False
    assert gate["checks"]["ledger_evidence_consistent"] is False


@pytest.mark.asyncio
async def test_simulation_gate_rejects_attempt_timezone_awareness_mismatch():
    module = _load_simulation_module()
    report = await _run_simulation()
    ledger_state = deepcopy(report["sqlite_state"])
    attempt = ledger_state["attempt_records"][0]
    attempt["attempted_at"] = f'{attempt["attempted_at"]}+00:00'

    gate = module.evaluate_simulation_gate(
        report["cases"],
        report["metrics"],
        ledger_state=ledger_state,
        publisher_state=report["publisher_state"],
    )

    assert gate["passed"] is False
    assert gate["checks"]["ledger_evidence_consistent"] is False


@pytest.mark.asyncio
async def test_simulation_gate_rejects_all_timezone_aware_evidence():
    module = _load_simulation_module()
    report = await _run_simulation()
    ledger_state = deepcopy(report["sqlite_state"])
    publisher_state = deepcopy(report["publisher_state"])

    for candidate in ledger_state["candidate_records"]:
        candidate["created_at"] = f'{candidate["created_at"]}+00:00'
        if candidate["published_at"]:
            candidate["published_at"] = f'{candidate["published_at"]}+00:00'
    for attempt in ledger_state["attempt_records"]:
        attempt["attempted_at"] = f'{attempt["attempted_at"]}+00:00'
    for record in publisher_state["records"]:
        record["virtual_at"] = f'{record["virtual_at"]}+00:00'

    gate = module.evaluate_simulation_gate(
        report["cases"],
        report["metrics"],
        ledger_state=ledger_state,
        publisher_state=publisher_state,
    )

    assert gate["passed"] is False
    assert gate["checks"]["ledger_evidence_consistent"] is False
    assert gate["checks"]["publisher_evidence_consistent"] is False


@pytest.mark.asyncio
async def test_simulation_gate_rejects_tampered_candidate_raw_record():
    module = _load_simulation_module()
    report = await _run_simulation()
    ledger_state = deepcopy(report["sqlite_state"])
    assert isinstance(ledger_state.get("candidate_records"), list)
    assert ledger_state["candidate_records"]
    ledger_state["candidate_records"][0]["message"] = "被篡改的候选正文"

    gate = module.evaluate_simulation_gate(
        report["cases"],
        report["metrics"],
        ledger_state=ledger_state,
        publisher_state=report["publisher_state"],
    )

    assert gate["passed"] is False
    assert gate["checks"]["publisher_evidence_consistent"] is False


@pytest.mark.asyncio
async def test_simulation_gate_rejects_attempt_raw_record_not_matching_summary():
    module = _load_simulation_module()
    report = await _run_simulation()
    ledger_state = deepcopy(report["sqlite_state"])
    assert isinstance(ledger_state.get("attempt_records"), list)
    accepted = next(
        record
        for record in ledger_state["attempt_records"]
        if record["accepted"] == 1
    )
    accepted["accepted"] = 0

    gate = module.evaluate_simulation_gate(
        report["cases"],
        report["metrics"],
        ledger_state=ledger_state,
        publisher_state=report["publisher_state"],
    )

    assert gate["passed"] is False
    assert gate["checks"]["ledger_evidence_consistent"] is False


@pytest.mark.asyncio
async def test_simulation_gate_rejects_forged_ledger_summary_and_uses_raw_records():
    module = _load_simulation_module()
    report = await _run_simulation()
    forged_safe_metrics = deepcopy(report["metrics"])
    ledger_state = deepcopy(report["sqlite_state"])
    ledger_state["duplicate_publish_count"] = 1
    ledger_state["duplicate_publishes"] = 1

    gate = module.evaluate_simulation_gate(
        report["cases"],
        forged_safe_metrics,
        ledger_state=ledger_state,
        publisher_state=report["publisher_state"],
    )

    assert gate["passed"] is False
    assert gate["checks"]["ledger_evidence_consistent"] is False
    assert gate["checks"]["no_duplicate_publish"] is True


@pytest.mark.asyncio
async def test_simulation_exercises_shared_candidate_kernel_and_sqlite_state(monkeypatch):
    module = _load_simulation_module()
    original = module.evaluate_outreach_candidate
    calls: list[str] = []

    async def recording_evaluator(**kwargs):
        calls.append(str(kwargs["request_id"]))
        return await original(**kwargs)

    monkeypatch.setattr(module, "evaluate_outreach_candidate", recording_evaluator)
    report = await module.run_accelerated_simulation()
    cases = _case_map(report)

    assert len(calls) >= 8
    assert cases["min_interval"]["status"] == "skipped_min_interval"
    assert cases["surge_not_due"]["status"] == "skipped_not_due"
    assert cases["surge_due"]["status"] == "candidate"
    assert cases["research_budget_exhausted"]["status"] == "research_blocked"
    assert report["metrics"]["budget_overrun_count"] == 0
    assert report["sqlite_state"]["duplicate_attempts"] == 1
    assert report["sqlite_state"]["delivery_attempts"] == 4
    assert report["sqlite_state"]["duplicate_publishes"] == 0


def test_simulation_schedule_gate_uses_canonical_policy_order_and_defaults():
    module = _load_simulation_module()
    now = datetime(2026, 7, 10, 12, 0, 0)

    decision = module._schedule_gate(
        now=now,
        last_effective_at=now - timedelta(minutes=10),
        last_interaction_at=now - timedelta(days=2),
        next_check_at=now + timedelta(hours=4),
        surge_roll=0.99,
    )

    assert decision["status"] == "skipped_not_due"
    assert decision["surge_probability"] == pytest.approx(0.6)
    assert decision["surge_roll"] == pytest.approx(0.99)


@pytest.mark.asyncio
async def test_simulation_budget_case_calls_real_plugin_limit_plus_one(monkeypatch):
    from core.proactive_research import ResearchBudgetPlugin

    module = _load_simulation_module()
    original = ResearchBudgetPlugin.pre_tool_execute
    tool_calls: list[str] = []

    async def recording_hook(self, args, **kwargs):
        tool_calls.append(str(kwargs.get("tool_name") or ""))
        return await original(self, args, **kwargs)

    monkeypatch.setattr(ResearchBudgetPlugin, "pre_tool_execute", recording_hook)
    report = await module.run_accelerated_simulation()
    observations = _observations(_case_map(report)["research_budget_exhausted"])

    assert observations["attempted_calls"] == observations["limit"] + 1
    assert observations["allowed_calls"] == observations["limit"]
    assert observations["blocked_calls"] == 1
    assert observations["exploration_calls"] == observations["limit"]
    assert tool_calls == ["web_search"] * observations["attempted_calls"]


@pytest.mark.asyncio
async def test_simulation_research_cases_use_production_runner_and_source_extractor(
    monkeypatch,
):
    import core.proactive_research as research_module

    module = _load_simulation_module()
    original_runner = module.run_proactive_research
    original_extractor = research_module.extract_verified_web_sources
    runner_calls = 0
    extractor_calls = 0

    async def recording_runner(*args, **kwargs):
        nonlocal runner_calls
        runner_calls += 1
        return await original_runner(*args, **kwargs)

    def recording_extractor(*args, **kwargs):
        nonlocal extractor_calls
        extractor_calls += 1
        return original_extractor(*args, **kwargs)

    monkeypatch.setattr(module, "run_proactive_research", recording_runner)
    monkeypatch.setattr(
        research_module,
        "extract_verified_web_sources",
        recording_extractor,
    )

    report = await module.run_accelerated_simulation()
    cases = _case_map(report)

    assert runner_calls == 5
    assert extractor_calls == 3
    assert all(
        _observations(cases[case_id])["production_runner"] is True
        for case_id in (
            "research_zero_sources",
            "research_one_source",
            "research_two_sources",
            "research_timeout",
            "research_budget_exhausted",
        )
    )


@pytest.mark.asyncio
async def test_simulation_timeout_case_uses_real_wait_on_unset_event(monkeypatch):
    module = _load_simulation_module()
    original = asyncio.wait
    wait_calls: list[float | None] = []

    async def recording_wait(fs, *, timeout=None, return_when=asyncio.ALL_COMPLETED):
        wait_calls.append(timeout)
        return await original(fs, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", recording_wait)
    report = await module.run_accelerated_simulation()
    observations = _observations(_case_map(report)["research_timeout"])

    assert wait_calls
    assert any(timeout is not None and timeout <= 0.01 for timeout in wait_calls)
    assert observations["timeout_caught"] is True
    assert observations["recorded_publish_count"] == 0
    assert not any(
        record.get("case_id") == "research_timeout"
        for record in report["publisher_state"]["records"]
    )


@pytest.mark.asyncio
async def test_simulation_ledger_derives_duplicate_attempts_for_arbitrary_keys():
    module = _load_simulation_module()
    ledger = module.SimulationLedger()
    publisher = module.RecordingPublisher()
    now = datetime(2026, 7, 10, 12, 0, 0)

    try:
        ledger.stage_candidate(key="alpha", message="A", now=now)
        assert await ledger.deliver(
            key="alpha",
            now=now,
            publisher=publisher,
        ) is True
        assert await ledger.deliver(
            key="alpha",
            now=now,
            publisher=publisher,
        ) is False
        ledger.stage_candidate(key="beta", message="B", now=now)
        assert await ledger.deliver(
            key="beta",
            now=now,
            publisher=publisher,
        ) is True

        state = ledger.snapshot()
        assert state["delivery_attempts"] == 3
        assert state["distinct_attempt_keys"] == 2
        assert state["duplicate_attempt_count"] == 1
        assert state["accepted_attempts"] == 2
        assert state["rejected_attempts"] == 1
    finally:
        ledger.close()


def test_derive_simulation_metrics_uses_actual_case_ledger_and_publisher_evidence():
    module = _load_simulation_module()
    cases = [
        {
            "case_id": "judge_finish_reason_length",
            "status": "judge_error",
            "passed": True,
            "expected": {"error_type": "model_truncated"},
            "observations": {
                "contract_ok": False,
                "error_type": "model_truncated",
            },
        },
        {
            "case_id": "judge_partial_json",
            "status": "judge_error",
            "passed": True,
            "expected": {"error_type": "contract_error"},
            "observations": {
                "contract_ok": False,
                "error_type": "contract_error",
            },
        },
        {
            "case_id": "research_timeout",
            "status": "research_blocked",
            "passed": True,
            "observations": {"timeout_caught": True},
        },
        {
            "case_id": "research_budget_exhausted",
            "status": "research_blocked",
            "passed": True,
            "observations": {
                "limit": 2,
                "exploration_calls": 2,
                "blocked_calls": 1,
            },
        },
    ]
    ledger_state = {
        "sent_rows": 2,
        "delivery_attempts": 3,
        "accepted_attempts": 2,
        "rejected_attempts": 1,
        "distinct_attempt_keys": 2,
        "duplicate_attempt_count": 1,
        "duplicate_publish_count": 0,
    }
    publisher_state = {
        "recorded_publish_count": 2,
        "successful_publish_count": 2,
        "unique_published_keys": 2,
        "duplicate_record_count": 0,
        "external_event_count": 1,
        "records": [],
    }

    metrics = module.derive_simulation_metrics(
        cases,
        ledger_state,
        publisher_state,
    )

    assert metrics["external_push_count"] == 1
    assert metrics["truncation_rate"] == pytest.approx(1 / 2)
    assert metrics["unexpected_truncation_rate"] == 0
    assert metrics["duplicate_attempt_rate"] == pytest.approx(1 / 3)
    assert metrics["duplicate_publish_rate"] == 0
    assert metrics["budget_block_count"] == 1
    assert metrics["budget_overrun_count"] == 0


def _direct_import_names(module: ModuleType, source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative = f"{'.' * node.level}{node.module or ''}"
                try:
                    names.add(importlib.util.resolve_name(relative, module.__package__ or "core"))
                except (ImportError, ValueError):
                    names.add(relative)
            elif node.module:
                names.add(node.module)
    return names


def _module_source(module_name: str) -> str:
    spec = importlib.util.find_spec(module_name)
    if spec is None or not spec.origin or not spec.origin.endswith(".py"):
        return ""
    return Path(spec.origin).read_text(encoding="utf-8")


def test_simulation_module_source_and_direct_import_graph_exclude_production_push():
    module = _load_simulation_module()
    source = inspect.getsource(module)
    imported_names = _direct_import_names(module, source)
    direct_core_sources = [
        _module_source(module_name)
        for module_name in imported_names
        if module_name.startswith("core.")
    ]
    inspected_source = "\n".join([source, *direct_core_sources])
    forbidden_symbol = "push" + "_to_qq"
    forbidden_module = "daily_" + "digest"

    assert forbidden_symbol not in inspected_source
    assert forbidden_module not in inspected_source
    assert all(forbidden_module not in module_name for module_name in imported_names)
