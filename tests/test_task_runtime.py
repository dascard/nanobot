"""统一 TaskRuntime 的合同、失败分类和遥测测试。"""

from __future__ import annotations

from collections.abc import Mapping
import json
from types import SimpleNamespace

import pytest


class _FakeTaskModelPort:
    def __init__(self, *outcomes: object) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[object] = []

    @property
    def adapter_id(self) -> str:
        return "fake_task_model"

    def complete_task(self, request):
        self.requests.append(request)
        if not self._outcomes:
            raise AssertionError("测试未提供模型结果")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _completion(content: str):
    return _completion_for("private_decision", content)


def _private_payload(**overrides):
    value = {
        "action": "reply_now",
        "effort": "short",
        "intent": "general_question",
        "response_mode": "agent",
        "confidence": 0.92,
        "conflicting_signals": [],
        "material_state": "none",
        "reason_code": "clear_request",
    }
    value.update(overrides)
    return value


def _private_output(**overrides):
    return json.dumps(
        _private_payload(**overrides),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _completion_for(route_key: str, content: str):
    from core.task_runtime import TaskModelCompletion

    return TaskModelCompletion(
        content=content,
        route_key=route_key,
        provider="local_llama",
        model="qwen-test",
        usage={
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "prompt_cache_hit_tokens": 9,
            "prompt_cache_miss_tokens": 3,
        },
    )


def _invocation(**overrides):
    from core.task_runtime import TaskInvocation

    values = {
        "invocation_id": "private_decision",
        "route_key": "private_decision",
        "input_values": {
            "message": "帮我看看这段代码",
            "has_files": "false",
        },
        "request_context": {
            "template_confidence_threshold": 0.85,
        },
        "idempotency_key": "private:u1:turn1",
        "timeout_budget_seconds": 15.0,
    }
    values.update(overrides)
    return TaskInvocation(**values)


def test_private_decision_contract_declares_strict_schema_and_runtime_owner():
    from core.prompt_v2.task_contracts import (
        get_task_contract,
        get_task_invocation_spec,
    )

    contract = get_task_contract("tasks/private_decision")
    invocation = get_task_invocation_spec("private_decision")

    assert contract is not None
    assert contract.owner_module == "core.task_runtime"
    assert contract.output_failure_policy == "single_attempt_normal_agent"
    assert contract.output_schema["additionalProperties"] is False
    assert contract.output_schema["required"] == [
        "action",
        "effort",
        "intent",
        "response_mode",
        "confidence",
        "conflicting_signals",
        "material_state",
        "reason_code",
    ]
    assert contract.output_schema["properties"]["action"]["enum"] == [
        "no_reply",
        "wait",
        "reply_now",
    ]
    assert contract.output_schema["properties"]["response_mode"]["enum"] == [
        "template",
        "agent",
        "none",
    ]
    assert invocation is not None
    assert invocation.output_parser_owner == (
        "core.task_runtime.TaskRuntime"
    )


def test_group_memory_learning_contract_is_registered_before_writer_cutover():
    from core.model_provider.route_registry import (
        require_model_route_descriptor,
    )
    from core.prompt_v2.task_contracts import (
        get_task_contract,
        get_task_invocation_spec,
    )

    contract = get_task_contract("tasks/group_memory_learning")
    invocation = get_task_invocation_spec("group_memory_learning")
    route = require_model_route_descriptor("group_memory_learning")

    assert contract is not None
    assert contract.owner_module == "app.group_learning"
    assert contract.output_contract_id == "group_memory_learning_v1"
    assert contract.output_failure_policy == "single_attempt_preserve_pending"
    assert contract.output_schema["additionalProperties"] is False
    assert set(contract.output_schema["required"]) == {
        "reviews",
        "discoveries",
    }
    assert invocation is not None
    assert invocation.output_parser_owner == (
        "core.task_runtime.TaskRuntime"
    )
    assert route.task_contract_keys == ("tasks/group_memory_learning",)
    assert route.output_contract_id == contract.output_contract_id


def test_task_runtime_returns_parsed_value_and_safe_provenance():
    from core.runtime.events import (
        InMemoryRuntimeEventSink,
        RuntimeEventEmitter,
    )
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.task_runtime import TaskRuntime

    port = _FakeTaskModelPort(_completion(_private_output()))
    sink = InMemoryRuntimeEventSink()
    runtime = TaskRuntime(
        port,
        event_emitter=RuntimeEventEmitter(
            RUNTIME_EVENT_REGISTRY,
            (sink,),
        ),
        run_id_factory=lambda: "taskrun_test",
    )

    result = runtime.execute(_invocation())

    assert result.ok is True
    assert dict(result.parsed_value or {}) == {
        **_private_payload(),
        "conflicting_signals": (),
    }
    assert result.contract_version == "private_decision_v2"
    assert result.route_key == "private_decision"
    assert result.provider == "local_llama"
    assert result.model == "qwen-test"
    assert result.attempt_count == 1
    assert result.run_id == "taskrun_test"
    assert result.failure is None
    assert len(result.raw_output_sha256) == 64
    assert result.raw_output_bytes > 0

    assert len(port.requests) == 1
    request = port.requests[0]
    assert request.task_id == "private_decision"
    assert request.contract_version == "private_decision_v2"
    assert request.timeout_seconds > 0
    assert tuple(message["role"] for message in request.messages) == (
        "system",
        "user",
    )

    events = sink.events
    assert [event.phase for event in events] == ["started", "succeeded"]
    assert all(
        event.context.task_id == "private_decision"
        for event in events
    )
    assert all(
        event.context.task_run_id == "taskrun_test"
        for event in events
    )
    attributes = dict(events[-1].attributes)
    assert attributes["task_id"] == "private_decision"
    assert attributes["output_bytes"] == result.raw_output_bytes
    assert attributes["output_sha256"] == result.raw_output_sha256
    assert attributes["slo_id"] == "task_slo.private_decision.v1"
    assert attributes["slo_version"] == "1.0.0"
    assert attributes["slo_status"] == "frozen"
    assert attributes["input_chars"] > 0
    assert attributes["input_tokens"] > 0
    assert attributes["output_tokens"] == 8
    assert attributes["total_tokens"] == 20
    assert attributes["prompt_cache_hit_tokens"] == 9
    assert attributes["prompt_cache_miss_tokens"] == 3
    serialized = repr(events)
    assert "帮我看看这段代码" not in serialized


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_tokens": 121},
        {
            "input_values": {},
            "rendered_messages": (
                {"role": "user", "content": "a" * 16385},
            ),
        },
        {
            "input_values": {},
            "rendered_messages": (
                {"role": "user", "content": "中" * 8193},
            ),
        },
    ],
)
def test_task_runtime_rejects_slo_quota_before_provider_call(overrides):
    from core.task_runtime import TaskRuntime

    port = _FakeTaskModelPort(_completion(_private_output()))
    runtime = TaskRuntime(
        port,
        run_id_factory=lambda: "taskrun_slo_quota",
    )

    result = runtime.execute(_invocation(**overrides))

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code.value == "quota_exceeded"
    assert result.failure.terminal_action.value == "normal_agent"
    assert result.attempt_count == 0
    assert port.requests == []


