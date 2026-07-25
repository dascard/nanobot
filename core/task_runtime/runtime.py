"""统一 Task 执行顺序、失败分类、重试终态和元数据遥测。"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Callable

from core.model_provider.route_registry import (
    ModelRouteNotFoundError,
    require_model_route_descriptor,
)
from core.prompt_v2.task_contracts import (
    TaskContract,
    TaskContractError,
    TaskOutputContractError,
    get_task_contract,
    get_task_invocation_spec,
    parse_task_output,
)
from core.prompt_v2.task_templates import (
    TaskTemplateUnavailableError,
    render_task_messages,
    render_task_pair,
    render_task_prompt,
)
from core.runtime.event_bus import (
    current_runtime_event_context,
    get_runtime_event_emitter,
)
from core.runtime.events import (
    RuntimeEventContext,
    RuntimeEventEmitter,
)
from core.task_runtime.contracts import (
    TaskFailureCode,
    TaskFailureStage,
    TaskInvocation,
    TaskModelCompletion,
    TaskModelExecutionError,
    TaskModelExecutionPort,
    TaskModelRequest,
    TaskResult,
    TaskTerminalAction,
    TaskTypedFailure,
    TaskValidationDiagnostic,
)
from core.task_runtime.resilience import (
    ResiliencePolicyDescriptor,
    require_resilience_policy,
)
from core.task_runtime.slo import (
    TaskSloDescriptor,
    get_task_slo_descriptor,
)
from core.task_runtime.validators import (
    TaskBusinessValidationError,
    validate_task_business_output,
)
from core.token_utils import estimate_tokens
from core.tracing_context import (
    reset_runtime_correlation,
    set_runtime_event_context,
)


class TaskRuntimeState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class _PreparedTask:
    invocation: TaskInvocation
    contract: TaskContract
    contract_version: str
    messages: tuple[dict[str, Any], ...]
    policy: ResiliencePolicyDescriptor
    slo: TaskSloDescriptor | None
    input_chars: int
    input_tokens: int


@dataclass(frozen=True, slots=True)
class _ExpectedFailure(Exception):
    code: TaskFailureCode
    stage: TaskFailureStage
    summary: str
    retryable: bool = False
    cause_type: str = ""
    diagnostics: tuple[TaskValidationDiagnostic, ...] = ()
    terminal_action: TaskTerminalAction | None = None


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_input_bytes(value: object) -> bytes:
    def fallback(item: object) -> str:
        return f"<{type(item).__module__}.{type(item).__qualname__}>"

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=fallback,
    ).encode("utf-8")


def _message_input_metrics(
    messages: tuple[dict[str, Any], ...],
) -> tuple[int, int]:
    contents = tuple(str(message.get("content") or "") for message in messages)
    return (
        sum(len(content) for content in contents),
        sum(estimate_tokens(content) for content in contents),
    )


def _usage_count(value: object) -> int | None:
    if type(value) is int:
        return value if value >= 0 else None
    if type(value) is float and value.is_integer() and value >= 0:
        return int(value)
    return None


def _usage_metrics(usage: Mapping[str, Any]) -> dict[str, int]:
    def first(*keys: str) -> int | None:
        for key in keys:
            parsed = _usage_count(usage.get(key))
            if parsed is not None:
                return parsed
        return None

    input_tokens = first("prompt_tokens", "input_tokens")
    output_tokens = first("completion_tokens", "output_tokens")
    total_tokens = first("total_tokens")
    if total_tokens is None and (
        input_tokens is not None and output_tokens is not None
    ):
        total_tokens = input_tokens + output_tokens
    return {
        key: value
        for key, value in (
            ("input_tokens", input_tokens),
            ("output_tokens", output_tokens),
            ("total_tokens", total_tokens),
        )
        if value is not None
    }


def _slo_event_attributes(
    descriptor: TaskSloDescriptor | None,
) -> dict[str, object]:
    if descriptor is None:
        return {}
    return {
        "slo_id": descriptor.slo_id,
        "slo_version": descriptor.version,
        "slo_status": descriptor.status.value,
    }


def _output_failure_from_exception(
    exc: TaskOutputContractError,
) -> _ExpectedFailure:
    raw_code = str(getattr(exc, "code", "") or "")
    try:
        code = TaskFailureCode(raw_code)
    except ValueError:
        code = TaskFailureCode.SCHEMA_INVALID
    diagnostics: tuple[TaskValidationDiagnostic, ...] = tuple(
        (
            item
            if isinstance(item, TaskValidationDiagnostic)
            else TaskValidationDiagnostic(
                code=str(item.get("code") or code.value),
                path=str(item.get("path") or ""),
                rule=str(item.get("rule") or ""),
                summary=str(item.get("summary") or ""),
            )
        )
        for item in getattr(exc, "diagnostics", ())
        if isinstance(item, (TaskValidationDiagnostic, dict))
    )
    if not diagnostics:
        diagnostics = (
            TaskValidationDiagnostic(
                code=code.value,
                rule="output_contract",
                summary="模型输出未通过结构化合同",
            ),
        )
    return _ExpectedFailure(
        code=code,
        stage=TaskFailureStage.OUTPUT_PARSE,
        summary="模型输出未通过结构化合同",
        retryable=True,
        cause_type=type(exc).__name__,
        diagnostics=diagnostics,
    )


class TaskRuntime:
    """执行一个版本化语义 Task；预期失败以 ``TaskResult`` 返回。"""

    def __init__(
        self,
        port: TaskModelExecutionPort,
        *,
        event_emitter: RuntimeEventEmitter | None = None,
        clock: Callable[[], float] | None = None,
        run_id_factory: Callable[[], str] | None = None,
        sleeper: Callable[[float], None] | None = None,
        jitter_source: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(port, TaskModelExecutionPort):
            raise TypeError("port 未实现 TaskModelExecutionPort")
        self._port = port
        self._event_emitter = event_emitter or get_runtime_event_emitter()
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._jitter_source = jitter_source or random.random
        self._run_id_factory = run_id_factory or (
            lambda: f"taskrun_{uuid.uuid4().hex}"
        )

    @property
    def adapter_id(self) -> str:
        return str(self._port.adapter_id)

    def execute(self, invocation: TaskInvocation) -> TaskResult:
        if not isinstance(invocation, TaskInvocation):
            raise TypeError("invocation 必须是 TaskInvocation")
        started_at = self._clock()
        run_id = str(self._run_id_factory() or "").strip()
        if not run_id:
            raise ValueError("Task Runtime run_id_factory 返回空值")
        input_bytes = _canonical_input_bytes({
            "input_values": dict(invocation.input_values),
            "rendered_messages": [
                dict(message)
                for message in invocation.rendered_messages
            ],
        })
        input_sha256 = _hash_bytes(input_bytes)
        context = replace(
            invocation.trace_context or self._event_context(),
            task_id=invocation.task_id,
            task_run_id=run_id,
        )
        self._emit(
            "started",
            invocation=invocation,
            context=context,
            contract_version=invocation.contract_version,
            attempt_count=0,
            latency_ms=0,
            input_sha256=input_sha256,
            input_bytes=len(input_bytes),
        )

        prepared: _PreparedTask | None = None
        try:
            prepared = self._prepare(invocation)
        except _ExpectedFailure as failure:
            return self._failed_result(
                invocation=invocation,
                prepared=None,
                failure=failure,
                attempt_count=0,
                started_at=started_at,
                run_id=run_id,
                context=context,
                input_sha256=input_sha256,
            )

        completion: TaskModelCompletion | None = None
        raw_output = ""
        raw_output_sha256 = ""
        raw_output_bytes = 0
        attempt_count = 0
        last_failure: _ExpectedFailure | None = None
        total_timeout_seconds = min(
            invocation.timeout_budget_seconds,
            prepared.policy.total_timeout_seconds,
        )

        for attempt_no in range(1, prepared.policy.max_attempts + 1):
            attempt_count = attempt_no
            elapsed = self._clock() - started_at
            remaining = total_timeout_seconds - elapsed
            if remaining <= 0:
                last_failure = _ExpectedFailure(
                    code=TaskFailureCode.EXECUTION_TIMEOUT,
                    stage=TaskFailureStage.PROVIDER,
                    summary="Task 总超时预算已耗尽",
                    retryable=True,
                    cause_type="TimeoutError",
                )
            else:
                try:
                    correlation_tokens = set_runtime_event_context(context)
                    try:
                        completion = self._port.complete_task(
                            TaskModelRequest(
                                task_id=invocation.task_id,
                                contract_version=prepared.contract_version,
                                route_key=invocation.route_key,
                                messages=prepared.messages,
                                run_id=run_id,
                                attempt_no=attempt_no,
                                timeout_seconds=min(
                                    remaining,
                                    prepared.policy.per_attempt_timeout_seconds,
                                ),
                                max_tokens=invocation.max_tokens,
                                temperature=invocation.temperature,
                                metadata={
                                    "idempotency_key_sha256": (
                                        _hash_bytes(
                                            invocation.idempotency_key.encode(
                                                "utf-8"
                                            )
                                        )
                                        if invocation.idempotency_key
                                        else ""
                                    ),
                                    "request_context": dict(
                                        invocation.request_context
                                    ),
                                },
                            )
                        )
                    finally:
                        reset_runtime_correlation(correlation_tokens)
                    if completion.route_key != invocation.route_key:
                        raise TypeError(
                            "Task 模型 Adapter 返回的 route_key 与请求不一致"
                        )
                    raw_output = completion.content
                    encoded_output = raw_output.encode("utf-8")
                    raw_output_sha256 = _hash_bytes(encoded_output)
                    raw_output_bytes = len(encoded_output)
                    parsed = parse_task_output(
                        prepared.contract.task_key,
                        raw_output,
                    )
                    parsed = validate_task_business_output(
                        prepared.contract_version,
                        parsed,
                        request_context=invocation.request_context,
                    )
                except TaskModelExecutionError as exc:
                    last_failure = _ExpectedFailure(
                        code=exc.code,
                        stage=TaskFailureStage.PROVIDER,
                        summary=exc.summary or "模型执行失败",
                        retryable=exc.retryable,
                        cause_type=exc.cause_type or type(exc).__name__,
                    )
                except TimeoutError as exc:
                    last_failure = _ExpectedFailure(
                        code=TaskFailureCode.EXECUTION_TIMEOUT,
                        stage=TaskFailureStage.PROVIDER,
                        summary="模型执行超时",
                        retryable=True,
                        cause_type=type(exc).__name__,
                    )
                except TaskOutputContractError as exc:
                    last_failure = _output_failure_from_exception(exc)
                except TaskBusinessValidationError as exc:
                    last_failure = _ExpectedFailure(
                        code=TaskFailureCode.BUSINESS_VALIDATION_FAILED,
                        stage=TaskFailureStage.BUSINESS_VALIDATION,
                        summary="模型输出未通过业务后置校验",
                        retryable=True,
                        cause_type=type(exc).__name__,
                        diagnostics=exc.diagnostics,
                    )
                else:
                    latency_ms = max(
                        0.0,
                        (self._clock() - started_at) * 1000.0,
                    )
                    result = TaskResult(
                        parsed_value=parsed,
                        contract_version=prepared.contract_version,
                        route_key=invocation.route_key,
                        provider=completion.provider,
                        model=completion.model,
                        attempt_count=attempt_count,
                        latency_ms=latency_ms,
                        failure=None,
                        raw_output_sha256=raw_output_sha256,
                        raw_output_bytes=raw_output_bytes,
                        validation_diagnostics=(),
                        run_id=run_id,
                        usage=completion.usage,
                        execution_metadata=completion.metadata,
                    )
                    self._emit(
                        "succeeded",
                        invocation=invocation,
                        context=context,
                        contract_version=prepared.contract_version,
                        attempt_count=attempt_count,
                        latency_ms=latency_ms,
                        input_sha256=input_sha256,
                        output_sha256=raw_output_sha256,
                        output_bytes=raw_output_bytes,
                        output_truncated=False,
                        provider=completion.provider,
                        model=completion.model,
                        input_chars=prepared.input_chars,
                        input_token_estimate=prepared.input_tokens,
                        **_usage_metrics(completion.usage),
                    )
                    return result

            if (
                last_failure is None
                or attempt_no >= prepared.policy.max_attempts
                or not prepared.policy.allows_retry(
                    last_failure.code,
                    failure_retryable=last_failure.retryable,
                )
            ):
                break
            backoff_seconds = prepared.policy.backoff_seconds(
                attempt_no,
                jitter_sample=self._jitter_source(),
            )
            remaining_after_attempt = (
                total_timeout_seconds
                - (self._clock() - started_at)
            )
            if backoff_seconds >= remaining_after_attempt:
                break
            if backoff_seconds > 0:
                self._sleeper(backoff_seconds)

        if last_failure is None:  # pragma: no cover - 循环不变量
            raise RuntimeError("Task Runtime 未生成结果或失败")
        return self._failed_result(
            invocation=invocation,
            prepared=prepared,
            failure=last_failure,
            attempt_count=attempt_count,
            started_at=started_at,
            run_id=run_id,
            context=context,
            input_sha256=input_sha256,
            completion=completion,
            raw_output_sha256=raw_output_sha256,
            raw_output_bytes=raw_output_bytes,
        )

    def _prepare(self, invocation: TaskInvocation) -> _PreparedTask:
        spec = get_task_invocation_spec(invocation.invocation_id)
        if spec is None:
            raise _ExpectedFailure(
                code=TaskFailureCode.INVALID_INVOCATION,
                stage=TaskFailureStage.CONTRACT,
                summary="Task invocation 未登记",
            )
        template_refs = invocation.template_refs or spec.template_keys
        if tuple(template_refs) != tuple(spec.template_keys):
            raise _ExpectedFailure(
                code=TaskFailureCode.INVALID_INVOCATION,
                stage=TaskFailureStage.CONTRACT,
                summary="Task template refs 与登记合同不一致",
            )
        contracts = tuple(get_task_contract(key) for key in template_refs)
        if any(contract is None for contract in contracts):
            raise _ExpectedFailure(
                code=TaskFailureCode.INVALID_INVOCATION,
                stage=TaskFailureStage.CONTRACT,
                summary="Task template contract 不存在",
            )
        typed_contracts = tuple(
            contract
            for contract in contracts
            if contract is not None
        )
        output_contract_ids = {
            contract.output_contract_id
            for contract in typed_contracts
        }
        if len(output_contract_ids) != 1:
            raise _ExpectedFailure(
                code=TaskFailureCode.INVALID_INVOCATION,
                stage=TaskFailureStage.CONTRACT,
                summary="Task invocation 包含不一致的输出合同",
            )
        output_contract_id = next(iter(output_contract_ids))
        if (
            invocation.contract_version
            and invocation.contract_version != output_contract_id
        ):
            raise _ExpectedFailure(
                code=TaskFailureCode.CONTRACT_VERSION_MISMATCH,
                stage=TaskFailureStage.CONTRACT,
                summary="Task 输出合同版本不匹配",
            )
        try:
            route = require_model_route_descriptor(invocation.route_key)
        except ModelRouteNotFoundError as exc:
            raise _ExpectedFailure(
                code=TaskFailureCode.ROUTE_UNAVAILABLE,
                stage=TaskFailureStage.ROUTE,
                summary="Task 模型路由未登记",
                cause_type=type(exc).__name__,
            ) from exc
        if (
            route.output_contract_id != output_contract_id
            or not set(template_refs).issubset(route.task_contract_keys)
        ):
            raise _ExpectedFailure(
                code=TaskFailureCode.CONTRACT_VERSION_MISMATCH,
                stage=TaskFailureStage.CONTRACT,
                summary="Task Contract 与 Model Route Descriptor 不一致",
            )
        policy_ids = {
            contract.output_failure_policy
            for contract in typed_contracts
        }
        if len(policy_ids) != 1:
            raise _ExpectedFailure(
                code=TaskFailureCode.INVALID_INVOCATION,
                stage=TaskFailureStage.CONTRACT,
                summary="Task invocation 包含不一致的失败策略",
            )
        policy = require_resilience_policy(next(iter(policy_ids)))
        slo = get_task_slo_descriptor(invocation.task_id)
        if (
            policy.slo_descriptor_id == "task_slo.by_invocation.v1"
            and slo is None
        ):
            raise _ExpectedFailure(
                code=TaskFailureCode.INVALID_INVOCATION,
                stage=TaskFailureStage.CONTRACT,
                summary="Task SLO 未登记",
                terminal_action=policy.terminal_action,
            )
        if (
            slo is not None
            and invocation.max_tokens is not None
            and invocation.max_tokens > slo.max_output_tokens
        ):
            raise _ExpectedFailure(
                code=TaskFailureCode.QUOTA_EXCEEDED,
                stage=TaskFailureStage.RENDER,
                summary="Task 请求超过 SLO 输出预算",
                terminal_action=policy.terminal_action,
            )
        try:
            messages = (
                self._validate_pre_rendered_messages(
                    invocation.rendered_messages
                )
                if invocation.rendered_messages
                else self._render_messages(
                    spec.render_api,
                    template_refs,
                    dict(invocation.input_values),
                )
            )
        except TaskTemplateUnavailableError as exc:
            raise _ExpectedFailure(
                code=TaskFailureCode.TEMPLATE_UNAVAILABLE,
                stage=TaskFailureStage.RENDER,
                summary="Task Prompt 模板不可用",
                cause_type=type(exc).__name__,
            ) from exc
        except TaskContractError as exc:
            raise _ExpectedFailure(
                code=TaskFailureCode.INVALID_INVOCATION,
                stage=TaskFailureStage.CONTRACT,
                summary="Task 输入不满足模板合同",
                cause_type=type(exc).__name__,
            ) from exc
        input_chars, input_tokens = _message_input_metrics(messages)
        if slo is not None and (
            input_chars > slo.max_input_chars
            or input_tokens > slo.max_input_tokens
        ):
            raise _ExpectedFailure(
                code=TaskFailureCode.QUOTA_EXCEEDED,
                stage=TaskFailureStage.RENDER,
                summary="Task 请求超过 SLO 输入预算",
                terminal_action=policy.terminal_action,
            )
        return _PreparedTask(
            invocation=invocation,
            contract=typed_contracts[0],
            contract_version=output_contract_id,
            messages=messages,
            policy=policy,
            slo=slo,
            input_chars=input_chars,
            input_tokens=input_tokens,
        )

    @staticmethod
    def _validate_pre_rendered_messages(
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        normalized: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "").strip()
            content = message.get("content")
            if role not in {"system", "user", "assistant"}:
                raise TaskContractError(
                    "Task 预渲染消息包含非法 role"
                )
            if not isinstance(content, str) or not content.strip():
                raise TaskContractError(
                    "Task 预渲染消息 content 不能为空"
                )
            normalized.append({"role": role, "content": content})
        if not normalized:
            raise TaskContractError("Task 预渲染消息不能为空")
        return tuple(normalized)

    @staticmethod
    def _render_messages(
        render_api: str,
        template_refs: tuple[str, ...],
        values: dict[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        if render_api == "messages":
            rendered = render_task_messages(
                template_refs[0],
                values,
                fallback_messages=[],
            )
            return tuple(dict(message) for message in rendered)
        if render_api == "paired_messages":
            rendered_pair = render_task_pair(
                get_task_invocation_spec_for_refs(template_refs),
                values,
            )
            return tuple(dict(message) for message in rendered_pair.messages)
        if render_api == "prompt":
            rendered_parts = tuple(
                render_task_prompt(key, values)
                for key in template_refs
            )
            if len(rendered_parts) == 1:
                return ({"role": "user", "content": rendered_parts[0]},)
            return (
                {"role": "system", "content": rendered_parts[0]},
                {
                    "role": "user",
                    "content": "\n\n".join(rendered_parts[1:]),
                },
            )
        raise TaskContractError(
            f"Task Runtime 不支持 render_api={render_api}"
        )

    def _failed_result(
        self,
        *,
        invocation: TaskInvocation,
        prepared: _PreparedTask | None,
        failure: _ExpectedFailure,
        attempt_count: int,
        started_at: float,
        run_id: str,
        context: RuntimeEventContext,
        input_sha256: str,
        completion: TaskModelCompletion | None = None,
        raw_output_sha256: str = "",
        raw_output_bytes: int = 0,
    ) -> TaskResult:
        latency_ms = max(0.0, (self._clock() - started_at) * 1000.0)
        terminal_action = (
            failure.terminal_action
            or (
                prepared.policy.terminal_action
                if prepared is not None
                else TaskTerminalAction.BLOCK
            )
        )
        typed_failure = TaskTypedFailure(
            code=failure.code,
            stage=failure.stage,
            retryable=(
                prepared is not None
                and prepared.policy.allows_retry(
                    failure.code,
                    failure_retryable=failure.retryable,
                )
            ),
            summary=failure.summary,
            terminal_action=terminal_action,
            cause_type=failure.cause_type,
            trace_ref=context.trace_id,
        )
        provider = completion.provider if completion is not None else ""
        model = completion.model if completion is not None else ""
        contract_version = (
            prepared.contract_version
            if prepared is not None
            else invocation.contract_version
        )
        result = TaskResult(
            parsed_value=None,
            contract_version=contract_version,
            route_key=invocation.route_key,
            provider=provider,
            model=model,
            attempt_count=attempt_count,
            latency_ms=latency_ms,
            failure=typed_failure,
            raw_output_sha256=raw_output_sha256,
            raw_output_bytes=raw_output_bytes,
            validation_diagnostics=failure.diagnostics,
            run_id=run_id,
            usage=completion.usage if completion is not None else {},
            execution_metadata=(
                completion.metadata
                if completion is not None
                else {}
            ),
        )
        self._emit(
            "failed",
            invocation=invocation,
            context=context,
            contract_version=contract_version,
            attempt_count=attempt_count,
            latency_ms=latency_ms,
            input_sha256=input_sha256,
            output_sha256=raw_output_sha256,
            output_bytes=raw_output_bytes,
            output_truncated=False,
            failure_code=failure.code.value,
            provider=provider,
            model=model,
            terminal_action=terminal_action.value,
            **(
                {
                    "input_chars": prepared.input_chars,
                    "input_token_estimate": prepared.input_tokens,
                    **_usage_metrics(completion.usage),
                }
                if prepared is not None and completion is not None
                else (
                    {
                        "input_chars": prepared.input_chars,
                        "input_token_estimate": prepared.input_tokens,
                    }
                    if prepared is not None
                    else {}
                )
            ),
        )
        return result

    @staticmethod
    def _event_context() -> RuntimeEventContext:
        try:
            return current_runtime_event_context()
        except Exception:
            return RuntimeEventContext()

    def _emit(
        self,
        phase: str,
        *,
        invocation: TaskInvocation,
        context: RuntimeEventContext,
        **attributes: object,
    ) -> None:
        payload = {
            "task_id": invocation.task_id,
            "route_key": invocation.route_key,
            **_slo_event_attributes(
                get_task_slo_descriptor(invocation.task_id)
            ),
            **attributes,
        }
        try:
            self._event_emitter.emit(
                "task.execute",
                phase,
                context=context,
                attributes=payload,
            )
        except Exception:
            # Telemetry 是 Observer，故障不能改变 Task 业务结果。
            return


def get_task_invocation_spec_for_refs(
    template_refs: tuple[str, ...],
) -> str:
    """按完整模板集合找 invocation；避免用首模板猜测多模板 Task。"""

    from core.prompt_v2.task_contracts import list_task_invocation_specs

    matches = [
        spec.invocation_id
        for spec in list_task_invocation_specs()
        if tuple(spec.template_keys) == tuple(template_refs)
    ]
    if len(matches) != 1:
        raise TaskContractError("Task template refs 未唯一映射 invocation")
    return matches[0]


class _TaskRuntimeLifecycle:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = TaskRuntimeState.NEW
        self._runtime: TaskRuntime | None = None

    def start(
        self,
        port: TaskModelExecutionPort,
        *,
        event_emitter: RuntimeEventEmitter | None = None,
    ) -> None:
        with self._lock:
            if self._state is TaskRuntimeState.RUNNING:
                if (
                    self._runtime is not None
                    and self._runtime.adapter_id == str(port.adapter_id)
                ):
                    return
                raise RuntimeError("Task Runtime 已由其他 Adapter 启动")
            self._runtime = TaskRuntime(
                port,
                event_emitter=event_emitter,
            )
            self._state = TaskRuntimeState.RUNNING

    def stop(self) -> None:
        with self._lock:
            self._runtime = None
            self._state = TaskRuntimeState.STOPPED

    def execute(self, invocation: TaskInvocation) -> TaskResult:
        with self._lock:
            if self._state is not TaskRuntimeState.RUNNING:
                raise RuntimeError("Task Runtime 尚未启动或已经停止")
            runtime = self._runtime
        if runtime is None:
            raise RuntimeError("Task Runtime Adapter 未配置")
        return runtime.execute(invocation)

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self._state.value,
                "adapter_id": (
                    self._runtime.adapter_id
                    if self._runtime is not None
                    else ""
                ),
            }


_TASK_RUNTIME = _TaskRuntimeLifecycle()


def start_task_runtime(
    port: TaskModelExecutionPort,
    *,
    event_emitter: RuntimeEventEmitter | None = None,
) -> None:
    _TASK_RUNTIME.start(port, event_emitter=event_emitter)


def stop_task_runtime() -> None:
    _TASK_RUNTIME.stop()


def execute_task(invocation: TaskInvocation) -> TaskResult:
    return _TASK_RUNTIME.execute(invocation)


def task_runtime_status() -> dict[str, object]:
    return _TASK_RUNTIME.status()
