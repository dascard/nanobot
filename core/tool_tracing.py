import time
from typing import Any


def install_executor_tracing(executor: Any) -> None:
    """在 KT Executor 实例上安装 Nanobot 工具调用追踪 wrapper。"""
    if not executor or getattr(executor, "_nanobot_trace_installed", False):
        return
    original_run_tool = getattr(executor, "_run_tool", None)
    if original_run_tool is None:
        return

    async def traced_run_tool(job_id: str, tool: Any, args: dict[str, Any], is_direct: bool = False):
        tool_call_id = ""
        started = time.time()
        try:
            from core.tracing import ToolTracer
            from core.tracing_context import get_trace_context

            trace_id, run_id = get_trace_context()
            tool_call_id = ToolTracer.start_tool_call(
                trace_id,
                run_id,
                getattr(tool, "tool_name", ""),
                args,
            )
        except Exception:
            tool_call_id = ""

        try:
            result = await original_run_tool(job_id, tool, args, is_direct)
        except Exception as e:
            if tool_call_id:
                try:
                    from core.tracing import ToolTracer

                    ToolTracer.finish_tool_call(
                        tool_call_id,
                        status="error",
                        result="",
                        error=str(e),
                        latency_ms=int((time.time() - started) * 1000),
                    )
                except Exception:
                    pass
            raise

        if tool_call_id:
            try:
                from core.tracing import ToolTracer

                status = "success"
                error = getattr(result, "error", "") or ""
                exit_code = getattr(result, "exit_code", 0)
                if error or exit_code not in (0, None):
                    status = "error"
                ToolTracer.finish_tool_call(
                    tool_call_id,
                    status=status,
                    result=getattr(result, "output", ""),
                    error=error,
                    latency_ms=int((time.time() - started) * 1000),
                )
            except Exception:
                pass
        return result

    executor._run_tool = traced_run_tool
    executor._nanobot_trace_installed = True
