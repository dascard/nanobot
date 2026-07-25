"""Event、Observer Hook、Transform Hook 与 Policy 的分离合同测试。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


def _observer_descriptor(
    hook_id: str,
    *,
    priority: int,
    fail_closed: bool = False,
):
    from core.runtime.extensions import (
        RuntimeFailurePolicy,
        RuntimeHookDescriptor,
        RuntimeHookKind,
    )

    return RuntimeHookDescriptor(
        hook_id=hook_id,
        kind=RuntimeHookKind.OBSERVER,
        owner_module="tests.runtime",
        domain="test",
        input_contract="runtime.event.v1",
        output_contract="none",
        priority=priority,
        failure_policy=(
            RuntimeFailurePolicy.FAIL_CLOSED
            if fail_closed
            else RuntimeFailurePolicy.FAIL_OPEN
        ),
        trusted_builtin=True,
    )


def _transform_descriptor(
    hook_id: str,
    *,
    priority: int,
    fail_closed: bool = True,
    trusted_builtin: bool = True,
    output_contract: str = "tests.transform.output.v1",
):
    from core.runtime.extensions import (
        PROTECTED_TRANSFORM_INVARIANTS,
        RuntimeFailurePolicy,
        RuntimeHookDescriptor,
        RuntimeHookKind,
    )

    return RuntimeHookDescriptor(
        hook_id=hook_id,
        kind=RuntimeHookKind.TRANSFORM,
        owner_module="tests.runtime",
        domain="test",
        input_contract="tests.transform.input.v1",
        output_contract=output_contract,
        priority=priority,
        failure_policy=(
            RuntimeFailurePolicy.FAIL_CLOSED
            if fail_closed
            else RuntimeFailurePolicy.FAIL_OPEN
        ),
        trusted_builtin=trusted_builtin,
        protected_invariants=PROTECTED_TRANSFORM_INVARIANTS,
    )


class _RecordingObserver:
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        failure: Exception | None = None,
        result: object | None = None,
    ) -> None:
        self.name = name
        self.calls = calls
        self.failure = failure
        self.result = result

    def observe(self, event: object) -> object | None:
        del event
        self.calls.append(self.name)
        if self.failure is not None:
            raise self.failure
        return self.result


@dataclass
class _AppendTransform:
    label: str

    def transform(self, value: object) -> object:
        assert isinstance(value, tuple)
        return (*value, self.label)


def _protected_invariants() -> dict[str, str]:
    from core.runtime.extensions import PROTECTED_TRANSFORM_INVARIANTS

    return {
        invariant: f"fixed:{invariant}"
        for invariant in PROTECTED_TRANSFORM_INVARIANTS
    }


def test_observer_dispatcher_orders_hooks_and_fail_open_preserves_business_result():
    from core.runtime.extensions import (
        RuntimeObserverBinding,
        RuntimeObserverDispatcher,
    )

    calls: list[str] = []
    dispatcher = RuntimeObserverDispatcher((
        RuntimeObserverBinding(
            _observer_descriptor("observer.last", priority=30),
            _RecordingObserver("last", calls),
        ),
        RuntimeObserverBinding(
            _observer_descriptor("observer.broken", priority=20),
            _RecordingObserver(
                "broken",
                calls,
                failure=RuntimeError("不得进入业务控制流"),
            ),
        ),
        RuntimeObserverBinding(
            _observer_descriptor("observer.first", priority=10),
            _RecordingObserver("first", calls),
        ),
    ))

    business_result = {"status": "success"}
    report = dispatcher.dispatch(object())

    assert business_result == {"status": "success"}
    assert calls == ["first", "broken", "last"]
    assert report.observed_hook_ids == ("observer.first", "observer.last")
    assert report.failure_ids == ("observer.broken",)
    assert report.failures[0].error_type == "RuntimeError"
    assert "不得进入业务控制流" not in repr(report)


def test_observer_non_none_return_is_typed_contract_failure_not_replacement():
    from core.runtime.extensions import (
        RuntimeHookFailureCode,
        RuntimeObserverBinding,
        RuntimeObserverDispatcher,
    )

    calls: list[str] = []
    dispatcher = RuntimeObserverDispatcher((
        RuntimeObserverBinding(
            _observer_descriptor("observer.invalid", priority=10),
            _RecordingObserver(
                "invalid",
                calls,
                result={"replacement": "禁止"},
            ),
        ),
        RuntimeObserverBinding(
            _observer_descriptor("observer.after", priority=20),
            _RecordingObserver("after", calls),
        ),
    ))

    report = dispatcher.dispatch("event")

    assert calls == ["invalid", "after"]
    assert report.observed_hook_ids == ("observer.after",)
    assert report.failures[0].code is RuntimeHookFailureCode.INVALID_RETURN


def test_fail_closed_observer_raises_typed_error_without_leaking_message():
    from core.runtime.extensions import (
        RuntimeHookExecutionError,
        RuntimeHookFailureCode,
        RuntimeObserverBinding,
        RuntimeObserverDispatcher,
    )

    calls: list[str] = []
    dispatcher = RuntimeObserverDispatcher((
        RuntimeObserverBinding(
            _observer_descriptor(
                "observer.closed",
                priority=10,
                fail_closed=True,
            ),
            _RecordingObserver(
                "closed",
                calls,
                failure=RuntimeError("secret failure text"),
            ),
        ),
    ))

    with pytest.raises(RuntimeHookExecutionError) as raised:
        dispatcher.dispatch("event")

    assert raised.value.hook_id == "observer.closed"
    assert raised.value.code is RuntimeHookFailureCode.EXECUTION_FAILED
    assert raised.value.error_type == "RuntimeError"
    assert "secret failure text" not in str(raised.value)


def test_runtime_event_emitter_uses_observer_dispatcher_without_changing_event():
    from core.runtime.events import RuntimeEventEmitter, RuntimeEventRegistry
    from core.runtime.extensions import (
        RuntimeObserverBinding,
        RuntimeObserverDispatcher,
    )

    calls: list[str] = []
    dispatcher = RuntimeObserverDispatcher((
        RuntimeObserverBinding(
            _observer_descriptor("observer.event", priority=10),
            _RecordingObserver("event", calls),
        ),
    ))
    emitter = RuntimeEventEmitter(
        RuntimeEventRegistry((
            __import__(
                "core.runtime.events",
                fromlist=["RuntimeEventDescriptor"],
            ).RuntimeEventDescriptor(
                name="tests.observed",
                domain="test",
                phases=("succeeded",),
            ),
        )).freeze(),
        observer_dispatcher=dispatcher,
        event_id_factory=lambda: "evt_observed",
    )

    event = emitter.emit("tests.observed", "succeeded")

    assert event.event_id == "evt_observed"
    assert calls == ["event"]


def test_hook_registry_rejects_duplicate_and_post_freeze_mutation():
    from core.runtime.extensions import (
        RuntimeHookRegistry,
        RuntimeHookRegistryError,
    )

    registry = RuntimeHookRegistry()
    descriptor = _observer_descriptor("observer.unique", priority=10)
    registry.register(descriptor)
    with pytest.raises(RuntimeHookRegistryError, match="重复"):
        registry.register(descriptor)

    registry.freeze()
    with pytest.raises(RuntimeHookRegistryError, match="冻结"):
        registry.register(
            _observer_descriptor("observer.late", priority=20)
        )
    assert registry.freeze() is registry
    assert registry.frozen is True
    assert registry.registry_snapshot.generation == 1


def test_hook_descriptor_rejects_ambiguous_kind_output_and_invariants():
    from core.runtime.extensions import (
        PROTECTED_TRANSFORM_INVARIANTS,
        RuntimeExtensionKind,
        RuntimeFailurePolicy,
        RuntimeHookDescriptor,
    )

    common = {
        "owner_module": "tests.runtime",
        "domain": "test",
        "input_contract": "tests.input.v1",
        "priority": 10,
        "failure_policy": RuntimeFailurePolicy.FAIL_OPEN,
        "trusted_builtin": True,
    }
    with pytest.raises(ValueError, match="observer 或 transform"):
        RuntimeHookDescriptor(
            hook_id="hook.policy",
            kind=RuntimeExtensionKind.POLICY,
            output_contract="none",
            **common,
        )
    with pytest.raises(ValueError, match="output_contract"):
        RuntimeHookDescriptor(
            hook_id="observer.output",
            kind=RuntimeExtensionKind.OBSERVER,
            output_contract="tests.output.v1",
            **common,
        )
    with pytest.raises(ValueError, match="不声明"):
        RuntimeHookDescriptor(
            hook_id="observer.invariants",
            kind=RuntimeExtensionKind.OBSERVER,
            output_contract="none",
            protected_invariants=PROTECTED_TRANSFORM_INVARIANTS,
            **common,
        )


def test_transform_requires_trusted_builtin_and_all_protected_invariants():
    from core.runtime.extensions import (
        PROTECTED_TRANSFORM_INVARIANTS,
        RuntimeFailurePolicy,
        RuntimeHookDescriptor,
        RuntimeHookKind,
    )

    with pytest.raises(ValueError, match="受信内建"):
        _transform_descriptor(
            "transform.untrusted",
            priority=10,
            trusted_builtin=False,
        )

    with pytest.raises(ValueError, match="受保护不变量"):
        RuntimeHookDescriptor(
            hook_id="transform.incomplete",
            kind=RuntimeHookKind.TRANSFORM,
            owner_module="tests.runtime",
            domain="test",
            input_contract="tests.transform.input.v1",
            output_contract="tests.transform.output.v1",
            priority=10,
            failure_policy=RuntimeFailurePolicy.FAIL_CLOSED,
            trusted_builtin=True,
            protected_invariants=(
                PROTECTED_TRANSFORM_INVARIANTS[0],
            ),
        )


def test_transform_dispatcher_composes_by_priority_without_exposing_invariants():
    from core.runtime.extensions import (
        RuntimeTransformBinding,
        RuntimeTransformDispatcher,
    )

    dispatcher = RuntimeTransformDispatcher((
        RuntimeTransformBinding(
            _transform_descriptor("transform.second", priority=20),
            _AppendTransform("second"),
        ),
        RuntimeTransformBinding(
            _transform_descriptor("transform.first", priority=10),
            _AppendTransform("first"),
        ),
    ))

    result = dispatcher.transform(
        (),
        protected_invariants=_protected_invariants(),
    )

    assert result.value == ("first", "second")
    assert result.applied_hook_ids == (
        "transform.first",
        "transform.second",
    )
    assert dict(result.protected_invariants) == _protected_invariants()
    with pytest.raises(TypeError):
        result.protected_invariants["identity"] = "tampered"


def test_transform_cannot_return_protected_invariant_override():
    from core.runtime.extensions import (
        RuntimeHookFailureCode,
        RuntimeTransformBinding,
        RuntimeTransformDispatcher,
    )

    class InvalidTransform:
        def transform(self, value: object) -> object:
            del value
            return {"identity": "tampered"}

    dispatcher = RuntimeTransformDispatcher((
        RuntimeTransformBinding(
            _transform_descriptor(
                "transform.invalid",
                priority=10,
                fail_closed=False,
            ),
            InvalidTransform(),
        ),
    ))

    result = dispatcher.transform(
        {"content": "original"},
        protected_invariants=_protected_invariants(),
    )

    assert result.value == {"content": "original"}
    assert result.failures[0].code is RuntimeHookFailureCode.INVARIANT_OVERRIDE


def test_transform_fail_closed_and_pipeline_contract_errors_are_typed():
    from core.runtime.extensions import (
        RuntimeHookContractError,
        RuntimeHookExecutionError,
        RuntimeHookFailureCode,
        RuntimeTransformBinding,
        RuntimeTransformDispatcher,
    )

    class BrokenTransform:
        def transform(self, value: object) -> object:
            del value
            raise RuntimeError("internal detail")

    dispatcher = RuntimeTransformDispatcher((
        RuntimeTransformBinding(
            _transform_descriptor("transform.closed", priority=10),
            BrokenTransform(),
        ),
    ))
    with pytest.raises(RuntimeHookExecutionError) as raised:
        dispatcher.transform(
            (),
            protected_invariants=_protected_invariants(),
        )
    assert raised.value.code is RuntimeHookFailureCode.EXECUTION_FAILED
    assert "internal detail" not in str(raised.value)

    with pytest.raises(RuntimeHookContractError, match="缺少"):
        RuntimeTransformDispatcher((
            RuntimeTransformBinding(
                _transform_descriptor("transform.required", priority=10),
                _AppendTransform("unused"),
            ),
        )).transform((), protected_invariants={})

    second_contract = _transform_descriptor(
        "transform.other_contract",
        priority=20,
        output_contract="tests.other.output.v1",
    )
    with pytest.raises(RuntimeHookContractError, match="必须一致"):
        RuntimeTransformDispatcher((
            RuntimeTransformBinding(
                _transform_descriptor("transform.base", priority=10),
                _AppendTransform("base"),
            ),
            RuntimeTransformBinding(
                second_contract,
                _AppendTransform("other"),
            ),
        ))


def _policy_descriptor(
    policy_id: str,
    *,
    fail_closed: bool,
    security_sensitive: bool,
):
    from core.runtime.extensions import (
        PolicyDescriptor,
        RuntimeFailurePolicy,
    )

    return PolicyDescriptor(
        policy_id=policy_id,
        owner_module="tests.runtime",
        domain="test",
        input_contract="tests.policy.input.v1",
        output_contract="tests.policy.output.v1",
        failure_policy=(
            RuntimeFailurePolicy.FAIL_CLOSED
            if fail_closed
            else RuntimeFailurePolicy.FAIL_OPEN
        ),
        security_sensitive=security_sensitive,
    )


def test_security_policy_requires_fail_closed_and_returns_typed_deny_fallback():
    from core.runtime.extensions import (
        PolicyFailureCode,
        PolicyFailureOutcome,
        RuntimeFailurePolicy,
        PolicyDescriptor,
        execute_policy,
    )

    with pytest.raises(ValueError, match="安全 Policy"):
        PolicyDescriptor(
            policy_id="policy.unsafe",
            owner_module="tests.runtime",
            domain="test",
            input_contract="tests.policy.input.v1",
            output_contract="tests.policy.output.v1",
            failure_policy=RuntimeFailurePolicy.FAIL_OPEN,
            security_sensitive=True,
        )

    descriptor = _policy_descriptor(
        "policy.security",
        fail_closed=True,
        security_sensitive=True,
    )

    def fail() -> bool:
        raise RuntimeError("敏感底层异常正文")

    result = execute_policy(
        descriptor,
        fail,
        fallback=lambda failure: (
            failure.fallback_outcome is PolicyFailureOutcome.ALLOW
        ),
    )

    assert result.value is False
    assert result.used_fallback is True
    assert result.failure is not None
    assert result.failure.code is PolicyFailureCode.EVALUATION_FAILED
    assert result.failure.fallback_outcome is PolicyFailureOutcome.DENY
    assert "敏感底层异常正文" not in repr(result)


def test_availability_policy_failure_is_typed_allow_not_none_or_block_string():
    from core.runtime.extensions import (
        PolicyFailureOutcome,
        execute_policy,
    )

    descriptor = _policy_descriptor(
        "policy.availability",
        fail_closed=False,
        security_sensitive=False,
    )
    result = execute_policy(
        descriptor,
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        fallback=lambda failure: (
            failure.fallback_outcome is PolicyFailureOutcome.ALLOW
        ),
    )

    assert result.value is True
    assert result.failure is not None
    assert result.failure.fallback_outcome is PolicyFailureOutcome.ALLOW


def test_policy_registry_and_invalid_results_have_explicit_state():
    from core.runtime.extensions import (
        PolicyExecutionError,
        PolicyFailureCode,
        PolicyRegistry,
        PolicyRegistryError,
        execute_policy,
    )

    descriptor = _policy_descriptor(
        "policy.registered",
        fail_closed=False,
        security_sensitive=False,
    )
    registry = PolicyRegistry()
    with pytest.raises(PolicyRegistryError, match="尚未冻结"):
        _ = registry.registry_snapshot
    registry.register(descriptor)
    with pytest.raises(PolicyRegistryError, match="重复"):
        registry.register(descriptor)
    registry.freeze()
    assert registry.frozen is True
    assert registry.freeze() is registry
    assert registry.registry_snapshot.require("policy.registered") is descriptor
    with pytest.raises(PolicyRegistryError, match="已冻结"):
        registry.register(
            _policy_descriptor(
                "policy.late",
                fail_closed=False,
                security_sensitive=False,
            )
        )

    none_result = execute_policy(
        descriptor,
        lambda: None,
        fallback=lambda failure: failure.code.value,
    )
    assert none_result.value == PolicyFailureCode.INVALID_RESULT.value
    assert none_result.failure is not None
    assert none_result.failure.code is PolicyFailureCode.INVALID_RESULT

    async def asynchronous_policy():
        return True

    async_result = execute_policy(
        descriptor,
        asynchronous_policy,
        fallback=lambda failure: failure.code.value,
    )
    assert async_result.value == PolicyFailureCode.INVALID_RESULT.value

    with pytest.raises(PolicyExecutionError, match="fallback"):
        execute_policy(
            descriptor,
            lambda: (_ for _ in ()).throw(RuntimeError("offline")),
            fallback=lambda failure: None,
        )


def test_prompt_contribution_declares_trusted_transform_contract():
    from core.prompt_v2.contribution_registry import (
        canonical_prompt_contributions,
    )
    from core.runtime.extensions import (
        PROTECTED_TRANSFORM_INVARIANTS,
        RuntimeHookKind,
    )

    descriptors = canonical_prompt_contributions()

    assert descriptors
    for descriptor in descriptors:
        assert descriptor.kind is RuntimeHookKind.TRANSFORM
        assert descriptor.input_contract == "prompt.contribution.render_context.v1"
        assert descriptor.output_contract == "prompt.contribution.render_result.v1"
        assert descriptor.trusted_builtin is True
        assert descriptor.protected_invariants == (
            PROTECTED_TRANSFORM_INVARIANTS
        )

    from core.prompt_v2.schema import PromptFlowSection

    assert {
        "kind",
        "input_contract",
        "output_contract",
        "trusted_builtin",
        "protected_invariants",
    }.issubset(PromptFlowSection.__annotations__)


def test_prompt_renderer_cannot_mutate_context_and_must_return_typed_result():
    from core.prompt_v2.contribution_registry import (
        PromptContributionRenderContext,
        PromptContributionRendererError,
        canonical_prompt_contributions,
        render_prompt_contribution,
    )

    descriptor = canonical_prompt_contributions()[0]
    context = PromptContributionRenderContext(
        descriptor=descriptor,
        node={"id": descriptor.contribution_id},
        template_values={"name": "value"},
        runtime_sections={},
        input_variables={},
    )

    class MutatingRenderer:
        renderer_id = descriptor.renderer_id

        def render(self, render_context):
            render_context.template_values["name"] = "tampered"
            return object()

    with pytest.raises(TypeError):
        render_prompt_contribution(MutatingRenderer(), context)

    class InvalidRenderer:
        renderer_id = descriptor.renderer_id

        def render(self, render_context):
            del render_context
            return {"content": "not typed"}

    with pytest.raises(PromptContributionRendererError, match="RenderResult"):
        render_prompt_contribution(InvalidRenderer(), context)


def test_timing_policy_mode_is_enum_and_invalid_mode_fails_closed():
    from core.timing_model_policy import (
        TimingModelMode,
        TimingModelPolicy,
    )

    policy = TimingModelPolicy("shadow", "test")

    assert policy.mode is TimingModelMode.SHADOW
    with pytest.raises(ValueError, match="TimingModelMode"):
        TimingModelPolicy("arbitrary", "test")


def test_sandbox_policy_runtime_failure_returns_typed_denial(monkeypatch):
    from core.sandbox.access_policy import SandboxAccessPolicy

    def fail_evaluation(self, *args, **kwargs):
        del self, args, kwargs
        raise RuntimeError("database path must not leak")

    monkeypatch.setattr(
        SandboxAccessPolicy,
        "_evaluate",
        fail_evaluation,
    )
    decision = SandboxAccessPolicy(
        None,
        infrastructure_allowed=True,
    ).evaluate(
        "workspace_read",
        platform="qq",
        chat_type="private",
        session_id="user_1",
    )

    assert decision.allowed is False
    assert decision.code == "authorization_failed"
    assert "database path" not in decision.reason
