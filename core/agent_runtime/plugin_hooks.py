"""AgentRuntime 合同到受管 Plugin Hook 的共享适配。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping

from core.agent_runtime.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
    RuntimePlanKind,
    RuntimeRunEvent,
    RuntimeRunEventHandler,
)
from core.runtime.plugin_lifecycle import (
    RuntimeHookPoint,
    RuntimePluginManager,
)


def _runtime_input_event_projection(
    request: AgentTurnRequest,
) -> Mapping[str, object]:
    """只暴露事件形态，不把身份、计划或用户正文交给 Event Hook。"""

    return {
        "attribute_keys": tuple(
            attribute.key for attribute in request.event_attributes
        ),
        "content_type": type(request.content).__name__,
        "kind": request.kind.value,
        "stream": request.stream,
    }


def _runtime_output_event_projection(
    event: RuntimeRunEvent,
) -> Mapping[str, object]:
    """投影 Ledger 已提交事件，排除 identity、正文、参数和结果。"""

    tool_call = event.tool_call
    usage = event.usage
    artifact = event.artifact
    error = event.error
    context_decision = event.context_decision
    return {
        "artifact": (
            {
                "artifact_id": artifact.artifact_id,
                "media_type": artifact.media_type,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "version": artifact.version,
            }
            if artifact is not None
            else None
        ),
        "attribute_keys": tuple(
            attribute.key for attribute in event.attributes
        ),
        "context_decision": (
            {
                "action": context_decision.action,
                "after_messages": context_decision.after_messages,
                "after_tokens": context_decision.after_tokens,
                "before_messages": context_decision.before_messages,
                "before_tokens": context_decision.before_tokens,
                "cause_code": context_decision.cause_code,
                "quality_status": context_decision.quality_status,
            }
            if context_decision is not None
            else None
        ),
        "error": (
            {
                "code": error.code,
                "retryable": error.retryable,
            }
            if error is not None
            else None
        ),
        "event_id": event.event_id,
        "kind": event.kind.value,
        "occurred_at": event.occurred_at.isoformat(),
        "sequence": event.sequence,
        "status": event.status.value,
        "text_delta_chars": len(event.text_delta),
        "tool_call": (
            {
                "call_id": tool_call.call_id,
                "name": tool_call.name,
                "status": tool_call.status.value,
            }
            if tool_call is not None
            else None
        ),
        "usage": (
            {
                "cached_input_tokens": usage.cached_input_tokens,
                "cost_microunits": usage.cost_microunits,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
            }
            if usage is not None
            else None
        ),
    }


def _runtime_completion_projection(
    result: AgentTurnResult,
) -> Mapping[str, object]:
    """提供完成摘要，不暴露框架 raw_result、消息正文或工具参数／结果。"""

    return {
        "message_roles": tuple(message.role for message in result.messages),
        "raw_result_type": type(result.raw_result).__name__,
        "tool_calls": tuple(
            {
                "call_id": call.call_id,
                "name": call.name,
                "status": call.status.value,
            }
            for call in result.tool_calls
        ),
    }


def _plan_sha256(request: AgentTurnRequest, kind: RuntimePlanKind) -> str:
    reference = request.context.plan(kind)
    return reference.sha256 if reference is not None else ""


def runtime_hook_invariants(
    runtime_id: str,
    request: AgentTurnRequest | None,
    *,
    tool_plan_sha256: str = "",
) -> Mapping[str, object]:
    """只在宿主侧保存安全不变量，不把其真实结构投影给 Hook。"""

    if request is None:
        return {
            "identity": str(runtime_id),
            "tool_plan": str(tool_plan_sha256 or ""),
            "permission": "",
            "prompt_runtime": "",
            "event_ledger": "",
        }
    context = request.context
    tool_plan = str(tool_plan_sha256 or "") or _plan_sha256(
        request,
        RuntimePlanKind.TOOL,
    )
    return {
        "identity": (
            context.run_id,
            context.turn_id,
            context.principal.canonical_id,
        ),
        "tool_plan": tool_plan,
        "permission": _plan_sha256(request, RuntimePlanKind.SECURITY),
        "prompt_runtime": _plan_sha256(request, RuntimePlanKind.PROMPT),
        "event_ledger": (context.run_id, context.turn_id),
    }


async def dispatch_runtime_input_event(
    manager: RuntimePluginManager,
    request: AgentTurnRequest,
    *,
    tool_plan_sha256: str = "",
) -> None:
    if not manager.has_hooks(RuntimeHookPoint.EVENT):
        return
    await manager.dispatch(
        RuntimeHookPoint.EVENT,
        {
            "direction": "input",
            "event": _runtime_input_event_projection(request),
            "runtime_id": manager.runtime_id,
        },
        protected_invariants=runtime_hook_invariants(
            manager.runtime_id,
            request,
            tool_plan_sha256=tool_plan_sha256,
        ),
    )


async def dispatch_runtime_completion(
    manager: RuntimePluginManager,
    request: AgentTurnRequest,
    result: AgentTurnResult,
    *,
    tool_plan_sha256: str = "",
) -> None:
    if not manager.has_hooks(RuntimeHookPoint.COMPLETION):
        return
    await manager.dispatch(
        RuntimeHookPoint.COMPLETION,
        {
            "message_count": len(result.messages),
            "result": _runtime_completion_projection(result),
            "runtime_id": manager.runtime_id,
            "tool_call_count": len(result.tool_calls),
        },
        protected_invariants=runtime_hook_invariants(
            manager.runtime_id,
            request,
            tool_plan_sha256=tool_plan_sha256,
        ),
    )


def ledger_first_runtime_event_handler(
    manager: RuntimePluginManager,
    request: AgentTurnRequest,
    handler: RuntimeRunEventHandler,
    *,
    tool_plan_sha256: str = "",
) -> RuntimeRunEventHandler:
    """先提交调用方 Event Ledger，再运行不可改写的 Event Hook。"""

    if not manager.has_hooks(RuntimeHookPoint.EVENT):
        return handler

    async def handle(event: RuntimeRunEvent) -> None:
        handled = handler(event)
        if inspect.isawaitable(handled):
            await handled
        await manager.dispatch(
            RuntimeHookPoint.EVENT,
            {
                "direction": "output",
                "event": _runtime_output_event_projection(event),
                "runtime_id": manager.runtime_id,
            },
            protected_invariants=runtime_hook_invariants(
                manager.runtime_id,
                request,
                tool_plan_sha256=tool_plan_sha256,
            ),
        )

    return handle


def dispatch_runtime_interrupt_nowait(
    manager: RuntimePluginManager,
    *,
    reason: str,
    request: AgentTurnRequest | None,
    tool_plan_sha256: str = "",
) -> None:
    if not manager.has_hooks(RuntimeHookPoint.INTERRUPT):
        return
    manager.dispatch_nowait(
        RuntimeHookPoint.INTERRUPT,
        {
            "reason": str(reason or ""),
            "runtime_id": manager.runtime_id,
        },
        protected_invariants=runtime_hook_invariants(
            manager.runtime_id,
            request,
            tool_plan_sha256=tool_plan_sha256,
        ),
    )


__all__ = [
    "dispatch_runtime_completion",
    "dispatch_runtime_input_event",
    "dispatch_runtime_interrupt_nowait",
    "ledger_first_runtime_event_handler",
    "runtime_hook_invariants",
]
