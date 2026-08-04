"""受管 Runtime Plugin 与异步 Hook 生命周期。

Plugin 只能由组合根显式绑定，不能扫描目录、动态导入或覆盖同名实现。每个 Hook
必须声明切点、顺序、超时、失败策略以及可读／可修改字段；调用时只会收到深度
只读投影。身份、ToolPlan、Permission、Prompt Runtime 与 Event Ledger 始终作为
宿主侧受保护不变量保存，不会暴露给 Hook，也不能通过返回值覆盖。
"""

from __future__ import annotations

import asyncio
import inspect
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields as dataclass_fields, is_dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from core.runtime.extensions import RuntimeFailurePolicy


_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


PROTECTED_RUNTIME_HOOK_INVARIANTS = (
    "identity",
    "tool_plan",
    "permission",
    "prompt_runtime",
    "event_ledger",
)


class RuntimeHookPoint(StrEnum):
    PRE_MODEL = "pre_model"
    POST_MODEL = "post_model"
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"
    EVENT = "event"
    INTERRUPT = "interrupt"
    COMPLETION = "completion"


class RuntimePluginState(StrEnum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class RuntimePluginFailureCode(StrEnum):
    LOAD_FAILED = "load_failed"
    UNLOAD_FAILED = "unload_failed"
    TIMED_OUT = "timed_out"
    EXECUTION_FAILED = "execution_failed"
    INVALID_RETURN = "invalid_return"
    UNDECLARED_FIELD = "undeclared_field"
    CONTRACT_VIOLATION = "contract_violation"


class RuntimePluginRegistryError(ValueError):
    """Plugin 或 Hook 声明存在冲突。"""


class RuntimePluginContractError(RuntimeError):
    """Plugin 输入或输出违反受管合同。"""


class RuntimePluginExecutionError(RuntimeError):
    """必需 Plugin 或 fail-closed Hook 的类型化失败。"""

    def __init__(
        self,
        *,
        plugin_id: str,
        hook_id: str,
        point: str,
        code: RuntimePluginFailureCode,
        error_type: str,
    ) -> None:
        self.plugin_id = plugin_id
        self.hook_id = hook_id
        self.point = point
        self.code = code
        self.error_type = error_type
        super().__init__(
            f"runtime_plugin_failed:{plugin_id}:{hook_id}:{point}:{code.value}"
        )


def _required_identifier(value: object, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} 不是合法标识符：{normalized!r}")
    return normalized


def _field_names(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(
        _required_identifier(value, field_name) for value in values
    )
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} 不能重复")
    return normalized


_POINT_READABLE_FIELDS: Mapping[RuntimeHookPoint, frozenset[str]] = {
    RuntimeHookPoint.PRE_MODEL: frozenset({
        "messages",
        "model",
        "model_step",
        "runtime_id",
        "stream",
        "tools",
    }),
    RuntimeHookPoint.POST_MODEL: frozenset({
        "model",
        "model_step",
        "response",
        "runtime_id",
        "stream",
        "tool_calls",
        "usage",
    }),
    RuntimeHookPoint.PRE_TOOL: frozenset({
        "arguments",
        "model_step",
        "runtime_id",
        "tool_call_id",
        "tool_name",
        "tool_round",
    }),
    RuntimeHookPoint.POST_TOOL: frozenset({
        "arguments",
        "error_code",
        "output",
        "runtime_id",
        "status",
        "tool_call_id",
        "tool_name",
    }),
    RuntimeHookPoint.EVENT: frozenset({
        "direction",
        "event",
        "runtime_id",
    }),
    RuntimeHookPoint.INTERRUPT: frozenset({
        "reason",
        "runtime_id",
    }),
    RuntimeHookPoint.COMPLETION: frozenset({
        "message_count",
        "result",
        "runtime_id",
        "tool_call_count",
    }),
}


_POINT_MUTABLE_FIELDS: Mapping[RuntimeHookPoint, frozenset[str]] = {
    RuntimeHookPoint.PRE_MODEL: frozenset(),
    RuntimeHookPoint.POST_MODEL: frozenset(),
    RuntimeHookPoint.PRE_TOOL: frozenset({"arguments"}),
    RuntimeHookPoint.POST_TOOL: frozenset({"output"}),
    RuntimeHookPoint.EVENT: frozenset(),
    RuntimeHookPoint.INTERRUPT: frozenset(),
    RuntimeHookPoint.COMPLETION: frozenset(),
}


@dataclass(frozen=True, slots=True)
class RuntimePluginHookDescriptor:
    """一个 Plugin Hook 的不可变能力声明。"""

    hook_id: str
    point: RuntimeHookPoint
    order: int
    timeout_seconds: float
    failure_policy: RuntimeFailurePolicy
    readable_fields: tuple[str, ...]
    mutable_fields: tuple[str, ...] = ()
    trusted_builtin: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "hook_id",
            _required_identifier(self.hook_id, "hook_id"),
        )
        try:
            point = RuntimeHookPoint(self.point)
        except ValueError as exc:
            raise ValueError("Hook point 无效") from exc
        object.__setattr__(self, "point", point)
        if type(self.order) is not int or not -10_000 <= self.order <= 10_000:
            raise ValueError("Hook order 必须是 -10000 到 10000 的整数")
        timeout = self.timeout_seconds
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(float(timeout))
            or not 0.001 <= float(timeout) <= 60.0
        ):
            raise ValueError("Hook timeout_seconds 必须在 0.001 到 60 秒之间")
        object.__setattr__(self, "timeout_seconds", float(timeout))
        if not isinstance(self.failure_policy, RuntimeFailurePolicy):
            raise ValueError("Hook failure_policy 必须是 RuntimeFailurePolicy")
        readable = _field_names(self.readable_fields, "hook.readable_field")
        mutable = _field_names(self.mutable_fields, "hook.mutable_field")
        if not readable:
            raise ValueError("Hook 至少声明一个可读字段")
        if not set(readable) <= _POINT_READABLE_FIELDS[point]:
            invalid = sorted(set(readable) - _POINT_READABLE_FIELDS[point])
            raise ValueError(f"Hook 声明了切点不支持的可读字段：{invalid}")
        if not set(mutable) <= set(readable):
            raise ValueError("Hook 可修改字段必须同时声明为可读")
        if not set(mutable) <= _POINT_MUTABLE_FIELDS[point]:
            invalid = sorted(set(mutable) - _POINT_MUTABLE_FIELDS[point])
            raise ValueError(f"Hook 声明了切点不支持的可修改字段：{invalid}")
        if set(readable) & set(PROTECTED_RUNTIME_HOOK_INVARIANTS):
            raise ValueError("Hook 不能读取宿主受保护不变量")
        if mutable and not self.trusted_builtin:
            raise ValueError("可修改 Hook 只能绑定受信内建实现")
        if point in {RuntimeHookPoint.EVENT, RuntimeHookPoint.INTERRUPT} and (
            self.failure_policy is not RuntimeFailurePolicy.FAIL_OPEN
        ):
            raise ValueError("Event 与 Interrupt Hook 必须 fail open")
        if type(self.trusted_builtin) is not bool:
            raise ValueError("Hook trusted_builtin 必须是 bool")
        object.__setattr__(self, "readable_fields", readable)
        object.__setattr__(self, "mutable_fields", mutable)


