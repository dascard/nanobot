"""
Buffered output module for programmatic access.

Supports an optional stream queue for SSE consumers to receive
processing errors in real time (heartbeats are managed by routes.py).
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import re
from collections.abc import Callable, Iterator
from typing import Any

from nanobot_kt.optional_output_api import BaseOutputModule

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
        "ai_daily": "正在生成 AI 日报...",
        "group_analysis": "正在生成群聊日报...",
        "image_summary": "正在生成图片摘要...",
        "image_generation": "正在生成图片...",
        "persona_update": "正在更新画像...",
        "memory_read": "正在读取记忆...",
        "memory_write": "正在写入记忆...",
    }

    _INTERRUPT_TOOLS = {"ai_daily", "group_analysis", "reply", "no_reply"}

    def __init__(self, **kwargs: Any):
        super().__init__()
        self._buffer: list[str] = []
        self._saved: str = ""  # clear_all 后仍可恢复
        self._interrupt_callback: Callable[[str], bool] | None = None
        self._complete_event = asyncio.Event()
        self._stream_queue: asyncio.Queue[dict[str, Any]] | None = None
        self._stream_tasks: set[asyncio.Task[Any]] = set()
        self._runtime_signal_handler: ContextVar[
            Callable[[str, dict[str, Any]], None] | None
        ] = ContextVar(
            f"nanobot_runtime_output_signal_{id(self)}",
            default=None,
        )

    def enable_stream(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._stream_queue = queue

    def disable_stream(self) -> None:
        self._stream_queue = None

    @contextmanager
    def capture_runtime_signals(
        self,
        handler: Callable[[str, dict[str, Any]], None],
    ) -> Iterator[None]:
        """仅在当前异步上下文中暴露 KT 输出信号给 Runtime Adapter。"""

        token = self._runtime_signal_handler.set(handler)
        try:
            yield
        finally:
            self._runtime_signal_handler.reset(token)

    def _emit_runtime_signal(self, kind: str, **payload: Any) -> None:
        handler = self._runtime_signal_handler.get()
        if handler is not None:
            handler(str(kind), dict(payload))

    def _runtime_signal_is_captured(self) -> bool:
        """当前请求是否由 Runtime Adapter 接管类型化输出。"""

        return self._runtime_signal_handler.get() is not None

    def set_interrupt_callback(
        self,
        callback: Callable[[str], bool] | None,
    ) -> None:
        """安装框架无关的中断回调；输出模块不持有 KT Agent。"""

        self._interrupt_callback = callback

    def _discard_stream_task(self, task: asyncio.Task[Any]) -> None:
        self._stream_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("[BufferedOutput] stream event task failed: %s", exc, exc_info=True)

    def _schedule_stream_event(self, event: dict[str, Any]) -> None:
        if self._stream_queue is None:
            return
        if event.get("status") == "progress":
            try:
                self._stream_queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug(
                    "[BufferedOutput] progress event dropped because stream queue is full: %s",
                    event,
                )
            return
        try:
            task = asyncio.create_task(self._stream_queue.put(event))
        except RuntimeError:
            logger.debug("[BufferedOutput] stream event dropped without running loop: %s", event)
            return
        self._stream_tasks.add(task)
        task.add_done_callback(self._discard_stream_task)

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
        if chunk:
            self._emit_runtime_signal("text_delta", text=str(chunk))
        if (
            chunk
            and self._stream_queue is not None
            and not self._runtime_signal_is_captured()
        ):
            await self._stream_queue.put({"status": "delta", "text": chunk})

    async def write_final(
        self,
        text: str,
        *,
        replace: bool = True,
        source: str = "bridge",
    ) -> None:
        if not text or self._stream_queue is None:
            return
        await self._stream_queue.put(
            {
                "status": "final",
                "text": str(text),
                "replace": bool(replace),
                "source": str(source or "bridge"),
            }
        )

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
            self._emit_runtime_signal(
                "error",
                code="kt_processing_error",
                message=str(detail),
                retryable=False,
            )
            if (
                self._stream_queue is not None
                and not self._runtime_signal_is_captured()
            ):
                self._schedule_stream_event({"status": "error", "message": detail})
            return

        if activity_type == "tool_error":
            logger.error(f"[Activity] {activity_type}: {detail}")
            self._emit_runtime_signal(
                "error",
                code="kt_tool_error",
                message=str(detail),
                retryable=False,
            )
            if (
                self._stream_queue is not None
                and not self._runtime_signal_is_captured()
            ):
                self._schedule_stream_event(
                    {"status": "progress", "text": f"工具失败：{detail}"}
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

            # reply 是内部工具——不推送到 QQbot 作为进度提示
            if tool_name != "reply" and self._stream_queue is not None:
                hint = self._TOOL_HINTS.get(tool_name, f"正在执行 {tool_name}")
                self._schedule_stream_event({"status": "progress", "text": hint})
            return

        if activity_type == "tool_done":
            logger.info(f"[Activity] {activity_type}: {detail}")
            # HTML 工具完成后立即 interrupt——避免模型磨蹭 45s 再调 reply
            try:
                tool_name = detail.split("[", 2)[1].split("]", 1)[0] if "[" in detail else ""
            except (IndexError, ValueError):
                tool_name = ""
            if (
                tool_name
                and tool_name in self._INTERRUPT_TOOLS
                and self._interrupt_callback is not None
            ):
                if self._interrupt_callback(f"tool_done:{tool_name}"):
                    logger.info("[BufferedOutput] interrupt after %s tool_done", tool_name)
            return

        logger.info(f"[Activity] {activity_type}: {detail}")