def test_task_runtime_telemetry_ignores_invalid_usage_without_leaking_text():
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import (
        InMemoryRuntimeEventSink,
        RuntimeEventEmitter,
    )
    from core.task_runtime import TaskModelCompletion, TaskRuntime

    secret = "secret-token-value"
    completion = TaskModelCompletion(
        content=_private_output(),
        route_key="private_decision",
        provider="local_llama",
        model="qwen-test",
        usage={
            "prompt_tokens": secret,
            "completion_tokens": -1,
            "total_tokens": 3.5,
        },
    )
    sink = InMemoryRuntimeEventSink()
    runtime = TaskRuntime(
        _FakeTaskModelPort(completion),
        event_emitter=RuntimeEventEmitter(
            RUNTIME_EVENT_REGISTRY,
            (sink,),
        ),
        run_id_factory=lambda: "taskrun_invalid_usage",
    )

    result = runtime.execute(_invocation())

    assert result.ok is True
    attributes = dict(sink.events[-1].attributes)
    assert "output_tokens" not in attributes
    assert "total_tokens" not in attributes
    assert secret not in repr(sink.events)


def test_task_runtime_telemetry_failure_does_not_change_slo_result():
    from core.task_runtime import TaskRuntime

    class BrokenEmitter:
        def emit(self, *_args, **_kwargs):
            raise RuntimeError("telemetry unavailable")

    runtime = TaskRuntime(
        _FakeTaskModelPort(_completion(_private_output())),
        event_emitter=BrokenEmitter(),
        run_id_factory=lambda: "taskrun_broken_telemetry",
    )

    result = runtime.execute(_invocation())

    assert result.ok is True
    assert result.parsed_value["action"] == "reply_now"


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        ("不是 JSON", "invalid_json"),
        (
            _private_output(intent="unknown"),
            "schema_invalid",
        ),
        (
            _private_output(confidence=1.1),
            "field_out_of_range",
        ),
        (
            _private_output(
                action="wait",
                intent="wait_for_more",
                response_mode="agent",
                reason_code="user_will_continue",
            ),
            "business_validation_failed",
        ),
    ],
)
def test_task_runtime_distinguishes_output_failure_types(raw, expected_code):
    from core.task_runtime import TaskRuntime

    runtime = TaskRuntime(
        _FakeTaskModelPort(_completion(raw)),
        run_id_factory=lambda: "taskrun_failure",
    )

    result = runtime.execute(_invocation())

    assert result.ok is False
    assert result.parsed_value is None
    assert result.failure is not None
    assert result.failure.code.value == expected_code
    assert result.failure.terminal_action.value == "normal_agent"
    assert result.failure.retryable is False
    assert result.attempt_count == 1
    assert result.validation_diagnostics


