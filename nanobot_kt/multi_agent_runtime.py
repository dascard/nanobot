"""多个 creature Agent 的进程级 BridgePool 所有权边界。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import Enum
from typing import Any

from core.agent_runtime import AgentRuntimeSelectionPolicy
from core.agent_runtime.errors import (
    AgentRuntimeNotFoundError,
    AgentRuntimeStateError,
)
from core.agent_runtime.registry import (
    AgentRuntimeDescriptor,
    AgentRuntimeRegistration,
    AgentRuntimeRegistry,
)
from nanobot_kt.agent_catalog import CreatureAgentSpec


class MultiAgentRuntimeState(str, Enum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class NanobotAgentRuntimeManager:
    """每个 Agent 一个 Pool，共享同一冻结 Runtime 选择策略。"""

    def __init__(
        self,
        specs: Iterable[CreatureAgentSpec],
        *,
        selection_policy: AgentRuntimeSelectionPolicy,
        pool_factory: Callable[..., Any] | None = None,
    ) -> None:
        materialized = tuple(specs)
        if not materialized:
            raise ValueError("至少需要一个 Agent spec")
        if any(not isinstance(item, CreatureAgentSpec) for item in materialized):
            raise TypeError("specs 必须全部是 CreatureAgentSpec")
        ids = [item.agent_id for item in materialized]
        if len(ids) != len(set(ids)):
            raise ValueError("Agent spec 不能重复")
        defaults = [item.agent_id for item in materialized if item.default]
        if len(defaults) != 1:
            raise ValueError("必须且只能声明一个默认 Agent")
        if not isinstance(selection_policy, AgentRuntimeSelectionPolicy):
            raise TypeError("selection_policy 必须是 AgentRuntimeSelectionPolicy")
        if pool_factory is None:
            from nanobot_kt.bridge import NanobotBridgePool

            pool_factory = NanobotBridgePool
        if not callable(pool_factory):
            raise TypeError("pool_factory 必须可调用")

        self._specs = {item.agent_id: item for item in materialized}
        self._ordered_ids = tuple(ids)
        self._default_agent_id = defaults[0]
        self._selection_policy = selection_policy
        self._pools = {
            item.agent_id: pool_factory(
                creature_path=item.creature_path,
                agent_id=item.agent_id,
                agent_profile=item.profile,
                allowed_tool_names=item.allowed_tool_names,
                allow_dynamic_tools=item.allow_dynamic_tools,
                model_profile_id=item.model_profile_id,
                selection_policy=selection_policy,
            )
            for item in materialized
        }
        self._state = MultiAgentRuntimeState.NEW

    @property
    def state(self) -> MultiAgentRuntimeState:
        return self._state

    @property
    def default_agent_id(self) -> str:
        return self._default_agent_id

    @property
    def agent_ids(self) -> tuple[str, ...]:
        return self._ordered_ids

    @property
    def default_pool(self) -> Any:
        return self._pools[self._default_agent_id]

    async def start(self) -> None:
        if self._state is MultiAgentRuntimeState.RUNNING:
            return
        if self._state is not MultiAgentRuntimeState.NEW:
            raise AgentRuntimeStateError(
                f"多 Agent Runtime 无法从 {self._state.value} 启动",
                runtime_id="multi-agent-runtime",
            )
        self._state = MultiAgentRuntimeState.STARTING
        started: list[Any] = []
        try:
            for agent_id in self._ordered_ids:
                pool = self._pools[agent_id]
                await pool.start()
                started.append(pool)
        except BaseException:
            for pool in reversed(started):
                try:
                    await pool.stop()
                except BaseException:
                    pass
            self._state = MultiAgentRuntimeState.STOPPED
            raise
        self._state = MultiAgentRuntimeState.RUNNING

    async def stop(self) -> None:
        if self._state is MultiAgentRuntimeState.STOPPED:
            return
        if self._state is MultiAgentRuntimeState.NEW:
            self._state = MultiAgentRuntimeState.STOPPED
            return
        if self._state is not MultiAgentRuntimeState.RUNNING:
            raise AgentRuntimeStateError(
                f"多 Agent Runtime 无法从 {self._state.value} 关闭",
                runtime_id="multi-agent-runtime",
            )
        self._state = MultiAgentRuntimeState.STOPPING
        first_error: BaseException | None = None
        for agent_id in reversed(self._ordered_ids):
            try:
                await self._pools[agent_id].stop()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        self._state = MultiAgentRuntimeState.STOPPED
        if first_error is not None:
            raise first_error

    def get_pool(self, agent_id: str = "") -> Any:
        resolved = str(agent_id or "").strip() or self._default_agent_id
        pool = self._pools.get(resolved)
        if pool is None:
            raise AgentRuntimeNotFoundError(
                f"Agent 未注册：{resolved}",
                runtime_id=resolved,
            )
        return pool

    def _running_pool(self, agent_id: str) -> Any:
        if self._state is not MultiAgentRuntimeState.RUNNING:
            raise AgentRuntimeStateError(
                "多 Agent Runtime 当前不可用",
                runtime_id=agent_id,
            )
        return self.get_pool(agent_id)

    def build_runtime_registry(
        self,
        *,
        research_factory_builder: (
            Callable[[Callable[[], Any]], Callable[[], Any]] | None
        ) = None,
    ) -> AgentRuntimeRegistry:
        registrations: list[AgentRuntimeRegistration] = []
        for agent_id in self._ordered_ids:
            spec = self._specs[agent_id]
            pool = self._pools[agent_id]
            isolated_factory = pool.create_isolated_bridge
            research_factory = (
                research_factory_builder(isolated_factory)
                if research_factory_builder is not None
                else None
            )
            descriptor = AgentRuntimeDescriptor(
                agent_id=spec.agent_id,
                display_name=spec.display_name,
                description=spec.description,
                adapter="nanobot_kt",
                source_ref=f"creatures/{spec.agent_id}",
                source_sha256=spec.source_sha256,
                runtime_policy_sha256=self._selection_policy.policy_sha256,
                allowed_entrypoints=spec.allowed_entrypoints,
                default=spec.default,
                manifest_snapshot_sha256=spec.manifest_snapshot_sha256,
                profile_sha256=spec.profile_sha256,
                tool_policy_sha256=spec.tool_policy_sha256,
            )
            registrations.append(AgentRuntimeRegistration(
                descriptor=descriptor,
                gateway_provider=(
                    lambda selected=agent_id: self._running_pool(selected)
                ),
                isolated_gateway_factory=isolated_factory,
                research_runtime_factory=research_factory,
            ))
        return AgentRuntimeRegistry.build(registrations)


__all__ = [
    "MultiAgentRuntimeState",
    "NanobotAgentRuntimeManager",
]
