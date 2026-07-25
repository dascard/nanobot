"""HTTP 请求关联与无正文 RuntimeEvent ASGI Middleware。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from core.runtime.event_bus import (
    current_runtime_event_context,
    get_runtime_event_emitter,
)
from core.runtime.events import RuntimeEventEmitter
from core.tracing_context import (
    reset_runtime_correlation,
    set_runtime_correlation,
)


def _valid_request_id(value: object) -> str:
    request_id = str(value or "").strip()
    if (
        not request_id
        or len(request_id) > 128
        or any(ord(character) < 33 or ord(character) > 126 for character in request_id)
    ):
        return ""
    return request_id


def _request_header(scope: dict[str, Any], name: bytes) -> str:
    for raw_name, raw_value in scope.get("headers") or ():
        if bytes(raw_name).lower() == name:
            return bytes(raw_value).decode("latin-1", errors="ignore")
    return ""


class TelemetryHttpMiddleware:
    """只记录方法、规范化路由、状态和耗时，不读取请求／响应正文。"""

    def __init__(
        self,
        app: Any,
        *,
        event_emitter: RuntimeEventEmitter | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.app = app
        self._event_emitter = event_emitter
        self._request_id_factory = request_id_factory or (
            lambda: f"req_{uuid.uuid4().hex}"
        )

    def _emitter(self) -> RuntimeEventEmitter:
        return self._event_emitter or get_runtime_event_emitter()

    def _emit(
        self,
        phase: str,
        *,
        attributes: dict[str, object],
    ) -> None:
        try:
            self._emitter().emit(
                "http.request",
                phase,
                context=current_runtime_event_context(),
                attributes=attributes,
            )
        except Exception:
            # Observer 故障不能改变 HTTP 业务结果。
            return

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = _valid_request_id(
            _request_header(scope, b"x-request-id")
        )
        if not request_id:
            request_id = _valid_request_id(self._request_id_factory())
        if not request_id:
            request_id = f"req_{uuid.uuid4().hex}"
        method = str(scope.get("method") or "UNKNOWN").upper()[:16]
        tokens = set_runtime_correlation(request_id=request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False
        self._emit("started", attributes={"method": method})

        async def send_with_request_id(message: dict[str, Any]) -> None:
            nonlocal status_code, response_started
            if message.get("type") == "http.response.start":
                response_started = True
                status_code = int(message.get("status") or 500)
                headers = [
                    (name, value)
                    for name, value in (message.get("headers") or ())
                    if bytes(name).lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except BaseException as exc:
            route = getattr(scope.get("route"), "path", "")
            self._emit(
                "failed",
                attributes={
                    "method": method,
                    "route": str(route or ""),
                    "status_code": status_code,
                    "latency_ms": (
                        time.perf_counter() - started
                    ) * 1000,
                    "failure_code": "http.unhandled",
                    "error_type": type(exc).__name__,
                },
            )
            raise
        else:
            route = getattr(scope.get("route"), "path", "")
            attributes: dict[str, object] = {
                "method": method,
                "route": str(route or ""),
                "status_code": status_code if response_started else 500,
                "latency_ms": (time.perf_counter() - started) * 1000,
            }
            phase = "succeeded"
            if not response_started or status_code >= 500:
                phase = "failed"
                attributes["failure_code"] = "http.server_error"
            self._emit(phase, attributes=attributes)
        finally:
            reset_runtime_correlation(tokens)


__all__ = ["TelemetryHttpMiddleware"]