@dataclass(frozen=True, slots=True)
class RuntimePluginDescriptor:
    """一个显式绑定 Plugin 的版本和生命周期声明。"""

    plugin_id: str
    version: str
    order: int
    required: bool
    lifecycle_timeout_seconds: float
    hooks: tuple[RuntimePluginHookDescriptor, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "plugin_id",
            _required_identifier(self.plugin_id, "plugin_id"),
        )
        version = str(self.version or "").strip()
        if not _SEMVER_PATTERN.fullmatch(version):
            raise ValueError("Plugin version 必须是 SemVer")
        object.__setattr__(self, "version", version)
        if type(self.order) is not int or not -10_000 <= self.order <= 10_000:
            raise ValueError("Plugin order 必须是 -10000 到 10000 的整数")
        if type(self.required) is not bool:
            raise ValueError("Plugin required 必须是 bool")
        timeout = self.lifecycle_timeout_seconds
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(float(timeout))
            or not 0.001 <= float(timeout) <= 60.0
        ):
            raise ValueError("Plugin lifecycle_timeout_seconds 无效")
        object.__setattr__(self, "lifecycle_timeout_seconds", float(timeout))
        hooks = tuple(self.hooks)
        if not hooks:
            raise ValueError("Plugin 至少声明一个 Hook")
        if any(not isinstance(item, RuntimePluginHookDescriptor) for item in hooks):
            raise TypeError("Plugin hooks 包含无效 Descriptor")
        hook_ids = [item.hook_id for item in hooks]
        if len(hook_ids) != len(set(hook_ids)):
            raise ValueError("同一 Plugin 的 hook_id 不能重复")
        object.__setattr__(
            self,
            "hooks",
            tuple(sorted(hooks, key=lambda item: (item.order, item.hook_id))),
        )


