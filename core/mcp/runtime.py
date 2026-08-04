"""MCP server 故障隔离、请求级 schema 冻结与动态 execution port。"""

from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import json
import time
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from sqlalchemy.orm import Session

from core.agent_runtime import (
    AgentRuntimeCapabilityError,
    RuntimeMcpSnapshot,
    RuntimeMcpToolDescriptor,
    RuntimeRunError,
    RuntimeToolCall,
    RuntimeToolCallStatus,
    RuntimeToolEffectClass,
    RuntimeToolExecutionRequest,
    RuntimeToolExecutionResult,
    ToolExecutionPort,
)
from core.mcp.config_service import McpConfigurationService
from core.mcp.contracts import (
    McpCallResult,
    McpClientFailure,
    McpConfigurationSnapshot,
    McpDiscoveryResult,
    McpSecretUnavailable,
    McpServerConfig,
    McpTransportClientPort,
)
from core.mcp.diagnostics import McpDiagnosticService
from core.mcp.secrets import McpSecretService


SessionFactory = Callable[[], Session]
_MAX_REQUEST_TOOLS = 64


@dataclass(frozen=True, slots=True)
class McpCatalogCacheEntry:
    tools: tuple[RuntimeMcpToolDescriptor, ...]
    expires_at: float


class McpCatalogCache:
    """只缓存无凭据的 schema 快照；配置摘要变化即自然失效。"""

    def __init__(self, *, ttl_seconds: float = 60.0) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._items: dict[str, McpCatalogCacheEntry] = {}

    def get(self, config: McpServerConfig) -> tuple[RuntimeMcpToolDescriptor, ...] | None:
        entry = self._items.get(config.config_sha256)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._items.pop(config.config_sha256, None)
            return None
        return entry.tools

    def put(
        self,
        config: McpServerConfig,
        tools: tuple[RuntimeMcpToolDescriptor, ...],
    ) -> None:
        self._items[config.config_sha256] = McpCatalogCacheEntry(
            tools=tuple(tools),
            expires_at=time.monotonic() + self.ttl_seconds,
        )

    def clear(self) -> None:
        self._items.clear()


DEFAULT_MCP_CATALOG_CACHE = McpCatalogCache()


def _valid_catalog(
    config: McpServerConfig,
    server_id: str,
    tools: tuple[RuntimeMcpToolDescriptor, ...],
) -> bool:
    """不信任 transport adapter 返回的身份、预算或 namespace。"""

    wire_names = [item.wire_name for item in tools]
    if not (
        server_id == config.server_id
        and len(tools) <= config.max_tools
        and len(wire_names) == len(set(wire_names))
        and all(
            item.provider_id == "mcp" and item.server_id == config.server_id
            for item in tools
        )
    ):
        return False
    try:
        for item in tools:
            schema = json.loads(item.input_schema_json.decode("utf-8"))
            Draft202012Validator.check_schema(schema)
    except (SchemaError, UnicodeError, ValueError, TypeError):
        return False
    return True


def _validate_arguments(
    descriptor: RuntimeMcpToolDescriptor,
    arguments: Mapping[str, Any],
) -> None:
    try:
        schema = json.loads(descriptor.input_schema_json.decode("utf-8"))
        Draft202012Validator(schema).validate(dict(arguments))
    except (SchemaError, ValidationError, UnicodeError, ValueError, TypeError):
        raise McpClientFailure(
            "arguments_invalid",
            phase="call",
            error_type="McpArgumentsInvalid",
            retryable=False,
        ) from None


def _wire_schema(descriptor: RuntimeMcpToolDescriptor) -> dict[str, Any]:
    parameters = json.loads(descriptor.input_schema_json.decode("utf-8"))
    return {
        "type": "function",
        "function": {
            "name": descriptor.wire_name,
            "description": (
                f"外部 MCP server {descriptor.server_id} 提供。"
                f"{descriptor.description}"[:1600]
            ),
            "parameters": parameters,
        },
    }


