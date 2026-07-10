from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any


logger = logging.getLogger("nanobot.kt.request_scope")

_REQUEST_DRY_RUN: ContextVar[bool] = ContextVar(
    "nanobot_bridge_request_dry_run",
    default=False,
)


def is_request_dry_run() -> bool:
    """返回当前异步请求是否处于只读 dry-run。"""
    return _REQUEST_DRY_RUN.get()


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
        self._lock_acquired = False
        self._closed = False

    async def __aenter__(self) -> "BridgeRequestScope":
        await self._lock.acquire()
        self._lock_acquired = True
        self._dry_run_token = _REQUEST_DRY_RUN.set(self._dry_run)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        try:
            if exc_type is None:
                self.finish("success")
            elif issubclass(exc_type, asyncio.CancelledError):
                self.finish("cancelled", error=str(exc or "request cancelled"))
            else:
                self.finish("error", error=str(exc or exc_type.__name__))
        finally:
            self.close()
        return False

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
                    _REQUEST_DRY_RUN.reset(self._dry_run_token)
                    self._dry_run_token = None
            finally:
                if self._lock_acquired:
                    self._lock.release()
                    self._lock_acquired = False
