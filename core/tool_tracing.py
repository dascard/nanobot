import time
from typing import Any


def begin_tool_trace(tool_name: str, args: Any) -> tuple[str, float]:
    started = time.time()
    try:
        from core.tracing import ToolTracer
        from core.tracing_context import get_trace_context

        trace_id, run_id = get_trace_context()
        return ToolTracer.start_tool_call(trace_id, run_id, tool_name, args), started
    except Exception:
        return "", started


def finish_tool_trace(
    tool_call_id: str,
    started: float,
    *,
    status: str = "success",
    result: Any = None,
    error: str = "",
) -> None:
    if not tool_call_id:
        return
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


def install_executor_tracing(executor: Any) -> None:
    """在 KT Executor 实例上安装 Nanobot 工具调用追踪 wrapper。"""
    if not executor or getattr(executor, "_nanobot_trace_installed", False):
        return
    original_run_tool = getattr(executor, "_run_tool", None)
    if original_run_tool is None:
        return

    async def traced_run_tool(job_id: str, tool: Any, args: dict[str, Any], is_direct: bool = False):
        tool_call_id, started = begin_tool_trace(getattr(tool, "tool_name", ""), args)

        try:
            result = await original_run_tool(job_id, tool, args, is_direct)
        except Exception as e:
            if tool_call_id:
                finish_tool_trace(tool_call_id, started, status="error", result="", error=str(e))
            raise

        if tool_call_id:
            status = "success"
            error = getattr(result, "error", "") or ""
            exit_code = getattr(result, "exit_code", 0)
            if error or exit_code not in (0, None):
                status = "error"
            finish_tool_trace(
                tool_call_id,
                started,
                status=status,
                result=getattr(result, "output", ""),
                error=error,
            )
        return result

    executor._run_tool = traced_run_tool
    executor._nanobot_trace_installed = True
