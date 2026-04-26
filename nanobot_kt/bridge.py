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
        ctrl = getattr(self._agent, 'controller', None)
        if ctrl:
            logger.info(f"[KT Agent] Controller type: {type(ctrl)}")
            logger.info(f"[KT Agent] Controller provider: {getattr(ctrl, 'provider', 'N/A')}")
            logger.info(f"[KT Agent] Controller model: {getattr(ctrl, 'model', 'N/A')}")
            logger.info(f"[KT Agent] Controller base_url: {getattr(ctrl, 'base_url', 'N/A')}")
        else:
            logger.warning("[KT Agent] No controller attribute found! Agent attributes: %s",
                           [a for a in dir(self._agent) if not a.startswith('__')])

    async def stop(self) -> None:
        """Shutdown the agent."""
        if self._agent:
            await self._agent.stop()
        logger.info("KT Agent stopped")

    PERSONA_MARKER = "[PersonaContext]"

    async def handle_message(
        self,
        query: str,
        *,
        user_id: str = "",
        session_id: str = "",
        sender_name: str = "",
        metadata: dict[str, Any] | None = None,
        stream_queue: asyncio.Queue[dict[str, Any]] | None = None,
    ) -> str:
        """
        Send a user message to the KT agent and return the response.

        If stream_queue is provided, concise progress events are pushed
        during processing (one per distinct tool call, plus errors).
        """
        if not self._agent:
            return "Error: Agent not initialized"

        async with self._lock:
            self._output.clear()
            if stream_queue is not None:
                self._output.enable_stream(stream_queue)
            logger.info(f"[NanobotBridge] Starting handle_message: query_len={len(query)}, user={user_id}, session={session_id}")

            # --- Inject persona as system message (authoritative weight, persists across clears) ---
            meta = metadata or {}
            persona_text = str(meta.get("persona_text", "")).strip()
            if persona_text:
                if hasattr(self._agent, 'controller') and hasattr(self._agent.controller, 'conversation'):
                    conv = self._agent.controller.conversation
                    conv._messages = [
                        m for m in conv._messages
                        if not (m.role == "system" and getattr(m, 'content', '').startswith(self.PERSONA_MARKER))
                    ]
                    conv.append("system", f"{self.PERSONA_MARKER} user={user_id}\n{persona_text}")
                    logger.info(f"[NanobotBridge] Persona injected as system message: len={len(persona_text)}")
                else:
                    logger.warning("[NanobotBridge] Cannot inject persona: agent has no controller/conversation")
            elif user_id:
                if hasattr(self._agent, 'controller') and hasattr(self._agent.controller, 'conversation'):
                    conv = self._agent.controller.conversation
                    conv._messages = [
                        m for m in conv._messages
                        if not (m.role == "system" and getattr(m, 'content', '').startswith(self.PERSONA_MARKER))
                    ]
                    conv.append("system", f"{self.PERSONA_MARKER} user={user_id}\n无已存储画像")
                    logger.info(f"[NanobotBridge] User ID tag injected (no persona yet): user={user_id}")
                else:
                    logger.warning("[NanobotBridge] Cannot inject user_id tag: no controller/conversation")
            else:
                logger.info(f"[NanobotBridge] No persona_text or user_id in metadata (keys={list(meta.keys())})")
            # -------------------------------------------------------------------------------------

            logger.debug(f"[NanobotBridge] Agent initialized: {self._agent is not None}")
            logger.debug(f"[NanobotBridge] Output module: {self._output}")
            logger.debug(f"[NanobotBridge] Agent output_module attr: {getattr(self._agent, '_output_module', 'NOT SET')}")

            # Create a user input event for the KT controller
            event = create_user_input_event(query)
            logger.info(f"[NanobotBridge] Event created, about to call _process_event")

            # --- Dynamic Model Routing (new priority-ordered system) ---
            route_client = None
            meta = metadata or {}
            raw_query = str(meta.get("raw_query", query)).strip() or query
            try:
                route_client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL)
                existing = registry.get_models_by_provider("new-api")
                if not existing:
                    logger.info("[Model Router] Registry empty, forcing model sync...")
                    await route_client.sync_models_to_registry(force=True)
                else:
                    await route_client.sync_models_to_registry(force=False)

                messages_for_routing = [{"role": "user", "content": raw_query}]
                complexity = route_client.estimate_complexity(messages_for_routing, tools=[{}])
                intel_floor = max(1, complexity - 1)
                candidates = route_client.get_ordered_candidates(
                    provider="new-api", intel_floor=intel_floor,
                )
                logger.info(
                    f"[Model Router] complexity={complexity}, intel_floor={intel_floor}, "
                    f"candidates={[(c['id'][:30], c.get('intelligence')) for c in candidates[:8]]}"
                )
            except Exception as e:
                logger.error(f"[Model Router] Failed to route: {e}", exc_info=True)
                candidates = []
            # --------------------------------------------------------

            # --- Context budget awareness ---
            est_tokens = len(query) // 2
            if candidates:
                ctx_window = candidates[0].get("context_window", 128000)
                logger.info(
                    f"[Context Budget] estimated_tokens={est_tokens}, "
                    f"context_window={ctx_window}, "
                    f"usage={est_tokens / ctx_window * 100:.1f}%"
                )
            # ----------------------------------

            model_iterator = iter(candidates)
            tracker = NewAPIClient.get_failure_tracker()
            max_attempts = min(len(candidates), 8) if candidates else 5
            result = None

            for attempt in range(max_attempts):
                self._output.clear()
                event = create_user_input_event(query)

                # Get next model from ordered list
                try:
                    candidate = next(model_iterator)
                    target_model = candidate["id"]
                except StopIteration:
                    logger.warning(f"[Model Router] No more candidates after {attempt} attempts")
                    break

                # Update KT agent's LLM model
                if hasattr(self._agent, 'controller') and hasattr(self._agent.controller, 'llm') and hasattr(self._agent.controller.llm, 'config'):
                    old_model = self._agent.controller.llm.config.model
                    self._agent.controller.llm.config.model = target_model
                    logger.info(
                        f"[Model Router] Attempt {attempt+1}: {target_model} "
                        f"(intel={candidate.get('intelligence')}, "
                        f"cost={candidate.get('cost_input_1m')})"
                    )

                try:
                    logger.info(f"[NanobotBridge] Calling _process_event (Attempt {attempt+1})...")
                    result = await self._agent._process_event(event)
                    logger.info(f"[NanobotBridge] _process_event returned: type={type(result)}, value={result}")
                except Exception as e:
                    logger.error(f"[NanobotBridge] Agent processing error: {e}", exc_info=True)
                    self._output._buffer.append(f"\n[系统内部错误] {str(e)}")

                response = self._output.get_response()
                is_empty = not response.strip()
                is_error = "[系统内部错误]" in response or "processing_error" in response
                if (is_empty or is_error) and attempt < max_attempts - 1:
                    logger.warning(f"[NanobotBridge] Framework error. Recording failure for {target_model}")
                    await tracker.record_failure(target_model)

                    # reasoning_content: deepseek thinking mode → ban all deepseek
                    if "reasoning_content" in response and route_client:
                        logger.warning("[NanobotBridge] reasoning_content — banning deepseek family")
                        for m in registry.get_models_by_provider("new-api"):
                            if "deepseek" in m.get("id", "").lower():
                                await tracker.record_failure(m["id"])

                    # Conversation rollback logic
                    tool_results_preserved = False
                    if hasattr(self._agent.controller, 'conversation'):
                        msgs = self._agent.controller.conversation.get_messages()
                        user_idx = self._agent.controller.conversation.find_last_user_index()
                        last_tool_idx = -1
                        if user_idx >= 0:
                            for i in range(len(msgs) - 1, user_idx, -1):
                                if msgs[i].role == "tool":
                                    last_tool_idx = i
                                    break
                        if last_tool_idx >= 0:
                            strip_from = last_tool_idx + 1
                            if strip_from < len(msgs):
                                self._agent.controller.conversation.truncate_from(strip_from)
                                logger.info(f"[NanobotBridge] Preserved tool results (idx≤{last_tool_idx})")
                            from kohakuterrarium.core.events import TriggerEvent
                            event = TriggerEvent(type="user_input", content="")
                            tool_results_preserved = True
                        elif user_idx >= 0:
                            content = msgs[user_idx].content
                            if isinstance(content, str) and content == query:
                                self._agent.controller.conversation.truncate_from(user_idx)
                            event = create_user_input_event(query)
                    if not tool_results_preserved:
                        event = create_user_input_event(query)
                    continue
                else:
                    await tracker.record_success(target_model)
                    break

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
