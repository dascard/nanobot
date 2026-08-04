"""MCP 控制面的严格配置、传输结果与错误合同。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from core.agent_runtime import RuntimeMcpToolDescriptor
from core.registry import RegistryBuilder, RegistrySnapshot


_SERVER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SECRET_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,126}$")
_OAUTH_SCOPE_PATTERN = re.compile(r"^[\x21-\x7e]{1,128}$")
_RESERVED_SECRET_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "host",
    "mcp-protocol-version",
    "mcp-session-id",
    "proxy-authorization",
    "transfer-encoding",
}


class McpControlPlaneError(ValueError):
    """MCP 配置、版本或秘密引用不满足控制面合同。"""


class McpConfigurationConflict(McpControlPlaneError):
    """原子配置替换的 expected revision 已过期。"""


class McpSecretUnavailable(McpControlPlaneError):
    """请求级秘密不存在、损坏或主密钥未配置。"""


class McpTransportKind(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


class McpAuthMode(str, Enum):
    NONE = "none"
    BEARER = "bearer"
    OAUTH_CLIENT_CREDENTIALS = "oauth_client_credentials"


def _required(value: object, name: str, *, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise McpControlPlaneError(f"{name} 不能为空")
    if len(normalized) > maximum or "\x00" in normalized:
        raise McpControlPlaneError(f"{name} 超过长度或包含非法字符")
    return normalized


def _identifier(value: object, name: str, *, maximum: int = 64) -> str:
    normalized = _required(value, name, maximum=maximum)
    if not _SERVER_ID_PATTERN.fullmatch(normalized):
        raise McpControlPlaneError(f"{name} 不是合法标识符")
    return normalized


def _bounded_number(
    value: object,
    name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool):
        raise McpControlPlaneError(f"{name} 必须是数值")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise McpControlPlaneError(f"{name} 必须是数值") from exc
    if not minimum <= normalized <= maximum:
        raise McpControlPlaneError(
            f"{name} 必须在 {minimum} 到 {maximum} 之间"
        )
    return normalized


def _http_url(value: object, name: str, *, required: bool) -> str:
    raw = str(value or "").strip()
    if not raw and not required:
        return ""
    raw = _required(raw, name, maximum=2048)
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise McpControlPlaneError(
            f"{name} 必须是无内嵌凭据和 fragment 的 HTTP(S) URL"
        )
    return raw


@dataclass(frozen=True, slots=True)
class McpSecretReference:
    """只描述请求期注入目标和秘密 ID，不包含秘密值。"""

    binding: str
    secret_id: str

    def __post_init__(self) -> None:
        binding = _required(self.binding, "mcp.secret_ref.binding", maximum=160)
        secret_id = _required(
            self.secret_id,
            "mcp.secret_ref.secret_id",
            maximum=128,
        )
        if not _SECRET_ID_PATTERN.fullmatch(secret_id):
            raise McpControlPlaneError("mcp.secret_ref.secret_id 不是合法标识符")
        valid = binding in {
            "auth.bearer",
            "oauth.client_id",
            "oauth.client_secret",
        }
        if binding.startswith("env."):
            valid = bool(_ENV_NAME_PATTERN.fullmatch(binding[4:]))
        elif binding.startswith("header."):
            header = binding[7:]
            valid = bool(_HEADER_NAME_PATTERN.fullmatch(header)) and (
                header.lower() not in _RESERVED_SECRET_HEADERS
            )
        if not valid:
            raise McpControlPlaneError("mcp.secret_ref.binding 无效或属于保留目标")
        object.__setattr__(self, "binding", binding)
        object.__setattr__(self, "secret_id", secret_id)

    def to_dict(self) -> dict[str, str]:
        return {"binding": self.binding, "secret_id": self.secret_id}


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """一个可原子发布且不携带秘密值的 MCP server 配置。"""

    server_id: str
    display_name: str
    transport: McpTransportKind
    enabled: bool = False
    endpoint: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    cwd: str = ""
    connect_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 60.0
    sse_read_timeout_seconds: float = 300.0
    reconnect_attempts: int = 1
    max_tools: int = 20
    auth_mode: McpAuthMode = McpAuthMode.NONE
    oauth_token_url: str = ""
    oauth_scopes: tuple[str, ...] = ()
    secret_refs: tuple[McpSecretReference, ...] = ()

    def __post_init__(self) -> None:
        server_id = _identifier(self.server_id, "mcp.server_id")
        display_name = _required(
            self.display_name or server_id,
            "mcp.display_name",
            maximum=128,
        )
        try:
            transport = McpTransportKind(self.transport)
        except ValueError as exc:
            raise McpControlPlaneError("mcp.transport 无效") from exc
        try:
            auth_mode = McpAuthMode(self.auth_mode)
        except ValueError as exc:
            raise McpControlPlaneError("mcp.auth_mode 无效") from exc
        if not isinstance(self.enabled, bool):
            raise McpControlPlaneError("mcp.enabled 必须是 bool")
        args = tuple(
            _required(item, "mcp.args[]", maximum=4096) for item in self.args
        )
        if len(args) > 128:
            raise McpControlPlaneError("mcp.args 最多 128 项")
        cwd = str(self.cwd or "").strip()
        if len(cwd) > 2048 or "\x00" in cwd:
            raise McpControlPlaneError("mcp.cwd 无效")
        reconnect_attempts = self.reconnect_attempts
        max_tools = self.max_tools
        if (
            isinstance(reconnect_attempts, bool)
            or not isinstance(reconnect_attempts, int)
            or not 0 <= reconnect_attempts <= 5
        ):
            raise McpControlPlaneError("mcp.reconnect_attempts 必须是 0 到 5 的整数")
        if (
            isinstance(max_tools, bool)
            or not isinstance(max_tools, int)
            or not 1 <= max_tools <= 100
        ):
            raise McpControlPlaneError("mcp.max_tools 必须是 1 到 100 的整数")
        scopes = tuple(sorted({
            _required(item, "mcp.oauth_scope", maximum=128)
            for item in self.oauth_scopes
        }))
        if len(scopes) > 32 or any(
            not _OAUTH_SCOPE_PATTERN.fullmatch(item) for item in scopes
        ):
            raise McpControlPlaneError("mcp.oauth_scopes 无效")
        raw_refs = tuple(self.secret_refs)
        if any(not isinstance(item, McpSecretReference) for item in raw_refs):
            raise McpControlPlaneError("mcp.secret_refs 包含无效引用")
        refs = tuple(sorted(raw_refs, key=lambda item: item.binding))
        bindings = [item.binding for item in refs]
        if len(refs) > 32 or len(bindings) != len(set(bindings)):
            raise McpControlPlaneError("mcp.secret_refs 超限或 binding 重复")
        endpoint = _http_url(
            self.endpoint,
            "mcp.endpoint",
            required=transport is not McpTransportKind.STDIO,
        )
        oauth_token_url = _http_url(
            self.oauth_token_url,
            "mcp.oauth_token_url",
            required=auth_mode is McpAuthMode.OAUTH_CLIENT_CREDENTIALS,
        )
        command = str(self.command or "").strip()
        if len(command) > 2048 or "\x00" in command:
            raise McpControlPlaneError("mcp.command 无效")
        if transport is McpTransportKind.STDIO:
            if not command or endpoint or auth_mode is not McpAuthMode.NONE:
                raise McpControlPlaneError(
                    "stdio 必须提供 command，且不能配置 endpoint 或 HTTP auth"
                )
            if any(not ref.binding.startswith("env.") for ref in refs):
                raise McpControlPlaneError("stdio 只接受 env.* 秘密引用")
            if oauth_token_url or scopes:
                raise McpControlPlaneError("stdio 不能配置 OAuth")
        else:
            if command or args or cwd:
                raise McpControlPlaneError("HTTP/SSE 不能配置 command、args 或 cwd")
            if any(ref.binding.startswith("env.") for ref in refs):
                raise McpControlPlaneError("HTTP/SSE 不能使用 env.* 秘密引用")
            required_bindings: set[str] = set()
            if auth_mode is McpAuthMode.BEARER:
                required_bindings = {"auth.bearer"}
            elif auth_mode is McpAuthMode.OAUTH_CLIENT_CREDENTIALS:
                required_bindings = {"oauth.client_id", "oauth.client_secret"}
            missing = required_bindings - set(bindings)
            if missing:
                raise McpControlPlaneError(
                    "MCP auth 缺少秘密引用：" + ", ".join(sorted(missing))
                )
            if auth_mode is McpAuthMode.NONE and oauth_token_url:
                raise McpControlPlaneError("未启用 OAuth 时不能配置 token URL")
        object.__setattr__(self, "server_id", server_id)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "transport", transport)
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "args", args)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(
            self,
            "connect_timeout_seconds",
            _bounded_number(
                self.connect_timeout_seconds,
                "mcp.connect_timeout_seconds",
                minimum=0.1,
                maximum=120.0,
            ),
        )
        object.__setattr__(
            self,
            "request_timeout_seconds",
            _bounded_number(
                self.request_timeout_seconds,
                "mcp.request_timeout_seconds",
                minimum=0.1,
                maximum=1800.0,
            ),
        )
        object.__setattr__(
            self,
            "sse_read_timeout_seconds",
            _bounded_number(
                self.sse_read_timeout_seconds,
                "mcp.sse_read_timeout_seconds",
                minimum=1.0,
                maximum=3600.0,
            ),
        )
        object.__setattr__(self, "auth_mode", auth_mode)
        object.__setattr__(self, "oauth_token_url", oauth_token_url)
        object.__setattr__(self, "oauth_scopes", scopes)
        object.__setattr__(self, "secret_refs", refs)

    @property
    def registry_namespace(self) -> str:
        return "mcp_server"

    @property
    def registry_id(self) -> str:
        return self.server_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return self.to_dict()

    @property
    def config_sha256(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "server_id": self.server_id,
            "display_name": self.display_name,
            "transport": self.transport.value,
            "enabled": self.enabled,
            "endpoint": self.endpoint,
            "command": self.command,
            "args": list(self.args),
            "cwd": self.cwd,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "sse_read_timeout_seconds": self.sse_read_timeout_seconds,
            "reconnect_attempts": self.reconnect_attempts,
            "max_tools": self.max_tools,
            "auth_mode": self.auth_mode.value,
            "oauth_token_url": self.oauth_token_url,
            "oauth_scopes": list(self.oauth_scopes),
            "secret_refs": [item.to_dict() for item in self.secret_refs],
        }


@dataclass(frozen=True, slots=True)
class McpConfigurationSnapshot:
    revision: int
    servers: tuple[McpServerConfig, ...]
    registry: RegistrySnapshot[McpServerConfig]
    diagnostics: tuple[Mapping[str, str], ...] = ()

    @classmethod
    def build(
        cls,
        revision: int,
        servers: tuple[McpServerConfig, ...],
        *,
        diagnostics: tuple[Mapping[str, str], ...] = (),
    ) -> "McpConfigurationSnapshot":
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise McpControlPlaneError("MCP 配置 revision 必须是非负整数")
        builder = RegistryBuilder[McpServerConfig]("mcp_server")
        for server in sorted(servers, key=lambda item: item.server_id):
            builder.register(server)
        registry = builder.freeze(generation=max(1, revision))
        return cls(
            revision=revision,
            servers=tuple(registry),
            registry=registry,
            diagnostics=tuple(MappingProxyType(dict(item)) for item in diagnostics),
        )

    @property
    def sha256(self) -> str:
        return self.registry.sha256

    def get(self, server_id: str) -> McpServerConfig | None:
        return self.registry.get(str(server_id or "").strip())


@dataclass(frozen=True, slots=True)
class McpDiscoveryResult:
    server_id: str
    tools: tuple[RuntimeMcpToolDescriptor, ...]
    latency_ms: int


@dataclass(frozen=True, slots=True)
class McpCallResult:
    payload: Mapping[str, Any]
    is_error: bool
    latency_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


class McpClientFailure(RuntimeError):
    """只暴露稳定分类，不携带 SDK exception message。"""

    def __init__(
        self,
        code: str,
        *,
        phase: str,
        error_type: str,
        retryable: bool,
        ambiguous: bool = False,
        latency_ms: int = 0,
    ) -> None:
        self.code = _identifier(code, "mcp.failure.code")
        self.phase = _identifier(phase, "mcp.failure.phase")
        self.error_type = re.sub(r"[^A-Za-z0-9_.-]", "_", error_type)[:128]
        self.retryable = bool(retryable)
        self.ambiguous = bool(ambiguous)
        self.latency_ms = max(0, int(latency_ms))
        super().__init__(f"MCP {self.phase} 失败（{self.code}）")


@runtime_checkable
class McpTransportClientPort(Protocol):
    async def discover(
        self,
        config: McpServerConfig,
        secret_values: Mapping[str, str],
    ) -> McpDiscoveryResult: ...

    async def call(
        self,
        config: McpServerConfig,
        descriptor: RuntimeMcpToolDescriptor,
        arguments: Mapping[str, Any],
        secret_values: Mapping[str, str],
    ) -> McpCallResult: ...


__all__ = [
    "McpAuthMode",
    "McpCallResult",
    "McpClientFailure",
    "McpConfigurationConflict",
    "McpConfigurationSnapshot",
    "McpControlPlaneError",
    "McpDiscoveryResult",
    "McpSecretReference",
    "McpSecretUnavailable",
    "McpServerConfig",
    "McpTransportClientPort",
    "McpTransportKind",
]
