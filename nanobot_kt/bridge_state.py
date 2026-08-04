"""Nanobot Bridge 的请求状态与结果合同。

本模块只承载数据合同和 Trace 收尾逻辑，避免生命周期 Bridge 同时承担
请求状态定义。``nanobot_kt.bridge`` 会重新导出这些名称，保留现有调用方
和测试的导入路径。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nanobot_kt.model_attempts import AttemptOutcome
from nanobot_kt.reply_contract import RichTerminalOutput


logger = logging.getLogger("nanobot.kt.bridge")


def build_bridge_run_meta(
    meta: dict[str, Any],
    *,
    sender_name: str,
    is_group: bool,
    prompt_engine: str,
    platform: str,
    chat_type: str,
) -> dict[str, Any]:
    """构造 Trace 与 Durable Task 共用的有界请求元数据。"""

    return {
        "sender_name": sender_name,
        "is_group": is_group,
        "message_id": meta.get("message_id", ""),
        "prompt_engine": prompt_engine,
        "platform": platform,
        "chat_type": chat_type,
        "source": meta.get("source", ""),
        "runtime_preset": meta.get("runtime_preset", ""),
        "task_run_id": meta.get("task_run_id", ""),
        "workflow_idempotency_key": meta.get("workflow_idempotency_key", ""),
        "run_timeout_seconds": meta.get("run_timeout_seconds", ""),
    }


async def bind_run_task_owner(request_scope: Any, task_lease: Any) -> None:
    """启动 Agent Run heartbeat，并把停止动作绑定到请求清理。"""

    from core.durable_tasks import RunTaskOwner

    owner = RunTaskOwner(task_lease)
    await owner.start()
    request_scope.bind_async_cleanup(owner.stop)


@dataclass(frozen=True)
class PromptRuntimeAssemblyContext:
    prompt_engine: str
    prompt_mode: str
    prompt_key: str
    chat_type: str
    runtime_chat_type: str
    session_id: str
    user_id: str
    group_id: str
    sender_name: str
    query: str
    persona_text: str
    history_header: str
    history_messages: list[dict[str, Any]]
    runtime_tool_prompt: str
    effort_constraint: str
    trace_id: str
    run_id: str
    is_group: bool
    meta: dict[str, Any]
    tool_plan: Any
    session_guidance: str = field(default="", repr=False)
    session_guidance_chat_stream_id: str = ""
    session_guidance_resolution_status: str = "not_requested"
    is_super_user: bool = False
    platform: str = "qq"


@dataclass
class BridgeRuntimeToolState:
    persona_text: str
    history_messages: Any
    history_header: str
    is_group: bool
    effort_constraint: str
    runtime_preset: str
    chat_type: str
    runtime_chat_type: str
    group_id: str
    user_id: str
    platform: str
    tool_plan: Any
    runtime_tool_prompt: str
    effective_tools: list[str]
    final_tools_token: Any
    tool_plan_token: Any


@dataclass
class BridgeEventPayload:
    event_content: Any
    image_parts: list[Any]
    required_capabilities: dict[str, bool]


@dataclass
class ModelLoopResult:
    response: str
    result: Any
    target_model: str
    terminal_output: RichTerminalOutput | None
    selected_candidate: dict[str, Any] | None
    attempts: int
    health_status: AttemptOutcome


@dataclass
class ReplyResolution:
    response: str
    agent_result: str
    no_reply: bool
    no_tool_call: bool
    output_preview: str
    finish_status: str
    error: str = ""


@dataclass
class BridgeTraceFinalizer:
    run_id: str
    trace_tokens: Any
    run_meta: dict[str, Any]
    started_at: float
    now: Any
    task_lease: Any = None
    correlation_tokens: Any = None
    final_tools_token: Any = None
    tool_plan_token: Any = None
    tool_execution_token: Any = None
    closed: bool = False

    def set_tool_tokens(
        self,
        *,
        final_tools_token: Any = None,
        tool_plan_token: Any = None,
    ) -> None:
        if final_tools_token is not None:
            self.final_tools_token = final_tools_token
        if tool_plan_token is not None:
            self.tool_plan_token = tool_plan_token

    def set_tool_execution_token(self, token: Any) -> None:
        self.tool_execution_token = token

    def finish(
        self,
        status: str,
        *,
        output_preview: str = "",
        error: str = "",
        model: str = "",
    ) -> None:
        if self.closed:
            return
        self.closed = True
        authority_failure: Exception | None = None

        def run_step(
            label: str,
            callback: Callable[[], None],
            *,
            authoritative: bool = False,
        ) -> None:
            nonlocal authority_failure
            try:
                callback()
            except Exception as exc:
                logger.error(
                    "Bridge trace cleanup step %s failed: %s",
                    label,
                    exc,
                    exc_info=True,
                )
                if authoritative and authority_failure is None:
                    authority_failure = exc

        def finish_run() -> None:
            from core.tracing import RunTracer

            RunTracer.finish_run(
                self.run_id,
                task_lease=self.task_lease,
                status=status,
                output_preview=output_preview,
                error=error,
                latency_ms=int((self.now() - self.started_at) * 1000),
                model=model,
                meta=self.run_meta,
            )

        run_step("finish_run", finish_run, authoritative=True)
        if self.tool_plan_token is not None:
            tool_plan_token = self.tool_plan_token
            self.tool_plan_token = None

            def reset_tool_plan() -> None:
                from core.tool_plan import reset_current_tool_plan

                reset_current_tool_plan(tool_plan_token)

            run_step("reset_tool_plan", reset_tool_plan)
        if self.final_tools_token is not None:
            final_tools_token = self.final_tools_token
            self.final_tools_token = None

            def reset_final_tools() -> None:
                from core.final_tools import reset_current_final_tools

                reset_current_final_tools(final_tools_token)

            run_step("reset_final_tools", reset_final_tools)
        if self.tool_execution_token is not None:
            tool_execution_token = self.tool_execution_token
            self.tool_execution_token = None

            def reset_tool_execution() -> None:
                from core.tool_execution_policy import (
                    reset_current_tool_execution_state,
                )

                reset_current_tool_execution_state(tool_execution_token)

            run_step("reset_tool_execution", reset_tool_execution)
        if self.correlation_tokens is not None:
            correlation_tokens = self.correlation_tokens
            self.correlation_tokens = None

            def reset_correlation() -> None:
                from core.tracing_context import reset_runtime_correlation

                reset_runtime_correlation(correlation_tokens)

            run_step("reset_correlation", reset_correlation)
        if self.trace_tokens is not None:
            trace_tokens = self.trace_tokens
            self.trace_tokens = None

            def reset_trace() -> None:
                from core.tracing_context import reset_trace_context

                reset_trace_context(trace_tokens)

            run_step("reset_trace", reset_trace)
        if authority_failure is not None:
            raise authority_failure


__all__ = [
    "BridgeEventPayload",
    "BridgeRuntimeToolState",
    "BridgeTraceFinalizer",
    "ModelLoopResult",
    "PromptRuntimeAssemblyContext",
    "ReplyResolution",
    "bind_run_task_owner",
    "build_bridge_run_meta",
]
