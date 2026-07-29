"""不经过模型的 KT 已注册工具执行入口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class DirectToolExecutionResult:
    """已注册工具的执行结果及其 Trace 关联。"""

    output: Any
    error: str
    exit_code: int | None
    metadata: dict[str, Any]
    trace_id: str
    run_id: str
    tool_call_id: str

    @property
    def success(self) -> bool:
        return not self.error and self.exit_code in {None, 0}


async def execute_registered_tool(
    bridge: Any,
    tool_name: str,
    args: dict[str, Any],
    *,
    user_id: str,
    session_id: str,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str = "",
    trace_id: str = "",
    timeout_seconds: float = 600.0,
    additional_tool_schemas: tuple[dict[str, Any], ...] = (),
) -> DirectToolExecutionResult:
    """在真实 ToolPlan、请求身份和 Trace 边界内直接执行一个 KT 工具。

    该入口专供确定性 workflow 使用，不构造 prompt，也不会调用模型。
    动态工具实例必须在调用前由受信 Adapter 注册；Schema 只在本请求的
    ContextVar 中生效。
    """

    agent = getattr(bridge, "_agent", None)
    if agent is None:
        raise RuntimeError("KT Agent 尚未初始化")
    name = str(tool_name or "").strip()
    if not name:
        raise ValueError("tool_name 不能为空")
    if not isinstance(args, dict):
        raise TypeError("工具参数必须是对象")
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ValueError("session_id 不能为空")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")

    meta = dict(metadata or {})
    is_group = bool(meta.get("is_group"))
    chat_type = "group" if is_group else "private"
    runtime_chat_type = str(
        meta.get("runtime_chat_type") or chat_type
    ).strip()
    platform = str(meta.get("platform") or "qq").strip().lower()
    normalized_user_id = str(
        meta.get("user_id")
        or user_id
        or ("" if is_group else normalized_session_id)
    ).strip()
    group_id = (
        str(meta.get("group_id") or "").strip() if is_group else ""
    )
    request_id = str(
        meta.get("request_id")
        or idempotency_key
        or f"direct-tool-{uuid4().hex}"
    )

    from core.tool_plan import (
        additional_tool_schemas_scope,
        build_tool_plan,
        tool_plan_scope,
    )
    from core.uow import UnitOfWork

    with additional_tool_schemas_scope(additional_tool_schemas):
        with UnitOfWork() as uow:
            tool_plan = build_tool_plan(
                chat_type=runtime_chat_type,
                group_id=group_id,
                user_id=normalized_user_id,
                platform=platform,
                session_id=normalized_session_id,
                runtime_preset=str(meta.get("runtime_preset") or "full"),
                db=uow.db,
                extra_disabled={
                    "schedule_task": "定时任务 workflow 禁止递归调度",
                    "reply": "确定性工具步骤不能发送聊天回复",
                    "no_reply": "确定性工具步骤不使用回复合同",
                },
            )
    tool_plan.ensure_executable(name)

    from core.agent_runtime.request_scope import runtime_context_scope
    from core.tool_execution_policy import tool_execution_scope
    from core.tracing import RunTracer, new_trace_id
    from core.tracing_context import (
        reset_runtime_correlation,
        reset_trace_context,
        set_runtime_correlation,
        set_trace_context,
    )

    resolved_trace_id = str(trace_id or new_trace_id())
    run_handle = RunTracer.start_run(
        trace_id=resolved_trace_id,
        session_id=normalized_session_id,
        user_id=normalized_user_id,
        chat_type=chat_type,
        group_id=group_id,
        run_type="scheduled_tool",
        prompt_mode="workflow",
        prompt_key="",
        input_preview=f"{name} 直接工具步骤",
        meta={
            "tool_name": name,
            "workflow_idempotency_key": str(idempotency_key or ""),
            "tool_plan_sha256": tool_plan.sha256,
        },
    )
    trace_tokens = set_trace_context(
        resolved_trace_id,
        run_handle.run_id,
    )
    correlation_tokens = set_runtime_correlation(
        request_id=request_id,
        session_id=normalized_session_id,
        trace_id=resolved_trace_id,
        run_id=run_handle.run_id,
        task_run_id=str(meta.get("task_run_id") or ""),
    )
    runtime_context = {
        "chat_type": chat_type,
        "runtime_chat_type": runtime_chat_type,
        "is_group": is_group,
        "is_super_user": bool(meta.get("is_superuser")),
        "session_id": normalized_session_id,
        "group_id": group_id,
        "user_id": normalized_user_id,
        "platform": platform,
        "sender_name": "定时任务",
        "trace_id": resolved_trace_id,
        "run_id": run_handle.run_id,
        "message_id": request_id,
        "workflow_idempotency_key": str(idempotency_key or ""),
    }
    result: Any = None
    finish_status = "error"
    finish_error = ""
    try:
        with (
            runtime_context_scope(runtime_context),
            tool_plan_scope(tool_plan),
            tool_execution_scope(request_id),
        ):
            executor = agent.executor
            job_id = await executor.submit(
                name,
                dict(args),
                is_direct=True,
            )
            result = await executor.wait_for(
                job_id,
                timeout=float(timeout_seconds),
            )
            if result is None:
                await executor.cancel(job_id)
                raise TimeoutError("直接工具步骤执行超时")
        finish_error = str(getattr(result, "error", "") or "")
        exit_code = getattr(result, "exit_code", None)
        finish_status = (
            "success"
            if not finish_error and exit_code in {None, 0}
            else "error"
        )
    except BaseException as exc:
        finish_error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        RunTracer.finish_run(
            run_handle.run_id,
            status=finish_status,
            output_preview=getattr(result, "output", ""),
            error=finish_error,
            meta={
                "tool_name": name,
                "workflow_idempotency_key": str(
                    idempotency_key or ""
                ),
                "tool_plan_sha256": tool_plan.sha256,
            },
        )
        reset_runtime_correlation(correlation_tokens)
        reset_trace_context(trace_tokens)

    tool_call_id = ""
    try:
        from core.database import SessionLocal, ToolCall

        trace_db = SessionLocal()
        try:
            row = (
                trace_db.query(ToolCall.tool_call_id)
                .filter(
                    ToolCall.run_id == run_handle.run_id,
                    ToolCall.tool_name == name,
                )
                .order_by(
                    ToolCall.started_at.desc(),
                    ToolCall.tool_call_id.desc(),
                )
                .first()
            )
            if row is not None:
                tool_call_id = str(row[0] or "")
        finally:
            trace_db.close()
    except Exception:
        pass
    return DirectToolExecutionResult(
        output=getattr(result, "output", ""),
        error=str(getattr(result, "error", "") or ""),
        exit_code=getattr(result, "exit_code", None),
        metadata=dict(getattr(result, "metadata", {}) or {}),
        trace_id=resolved_trace_id,
        run_id=run_handle.run_id,
        tool_call_id=tool_call_id,
    )


__all__ = [
    "DirectToolExecutionResult",
    "execute_registered_tool",
]