def _redact(value: Any, secret_values: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        result = value
        for secret in secret_values:
            if secret:
                result = result.replace(secret, "[REDACTED]")
        return result
    if isinstance(value, Mapping):
        return {
            str(key): _redact(item, secret_values)
            for key, item in value.items()
            if str(key) != "_meta"
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, secret_values) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class McpRuntimeBuildResult:
    configuration: McpConfigurationSnapshot
    runtime: "McpRequestRuntime | None"
    snapshot: RuntimeMcpSnapshot | None
    tool_schemas: tuple[dict[str, Any], ...]
    configured_server_count: int
    healthy_server_count: int
    failed_server_count: int
    cached_server_count: int
    persistence_pending: bool
    diagnostics: tuple[Mapping[str, str], ...] = ()


@dataclass(slots=True)
class McpRequestRuntime:
    """仅持有配置引用和 schema；秘密值在每次调用时临时解析。"""

    snapshot: RuntimeMcpSnapshot
    configs: Mapping[str, McpServerConfig]
    client: McpTransportClientPort = field(repr=False)
    session_factory: SessionFactory = field(repr=False)
    descriptors: Mapping[str, RuntimeMcpToolDescriptor] = field(init=False, repr=False)
    execution_port: "McpToolExecutionPort" = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.configs = MappingProxyType(dict(self.configs))
        self.descriptors = MappingProxyType({
            item.wire_name: item for item in self.snapshot.tools
        })
        self.execution_port = McpToolExecutionPort(self)

    def descriptor(self, wire_name: str) -> RuntimeMcpToolDescriptor | None:
        return self.descriptors.get(str(wire_name or "").strip())

    def binding_id(self, wire_name: str) -> str:
        descriptor = self.descriptor(wire_name)
        return descriptor.execution_port_id if descriptor is not None else ""

    def effect_class(self, wire_name: str) -> RuntimeToolEffectClass | None:
        descriptor = self.descriptor(wire_name)
        if descriptor is None:
            return None
        # MCP annotation 由外部 server 自报，只能作为提示，不能下调授权等级。
        return RuntimeToolEffectClass.EXTERNAL

    def _record_call(
        self,
        config: McpServerConfig,
        *,
        result: McpCallResult | None = None,
        failure: McpClientFailure | None = None,
    ) -> None:
        db = self.session_factory()
        try:
            diagnostics = McpDiagnosticService(db)
            if failure is not None:
                diagnostics.record_failure(config, failure, operation="call")
            elif result is not None:
                diagnostics.record_success(
                    config,
                    operation="call",
                    latency_ms=result.latency_ms,
                    tool_count=1,
                )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    async def call(
        self,
        wire_name: str,
        arguments: Mapping[str, Any],
    ) -> McpCallResult:
        descriptor = self.descriptor(wire_name)
        if descriptor is None:
            raise AgentRuntimeCapabilityError(
                "MCP 工具不在当前请求快照中",
                runtime_id="mcp:request-runtime",
            )
        config = self.configs.get(descriptor.server_id)
        if config is None or not config.enabled:
            raise AgentRuntimeCapabilityError(
                "MCP server 已停用或不在当前配置快照中",
                runtime_id="mcp:request-runtime",
            )
        _validate_arguments(descriptor, arguments)
        db = self.session_factory()
        try:
            try:
                secrets = McpSecretService(db).resolve(config.secret_refs)
            except McpSecretUnavailable:
                failure = McpClientFailure(
                    "secret_unavailable",
                    phase="call",
                    error_type="McpSecretUnavailable",
                    retryable=False,
                )
                self._record_call(config, failure=failure)
                raise failure from None
        finally:
            db.close()
        try:
            result = await self.client.call(
                config,
                descriptor,
                dict(arguments),
                secrets,
            )
        except McpClientFailure as failure:
            self._record_call(config, failure=failure)
            raise
        scrubbed = _redact(
            dict(result.payload),
            tuple(value for value in secrets.values() if value),
        )
        safe_result = McpCallResult(
            payload={
                "_nanobot_mcp_result": {
                    "server_id": descriptor.server_id,
                    "tool_name": descriptor.tool_name,
                    "is_error": result.is_error,
                    "result": scrubbed,
                }
            },
            is_error=result.is_error,
            latency_ms=result.latency_ms,
        )
        self._record_call(config, result=safe_result)
        return safe_result


class McpToolExecutionPort:
    """Native Runtime 的请求级 MCP execution port。"""

    port_id = "mcp:request-tool-execution"

    def __init__(self, runtime: McpRequestRuntime) -> None:
        self.runtime = runtime

    async def execute(
        self,
        request: RuntimeToolExecutionRequest,
    ) -> RuntimeToolExecutionResult:
        if not isinstance(request, RuntimeToolExecutionRequest):
            raise TypeError("request 必须是 RuntimeToolExecutionRequest")
        descriptor = self.runtime.descriptor(request.tool_call.name)
        if (
            descriptor is None
            or request.execution_port_id != descriptor.execution_port_id
        ):
            raise AgentRuntimeCapabilityError(
                "MCP execution binding 与请求快照不匹配",
                runtime_id=self.port_id,
            )
        try:
            result = await self.runtime.call(
                request.tool_call.name,
                request.arguments,
            )
        except McpClientFailure as failure:
            if failure.ambiguous:
                raise
            descriptor = self.runtime.descriptor(request.tool_call.name)
            assert descriptor is not None
            return RuntimeToolExecutionResult(
                tool_call=RuntimeToolCall(
                    call_id=request.tool_call.call_id,
                    name=request.tool_call.name,
                    arguments=request.arguments,
                    status=RuntimeToolCallStatus.FAILED,
                ),
                error=RuntimeRunError(
                    code=f"mcp_{failure.code}",
                    message=f"MCP 工具调用失败：{request.tool_call.name}",
                    retryable=failure.retryable,
                ),
                exit_code=1,
                metadata={
                    "mcp": True,
                    "server_id": descriptor.server_id,
                    "input_schema_sha256": descriptor.input_schema_sha256,
                    "mcp_failure_code": failure.code,
                    "ambiguous": False,
                },
            )
        status = (
            RuntimeToolCallStatus.FAILED
            if result.is_error
            else RuntimeToolCallStatus.COMPLETED
        )
        return RuntimeToolExecutionResult(
            tool_call=RuntimeToolCall(
                call_id=request.tool_call.call_id,
                name=request.tool_call.name,
                arguments=request.arguments,
                status=status,
                result=copy.deepcopy(dict(result.payload)),
            ),
            error=(
                RuntimeRunError(
                    code="mcp_tool_error",
                    message=f"MCP 工具返回错误：{request.tool_call.name}",
                    retryable=False,
                )
                if result.is_error
                else None
            ),
            exit_code=1 if result.is_error else 0,
            metadata={
                "mcp": True,
                "server_id": descriptor.server_id,
                "input_schema_sha256": descriptor.input_schema_sha256,
            },
        )


class McpRuntimeService:
    """并行发现各 server；一个 server 失败只移除自身工具。"""

    def __init__(
        self,
        db: Session,
        *,
        client: McpTransportClientPort,
        session_factory: SessionFactory,
        cache: McpCatalogCache | None = None,
    ) -> None:
        if not isinstance(client, McpTransportClientPort):
            raise TypeError("client 未实现 McpTransportClientPort")
        self.db = db
        self.client = client
        self.session_factory = session_factory
        self.cache = cache or DEFAULT_MCP_CATALOG_CACHE

    async def build_request(
        self,
        *,
        existing_tool_names: frozenset[str] | set[str],
        bypass_cache: bool = False,
    ) -> McpRuntimeBuildResult:
        configuration = McpConfigurationService(self.db).snapshot()
        enabled = tuple(config for config in configuration.servers if config.enabled)
        diagnostics = McpDiagnosticService(self.db)
        pending = False
        failed_count = 0
        cached_count = 0
        outcomes: list[
            tuple[McpServerConfig, tuple[RuntimeMcpToolDescriptor, ...] | None]
        ] = []
        pending_discovery: list[tuple[McpServerConfig, Mapping[str, str]]] = []
        for config in enabled:
            cached = None if bypass_cache else self.cache.get(config)
            if cached is not None:
                if _valid_catalog(config, config.server_id, cached):
                    cached_count += 1
                    outcomes.append((config, cached))
                else:
                    failure = McpClientFailure(
                        "invalid_server_catalog",
                        phase="discover",
                        error_type="McpCatalogContractError",
                        retryable=False,
                    )
                    diagnostics.record_failure(config, failure, operation="discover")
                    pending = True
                    failed_count += 1
                    outcomes.append((config, None))
                continue
            try:
                secrets = McpSecretService(self.db).resolve(config.secret_refs)
            except McpSecretUnavailable:
                failure = McpClientFailure(
                    "secret_unavailable",
                    phase="discover",
                    error_type="McpSecretUnavailable",
                    retryable=False,
                )
                diagnostics.record_failure(config, failure, operation="discover")
                pending = True
                failed_count += 1
                outcomes.append((config, None))
                continue
            pending_discovery.append((config, secrets))

        async def discover_one(
            config: McpServerConfig,
            secrets: Mapping[str, str],
        ) -> tuple[McpServerConfig, McpDiscoveryResult | McpClientFailure]:
            try:
                return config, await self.client.discover(config, secrets)
            except McpClientFailure as failure:
                return config, failure
            except Exception as exc:
                return config, McpClientFailure(
                    "unexpected_client_failure",
                    phase="discover",
                    error_type=type(exc).__name__,
                    retryable=False,
                )

        discovered = await asyncio.gather(*(
            discover_one(config, secrets)
            for config, secrets in pending_discovery
        ))
        for config, outcome in discovered:
            if isinstance(outcome, McpClientFailure):
                diagnostics.record_failure(config, outcome, operation="discover")
                pending = True
                failed_count += 1
                outcomes.append((config, None))
                continue
            tools = tuple(outcome.tools)
            if not _valid_catalog(config, outcome.server_id, tools):
                failure = McpClientFailure(
                    "invalid_server_catalog",
                    phase="discover",
                    error_type="McpCatalogContractError",
                    retryable=False,
                )
                diagnostics.record_failure(config, failure, operation="discover")
                pending = True
                failed_count += 1
                outcomes.append((config, None))
                continue
            self.cache.put(config, tools)
            diagnostics.record_success(
                config,
                operation="discover",
                latency_ms=outcome.latency_ms,
                tool_count=len(tools),
            )
            pending = True
            outcomes.append((config, tools))

        accepted: list[RuntimeMcpToolDescriptor] = []
        accepted_configs: dict[str, McpServerConfig] = {}
        wire_owners: dict[str, str] = {}
        existing = {str(name) for name in existing_tool_names}
        for config, tools in sorted(outcomes, key=lambda item: item[0].server_id):
            if tools is None:
                continue
            local_names = {item.wire_name for item in tools}
            collision = local_names & existing
            collision.update(
                name for name in local_names if name in wire_owners
            )
            if collision or len(accepted) + len(tools) > _MAX_REQUEST_TOOLS:
                failure = McpClientFailure(
                    "tool_namespace_collision" if collision else "tool_budget_exceeded",
                    phase="discover",
                    error_type="McpRegistryConflict",
                    retryable=False,
                )
                diagnostics.record_failure(config, failure, operation="discover")
                pending = True
                failed_count += 1
                continue
            for tool in tools:
                wire_owners[tool.wire_name] = config.server_id
            accepted.extend(tools)
            accepted_configs[config.server_id] = config
        if not accepted:
            return McpRuntimeBuildResult(
                configuration=configuration,
                runtime=None,
                snapshot=None,
                tool_schemas=(),
                configured_server_count=len(enabled),
                healthy_server_count=0,
                failed_server_count=failed_count,
                cached_server_count=cached_count,
                persistence_pending=pending,
                diagnostics=configuration.diagnostics,
            )
        snapshot = RuntimeMcpSnapshot(
            provider_id="mcp",
            revision=(
                f"configuration:{configuration.revision}:"
                f"{configuration.sha256[:16]}"
            ),
            tools=tuple(accepted),
        )
        runtime = McpRequestRuntime(
            snapshot=snapshot,
            configs=accepted_configs,
            client=self.client,
            session_factory=self.session_factory,
        )
        return McpRuntimeBuildResult(
            configuration=configuration,
            runtime=runtime,
            snapshot=snapshot,
            tool_schemas=tuple(_wire_schema(item) for item in snapshot.tools),
            configured_server_count=len(enabled),
            healthy_server_count=len(accepted_configs),
            failed_server_count=failed_count,
            cached_server_count=cached_count,
            persistence_pending=pending,
            diagnostics=configuration.diagnostics,
        )

    async def health(self, server_id: str) -> dict[str, object]:
        configuration = McpConfigurationService(self.db).snapshot()
        config = configuration.get(server_id)
        if config is None:
            raise KeyError("MCP server 不存在")
        if not config.enabled:
            return {
                "server_id": config.server_id,
                "status": "disabled",
                "tool_count": 0,
                "config_sha256": config.config_sha256,
            }
        diagnostics = McpDiagnosticService(self.db)
        try:
            secrets = McpSecretService(self.db).resolve(config.secret_refs)
            result = await self.client.discover(config, secrets)
            tools = tuple(result.tools)
            if not _valid_catalog(config, result.server_id, tools):
                raise McpClientFailure(
                    "invalid_server_catalog",
                    phase="health",
                    error_type="McpCatalogContractError",
                    retryable=False,
                )
        except McpSecretUnavailable:
            failure = McpClientFailure(
                "secret_unavailable",
                phase="health",
                error_type="McpSecretUnavailable",
                retryable=False,
            )
        except McpClientFailure as exc:
            failure = exc
        else:
            self.cache.put(config, tools)
            diagnostics.record_success(
                config,
                operation="health",
                latency_ms=result.latency_ms,
                tool_count=len(tools),
            )
            return {
                "server_id": config.server_id,
                "status": "healthy",
                "tool_count": len(tools),
                "latency_ms": result.latency_ms,
                "config_sha256": config.config_sha256,
            }
        diagnostics.record_failure(config, failure, operation="health")
        return {
            "server_id": config.server_id,
            "status": "failed",
            "tool_count": 0,
            "latency_ms": failure.latency_ms,
            "error_code": failure.code,
            "error_type": failure.error_type,
            "retryable": failure.retryable,
            "config_sha256": config.config_sha256,
        }


_CURRENT_MCP_RUNTIME: ContextVar[McpRequestRuntime | None] = ContextVar(
    "nanobot_mcp_request_runtime",
    default=None,
)


def get_current_mcp_runtime() -> McpRequestRuntime | None:
    return _CURRENT_MCP_RUNTIME.get()


def set_current_mcp_runtime(
    runtime: McpRequestRuntime | None,
) -> Token[McpRequestRuntime | None]:
    return _CURRENT_MCP_RUNTIME.set(runtime)


def reset_current_mcp_runtime(token: Token[McpRequestRuntime | None]) -> None:
    _CURRENT_MCP_RUNTIME.reset(token)


@contextmanager
def mcp_request_runtime_scope(
    runtime: McpRequestRuntime | None,
) -> Iterator[McpRequestRuntime | None]:
    token = set_current_mcp_runtime(runtime)
    try:
        yield runtime
    finally:
        reset_current_mcp_runtime(token)


def current_mcp_binding_id(tool_name: str) -> str:
    runtime = get_current_mcp_runtime()
    return runtime.binding_id(tool_name) if runtime is not None else ""


def current_mcp_effect_class(
    tool_name: str,
) -> RuntimeToolEffectClass | None:
    runtime = get_current_mcp_runtime()
    return runtime.effect_class(tool_name) if runtime is not None else None


def current_mcp_execution_port(tool_name: str) -> ToolExecutionPort | None:
    runtime = get_current_mcp_runtime()
    if runtime is None or runtime.descriptor(tool_name) is None:
        return None
    return runtime.execution_port


__all__ = [
    "DEFAULT_MCP_CATALOG_CACHE",
    "McpCatalogCache",
    "McpRequestRuntime",
    "McpRuntimeBuildResult",
    "McpRuntimeService",
    "McpToolExecutionPort",
    "current_mcp_binding_id",
    "current_mcp_effect_class",
    "current_mcp_execution_port",
    "get_current_mcp_runtime",
    "mcp_request_runtime_scope",
    "reset_current_mcp_runtime",
    "set_current_mcp_runtime",
]