@dataclass(frozen=True, slots=True)
class RuntimeHookInvocation:
    plugin_id: str
    hook_id: str
    point: RuntimeHookPoint
    runtime_id: str
    fields: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fields",
            _freeze_projection_mapping(self.fields),
        )


@dataclass(frozen=True, slots=True)
class RuntimeHookPatch:
    updates: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "updates", _freeze_mapping(self.updates))


@runtime_checkable
class RuntimePlugin(Protocol):
    async def on_load(self, context: Mapping[str, object]) -> None: ...

    async def on_unload(self) -> None: ...

    async def invoke(
        self,
        invocation: RuntimeHookInvocation,
    ) -> RuntimeHookPatch | None: ...


@dataclass(frozen=True, slots=True)
class RuntimePluginBinding:
    descriptor: RuntimePluginDescriptor
    plugin: RuntimePlugin

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, RuntimePluginDescriptor):
            raise TypeError("descriptor 必须是 RuntimePluginDescriptor")
        for method_name in ("on_load", "on_unload", "invoke"):
            method = getattr(self.plugin, method_name, None)
            if not callable(method) or not inspect.iscoroutinefunction(method):
                raise TypeError(f"Plugin {method_name} 必须是 async 方法")


@dataclass(frozen=True, slots=True)
class RuntimePluginFailure:
    plugin_id: str
    hook_id: str
    point: str
    code: RuntimePluginFailureCode
    error_type: str
    failure_policy: RuntimeFailurePolicy


@dataclass(frozen=True, slots=True)
class RuntimePluginDiagnostic:
    runtime_id: str
    failure: RuntimePluginFailure
    timeout_ms: int


class RuntimePluginDiagnosticEmitter(Protocol):
    def __call__(self, diagnostic: RuntimePluginDiagnostic) -> None: ...


@dataclass(frozen=True, slots=True)
class RuntimeHookDispatchResult:
    fields: Mapping[str, object]
    applied_hook_ids: tuple[str, ...]
    failures: tuple[RuntimePluginFailure, ...]
    protected_invariants: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _freeze_mapping(self.fields))
        object.__setattr__(
            self,
            "protected_invariants",
            _freeze_mapping(self.protected_invariants),
        )


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_value(item) for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    return value


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({
        str(key): _freeze_value(item) for key, item in value.items()
    })


def _freeze_projection_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return MappingProxyType({
            item.name: _freeze_projection_value(getattr(value, item.name))
            for item in dataclass_fields(value)
        })
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_projection_value(item)
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_projection_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_projection_value(item) for item in value)
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    return value


