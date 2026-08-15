"""不经过模型的 KT 已注册工具执行入口。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
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


def _build_tool_execution_request(
    *,
    request_id: str,
    platform: str,
    normalized_session_id: str,
    normalized_user_id: str,
    group_id: str,
    is_group: bool,
    trace_id: str,
    run_id: str,
    name: str,
    args: dict[str, Any],
    execution_port_id: str,
    idempotency_key: str,
    tool_plan_sha256: str,
    timeout_seconds: float,
    governance: Any,
    agent_id: str,
):
    from core.agent_runtime import (
        RequestRuntimeContext,
        RuntimeActor,
        RuntimeActorType,
        RuntimeChatType,
        RuntimeOwnerType,
        RuntimePlanKind,
        RuntimePlanRef,
        RuntimePrincipal,
        RuntimeToolCall,
        RuntimeToolExecutionRequest,
    )

    owner_id = (
        group_id or normalized_session_id
        if is_group
        else normalized_user_id
    )
    return RuntimeToolExecutionRequest(
        context=RequestRuntimeContext(
            request_id=request_id,
            agent_id=agent_id,
            principal=RuntimePrincipal(
                platform=platform,
                owner_type=(
                    RuntimeOwnerType.GROUP
                    if is_group
                    else RuntimeOwnerType.USER
                ),
                owner_id=owner_id,
            ),
            session_id=normalized_session_id,
            chat_type=(
                RuntimeChatType.GROUP
                if is_group
                else RuntimeChatType.PRIVATE
            ),
            trace_id=trace_id,
            run_id=run_id,
            turn_id=request_id,
            correlation_id=request_id,
            actor=RuntimeActor(
                RuntimeActorType.SYSTEM,
                "scheduled-task",
                parent_actor_id=normalized_user_id,
            ),
            message_id=request_id,
            capabilities=frozenset({"tools"}),
            plans=(
                RuntimePlanRef(
                    RuntimePlanKind.TOOL,
                    f"tool-plan:{tool_plan_sha256}",
                    tool_plan_sha256,
                ),
            ),
            deadline_at=(
                datetime.now(timezone.utc)
                + timedelta(seconds=float(timeout_seconds))
            ),
            governance=governance,
        ),
        tool_call=RuntimeToolCall(
            call_id=f"tool-{uuid4().hex}",
            name=name,
            arguments=dict(args),
        ),
        execution_port_id=execution_port_id,
        idempotency_key=str(idempotency_key or request_id),
        timeout_seconds=float(timeout_seconds),
    )


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

    from core.agent_runtime import AgentRuntimeKind

    runtime_kind = getattr(bridge, "runtime_kind", AgentRuntimeKind.KT)
    agent = getattr(bridge, "_agent", None)
    if runtime_kind is AgentRuntimeKind.KT and agent is None:
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
    from core.run_ledger.contracts import RunTriggerBinding

    trigger_binding = meta.pop("_trigger_run_binding", None)
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
    from core.tool_registration import TOOL_REGISTRATION_REGISTRY

    registration = TOOL_REGISTRATION_REGISTRY.get(name)
    execution_binding = (
        registration.execution_binding
        if registration is not None
        else None
    )
    if execution_binding is None:
        raise RuntimeError(f"工具 {name} 缺少确定性 execution binding")

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
    run_meta = {
        "platform": platform,
        "message_id": request_id,
        "tool_name": name,
        "task_run_id": str(meta.get("task_run_id") or ""),
        "workflow_idempotency_key": str(idempotency_key or ""),
        "run_timeout_seconds": timeout_seconds,
        "tool_plan_sha256": tool_plan.sha256,
    }
    if isinstance(trigger_binding, RunTriggerBinding):
        run_meta["_trigger_run_binding"] = trigger_binding
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
        meta=run_meta,
    )
    trigger_trace_meta = (
        {
            "trigger_id": trigger_binding.trigger_id,
            "trigger_type": trigger_binding.trigger_type,
            "trigger_sha256": trigger_binding.trigger_sha256,
            "governance_sha256": trigger_binding.governance_sha256,
        }
        if isinstance(trigger_binding, RunTriggerBinding)
        else {}
    )
    from core.durable_tasks import RunTaskOwner

    run_task_owner = RunTaskOwner(run_handle.task_lease)
    await run_task_owner.start()
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
        "turn_id": request_id,
        "correlation_id": request_id,
        "actor_type": "system",
        "actor_id": "scheduled-task",
        "actor_parent_id": normalized_user_id,
        "owner_type": "group" if is_group else "user",
        "owner_id": (
            group_id or normalized_session_id
            if is_group
            else normalized_user_id
        ),
        "message_id": request_id,
        "workflow_idempotency_key": str(idempotency_key or ""),
    }
    if runtime_kind is AgentRuntimeKind.NATIVE:
        from bootstrap.native_tool_runtime import (
            build_native_tool_execution_port,
        )

        execution_port = build_native_tool_execution_port()
    else:
        from nanobot_kt.tool_execution_adapter import (
            KtRegisteredToolExecutionAdapter,
        )

        execution_port = KtRegisteredToolExecutionAdapter(agent)
    from core.permissions import default_session_permission_port
    from nanobot_kt.runtime_context_adapter import (
        build_request_runtime_governance,
    )

    governance = build_request_runtime_governance(
        tool_plan=tool_plan,
        skill_plan=None,
    )
    permission_port = default_session_permission_port()
    result: Any = None
    finish_status = "error"
    finish_error = ""
    try:
        tool_request = _build_tool_execution_request(
            request_id=request_id,
            platform=platform,
            normalized_session_id=normalized_session_id,
            normalized_user_id=normalized_user_id,
            group_id=group_id,
            is_group=is_group,
            trace_id=resolved_trace_id,
            run_id=run_handle.run_id,
            name=name,
            args=args,
            execution_port_id=execution_binding.port_id,
            idempotency_key=idempotency_key,
            tool_plan_sha256=tool_plan.sha256,
            timeout_seconds=timeout_seconds,
            governance=governance,
            agent_id=str(getattr(bridge, "_agent_id", "") or "nanobot"),
        )
        from core.permissions import authorize_tool_execution
        from core import database
        from core.agent_runtime import RuntimeBudgetManager
        from core.run_ledger.sinks import SqlAlchemyRuntimeBudgetDecisionSink

        budget = RuntimeBudgetManager(
            sink=SqlAlchemyRuntimeBudgetDecisionSink(
                lambda: database.SessionLocal()
            )
        ).bind(
            tool_request.context.execution_identity(),
            tool_request.context.governance,
        )

        await authorize_tool_execution(
            permission_port,
            context=tool_request.context,
            tool_name=name,
            tool_call_id=tool_request.tool_call.call_id,
        )
        reservation = budget.reserve_tool(name)
        try:
            tool_request = replace(
                tool_request,
                timeout_seconds=min(
                    tool_request.timeout_seconds,
                    budget.tool_timeout_seconds(),
                ),
            )
            with (
                runtime_context_scope(runtime_context),
                tool_plan_scope(tool_plan),
                tool_execution_scope(request_id),
            ):
                result = await execution_port.execute(tool_request)
        finally:
            budget.release(reservation)
        finish_error = (
            result.error.message if result.error is not None else ""
        )
        exit_code = result.exit_code
        finish_status = (
            "success"
            if not finish_error and exit_code in {None, 0}
            else "error"
        )
    except asyncio.CancelledError as exc:
        from core.durable_tasks import durable_cancel_status

        finish_status = durable_cancel_status(exc)
        finish_error = f"{type(exc).__name__}: {exc}"
        raise
    except BaseException as exc:
        finish_error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            await run_task_owner.stop()
            RunTracer.finish_run(
                run_handle.run_id,
                task_lease=run_task_owner.lease,
                status=finish_status,
                output_preview=(
                    result.output if result is not None else ""
                ),
                error=finish_error,
                meta={
                    "tool_name": name,
                    "workflow_idempotency_key": str(
                        idempotency_key or ""
                    ),
                    "tool_plan_sha256": tool_plan.sha256,
                    **trigger_trace_meta,
                },
            )
        finally:
            reset_runtime_correlation(correlation_tokens)
            reset_trace_context(trace_tokens)

    return DirectToolExecutionResult(
        output=result.output,
        error=(result.error.message if result.error is not None else ""),
        exit_code=result.exit_code,
        metadata=dict(result.metadata),
        trace_id=resolved_trace_id,
        run_id=run_handle.run_id,
        tool_call_id=result.tool_call_id,
    )


__all__ = [
    "DirectToolExecutionResult",
    "execute_registered_tool",
]
