import hashlib
import json
import threading
import time
from dataclasses import replace
from typing import Any


_EVENT_STATE_LOCK = threading.Lock()
_EVENT_TOOL_STATE: dict[str, tuple[str, int, str]] = {}


def _payload_fingerprint(value: Any) -> tuple[int, str]:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = repr(type(value))
    encoded = text.encode("utf-8", errors="replace")
    return len(encoded), hashlib.sha256(encoded).hexdigest()


def _tool_event_context(tool_call_id: str):
    from core.runtime.event_bus import current_runtime_event_context

    return replace(
        current_runtime_event_context(),
        tool_call_id=tool_call_id,
    )


def begin_tool_trace(
    tool_name: str,
    args: Any,
    *,
    tool_call_id: str = "",
) -> tuple[str, float]:
    started = time.time()
    try:
        from core.tracing import ToolTracer
        from core.tracing_context import get_trace_context

        trace_id, run_id = get_trace_context()
        tool_call_id = ToolTracer.start_tool_call(
            trace_id,
            run_id,
            tool_name,
            args,
            tool_call_id=tool_call_id,
        )
    except Exception:
        return "", started
    args_bytes, args_sha256 = _payload_fingerprint(args)
    with _EVENT_STATE_LOCK:
        _EVENT_TOOL_STATE[tool_call_id] = (
            str(tool_name or ""),
            args_bytes,
            args_sha256,
        )
    from core.runtime.event_bus import emit_runtime_event

    emit_runtime_event(
        "tool.execute",
        "started",
        context=_tool_event_context(tool_call_id),
        attributes={
            "tool_name": str(tool_name or ""),
            "args_bytes": args_bytes,
            "args_sha256": args_sha256,
        },
    )
    return tool_call_id, started


def finish_tool_trace(
    tool_call_id: str,
    started: float,
    *,
    status: str = "success",
    result: Any = None,
    error: str = "",
    failure_code: str = "",
    error_type: str = "",
    retryable: bool | None = None,
    stop: bool | None = None,
) -> None:
    if not tool_call_id:
        return
    with _EVENT_STATE_LOCK:
        event_state = _EVENT_TOOL_STATE.pop(tool_call_id, None)
    try:
        from core.tracing import ToolTracer

        ToolTracer.finish_tool_call(
            tool_call_id,
            status=status,
            result=result,
            error=error,
            latency_ms=int((time.time() - started) * 1000),
        )
    except Exception:
        pass
    if event_state is None:
        return
    tool_name, args_bytes, args_sha256 = event_state
    result_bytes, result_sha256 = _payload_fingerprint(result)
    from core.runtime.event_bus import emit_runtime_event

    attributes: dict[str, object] = {
        "tool_name": tool_name,
        "args_bytes": args_bytes,
        "args_sha256": args_sha256,
        "result_bytes": result_bytes,
        "result_sha256": result_sha256,
        "result_truncated": False,
        "latency_ms": (time.time() - started) * 1000,
        "failure_code": (
            str(failure_code or "tool_error")
            if status != "success"
            else ""
        ),
        "error_type": (
            str(error_type or "tool_error")
            if status != "success"
            else ""
        ),
    }
    if retryable is not None:
        attributes["retryable"] = retryable
    if stop is not None:
        attributes["stop"] = stop
    emit_runtime_event(
        "tool.execute",
        "succeeded" if status == "success" else "failed",
        context=_tool_event_context(tool_call_id),
        attributes=attributes,
    )


def install_executor_tracing(executor: Any) -> None:
    """在 KT Executor 实例上安装 Nanobot 工具调用追踪 wrapper。"""
    if not executor or getattr(executor, "_nanobot_trace_installed", False):
        return
    original_run_tool = getattr(executor, "_run_tool", None)
    if original_run_tool is None:
        return

    async def traced_run_tool(job_id: str, tool: Any, args: dict[str, Any], is_direct: bool = False):
        tool_call_id, started = begin_tool_trace(getattr(tool, "tool_name", ""), args)
        from core.tracing_context import (
            reset_tool_trace_context,
            set_tool_trace_context,
        )

        tool_trace_token = set_tool_trace_context(tool_call_id)

        try:
            result = await original_run_tool(job_id, tool, args, is_direct)
        except Exception as e:
            if tool_call_id:
                finish_tool_trace(tool_call_id, started, status="error", result="", error=str(e))
            raise
        else:
            if tool_call_id:
                from core.tool_execution_policy import extract_tool_failure

                structured_failure = extract_tool_failure(result)
                status = "success"
                error = getattr(result, "error", "") or ""
                exit_code = getattr(result, "exit_code", 0)
                if (
                    error
                    or exit_code not in (0, None)
                    or structured_failure is not None
                ):
                    status = "error"
                if structured_failure is not None and not error:
                    error = (
                        f"{structured_failure.code}: "
                        f"{structured_failure.summary}"
                    )
                finish_tool_trace(
                    tool_call_id,
                    started,
                    status=status,
                    result=getattr(result, "output", ""),
                    error=error,
                    failure_code=(
                        structured_failure.code
                        if structured_failure is not None
                        else ""
                    ),
                    error_type=(
                        "structured_tool_error"
                        if structured_failure is not None
                        else ""
                    ),
                    retryable=(
                        structured_failure.retryable
                        if structured_failure is not None
                        else None
                    ),
                    stop=(
                        structured_failure.stop
                        if structured_failure is not None
                        else None
                    ),
                )
            return result
        finally:
            reset_tool_trace_context(tool_trace_token)

    executor._run_tool = traced_run_tool
    executor._nanobot_trace_installed = True
