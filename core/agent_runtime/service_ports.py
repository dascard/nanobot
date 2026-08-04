"""Agent Runtime 的 Checkpoint、Artifact 与 Permission 稳定 Port。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from core.agent_runtime.contracts import (
    RuntimeArtifactRef,
    RuntimeAttribute,
    RuntimePrincipal,
    RuntimeRunIdentity,
)


def _required(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} 不能为空")
    return normalized


def _sha256(value: object, name: str, *, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized and allow_empty:
        return ""
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise ValueError(f"{name} 必须是 64 位十六进制摘要")
    return normalized


def _virtual_path(value: object) -> str:
    normalized = _required(value, "virtual_path")
    if "\\" in normalized:
        raise ValueError("virtual_path 必须使用 POSIX 分隔符")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or path.as_posix() != normalized
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("virtual_path 必须是工作区内规范相对路径")
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class RuntimeCheckpoint:
    checkpoint_id: str
    identity: RuntimeRunIdentity
    sequence: int
    schema_version: int
    created_at: datetime
    payload: bytes
    payload_sha256: str = ""
    parent_checkpoint_id: str = ""
    attributes: tuple[RuntimeAttribute, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "checkpoint_id",
            _required(self.checkpoint_id, "checkpoint_id"),
        )
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("checkpoint.identity 无效")
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("checkpoint.sequence 必须是正整数")
        if type(self.schema_version) is not int or self.schema_version <= 0:
            raise ValueError("checkpoint.schema_version 必须是正整数")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("checkpoint.created_at 必须包含时区")
        if not isinstance(self.payload, (bytes, bytearray, memoryview)):
            raise TypeError("checkpoint.payload 必须是 bytes")
        payload = bytes(self.payload)
        digest = hashlib.sha256(payload).hexdigest()
        declared = str(self.payload_sha256 or "").strip().lower()
        if declared and _sha256(declared, "checkpoint.payload_sha256") != digest:
            raise ValueError("checkpoint.payload_sha256 与 payload 不匹配")
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "payload_sha256", digest)
        object.__setattr__(
            self,
            "parent_checkpoint_id",
            str(self.parent_checkpoint_id or "").strip(),
        )
        object.__setattr__(self, "attributes", tuple(self.attributes))
        keys = [attribute.key for attribute in self.attributes]
        if len(keys) != len(set(keys)):
            raise ValueError("checkpoint.attributes 不能包含重复 key")

    @property
    def size_bytes(self) -> int:
        return len(self.payload)


@runtime_checkable
class CheckpointStore(Protocol):
    async def save(self, checkpoint: RuntimeCheckpoint) -> RuntimeCheckpoint: ...

    async def load(
        self,
        checkpoint_id: str,
        *,
        owner: RuntimePrincipal,
    ) -> RuntimeCheckpoint | None: ...

    async def load_latest(
        self,
        run_id: str,
        *,
        owner: RuntimePrincipal,
    ) -> RuntimeCheckpoint | None: ...


class InMemoryCheckpointStore:
    """严格单调、owner 隔离的测试 Store，不提供跨进程持久化。"""

    def __init__(self) -> None:
        self._records: dict[str, RuntimeCheckpoint] = {}
        self._run_order: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def save(self, checkpoint: RuntimeCheckpoint) -> RuntimeCheckpoint:
        if not isinstance(checkpoint, RuntimeCheckpoint):
            raise TypeError("checkpoint 必须是 RuntimeCheckpoint")
        async with self._lock:
            existing = self._records.get(checkpoint.checkpoint_id)
            if existing is not None:
                if existing == checkpoint:
                    return existing
                raise ValueError(
                    f"checkpoint_id 已绑定不同内容：{checkpoint.checkpoint_id}"
                )
            ordered = self._run_order.setdefault(checkpoint.identity.run_id, [])
            if ordered:
                previous = self._records[ordered[-1]]
                if previous.identity.owner != checkpoint.identity.owner:
                    raise ValueError("同一 run_id 不能切换 checkpoint owner")
                if checkpoint.sequence <= previous.sequence:
                    raise ValueError("checkpoint.sequence 必须按 run 严格递增")
                if checkpoint.parent_checkpoint_id != previous.checkpoint_id:
                    raise ValueError("后续 checkpoint 必须引用当前最新 checkpoint")
            elif checkpoint.parent_checkpoint_id:
                raise ValueError("首个 checkpoint 不能声明 parent_checkpoint_id")
            self._records[checkpoint.checkpoint_id] = checkpoint
            ordered.append(checkpoint.checkpoint_id)
            return checkpoint

    async def load(
        self,
        checkpoint_id: str,
        *,
        owner: RuntimePrincipal,
    ) -> RuntimeCheckpoint | None:
        normalized = _required(checkpoint_id, "checkpoint_id")
        async with self._lock:
            record = self._records.get(normalized)
            if record is None or record.identity.owner != owner:
                return None
            return record

    async def load_latest(
        self,
        run_id: str,
        *,
        owner: RuntimePrincipal,
    ) -> RuntimeCheckpoint | None:
        normalized = _required(run_id, "run_id")
        async with self._lock:
            for checkpoint_id in reversed(self._run_order.get(normalized, [])):
                record = self._records[checkpoint_id]
                if record.identity.owner == owner:
                    return record
            return None


@dataclass(frozen=True, slots=True)
class RuntimeArtifactPublishRequest:
    identity: RuntimeRunIdentity
    workspace_id: str
    virtual_path: str
    media_type: str = "application/octet-stream"
    expected_sha256: str = ""
    display_name: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("artifact.identity 无效")
        object.__setattr__(
            self,
            "workspace_id",
            _required(self.workspace_id, "workspace_id"),
        )
        object.__setattr__(self, "virtual_path", _virtual_path(self.virtual_path))
        object.__setattr__(
            self,
            "media_type",
            _required(self.media_type, "media_type").lower(),
        )
        object.__setattr__(
            self,
            "expected_sha256",
            _sha256(
                self.expected_sha256,
                "expected_sha256",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "display_name",
            str(self.display_name or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class RuntimeArtifactReadRequest:
    artifact: RuntimeArtifactRef
    owner: RuntimePrincipal
    offset: int = 0
    limit: int = 1024 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, RuntimeArtifactRef):
            raise ValueError("artifact ref 无效")
        if not isinstance(self.owner, RuntimePrincipal):
            raise ValueError("artifact owner 无效")
        if type(self.offset) is not int or self.offset < 0:
            raise ValueError("artifact offset 必须是非负整数")
        if type(self.limit) is not int or not 0 < self.limit <= 16 * 1024 * 1024:
            raise ValueError("artifact limit 必须在 1 到 16MiB 之间")


@dataclass(frozen=True, slots=True)
class RuntimeArtifactContent:
    artifact: RuntimeArtifactRef
    data: bytes
    offset: int
    eof: bool

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, RuntimeArtifactRef):
            raise ValueError("artifact ref 无效")
        if not isinstance(self.data, (bytes, bytearray, memoryview)):
            raise TypeError("artifact data 必须是 bytes")
        if type(self.offset) is not int or self.offset < 0:
            raise ValueError("artifact content offset 必须是非负整数")
        if not isinstance(self.eof, bool):
            raise ValueError("artifact content eof 必须是 bool")
        object.__setattr__(self, "data", bytes(self.data))


@runtime_checkable
class ArtifactPort(Protocol):
    async def publish(
        self,
        request: RuntimeArtifactPublishRequest,
    ) -> RuntimeArtifactRef: ...

    async def read(
        self,
        request: RuntimeArtifactReadRequest,
    ) -> RuntimeArtifactContent: ...


class InMemoryArtifactPort:
    """测试用 owner/workspace 隔离 ArtifactPort。"""

    def __init__(self) -> None:
        self._sources: dict[tuple[str, str, str], bytes] = {}
        self._artifacts: dict[str, bytes] = {}
        self._refs: dict[str, RuntimeArtifactRef] = {}
        self._owners: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    async def stage_source(
        self,
        *,
        owner: RuntimePrincipal,
        workspace_id: str,
        virtual_path: str,
        data: bytes,
    ) -> None:
        if not isinstance(owner, RuntimePrincipal):
            raise ValueError("artifact owner 无效")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("artifact source data 必须是 bytes")
        key = (
            owner.canonical_id,
            _required(workspace_id, "workspace_id"),
            _virtual_path(virtual_path),
        )
        async with self._lock:
            self._sources[key] = bytes(data)

    async def publish(
        self,
        request: RuntimeArtifactPublishRequest,
    ) -> RuntimeArtifactRef:
        if not isinstance(request, RuntimeArtifactPublishRequest):
            raise TypeError("request 必须是 RuntimeArtifactPublishRequest")
        source_key = (
            request.identity.owner.canonical_id,
            request.workspace_id,
            request.virtual_path,
        )
        async with self._lock:
            try:
                data = self._sources[source_key]
            except KeyError as exc:
                raise FileNotFoundError("工作区来源不存在或 owner 未授权") from exc
            digest = hashlib.sha256(data).hexdigest()
            if request.expected_sha256 and request.expected_sha256 != digest:
                raise ValueError("Artifact 来源摘要与 expected_sha256 不一致")
            artifact = RuntimeArtifactRef(
                artifact_id=f"artifact:{digest}",
                uri=f"asset://sha256/{digest}",
                sha256=digest,
                media_type=request.media_type,
                size_bytes=len(data),
            )
            existing_ref = self._refs.get(digest)
            if existing_ref is not None and existing_ref != artifact:
                raise ValueError("相同 Artifact 摘要已绑定不同元数据")
            self._artifacts.setdefault(digest, data)
            self._refs.setdefault(digest, artifact)
            self._owners.setdefault(digest, set()).add(
                request.identity.owner.canonical_id
            )
            return self._refs[digest]

    async def read(
        self,
        request: RuntimeArtifactReadRequest,
    ) -> RuntimeArtifactContent:
        if not isinstance(request, RuntimeArtifactReadRequest):
            raise TypeError("request 必须是 RuntimeArtifactReadRequest")
        digest = request.artifact.sha256
        if not digest and request.artifact.uri.startswith("asset://sha256/"):
            digest = request.artifact.uri.removeprefix("asset://sha256/")
        digest = _sha256(digest, "artifact.sha256")
        async with self._lock:
            if request.owner.canonical_id not in self._owners.get(digest, set()):
                raise PermissionError("Artifact 不存在或 owner 未授权")
            data = self._artifacts[digest]
            chunk = data[request.offset : request.offset + request.limit]
            end = request.offset + len(chunk)
            return RuntimeArtifactContent(
                artifact=self._refs[digest],
                data=chunk,
                offset=request.offset,
                eof=end >= len(data),
            )


class RuntimePermissionOutcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    ALLOW_ONCE = "allow_once"


class RuntimePermissionRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class RuntimePermissionRequest:
    request_id: str
    identity: RuntimeRunIdentity
    action: str
    resource: str
    risk: RuntimePermissionRisk
    requested_at: datetime
    attributes: tuple[RuntimeAttribute, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _required(self.request_id, "request_id"))
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("permission.identity 无效")
        object.__setattr__(self, "action", _required(self.action, "permission.action"))
        object.__setattr__(
            self,
            "resource",
            _required(self.resource, "permission.resource"),
        )
        try:
            risk = RuntimePermissionRisk(self.risk)
        except ValueError as exc:
            raise ValueError("permission.risk 无效") from exc
        object.__setattr__(self, "risk", risk)
        if self.requested_at.tzinfo is None or self.requested_at.utcoffset() is None:
            raise ValueError("permission.requested_at 必须包含时区")
        object.__setattr__(self, "attributes", tuple(self.attributes))
        keys = [attribute.key for attribute in self.attributes]
        if len(keys) != len(set(keys)):
            raise ValueError("permission.attributes 不能包含重复 key")


@dataclass(frozen=True, slots=True)
class RuntimePermissionDecision:
    decision_id: str
    request_id: str
    outcome: RuntimePermissionOutcome
    reason: str
    decided_at: datetime
    grant_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "decision_id",
            _required(self.decision_id, "decision_id"),
        )
        object.__setattr__(self, "request_id", _required(self.request_id, "request_id"))
        try:
            outcome = RuntimePermissionOutcome(self.outcome)
        except ValueError as exc:
            raise ValueError("permission.outcome 无效") from exc
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "reason", _required(self.reason, "permission.reason"))
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ValueError("permission.decided_at 必须包含时区")
        object.__setattr__(self, "grant_id", str(self.grant_id or "").strip())
        if outcome is RuntimePermissionOutcome.ALLOW_ONCE and not self.grant_id:
            raise ValueError("allow_once 决策必须携带 grant_id")


@runtime_checkable
class PermissionPort(Protocol):
    async def evaluate(
        self,
        request: RuntimePermissionRequest,
    ) -> RuntimePermissionDecision: ...


class StaticPermissionPort:
    """测试与显式默认策略使用；未配置 action 一律拒绝。"""

    def __init__(
        self,
        policies: Mapping[str, RuntimePermissionOutcome] = MappingProxyType({}),
    ) -> None:
        self._policies = MappingProxyType(
            {
                _required(action, "permission action"): RuntimePermissionOutcome(
                    outcome
                )
                for action, outcome in policies.items()
            }
        )
        self._requests: dict[str, RuntimePermissionRequest] = {}
        self._decisions: dict[str, RuntimePermissionDecision] = {}
        self._lock = asyncio.Lock()

    async def evaluate(
        self,
        request: RuntimePermissionRequest,
    ) -> RuntimePermissionDecision:
        if not isinstance(request, RuntimePermissionRequest):
            raise TypeError("request 必须是 RuntimePermissionRequest")
        async with self._lock:
            existing = self._decisions.get(request.request_id)
            if existing is not None:
                if self._requests[request.request_id] != request:
                    raise ValueError(
                        f"permission request_id 已绑定不同请求：{request.request_id}"
                    )
                return existing
            outcome = self._policies.get(
                request.action,
                RuntimePermissionOutcome.DENY,
            )
            decision = RuntimePermissionDecision(
                decision_id=f"permission:{request.request_id}",
                request_id=request.request_id,
                outcome=outcome,
                reason=f"static_policy:{outcome.value}",
                decided_at=datetime.now(timezone.utc),
                grant_id=(
                    f"grant:{request.request_id}"
                    if outcome is RuntimePermissionOutcome.ALLOW_ONCE
                    else ""
                ),
            )
            self._requests[request.request_id] = request
            self._decisions[request.request_id] = decision
            return decision


__all__ = [
    "ArtifactPort",
    "CheckpointStore",
    "InMemoryArtifactPort",
    "InMemoryCheckpointStore",
    "PermissionPort",
    "RuntimeArtifactContent",
    "RuntimeArtifactPublishRequest",
    "RuntimeArtifactReadRequest",
    "RuntimeCheckpoint",
    "RuntimePermissionDecision",
    "RuntimePermissionOutcome",
    "RuntimePermissionRequest",
    "RuntimePermissionRisk",
    "StaticPermissionPort",
]
