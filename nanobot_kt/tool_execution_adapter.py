"""KT 已注册工具到框架无关 ``ToolExecutionPort`` 的执行适配器。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from core.agent_runtime import (
    AgentRuntimeCapabilityError,
    AgentRuntimeExecutionError,
    RuntimeRunError,
    RuntimeToolCall,
    RuntimeToolCallStatus,
    RuntimeToolExecutionRequest,
    RuntimeToolExecutionResult,
)
from core.tool_execution_policy import extract_tool_failure
from core.tool_tracing import begin_tool_trace, finish_tool_trace
from core.tracing_context import (
    reset_tool_trace_context,
    set_tool_trace_context,
)


class KtRegisteredToolExecutionAdapter:
    """调用 KT Tool 的公开 ``execute``，不提交 KT executor job。"""

    port_id = "kt:registered-tool-execution"

    def __init__(self, agent: object) -> None:
        self._agent = agent

    def _resolve_tool(self, request: RuntimeToolExecutionRequest) -> object:
        registry = getattr(self._agent, "registry", None)
        get_tool = getattr(registry, "get_tool", None)
        if not callable(get_tool):
            raise AgentRuntimeCapabilityError(
                "KT Agent 缺少公开 registry.get_tool 能力",
                runtime_id=self.port_id,
            )
        tool = get_tool(request.tool_call.name)
        if tool is None:
            raise AgentRuntimeCapabilityError(
                f"KT 工具未注册：{request.tool_call.name}",
                runtime_id=self.port_id,
            )
        execute = getattr(tool, "execute", None)
        if not callable(execute):
            raise AgentRuntimeCapabilityError(
                f"KT 工具缺少公开 execute：{request.tool_call.name}",
                runtime_id=self.port_id,
            )
        return tool

    @staticmethod
    def _finalize_result(
        agent: object,
        tool: object,
        request: RuntimeToolExecutionRequest,
        raw_result: object,
        *,
        trace_call_id: str,
        started: float,
    ) -> RuntimeToolExecutionResult:
        output = getattr(raw_result, "output", raw_result)
        raw_error = str(getattr(raw_result, "error", "") or "")
        exit_code = getattr(raw_result, "exit_code", None)
        raw_metadata = getattr(raw_result, "metadata", {})
        metadata = (
            dict(raw_metadata)
            if isinstance(raw_metadata, Mapping)
            else {}
        )
        from kohakuterrarium.core.tool_output import normalize_tool_output

        config = getattr(tool, "config", None)
        try:
            max_output = max(0, int(getattr(config, "max_output", 0) or 0))
        except (TypeError, ValueError):
            max_output = 0
        normalized = normalize_tool_output(
            output,
            max_output=max_output,
            job_id=request.tool_call.call_id,
            tool_name=request.tool_call.name,
            artifact_store=getattr(agent, "session_store", None),
        )
        output = normalized.output
        metadata.update(normalized.metadata)
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
        else:
            status = RuntimeToolCallStatus.COMPLETED
            error = None
            trace_status = "success"

        finish_tool_trace(
            trace_call_id,
            started,
            status=trace_status,
            result=output,
            error=raw_error,
            failure_code=failure_code,
            error_type="structured_tool_error" if structured_failure else "",
            retryable=retryable if failure_code else None,
            stop=stop if failure_code else None,
        )
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

    async def execute(
        self,
        request: RuntimeToolExecutionRequest,
    ) -> RuntimeToolExecutionResult:
        if not isinstance(request, RuntimeToolExecutionRequest):
            raise TypeError("request 必须是 RuntimeToolExecutionRequest")
        tool = self._resolve_tool(request)
        arguments = dict(request.arguments)
        trace_call_id, started = begin_tool_trace(
            request.tool_call.name,
            arguments,
            tool_call_id=request.tool_call.call_id,
        )
        correlation_id = trace_call_id or request.tool_call.call_id
        trace_token = set_tool_trace_context(correlation_id)
        try:
            execute = getattr(tool, "execute")
            raw_result = await asyncio.wait_for(
                execute(arguments, context=None),
                timeout=request.timeout_seconds,
            )
        except asyncio.CancelledError:
            finish_tool_trace(
                trace_call_id,
                started,
                status="error",
                error="工具执行已取消",
                failure_code="tool_cancelled",
                error_type="cancelled",
                retryable=False,
                stop=True,
            )
            raise
        except TimeoutError:
            finish_tool_trace(
                trace_call_id,
                started,
                status="error",
                error="工具执行超时",
                failure_code="tool_timeout",
                error_type="timeout",
                retryable=True,
                stop=False,
            )
            raise
        except Exception as exc:
            finish_tool_trace(
                trace_call_id,
                started,
                status="error",
                error=str(exc),
                failure_code="tool_execution_failed",
                error_type=type(exc).__name__,
            )
            raise AgentRuntimeExecutionError(
                "KT 工具公开 execute 调用失败",
                runtime_id=self.port_id,
            ) from exc
        else:
            return self._finalize_result(
                self._agent,
                tool,
                request,
                raw_result,
                trace_call_id=trace_call_id,
                started=started,
            )
        finally:
            reset_tool_trace_context(trace_token)


__all__ = ["KtRegisteredToolExecutionAdapter"]