def test_private_template_below_threshold_is_typed_business_failure():
    from core.task_runtime import TaskRuntime

    runtime = TaskRuntime(
        _FakeTaskModelPort(_completion(_private_output(
            effort="casual",
            intent="identity_probe",
            response_mode="template",
            confidence=0.8,
            reason_code="casual_exchange",
        ))),
        run_id_factory=lambda: "taskrun_template_threshold",
    )

    result = runtime.execute(_invocation())

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code.value == "business_validation_failed"
    assert result.failure.terminal_action.value == "normal_agent"
    assert result.validation_diagnostics[0].code == (
        "private_decision_template_conflict"
    )


@pytest.mark.parametrize(
    ("exception_factory", "expected_code"),
    [
        (
            lambda: __import__(
                "core.task_runtime",
                fromlist=["TaskModelExecutionError"],
            ).TaskModelExecutionError(
                code="provider_unavailable",
                summary="模型供应商暂不可用",
                retryable=True,
            ),
            "provider_unavailable",
        ),
        (lambda: TimeoutError("模型调用超时"), "execution_timeout"),
    ],
)
def test_task_runtime_returns_typed_provider_failures(
    exception_factory,
    expected_code,
):
    from core.task_runtime import TaskRuntime

    runtime = TaskRuntime(
        _FakeTaskModelPort(exception_factory()),
        run_id_factory=lambda: "taskrun_provider_failure",
    )

    result = runtime.execute(_invocation())

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code.value == expected_code
    assert result.failure.terminal_action.value == "normal_agent"
    assert result.attempt_count == 1


def test_task_runtime_does_not_swallow_programming_errors():
    from core.task_runtime import TaskRuntime

    runtime = TaskRuntime(
        _FakeTaskModelPort(TypeError("task adapter programming error")),
    )

    with pytest.raises(TypeError, match="programming error"):
        runtime.execute(_invocation())


def test_task_result_mappings_are_read_only():
    from core.task_runtime import TaskRuntime

    runtime = TaskRuntime(
        _FakeTaskModelPort(_completion(_private_output()))
    )
    result = runtime.execute(_invocation(
        request_context={"session_id": "private:u1"},
    ))

    assert isinstance(result.parsed_value, Mapping)
    with pytest.raises(TypeError):
        result.parsed_value["action"] = "wait"


