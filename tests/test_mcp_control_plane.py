"""MCP transport、配置隔离、秘密边界与正式 Runtime 接入测试。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
from types import MappingProxyType, SimpleNamespace
from typing import Any, Mapping

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clients.mcp import McpSdkClient
from core.agent_runtime import (
    RuntimeMcpToolDescriptor,
    RuntimeToolEffectClass,
    mcp_wire_tool_name,
)
from core.database import AdminAuditLog, SessionLocal
from core.db.models.mcp import McpDiagnosticRow, McpSecretRow
from core.mcp import (
    McpCallResult,
    McpCatalogCache,
    McpClientFailure,
    McpConfigurationConflict,
    McpConfigurationService,
    McpControlPlaneError,
    McpDiscoveryResult,
    McpRuntimeService,
    McpSecretReference,
    McpSecretService,
    McpServerConfig,
    current_mcp_binding_id,
    current_mcp_effect_class,
    current_mcp_execution_port,
    mcp_request_runtime_scope,
)
from nanobot_kt.mcp_runtime import McpProxyTool


def _descriptor(
    server_id: str,
    name: str = "search",
    *,
    read_only: bool = True,
) -> RuntimeMcpToolDescriptor:
    return RuntimeMcpToolDescriptor(
        provider_id="mcp",
        server_id=server_id,
        tool_name=name,
        input_schema_json=(
            b'{"properties":{"query":{"type":"string"}},"type":"object"}'
        ),
        execution_port_id=f"mcp.execute.{server_id}.{name}",
        description="搜索外部测试数据",
        read_only=read_only,
    )


def _http_config(
    server_id: str,
    *,
    enabled: bool = True,
    secret_refs: tuple[McpSecretReference, ...] = (),
    auth_mode: str = "none",
) -> McpServerConfig:
    return McpServerConfig(
        server_id=server_id,
        display_name=server_id,
        transport="http",
        endpoint=f"https://{server_id}.example.test/mcp",
        enabled=enabled,
        secret_refs=secret_refs,
        auth_mode=auth_mode,
    )


class _FakeClient:
    def __init__(self, *, failures: set[str] | None = None, leak: str = "") -> None:
        self.failures = set(failures or set())
        self.leak = leak
        self.discoveries: list[tuple[str, dict[str, str]]] = []
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []

    async def discover(
        self,
        config: McpServerConfig,
        secret_values: Mapping[str, str],
    ) -> McpDiscoveryResult:
        self.discoveries.append((config.server_id, dict(secret_values)))
        if config.server_id in self.failures:
            raise McpClientFailure(
                "connect_failed",
                phase="discover",
                error_type="FakeConnectionError",
                retryable=True,
            )
        return McpDiscoveryResult(
            server_id=config.server_id,
            tools=(_descriptor(config.server_id),),
            latency_ms=3,
        )

    async def call(
        self,
        config: McpServerConfig,
        descriptor: RuntimeMcpToolDescriptor,
        arguments: Mapping[str, Any],
        secret_values: Mapping[str, str],
    ) -> McpCallResult:
        self.calls.append((
            config.server_id,
            descriptor.tool_name,
            dict(arguments),
            dict(secret_values),
        ))
        return McpCallResult(
            payload={
                "content": [
                    {"type": "text", "text": f"result:{self.leak}"},
                    {"type": "resource_link", "uri": "memory://one"},
                ],
                "structuredContent": {"secret_echo": self.leak},
                "_meta": {"credential": self.leak},
                "isError": False,
            },
            is_error=False,
            latency_ms=4,
        )


def test_mcp_transport_configs_require_scoped_references_and_safe_urls():
    stdio = McpServerConfig(
        server_id="local-files",
        display_name="Local Files",
        transport="stdio",
        command="python",
        args=("-m", "example_server"),
        secret_refs=(McpSecretReference("env.API_TOKEN", "local.api-token"),),
    )
    sse = McpServerConfig(
        server_id="legacy-events",
        display_name="Legacy SSE",
        transport="sse",
        endpoint="https://mcp.example.test/sse",
        auth_mode="bearer",
        secret_refs=(McpSecretReference("auth.bearer", "legacy.token"),),
    )
    http = McpServerConfig(
        server_id="modern-http",
        display_name="Modern HTTP",
        transport="http",
        endpoint="https://mcp.example.test/mcp",
        auth_mode="oauth_client_credentials",
        oauth_token_url="https://auth.example.test/oauth/token",
        oauth_scopes=("tools.read",),
        secret_refs=(
            McpSecretReference("oauth.client_id", "modern.client-id"),
            McpSecretReference("oauth.client_secret", "modern.client-secret"),
        ),
    )

    assert [stdio.transport.value, sse.transport.value, http.transport.value] == [
        "stdio",
        "sse",
        "http",
    ]
    with pytest.raises(McpControlPlaneError, match="内嵌凭据"):
        McpServerConfig(
            server_id="unsafe-url",
            display_name="Unsafe",
            transport="http",
            endpoint="https://user:password@example.test/mcp",
        )
    with pytest.raises(McpControlPlaneError, match="保留目标"):
        McpSecretReference("header.Authorization", "unsafe.token")
    with pytest.raises(McpControlPlaneError, match="stdio 只接受"):
        McpServerConfig(
            server_id="unsafe-stdio",
            display_name="Unsafe",
            transport="stdio",
            command="python",
            secret_refs=(McpSecretReference("header.X-Key", "unsafe.key"),),
        )


def test_mcp_wire_names_are_namespaced_bounded_and_schema_snapshot_pinned():
    first = _descriptor("server-a", "search.records")
    second = _descriptor("server-b", "search.records")
    long_name = mcp_wire_tool_name("server-a", "x" * 128)

    assert first.wire_name == "server-a__search_records"
    assert second.wire_name == "server-b__search_records"
    assert first.wire_name != second.wire_name
    assert len(long_name) == 64
    assert first.input_schema_sha256


def test_mcp_configuration_replace_is_atomic_and_cas_guarded(db_session):
    service = McpConfigurationService(db_session)
    first = _http_config("first")
    initial = service.replace_all((first,), expected_revision=0, actor_id="admin")
    db_session.commit()

    assert initial.revision == 1
    assert service.snapshot().servers == (first,)
    with pytest.raises(McpConfigurationConflict):
        service.replace_all(
            (_http_config("stale"),),
            expected_revision=0,
            actor_id="admin",
        )
    db_session.rollback()
    current = service.snapshot()
    assert current.revision == 1
    assert [item.server_id for item in current.servers] == ["first"]


def test_mcp_secrets_are_encrypted_and_resolved_only_by_binding(
    db_session,
    monkeypatch,
):
    monkeypatch.setenv(
        "NANOBOT_MCP_CREDENTIAL_SECRET",
        "mcp-test-root-secret-0123456789abcdef",
    )
    service = McpSecretService(db_session)
    service.replace("github.token", "Bearer-SHOULD-NOT-LEAK")
    db_session.commit()

    row = db_session.get(McpSecretRow, "github.token")
    assert row is not None
    assert "SHOULD-NOT-LEAK" not in row.encrypted_value
    resolved = service.resolve((
        McpSecretReference("auth.bearer", "github.token"),
    ))
    assert resolved == {"auth.bearer": "Bearer-SHOULD-NOT-LEAK"}
    assert "SHOULD-NOT-LEAK" not in repr(service)


@pytest.mark.asyncio
async def test_bad_mcp_server_isolated_while_healthy_server_enters_tool_plan(
    db_session,
):
    configs = (_http_config("broken"), _http_config("healthy"))
    McpConfigurationService(db_session).replace_all(
        configs,
        expected_revision=0,
        actor_id="admin",
    )
    db_session.commit()
    client = _FakeClient(failures={"broken"})
    result = await McpRuntimeService(
        db_session,
        client=client,
        session_factory=SessionLocal,
        cache=McpCatalogCache(ttl_seconds=60),
    ).build_request(existing_tool_names={"reply", "no_reply"})

    assert result.runtime is not None
    assert result.healthy_server_count == 1
    assert result.failed_server_count == 1
    assert [item.wire_name for item in result.snapshot.tools] == [
        "healthy__search"
    ]
    assert result.tool_schemas[0]["function"]["name"] == "healthy__search"
    db_session.flush()
    rows = db_session.query(McpDiagnosticRow).all()
    assert {row.server_id for row in rows} == {"broken", "healthy"}
    assert all("example.test" not in row.error_type for row in rows)


@pytest.mark.asyncio
async def test_mcp_catalog_identity_and_namespace_collisions_are_isolated(
    db_session,
):
    class WrongIdentityClient(_FakeClient):
        async def discover(self, config, secret_values):
            if config.server_id == "bad-identity":
                return McpDiscoveryResult(
                    server_id="smuggled",
                    tools=(_descriptor("smuggled"),),
                    latency_ms=1,
                )
            return await super().discover(config, secret_values)

    McpConfigurationService(db_session).replace_all(
        (_http_config("bad-identity"), _http_config("good-identity")),
        expected_revision=0,
        actor_id="admin",
    )
    db_session.commit()
    identity = await McpRuntimeService(
        db_session,
        client=WrongIdentityClient(),
        session_factory=SessionLocal,
        cache=McpCatalogCache(ttl_seconds=60),
    ).build_request(existing_tool_names=set())

    assert identity.runtime is not None
    assert [item.server_id for item in identity.snapshot.tools] == [
        "good-identity"
    ]
    assert identity.failed_server_count == 1

    McpConfigurationService(db_session).replace_all(
        (_http_config("a.b"), _http_config("a_b")),
        expected_revision=1,
        actor_id="admin",
    )
    db_session.commit()
    collision = await McpRuntimeService(
        db_session,
        client=_FakeClient(),
        session_factory=SessionLocal,
        cache=McpCatalogCache(ttl_seconds=60),
    ).build_request(existing_tool_names=set())

    assert collision.runtime is not None
    assert [item.server_id for item in collision.snapshot.tools] == ["a.b"]
    assert collision.failed_server_count == 1


@pytest.mark.asyncio
async def test_runtime_call_preserves_blocks_scrubs_credentials_and_binds_native_port(
    db_session,
    monkeypatch,
):
    runtime_events: list[tuple[str, str, dict[str, object]]] = []

    def capture_runtime_event(name, phase, *, attributes=None, context=None):
        del context
        runtime_events.append((name, phase, dict(attributes or {})))
        return None

    monkeypatch.setattr(
        "core.runtime.event_bus.emit_runtime_event",
        capture_runtime_event,
    )
    secret = "MCP-CREDENTIAL-MUST-NOT-LEAK"
    monkeypatch.setenv(
        "NANOBOT_MCP_CREDENTIAL_SECRET",
        "mcp-test-root-secret-0123456789abcdef",
    )
    ref = McpSecretReference("auth.bearer", "remote.token")
    config = _http_config("remote", secret_refs=(ref,), auth_mode="bearer")
    McpConfigurationService(db_session).replace_all(
        (config,),
        expected_revision=0,
        actor_id="admin",
    )
    McpSecretService(db_session).replace("remote.token", secret)
    db_session.commit()
    client = _FakeClient(leak=secret)
    result = await McpRuntimeService(
        db_session,
        client=client,
        session_factory=SessionLocal,
        cache=McpCatalogCache(ttl_seconds=60),
    ).build_request(existing_tool_names=set())
    runtime = result.runtime
    assert runtime is not None

    with mcp_request_runtime_scope(runtime):
        assert current_mcp_binding_id("remote__search").startswith("mcp.execute")
        # 外部 server 自报 readOnlyHint 不能下调服务端授权等级。
        assert current_mcp_effect_class("remote__search") is RuntimeToolEffectClass.EXTERNAL
        assert current_mcp_execution_port("remote__search") is runtime.execution_port
        call = await runtime.call("remote__search", {"query": "hello"})
        kt_result = await McpProxyTool("remote__search", "search").execute(
            {"query": "kt"},
            context=None,
        )

    serialized = json.dumps(dict(call.payload), ensure_ascii=False)
    assert secret not in serialized
    assert "_meta" not in serialized
    assert "resource_link" in serialized
    assert "structuredContent" in serialized
    assert kt_result.success
    assert secret not in str(kt_result.output)
    assert client.calls[0][3] == {"auth.bearer": secret}

    with mcp_request_runtime_scope(runtime):
        with pytest.raises(McpClientFailure) as invalid:
            await runtime.call("remote__search", {"query": 1})
    assert invalid.value.code == "arguments_invalid"
    assert len(client.calls) == 2
    mcp_events = [item for item in runtime_events if item[0] == "mcp.call"]
    assert [item[1] for item in mcp_events] == [
        "started",
        "succeeded",
        "started",
        "succeeded",
        "started",
        "failed",
    ]
    assert mcp_events[-1][2]["failure_code"] == "arguments_invalid"
    assert all(item[2]["server_id"] == "remote" for item in mcp_events)
    serialized_events = json.dumps(mcp_events, ensure_ascii=False)
    assert secret not in serialized_events
    assert "hello" not in serialized_events
    assert "kt" not in serialized_events


@pytest.mark.asyncio
async def test_sdk_stdio_transport_discovers_and_calls_real_server():
    fixture = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    config = McpServerConfig(
        server_id="stdio-test",
        display_name="Stdio Test",
        transport="stdio",
        command=sys.executable,
        args=(str(fixture),),
        enabled=True,
        connect_timeout_seconds=10,
        request_timeout_seconds=10,
        reconnect_attempts=0,
    )
    client = McpSdkClient()

    discovery = await client.discover(config, MappingProxyType({}))
    assert [item.tool_name for item in discovery.tools] == ["echo"]
    result = await client.call(
        config,
        discovery.tools[0],
        {"value": "你好"},
        MappingProxyType({}),
    )
    serialized = json.dumps(dict(result.payload), ensure_ascii=False)
    assert not result.is_error
    assert "你好" in serialized
    assert "content" in serialized


@pytest.mark.asyncio
async def test_sdk_selects_sse_and_http_and_keeps_oauth_local(monkeypatch):
    import clients.mcp as module

    calls: list[tuple[str, str, dict[str, str]]] = []

    @asynccontextmanager
    async def fake_sse(url, *, headers, **_kwargs):
        calls.append(("sse", url, dict(headers)))
        yield (object(), object())

    @asynccontextmanager
    async def fake_http(url, *, headers, **_kwargs):
        calls.append(("http", url, dict(headers)))
        yield (object(), object(), lambda: None)

    class FakeSession:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def initialize(self):
            return None

    monkeypatch.setattr(module, "sse_client", fake_sse)
    monkeypatch.setattr(module, "streamablehttp_client", fake_http)
    monkeypatch.setattr(module, "ClientSession", FakeSession)
    client = McpSdkClient()
    monkeypatch.setattr(client, "_oauth_token", lambda *_args: _async_value("oauth-secret"))
    sse = McpServerConfig(
        server_id="sse-test",
        display_name="SSE",
        transport="sse",
        endpoint="https://sse.example.test/events",
    )
    http = McpServerConfig(
        server_id="http-test",
        display_name="HTTP",
        transport="http",
        endpoint="https://http.example.test/mcp",
        auth_mode="oauth_client_credentials",
        oauth_token_url="https://auth.example.test/token",
        secret_refs=(
            McpSecretReference("oauth.client_id", "client.id"),
            McpSecretReference("oauth.client_secret", "client.secret"),
        ),
    )

    async with client._session(sse, {}) as (_session, prepared):
        assert not prepared.headers
    async with client._session(
        http,
        {"oauth.client_id": "id", "oauth.client_secret": "secret"},
    ) as (_session, prepared):
        assert prepared.headers["Authorization"] == "Bearer oauth-secret"
    assert [item[0] for item in calls] == ["sse", "http"]
    assert "oauth-secret" not in repr(client)


@pytest.mark.asyncio
async def test_sdk_reconnects_before_dispatch_but_never_replays_ambiguous_call(
    monkeypatch,
):
    config = McpServerConfig(
        server_id="retry-test",
        display_name="Retry Test",
        transport="http",
        endpoint="https://retry.example.test/mcp",
        enabled=True,
        reconnect_attempts=2,
    )
    descriptor = _descriptor("retry-test")

    class SuccessfulSession:
        async def call_tool(self, _name, *, arguments):
            return {
                "content": [{"type": "text", "text": arguments["query"]}],
                "isError": False,
            }

    reconnecting = McpSdkClient()
    reconnect_count = 0

    @asynccontextmanager
    async def reconnecting_session(_config, _secrets):
        nonlocal reconnect_count
        reconnect_count += 1
        if reconnect_count == 1:
            raise OSError("connect failed before dispatch")
        yield SuccessfulSession(), SimpleNamespace(redactions=())

    async def stable_catalog(_session, _config):
        return (descriptor,)

    monkeypatch.setattr(reconnecting, "_session", reconnecting_session)
    monkeypatch.setattr(reconnecting, "_list_tools", stable_catalog)
    result = await reconnecting.call(
        config,
        descriptor,
        {"query": "ok"},
        {},
    )
    assert not result.is_error
    assert reconnect_count == 2

    class AmbiguousSession:
        async def call_tool(self, _name, *, arguments):
            del arguments
            raise TimeoutError("result unknown after dispatch")

    ambiguous = McpSdkClient()
    ambiguous_count = 0

    @asynccontextmanager
    async def ambiguous_session(_config, _secrets):
        nonlocal ambiguous_count
        ambiguous_count += 1
        yield AmbiguousSession(), SimpleNamespace(redactions=())

    monkeypatch.setattr(ambiguous, "_session", ambiguous_session)
    monkeypatch.setattr(ambiguous, "_list_tools", stable_catalog)
    with pytest.raises(McpClientFailure) as failure:
        await ambiguous.call(config, descriptor, {"query": "unknown"}, {})
    assert failure.value.code == "call_result_ambiguous"
    assert failure.value.ambiguous is True
    assert failure.value.retryable is False
    assert ambiguous_count == 1


async def _async_value(value: str) -> str:
    return value


def test_mcp_admin_configuration_and_secret_responses_never_echo_value(
    client,
    db_session,
    monkeypatch,
):
    import api.admin_routes as admin_routes

    monkeypatch.setattr(admin_routes, "NANOBOT_ADMIN_TOKEN", "admin-token")
    monkeypatch.setenv(
        "NANOBOT_MCP_CREDENTIAL_SECRET",
        "mcp-test-root-secret-0123456789abcdef",
    )
    headers = {"Authorization": "Bearer admin-token"}
    secret = "ADMIN-MCP-SECRET-MUST-NOT-ECHO"
    written = client.put(
        "/api/v1/admin/mcp/secrets/admin.token",
        json={"action": "replace", "value": secret},
        headers=headers,
    )
    assert written.status_code == 200
    created = client.put(
        "/api/v1/admin/mcp",
        json={
            "expected_revision": 0,
            "servers": [{
                "server_id": "admin-http",
                "display_name": "Admin HTTP",
                "transport": "http",
                "enabled": True,
                "endpoint": "https://mcp.example.test/mcp",
                "auth_mode": "bearer",
                "secret_refs": [{
                    "binding": "auth.bearer",
                    "secret_id": "admin.token",
                }],
            }],
        },
        headers=headers,
    )
    assert created.status_code == 200
    serialized = json.dumps(created.json(), ensure_ascii=False)
    assert secret not in serialized
    assert created.json()["servers"][0]["secret_refs"][0]["configured"] is True
    disabled = client.patch(
        "/api/v1/admin/mcp/servers/admin-http/enabled",
        json={"expected_revision": 1, "enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["revision"] == 2
    assert disabled.json()["servers"][0]["enabled"] is False
    assert secret not in json.dumps(disabled.json(), ensure_ascii=False)


def test_mcp_secret_audit_failure_rolls_back_secret(
    client,
    db_session,
    monkeypatch,
):
    import api.admin_routes as admin_routes

    monkeypatch.setattr(admin_routes, "NANOBOT_ADMIN_TOKEN", "admin-token")
    monkeypatch.setenv(
        "NANOBOT_MCP_CREDENTIAL_SECRET",
        "mcp-audit-root-secret-0123456789abcdef",
    )

    def fail_admin_audit(session, _flush_context, _instances):
        if any(isinstance(item, AdminAuditLog) for item in session.new):
            raise RuntimeError("simulated mcp audit failure")

    event.listen(Session, "before_flush", fail_admin_audit)
    try:
        response = client.put(
            "/api/v1/admin/mcp/secrets/audit.token",
            headers={"Authorization": "Bearer admin-token"},
            json={"action": "replace", "value": "must-not-persist"},
        )
    finally:
        event.remove(Session, "before_flush", fail_admin_audit)
        db_session.rollback()

    assert response.status_code == 500
    assert db_session.get(McpSecretRow, "audit.token") is None
    assert db_session.query(AdminAuditLog).count() == 0


def test_mcp_schema_migration_creates_control_tables_and_append_only_diagnostics():
    from core.schema_migrations import (
        MIGRATIONS,
        _MCP_CONTROL_PLANE_V1_VERSION,
        _mcp_control_plane_v1,
    )

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _mcp_control_plane_v1(connection, engine, None)
        connection.execute(text(
            "INSERT INTO mcp_diagnostics("
            "server_id, config_sha256, transport, operation, status, "
            "retryable, ambiguous, latency_ms, tool_count"
            ") VALUES ("
            f"'server', '{('a' * 64)}', 'http', 'health', 'healthy', "
            "0, 0, 1, 1)"
        ))
    assert {
        "mcp_configuration_state",
        "mcp_servers",
        "mcp_secrets",
        "mcp_diagnostics",
    } <= set(inspect(engine).get_table_names())
    assert _MCP_CONTROL_PLANE_V1_VERSION in {
        version for version, _name, _migration in MIGRATIONS
    }
    with pytest.raises(IntegrityError, match="mcp_diagnostics_append_only"):
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE mcp_diagnostics SET latency_ms=2 WHERE server_id='server'"
            ))
