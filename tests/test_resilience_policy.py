"""统一 Typed Failure 与 ResiliencePolicy 的合同测试。"""

from __future__ import annotations

from types import SimpleNamespace
import urllib.error

import pytest


class _FailurePort:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.requests: list[object] = []

    @property
    def adapter_id(self) -> str:
        return "failure_port"

    def complete_task(self, request):
        self.requests.append(request)
        raise self.failure


def test_failure_category_is_complete_and_stable():
    from core.resilience import FailureCategory

    assert {item.value for item in FailureCategory} == {
        "validation",
        "authorization",
        "unavailable",
        "timeout",
        "rate_limited",
        "transient_transport",
        "contract_violation",
        "conflict",
        "quota",
        "cancelled",
        "permanent",
    }


def test_task_typed_failure_derives_category_and_sanitizes_safe_fields():
    from core.resilience import FailureCategory
    from core.task_runtime import (
        TaskFailureCode,
        TaskFailureStage,
        TaskTerminalAction,
        TaskTypedFailure,
    )

    failure = TaskTypedFailure(
        code=TaskFailureCode.AUTHORIZATION_FAILED,
        stage=TaskFailureStage.PROVIDER,
        retryable=False,
        summary="拒绝\n访问\t" + ("x" * 300),
        terminal_action=TaskTerminalAction.BLOCK,
        cause_type="HTTPError\nsecret",
        trace_ref="trace\t123",
    )

    assert failure.category is FailureCategory.AUTHORIZATION
    assert failure.code.value == "authorization_failed"
    assert "\n" not in failure.summary
    assert "\t" not in failure.summary
    assert len(failure.summary) == 240
    assert failure.cause_type == "HTTPError secret"
    assert failure.trace_ref == "trace 123"


def test_failure_code_to_category_mapping_covers_operational_classes():
    from core.resilience import FailureCategory
    from core.task_runtime import (
        TaskFailureCode,
        failure_category_for_code,
    )

    assert failure_category_for_code(
        TaskFailureCode.EXECUTION_TIMEOUT
    ) is FailureCategory.TIMEOUT
    assert failure_category_for_code(
        TaskFailureCode.RATE_LIMITED
    ) is FailureCategory.RATE_LIMITED
    assert failure_category_for_code(
        TaskFailureCode.TRANSIENT_TRANSPORT
    ) is FailureCategory.TRANSIENT_TRANSPORT
    assert failure_category_for_code(
        TaskFailureCode.SCHEMA_INVALID
    ) is FailureCategory.CONTRACT_VIOLATION
    assert failure_category_for_code(
        TaskFailureCode.QUOTA_EXCEEDED
    ) is FailureCategory.QUOTA
    assert failure_category_for_code(
        TaskFailureCode.CANCELLED
    ) is FailureCategory.CANCELLED
    assert failure_category_for_code(
        TaskFailureCode.PERMANENT_FAILURE
    ) is FailureCategory.PERMANENT


def test_resilience_policy_declares_complete_retry_budget():
    from core.resilience import FailureCategory
    from core.task_runtime import (
        ResiliencePolicyDescriptor,
        TaskFailureCode,
        TaskTerminalAction,
    )

    policy = ResiliencePolicyDescriptor(
        policy_id="test.retry.v1",
        version="1.0.0",
        owner_module="tests.resilience",
        max_attempts=3,
        total_timeout_seconds=30.0,
        per_attempt_timeout_seconds=10.0,
        backoff_initial_seconds=0.25,
        backoff_multiplier=2.0,
        backoff_max_seconds=1.0,
        jitter_ratio=0.2,
        retryable_failure_categories=frozenset({
            FailureCategory.TIMEOUT,
        }),
        retryable_failure_codes=frozenset({
            TaskFailureCode.RATE_LIMITED,
        }),
        fallback_route="reply",
        circuit_breaker_policy_id="model_failure_tracker.default",
        terminal_action=TaskTerminalAction.NORMAL_AGENT,
        slo_descriptor_id="task_slo.test.v1",
    )

    assert policy.allows_retry(
        TaskFailureCode.EXECUTION_TIMEOUT,
        failure_retryable=True,
    )
    assert policy.allows_retry(
        TaskFailureCode.RATE_LIMITED,
        failure_retryable=True,
    )
    assert not policy.allows_retry(
        TaskFailureCode.PERMANENT_FAILURE,
        failure_retryable=True,
    )
    assert not policy.allows_retry(
        TaskFailureCode.EXECUTION_TIMEOUT,
        failure_retryable=False,
    )
    assert policy.backoff_seconds(1, jitter_sample=0.5) == 0.25
    assert policy.backoff_seconds(2, jitter_sample=0.5) == 0.5
    assert policy.backoff_seconds(3, jitter_sample=0.5) == 1.0


