"""统一定时任务 worker 到 KT、Agent Link 与 outbox 的生产适配器。"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from core.scheduled_task_outbound import (
    enqueue_frozen_scheduled_task_content,
)
from core.scheduled_workflow import (
    ScheduledWorkflowContext,
    ScheduledWorkflowStepOutcome,
)
from core.run_ledger.contracts import RunTriggerBinding
from core.trigger_runtime import TriggerToolConstraint
from core.tool_execution_policy import extract_tool_failure


def _render_tool_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        from kohakuterrarium.core.tool_output import render_content_text

        return render_content_text(value)
    except Exception:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )


def _workflow_tool_output(result: Any) -> Any:
    """优先保留工具的稳定结构化信封，供条件和循环直接读取。"""

    metadata = getattr(result, "metadata", None)
    structured = (
        metadata.get("structured_content")
        if isinstance(metadata, dict)
        else None
    )
    if structured is not None:
        try:
            # 防止 Mapping 子类、NaN 或不可序列化对象进入持久 checkpoint。
            return json.loads(
                json.dumps(
                    structured,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return _render_tool_output(getattr(result, "output", result))


def _trigger_run_binding(
    context: ScheduledWorkflowContext,
) -> RunTriggerBinding | None:
    envelope = context.trigger_envelope
    if envelope is None:
        return None
    return envelope.run_binding()


def _trigger_tool_constraint(
    context: ScheduledWorkflowContext,
) -> TriggerToolConstraint | None:
    envelope = context.trigger_envelope
    if envelope is None:
        return None
    return envelope.tool_constraint(context.model_tool_names)


class KtScheduledWorkflowCallbacks:
    """生产 callback：model 启动完整 Agent Run，emit 只提交 outbox。"""

    def __init__(self, *, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def execute_tool(
        self,
        context: ScheduledWorkflowContext,
        *,
        tool_name: str,
        args: dict[str, Any],
        idempotency_key: str,
    ) -> ScheduledWorkflowStepOutcome:
        from core.agent_runtime.gateway import create_isolated_agent_gateway
        from core.tool_plan import ToolPlanExecutionError
        from core.tracing import new_trace_id
        from core.daily_digest import (
            _scheduled_task_metadata,
            _scheduled_task_session_id,
        )

        bridge = create_isolated_agent_gateway()
        additional_schemas: tuple[dict[str, Any], ...] = ()
        try:
            await bridge.start()
            agent = getattr(bridge, "agent", None)
            registry = getattr(agent, "registry", None)
            if registry is None:
                return ScheduledWorkflowStepOutcome.failed(
                    "tool_registry_unavailable",
                    "KT 工具注册表不可用",
                    blocked=True,
                )
            if registry.get_tool(tool_name) is None:
                dynamic = await self._install_agent_link_tool(
                    bridge,
                    context,
                    tool_name,
                )
                if isinstance(dynamic, ScheduledWorkflowStepOutcome):
                    return dynamic
                additional_schemas = dynamic

            metadata = _scheduled_task_metadata(context.task_snapshot)
            metadata.update({
                "request_id": idempotency_key,
                "task_run_id": str(context.execution_id),
                "workflow_idempotency_key": idempotency_key,
                "runtime_preset": "full",
            })
            trigger_binding = _trigger_run_binding(context)
            if trigger_binding is not None:
                metadata["_trigger_run_binding"] = trigger_binding
            try:
                result = await bridge.execute_registered_tool(
                    tool_name,
                    args,
                    user_id=str(metadata.get("user_id") or ""),
                    session_id=_scheduled_task_session_id(
                        context.task_snapshot
                    ),
                    metadata=metadata,
                    idempotency_key=idempotency_key,
                    trace_id=new_trace_id(),
                    additional_tool_schemas=additional_schemas,
                )
            except ToolPlanExecutionError as exc:
                return ScheduledWorkflowStepOutcome.failed(
                    "tool_not_authorized",
                    str(exc),
                    blocked=True,
                )

            failure = extract_tool_failure(result)
            if failure is not None:
                return ScheduledWorkflowStepOutcome.failed(
                    failure.code,
                    failure.summary,
                    retryable=failure.retryable,
                    blocked=(
                        failure.stop
                        or failure.code
                        in {
                            "authorization_failed",
                            "sandbox_not_enabled",
                            "asset_not_authorized",
                        }
                    ),
                    stop=failure.stop,
                    tool_call_id=result.tool_call_id,
                    agent_run_id=result.run_id,
                )
            if not result.success:
                code = "tool_execution_failed"
                retryable = bool(result.metadata.get("retryable", False))
                try:
                    payload = json.loads(result.error)
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, dict):
                    code = str(payload.get("code") or code)
                    retryable = bool(
                        payload.get("retryable", retryable)
                    )
                return ScheduledWorkflowStepOutcome.failed(
                    code,
                    "任务工具步骤执行失败",
                    retryable=retryable,
                    blocked=code in {"OFFLINE", "TOOL_UNAVAILABLE"},
                )
            return ScheduledWorkflowStepOutcome(
                output=_workflow_tool_output(result),
                tool_call_id=result.tool_call_id,
                agent_run_id=result.run_id,
            )
        except Exception as exc:
            # callback 是否可安全重试由 program 中 tool.recovery 决定；这里不
            # 猜测异常发生在发送副作用之前还是之后。
            raise RuntimeError(
                f"KT 直接工具执行异常: {type(exc).__name__}"
            ) from exc
        finally:
            try:
                await bridge.stop()
            except Exception:
                pass

    async def _install_agent_link_tool(
        self,
        bridge: Any,
        context: ScheduledWorkflowContext,
        tool_name: str,
    ) -> tuple[dict[str, Any], ...] | ScheduledWorkflowStepOutcome:
        from core.agent_link.runtime import get_agent_link_runtime
        from nanobot_kt.agent_link_adapter import KtAgentLinkChatAdapter
        from nanobot_kt.agent_link_tools import build_agent_link_tools

        snapshot = context.task_snapshot
        runtime = get_agent_link_runtime()
        peer = await runtime.current_peer_for_bridge_session_id(
            snapshot.owner_session_id,
            platform_id=snapshot.owner_platform,
        )
        if peer is None:
            return ScheduledWorkflowStepOutcome.failed(
                "OFFLINE",
                "前端能力连接当前离线，操作未执行",
                retryable=True,
                blocked=True,
            )
        definition = next(
            (
                item
                for item in peer.tool_definitions()
                if item.name == tool_name
            ),
            None,
        )
        if definition is None:
            return ScheduledWorkflowStepOutcome.failed(
                "TOOL_UNAVAILABLE",
                "当前前端没有注册该工具",
                blocked=True,
            )
        tools = build_agent_link_tools(
            peer.key,
            (definition,),
            runtime=runtime,
        )
        KtAgentLinkChatAdapter._install_runtime_tools(bridge, tools)
        return (definition.wire_schema(),)

    async def execute_model(
        self,
        context: ScheduledWorkflowContext,
        *,
        prompt: str,
        idempotency_key: str,
    ) -> ScheduledWorkflowStepOutcome:
        from core.daily_digest import _generate_task_message
        from core.tracing import new_trace_id

        trace_id = new_trace_id()
        # program 中当前 model prompt 是冻结事实；不能回读已修改的 live task。
        step_snapshot = replace(
            context.task_snapshot,
            prompt_template=prompt,
        )
        generation_kwargs: dict[str, Any] = {
            "trace_id": trace_id,
            "workflow_idempotency_key": idempotency_key,
            "task_run_id": str(context.execution_id),
        }
        trigger_constraint = _trigger_tool_constraint(context)
        if trigger_constraint is not None:
            generation_kwargs["trigger_constraint"] = trigger_constraint
        content = await _generate_task_message(
            step_snapshot,
            **generation_kwargs,
        )
        agent_run_id = ""
        agent_status = ""
        db = self._session_factory()
        try:
            from core.database import AgentRun

            row = (
                db.query(AgentRun.run_id, AgentRun.status)
                .filter(AgentRun.trace_id == trace_id)
                .order_by(AgentRun.started_at.desc())
                .first()
            )
            if row is not None:
                agent_run_id = str(row[0] or "")
                agent_status = str(row[1] or "")
        finally:
            db.close()
        if agent_status in {"no_reply", "suppressed"}:
            return ScheduledWorkflowStepOutcome(
                output={"status": agent_status},
                model_trace_id=trace_id,
                agent_run_id=agent_run_id,
                stop=True,
            )
        if not content:
            return ScheduledWorkflowStepOutcome.failed(
                "empty_model_output",
                "模型没有生成可用内容",
                retryable=False,
                model_trace_id=trace_id,
                agent_run_id=agent_run_id,
            )
        return ScheduledWorkflowStepOutcome(
            output=str(content),
            model_trace_id=trace_id,
            agent_run_id=agent_run_id,
        )

    async def emit(
        self,
        context: ScheduledWorkflowContext,
        *,
        content: str,
        idempotency_key: str,
        model_trace_id: str,
    ) -> ScheduledWorkflowStepOutcome:
        db = self._session_factory()
        try:
            result = await enqueue_frozen_scheduled_task_content(
                db,
                snapshot=context.task_snapshot,
                occurrence=context.occurrence,
                content=content,
                model_trace_id=model_trace_id,
                session_factory=self._session_factory,
                trigger_type=(
                    "manual"
                    if context.trigger_type == "manual"
                    else "cron"
                ),
            )
        finally:
            db.close()
        if result.status in {"queued", "pending", "delivered"}:
            return ScheduledWorkflowStepOutcome(
                output={
                    "status": result.status,
                    "run_id": result.run_id,
                    "outbox_id": result.outbox_id,
                    "deduplicated": result.deduplicated,
                },
                outbound_run_id=result.run_id,
                model_trace_id=model_trace_id,
            )
        return ScheduledWorkflowStepOutcome.failed(
            f"outbound_{result.status}",
            "任务内容未能安全提交到投递队列",
            retryable=result.status in {"retry_wait", "delivering"},
            ambiguous=result.status == "ambiguous",
            blocked=result.status == "blocked",
        )


__all__ = ["KtScheduledWorkflowCallbacks"]
