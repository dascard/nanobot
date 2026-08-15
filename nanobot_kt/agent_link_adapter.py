"""Agent Link 核心 Port 到 KohakuTerrarium 的执行 Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.agent_link.runtime import (
    AgentLinkChatRequest,
    AgentLinkToolCaller,
)
from core.tool_plan import additional_tool_schemas_scope
from foundation.identity import (
    ActorIdentity,
    Principal,
    RecipientIdentity,
    resolve_chat_stream_identity,
)
from foundation.message_contract import (
    GatewayMetadata,
    InboundMessageContract,
    MessageTrace,
    TextContent,
)
from nanobot_kt.agent_link_tools import build_agent_link_tools


class KtAgentLinkChatAdapter:
    """为每个 Agent Link 会话安装动态工具并运行 Nanobot Agent Loop。"""

    def __init__(
        self,
        bridge_pool: Any = None,
        *,
        bridge_pool_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        if bridge_pool is not None and bridge_pool_resolver is not None:
            raise ValueError("bridge_pool 与 bridge_pool_resolver 只能提供一个")
        if bridge_pool is None and bridge_pool_resolver is None:
            raise ValueError("必须提供 BridgePool 或 resolver")
        self._bridge_pool = bridge_pool
        self._bridge_pool_resolver = bridge_pool_resolver

    @staticmethod
    def _install_runtime_tools(bridge: Any, tools: tuple[Any, ...]) -> None:
        # Native Runtime 可以直接处理不带前端动态工具的 Agent Link 对话；
        # 只有确实存在动态工具时才要求 KT 的 Registry/Executor 扩展面。
        if not tools:
            return
        agent = getattr(bridge, "agent", None)
        registry = getattr(agent, "registry", None)
        executor = getattr(agent, "executor", None)
        if registry is None or executor is None:
            raise RuntimeError("KT Agent 缺少动态工具注册能力")
        registered = set(
            getattr(bridge, "_agent_link_dynamic_tool_names", set())
        )
        for tool in tools:
            name = str(getattr(tool, "tool_name", "") or "").strip()
            if not name:
                raise ValueError("Agent Link 动态工具缺少名称")
            if registry.get_tool(name) is not None and name not in registered:
                raise ValueError(
                    f"Agent Link 动态工具与现有工具冲突：{name}"
                )
            registry.register_tool(tool)
            executor.register_tool(tool)
            registered.add(name)
        bridge._agent_link_dynamic_tool_names = registered

    async def run_chat(
        self,
        request: AgentLinkChatRequest,
        tool_caller: AgentLinkToolCaller,
    ) -> str:
        pool = (
            self._bridge_pool_resolver(request.target_agent_id)
            if self._bridge_pool_resolver is not None
            else self._bridge_pool
        )
        pool_key = pool._session_key(
            user_id=request.key.bridge_user_id,
            session_id=request.key.bridge_session_id,
        )
        bridge = await pool._acquire_bridge(pool_key)
        try:
            tools = build_agent_link_tools(
                request.key,
                request.tools,
                runtime=tool_caller,
            )
            self._install_runtime_tools(bridge, tools)
            schemas = tuple(
                definition.wire_schema()
                for definition in request.tools
            )
            client_id = request.client.platform_id
            metadata = {
                "platform": client_id,
                "agent_id": request.target_agent_id,
                "gateway_transport": "agent_link",
                "policy_profile": request.policy_profile,
                "chat_type": "private",
                "is_group": False,
                "message_id": request.request_id,
                "raw_query": request.user_text,
                "history_header": (
                    f"{client_id} 客户端提供的最近对话，仅用于理解上下文。"
                ),
                "history_messages": list(request.history),
                "files": list(request.files),
                "required_capabilities": {
                    "supports_image": any(
                        "image" in definition.result_modalities
                        for definition in request.tools
                    ),
                },
            }
            chat_stream = resolve_chat_stream_identity(
                platform=client_id,
                chat_type="private",
                session_id=request.key.bridge_session_id,
            )
            message = InboundMessageContract(
                message_id=request.request_id,
                chat_stream=chat_stream,
                actor=ActorIdentity(
                    platform=client_id,
                    actor_id=request.key.bridge_user_id,
                ),
                recipient=RecipientIdentity(
                    platform=client_id,
                    recipient_type="user",
                    recipient_id=request.key.bridge_user_id,
                ),
                principal=Principal(
                    platform=client_id,
                    owner_type="user",
                    owner_id=request.key.bridge_user_id,
                ),
                text=request.user_text,
                parts=(TextContent(request.user_text or request.content),),
                gateway=GatewayMetadata(
                    source="agent_link",
                    session_name=request.client.name,
                ),
                trace=MessageTrace(
                    request_id=request.request_id,
                    correlation_id=request.request_id,
                    idempotency_key=request.request_id,
                ),
            )
            with additional_tool_schemas_scope(schemas):
                return await bridge.handle_message_contract(
                    message,
                    content=request.content,
                    runtime_user_id=request.key.bridge_user_id,
                    runtime_session_id=request.key.bridge_session_id,
                    sender_name=f"{client_id} 用户",
                    metadata=metadata,
                    stream=False,
                    trusted_gateway_transport="agent_link",
                )
        finally:
            await pool._release_bridge(pool_key)


__all__ = ["KtAgentLinkChatAdapter"]
