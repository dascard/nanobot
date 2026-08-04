"""受管 Plugin 生命周期、字段边界和诊断事件测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest


def _invariants() -> dict[str, str]:
    from core.runtime.plugin_lifecycle import (
        PROTECTED_RUNTIME_HOOK_INVARIANTS,
    )

    return {
        name: f"fixed:{name}"
        for name in PROTECTED_RUNTIME_HOOK_INVARIANTS
    }


def _hook(
    hook_id: str,
    point,
    *,
    order: int = 0,
    timeout_seconds: float = 1.0,
    fail_closed: bool = False,
    readable_fields: tuple[str, ...],
    mutable_fields: tuple[str, ...] = (),
):
    from core.runtime.extensions import RuntimeFailurePolicy
    from core.runtime.plugin_lifecycle import RuntimePluginHookDescriptor

    return RuntimePluginHookDescriptor(
        hook_id=hook_id,
        point=point,
        order=order,
        timeout_seconds=timeout_seconds,
        failure_policy=(
            RuntimeFailurePolicy.FAIL_CLOSED
            if fail_closed
            else RuntimeFailurePolicy.FAIL_OPEN
        ),
        readable_fields=readable_fields,
        mutable_fields=mutable_fields,
        trusted_builtin=bool(mutable_fields),
    )


@dataclass
class _Plugin:
    name: str
    calls: list[str]
    patches: dict[str, object] = field(default_factory=dict)
    failures: dict[str, BaseException] = field(default_factory=dict)
    delays: dict[str, float] = field(default_factory=dict)
    mutate_nested: bool = False

    async def on_load(self, context) -> None:
        assert context["runtime_id"] == "native:test"
        self.calls.append(f"load:{self.name}")
        failure = self.failures.get("load")
        if failure is not None:
            raise failure

    async def on_unload(self) -> None:
        self.calls.append(f"unload:{self.name}")
        failure = self.failures.get("unload")
        if failure is not None:
            raise failure

    async def invoke(self, invocation):
        from core.runtime.plugin_lifecycle import RuntimeHookPatch

        self.calls.append(f"hook:{self.name}:{invocation.hook_id}")
        delay = self.delays.get(invocation.hook_id, 0.0)
        if delay:
            await asyncio.sleep(delay)
        failure = self.failures.get(invocation.hook_id)
        if failure is not None:
            raise failure
        if self.mutate_nested:
            invocation.fields["arguments"]["value"] = "篡改"
        patch = self.patches.get(invocation.hook_id)
        if patch is None:
            return None
        if isinstance(patch, RuntimeHookPatch):
            return patch
        return patch


def _binding(
    plugin_id: str,
    plugin: _Plugin,
    *hooks,
    order: int = 0,
    required: bool = True,
):
    from core.runtime.plugin_lifecycle import (
        RuntimePluginBinding,
        RuntimePluginDescriptor,
    )

    return RuntimePluginBinding(
        RuntimePluginDescriptor(
            plugin_id=plugin_id,
            version="1.0.0",
            order=order,
            required=required,
            lifecycle_timeout_seconds=1.0,
            hooks=tuple(hooks),
        ),
        plugin,
    )


def test_hook_descriptor_rejects_unsafe_fields_and_observer_fail_closed():
    from core.runtime.plugin_lifecycle import RuntimeHookPoint

    with pytest.raises(ValueError, match="可修改字段必须"):
        _hook(
            "unsafe.mutable",
            RuntimeHookPoint.PRE_TOOL,
            readable_fields=("tool_name",),
            mutable_fields=("arguments",),
        )
    with pytest.raises(ValueError, match="切点不支持的可读字段"):
        _hook(
            "unsafe.permission",
            RuntimeHookPoint.PRE_TOOL,
            readable_fields=("permission",),
        )
    with pytest.raises(ValueError, match="必须 fail open"):
        _hook(
            "event.closed",
            RuntimeHookPoint.EVENT,
            fail_closed=True,
            readable_fields=("event",),
        )
    with pytest.raises(ValueError, match="受信内建"):
        from core.runtime.extensions import RuntimeFailurePolicy
        from core.runtime.plugin_lifecycle import RuntimePluginHookDescriptor

        RuntimePluginHookDescriptor(
            hook_id="tool.untrusted",
            point=RuntimeHookPoint.PRE_TOOL,
            order=0,
            timeout_seconds=1,
            failure_policy=RuntimeFailurePolicy.FAIL_CLOSED,
            readable_fields=("arguments",),
            mutable_fields=("arguments",),
            trusted_builtin=False,
        )


@pytest.mark.asyncio
async def test_plugin_manager_orders_load_hooks_and_reverse_unload():
    from core.runtime.plugin_lifecycle import (
        RuntimeHookPoint,
        RuntimeHookPatch,
        RuntimePluginManager,
        RuntimePluginState,
    )

    calls: list[str] = []
    first = _Plugin(
        "first",
        calls,
        patches={"arguments": RuntimeHookPatch({"arguments": {"value": 2}})},
    )
    second = _Plugin("second", calls)
    manager = RuntimePluginManager(
        "native:test",
        (
            _binding(
                "plugin.second",
                second,
                _hook(
                    "observe",
                    RuntimeHookPoint.PRE_TOOL,
                    order=20,
                    readable_fields=("arguments",),
                ),
                order=20,
            ),
            _binding(
                "plugin.first",
                first,
                _hook(
                    "arguments",
                    RuntimeHookPoint.PRE_TOOL,
                    order=10,
                    fail_closed=True,
                    readable_fields=("arguments",),
                    mutable_fields=("arguments",),
                ),
                order=10,
            ),
        ),
        diagnostic_emitter=lambda diagnostic: None,
    )

    await manager.start()
    result = await manager.dispatch(
        RuntimeHookPoint.PRE_TOOL,
        {"arguments": {"value": 1}},
        protected_invariants=_invariants(),
    )
    await manager.stop()

    assert manager.state is RuntimePluginState.STOPPED
    assert result.fields["arguments"]["value"] == 2
    assert result.applied_hook_ids == (
        "plugin.first:arguments",
        "plugin.second:observe",
    )
    assert calls == [
        "load:first",
        "load:second",
        "hook:first:arguments",
        "hook:second:observe",
        "unload:second",
        "unload:first",
    ]


@pytest.mark.asyncio
async def test_plugin_receives_deep_readonly_projection():
    from core.runtime.plugin_lifecycle import (
        RuntimeHookPoint,
        RuntimePluginExecutionError,
        RuntimePluginFailureCode,
        RuntimePluginManager,
    )

    diagnostics = []
    plugin = _Plugin("readonly", [], mutate_nested=True)
    manager = RuntimePluginManager(
        "native:test",
        (
            _binding(
                "plugin.readonly",
                plugin,
                _hook(
                    "readonly",
                    RuntimeHookPoint.PRE_TOOL,
                    fail_closed=True,
                    readable_fields=("arguments",),
                ),
            ),
        ),
        diagnostic_emitter=diagnostics.append,
    )
    await manager.start()

    with pytest.raises(RuntimePluginExecutionError) as raised:
        await manager.dispatch(
            RuntimeHookPoint.PRE_TOOL,
            {"arguments": {"value": 1}},
            protected_invariants=_invariants(),
        )

    assert raised.value.code is RuntimePluginFailureCode.EXECUTION_FAILED
    assert diagnostics[0].failure.error_type == "TypeError"
    await manager.stop()


@pytest.mark.asyncio
async def test_fail_open_error_is_diagnostic_and_next_hook_continues():
    from core.runtime.plugin_lifecycle import (
        RuntimeHookPoint,
        RuntimePluginFailureCode,
        RuntimePluginManager,
    )

    calls: list[str] = []
    broken = _Plugin(
        "broken",
        calls,
        failures={"broken": RuntimeError("敏感异常正文")},
    )
    healthy = _Plugin("healthy", calls)
    diagnostics = []
    manager = RuntimePluginManager(
        "native:test",
        (
            _binding(
                "plugin.broken",
                broken,
                _hook(
                    "broken",
                    RuntimeHookPoint.POST_MODEL,
                    readable_fields=("response",),
                ),
                order=10,
            ),
            _binding(
                "plugin.healthy",
                healthy,
                _hook(
                    "healthy",
                    RuntimeHookPoint.POST_MODEL,
                    readable_fields=("response",),
                ),
                order=20,
            ),
        ),
        diagnostic_emitter=diagnostics.append,
    )
    await manager.start()

    result = await manager.dispatch(
        RuntimeHookPoint.POST_MODEL,
        {"response": "完成"},
        protected_invariants=_invariants(),
    )

    assert result.applied_hook_ids == ("plugin.healthy:healthy",)
    assert result.failures[0].code is RuntimePluginFailureCode.EXECUTION_FAILED
    assert diagnostics[0].failure.error_type == "RuntimeError"
    assert "敏感异常正文" not in repr(diagnostics[0])
    assert calls[-1] == "hook:healthy:healthy"
    await manager.stop()


@pytest.mark.asyncio
async def test_fail_closed_timeout_raises_typed_error_and_diagnostic():
    from core.runtime.plugin_lifecycle import (
        RuntimeHookPoint,
        RuntimePluginExecutionError,
        RuntimePluginFailureCode,
        RuntimePluginManager,
    )

    diagnostics = []
    plugin = _Plugin("slow", [], delays={"slow": 0.05})
    manager = RuntimePluginManager(
        "native:test",
        (
            _binding(
                "plugin.slow",
                plugin,
                _hook(
                    "slow",
                    RuntimeHookPoint.PRE_MODEL,
                    timeout_seconds=0.005,
                    fail_closed=True,
                    readable_fields=("model",),
                ),
            ),
        ),
        diagnostic_emitter=diagnostics.append,
    )
    await manager.start()

    with pytest.raises(RuntimePluginExecutionError) as raised:
        await manager.dispatch(
            RuntimeHookPoint.PRE_MODEL,
            {"model": "qwen"},
            protected_invariants=_invariants(),
        )

    assert raised.value.code is RuntimePluginFailureCode.TIMED_OUT
    assert diagnostics[0].timeout_ms == 5
    await manager.stop()


@pytest.mark.asyncio
async def test_invalid_patch_and_undeclared_update_are_typed_failures():
    from core.runtime.plugin_lifecycle import (
        RuntimeHookPoint,
        RuntimeHookPatch,
        RuntimePluginFailureCode,
        RuntimePluginManager,
    )

    diagnostics = []
    plugin = _Plugin(
        "invalid",
        [],
        patches={
            "raw": {"arguments": {"value": 2}},
            "undeclared": RuntimeHookPatch({"tool_name": "other"}),
        },
    )
    manager = RuntimePluginManager(
        "native:test",
        (
            _binding(
                "plugin.invalid",
                plugin,
                _hook(
                    "raw",
                    RuntimeHookPoint.PRE_TOOL,
                    order=10,
                    readable_fields=("arguments",),
                ),
                _hook(
                    "undeclared",
                    RuntimeHookPoint.PRE_TOOL,
                    order=20,
                    readable_fields=("arguments", "tool_name"),
                ),
            ),
        ),
        diagnostic_emitter=diagnostics.append,
    )
    await manager.start()

    result = await manager.dispatch(
        RuntimeHookPoint.PRE_TOOL,
        {"arguments": {"value": 1}, "tool_name": "reply"},
        protected_invariants=_invariants(),
    )

    assert [failure.code for failure in result.failures] == [
        RuntimePluginFailureCode.INVALID_RETURN,
        RuntimePluginFailureCode.UNDECLARED_FIELD,
    ]
    assert len(diagnostics) == 2
    await manager.stop()


@pytest.mark.asyncio
async def test_hook_patch_contract_revalidation_obeys_failure_policy():
    from core.runtime.plugin_lifecycle import (
        RuntimeHookPatch,
        RuntimeHookPoint,
        RuntimePluginExecutionError,
        RuntimePluginFailureCode,
        RuntimePluginManager,
    )

    def validate(fields) -> None:
        if not isinstance(fields["arguments"]["value"], int):
            raise ValueError("不得泄露的参数正文")

    diagnostics = []
    open_plugin = _Plugin(
        "open",
        [],
        patches={
            "arguments": RuntimeHookPatch({
                "arguments": {"value": "敏感非法值"},
            }),
        },
    )
    open_manager = RuntimePluginManager(
        "native:test",
        (
            _binding(
                "plugin.open",
                open_plugin,
                _hook(
                    "arguments",
                    RuntimeHookPoint.PRE_TOOL,
                    readable_fields=("arguments",),
                    mutable_fields=("arguments",),
                ),
            ),
        ),
        diagnostic_emitter=diagnostics.append,
    )
    await open_manager.start()

    result = await open_manager.dispatch(
        RuntimeHookPoint.PRE_TOOL,
        {"arguments": {"value": 1}},
        protected_invariants=_invariants(),
        validate_fields=validate,
    )

    assert result.fields["arguments"]["value"] == 1
    assert result.failures[0].code is (
        RuntimePluginFailureCode.CONTRACT_VIOLATION
    )
    assert result.failures[0].error_type == "ValueError"
    assert "敏感非法值" not in repr(diagnostics[0])
    assert "不得泄露的参数正文" not in repr(diagnostics[0])
    await open_manager.stop()

    closed_plugin = _Plugin(
        "closed",
        [],
        patches={
            "arguments": RuntimeHookPatch({
                "arguments": {"value": "非法"},
            }),
        },
    )
    closed_manager = RuntimePluginManager(
        "native:test",
        (
            _binding(
                "plugin.closed",
                closed_plugin,
                _hook(
                    "arguments",
                    RuntimeHookPoint.PRE_TOOL,
                    fail_closed=True,
                    readable_fields=("arguments",),
                    mutable_fields=("arguments",),
                ),
            ),
        ),
        diagnostic_emitter=lambda diagnostic: None,
    )
    await closed_manager.start()

    with pytest.raises(RuntimePluginExecutionError) as raised:
        await closed_manager.dispatch(
            RuntimeHookPoint.PRE_TOOL,
            {"arguments": {"value": 1}},
            protected_invariants=_invariants(),
            validate_fields=validate,
        )

    assert raised.value.code is RuntimePluginFailureCode.CONTRACT_VIOLATION
    await closed_manager.stop()


@pytest.mark.asyncio
async def test_optional_load_failure_is_disabled_but_required_failure_closes():
    from core.runtime.plugin_lifecycle import (
        RuntimeHookPoint,
        RuntimePluginExecutionError,
        RuntimePluginManager,
        RuntimePluginState,
    )

    optional = _Plugin(
        "optional",
        [],
        failures={"load": RuntimeError("optional unavailable")},
    )
    manager = RuntimePluginManager(
        "native:test",
        (
            _binding(
                "plugin.optional",
                optional,
                _hook(
                    "event",
                    RuntimeHookPoint.EVENT,
                    readable_fields=("event",),
                ),
                required=False,
            ),
        ),
        diagnostic_emitter=lambda diagnostic: None,
    )
    await manager.start()
    assert manager.state is RuntimePluginState.RUNNING
    await manager.stop()

    required = _Plugin(
        "required",
        [],
        failures={"load": RuntimeError("required unavailable")},
    )
    closed = RuntimePluginManager(
        "native:test",
        (
            _binding(
                "plugin.required",
                required,
                _hook(
                    "event",
                    RuntimeHookPoint.EVENT,
                    readable_fields=("event",),
                ),
            ),
        ),
        diagnostic_emitter=lambda diagnostic: None,
    )
    with pytest.raises(RuntimePluginExecutionError):
        await closed.start()
    assert closed.state is RuntimePluginState.FAILED


@pytest.mark.asyncio
async def test_interrupt_hook_runs_from_sync_boundary_and_can_be_drained():
    from core.runtime.plugin_lifecycle import (
        RuntimeHookPoint,
        RuntimePluginManager,
    )

    calls: list[str] = []
    plugin = _Plugin("interrupt", calls)
    manager = RuntimePluginManager(
        "native:test",
        (
            _binding(
                "plugin.interrupt",
                plugin,
                _hook(
                    "interrupt",
                    RuntimeHookPoint.INTERRUPT,
                    readable_fields=("reason",),
                ),
            ),
        ),
        diagnostic_emitter=lambda diagnostic: None,
    )
    await manager.start()

    manager.dispatch_nowait(
        RuntimeHookPoint.INTERRUPT,
        {"reason": "用户取消"},
        protected_invariants=_invariants(),
    )
    await manager.drain_background_tasks()

    assert "hook:interrupt:interrupt" in calls
    await manager.stop()


def test_interrupt_hook_does_not_create_private_event_loop():
    from core.runtime.plugin_lifecycle import (
        RuntimeHookPoint,
        RuntimePluginContractError,
        RuntimePluginManager,
    )

    plugin = _Plugin("interrupt", [])
    manager = RuntimePluginManager(
        "native:test",
        (
            _binding(
                "plugin.interrupt",
                plugin,
                _hook(
                    "interrupt",
                    RuntimeHookPoint.INTERRUPT,
                    readable_fields=("reason",),
                ),
            ),
        ),
        diagnostic_emitter=lambda diagnostic: None,
    )

    with pytest.raises(RuntimePluginContractError, match="运行中的事件循环"):
        manager.dispatch_nowait(
            RuntimeHookPoint.INTERRUPT,
            {"reason": "同步边界"},
            protected_invariants=_invariants(),
        )
