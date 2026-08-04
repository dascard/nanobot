"""A2A 1.0 JSON-RPC 出站 Client Adapter。

该 Adapter 只执行显式、单次、无自动重试的 Client 调用。远端 Task/Artifact 是
不可变交换投影，不会被冒充为本地 Nanobot Run/Artifact；调用方的
``RuntimeRunIdentity`` 只用于绑定来源，也绝不进入远端 metadata。
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import inspect
import ipaddress
import json
import math
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit
import uuid

import httpx

from core.agent_runtime.contracts import RuntimeRunIdentity
from core.interoperability.contracts import (
    InteroperabilityError,
    require_interoperability_enabled,
)
from core.lifecycle import FeatureEnablementDecision


A2A_FEATURE_ID = "interoperability.a2a_v1_client"
A2A_PROTOCOL_VERSION = "1.0"
A2A_PROTOCOL_BINDING = "JSONRPC"
_JSONRPC_VERSION = "2.0"
_MAX_IDENTIFIER_CHARS = 256


class A2AProtocolError(InteroperabilityError):
    """远端协议内容不符合 A2A 1.0 最小合同。"""


class A2ATransportError(InteroperabilityError):
    """安全归一化的出站错误；不包含 URL、凭据或响应正文。"""

    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        ambiguous: bool,
    ) -> None:
        self.ambiguous = bool(ambiguous)
        super().__init__(code, safe_message)


def _required_identifier(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是字符串")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > _MAX_IDENTIFIER_CHARS
        or any(character in normalized for character in "\r\n\x00")
    ):
        raise ValueError(f"{name} 非法")
    return normalized


def _optional_identifier(value: object, name: str) -> str:
    if value is None or value == "":
        return ""
    return _required_identifier(value, name)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise A2AProtocolError("INVALID_RESPONSE", f"A2A {name} 必须是对象")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise A2AProtocolError("INVALID_RESPONSE", f"A2A {name} 必须是数组")
    return value


def _origin(value: str, *, allow_path: bool) -> tuple[str, str]:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("A2A endpoint 必须是无凭据、查询和片段的 HTTPS URL")
    if not allow_path and parsed.path not in {"", "/"}:
        raise ValueError("A2A allowlist origin 不得包含路径")
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError("A2A endpoint 不得使用非公网 IP 字面量")
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValueError("A2A endpoint 不得使用本地域名")
    port = parsed.port
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    origin = f"https://{rendered_host}"
    if port not in {None, 443}:
        origin = f"{origin}:{port}"
    path = parsed.path or "/"
    return origin, path


@dataclass(frozen=True, slots=True)
class A2AInterface:
    """由受信配置固定的 AgentInterface；不做动态 Agent Card 发现。"""

    url: str
    allowed_origins: tuple[str, ...]
    protocol_binding: str = A2A_PROTOCOL_BINDING
    protocol_version: str = A2A_PROTOCOL_VERSION
    tenant: str = ""

    def __post_init__(self) -> None:
        if self.protocol_binding != A2A_PROTOCOL_BINDING:
            raise ValueError("当前仅支持 A2A JSONRPC binding")
        if self.protocol_version != A2A_PROTOCOL_VERSION:
            raise ValueError("当前仅支持 A2A protocol version 1.0")
        endpoint_origin, _ = _origin(str(self.url or "").strip(), allow_path=True)
        origins: list[str] = []
        for value in self.allowed_origins:
            origin, _ = _origin(str(value or "").strip(), allow_path=False)
            origins.append(origin)
        normalized_origins = tuple(dict.fromkeys(origins))
        if not normalized_origins or endpoint_origin not in normalized_origins:
            raise ValueError("A2A endpoint origin 不在显式 allowlist")
        object.__setattr__(self, "url", str(self.url).strip())
        object.__setattr__(self, "allowed_origins", normalized_origins)
        object.__setattr__(
            self,
            "tenant",
            _optional_identifier(self.tenant, "A2A tenant"),
        )


@dataclass(frozen=True, slots=True)
class A2ATransportLimits:
    timeout_seconds: float = 60.0
    max_request_bytes: int = 1024 * 1024
    max_response_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(float(self.timeout_seconds))
            or float(self.timeout_seconds) <= 0
        ):
            raise ValueError("A2A timeout_seconds 必须是有限正数")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        for name in ("max_request_bytes", "max_response_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"A2A {name} 必须是正整数")


AuthorizationProvider = Callable[[], str | Awaitable[str]]


@runtime_checkable
class A2AJsonRpcTransport(Protocol):
    @property
    def interface(self) -> A2AInterface: ...

    async def send(
        self,
        envelope: Mapping[str, object],
    ) -> Mapping[str, object]: ...


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 包含重复 key")
        result[key] = value
    return result


def _validate_json_shape(
    value: object,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> None:
    remaining = budget if budget is not None else [20_000]
    remaining[0] -= 1
    if remaining[0] < 0 or depth > 24:
        raise ValueError("JSON 结构超过复杂度上限")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON 对象 key 必须是字符串")
            _validate_json_shape(item, depth=depth + 1, budget=remaining)
    elif isinstance(value, list):
        for item in value:
            _validate_json_shape(item, depth=depth + 1, budget=remaining)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("JSON 包含非法类型")


class HttpsA2AJsonRpcTransport:
    """禁用环境代理、重定向和重试的实际 HTTPS transport。"""

    def __init__(
        self,
        *,
        interface: A2AInterface,
        authorization_provider: AuthorizationProvider | None = None,
        limits: A2ATransportLimits = A2ATransportLimits(),
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not isinstance(interface, A2AInterface):
            raise TypeError("interface 必须是 A2AInterface")
        if authorization_provider is not None and not callable(authorization_provider):
            raise TypeError("authorization_provider 必须可调用")
        if not isinstance(limits, A2ATransportLimits):
            raise TypeError("limits 必须是 A2ATransportLimits")
        self._interface = interface
        self._authorization_provider = authorization_provider
        self._limits = limits
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = client is None

    @property
    def interface(self) -> A2AInterface:
        return self._interface

    async def send(
        self,
        envelope: Mapping[str, object],
    ) -> Mapping[str, object]:
        try:
            body = json.dumps(
                dict(envelope),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise A2ATransportError(
                "INVALID_REQUEST",
                "A2A 请求不是有效 JSON",
                ambiguous=False,
            ) from exc
        if len(body) > self._limits.max_request_bytes:
            raise A2ATransportError(
                "REQUEST_TOO_LARGE",
                "A2A 请求超过大小上限",
                ambiguous=False,
            )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, application/a2a+json",
            "A2A-Version": self._interface.protocol_version,
            "User-Agent": "Nanobot-A2A-Adapter/1.0",
        }
        if self._authorization_provider is not None:
            try:
                authorization = self._authorization_provider()
                if inspect.isawaitable(authorization):
                    authorization = await authorization
            except Exception as exc:
                raise A2ATransportError(
                    "CREDENTIAL_UNAVAILABLE",
                    "A2A 认证材料不可用",
                    ambiguous=False,
                ) from exc
            if (
                not isinstance(authorization, str)
                or not authorization.strip()
                or len(authorization) > 8192
                or any(character in authorization for character in "\r\n\x00")
            ):
                raise A2ATransportError(
                    "CREDENTIAL_INVALID",
                    "A2A 认证材料无效",
                    ambiguous=False,
                )
            headers["Authorization"] = authorization.strip()

        try:
            async with self._client.stream(
                "POST",
                self._interface.url,
                headers=headers,
                content=body,
                timeout=self._limits.timeout_seconds,
                follow_redirects=False,
            ) as response:
                if 300 <= response.status_code < 400:
                    raise A2ATransportError(
                        "REDIRECT_FORBIDDEN",
                        "A2A endpoint 返回了被禁止的重定向",
                        ambiguous=False,
                    )
                if response.status_code != 200:
                    raise A2ATransportError(
                        "HTTP_FAILURE",
                        "A2A endpoint 返回非成功状态",
                        ambiguous=response.status_code >= 500,
                    )
                content_type = response.headers.get("content-type", "")
                media_type = content_type.split(";", 1)[0].strip().lower()
                if media_type not in {
                    "application/json",
                    "application/a2a+json",
                }:
                    raise A2ATransportError(
                        "CONTENT_TYPE_INVALID",
                        "A2A endpoint 返回不支持的媒体类型",
                        ambiguous=False,
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._limits.max_response_bytes:
                        raise A2ATransportError(
                            "RESPONSE_TOO_LARGE",
                            "A2A 响应超过大小上限",
                            ambiguous=True,
                        )
                    chunks.append(chunk)
        except A2ATransportError:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise A2ATransportError(
                "CONNECT_FAILED",
                "A2A endpoint 连接失败",
                ambiguous=False,
            ) from exc
        except httpx.HTTPError as exc:
            raise A2ATransportError(
                "TRANSPORT_FAILED",
                "A2A 传输在请求发出后失败",
                ambiguous=True,
            ) from exc

        try:
            payload = json.loads(
                b"".join(chunks).decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ValueError("JSON 常量无效")
                ),
            )
            _validate_json_shape(payload)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise A2ATransportError(
                "RESPONSE_JSON_INVALID",
                "A2A endpoint 返回无效 JSON",
                ambiguous=False,
            ) from exc
        if not isinstance(payload, Mapping):
            raise A2ATransportError(
                "RESPONSE_JSON_INVALID",
                "A2A endpoint 响应必须是对象",
                ambiguous=False,
            )
        return payload

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


@dataclass(frozen=True, slots=True)
class A2AClientRequest:
    identity: RuntimeRunIdentity
    message_id: str
    text: str
    context_id: str = ""
    task_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("A2A identity 必须是 RuntimeRunIdentity")
        object.__setattr__(
            self,
            "message_id",
            _required_identifier(self.message_id, "A2A message_id"),
        )
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("A2A text 不能为空")
        if len(self.text.encode("utf-8")) > 1024 * 1024:
            raise ValueError("A2A text 超过大小上限")
        object.__setattr__(
            self,
            "context_id",
            _optional_identifier(self.context_id, "A2A context_id"),
        )
        object.__setattr__(
            self,
            "task_id",
            _optional_identifier(self.task_id, "A2A task_id"),
        )
        if self.context_id or self.task_id:
            raise ValueError(
                "实验 A2A Client 仅创建新任务，不接受未绑定的远端 context/task"
            )


class A2ATaskState(str, Enum):
    UNSPECIFIED = "TASK_STATE_UNSPECIFIED"
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    COMPLETED = "TASK_STATE_COMPLETED"
    FAILED = "TASK_STATE_FAILED"
    CANCELED = "TASK_STATE_CANCELED"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    REJECTED = "TASK_STATE_REJECTED"
    AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            A2ATaskState.COMPLETED,
            A2ATaskState.FAILED,
            A2ATaskState.CANCELED,
            A2ATaskState.REJECTED,
        }

    @property
    def is_interrupted(self) -> bool:
        return self in {
            A2ATaskState.INPUT_REQUIRED,
            A2ATaskState.AUTH_REQUIRED,
        }


class A2APartKind(str, Enum):
    TEXT = "text"
    RAW = "raw"
    URL = "url"
    DATA = "data"


@dataclass(frozen=True, slots=True)
class A2ARemotePart:
    kind: A2APartKind
    media_type: str
    filename: str
    value: object
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class A2ARemoteArtifact:
    artifact_id: str
    name: str
    description: str
    parts: tuple[A2ARemotePart, ...]


@dataclass(frozen=True, slots=True)
class A2ARemoteMessage:
    message_id: str
    context_id: str
    task_id: str
    role: str
    parts: tuple[A2ARemotePart, ...]


@dataclass(frozen=True, slots=True)
class A2ARemoteTask:
    task_id: str
    context_id: str
    state: A2ATaskState
    artifacts: tuple[A2ARemoteArtifact, ...]
    status_message: A2ARemoteMessage | None = None


@dataclass(frozen=True, slots=True)
class A2AExchange:
    """绑定本地来源的瞬时交换结果，不是新的 Run 或 Artifact 事实。"""

    source_identity: RuntimeRunIdentity
    task: A2ARemoteTask | None = None
    message: A2ARemoteMessage | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_identity, RuntimeRunIdentity):
            raise ValueError("A2A source_identity 无效")
        if (self.task is None) == (self.message is None):
            raise ValueError("A2A exchange 必须且只能包含 task 或 message")


@dataclass(frozen=True, slots=True)
class A2AParseLimits:
    max_artifacts: int = 32
    max_parts_per_item: int = 64
    max_part_bytes: int = 2 * 1024 * 1024
    max_total_artifact_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in (
            "max_artifacts",
            "max_parts_per_item",
            "max_part_bytes",
            "max_total_artifact_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"A2A {name} 必须是正整数")


def _safe_string(value: object, name: str, *, maximum: int) -> str:
    if value is None:
        return ""
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or any(character in value for character in "\x00")
    ):
        raise A2AProtocolError("INVALID_RESPONSE", f"A2A {name} 非法")
    return value


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise A2AProtocolError("INVALID_RESPONSE", "A2A data part 不是 JSON 值")


def _parse_part(raw: object, limits: A2AParseLimits) -> A2ARemotePart:
    part = _mapping(raw, "part")
    present = [key for key in ("text", "raw", "url", "data") if key in part]
    if len(present) != 1:
        raise A2AProtocolError(
            "INVALID_RESPONSE",
            "A2A part 必须且只能包含一种 content",
        )
    key = present[0]
    filename = _safe_string(part.get("filename"), "filename", maximum=512)
    media_type = _safe_string(
        part.get("mediaType"),
        "mediaType",
        maximum=256,
    ).lower()
    if key == "text":
        value = _safe_string(
            part.get("text"), "text part", maximum=limits.max_part_bytes
        )
        encoded = value.encode("utf-8")
        kind = A2APartKind.TEXT
    elif key == "raw":
        raw_value = part.get("raw")
        if not isinstance(raw_value, str):
            raise A2AProtocolError("INVALID_RESPONSE", "A2A raw part 非法")
        try:
            value = base64.b64decode(raw_value, validate=True)
        except (ValueError, TypeError) as exc:
            raise A2AProtocolError(
                "INVALID_RESPONSE",
                "A2A raw part 不是有效 base64",
            ) from exc
        encoded = value
        kind = A2APartKind.RAW
    elif key == "url":
        value = _safe_string(part.get("url"), "url part", maximum=2048)
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise A2AProtocolError(
                "UNSAFE_ARTIFACT_URL",
                "A2A artifact URL 必须是无凭据、查询和片段的 HTTPS URL",
            )
        encoded = value.encode("utf-8")
        kind = A2APartKind.URL
    else:
        value = _freeze_json(part.get("data"))
        try:
            encoded = json.dumps(
                part.get("data"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise A2AProtocolError(
                "INVALID_RESPONSE",
                "A2A data part 不是有效 JSON",
            ) from exc
        kind = A2APartKind.DATA
    if len(encoded) > limits.max_part_bytes:
        raise A2AProtocolError("PART_TOO_LARGE", "A2A part 超过大小上限")
    return A2ARemotePart(
        kind=kind,
        media_type=media_type,
        filename=filename,
        value=value,
        size_bytes=len(encoded),
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _parse_parts(raw: object, limits: A2AParseLimits) -> tuple[A2ARemotePart, ...]:
    items = _sequence(raw, "parts")
    if not items or len(items) > limits.max_parts_per_item:
        raise A2AProtocolError("INVALID_RESPONSE", "A2A parts 数量无效")
    return tuple(_parse_part(item, limits) for item in items)


def _parse_message(raw: object, limits: A2AParseLimits) -> A2ARemoteMessage:
    message = _mapping(raw, "message")
    role = message.get("role")
    if role != "ROLE_AGENT":
        raise A2AProtocolError("INVALID_RESPONSE", "A2A 响应 message role 非 AGENT")
    try:
        message_id = _required_identifier(message.get("messageId"), "messageId")
        context_id = _optional_identifier(message.get("contextId"), "contextId")
        task_id = _optional_identifier(message.get("taskId"), "taskId")
    except ValueError as exc:
        raise A2AProtocolError("INVALID_RESPONSE", "A2A message 标识无效") from exc
    return A2ARemoteMessage(
        message_id=message_id,
        context_id=context_id,
        task_id=task_id,
        role=role,
        parts=_parse_parts(message.get("parts"), limits),
    )


def _parse_artifact(raw: object, limits: A2AParseLimits) -> A2ARemoteArtifact:
    artifact = _mapping(raw, "artifact")
    try:
        artifact_id = _required_identifier(
            artifact.get("artifactId"),
            "artifactId",
        )
    except ValueError as exc:
        raise A2AProtocolError("INVALID_RESPONSE", "A2A artifactId 无效") from exc
    return A2ARemoteArtifact(
        artifact_id=artifact_id,
        name=_safe_string(artifact.get("name"), "artifact.name", maximum=512),
        description=_safe_string(
            artifact.get("description"),
            "artifact.description",
            maximum=4096,
        ),
        parts=_parse_parts(artifact.get("parts"), limits),
    )


def _parse_task(raw: object, limits: A2AParseLimits) -> A2ARemoteTask:
    task = _mapping(raw, "task")
    try:
        task_id = _required_identifier(task.get("id"), "task.id")
        context_id = _optional_identifier(task.get("contextId"), "task.contextId")
    except ValueError as exc:
        raise A2AProtocolError("INVALID_RESPONSE", "A2A task 标识无效") from exc
    status = _mapping(task.get("status"), "task.status")
    try:
        state = A2ATaskState(status.get("state"))
    except (TypeError, ValueError) as exc:
        raise A2AProtocolError("INVALID_RESPONSE", "A2A TaskState 无效") from exc
    if state is A2ATaskState.UNSPECIFIED:
        raise A2AProtocolError("INVALID_RESPONSE", "A2A TaskState 未明确")
    if not state.is_terminal and not state.is_interrupted:
        raise A2AProtocolError(
            "NONTERMINAL_TASK",
            "A2A 阻塞 SendMessage 返回了非终态 Task",
        )
    raw_artifacts = task.get("artifacts", [])
    artifacts_seq = _sequence(raw_artifacts, "task.artifacts")
    if len(artifacts_seq) > limits.max_artifacts:
        raise A2AProtocolError("ARTIFACT_LIMIT", "A2A artifact 数量超过上限")
    artifacts = tuple(_parse_artifact(item, limits) for item in artifacts_seq)
    total_bytes = sum(
        part.size_bytes for artifact in artifacts for part in artifact.parts
    )
    if total_bytes > limits.max_total_artifact_bytes:
        raise A2AProtocolError(
            "ARTIFACT_TOO_LARGE",
            "A2A artifact 总大小超过上限",
        )
    status_message = status.get("message")
    return A2ARemoteTask(
        task_id=task_id,
        context_id=context_id,
        state=state,
        artifacts=artifacts,
        status_message=(
            _parse_message(status_message, limits)
            if status_message is not None
            else None
        ),
    )


class A2AClientAdapter:
    """受管 A2A 1.0 Client；不实现 Server、push、SSE 或自动发现。"""

    def __init__(
        self,
        *,
        enablement: FeatureEnablementDecision,
        interface: A2AInterface,
        transport: A2AJsonRpcTransport,
        parse_limits: A2AParseLimits = A2AParseLimits(),
    ) -> None:
        require_interoperability_enabled(
            enablement,
            feature_id=A2A_FEATURE_ID,
        )
        if not isinstance(interface, A2AInterface):
            raise TypeError("interface 必须是 A2AInterface")
        if not isinstance(transport, A2AJsonRpcTransport):
            raise TypeError("transport 必须是 A2AJsonRpcTransport")
        if transport.interface != interface:
            raise ValueError("A2A transport 与冻结 AgentInterface 不一致")
        if not isinstance(parse_limits, A2AParseLimits):
            raise TypeError("parse_limits 必须是 A2AParseLimits")
        self._interface = interface
        self._transport = transport
        self._parse_limits = parse_limits

    async def send_message(self, request: A2AClientRequest) -> A2AExchange:
        if not isinstance(request, A2AClientRequest):
            raise TypeError("request 必须是 A2AClientRequest")
        rpc_id = f"a2a-{uuid.uuid4().hex}"
        message: dict[str, object] = {
            "messageId": request.message_id,
            "role": "ROLE_USER",
            "parts": [{"text": request.text, "mediaType": "text/plain"}],
        }
        if request.context_id:
            message["contextId"] = request.context_id
        if request.task_id:
            message["taskId"] = request.task_id
        params: dict[str, object] = {
            "message": message,
            "configuration": {
                "acceptedOutputModes": [
                    "text/plain",
                    "application/json",
                ],
                "historyLength": 0,
                "returnImmediately": False,
            },
        }
        if self._interface.tenant:
            params["tenant"] = self._interface.tenant
        response = await self._transport.send(
            {
                "jsonrpc": _JSONRPC_VERSION,
                "id": rpc_id,
                "method": "SendMessage",
                "params": params,
            }
        )
        return self._parse_exchange(
            response,
            rpc_id=rpc_id,
            identity=request.identity,
        )

    def _parse_exchange(
        self,
        response: Mapping[str, object],
        *,
        rpc_id: str,
        identity: RuntimeRunIdentity,
    ) -> A2AExchange:
        envelope = _mapping(response, "JSON-RPC response")
        if envelope.get("jsonrpc") != _JSONRPC_VERSION:
            raise A2AProtocolError("INVALID_RESPONSE", "A2A JSON-RPC 版本无效")
        if envelope.get("id") != rpc_id:
            raise A2AProtocolError("INVALID_RESPONSE", "A2A JSON-RPC id 不匹配")
        if "error" in envelope:
            raise A2AProtocolError("REMOTE_ERROR", "远端 A2A Agent 拒绝了请求")
        result = _mapping(envelope.get("result"), "SendMessage result")
        has_task = "task" in result
        has_message = "message" in result
        if has_task == has_message:
            raise A2AProtocolError(
                "INVALID_RESPONSE",
                "A2A SendMessage result 必须且只能包含 task 或 message",
            )
        if has_task:
            return A2AExchange(
                source_identity=identity,
                task=_parse_task(result.get("task"), self._parse_limits),
            )
        return A2AExchange(
            source_identity=identity,
            message=_parse_message(
                result.get("message"),
                self._parse_limits,
            ),
        )


__all__ = [
    "A2A_FEATURE_ID",
    "A2A_PROTOCOL_BINDING",
    "A2A_PROTOCOL_VERSION",
    "A2AClientAdapter",
    "A2AClientRequest",
    "A2AExchange",
    "A2AInterface",
    "A2AJsonRpcTransport",
    "A2AParseLimits",
    "A2APartKind",
    "A2AProtocolError",
    "A2ARemoteArtifact",
    "A2ARemoteMessage",
    "A2ARemotePart",
    "A2ARemoteTask",
    "A2ATaskState",
    "A2ATransportError",
    "A2ATransportLimits",
    "HttpsA2AJsonRpcTransport",
]