def test_pre_rendered_messages_keep_contract_validation_and_hash_only_telemetry():
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.runtime.events import (
        InMemoryRuntimeEventSink,
        RuntimeEventEmitter,
    )
    from core.task_runtime import TaskRuntime

    secret_prompt = "只能用于模型输入的正文 secret-task-prompt"
    port = _FakeTaskModelPort(_completion(_private_output()))
    sink = InMemoryRuntimeEventSink()
    runtime = TaskRuntime(
        port,
        event_emitter=RuntimeEventEmitter(
            RUNTIME_EVENT_REGISTRY,
            (sink,),
        ),
        run_id_factory=lambda: "taskrun_pre_rendered",
    )

    result = runtime.execute(_invocation(
        input_values={},
        rendered_messages=(
            {"role": "system", "content": "只输出 JSON"},
            {"role": "user", "content": secret_prompt},
        ),
    ))

    assert result.ok is True
    assert tuple(port.requests[0].messages)[1]["content"] == secret_prompt
    event_text = repr(sink.events)
    assert secret_prompt not in event_text
    assert "input_sha256" in event_text

    mismatched = runtime.execute(_invocation(
        contract_version="private_decision_v0",
        input_values={},
        rendered_messages=(
            {"role": "user", "content": secret_prompt},
        ),
    ))
    assert mismatched.ok is False
    assert mismatched.failure is not None
    assert mismatched.failure.code.value == "contract_version_mismatch"
    assert len(port.requests) == 1


def test_news_task_rejects_source_ids_outside_current_cards():
    from core.task_runtime import TaskInvocation, TaskRuntime

    raw = json.dumps({
        "title": "日报",
        "subtitle": "今日摘要",
        "verdict": "一项值得关注的更新",
        "top_story": {
            "title": "越界来源",
            "what_happened": "模型引用了本轮不存在的来源卡片。",
            "why_it_matters": "需要阻止证据越界。",
            "source_ids": [99],
            "confidence": "high",
        },
        "highlights": [],
        "details": [],
        "watchlist": [],
        "missing_info": [],
        "closing": "以上。",
    }, ensure_ascii=False)
    runtime = TaskRuntime(
        _FakeTaskModelPort(_completion_for("news_daily_quality", raw)),
        run_id_factory=lambda: "taskrun_news_evidence",
    )

    result = runtime.execute(TaskInvocation(
        invocation_id="news_daily_quality",
        route_key="news_daily_quality",
        input_values={"message": "候选卡片正文"},
        request_context={"allowed_source_ids": (1, 2)},
    ))

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code.value == "business_validation_failed"
    assert result.validation_diagnostics[0].code == (
        "news_source_id_not_authorized"
    )


def test_group_topics_task_rejects_evidence_outside_trusted_window():
    from core.task_runtime import TaskInvocation, TaskRuntime

    raw = json.dumps({
        "topics": [{
            "topic": "窗口外证据",
            "contributors": ["u1"],
            "detail": "引用了不属于当前可信窗口的消息。",
            "evidence_log_ids": [999],
        }],
    }, ensure_ascii=False)
    completions = tuple(
        _completion_for("group_analysis_topics", raw)
        for _ in range(3)
    )
    runtime = TaskRuntime(
        _FakeTaskModelPort(*completions),
        run_id_factory=lambda: "taskrun_group_evidence",
    )

    result = runtime.execute(TaskInvocation(
        invocation_id="group_analysis_topics",
        route_key="group_analysis_topics",
        input_values={"message": "可信窗口消息"},
        request_context={"allowed_evidence_log_ids": (1, 2)},
    ))

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code.value == "business_validation_failed"
    assert result.failure.terminal_action.value == "branch_failed"
    assert result.attempt_count == 3
    assert result.validation_diagnostics[0].code == (
        "group_evidence_not_authorized"
    )


