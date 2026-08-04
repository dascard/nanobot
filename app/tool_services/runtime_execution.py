"""Native Runtime 的框架无关工具执行组合。"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from core.agent_runtime import (
    AgentRuntimeExecutionError,
    RegisteredToolExecutionPort,
    RuntimeRunError,
    RuntimeToolCall,
    RuntimeToolCallStatus,
    RuntimeToolExecutionRequest,
    RuntimeToolExecutionResult,
)
from core.tool_execution_policy import extract_tool_failure
from core.tool_registration import list_active_tool_registrations
from core.tool_tracing import begin_tool_trace, finish_tool_trace
from core.tracing_context import (
    reset_tool_trace_context,
    set_tool_trace_context,
)


ServiceResult = object
ServiceCallable = Callable[
    [dict[str, Any]],
    ServiceResult | Awaitable[ServiceResult],
]


def _normalize_service_result(
    request: RuntimeToolExecutionRequest,
    raw_result: object,
    *,
    trace_call_id: str,
    started: float,
) -> RuntimeToolExecutionResult:
    raw_output = getattr(raw_result, "output", raw_result)
    raw_error = str(getattr(raw_result, "error", "") or "")
    exit_code = getattr(raw_result, "exit_code", None)
    raw_metadata = getattr(raw_result, "metadata", {})
    metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    structured_failure = extract_tool_failure(raw_result)
    failure_code = ""
    retryable = bool(metadata.get("retryable", False))
    stop = False
    if structured_failure is not None:
        failure_code = structured_failure.code
        retryable = structured_failure.retryable
        stop = structured_failure.stop
        if not raw_error:
            raw_error = structured_failure.summary
    elif raw_error or exit_code not in {None, 0}:
        failure_code = str(metadata.get("code") or "tool_execution_failed")

    if failure_code:
        status = RuntimeToolCallStatus.FAILED
        error = RuntimeRunError(
            code=failure_code,
            message=raw_error or "工具执行失败",
            retryable=retryable,
        )
        trace_status = "error"
        output = (
            raw_output
            if raw_output is not None and raw_output != ""
            else None
        )
    else:
        status = RuntimeToolCallStatus.COMPLETED
        error = None
        trace_status = "success"
        output = raw_output

    finish_tool_trace(
        trace_call_id,
        started,
        status=trace_status,
        result=output,
        error=raw_error,
        failure_code=failure_code,
        error_type=(
            "structured_tool_error" if structured_failure is not None else ""
        ),
        retryable=retryable if failure_code else None,
        stop=stop if failure_code else None,
    )
    if stop:
        metadata["stop"] = True
    return RuntimeToolExecutionResult(
        tool_call=RuntimeToolCall(
            call_id=request.tool_call.call_id,
            name=request.tool_call.name,
            arguments=request.arguments,
            status=status,
            result=output,
        ),
        error=error,
        exit_code=exit_code,
        metadata=metadata,
    )


def _handler(service: ServiceCallable):
    async def execute(
        request: RuntimeToolExecutionRequest,
    ) -> RuntimeToolExecutionResult:
        arguments = dict(request.arguments)
        trace_call_id, started = begin_tool_trace(
            request.tool_call.name,
            arguments,
            tool_call_id=request.tool_call.call_id,
        )
        correlation_id = trace_call_id or request.tool_call.call_id
        trace_token = set_tool_trace_context(correlation_id)
        try:
            raw_result = service(arguments)
            if inspect.isawaitable(raw_result):
                raw_result = await raw_result
            return _normalize_service_result(
                request,
                raw_result,
                trace_call_id=trace_call_id,
                started=started,
            )
        except BaseException as exc:
            finish_tool_trace(
                trace_call_id,
                started,
                status="error",
                error=str(exc),
                failure_code="tool_execution_failed",
                error_type=type(exc).__name__,
            )
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise AgentRuntimeExecutionError(
                f"Native 工具应用服务调用失败：{request.tool_call.name}",
                runtime_id="native:registered-tool-execution",
            ) from exc
        finally:
            reset_tool_trace_context(trace_token)

    return execute


def build_registered_tool_execution_port(
    services: Mapping[str, ServiceCallable],
    *,
    port_id: str = "native:registered-tool-execution",
) -> RegisteredToolExecutionPort:
    """把 Composition Root 注入的应用服务冻结为确定性执行 Port。"""

    expected = {
        registration.execution_binding.port_id
        for registration in list_active_tool_registrations()
        if registration.execution_binding is not None
    }
    actual = set(services)
    missing = sorted(expected - actual)
    orphan = sorted(actual - expected)
    if missing or orphan:
        raise RuntimeError(
            "Native execution binding 漂移："
            f"缺失={missing}，悬空={orphan}"
        )
    handlers = {
        port_id: _handler(services[port_id])
        for port_id in sorted(expected)
    }
    return RegisteredToolExecutionPort(
        handlers,
        port_id=port_id,
    )


__all__ = [
    "ServiceCallable",
    "build_registered_tool_execution_port",
]
