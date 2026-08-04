from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextvars import Token
from types import TracebackType
from typing import Any

from core.agent_runtime.request_scope import (
    is_runtime_request_dry_run,
    reset_runtime_request_dry_run,
    set_runtime_request_dry_run,
)


logger = logging.getLogger("nanobot.kt.request_scope")


def is_request_dry_run() -> bool:
    """兼容旧导入；dry-run 的事实源已迁入 Runtime Core。"""

    return is_runtime_request_dry_run()


class BridgeRequestScope:
    """在持有 session lock 期间统一收尾 Bridge 请求资源。"""

    def __init__(
        self,
        lock: asyncio.Lock,
        output: Any,
        *,
        dry_run: bool = False,
    ) -> None:
        self._lock = lock
        self._output = output
        self._dry_run = bool(dry_run)
        self._dry_run_token: Token[bool] | None = None
        self._trace_finalizer: Any = None
        self._async_cleanups: list[Callable[[], Awaitable[None]]] = []
        self._lock_acquired = False
        self._closed = False

    async def __aenter__(self) -> "BridgeRequestScope":
        await self._lock.acquire()
        self._lock_acquired = True
        self._dry_run_token = set_runtime_request_dry_run(self._dry_run)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        try:
            await self._run_async_cleanups()
            if exc_type is None:
                self.finish("success")
            elif issubclass(exc_type, asyncio.CancelledError):
                self.finish("cancelled", error=str(exc or "request cancelled"))
            else:
                self.finish("error", error=str(exc or exc_type.__name__))
        finally:
            self.close()
        return False

    def bind_async_cleanup(
        self,
        cleanup: Callable[[], Awaitable[None]],
    ) -> None:
        if self._closed:
            raise RuntimeError("Bridge request scope 已关闭")
        self._async_cleanups.append(cleanup)

    async def _run_async_cleanups(self) -> None:
        cleanups = list(reversed(self._async_cleanups))
        self._async_cleanups.clear()
        for cleanup in cleanups:
            try:
                await cleanup()
            except asyncio.CancelledError:
                logger.warning("Bridge request async cleanup was cancelled")
            except Exception as exc:
                logger.warning(
                    "Bridge request async cleanup failed: %s",
                    exc,
                    exc_info=True,
                )

    def bind_trace_finalizer(self, finalizer: Any) -> None:
        self._trace_finalizer = finalizer

    def finish(
        self,
        status: str,
        *,
        output_preview: str = "",
        error: str = "",
        model: str = "",
    ) -> None:
        finalizer = self._trace_finalizer
        if finalizer is None or bool(getattr(finalizer, "closed", False)):
            return
        try:
            finalizer.finish(
                status,
                output_preview=output_preview,
                error=error,
                model=model,
            )
        except Exception as exc:
            logger.warning("Bridge request trace finalization failed: %s", exc, exc_info=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            disable_stream = getattr(self._output, "disable_stream", None)
            if callable(disable_stream):
                disable_stream()
        except Exception as exc:
            logger.warning("Bridge request stream cleanup failed: %s", exc, exc_info=True)
        finally:
            try:
                if self._dry_run_token is not None:
                    reset_runtime_request_dry_run(self._dry_run_token)
                    self._dry_run_token = None
            finally:
                if self._lock_acquired:
                    self._lock.release()
                    self._lock_acquired = False
