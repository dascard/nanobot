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
        await self._output.start()
        tool_count = len(self._agent.registry.list_tools())
        tools_list = [t.name for t in self._agent.registry.list_tools()]
        logger.info(f"KT Agent '{config.name}' initialized with {tool_count} tools: {tools_list}")

    async def stop(self) -> None:
        """Shutdown the agent."""
        if self._output:
            await self._output.stop()
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
            logger.debug(f"[NanobotBridge] Agent controller: {self._agent._controller if hasattr(self._agent, '_controller') else 'N/A'}")
            logger.debug(f"[NanobotBridge] Agent output_module: {self._agent._output_module if hasattr(self._agent, '_output_module') else 'N/A'}")

            # Create a user input event for the KT controller
            event = create_user_input_event(query)
            logger.debug(f"[NanobotBridge] Created event: type={type(event).__name__}, content_len={len(str(event))}")

            try:
                # Process the event through KT's controller pipeline
                # This runs: LLM call → tool dispatch → multi-turn loop → final response
                logger.debug(f"[NanobotBridge] Before _process_event: output._buffer={len(self._output._buffer) if hasattr(self._output, '_buffer') else 'N/A'}")
                await self._agent._process_event(event)
                logger.debug(f"[NanobotBridge] After _process_event: output._buffer={len(self._output._buffer) if hasattr(self._output, '_buffer') else 'N/A'}")
            except Exception as e:
                logger.error(f"[NanobotBridge] Agent processing error: {e}", exc_info=True)
                return f"处理消息时出错: {str(e)}"

            response = self._output.get_response()
            buffer_list = self._output._buffer if hasattr(self._output, '_buffer') else []
            buffer_len = len(buffer_list)
            
            logger.info(f"[NanobotBridge] After processing: response_len={len(response)}, buffer_chunks={buffer_len}, chunks_content={buffer_list}")
            if response:
                logger.debug(f"[NanobotBridge] Response preview: {response[:200]}")
            else:
                logger.warning(f"[NanobotBridge] EMPTY RESPONSE!")
                logger.warning(f"[NanobotBridge] buffer_list={buffer_list}, buffer_len={buffer_len}")
                if hasattr(self._agent, '_controller') and self._agent._controller:
                    logger.warning(f"[NanobotBridge] Controller state: {self._agent._controller}")

            if not response.strip():
                logger.warning(f"[NanobotBridge] KT agent returned empty response after strip")
                return ""

            return response

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
