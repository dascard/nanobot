"""Bridge 类型化 Runtime 事件的持久化与旧 SSE 投影。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from core.agent_runtime import RuntimeRunEvent, RuntimeRunEventKind


def _reply_contract_stream_gate_active() -> bool:
    """有最终动作工具时，首轮普通文本只能留在内部审计。"""

    try:
        from core.tool_execution_policy import FINAL_ACTION_TOOLS
        from core.tool_plan import get_current_tool_plan

        plan = get_current_tool_plan()
        if plan is None:
            return False
        names = {
            str(name or "").strip()
            for name in getattr(plan, "sent_tool_names", ())
        }
        return bool(names.intersection(FINAL_ACTION_TOOLS))
    except Exception:
        # 事件投影不能因为诊断性门禁异常阻断 durable sink；合同检查仍会
        # 在 Bridge 的最终阶段执行。
        return False


RuntimeEventHandler = Callable[[RuntimeRunEvent], Awaitable[None]]


def create_default_run_event_sink() -> Any:
    """延迟创建生产 Ledger Sink，保留测试替换 ``_run_event_sink``。"""

    from core.run_ledger.sinks import default_runtime_run_event_sink

    return default_runtime_run_event_sink()


def build_runtime_event_handler(
    owner: object,
    stream_queue: asyncio.Queue[dict[str, Any]] | None = None,
) -> RuntimeEventHandler:
    """生成先持久化、后投影旧 SSE 的单一事件处理器。"""

    async def handle(event: RuntimeRunEvent) -> None:
        durable_sink = getattr(owner, "_run_event_sink", None)
        if durable_sink is not None:
            await durable_sink.append(event)
        if stream_queue is None:
            return
        if (
            event.kind is RuntimeRunEventKind.TEXT_DELTA
            and event.text_delta
            and not _reply_contract_stream_gate_active()
        ):
            await stream_queue.put({"status": "delta", "text": event.text_delta})
        elif event.kind is RuntimeRunEventKind.ERROR and event.error is not None:
            await stream_queue.put({
                "status": "error",
                "message": event.error.message,
            })

    return handle


__all__ = [
    "RuntimeEventHandler",
    "build_runtime_event_handler",
    "create_default_run_event_sink",
]
