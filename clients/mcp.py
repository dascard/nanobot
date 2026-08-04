"""基于官方 Python SDK 的 MCP stdio、SSE 与 Streamable HTTP 客户端。"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import os
import time
from typing import Any, AsyncIterator, Mapping

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from core.agent_runtime import RuntimeMcpSnapshot, RuntimeMcpToolDescriptor
from core.mcp import (
    McpAuthMode,
    McpCallResult,
    McpClientFailure,
    McpDiscoveryResult,
    McpServerConfig,
    McpTransportKind,
)


@dataclass(frozen=True, slots=True)
class _PreparedTransport:
    headers: Mapping[str, str]
    env: Mapping[str, str]
    redactions: tuple[str, ...]


def _safe_failure(
    code: str,
    *,
    phase: str,
    exc: BaseException,
    retryable: bool,
    ambiguous: bool = False,
    started_at: float,
) -> McpClientFailure:
    return McpClientFailure(
        code,
        phase=phase,
        error_type=type(exc).__name__,
        retryable=retryable,
        ambiguous=ambiguous,
        latency_ms=max(0, int((time.monotonic() - started_at) * 1000)),
    )


def _strip_private_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_private_metadata(item)
            for key, item in value.items()
            if str(key) != "_meta"
        }
    if isinstance(value, (list, tuple)):
        return [_strip_private_metadata(item) for item in value]
    return value


def _redact(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if secret:
                result = result.replace(secret, "[REDACTED]")
        return result
    if isinstance(value, Mapping):
        return {str(key): _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, secrets) for item in value]
    return value


def _dump_model(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, Mapping):
        return dict(value)
    return value


class McpSdkClient:
    """所有失败均压缩为无 URL、无响应正文、无 SDK message 的稳定分类。"""

    async def _oauth_token(
        self,
        config: McpServerConfig,
        secrets: Mapping[str, str],
    ) -> str:
        started = time.monotonic()
        client_id = str(secrets.get("oauth.client_id") or "")
        client_secret = str(secrets.get("oauth.client_secret") or "")
        if not client_id or not client_secret:
            raise McpClientFailure(
                "secret_unavailable",
                phase="oauth",
                error_type="McpSecretUnavailable",
                retryable=False,
            )
        try:
            async with httpx.AsyncClient(
                timeout=config.connect_timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    config.oauth_token_url,
                    data={
                        "grant_type": "client_credentials",
                        **(
                            {"scope": " ".join(config.oauth_scopes)}
                            if config.oauth_scopes
                            else {}
                        ),
                    },
                    auth=httpx.BasicAuth(client_id, client_secret),
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
                token = str(
                    payload.get("access_token") if isinstance(payload, dict) else ""
                )
                token_type = str(
                    payload.get("token_type", "Bearer")
                    if isinstance(payload, dict)
                    else ""
                ).strip().lower()
                if not token or token_type != "bearer":
                    raise ValueError("OAuth token response contract invalid")
                return token
        except McpClientFailure:
            raise
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            raise _safe_failure(
                "oauth_failed",
                phase="oauth",
                exc=exc,
                retryable=False,
                started_at=started,
            ) from exc

    async def _prepare(
        self,
        config: McpServerConfig,
        secret_values: Mapping[str, str],
    ) -> _PreparedTransport:
        headers: dict[str, str] = {}
        env: dict[str, str] = {}
        redactions = [str(value) for value in secret_values.values() if value]
        for binding, value in secret_values.items():
            if binding.startswith("header."):
                headers[binding[7:]] = str(value)
            elif binding.startswith("env."):
                env[binding[4:]] = str(value)
        if config.auth_mode is McpAuthMode.BEARER:
            bearer = str(secret_values.get("auth.bearer") or "")
            if not bearer:
                raise McpClientFailure(
                    "secret_unavailable",
                    phase="auth",
                    error_type="McpSecretUnavailable",
                    retryable=False,
                )
            headers["Authorization"] = f"Bearer {bearer}"
        elif config.auth_mode is McpAuthMode.OAUTH_CLIENT_CREDENTIALS:
            token = await self._oauth_token(config, secret_values)
            headers["Authorization"] = f"Bearer {token}"
            redactions.append(token)
        return _PreparedTransport(
            headers=headers,
            env=env,
            redactions=tuple(dict.fromkeys(redactions)),
        )

    @asynccontextmanager
    async def _session(
        self,
        config: McpServerConfig,
        secret_values: Mapping[str, str],
    ) -> AsyncIterator[tuple[ClientSession, _PreparedTransport]]:
        prepared = await self._prepare(config, secret_values)
        stack = AsyncExitStack()
        try:
            async with asyncio.timeout(config.connect_timeout_seconds):
                if config.transport is McpTransportKind.STDIO:
                    stderr_sink = stack.enter_context(
                        open(os.devnull, "w", encoding="utf-8")
                    )
                    streams = await stack.enter_async_context(stdio_client(
                        StdioServerParameters(
                            command=config.command,
                            args=list(config.args),
                            env=dict(prepared.env),
                            cwd=config.cwd or None,
                        ),
                        errlog=stderr_sink,
                    ))
                elif config.transport is McpTransportKind.SSE:
                    streams = await stack.enter_async_context(sse_client(
                        config.endpoint,
                        headers=dict(prepared.headers),
                        timeout=config.connect_timeout_seconds,
                        sse_read_timeout=config.sse_read_timeout_seconds,
                    ))
                else:
                    streams = await stack.enter_async_context(streamablehttp_client(
                        config.endpoint,
                        headers=dict(prepared.headers),
                        timeout=config.connect_timeout_seconds,
                        sse_read_timeout=config.sse_read_timeout_seconds,
                        terminate_on_close=True,
                    ))
                read_stream, write_stream = streams[:2]
                session = await stack.enter_async_context(ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(
                        seconds=config.request_timeout_seconds
                    ),
                ))
                await session.initialize()
            yield session, prepared
        finally:
            await stack.aclose()

    async def _list_tools(
        self,
        session: ClientSession,
        config: McpServerConfig,
    ) -> tuple[RuntimeMcpToolDescriptor, ...]:
        tools: list[Any] = []
        cursor: str | None = None
        for _page in range(20):
            result = await asyncio.wait_for(
                session.list_tools(cursor=cursor),
                timeout=config.request_timeout_seconds,
            )
            tools.extend(tuple(result.tools))
            if len(tools) > config.max_tools:
                raise McpClientFailure(
                    "server_tool_budget_exceeded",
                    phase="list_tools",
                    error_type="McpToolBudgetError",
                    retryable=False,
                )
            cursor = str(result.nextCursor or "") or None
            if cursor is None:
                break
        if cursor is not None:
            raise McpClientFailure(
                "server_tool_pagination_exceeded",
                phase="list_tools",
                error_type="McpPaginationError",
                retryable=False,
            )
        descriptors: list[RuntimeMcpToolDescriptor] = []
        for tool in tools:
            schema = _dump_model(getattr(tool, "inputSchema", {}))
            schema_json = json.dumps(
                schema,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            annotations = _dump_model(getattr(tool, "annotations", None))
            read_only = bool(
                annotations.get("readOnlyHint", False)
                if isinstance(annotations, Mapping)
                else False
            )
            tool_name = str(getattr(tool, "name", "") or "")
            schema_hash = hashlib.sha256(schema_json).hexdigest()
            binding_hash = hashlib.sha256(
                (
                    f"{config.config_sha256}:{tool_name}:{schema_hash}"
                ).encode("utf-8")
            ).hexdigest()
            descriptors.append(RuntimeMcpToolDescriptor(
                provider_id="mcp",
                server_id=config.server_id,
                tool_name=tool_name,
                input_schema_json=schema_json,
                execution_port_id=f"mcp.execute.{binding_hash}",
                description=str(getattr(tool, "description", "") or "")[:4000],
                read_only=read_only,
            ))
        snapshot = RuntimeMcpSnapshot(
            provider_id="mcp",
            revision=f"server:{config.config_sha256[:16]}",
            tools=tuple(descriptors),
        )
        return snapshot.tools

    async def discover(
        self,
        config: McpServerConfig,
        secret_values: Mapping[str, str],
    ) -> McpDiscoveryResult:
        started = time.monotonic()
        last_failure: McpClientFailure | None = None
        for attempt in range(config.reconnect_attempts + 1):
            try:
                async with self._session(config, secret_values) as (session, _prepared):
                    tools = await self._list_tools(session, config)
                return McpDiscoveryResult(
                    server_id=config.server_id,
                    tools=tools,
                    latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                )
            except McpClientFailure as failure:
                last_failure = failure
                if not failure.retryable:
                    raise
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                last_failure = _safe_failure(
                    "connect_or_discovery_failed",
                    phase="discover",
                    exc=exc,
                    retryable=True,
                    started_at=started,
                )
            if attempt >= config.reconnect_attempts:
                break
        assert last_failure is not None
        raise McpClientFailure(
            last_failure.code,
            phase=last_failure.phase,
            error_type=last_failure.error_type,
            retryable=True,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        )

    async def call(
        self,
        config: McpServerConfig,
        descriptor: RuntimeMcpToolDescriptor,
        arguments: Mapping[str, Any],
        secret_values: Mapping[str, str],
    ) -> McpCallResult:
        started = time.monotonic()
        last_failure: McpClientFailure | None = None
        for attempt in range(config.reconnect_attempts + 1):
            dispatched = False
            try:
                async with self._session(config, secret_values) as (session, prepared):
                    current = {
                        item.tool_name: item
                        for item in await self._list_tools(session, config)
                    }.get(descriptor.tool_name)
                    if (
                        current is None
                        or current.input_schema_sha256
                        != descriptor.input_schema_sha256
                    ):
                        raise McpClientFailure(
                            "tool_schema_drift",
                            phase="call",
                            error_type="McpSchemaDrift",
                            retryable=False,
                        )
                    dispatched = True
                    raw = await asyncio.wait_for(
                        session.call_tool(
                            descriptor.tool_name,
                            arguments=dict(arguments),
                        ),
                        timeout=config.request_timeout_seconds,
                    )
                    dumped = _strip_private_metadata(_dump_model(raw))
                    payload = _redact(dumped, prepared.redactions)
                    return McpCallResult(
                        payload=(payload if isinstance(payload, Mapping) else {}),
                        is_error=bool(
                            payload.get("isError", False)
                            if isinstance(payload, Mapping)
                            else False
                        ),
                        latency_ms=max(
                            0,
                            int((time.monotonic() - started) * 1000),
                        ),
                    )
            except McpClientFailure as failure:
                last_failure = failure
                if dispatched or not failure.retryable:
                    raise
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                if dispatched:
                    raise _safe_failure(
                        "call_result_ambiguous",
                        phase="call",
                        exc=exc,
                        retryable=False,
                        ambiguous=True,
                        started_at=started,
                    ) from exc
                last_failure = _safe_failure(
                    "connect_or_validation_failed",
                    phase="call",
                    exc=exc,
                    retryable=True,
                    started_at=started,
                )
            if attempt >= config.reconnect_attempts:
                break
        assert last_failure is not None
        raise McpClientFailure(
            last_failure.code,
            phase=last_failure.phase,
            error_type=last_failure.error_type,
            retryable=True,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        )


__all__ = ["McpSdkClient"]
