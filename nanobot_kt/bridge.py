"""
NanobotBridge — Lifecycle manager that wraps KT's Agent for HTTP request/response usage.

Replaces the old manual NanobotKTController + UnifiedProvider setup.
The bridge creates a KT Agent from the creature config, manages its lifecycle,
and provides a simple async handle_message() interface for use in routes.py.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.core.events import create_user_input_event

from nanobot_kt.output import BufferedOutput
from clients.new_api_client import NewAPIClient
from clients.model_registry import registry
from config import NEW_API_KEY, NEW_API_BASE_URL

logger = logging.getLogger("nanobot.kt.bridge")


class NanobotBridge:
    """
    Wraps a KT Agent for use as a request/response handler.
    
    Lifecycle:
        bridge = NanobotBridge("creatures/nanobot")
        await bridge.start()
        response = await bridge.handle_message("hello", user_id="u1", session_id="s1")
        await bridge.stop()
    """

    def __init__(self, creature_path: str = "creatures/nanobot"):
        self.creature_path = creature_path
        self._output = BufferedOutput()
        self._agent: Optional[Agent] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Initialize the KT agent from creature config."""
        logger.info(f"Loading KT agent from {self.creature_path}")
        config = load_agent_config(self.creature_path)
        self._agent = Agent(
            config,
            output_module=self._output,
        )
        # Critical: Agent must be started so _running=True; otherwise
        # _process_event() drops all events and returns empty output.
        await self._agent.start()
        tools_list = self._agent.registry.list_tools()
        logger.info(f"KT Agent '{config.name}' initialized with {len(tools_list)} tools: {tools_list}")

        # 检查 controller 配置
        if hasattr(self._agent, '_controller'):
            ctrl = self._agent._controller
            logger.info(f"[KT Agent] Controller type: {type(ctrl)}")
            logger.info(f"[KT Agent] Controller provider: {getattr(ctrl, 'provider', 'N/A')}")
            logger.info(f"[KT Agent] Controller model: {getattr(ctrl, 'model', 'N/A')}")
            logger.info(f"[KT Agent] Controller base_url: {getattr(ctrl, 'base_url', 'N/A')}")
        else:
            logger.warning("[KT Agent] No _controller attribute found!")

    async def stop(self) -> None:
        """Shutdown the agent."""
        if self._agent:
            await self._agent.stop()
        logger.info("KT Agent stopped")

    async def handle_message(
        self,
        query: str,
        *,
        user_id: str = "",
        session_id: str = "",
        sender_name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Send a user message to the KT agent and return the response.

        This bridges between the HTTP request/response model (what routes.py needs)
        and KT's event-driven agent model.

        Args:
            query: User message text
            user_id: User identifier
            session_id: Session/group identifier
            sender_name: Display name of the sender
            metadata: Additional metadata

        Returns:
            Agent's response text
        """
        if not self._agent:
            return "Error: Agent not initialized"

        async with self._lock:
            self._output.clear()
            logger.info(f"[NanobotBridge] Starting handle_message: query_len={len(query)}, user={user_id}, session={session_id}")
            logger.debug(f"[NanobotBridge] Agent initialized: {self._agent is not None}")
            logger.debug(f"[NanobotBridge] Output module: {self._output}")
            logger.debug(f"[NanobotBridge] Agent output_module attr: {getattr(self._agent, '_output_module', 'NOT SET')}")

            # Create a user input event for the KT controller
            event = create_user_input_event(query)
            logger.info(f"[NanobotBridge] Event created, about to call _process_event")

            # --- Dynamic Model Routing ---
            route_client = None
            try:
                route_client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL)
                # Ensure registry is populated before routing (cold start after deploy)
                existing = registry.get_models_by_provider("new-api")
                if not existing:
                    logger.info("[Model Router] Registry empty, forcing model sync...")
                    await route_client.sync_models_to_registry(force=True)
                else:
                    await route_client.sync_models_to_registry(force=False)
                messages = [{"role": "user", "content": query}]
                # Simulate presence of tools for tag inference so tasks map correctly
                routed_tier = route_client._route_model_tier(messages, tools=[{}], requested_tier="smart")
                task_tags = route_client._infer_task_tags(messages, tools=[{}])
                target_model = route_client._resolve_model_for_task(routed_tier, task_tags=task_tags, manual_model="")
            except Exception as e:
                logger.error(f"[Model Router] Failed to route model, using default: {e}", exc_info=True)
                target_model = "gpt-4o"
                routed_tier = "smart"
            # -----------------------------

            max_attempts = 3
            failed_models = []
            for attempt in range(max_attempts):
                self._output.clear()
                event = create_user_input_event(query)
                
                # Dynamically update the KT Agent's underlying LLM Provider model for this request
                if hasattr(self._agent, 'controller') and hasattr(self._agent.controller, 'llm') and hasattr(self._agent.controller.llm, 'config'):
                    if attempt > 0 and route_client:
                        failed_models.append(old_model)
                        # Try to find another model in the SAME tier first
                        target_model = route_client._resolve_model_for_task(routed_tier, task_tags=[], manual_model="", exclude_models=failed_models)
                        
                        # If no new model was found in this tier, downgrade tier
                        if target_model == old_model or target_model in failed_models:
                            logger.warning(f"[Model Router] Tier '{routed_tier}' exhausted (failed: {failed_models}). Downgrading tier...")
                            if routed_tier == "reasoning": routed_tier = "smart"
                            elif routed_tier == "smart": routed_tier = "fast"
                            # Re-resolve with downgraded tier
                            target_model = route_client._resolve_model_for_task(routed_tier, task_tags=[], manual_model="", exclude_models=failed_models)
                        
                    old_model = self._agent.controller.llm.config.model
                    self._agent.controller.llm.config.model = target_model
                    logger.info(f"[Model Router] Attempt {attempt+1}: Query routed to tier '{routed_tier}'. Changed KT model: {old_model} -> {target_model}")
                
                try:
                    # Process the event through KT's controller pipeline
                    logger.info(f"[NanobotBridge] Calling _process_event (Attempt {attempt+1})...")
                    result = await self._agent._process_event(event)
                    logger.info(f"[NanobotBridge] _process_event returned: type={type(result)}, value={result}")
                except Exception as e:
                    logger.error(f"[NanobotBridge] Agent processing error: {e}", exc_info=True)
                    self._output._buffer.append(f"\n[系统内部错误] {str(e)}")

                response = self._output.get_response()
                if "[系统内部错误]" in response and attempt < max_attempts - 1:
                    logger.warning(f"[NanobotBridge] Framework error detected. Attempting fallback.")
                    tool_results_preserved = False
                    if hasattr(self._agent.controller, 'conversation'):
                        msgs = self._agent.controller.conversation.get_messages()
                        user_idx = self._agent.controller.conversation.find_last_user_index()

                        # Find any 'tool' messages that landed AFTER the last user message.
                        # These represent tool calls that COMPLETED successfully before the crash.
                        # We must NOT re-execute them; only strip the dangling assistant response.
                        last_tool_idx = -1
                        if user_idx >= 0:
                            for i in range(len(msgs) - 1, user_idx, -1):
                                if msgs[i].role == "tool":
                                    last_tool_idx = i
                                    break

                        if last_tool_idx >= 0:
                            # Tools ran successfully. Strip only the broken assistant response
                            # AFTER the last tool result. Keep all tool messages intact.
                            strip_from = last_tool_idx + 1
                            if strip_from < len(msgs):
                                self._agent.controller.conversation.truncate_from(strip_from)
                                logger.info(
                                    f"[NanobotBridge] Preserved tool results (idx≤{last_tool_idx}). "
                                    f"Stripped only broken post-tool assistant turn from idx={strip_from}."
                                )
                            # Send an empty continuation event, NOT the original user query again,
                            # so the model picks up from the existing tool results.
                            from kohakuterrarium.core.events import TriggerEvent
                            event = TriggerEvent(type="user_input", content="")
                            tool_results_preserved = True
                        elif user_idx >= 0:
                            # No tools ran. Full rollback is safe — retry with the original query.
                            content = msgs[user_idx].content
                            if isinstance(content, str) and content == query:
                                self._agent.controller.conversation.truncate_from(user_idx)
                                logger.info(f"[NanobotBridge] Full rollback: removed failed user message at idx={user_idx}")
                            event = create_user_input_event(query)

                    if not tool_results_preserved:
                        event = create_user_input_event(query)
                    continue  # Retry with downgraded model
                else:
                    break  # Success or max attempts reached

            logger.info(f"[NanobotBridge] Checking output buffer...")
            response = self._output.get_response()
            buffer_list = self._output._buffer if hasattr(self._output, '_buffer') else []
            buffer_len = len(buffer_list)

            # 如果 output buffer 为空，尝试从返回值获取
            if not response and result:
                logger.info(f"[NanobotBridge] Buffer empty, using _process_event return value")
                response = str(result) if result else ""

            # Native mode under some gateways may emit no text chunks but still
            # keep useful content in conversation / extra_fields.
            if not response:
                fallback = self._extract_fallback_response()
                if fallback:
                    logger.info(
                        f"[NanobotBridge] Buffer empty, using fallback response len={len(fallback)}"
                    )
                    response = fallback
            
            logger.info(f"[NanobotBridge] After processing: response_len={len(response)}, buffer_chunks={buffer_len}")
            if response:
                logger.debug(f"[NanobotBridge] Response preview: {response[:200]}")
            else:
                logger.warning(f"[NanobotBridge] EMPTY RESPONSE!")
                logger.warning(f"[NanobotBridge] buffer={buffer_list}, result={result}")

            if not response.strip():
                logger.warning(f"[NanobotBridge] KT agent returned empty response after strip")
                return ""

            return response

    def _extract_fallback_response(self) -> str:
        """Best-effort fallback when output buffer has no text chunks."""
        if not self._agent:
            return ""

        # 1) Last assistant message from conversation
        try:
            messages = self._agent.controller.conversation.to_messages()
            for msg in reversed(messages):
                if msg.get("role") != "assistant":
                    continue
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    parts = []
                    for p in content:
                        if isinstance(p, dict) and p.get("type") == "text":
                            txt = (p.get("text") or "").strip()
                            if txt:
                                parts.append(txt)
                    if parts:
                        return "\n".join(parts)
                break
        except Exception as e:
            logger.debug(f"[NanobotBridge] fallback conversation read failed: {e}")

        # 2) Provider extra fields (e.g. reasoning_content)
        try:
            extras = getattr(self._agent.llm, "last_assistant_extra_fields", {}) or {}
            for key in ("reasoning_content", "reasoning"):
                val = extras.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            details = extras.get("reasoning_details")
            if isinstance(details, list):
                lines = []
                for item in details:
                    if isinstance(item, str) and item.strip():
                        lines.append(item.strip())
                    elif isinstance(item, dict):
                        text = str(item.get("text") or item.get("content") or "").strip()
                        if text:
                            lines.append(text)
                if lines:
                    return "\n".join(lines)
        except Exception as e:
            logger.debug(f"[NanobotBridge] fallback extras read failed: {e}")

        return ""

    @property
    def agent(self) -> Optional[Agent]:
        """Access the underlying KT agent for advanced operations."""
        return self._agent


# Module-level singleton (initialized by server.py lifespan)
_bridge: Optional[NanobotBridge] = None


def get_bridge() -> NanobotBridge:
    """Get the global bridge instance."""
    global _bridge
    if _bridge is None:
        _bridge = NanobotBridge()
    return _bridge


async def init_bridge() -> NanobotBridge:
    """Initialize and start the global bridge. Called from server.py lifespan."""
    global _bridge
    _bridge = NanobotBridge()
    await _bridge.start()
    return _bridge


async def shutdown_bridge() -> None:
    """Shutdown the global bridge. Called from server.py lifespan."""
    global _bridge
    if _bridge:
        await _bridge.stop()
        _bridge = None