@pytest.mark.parametrize(
    ("evidence_log_ids", "target_memory_id", "expected_code"),
    [
        ([999], None, "group_learning_evidence_not_authorized"),
        ([101], 88, "group_learning_target_not_authorized"),
    ],
)
def test_group_memory_learning_task_rejects_scope_escape_end_to_end(
    evidence_log_ids,
    target_memory_id,
    expected_code,
):
    from core.task_runtime import TaskInvocation, TaskRuntime

    action = "merge_into" if target_memory_id is not None else "new"
    raw = json.dumps({
        "reviews": [{
            "candidate_id": "glc_test_1",
            "action": action,
            "candidate_type": "slang",
            "content": "摸鱼",
            "meaning": "工作时间暂时休息",
            "evidence_log_ids": evidence_log_ids,
            "target_memory_id": target_memory_id,
            "reason": "尝试引用未授权范围",
        }],
        "discoveries": [],
    }, ensure_ascii=False)
    runtime = TaskRuntime(
        _FakeTaskModelPort(
            _completion_for("group_memory_learning", raw)
        ),
        run_id_factory=lambda: "taskrun_group_learning_scope",
    )

    result = runtime.execute(TaskInvocation(
        invocation_id="group_memory_learning",
        route_key="group_memory_learning",
        input_values={"message": "群学习候选审核输入"},
        request_context={
            "allowed_candidate_ids": ("glc_test_1",),
            "candidate_types": {"glc_test_1": "slang"},
            "allowed_evidence_log_ids": (101,),
            "allowed_target_memory_ids": {"glc_test_1": (7,)},
            "selected_candidate_types": ("slang",),
        },
    ))

    assert result.ok is False
    assert result.failure is not None
    assert result.failure.code.value == "business_validation_failed"
    assert result.failure.terminal_action.value == "preserve_pending"
    assert result.validation_diagnostics[0].code == expected_code


@pytest.mark.parametrize(
    ("execution_mode", "expected_transport"),
    [
        ("route_completion", "route"),
        ("chat_completion", "chat"),
    ],
)
def test_route_task_adapter_selects_transport_from_descriptor(
    monkeypatch,
    execution_mode,
    expected_transport,
):
    import clients.task_runtime_adapter as adapter_module
    from core.model_provider.route_registry import ModelRouteExecutionMode
    from core.task_runtime import TaskModelRequest

    calls: list[str] = []
    route = {
        "provider_id": "newapi",
        "model": "route-model",
        "base_url": "http://model.invalid/v1",
        "api_key": "test-key",
        "temperature": 0.1,
        "max_tokens": 128,
        "enable_thinking": "false",
    }
    monkeypatch.setattr(
        adapter_module.classifier_client,
        "resolve_model_route",
        lambda _route_key: route,
    )
    monkeypatch.setattr(
        adapter_module.classifier_client,
        "resolve_model_route_attempts",
        lambda _route_key: [route],
        raising=False,
    )
    monkeypatch.setattr(
        adapter_module,
        "require_model_route_descriptor",
        lambda _route_key: SimpleNamespace(
            execution_mode=ModelRouteExecutionMode(execution_mode),
        ),
    )

    def fake_route_response(**_kwargs):
        calls.append("route")
        return SimpleNamespace(
            content='{"ok":true}',
            raw_response={"model": "route-model"},
            usage={},
            finish_reason="stop",
        )

    class DummyClient:
        def __init__(self, **_kwargs):
            pass

        async def chat_completion(self, **_kwargs):
            calls.append("chat")
            return {
                "choices": [{
                    "message": {"content": '{"ok":true}'},
                    "finish_reason": "stop",
                }],
                "model": "chat-model",
            }

    monkeypatch.setattr(
        adapter_module,
        "call_model_route_response",
        fake_route_response,
    )
    monkeypatch.setattr(adapter_module, "NewAPIClient", DummyClient)

    result = adapter_module.RouteTaskModelAdapter().complete_task(
        TaskModelRequest(
            task_id="transport-test",
            contract_version="test-v1",
            route_key="private_decision",
            messages=({"role": "user", "content": "测试"},),
            run_id="taskrun_transport",
            attempt_no=1,
            timeout_seconds=5,
        )
    )

    assert calls == [expected_transport]
    assert result.content == '{"ok":true}'