def test_resilience_policy_rejects_attempt_timeout_over_total_budget():
    from core.resilience import FailureCategory
    from core.task_runtime import (
        ResiliencePolicyDescriptor,
        TaskTerminalAction,
    )

    with pytest.raises(ValueError, match="单次 timeout"):
        ResiliencePolicyDescriptor(
            policy_id="test.invalid.v1",
            version="1.0.0",
            owner_module="tests.resilience",
            max_attempts=2,
            total_timeout_seconds=5.0,
            per_attempt_timeout_seconds=6.0,
            backoff_initial_seconds=0.0,
            backoff_multiplier=1.0,
            backoff_max_seconds=0.0,
            jitter_ratio=0.0,
            retryable_failure_categories=frozenset({
                FailureCategory.TIMEOUT,
            }),
            retryable_failure_codes=frozenset(),
            fallback_route=None,
            circuit_breaker_policy_id="model_failure_tracker.default",
            terminal_action=TaskTerminalAction.BLOCK,
            slo_descriptor_id="task_slo.test.v1",
        )


def test_retry_decision_never_reads_exception_message():
    from core.task_runtime import (
        TaskInvocation,
        TaskModelExecutionError,
        TaskRuntime,
    )

    port = _FailurePort(TaskModelExecutionError(
        code="permanent_failure",
        summary="timeout provider unavailable，请重试",
        retryable=True,
    ))
    runtime = TaskRuntime(
        port,
        run_id_factory=lambda: "taskrun_typed_retry_only",
    )

    result = runtime.execute(TaskInvocation(
        invocation_id="group_analysis_topics",
        route_key="group_analysis_topics",
        input_values={"message": "群分析输入"},
        timeout_budget_seconds=30.0,
    ))

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code.value == "permanent_failure"
    assert result.failure.category.value == "permanent"
    assert result.attempt_count == 1
    assert len(port.requests) == 1


def test_semantic_task_terminal_actions_are_frozen():
    from core.prompt_v2.task_contracts import get_task_contract
    from core.task_runtime import require_resilience_policy

    private_policy = require_resilience_policy(
        get_task_contract(
            "tasks/private_decision"
        ).output_failure_policy
    )
    news_quality_policy = require_resilience_policy(
        get_task_contract(
            "tasks/news_daily_quality"
        ).output_failure_policy
    )
    group_learning_policy = require_resilience_policy(
        get_task_contract(
            "tasks/group_memory_learning"
        ).output_failure_policy
    )
    relevance_policy = require_resilience_policy(
        "single_attempt_conservative_downrank"
    )

    assert private_policy.max_attempts == 1
    assert private_policy.terminal_action.value == "normal_agent"
    assert news_quality_policy.terminal_action.value == (
        "deterministic_fallback"
    )
    assert group_learning_policy.max_attempts == 1
    assert group_learning_policy.terminal_action.value == (
        "preserve_pending"
    )
    assert relevance_policy.max_attempts == 1
    assert relevance_policy.terminal_action.value == (
        "conservative_downrank"
    )


def test_all_resilience_policies_reference_slo_and_circuit_breaker():
    from core.task_runtime import list_resilience_policies

    policies = list_resilience_policies()

    assert policies
    assert all(policy.version == "1.0.0" for policy in policies)
    assert all(policy.owner_module for policy in policies)
    assert all(policy.slo_descriptor_id for policy in policies)
    assert all(policy.circuit_breaker_policy_id for policy in policies)
    assert all(
        policy.per_attempt_timeout_seconds
        <= policy.total_timeout_seconds
        for policy in policies
    )


@pytest.mark.parametrize(
    ("status_code", "expected_code", "expected_category", "retryable"),
    [
        (401, "authorization_failed", "authorization", False),
        (429, "rate_limited", "rate_limited", True),
        (503, "transient_transport", "transient_transport", True),
        (400, "permanent_failure", "permanent", False),
    ],
)
def test_route_task_adapter_classifies_http_status_without_body_matching(
    monkeypatch,
    status_code,
    expected_code,
    expected_category,
    retryable,
):
    import clients.task_runtime_adapter as adapter_module
    from core.model_provider.route_registry import ModelRouteExecutionMode
    from core.task_runtime import (
        TaskModelExecutionError,
        TaskModelRequest,
    )

    monkeypatch.setattr(
        adapter_module.classifier_client,
        "resolve_model_route",
        lambda _route_key: {
            "provider_id": "provider",
            "model": "model",
        },
    )
    monkeypatch.setattr(
        adapter_module,
        "require_model_route_descriptor",
        lambda _route_key: SimpleNamespace(
            execution_mode=ModelRouteExecutionMode.ROUTE_COMPLETION,
        ),
    )

    def fail_route(**_kwargs):
        raise urllib.error.HTTPError(
            "https://provider.invalid/v1",
            status_code,
            "response body contains timeout and retry",
            None,
            None,
        )

    monkeypatch.setattr(
        adapter_module,
        "call_model_route_response",
        fail_route,
    )

    with pytest.raises(TaskModelExecutionError) as captured:
        adapter_module.RouteTaskModelAdapter().complete_task(
            TaskModelRequest(
                task_id="http-classification",
                contract_version="test-v1",
                route_key="private_decision",
                messages=({"role": "user", "content": "测试"},),
                run_id="taskrun_http_classification",
                attempt_no=1,
                timeout_seconds=5,
            )
        )

    assert captured.value.code.value == expected_code
    assert captured.value.category.value == expected_category
    assert captured.value.retryable is retryable
