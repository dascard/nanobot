"""通过 Unix Domain Socket 调用宿主 sandboxd。"""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import AsyncIterable, Mapping
from pathlib import Path
from typing import Any

import httpx

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.paths import validate_sha256, validate_workspace_id


def _read_token_file(token_file: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        file_fd = os.open(token_file, flags)
        try:
            metadata = os.fstat(file_fd)
            mode = stat.S_IMODE(metadata.st_mode)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > 4096
                or not mode & stat.S_IRUSR
                or mode & ~0o640
            ):
                raise ValueError("invalid token file")
            raw = os.read(file_fd, 4097)
            if len(raw) != metadata.st_size:
                raise ValueError("token file changed while reading")
        finally:
            os.close(file_fd)
        if raw.endswith(b"\r\n"):
            payload = raw[:-2]
        elif raw.endswith(b"\n"):
            payload = raw[:-1]
        else:
            payload = raw
        if not 32 <= len(payload) <= 4096 or any(
            byte < 0x21 or byte > 0x7E for byte in payload
        ):
            raise ValueError("invalid token content")
        token = payload.decode("ascii")
    except (OSError, UnicodeError, ValueError) as exc:
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "Sandbox 控制面凭据不可用",
            retryable=True,
            stop=False,
        ) from exc
    return token


class HttpSandboxdBackend:
    def __init__(
        self,
        *,
        socket_path: str,
        token_file: str,
        base_url: str = "http://sandboxd",
        timeout_seconds: float = 15.0,
        run_timeout_seconds: float = 165.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.socket_path = str(socket_path)
        self.token_file = Path(token_file)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.run_timeout_seconds = float(run_timeout_seconds)
        self._owns_client = client is None
        self.client = client or httpx.Client(
            transport=httpx.HTTPTransport(uds=self.socket_path),
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _token(self) -> str:
        return _read_token_file(self.token_file)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        run_request: bool = False,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        headers["X-Nanobot-Request-ID"] = request_id or secrets.token_hex(16)
        try:
            response = self.client.request(
                method,
                path,
                json=dict(payload) if payload is not None else None,
                headers=headers,
                timeout=self.run_timeout_seconds if run_request else self.timeout_seconds,
            )
            body = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 控制面暂时不可用",
                retryable=True,
                hint="稍后重试；主聊天功能不受影响",
                stop=False,
            ) from exc
        if not isinstance(body, dict):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 控制面返回了无效响应",
                retryable=True,
                stop=False,
            )
        if response.is_error or body.get("status") == "error":
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            raw_code = str(error.get("code") or SandboxErrorCode.RUNTIME_UNAVAILABLE.value)
            try:
                code = SandboxErrorCode(raw_code)
            except ValueError:
                code = SandboxErrorCode.RUNTIME_UNAVAILABLE
            raise SandboxServiceError(
                code,
                str(body.get("summary") or "Sandbox 请求失败"),
                retryable=bool(error.get("retryable", False)),
                hint=str(error.get("hint") or ""),
                stop=bool(error.get("stop", True)),
            )
        return body

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/healthz")

    def ready(self) -> dict[str, Any]:
        return self._request("GET", "/v1/readyz")

    def ensure_workspace(self, workspace_id: str, *, request_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/workspaces/ensure",
            payload={"workspace_id": workspace_id},
            request_id=request_id,
        )

    def list_files(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/files/list", payload=payload)

    def read_file(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/files/read", payload=payload)

    def search_files(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/files/search", payload=payload)

    def write_file(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/files/write", payload=payload)

    def publish_asset(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/assets/publish", payload=payload)

    def stage_assets(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/assets/stage", payload=payload)

    def run(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request_id = str(payload.get("request_id") or "")
        return self._request(
            "POST",
            "/v1/runs",
            payload=payload,
            request_id=request_id or None,
            run_request=True,
        )

    def cancel_run(self, run_id: str, *, request_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/runs/{run_id}/cancel",
            payload={},
            request_id=request_id,
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/runs/{run_id}")


class AsyncSandboxdAssetClient:
    """Nanobot 不挂载数据目录，仅通过 UDS 流式代理大资产。"""

    def __init__(
        self,
        *,
        socket_path: str,
        token_file: str,
        timeout_seconds: float = 600.0,
        base_url: str = "http://sandboxd",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.token_file = Path(token_file)
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(socket_path)),
            base_url=base_url.rstrip("/"),
            timeout=float(timeout_seconds),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def _headers(self, *, request_id: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {_read_token_file(self.token_file)}",
            "X-Nanobot-Request-ID": request_id or secrets.token_hex(16),
        }

    @staticmethod
    async def _response_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except (ValueError, TypeError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 控制面返回了无效响应",
                retryable=True,
                stop=False,
            ) from exc
        if not isinstance(body, dict):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 控制面返回了无效响应",
                retryable=True,
                stop=False,
            )
        if response.is_error or body.get("status") == "error":
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            try:
                code = SandboxErrorCode(str(error.get("code") or "runtime_unavailable"))
            except ValueError:
                code = SandboxErrorCode.RUNTIME_UNAVAILABLE
            raise SandboxServiceError(
                code,
                str(body.get("summary") or "Sandbox 请求失败"),
                retryable=bool(error.get("retryable", False)),
                hint=str(error.get("hint") or ""),
                stop=bool(error.get("stop", True)),
            )
        return body

    async def upload_asset(
        self,
        *,
        workspace_id: str,
        media_type: str,
        content: AsyncIterable[bytes],
        content_length: int | None,
        request_id: str,
    ) -> dict[str, Any]:
        workspace_id = validate_workspace_id(workspace_id)
        headers = self._headers(request_id=request_id)
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        try:
            response = await self.client.post(
                "/v1/assets/upload",
                params={"workspace_id": workspace_id, "media_type": media_type},
                headers=headers,
                content=content,
            )
        except httpx.HTTPError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 资产传输暂时不可用",
                retryable=True,
                stop=False,
            ) from exc
        return await self._response_body(response)

    async def open_asset(
        self,
        sha256: str,
        *,
        range_header: str = "",
    ) -> httpx.Response:
        sha256 = validate_sha256(sha256)
        headers = self._headers()
        if range_header:
            headers["Range"] = range_header
        request = self.client.build_request(
            "GET",
            f"/v1/assets/{sha256}",
            headers=headers,
        )
        try:
            response = await self.client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 资产传输暂时不可用",
                retryable=True,
                stop=False,
            ) from exc
        if response.status_code in {200, 206, 416}:
            return response
        error_body = bytearray()
        try:
            if response.is_stream_consumed:
                error_body.extend(response.content[:4096])
            else:
                async for chunk in response.aiter_raw():
                    remaining = 4096 - len(error_body)
                    if remaining <= 0:
                        break
                    error_body.extend(chunk[:remaining])
                    if len(error_body) >= 4096:
                        break
        finally:
            await response.aclose()
        bounded_response = httpx.Response(
            response.status_code,
            content=bytes(error_body),
        )
        await self._response_body(bounded_response)
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "Sandbox 资产传输暂时不可用",
        )