def test_route_task_adapter_switches_chat_candidate_on_runtime_retry(monkeypatch):
    import clients.task_runtime_adapter as adapter_module
    from core.model_provider.route_registry import ModelRouteExecutionMode
    from core.task_runtime import TaskModelRequest

    candidates = [
        {
            "provider_id": "newapi",
            "model": "cheap-json-weak",
            "base_url": "http://first.invalid/v1",
            "api_key": "first-key",
            "temperature": 0.1,
            "max_tokens": 128,
            "enable_thinking": "false",
        },
        {
            "provider_id": "newapi",
            "model": "reliable-json-model",
            "base_url": "http://second.invalid/v1",
            "api_key": "second-key",
            "temperature": 0.1,
            "max_tokens": 128,
            "enable_thinking": "false",
        },
    ]
    monkeypatch.setattr(
        adapter_module.classifier_client,
        "resolve_model_route",
        lambda _route_key: candidates[0],
    )
    monkeypatch.setattr(
        adapter_module.classifier_client,
        "resolve_model_route_attempts",
        lambda _route_key: candidates,
        raising=False,
    )
    monkeypatch.setattr(
        adapter_module,
        "require_model_route_descriptor",
        lambda _route_key: SimpleNamespace(
            execution_mode=ModelRouteExecutionMode.CHAT_COMPLETION,
        ),
    )

    calls: list[dict] = []

    class DummyClient:
        def __init__(self, **kwargs):
            self.connection = kwargs

        async def chat_completion(self, **kwargs):
            calls.append({**self.connection, **kwargs})
            return {
                "choices": [{
                    "message": {"content": '{"ok":true}'},
                    "finish_reason": "stop",
                }],
                "model": kwargs["manual_model"],
            }

    monkeypatch.setattr(adapter_module, "NewAPIClient", DummyClient)

    result = adapter_module.RouteTaskModelAdapter().complete_task(
        TaskModelRequest(
            task_id="candidate-retry",
            contract_version="test-v1",
            route_key="session_summary",
            messages=({"role": "user", "content": "测试"},),
            run_id="taskrun_candidate_retry",
            attempt_no=2,
            timeout_seconds=5,
        )
    )

    assert result.content == '{"ok":true}'
    assert calls == [{
        "api_key": "second-key",
        "base_url": "http://second.invalid/v1",
        "messages": [{"role": "user", "content": "测试"}],
        "temperature": 0.1,
        "manual_model": "reliable-json-model",
        "max_tokens": 128,
        "llm_source": "session_summary",
        "enable_thinking": "false",
    }]


@pytest.mark.parametrize(
    "wrapper",
    [
        lambda payload: f"```json\n{payload}\n```",
        lambda payload: f"<think>先核对字段，不输出这段。</think>\n{payload}",
        lambda payload: (
            "<think>先核对字段，不输出这段。</think>\n"
            f"```json\n{payload}\n```"
        ),
    ],
)
def test_session_summary_accepts_controlled_json_transport_envelopes(wrapper):
    from core.task_runtime import TaskInvocation, TaskRuntime, thaw_task_value

    payload = json.dumps(
        {
            "summary": "群聊摘要",
            "open_threads": [],
            "decisions": [],
            "important_user_requests": [],
            "resolved_items": [],
            "artifacts": [],
            "participants": ["甲"],
            "keywords": ["缓存"],
            "quality": {"score": 0.95, "issues": []},
            "inheritance": [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    raw = wrapper(payload)
    runtime = TaskRuntime(
        _FakeTaskModelPort(
            _completion_for("session_summary", raw),
            _completion_for("session_summary", raw),
        ),
        run_id_factory=lambda: "taskrun_session_summary_envelope",
        sleeper=lambda _seconds: None,
        jitter_source=lambda: 0.0,
    )

    result = runtime.execute(TaskInvocation(
        invocation_id="session_summary",
        route_key="session_summary",
        input_values={},
        rendered_messages=({"role": "user", "content": "生成摘要"},),
        timeout_budget_seconds=120,
    ))

    assert result.ok is True
    assert result.parsed_value["summary"] == "群聊摘要"
    assert thaw_task_value(result.parsed_value["quality"]) == {
        "score": 0.95,
        "issues": [],
    }


def test_task_runtime_stop_never_recreates_adapter_implicitly():
    from core.task_runtime import (
        execute_task,
        start_task_runtime,
        stop_task_runtime,
        task_runtime_status,
    )

    stop_task_runtime()
    start_task_runtime(
        _FakeTaskModelPort(_completion(_private_output()))
    )
    assert task_runtime_status()["state"] == "running"
    stop_task_runtime()

    assert task_runtime_status() == {
        "state": "stopped",
        "adapter_id": "",
    }
    with pytest.raises(RuntimeError, match="已经停止"):
        execute_task(_invocation())
