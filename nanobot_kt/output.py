"""
Buffered output module for programmatic access.

Instead of printing to stdout, this module buffers all output chunks
so the bridge can retrieve the full response after processing completes.
"""

import asyncio
import logging
from typing import Any

from kohakuterrarium.modules.output.base import BaseOutputModule

logger = logging.getLogger("nanobot.kt.output")


class BufferedOutput(BaseOutputModule):
    """
    Collects LLM output into a buffer for programmatic retrieval.
    
    Used by NanobotBridge to get the agent's response as a string
    after handle_message() completes.
    """

    def __init__(self, **kwargs: Any):
        super().__init__()
        self._buffer: list[str] = []
        self._complete_event = asyncio.Event()

    async def write(self, content: str) -> None:
        """Buffer complete content."""
        logger.info(f"[BufferedOutput.write] called with {len(content)} chars: {content[:100] if content else '(empty)'}")
        self._buffer.append(content)

    async def write_stream(self, chunk: str) -> None:
        """Buffer streaming chunks."""
        logger.info(f"[BufferedOutput.write_stream] called with {len(chunk)} chars: {chunk[:100] if chunk else '(empty)'}")
        self._buffer.append(chunk)

    async def flush(self) -> None:
        """No-op for buffer."""
        logger.debug(f"[BufferedOutput.flush] called, current buffer_len={len(''.join(self._buffer))}")

    async def on_processing_start(self) -> None:
        """Reset buffer at the start of each processing cycle."""
        logger.debug(f"[BufferedOutput.on_processing_start] resetting buffer")
        self._buffer.clear()
        self._complete_event.clear()

    async def on_processing_end(self) -> None:
        """Signal that processing is done."""
        final_len = len("".join(self._buffer))
        logger.info(f"[BufferedOutput.on_processing_end] processing complete, final_buffer_len={final_len}")
        self._complete_event.set()

    def get_response(self) -> str:
        """Retrieve the buffered response text."""
        return "".join(self._buffer)

    def clear(self) -> None:
        """Clear the buffer."""
        self._buffer.clear()
        self._complete_event.clear()

    def on_activity(self, activity_type: str, detail: str) -> None:
        """Log tool/subagent activity."""
        logger.debug(f"[Activity] {activity_type}: {detail}")