def _freeze_projection_mapping(
    value: Mapping[str, object],
) -> Mapping[str, object]:
    return MappingProxyType({
        str(key): _freeze_projection_value(item)
        for key, item in value.items()
    })


def thaw_runtime_hook_value(value: object) -> object:
    """把 Hook 返回的只读投影复制回宿主可消费的普通容器。"""

    if isinstance(value, Mapping):
        return {
            str(key): thaw_runtime_hook_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [thaw_runtime_hook_value(item) for item in value]
    if isinstance(value, frozenset):
        return {
            thaw_runtime_hook_value(item) for item in value
        }
    return value


def emit_runtime_plugin_diagnostic(
    diagnostic: RuntimePluginDiagnostic,
) -> None:
    """把 Plugin 异常投递到统一 RuntimeEvent 与权威 Ledger。"""

    from core.runtime.event_bus import emit_runtime_event

    failure = diagnostic.failure
    emit_runtime_event(
        "agent.plugin_hook",
        "failed",
        attributes={
            "runtime_id": diagnostic.runtime_id,
            "plugin_id": failure.plugin_id,
            "hook_id": failure.hook_id,
            "hook_point": failure.point,
            "failure_code": failure.code.value,
            "failure_policy": failure.failure_policy.value,
            "error_type": failure.error_type,
            "timeout_ms": diagnostic.timeout_ms,
        },
    )


class RuntimePluginManager:
    """启动期冻结、按声明顺序执行的请求级 Plugin Manager。"""

    def __init__(
        self,
        runtime_id: str,
        bindings: Sequence[RuntimePluginBinding] = (),
        *,
        diagnostic_emitter: RuntimePluginDiagnosticEmitter | None = None,
    ) -> None:
        self._runtime_id = str(runtime_id or "").strip()
        if not self._runtime_id:
            raise ValueError("runtime_id 不能为空")
        normalized = tuple(bindings)
        if any(not isinstance(item, RuntimePluginBinding) for item in normalized):
            raise TypeError("bindings 包含无效 RuntimePluginBinding")
        ids = [item.descriptor.plugin_id for item in normalized]
        if len(ids) != len(set(ids)):
            raise RuntimePluginRegistryError("Plugin ID 重复")
        self._bindings = tuple(sorted(
            normalized,
            key=lambda item: (item.descriptor.order, item.descriptor.plugin_id),
        ))
        hook_keys: set[tuple[str, str]] = set()
        for binding in self._bindings:
            for hook in binding.descriptor.hooks:
                key = (binding.descriptor.plugin_id, hook.hook_id)
                if key in hook_keys:
                    raise RuntimePluginRegistryError("Plugin Hook 重复绑定")
                hook_keys.add(key)
        self._diagnostic_emitter = (
            diagnostic_emitter or emit_runtime_plugin_diagnostic
        )
        self._state = RuntimePluginState.NEW
        self._active_plugin_ids: set[str] = set()
        self._background_tasks: set[asyncio.Task[object]] = set()

    @property
    def runtime_id(self) -> str:
        return self._runtime_id

    @property
    def state(self) -> RuntimePluginState:
        return self._state

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        return tuple(
            binding.descriptor.plugin_id for binding in self._bindings
        )

    def has_hooks(self, point: RuntimeHookPoint | None = None) -> bool:
        return any(
            point is None or hook.point is RuntimeHookPoint(point)
            for binding in self._bindings
            for hook in binding.descriptor.hooks
        )

    def _failure(
        self,
        *,
        binding: RuntimePluginBinding,
        hook_id: str,
        point: str,
        code: RuntimePluginFailureCode,
        error_type: str,
        failure_policy: RuntimeFailurePolicy,
        timeout_seconds: float,
    ) -> RuntimePluginFailure:
        failure = RuntimePluginFailure(
            plugin_id=binding.descriptor.plugin_id,
            hook_id=hook_id,
            point=point,
            code=code,
            error_type=error_type,
            failure_policy=failure_policy,
        )
        self._diagnostic_emitter(RuntimePluginDiagnostic(
            runtime_id=self.runtime_id,
            failure=failure,
            timeout_ms=max(1, round(timeout_seconds * 1000)),
        ))
        return failure

    @staticmethod
    def _raise_failure(failure: RuntimePluginFailure) -> None:
        raise RuntimePluginExecutionError(
            plugin_id=failure.plugin_id,
            hook_id=failure.hook_id,
            point=failure.point,
            code=failure.code,
            error_type=failure.error_type,
        )

    async def start(self) -> None:
        if self._state is RuntimePluginState.RUNNING:
            return
        if self._state is not RuntimePluginState.NEW:
            raise RuntimePluginContractError(
                f"Plugin Manager 无法从 {self._state.value} 启动"
            )
        self._state = RuntimePluginState.STARTING
        context = _freeze_mapping({"runtime_id": self.runtime_id})
        try:
            for binding in self._bindings:
                descriptor = binding.descriptor
                try:
                    async with asyncio.timeout(
                        descriptor.lifecycle_timeout_seconds
                    ):
                        await binding.plugin.on_load(context)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    code = (
                        RuntimePluginFailureCode.TIMED_OUT
                        if isinstance(exc, TimeoutError)
                        else RuntimePluginFailureCode.LOAD_FAILED
                    )
                    failure = self._failure(
                        binding=binding,
                        hook_id="lifecycle.load",
                        point="load",
                        code=code,
                        error_type=type(exc).__name__,
                        failure_policy=(
                            RuntimeFailurePolicy.FAIL_CLOSED
                            if descriptor.required
                            else RuntimeFailurePolicy.FAIL_OPEN
                        ),
                        timeout_seconds=descriptor.lifecycle_timeout_seconds,
                    )
                    if descriptor.required:
                        self._raise_failure(failure)
                    continue
                self._active_plugin_ids.add(descriptor.plugin_id)
        except BaseException:
            self._state = RuntimePluginState.FAILED
            await self._unload_active(suppress_failures=True)
            raise
        self._state = RuntimePluginState.RUNNING

    async def _unload_active(self, *, suppress_failures: bool) -> None:
        first_failure: RuntimePluginFailure | None = None
        for binding in reversed(self._bindings):
            descriptor = binding.descriptor
            if descriptor.plugin_id not in self._active_plugin_ids:
                continue
            try:
                async with asyncio.timeout(
                    descriptor.lifecycle_timeout_seconds
                ):
                    await binding.plugin.on_unload()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                code = (
                    RuntimePluginFailureCode.TIMED_OUT
                    if isinstance(exc, TimeoutError)
                    else RuntimePluginFailureCode.UNLOAD_FAILED
                )
                failure = self._failure(
                    binding=binding,
                    hook_id="lifecycle.unload",
                    point="unload",
                    code=code,
                    error_type=type(exc).__name__,
                    failure_policy=(
                        RuntimeFailurePolicy.FAIL_CLOSED
                        if descriptor.required
                        else RuntimeFailurePolicy.FAIL_OPEN
                    ),
                    timeout_seconds=descriptor.lifecycle_timeout_seconds,
                )
                if descriptor.required and first_failure is None:
                    first_failure = failure
            finally:
                self._active_plugin_ids.discard(descriptor.plugin_id)
        if first_failure is not None and not suppress_failures:
            self._raise_failure(first_failure)

    async def stop(self) -> None:
        if self._state is RuntimePluginState.STOPPED:
            return
        if self._state is RuntimePluginState.NEW:
            self._state = RuntimePluginState.STOPPED
            return
        if self._state not in {
            RuntimePluginState.RUNNING,
            RuntimePluginState.FAILED,
        }:
            raise RuntimePluginContractError(
                f"Plugin Manager 无法从 {self._state.value} 停止"
            )
        self._state = RuntimePluginState.STOPPING
        first_error: BaseException | None = None
        try:
            await self.drain_background_tasks()
        except BaseException as exc:
            first_error = exc
        try:
            await self._unload_active(suppress_failures=False)
        except BaseException as exc:
            if first_error is None:
                first_error = exc
        if first_error is not None:
            self._state = RuntimePluginState.FAILED
            raise first_error
        self._state = RuntimePluginState.STOPPED

    def _ordered_hooks(
        self,
        point: RuntimeHookPoint,
    ) -> tuple[tuple[RuntimePluginBinding, RuntimePluginHookDescriptor], ...]:
        pairs = [
            (binding, hook)
            for binding in self._bindings
            if binding.descriptor.plugin_id in self._active_plugin_ids
            for hook in binding.descriptor.hooks
            if hook.point is point
        ]
        return tuple(sorted(
            pairs,
            key=lambda pair: (
                pair[0].descriptor.order,
                pair[1].order,
                pair[0].descriptor.plugin_id,
                pair[1].hook_id,
            ),
        ))

    async def dispatch(
        self,
        point: RuntimeHookPoint,
        fields: Mapping[str, object],
        *,
        protected_invariants: Mapping[str, object],
        validate_fields: Callable[[Mapping[str, object]], None] | None = None,
    ) -> RuntimeHookDispatchResult:
        if self._state is not RuntimePluginState.RUNNING:
            raise RuntimePluginContractError("Plugin Manager 尚未运行")
        normalized_point = RuntimeHookPoint(point)
        current = dict(fields)
        unknown = sorted(set(current) - _POINT_READABLE_FIELDS[normalized_point])
        if unknown:
            raise RuntimePluginContractError(
                f"{normalized_point.value} 输入包含未知字段：{unknown}"
            )
        invariants = dict(protected_invariants)
        required_invariants = set(PROTECTED_RUNTIME_HOOK_INVARIANTS)
        if set(invariants) != required_invariants:
            missing = sorted(required_invariants - set(invariants))
            extra = sorted(set(invariants) - required_invariants)
            raise RuntimePluginContractError(
                f"Hook 受保护不变量不完整：missing={missing}, extra={extra}"
            )
        applied: list[str] = []
        failures: list[RuntimePluginFailure] = []
        for binding, hook in self._ordered_hooks(normalized_point):
            missing_fields = sorted(set(hook.readable_fields) - set(current))
            if missing_fields:
                raise RuntimePluginContractError(
                    f"Hook {hook.hook_id} 缺少声明输入：{missing_fields}"
                )
            invocation = RuntimeHookInvocation(
                plugin_id=binding.descriptor.plugin_id,
                hook_id=hook.hook_id,
                point=normalized_point,
                runtime_id=self.runtime_id,
                fields={name: current[name] for name in hook.readable_fields},
            )
            try:
                async with asyncio.timeout(hook.timeout_seconds):
                    patch = await binding.plugin.invoke(invocation)
                if patch is not None and not isinstance(patch, RuntimeHookPatch):
                    raise _InvalidHookReturn(type(patch).__name__)
                updates = dict(patch.updates) if patch is not None else {}
                undeclared = sorted(set(updates) - set(hook.mutable_fields))
                if undeclared:
                    raise _UndeclaredHookField(undeclared)
                candidate = {**current, **updates}
                if updates and validate_fields is not None:
                    try:
                        validate_fields(candidate)
                    except Exception as exc:
                        raise _HookContractViolation(
                            type(exc).__name__
                        ) from exc
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, TimeoutError):
                    code = RuntimePluginFailureCode.TIMED_OUT
                elif isinstance(exc, _InvalidHookReturn):
                    code = RuntimePluginFailureCode.INVALID_RETURN
                elif isinstance(exc, _UndeclaredHookField):
                    code = RuntimePluginFailureCode.UNDECLARED_FIELD
                elif isinstance(exc, _HookContractViolation):
                    code = RuntimePluginFailureCode.CONTRACT_VIOLATION
                else:
                    code = RuntimePluginFailureCode.EXECUTION_FAILED
                failure = self._failure(
                    binding=binding,
                    hook_id=hook.hook_id,
                    point=hook.point.value,
                    code=code,
                    error_type=(
                        exc.error_type
                        if isinstance(exc, _HookContractViolation)
                        else type(exc).__name__
                    ),
                    failure_policy=hook.failure_policy,
                    timeout_seconds=hook.timeout_seconds,
                )
                failures.append(failure)
                if hook.failure_policy is RuntimeFailurePolicy.FAIL_CLOSED:
                    self._raise_failure(failure)
                continue
            current = candidate
            applied.append(f"{binding.descriptor.plugin_id}:{hook.hook_id}")
        return RuntimeHookDispatchResult(
            fields=current,
            applied_hook_ids=tuple(applied),
            failures=tuple(failures),
            protected_invariants=invariants,
        )

    def dispatch_nowait(
        self,
        point: RuntimeHookPoint,
        fields: Mapping[str, object],
        *,
        protected_invariants: Mapping[str, object],
    ) -> None:
        """为同步 interrupt 边界调度 fail-open Hook。"""

        if RuntimeHookPoint(point) is not RuntimeHookPoint.INTERRUPT:
            raise RuntimePluginContractError("只有 Interrupt Hook 可后台调度")
        if not self.has_hooks(RuntimeHookPoint.INTERRUPT):
            return

        async def invoke() -> object:
            return await self.dispatch(
                RuntimeHookPoint.INTERRUPT,
                fields,
                protected_invariants=protected_invariants,
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimePluginContractError(
                "Interrupt Hook 必须由运行中的事件循环调度"
            ) from exc
        task = loop.create_task(invoke())
        self._background_tasks.add(task)

    async def drain_background_tasks(self) -> None:
        tasks = tuple(self._background_tasks)
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.difference_update(tasks)
        first_failure = next(
            (
                result
                for result in results
                if isinstance(result, BaseException)
                and not isinstance(result, asyncio.CancelledError)
            ),
            None,
        )
        if first_failure is not None:
            raise first_failure


class _InvalidHookReturn(RuntimeError):
    pass


class _UndeclaredHookField(RuntimeError):
    def __init__(self, fields: Sequence[str]) -> None:
        self.fields = tuple(fields)
        super().__init__("Hook 返回了未声明字段")


class _HookContractViolation(RuntimeError):
    def __init__(self, error_type: str) -> None:
        self.error_type = str(error_type or "ContractError")
        super().__init__("Hook 返回值未通过宿主合同复验")


def build_runtime_plugin_manager(
    runtime_id: str,
    bindings: Sequence[RuntimePluginBinding] = (),
    *,
    diagnostic_emitter: RuntimePluginDiagnosticEmitter | None = None,
) -> RuntimePluginManager:
    """组合一个真实请求路径使用的冻结 Plugin Manager。"""

    return RuntimePluginManager(
        runtime_id,
        bindings,
        diagnostic_emitter=diagnostic_emitter,
    )


__all__ = [
    "PROTECTED_RUNTIME_HOOK_INVARIANTS",
    "RuntimeHookDispatchResult",
    "RuntimeHookInvocation",
    "RuntimeHookPatch",
    "RuntimeHookPoint",
    "RuntimePlugin",
    "RuntimePluginBinding",
    "RuntimePluginContractError",
    "RuntimePluginDescriptor",
    "RuntimePluginDiagnostic",
    "RuntimePluginDiagnosticEmitter",
    "RuntimePluginExecutionError",
    "RuntimePluginFailure",
    "RuntimePluginFailureCode",
    "RuntimePluginHookDescriptor",
    "RuntimePluginManager",
    "RuntimePluginRegistryError",
    "RuntimePluginState",
    "build_runtime_plugin_manager",
    "emit_runtime_plugin_diagnostic",
    "thaw_runtime_hook_value",
]
