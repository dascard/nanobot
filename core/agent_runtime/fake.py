"""不依赖 Agent 框架的端口测试替身。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from collections import deque
from dataclasses import replace

from core.agent_runtime.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
    RuntimeLifecycleEvent,
    RuntimeLifecycleEventSink,
    RuntimeLifecycleState,
    RuntimeCapabilities,
    RuntimeCapability,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimePendingStateReset,
    RuntimeRunError,
    RuntimeRunEvent,
    RuntimeRunEventHandler,
    RuntimeRunStatus,
    RuntimeToolCall,
    RuntimeToolPolicyStatus,
)
from core.agent_runtime.event_stream import (
    RuntimeRunEventEmitter,
    relay_runtime_run_events,
)
from core.agent_runtime.lifecycle import RuntimeLifecycleMachine


class FakeAgentRuntime:
    """可记录交互、可预设输出的确定性 Agent Runtime。"""

    def __init__(
        self,
        *,
        runtime_id: str = "fake-agent-runtime",
        event_sinks: tuple[RuntimeLifecycleEventSink, ...] = (),
    ) -> None:
        self._lifecycle = RuntimeLifecycleMachine(
            runtime_id,
            event_sinks=event_sinks,
        )
        self.requests: list[AgentTurnRequest] = []
        self.routes: list[RuntimeModelRoute] = []
        self.interrupt_reasons: list[str] = []
        self.tool_names: tuple[str, ...] = ()
        self._messages: tuple[RuntimeMessage, ...] = ()
        self._tool_calls: tuple[RuntimeToolCall, ...] = ()
        self._queued_results: deque[AgentTurnResult] = deque()
        self._queued_text_deltas: deque[tuple[str, ...]] = deque()

    @property
    def runtime_id(self) -> str:
        return self._lifecycle.runtime_id

    @property
    def state(self) -> RuntimeLifecycleState:
        return self._lifecycle.state

    @property
    def lifecycle_events(self) -> tuple[RuntimeLifecycleEvent, ...]:
        return self._lifecycle.events

    @property
    def runtime_capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            runtime_id=self.runtime_id,
            supported=frozenset(RuntimeCapability),
        )

    def queue_result(self, result: AgentTurnResult) -> None:
        self._queued_results.append(result)

    def queue_text_deltas(self, *deltas: str) -> None:
        """为下一次事件化调用预设确定性的真实增量。"""

        normalized = tuple(str(delta) for delta in deltas if str(delta))
        self._queued_text_deltas.append(normalized)

    async def start(self) -> None:
        self._lifecycle.ensure(RuntimeLifecycleState.NEW)
        self._lifecycle.transition(RuntimeLifecycleState.STARTING)
        self._lifecycle.transition(RuntimeLifecycleState.RUNNING)

    async def stop(self) -> None:
        if self.state is RuntimeLifecycleState.STOPPED:
            return
        if self.state is RuntimeLifecycleState.NEW:
            self._lifecycle.transition(RuntimeLifecycleState.STOPPED)
            return
        self._lifecycle.ensure(
            RuntimeLifecycleState.RUNNING,
            RuntimeLifecycleState.FAILED,
        )
        self._lifecycle.transition(RuntimeLifecycleState.STOPPING)
        self._lifecycle.transition(RuntimeLifecycleState.STOPPED)

    async def run(self, request: AgentTurnRequest) -> AgentTurnResult:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        self.requests.append(request)
        if self._queued_results:
            result = self._queued_results.popleft()
        else:
            result = AgentTurnResult(
                raw_result=None,
                messages=self._messages + (RuntimeMessage("user", request.content),),
            )
        self._messages = result.messages
        self._tool_calls = result.tool_calls
        return result

    def run_stream(
        self,
        request: AgentTurnRequest,
    ) -> AsyncIterator[RuntimeRunEvent]:
        stream_request = replace(request, stream=True)
        return relay_runtime_run_events(
            lambda handler: self.run_event(stream_request, handler)
        )

    async def run_event(
        self,
        request: AgentTurnRequest,
        handler: RuntimeRunEventHandler,
    ) -> AgentTurnResult:
        emitter = RuntimeRunEventEmitter(
            request.context.execution_identity(),
            handler,
        )
        deltas = (
            self._queued_text_deltas.popleft()
            if self._queued_text_deltas
            else ()
        )
        await emitter.status_changed(RuntimeRunStatus.ACCEPTED)
        await emitter.status_changed(RuntimeRunStatus.RUNNING)
        try:
            result = await self.run(request)
        except asyncio.CancelledError:
            await emitter.end(RuntimeRunStatus.CANCELLED)
            raise
        except TimeoutError as exc:
            await emitter.error(
                RuntimeRunError(
                    code="runtime_timeout",
                    message=str(exc) or "Runtime 执行超时",
                    retryable=True,
                )
            )
            await emitter.end(RuntimeRunStatus.TIMED_OUT)
            raise
        except Exception as exc:
            await emitter.error(
                RuntimeRunError(
                    code="runtime_execution_error",
                    message=str(exc) or type(exc).__name__,
                )
            )
            await emitter.end(RuntimeRunStatus.FAILED)
            raise

        for delta in deltas:
            await emitter.text_delta(delta)
        for tool_call in result.tool_calls:
            await emitter.tool_activity(tool_call)
        await emitter.end(RuntimeRunStatus.SUCCEEDED)
        return result

    async def execute_turn(self, request: AgentTurnRequest) -> AgentTurnResult:
        """旧版兼容 façade；不产生类型化事件。"""

        return await self.run(request)

    def replace_conversation(self, messages: tuple[RuntimeMessage, ...]) -> int:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        self._messages = tuple(messages)
        self._tool_calls = tuple(
            tool_call for message in self._messages for tool_call in message.tool_calls
        )
        return len(self._messages)

    def read_conversation(self) -> tuple[RuntimeMessage, ...]:
        return self._messages

    def clear_pending_events(self) -> RuntimePendingStateReset:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        return RuntimePendingStateReset()

    def install_tool_policy(self) -> RuntimeToolPolicyStatus:
        self._lifecycle.ensure(
            RuntimeLifecycleState.NEW,
            RuntimeLifecycleState.RUNNING,
        )
        return RuntimeToolPolicyStatus(
            ready=True,
            guard_installed=True,
            schema_filter_installed=True,
        )

    def set_model_route(self, route: RuntimeModelRoute) -> None:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        self.routes.append(route)

    def inspect_tool_calls(self) -> tuple[RuntimeToolCall, ...]:
        return self._tool_calls

    def list_tool_names(self) -> tuple[str, ...]:
        return self.tool_names

    def interrupt(self, *, reason: str = "") -> bool:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        self.interrupt_reasons.append(str(reason or ""))
        return True
