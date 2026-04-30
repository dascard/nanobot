"""
Buffered output module for programmatic access.

Supports an optional stream queue for SSE consumers to receive
processing errors in real time (heartbeats are managed by routes.py).
"""

import asyncio
import logging
import re
from typing import Any

from kohakuterrarium.modules.output.base import BaseOutputModule

logger = logging.getLogger("nanobot.kt.output")


def _safety_check(tool_name: str, args: dict[str, Any] | None) -> str | None:
    """Rule-based safety check on tool calls. Logs warnings; does NOT block."""
    if not args:
        return None
    code = str(args.get("code") or args.get("sql") or "")
    if tool_name in ("python_sandbox", "bash") and code:
        dangerous = [
            (r"\bos\.remove\b", "delete file"),
            (r"\bos\.rmdir\b", "delete directory"),
            (r"\bshutil\.rmtree\b", "recursive delete"),
            (r"\bopen\s*\([^)]*['\"]w", "write file"),
            (r"\brequests\.post\b", "HTTP POST"),
            (r"\bsubprocess\b", "spawn process"),
        ]
        for pattern, desc in dangerous:
            if re.search(pattern, code):
                return f"[安全警告] {tool_name}: {desc}"
    if tool_name == "sql_analysis" and code:
        lowered = code.lower()
        for kw in ("insert", "update", "delete", "drop", "alter", "create"):
            if re.search(rf"\b{kw}\b", lowered):
                return f"[安全警告] sql_analysis: 包含 {kw.upper()} 语句"
    return None


class BufferedOutput(BaseOutputModule):
    """Collects LLM output. Streams concise progress + errors via optional queue."""

    _TOOL_HINTS: dict[str, str] = {
        "sql_analysis": "正在查询数据库...",
        "python_sandbox": "正在执行数据分析...",
        "news_search": "正在搜索资讯...",
        "image_summary": "正在生成图片摘要...",
        "persona_update": "正在更新画像...",
        "memory_read": "正在读取记忆...",
        "memory_write": "正在写入记忆...",
    }

    def __init__(self, **kwargs: Any):
        super().__init__()
        self._buffer: list[str] = []
        self._saved: str = ""  # clear_all 后仍可恢复
        self._complete_event = asyncio.Event()
        self._stream_queue: asyncio.Queue[dict[str, Any]] | None = None

    def enable_stream(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._stream_queue = queue

    def disable_stream(self) -> None:
        self._stream_queue = None

    async def start(self) -> None:
        logger.info("[BufferedOutput.start] Initializing")

    async def stop(self) -> None:
        logger.info("[BufferedOutput.stop] Shutting down")

    async def write(self, content: str) -> None:
        logger.info(f"[BufferedOutput.write] called with {len(content)} chars: {content[:100] if content else '(empty)'}")
        self._buffer.append(content)

    async def write_stream(self, chunk: str) -> None:
        logger.info(f"[BufferedOutput.write_stream] called with {len(chunk)} chars: {chunk[:100] if chunk else '(empty)'}")
        self._buffer.append(chunk)

    async def flush(self) -> None:
        logger.debug(f"[BufferedOutput.flush] called, current buffer_len={len(''.join(self._buffer))}")

    async def on_processing_start(self) -> None:
        logger.debug("[BufferedOutput.on_processing_start] resetting buffer")
        self._buffer.clear()
        self._complete_event.clear()

    async def on_processing_end(self) -> None:
        self._saved = "".join(self._buffer)  # 快照：防止 KT 框架 clear_all 清空
        logger.info(f"[BufferedOutput.on_processing_end] processing complete, final_buffer_len={len(self._saved)}")
        self._complete_event.set()

    def get_response(self) -> str:
        buf = "".join(self._buffer)
        return buf if buf else self._saved

    def clear(self) -> None:
        self._buffer.clear()
        self._saved = ""
        self._complete_event.clear()

    def on_activity(self, activity_type: str, detail: str, **kwargs: Any) -> None:
        """Log activity. Emit progress for tool starts + errors to stream."""
        if activity_type == "processing_error":
            logger.error(f"[Activity] {activity_type}: {detail}")
            self._buffer.append(f"\n[系统内部错误] {detail}")
            if self._stream_queue is not None:
                asyncio.ensure_future(self._stream_queue.put({"status": "error", "message": detail}))
            return

        if activity_type == "tool_error":
            logger.error(f"[Activity] {activity_type}: {detail}")
            if self._stream_queue is not None:
                asyncio.ensure_future(
                    self._stream_queue.put(
                        {"status": "progress", "text": f"工具失败：{detail}"}
                    )
                )
            return

        if activity_type == "tool_start":
            logger.info(f"[Activity] {activity_type}: {detail}")
            try:
                tool_name = detail.split("[", 2)[1].split("]", 1)[0] if "[" in detail else "unknown"
            except IndexError:
                tool_name = "unknown"

            args_str = detail.split("]", 1)[1].strip() if "]" in detail else ""
            if args_str:
                args = {"code": args_str} if "=" not in args_str else {}
                for part in args_str.split():
                    if "=" in part and not args:
                        k, v = part.split("=", 1)
                        args[k] = v
                warning = _safety_check(tool_name, args)
                if warning:
                    logger.warning(f"[Safety] {warning}")

            if self._stream_queue is not None:
                hint = self._TOOL_HINTS.get(tool_name, f"正在执行 {tool_name}")
                asyncio.ensure_future(self._stream_queue.put({"status": "progress", "text": hint}))
            return

        if activity_type == "tool_done":
            logger.info(f"[Activity] {activity_type}: {detail}")
            return

        logger.info(f"[Activity] {activity_type}: {detail}")
