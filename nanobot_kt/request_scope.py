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
        cleanup_failure = await self._run_async_cleanups()
        finish_failure: BaseException | None = None
        try:
            if exc_type is None and cleanup_failure is None:
                self.finish("success")
            elif (
                exc_type is not None
                and issubclass(exc_type, asyncio.CancelledError)
            ):
                from core.durable_tasks import durable_cancel_status

                self.finish(
                    durable_cancel_status(exc or asyncio.CancelledError()),
                    error=str(exc or "request cancelled"),
                )
            else:
                failure = cleanup_failure or exc
                ambiguous = False
                if failure is not None:
                    from core.agent_runtime import AgentRuntimeAmbiguousError
                    from core.run_ledger.contracts import (
                        find_run_ledger_authority_error,
                    )

                    authority = find_run_ledger_authority_error(failure)
                    ambiguous = isinstance(
                        failure,
                        AgentRuntimeAmbiguousError,
                    ) or (
                        authority is not None
                        and authority.code == "side_effect_receipt_unconfirmed"
                    )
                self.finish(
                    "ambiguous" if ambiguous else "error",
                    error=str(
                        failure
                        or (exc_type.__name__ if exc_type is not None else "")
                    ),
                )
        except BaseException as finish_exc:
            finish_failure = finish_exc
        finally:
            self.close()
        if finish_failure is not None:
            raise finish_failure
        if cleanup_failure is not None:
            raise cleanup_failure
        return False

    def bind_async_cleanup(
        self,
        cleanup: Callable[[], Awaitable[None]],
    ) -> None:
        if self._closed:
            raise RuntimeError("Bridge request scope 已关闭")
        self._async_cleanups.append(cleanup)

    async def _run_async_cleanups(self) -> BaseException | None:
        cleanups = list(reversed(self._async_cleanups))
        self._async_cleanups.clear()
        authority_failure: BaseException | None = None
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
                from core.run_ledger.contracts import (
                    find_run_ledger_authority_error,
                )

                if authority_failure is None:
                    authority_failure = find_run_ledger_authority_error(exc)
        return authority_failure

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
        finalizer.finish(
            status,
            output_preview=output_preview,
            error=error,
            model=model,
        )

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
