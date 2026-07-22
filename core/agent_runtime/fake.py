"""不依赖 Agent 框架的端口测试替身。"""

from __future__ import annotations

from collections import deque

from core.agent_runtime.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
    RuntimeLifecycleEvent,
    RuntimeLifecycleEventSink,
    RuntimeLifecycleState,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimePendingStateReset,
    RuntimeToolCall,
    RuntimeToolPolicyStatus,
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

    @property
    def runtime_id(self) -> str:
        return self._lifecycle.runtime_id

    @property
    def state(self) -> RuntimeLifecycleState:
        return self._lifecycle.state

    @property
    def lifecycle_events(self) -> tuple[RuntimeLifecycleEvent, ...]:
        return self._lifecycle.events

    def queue_result(self, result: AgentTurnResult) -> None:
        self._queued_results.append(result)

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

    async def execute_turn(self, request: AgentTurnRequest) -> AgentTurnResult:
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
