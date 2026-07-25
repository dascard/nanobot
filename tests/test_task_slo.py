"""语义 Task SLO Descriptor、引用对账与激活门禁测试。"""

from __future__ import annotations

import pytest


def test_task_slo_registry_covers_stage_five_to_seven_tasks():
    from core.task_runtime.slo import (
        TASK_SLO_REGISTRY,
        TaskSloStatus,
    )

    descriptors = {
        descriptor.task_id: descriptor
        for descriptor in TASK_SLO_REGISTRY.descriptors()
    }

    assert tuple(descriptors) == (
        "group_analysis_quality",
        "group_analysis_quotes",
        "group_analysis_titles",
        "group_analysis_topics",
        "group_memory_learning",
        "news_daily_quality",
        "news_relevance_review",
        "private_decision",
        "timing_gate",
    )
    assert descriptors["timing_gate"].status is TaskSloStatus.FROZEN
    assert descriptors["private_decision"].status is TaskSloStatus.FROZEN
    assert all(
        descriptors[task_id].status is TaskSloStatus.BASELINE_ONLY
        for task_id in (
            "news_daily_quality",
            "group_analysis_topics",
            "group_analysis_titles",
            "group_analysis_quotes",
            "group_analysis_quality",
            "group_memory_learning",
        )
    )
    assert all(
        descriptor.baseline_artifact
        == "docs/architecture/semantic-task-performance-baseline.json"
        for descriptor in descriptors.values()
    )
    assert all(
        descriptor.max_task_runs_per_request == 1
        for descriptor in descriptors.values()
    )


def test_frozen_task_slo_has_latency_cost_resource_and_failure_budgets():
    from core.task_runtime.slo import (
        TaskBillingClass,
        require_task_slo_descriptor,
    )

    descriptor = require_task_slo_descriptor("private_decision")

    assert descriptor.billing_class is TaskBillingClass.LOCAL_FREE
    assert (
        descriptor.p50_latency_ms
        <= descriptor.p95_latency_ms
        <= descriptor.p99_latency_ms
    )
    assert descriptor.max_provider_attempts_per_run == 1
    assert descriptor.max_input_chars > 0
    assert descriptor.max_input_tokens > 0
    assert descriptor.max_output_tokens == 120
    assert descriptor.daily_call_limit > 0
    assert descriptor.max_concurrency > 0
    assert descriptor.daily_cost_limit is None
    assert descriptor.cost_per_1000_calls_limit is None
    assert descriptor.cpu_time_ms_limit is not None
    assert descriptor.gpu_time_ms_limit is not None
    assert 0 <= descriptor.max_total_failure_rate <= 1
    assert 0 <= descriptor.max_timeout_rate <= 1
    assert 0 <= descriptor.max_unavailable_rate <= 1
    assert 0 <= descriptor.max_contract_violation_rate <= 1
    assert 0 <= descriptor.max_fallback_rate <= 1
    assert descriptor.approved_by == "project_owner_plan"
    assert descriptor.approval_ref.endswith("#47-语义-task-slo-与成本预算")


def test_task_slo_reconciles_route_contract_and_resilience_references():
    from core.model_provider.route_registry import (
        require_model_route_descriptor,
    )
    from core.prompt_v2.task_contracts import get_task_contract
    from core.task_runtime import require_resilience_policy
    from core.task_runtime.slo import TASK_SLO_REGISTRY

    for descriptor in TASK_SLO_REGISTRY.descriptors():
        route = require_model_route_descriptor(descriptor.route_key)
        contract = get_task_contract(f"tasks/{descriptor.task_id}")
        assert contract is not None
        policy = require_resilience_policy(
            contract.output_failure_policy
        )

        assert route.runtime_task_key == f"tasks/{descriptor.task_id}"
        assert route.slo.task_slo_descriptor_id == descriptor.slo_id
        assert route.owner == descriptor.owner_module
        assert route.default_max_tokens <= descriptor.max_output_tokens
        assert (
            policy.max_attempts
            <= descriptor.max_provider_attempts_per_run
        )
        assert policy.slo_descriptor_id == "task_slo.by_invocation.v1"
        assert policy.terminal_action is descriptor.terminal_action


def test_baseline_only_task_slo_cannot_pass_activation_gate():
    from core.task_runtime.slo import (
        TaskSloActivationError,
        require_task_slo_activation,
    )

    with pytest.raises(
        TaskSloActivationError,
        match="只能观察",
    ):
        require_task_slo_activation("news_daily_quality")

    assert (
        require_task_slo_activation("private_decision").task_id
        == "private_decision"
    )


def test_frozen_provider_task_requires_explicit_cost_budgets():
    from core.task_runtime import TaskTerminalAction
    from core.task_runtime.slo import (
        TaskBillingClass,
        TaskSloDescriptor,
        TaskSloStatus,
    )

    with pytest.raises(ValueError, match="成本预算"):
        TaskSloDescriptor(
            slo_id="task_slo.test.v1",
            task_id="test",
            route_key="test",
            owner_module="tests",
            version="1.0.0",
            status=TaskSloStatus.FROZEN,
            baseline_artifact="baseline.json",
            baseline_task_id="test",
            observation_window_days=30,
            min_sample_count=50,
            p50_latency_ms=100,
            p95_latency_ms=200,
            p99_latency_ms=300,
            max_task_runs_per_request=1,
            max_provider_attempts_per_run=1,
            max_input_chars=1000,
            max_input_tokens=500,
            max_output_tokens=100,
            daily_call_limit=100,
            max_concurrency=2,
            billing_class=TaskBillingClass.PROVIDER_GATEWAY,
            max_total_failure_rate=0.1,
            max_timeout_rate=0.05,
            max_unavailable_rate=0.05,
            max_contract_violation_rate=0.05,
            max_fallback_rate=0.1,
            circuit_breaker_scope="route:test",
            terminal_action=TaskTerminalAction.BLOCK,
            approved_by="tester",
            approval_ref="tests/test_task_slo.py",
        )
