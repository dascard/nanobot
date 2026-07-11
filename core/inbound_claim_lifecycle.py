"""入站消息 claim owner 的异步生命周期。"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from core.inbound_idempotency import (
    DEFAULT_LEASE_SECONDS,
    CompletedInboundResponse,
    InboundClaimHandle,
    complete_inbound_claim,
    encode_completed_inbound_response,
    fail_inbound_claim,
    renew_inbound_claim,
)


_LOGGER = logging.getLogger(__name__)
_ResultT = TypeVar("_ResultT")


class InboundClaimOwnershipLostError(RuntimeError):
    """当前 owner token 已不能对 claim 执行请求的终结操作。"""


class InboundClaimCompletionConflictError(ValueError):
    """重复完成传入的业务结果与已持久化结果不一致。"""


def _default_session_factory() -> Any:
    # 必须在调用时取 SessionLocal，兼容测试及运行时替换数据库工厂。
    from core import database

    return database.SessionLocal()


def _positive_seconds(value: int | float, *, field_name: str) -> float:
    if type(value) not in (int, float):
        raise TypeError(f"{field_name} 必须是数字")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{field_name} 必须是有限正数")
    return normalized


class InboundClaimOwner:
    """用 fresh Session 续租并终结单个已取得的入站 claim。"""

    def __init__(
        self,
        handle: InboundClaimHandle,
        *,
        session_factory: Callable[[], Any] | None = None,
        lease_seconds: int | float = DEFAULT_LEASE_SECONDS,
        renew_interval_seconds: int | float | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        logger: Any = _LOGGER,
    ) -> None:
        if type(handle) is not InboundClaimHandle:
            raise TypeError("handle 必须是 InboundClaimHandle")
        if not callable(session_factory) and session_factory is not None:
            raise TypeError("session_factory 必须可调用")
        if not callable(sleep):
            raise TypeError("sleep 必须可调用")

        normalized_lease = _positive_seconds(lease_seconds, field_name="lease_seconds")
        interval = normalized_lease / 3 if renew_interval_seconds is None else renew_interval_seconds

        self.handle = handle
        self._session_factory = (
            _default_session_factory if session_factory is None else session_factory
        )
        self._lease_seconds = normalized_lease
        self._renew_interval_seconds = _positive_seconds(
            interval,
            field_name="renew_interval_seconds",
        )
        self._sleep = sleep
        self._logger = logger
        self._lock = asyncio.Lock()
        self._renewal_task: asyncio.Task[None] | None = None
        self._started = False
        self._renewal_error: BaseException | None = None
        self._ownership_lost = False
        self._settlement: str | None = None
        self._settlement_error: BaseException | None = None
        self._completed_response_json: str | None = None
        self._unusable_event = asyncio.Event()
        self._unusable_error: BaseException | None = None

    @property
    def renewal_task(self) -> asyncio.Task[None] | None:
        return self._renewal_task

    @property
    def renewal_error(self) -> BaseException | None:
        return self._renewal_error

    @property
    def ownership_lost(self) -> bool:
        return self._ownership_lost

    async def start(self) -> asyncio.Task[None]:
        """启动且仅启动一个后台续租 task。"""

        async with self._lock:
            if self._settlement is not None:
                raise RuntimeError("claim 已进入终结流程，不能再启动续租")
            if not self._started:
                self._started = True
                message_id = self.handle.key.message_id
                self._renewal_task = asyncio.create_task(
                    self._renew_loop(),
                    name=f"inbound-claim-renew:{message_id}",
                )
            assert self._renewal_task is not None
            return self._renewal_task

    async def pause(self) -> bool:
        """暂停周期续租，但保留未终结 owner 供后续 fenced resume。"""

        async with self._lock:
            if self._settlement is not None:
                return False
            await self._stop_renewal()
            self._started = False
            return True

    async def resume(self) -> asyncio.Task[None]:
        """立即 fenced 续租成功后恢复周期续租。"""

        async with self._lock:
            if self._settlement is not None:
                raise RuntimeError("claim 已进入终结流程，不能恢复续租")
            if self._ownership_lost:
                raise self._ownership_lost_error("恢复续租")
            task = self._renewal_task
            if self._started and task is not None and not task.done():
                return task

            await self._stop_renewal()
            self._started = False
            try:
                renewed = bool(
                    await self._run_with_fresh_session(
                        lambda db: renew_inbound_claim(
                            db,
                            self.handle,
                            lease_seconds=self._lease_seconds,
                        )
                    )
                )
            except BaseException as exc:
                self._renewal_error = exc
                self._set_unusable(exc)
                raise

            if not renewed:
                self._ownership_lost = True
                error = self._ownership_lost_error("恢复续租")
                self._set_unusable(error)
                raise error

            self._renewal_error = None
            self._started = True
            message_id = self.handle.key.message_id
            self._renewal_task = asyncio.create_task(
                self._renew_loop(),
                name=f"inbound-claim-renew:{message_id}",
            )
            return self._renewal_task

    def _call_with_fresh_session(
        self,
        operation: Callable[[Any], _ResultT],
    ) -> _ResultT:
        session = self._session_factory()
        try:
            result = operation(session)
        except BaseException:
            self._close_session(session, message="关闭 claim Session 时再次失败")
            raise
        self._close_session(session, message="关闭 claim Session 失败")
        return result

    async def _run_with_fresh_session(
        self,
        operation: Callable[[Any], _ResultT],
    ) -> _ResultT:
        return await asyncio.to_thread(self._call_with_fresh_session, operation)

    def _set_unusable(self, error: BaseException) -> None:
        if self._unusable_error is not None:
            return
        self._unusable_error = error
        self._unusable_event.set()

    async def wait_unusable(self) -> None:
        """等待 owner 失权或无法继续证明 lease 有效，并重抛原始原因。"""

        await self._unusable_event.wait()
        error = self._unusable_error
        assert error is not None
        raise error

    async def checkpoint(self) -> bool:
        """使用 fresh Session 执行一次 fenced 续租检查。"""

        async with self._lock:
            if self._settlement is not None:
                raise self._ownership_lost_error(
                    "检查",
                    detail="claim 已进入结算",
                )
            if self._ownership_lost:
                raise self._ownership_lost_error("检查")
            try:
                renewed = bool(
                    await self._run_with_fresh_session(
                        lambda db: renew_inbound_claim(
                            db,
                            self.handle,
                            lease_seconds=self._lease_seconds,
                        )
                    )
                )
            except BaseException as exc:
                self._renewal_error = exc
                self._set_unusable(exc)
                raise

            if not renewed:
                self._ownership_lost = True
                error = self._ownership_lost_error("检查")
                self._set_unusable(error)
                raise error
            self._renewal_error = None
            return True

    def _close_session(self, session: Any, *, message: str) -> None:
        try:
            session.close()
        except BaseException:
            self._safe_log("exception", message)

    def _safe_log(self, method_name: str, message: str, *args: Any, **kwargs: Any) -> None:
        try:
            log_method = getattr(self._logger, method_name)
            log_method(message, *args, **kwargs)
        except BaseException:
            return

    async def _renew_loop(self) -> None:
        while True:
            try:
                await self._sleep(self._renew_interval_seconds)
                renewed = await self._run_with_fresh_session(
                    lambda db: renew_inbound_claim(
                        db,
                        self.handle,
                        lease_seconds=self._lease_seconds,
                    )
                )
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                self._renewal_error = exc
                self._set_unusable(exc)
                self._safe_log(
                    "error",
                    "入站 claim 续租异常，停止续租：message_id=%s owner_token=%s",
                    self.handle.key.message_id,
                    self.handle.owner_token,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                return

            if renewed:
                continue
            self._ownership_lost = True
            error = self._ownership_lost_error("续租")
            self._set_unusable(error)
            self._safe_log(
                "warning",
                "入站 claim 已失去 owner，停止续租：message_id=%s owner_token=%s",
                self.handle.key.message_id,
                self.handle.owner_token,
            )
            return

    async def _stop_renewal(self) -> None:
        task = self._renewal_task
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _ownership_lost_error(self, operation: str, *, detail: str = "") -> Exception:
        suffix = f"（{detail}）" if detail else ""
        return InboundClaimOwnershipLostError(
            f"入站 claim owner 已失效，无法{operation}{suffix}："
            f"message_id={self.handle.key.message_id} "
            f"owner_token={self.handle.owner_token}"
        )

    def _raise_cached_settlement_error(self) -> None:
        error = self._settlement_error
        assert error is not None
        raise error

    async def complete(self, response: CompletedInboundResponse) -> bool:
        """条件完成 claim；失权时抛异常，禁止调用方误发 done。"""

        async with self._lock:
            if self._settlement == "completed":
                response_json = encode_completed_inbound_response(response)
                if response_json != self._completed_response_json:
                    raise InboundClaimCompletionConflictError(
                        "重复完成的完成结果不一致，拒绝复用已持久化结果"
                    )
                return True
            if self._settlement == "failed":
                raise self._ownership_lost_error("完成", detail="claim 已失败")
            if self._settlement == "ownership_lost":
                raise self._ownership_lost_error("完成")
            if self._settlement in {"complete_error", "fail_error"}:
                self._raise_cached_settlement_error()

            await self._stop_renewal()
            try:
                response_json = encode_completed_inbound_response(response)
                completed = bool(
                    await self._run_with_fresh_session(
                        lambda db: complete_inbound_claim(db, self.handle, response)
                    )
                )
            except BaseException as exc:
                self._settlement = "complete_error"
                self._settlement_error = exc
                raise

            if not completed:
                self._ownership_lost = True
                self._settlement = "ownership_lost"
                raise self._ownership_lost_error("完成")
            self._completed_response_json = response_json
            self._settlement = "completed"
            return True

    async def fail(self, error: Any) -> bool:
        """条件标记 claim 失败；失权或已经完成时返回 ``False``。"""

        async with self._lock:
            if self._settlement == "failed":
                return True
            if self._settlement in {"completed", "ownership_lost"}:
                return False
            if self._settlement == "fail_error":
                self._raise_cached_settlement_error()

            await self._stop_renewal()
            try:
                failed = bool(
                    await self._run_with_fresh_session(
                        lambda db: fail_inbound_claim(db, self.handle, error)
                    )
                )
            except BaseException as exc:
                self._settlement = "fail_error"
                self._settlement_error = exc
                raise

            if not failed:
                self._ownership_lost = True
                self._settlement = "ownership_lost"
                return False
            self._settlement = "failed"
            return True
