"""内建 Observer、Transform 与 Policy 的类型化运行时合同。

本模块只提供启动期显式组合所需的最小合同，不执行目录扫描、动态导入、同名覆盖
或第三方代码加载。领域模块保留自己的 Descriptor 和业务校验；这里统一失败语义、
冻结顺序和受保护不变量。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import inspect
from types import MappingProxyType
from typing import Generic, Protocol, TypeVar, runtime_checkable

from core.registry import RegistryBuilder, RegistrySnapshot
from core.registry.validation import validate_identifier


PROTECTED_TRANSFORM_INVARIANTS = (
    "identity",
    "tool_plan",
    "trace_redaction",
    "sandbox_security",
)


class RuntimeExtensionKind(StrEnum):
    OBSERVER = "observer"
    TRANSFORM = "transform"
    POLICY = "policy"


# 保留 Hook 语境下更直接的公开名称，但使用同一个枚举事实源。
RuntimeHookKind = RuntimeExtensionKind


class RuntimeFailurePolicy(StrEnum):
    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


class RuntimeHookFailureCode(StrEnum):
    EXECUTION_FAILED = "execution_failed"
    INVALID_RETURN = "invalid_return"
    INVARIANT_OVERRIDE = "invariant_override"


class PolicyFailureCode(StrEnum):
    EVALUATION_FAILED = "evaluation_failed"
    INVALID_RESULT = "invalid_result"


class PolicyFailureOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class RuntimeHookRegistryError(ValueError):
    """Hook Descriptor 注册或冻结失败。"""


class RuntimeHookContractError(RuntimeError):
    """Hook 输入、输出或受保护不变量违反合同。"""


class RuntimeHookExecutionError(RuntimeError):
    """fail-closed Hook 的类型化执行失败。"""

    def __init__(
        self,
        *,
        hook_id: str,
        code: RuntimeHookFailureCode,
        error_type: str,
    ) -> None:
        self.hook_id = hook_id
        self.code = code
        self.error_type = error_type
        super().__init__(f"runtime_hook_failed:{hook_id}:{code.value}")


class PolicyRegistryError(ValueError):
    """Policy Descriptor 注册或冻结失败。"""


class PolicyExecutionError(RuntimeError):
    """Policy fallback 本身违反输出合同。"""


@dataclass(frozen=True, slots=True)
class RuntimeHookDescriptor:
    """Observer／Transform Hook 的不可变代码侧声明。"""

    hook_id: str
    kind: RuntimeExtensionKind
    owner_module: str
    domain: str
    input_contract: str
    output_contract: str
    priority: int
    failure_policy: RuntimeFailurePolicy
    trusted_builtin: bool
    protected_invariants: tuple[str, ...] = ()
    registry_namespace: str = field(
        default="runtime_hook",
        init=False,
    )
    registry_dependencies: tuple[str, ...] = field(
        default=(),
        init=False,
    )

    def __post_init__(self) -> None:
        validate_identifier(self.hook_id, field_name="hook_id")
        validate_identifier(
            self.owner_module,
            field_name="hook.owner_module",
        )
        validate_identifier(self.domain, field_name="hook.domain")
        validate_identifier(
            self.input_contract,
            field_name="hook.input_contract",
        )
        validate_identifier(
            self.output_contract,
            field_name="hook.output_contract",
        )
        if self.kind not in {
            RuntimeExtensionKind.OBSERVER,
            RuntimeExtensionKind.TRANSFORM,
        }:
            raise ValueError("Hook kind 只能是 observer 或 transform")
        if not isinstance(self.failure_policy, RuntimeFailurePolicy):
            raise ValueError("Hook failure_policy 必须是 RuntimeFailurePolicy")
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not 0 <= self.priority <= 1_000_000
        ):
            raise ValueError("Hook priority 必须是 0～1000000 的整数")
        if type(self.trusted_builtin) is not bool:
            raise ValueError("Hook trusted_builtin 必须是 bool")
        invariants = tuple(self.protected_invariants)
        if len(invariants) != len(set(invariants)):
            raise ValueError("Hook protected_invariants 不能重复")
        for invariant in invariants:
            validate_identifier(
                invariant,
                field_name="hook.protected_invariant",
            )
        object.__setattr__(self, "protected_invariants", invariants)

        if self.kind is RuntimeExtensionKind.OBSERVER:
            if self.output_contract != "none":
                raise ValueError("Observer output_contract 必须是 none")
            if invariants:
                raise ValueError("Observer 不声明 Transform 受保护不变量")
            return

        if not self.trusted_builtin:
            raise ValueError("Transform Hook 只能注册受信内建实现")
        missing = sorted(
            set(PROTECTED_TRANSFORM_INVARIANTS) - set(invariants)
        )
        if missing:
            raise ValueError(
                f"Transform Hook 缺少受保护不变量声明：{missing}"
            )

    @property
    def registry_id(self) -> str:
        return self.hook_id

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "kind": self.kind.value,
            "owner_module": self.owner_module,
            "domain": self.domain,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "priority": self.priority,
            "failure_policy": self.failure_policy.value,
            "trusted_builtin": self.trusted_builtin,
            "protected_invariants": list(self.protected_invariants),
        }


@dataclass(frozen=True, slots=True)
class PolicyDescriptor:
    """同步确定性 Policy 的代码侧声明。"""

    policy_id: str
    owner_module: str
    domain: str
    input_contract: str
    output_contract: str
    failure_policy: RuntimeFailurePolicy
    security_sensitive: bool
    kind: RuntimeExtensionKind = field(
        default=RuntimeExtensionKind.POLICY,
        init=False,
    )
    registry_namespace: str = field(
        default="runtime_policy",
        init=False,
    )
    registry_dependencies: tuple[str, ...] = field(
        default=(),
        init=False,
    )

    def __post_init__(self) -> None:
        validate_identifier(self.policy_id, field_name="policy_id")
        validate_identifier(
            self.owner_module,
            field_name="policy.owner_module",
        )
        validate_identifier(self.domain, field_name="policy.domain")
        validate_identifier(
            self.input_contract,
            field_name="policy.input_contract",
        )
        validate_identifier(
            self.output_contract,
            field_name="policy.output_contract",
        )
        if not isinstance(self.failure_policy, RuntimeFailurePolicy):
            raise ValueError("Policy failure_policy 必须是 RuntimeFailurePolicy")
        if type(self.security_sensitive) is not bool:
            raise ValueError("Policy security_sensitive 必须是 bool")
        if (
            self.security_sensitive
            and self.failure_policy is not RuntimeFailurePolicy.FAIL_CLOSED
        ):
            raise ValueError("安全 Policy 必须 fail closed")

    @property
    def registry_id(self) -> str:
        return self.policy_id

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "kind": self.kind.value,
            "owner_module": self.owner_module,
            "domain": self.domain,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "failure_policy": self.failure_policy.value,
            "security_sensitive": self.security_sensitive,
        }


class RuntimeHookRegistry:
    """只在组合期注册、服务期冻结的 Hook Registry。"""

    def __init__(
        self,
        descriptors: Sequence[RuntimeHookDescriptor] = (),
    ) -> None:
        self._descriptors: dict[str, RuntimeHookDescriptor] = {}
        self._snapshot: RegistrySnapshot[RuntimeHookDescriptor] | None = None
        for descriptor in descriptors:
            self.register(descriptor)

    @property
    def frozen(self) -> bool:
        return self._snapshot is not None

    @property
    def registry_snapshot(self) -> RegistrySnapshot[RuntimeHookDescriptor]:
        if self._snapshot is None:
            raise RuntimeHookRegistryError("Runtime Hook Registry 尚未冻结")
        return self._snapshot

    def register(self, descriptor: RuntimeHookDescriptor) -> None:
        if self._snapshot is not None:
            raise RuntimeHookRegistryError("Runtime Hook Registry 已冻结")
        if not isinstance(descriptor, RuntimeHookDescriptor):
            raise TypeError("descriptor 必须是 RuntimeHookDescriptor")
        if descriptor.hook_id in self._descriptors:
            raise RuntimeHookRegistryError(
                f"Runtime Hook 重复注册：{descriptor.hook_id}"
            )
        self._descriptors[descriptor.hook_id] = descriptor

    def freeze(
        self,
        *,
        generation: int = 1,
    ) -> "RuntimeHookRegistry":
        if self._snapshot is not None:
            return self
        builder = RegistryBuilder[RuntimeHookDescriptor]("runtime_hook")
        for hook_id in sorted(self._descriptors):
            builder.register(self._descriptors[hook_id])
        self._snapshot = builder.freeze(generation=generation)
        return self

    def ordered(
        self,
        kind: RuntimeExtensionKind | None = None,
    ) -> tuple[RuntimeHookDescriptor, ...]:
        descriptors = tuple(self.registry_snapshot)
        if kind is not None:
            descriptors = tuple(
                descriptor
                for descriptor in descriptors
                if descriptor.kind is kind
            )
        return tuple(sorted(
            descriptors,
            key=lambda descriptor: (
                descriptor.priority,
                descriptor.hook_id,
            ),
        ))


class PolicyRegistry:
    """只在组合期注册、服务期冻结的 Policy Registry。"""

    def __init__(
        self,
        descriptors: Sequence[PolicyDescriptor] = (),
    ) -> None:
        self._descriptors: dict[str, PolicyDescriptor] = {}
        self._snapshot: RegistrySnapshot[PolicyDescriptor] | None = None
        for descriptor in descriptors:
            self.register(descriptor)

    @property
    def frozen(self) -> bool:
        return self._snapshot is not None

    @property
    def registry_snapshot(self) -> RegistrySnapshot[PolicyDescriptor]:
        if self._snapshot is None:
            raise PolicyRegistryError("Policy Registry 尚未冻结")
        return self._snapshot

    def register(self, descriptor: PolicyDescriptor) -> None:
        if self._snapshot is not None:
            raise PolicyRegistryError("Policy Registry 已冻结")
        if not isinstance(descriptor, PolicyDescriptor):
            raise TypeError("descriptor 必须是 PolicyDescriptor")
        if descriptor.policy_id in self._descriptors:
            raise PolicyRegistryError(
                f"Policy 重复注册：{descriptor.policy_id}"
            )
        self._descriptors[descriptor.policy_id] = descriptor

    def freeze(
        self,
        *,
        generation: int = 1,
    ) -> "PolicyRegistry":
        if self._snapshot is not None:
            return self
        builder = RegistryBuilder[PolicyDescriptor]("runtime_policy")
        for policy_id in sorted(self._descriptors):
            builder.register(self._descriptors[policy_id])
        self._snapshot = builder.freeze(generation=generation)
        return self


@runtime_checkable
class RuntimeObserverHook(Protocol):
    def observe(self, event: object) -> object | None: ...


@runtime_checkable
class RuntimeTransformHook(Protocol):
    def transform(self, value: object) -> object: ...


@dataclass(frozen=True, slots=True)
class RuntimeObserverBinding:
    descriptor: RuntimeHookDescriptor
    observer: RuntimeObserverHook

    def __post_init__(self) -> None:
        if self.descriptor.kind is not RuntimeExtensionKind.OBSERVER:
            raise ValueError("RuntimeObserverBinding 只接受 Observer Descriptor")
        if not isinstance(self.observer, RuntimeObserverHook):
            raise TypeError("observer 不满足 RuntimeObserverHook")


@dataclass(frozen=True, slots=True)
class RuntimeTransformBinding:
    descriptor: RuntimeHookDescriptor
    transform_hook: RuntimeTransformHook

    def __post_init__(self) -> None:
        if self.descriptor.kind is not RuntimeExtensionKind.TRANSFORM:
            raise ValueError("RuntimeTransformBinding 只接受 Transform Descriptor")
        if not isinstance(self.transform_hook, RuntimeTransformHook):
            raise TypeError("transform_hook 不满足 RuntimeTransformHook")


@dataclass(frozen=True, slots=True)
class RuntimeHookFailure:
    hook_id: str
    code: RuntimeHookFailureCode
    error_type: str


@dataclass(frozen=True, slots=True)
class ObserverDispatchReport:
    observed_hook_ids: tuple[str, ...]
    failures: tuple[RuntimeHookFailure, ...]

    @property
    def failure_ids(self) -> tuple[str, ...]:
        return tuple(failure.hook_id for failure in self.failures)


@dataclass(frozen=True, slots=True)
class TransformDispatchResult:
    value: object
    applied_hook_ids: tuple[str, ...]
    failures: tuple[RuntimeHookFailure, ...]
    protected_invariants: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "protected_invariants",
            MappingProxyType(dict(self.protected_invariants)),
        )


def _hook_failure(
    descriptor: RuntimeHookDescriptor,
    *,
    code: RuntimeHookFailureCode,
    error_type: str,
) -> RuntimeHookFailure:
    return RuntimeHookFailure(
        hook_id=descriptor.hook_id,
        code=code,
        error_type=error_type,
    )


def _handle_hook_failure(
    descriptor: RuntimeHookDescriptor,
    failure: RuntimeHookFailure,
) -> None:
    if descriptor.failure_policy is RuntimeFailurePolicy.FAIL_CLOSED:
        raise RuntimeHookExecutionError(
            hook_id=failure.hook_id,
            code=failure.code,
            error_type=failure.error_type,
        )


class RuntimeObserverDispatcher:
    """按显式 priority 只读投递 Observer；默认故障隔离。"""

    def __init__(
        self,
        bindings: Sequence[RuntimeObserverBinding] = (),
    ) -> None:
        self._bindings = {
            binding.descriptor.hook_id: binding
            for binding in bindings
        }
        if len(self._bindings) != len(bindings):
            raise RuntimeHookRegistryError("Observer Hook 重复绑定")
        self._registry = RuntimeHookRegistry(tuple(
            binding.descriptor
            for binding in bindings
        )).freeze()
        self._ordered_ids = tuple(
            descriptor.hook_id
            for descriptor in self._registry.ordered(
                RuntimeExtensionKind.OBSERVER
            )
        )

    @property
    def registry(self) -> RuntimeHookRegistry:
        return self._registry

    @property
    def ordered_ids(self) -> tuple[str, ...]:
        return self._ordered_ids

    def dispatch(self, event: object) -> ObserverDispatchReport:
        observed: list[str] = []
        failures: list[RuntimeHookFailure] = []
        for hook_id in self._ordered_ids:
            binding = self._bindings[hook_id]
            descriptor = binding.descriptor
            try:
                result = binding.observer.observe(event)
                if result is not None:
                    failure = _hook_failure(
                        descriptor,
                        code=RuntimeHookFailureCode.INVALID_RETURN,
                        error_type=type(result).__name__,
                    )
                    _handle_hook_failure(descriptor, failure)
                    failures.append(failure)
                    continue
            except RuntimeHookExecutionError:
                raise
            except Exception as exc:
                failure = _hook_failure(
                    descriptor,
                    code=RuntimeHookFailureCode.EXECUTION_FAILED,
                    error_type=type(exc).__name__,
                )
                _handle_hook_failure(descriptor, failure)
                failures.append(failure)
                continue
            observed.append(hook_id)
        return ObserverDispatchReport(
            observed_hook_ids=tuple(observed),
            failures=tuple(failures),
        )


class RuntimeTransformDispatcher:
    """顺序执行受信内建 Transform，不把安全不变量暴露给 Hook。"""

    def __init__(
        self,
        bindings: Sequence[RuntimeTransformBinding] = (),
    ) -> None:
        self._bindings = {
            binding.descriptor.hook_id: binding
            for binding in bindings
        }
        if len(self._bindings) != len(bindings):
            raise RuntimeHookRegistryError("Transform Hook 重复绑定")
        self._registry = RuntimeHookRegistry(tuple(
            binding.descriptor
            for binding in bindings
        )).freeze()
        ordered = self._registry.ordered(RuntimeExtensionKind.TRANSFORM)
        contracts = {
            (
                descriptor.input_contract,
                descriptor.output_contract,
            )
            for descriptor in ordered
        }
        if len(contracts) > 1:
            raise RuntimeHookContractError(
                "同一 Transform Pipeline 的输入／输出 Contract 必须一致"
            )
        self._ordered_ids = tuple(
            descriptor.hook_id
            for descriptor in ordered
        )

    @property
    def registry(self) -> RuntimeHookRegistry:
        return self._registry

    @property
    def ordered_ids(self) -> tuple[str, ...]:
        return self._ordered_ids

    def transform(
        self,
        value: object,
        *,
        protected_invariants: Mapping[str, object],
    ) -> TransformDispatchResult:
        fixed = MappingProxyType(dict(protected_invariants))
        required = {
            invariant
            for hook_id in self._ordered_ids
            for invariant in self._bindings[
                hook_id
            ].descriptor.protected_invariants
        }
        missing = sorted(required - set(fixed))
        if missing:
            raise RuntimeHookContractError(
                f"Transform Pipeline 缺少受保护不变量：{missing}"
            )

        current = value
        applied: list[str] = []
        failures: list[RuntimeHookFailure] = []
        for hook_id in self._ordered_ids:
            binding = self._bindings[hook_id]
            descriptor = binding.descriptor
            try:
                candidate = binding.transform_hook.transform(current)
                if candidate is None:
                    failure = _hook_failure(
                        descriptor,
                        code=RuntimeHookFailureCode.INVALID_RETURN,
                        error_type="NoneType",
                    )
                    _handle_hook_failure(descriptor, failure)
                    failures.append(failure)
                    continue
                if isinstance(candidate, Mapping):
                    overridden = sorted(
                        set(candidate) & set(
                            descriptor.protected_invariants
                        )
                    )
                    if overridden:
                        failure = _hook_failure(
                            descriptor,
                            code=(
                                RuntimeHookFailureCode.INVARIANT_OVERRIDE
                            ),
                            error_type="ProtectedInvariantOverride",
                        )
                        _handle_hook_failure(descriptor, failure)
                        failures.append(failure)
                        continue
            except RuntimeHookExecutionError:
                raise
            except Exception as exc:
                failure = _hook_failure(
                    descriptor,
                    code=RuntimeHookFailureCode.EXECUTION_FAILED,
                    error_type=type(exc).__name__,
                )
                _handle_hook_failure(descriptor, failure)
                failures.append(failure)
                continue
            current = candidate
            applied.append(hook_id)
        return TransformDispatchResult(
            value=current,
            applied_hook_ids=tuple(applied),
            failures=tuple(failures),
            protected_invariants=fixed,
        )


@dataclass(frozen=True, slots=True)
class PolicyTypedFailure:
    policy_id: str
    code: PolicyFailureCode
    fallback_outcome: PolicyFailureOutcome
    error_type: str


PolicyValueT = TypeVar("PolicyValueT")


@dataclass(frozen=True, slots=True)
class PolicyExecutionResult(Generic[PolicyValueT]):
    value: PolicyValueT
    failure: PolicyTypedFailure | None = None

    @property
    def used_fallback(self) -> bool:
        return self.failure is not None


class _InvalidPolicyResult(RuntimeError):
    pass


def execute_policy(
    descriptor: PolicyDescriptor,
    evaluator: Callable[[], PolicyValueT],
    *,
    fallback: Callable[[PolicyTypedFailure], PolicyValueT],
) -> PolicyExecutionResult[PolicyValueT]:
    """同步执行 Policy，并把异常收敛为显式 allow／deny fallback。"""

    if not isinstance(descriptor, PolicyDescriptor):
        raise TypeError("descriptor 必须是 PolicyDescriptor")
    if not callable(evaluator) or not callable(fallback):
        raise TypeError("evaluator 和 fallback 必须可调用")
    try:
        value = evaluator()
        if inspect.isawaitable(value):
            close = getattr(value, "close", None)
            if callable(close):
                close()
            raise _InvalidPolicyResult("Policy 不允许返回 awaitable")
        if value is None:
            raise _InvalidPolicyResult("Policy 不允许以 None 表达状态")
        return PolicyExecutionResult(value=value)
    except Exception as exc:
        failure = PolicyTypedFailure(
            policy_id=descriptor.policy_id,
            code=(
                PolicyFailureCode.INVALID_RESULT
                if isinstance(exc, _InvalidPolicyResult)
                else PolicyFailureCode.EVALUATION_FAILED
            ),
            fallback_outcome=(
                PolicyFailureOutcome.DENY
                if descriptor.failure_policy
                is RuntimeFailurePolicy.FAIL_CLOSED
                else PolicyFailureOutcome.ALLOW
            ),
            error_type=type(exc).__name__,
        )
        fallback_value = fallback(failure)
        if fallback_value is None or inspect.isawaitable(fallback_value):
            close = getattr(fallback_value, "close", None)
            if callable(close):
                close()
            raise PolicyExecutionError(
                f"Policy {descriptor.policy_id} fallback 违反输出合同"
            )
        return PolicyExecutionResult(
            value=fallback_value,
            failure=failure,
        )


__all__ = [
    "ObserverDispatchReport",
    "PROTECTED_TRANSFORM_INVARIANTS",
    "PolicyDescriptor",
    "PolicyExecutionError",
    "PolicyExecutionResult",
    "PolicyFailureCode",
    "PolicyFailureOutcome",
    "PolicyRegistry",
    "PolicyRegistryError",
    "PolicyTypedFailure",
    "RuntimeExtensionKind",
    "RuntimeFailurePolicy",
    "RuntimeHookContractError",
    "RuntimeHookDescriptor",
    "RuntimeHookExecutionError",
    "RuntimeHookFailure",
    "RuntimeHookFailureCode",
    "RuntimeHookKind",
    "RuntimeHookRegistry",
    "RuntimeHookRegistryError",
    "RuntimeObserverBinding",
    "RuntimeObserverDispatcher",
    "RuntimeObserverHook",
    "RuntimeTransformBinding",
    "RuntimeTransformDispatcher",
    "RuntimeTransformHook",
    "TransformDispatchResult",
    "execute_policy",
]
