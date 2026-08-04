"""Run Checkpoint 的序列化、版本证明与恢复结果合同。"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from types import MappingProxyType
from core.agent_runtime import (
    RuntimeActor,
    RuntimeActorType,
    RuntimeCheckpointBoundary,
    RuntimeCheckpointReference,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimeOwnerType,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
    RuntimeRecoveryOperationKind,
    RuntimeRunIdentity,
    RuntimeToolCall,
    RuntimeToolCallStatus,
)


RUN_CHECKPOINT_SCHEMA_VERSION = 1
RUN_CHECKPOINT_PAYLOAD_ENCODING = "json+gzip"
RUN_CHECKPOINT_MAX_STATE_BYTES = 4 * 1024 * 1024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_INLINE_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{16,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
)
_SECRET_KEYS = frozenset({
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "client_secret",
    "oauth_token",
    "passphrase",
    "passwd",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "secret_value",
})


class RunRecoveryError(RuntimeError):
    code = "run_recovery_failed"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code:
            self.code = str(code)
        super().__init__(message)


class RunRecoveryNotFound(RunRecoveryError):
    code = "run_recovery_not_found"


class RunRecoveryAccessDenied(RunRecoveryError):
    code = "run_recovery_access_denied"


class RunRecoveryConflict(RunRecoveryError):
    code = "run_recovery_conflict"


class RunRecoveryIntegrityError(RunRecoveryError):
    code = "run_recovery_integrity_failed"


class RunRecoveryPreflightDenied(RunRecoveryError):
    code = "run_recovery_preflight_denied"


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def require_sha256(value: object, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{name} 必须是 64 位十六进制摘要")
    return normalized


def _redact_string(value: str) -> str:
    result = value
    for pattern in _INLINE_SECRET_PATTERNS:
        result = pattern.sub("[redacted]", result)
    return result


def sanitize_checkpoint_value(value: object, *, depth: int = 0) -> object:
    """保留模型可见状态，但剥离常见凭据字段和内联秘密。"""

    if depth > 32:
        raise ValueError("checkpoint 状态嵌套超过 32 层")
    if value is None or type(value) in {bool, int, float}:
        return value
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if key.strip().lower() in _SECRET_KEYS:
                normalized[key] = {"redacted": True}
            else:
                normalized[key] = sanitize_checkpoint_value(
                    item,
                    depth=depth + 1,
                )
        return normalized
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray, memoryview),
    ):
        return [
            sanitize_checkpoint_value(item, depth=depth + 1)
            for item in value
        ]
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {
            "binary_redacted": True,
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return _redact_string(str(value))


def _virtual_path(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized or "\\" in normalized:
        raise ValueError("恢复文件证明必须使用工作区内 POSIX 相对路径")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or path.as_posix() != normalized
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("恢复文件证明必须使用工作区内规范相对路径")
    return normalized


@dataclass(frozen=True, slots=True)
class RunRecoveryFileProof:
    workspace_id: str
    virtual_path: str
    sha256: str
    exists: bool = True

    def __post_init__(self) -> None:
        workspace_id = str(self.workspace_id or "").strip()
        if not workspace_id:
            raise ValueError("file proof.workspace_id 不能为空")
        object.__setattr__(self, "workspace_id", workspace_id)
        object.__setattr__(self, "virtual_path", _virtual_path(self.virtual_path))
        object.__setattr__(
            self,
            "sha256",
            require_sha256(self.sha256, "file proof.sha256"),
        )
        if not isinstance(self.exists, bool):
            raise ValueError("file proof.exists 必须是 bool")

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "virtual_path": self.virtual_path,
            "sha256": self.sha256,
            "exists": self.exists,
        }


@dataclass(frozen=True, slots=True)
class RunRecoveryArtifactProof:
    workspace_id: str
    artifact_id: str
    sha256: str
    size_bytes: int = 0
    media_type: str = "application/octet-stream"

    def __post_init__(self) -> None:
        for name in ("workspace_id", "artifact_id"):
            normalized = str(getattr(self, name) or "").strip()
            if not normalized:
                raise ValueError(f"artifact proof.{name} 不能为空")
            object.__setattr__(self, name, normalized)
        object.__setattr__(
            self,
            "sha256",
            require_sha256(self.sha256, "artifact proof.sha256"),
        )
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError("artifact proof.size_bytes 必须是非负整数")
        media_type = str(self.media_type or "application/octet-stream").strip().lower()
        if not media_type:
            raise ValueError("artifact proof.media_type 不能为空")
        object.__setattr__(self, "media_type", media_type)

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
        }


@dataclass(frozen=True, slots=True)
class RunCheckpointState:
    reference: RuntimeCheckpointReference
    identity: RuntimeRunIdentity
    runtime_id: str
    runtime_protocol_version: str
    messages: tuple[RuntimeMessage, ...]
    plans: tuple[RuntimePlanRef, ...]
    model_route: RuntimeModelRoute | None
    model_step: int
    tool_round: int
    file_proofs: tuple[RunRecoveryFileProof, ...]
    artifact_proofs: tuple[RunRecoveryArtifactProof, ...]
    side_effect_receipt_ids: tuple[str, ...]
    side_effect_frontier: int
    state_sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.reference, RuntimeCheckpointReference):
            raise ValueError("checkpoint state.reference 无效")
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("checkpoint state.identity 无效")
        if self.reference.run_id != self.identity.run_id:
            raise ValueError("checkpoint state run_id 不一致")
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "plans", tuple(self.plans))
        object.__setattr__(self, "file_proofs", tuple(self.file_proofs))
        object.__setattr__(self, "artifact_proofs", tuple(self.artifact_proofs))
        object.__setattr__(
            self,
            "side_effect_receipt_ids",
            tuple(self.side_effect_receipt_ids),
        )
        if type(self.side_effect_frontier) is not int or self.side_effect_frontier < 0:
            raise ValueError("checkpoint side_effect_frontier 必须是非负整数")
        object.__setattr__(
            self,
            "state_sha256",
            require_sha256(self.state_sha256, "checkpoint state_sha256"),
        )
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("checkpoint created_at 必须包含时区")

    def plan(self, kind: RuntimePlanKind) -> RuntimePlanRef | None:
        return next((item for item in self.plans if item.kind is kind), None)


@dataclass(frozen=True, slots=True)
class RunRecoveryPreflight:
    allowed: bool
    operation_kind: RuntimeRecoveryOperationKind
    source_run_id: str
    checkpoint_id: str
    source_head_sequence: int
    source_head_sha256: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "operation_kind": self.operation_kind.value,
            "source_run_id": self.source_run_id,
            "checkpoint_id": self.checkpoint_id,
            "source_head_sequence": self.source_head_sequence,
            "source_head_sha256": self.source_head_sha256,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class RunRecoveryPreparedOperation:
    operation_id: str
    operation_kind: RuntimeRecoveryOperationKind
    child_run_id: str
    restored_checkpoint_id: str
    status: str
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "operation_kind": self.operation_kind.value,
            "child_run_id": self.child_run_id,
            "restored_checkpoint_id": self.restored_checkpoint_id,
            "status": self.status,
            "idempotent_replay": self.idempotent_replay,
        }


def _tool_call_to_dict(call: RuntimeToolCall) -> dict[str, object]:
    return {
        "call_id": call.call_id,
        "name": call.name,
        "arguments": sanitize_checkpoint_value(call.arguments),
        "status": call.status.value,
        "result": sanitize_checkpoint_value(call.result),
    }


def _message_to_dict(message: RuntimeMessage) -> dict[str, object]:
    return {
        "role": message.role,
        "content": sanitize_checkpoint_value(message.content),
        "name": message.name,
        "tool_call_id": message.tool_call_id,
        "tool_calls": [_tool_call_to_dict(item) for item in message.tool_calls],
    }


def _plan_to_dict(plan: RuntimePlanRef) -> dict[str, str]:
    return {
        "kind": plan.kind.value,
        "identity": plan.identity,
        "sha256": plan.sha256,
    }


def _model_route_to_dict(route: RuntimeModelRoute | None) -> dict[str, object] | None:
    if route is None:
        return None
    return {
        "route_id": route.route_id,
        "model_id": route.model_id,
        "provider_id": route.provider_id,
        "profile_id": route.profile_id,
        "temperature": route.temperature,
        "max_tokens": route.max_tokens,
        "timeout_seconds": route.timeout_seconds,
        "enable_thinking": route.enable_thinking,
    }


def checkpoint_state_document(
    *,
    identity: RuntimeRunIdentity,
    boundary: RuntimeCheckpointBoundary,
    runtime_id: str,
    runtime_protocol_version: str,
    messages: tuple[RuntimeMessage, ...],
    plans: tuple[RuntimePlanRef, ...],
    model_route: RuntimeModelRoute | None,
    model_step: int,
    tool_round: int,
    file_proofs: tuple[RunRecoveryFileProof, ...],
    artifact_proofs: tuple[RunRecoveryArtifactProof, ...],
    side_effect_receipt_ids: tuple[str, ...],
    side_effect_frontier: int,
    resumable: bool,
) -> dict[str, object]:
    return {
        "schema_version": RUN_CHECKPOINT_SCHEMA_VERSION,
        "boundary": RuntimeCheckpointBoundary(boundary).value,
        "identity": {
            "run_id": identity.run_id,
            "turn_id": identity.turn_id,
            "correlation_id": identity.correlation_id,
            "actor": {
                "actor_type": identity.actor.actor_type.value,
                "actor_id": identity.actor.actor_id,
                "parent_actor_id": identity.actor.parent_actor_id,
            },
            "owner": {
                "platform": identity.owner.platform,
                "owner_type": identity.owner.owner_type.value,
                "owner_id": identity.owner.owner_id,
            },
        },
        "runtime": {
            "runtime_id": str(runtime_id),
            "protocol_version": str(runtime_protocol_version),
        },
        "messages": [_message_to_dict(item) for item in messages],
        "plans": [_plan_to_dict(item) for item in plans],
        "model_route": _model_route_to_dict(model_route),
        "progress": {
            "model_step": int(model_step),
            "tool_round": int(tool_round),
        },
        "file_proofs": [item.to_dict() for item in file_proofs],
        "artifact_proofs": [item.to_dict() for item in artifact_proofs],
        "side_effect_receipt_ids": list(side_effect_receipt_ids),
        "side_effect_frontier": int(side_effect_frontier),
        "resumable": bool(resumable),
    }


def encode_checkpoint_document(document: Mapping[str, object]) -> tuple[bytes, str, str]:
    canonical = canonical_json_bytes(dict(document))
    if len(canonical) > RUN_CHECKPOINT_MAX_STATE_BYTES:
        raise ValueError("checkpoint 状态超过 4MiB 上限")
    state_sha256 = hashlib.sha256(canonical).hexdigest()
    blob = gzip.compress(canonical, compresslevel=6, mtime=0)
    return blob, hashlib.sha256(blob).hexdigest(), state_sha256


def decode_checkpoint_document(
    payload_blob: bytes,
    *,
    expected_payload_sha256: str,
    expected_state_sha256: str,
) -> Mapping[str, object]:
    raw = bytes(payload_blob)
    if len(raw) > RUN_CHECKPOINT_MAX_STATE_BYTES + 64 * 1024:
        raise RunRecoveryIntegrityError("Checkpoint 压缩 payload 超过大小上限")
    if hashlib.sha256(raw).hexdigest() != require_sha256(
        expected_payload_sha256,
        "expected_payload_sha256",
    ):
        raise RunRecoveryIntegrityError("Checkpoint payload 摘要不一致")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
            canonical = stream.read(RUN_CHECKPOINT_MAX_STATE_BYTES + 1)
    except (OSError, EOFError) as exc:
        raise RunRecoveryIntegrityError("Checkpoint payload 压缩内容损坏") from exc
    if len(canonical) > RUN_CHECKPOINT_MAX_STATE_BYTES:
        raise RunRecoveryIntegrityError("Checkpoint 解压后超过状态大小上限")
    if hashlib.sha256(canonical).hexdigest() != require_sha256(
        expected_state_sha256,
        "expected_state_sha256",
    ):
        raise RunRecoveryIntegrityError("Checkpoint state 摘要不一致")
    try:
        value = json.loads(canonical)
    except json.JSONDecodeError as exc:
        raise RunRecoveryIntegrityError("Checkpoint state JSON 损坏") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RunRecoveryIntegrityError("Checkpoint schema version 不受支持")
    if canonical_json_bytes(value) != canonical:
        raise RunRecoveryIntegrityError("Checkpoint state 不是 canonical JSON")
    return MappingProxyType(value)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RunRecoveryIntegrityError(f"Checkpoint {name} 无效")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise RunRecoveryIntegrityError(f"Checkpoint {name} 无效")
    return value


def checkpoint_document_identity(document: Mapping[str, object]) -> RuntimeRunIdentity:
    identity = _mapping(document.get("identity"), "identity")
    actor = _mapping(identity.get("actor"), "identity.actor")
    owner = _mapping(identity.get("owner"), "identity.owner")
    return RuntimeRunIdentity(
        run_id=str(identity.get("run_id") or ""),
        turn_id=str(identity.get("turn_id") or ""),
        correlation_id=str(identity.get("correlation_id") or ""),
        actor=RuntimeActor(
            RuntimeActorType(str(actor.get("actor_type") or "")),
            str(actor.get("actor_id") or ""),
            str(actor.get("parent_actor_id") or ""),
        ),
        owner=RuntimePrincipal(
            platform=str(owner.get("platform") or ""),
            owner_type=RuntimeOwnerType(str(owner.get("owner_type") or "")),
            owner_id=str(owner.get("owner_id") or ""),
        ),
    )


def checkpoint_document_messages(
    document: Mapping[str, object],
) -> tuple[RuntimeMessage, ...]:
    messages: list[RuntimeMessage] = []
    for raw_message in _sequence(document.get("messages"), "messages"):
        message = _mapping(raw_message, "message")
        calls: list[RuntimeToolCall] = []
        for raw_call in _sequence(message.get("tool_calls", ()), "tool_calls"):
            call = _mapping(raw_call, "tool_call")
            calls.append(RuntimeToolCall(
                call_id=str(call.get("call_id") or ""),
                name=str(call.get("name") or ""),
                arguments=call.get("arguments"),
                status=RuntimeToolCallStatus(str(call.get("status") or "requested")),
                result=call.get("result"),
            ))
        messages.append(RuntimeMessage(
            role=str(message.get("role") or ""),
            content=message.get("content"),
            name=str(message.get("name") or ""),
            tool_call_id=str(message.get("tool_call_id") or ""),
            tool_calls=tuple(calls),
        ))
    return tuple(messages)


def checkpoint_document_plans(
    document: Mapping[str, object],
) -> tuple[RuntimePlanRef, ...]:
    return tuple(
        RuntimePlanRef(
            RuntimePlanKind(str(plan.get("kind") or "")),
            str(plan.get("identity") or ""),
            str(plan.get("sha256") or ""),
        )
        for plan in (
            _mapping(item, "plan")
            for item in _sequence(document.get("plans"), "plans")
        )
    )


def checkpoint_document_model_route(
    document: Mapping[str, object],
) -> RuntimeModelRoute | None:
    raw = document.get("model_route")
    if raw is None:
        return None
    route = _mapping(raw, "model_route")
    return RuntimeModelRoute(
        route_id=str(route.get("route_id") or ""),
        model_id=str(route.get("model_id") or ""),
        provider_id=str(route.get("provider_id") or ""),
        profile_id=str(route.get("profile_id") or ""),
        temperature=route.get("temperature"),
        max_tokens=route.get("max_tokens"),
        timeout_seconds=route.get("timeout_seconds"),
        enable_thinking=route.get("enable_thinking"),
    )


def checkpoint_document_file_proofs(
    document: Mapping[str, object],
) -> tuple[RunRecoveryFileProof, ...]:
    return tuple(
        RunRecoveryFileProof(
            workspace_id=str(item.get("workspace_id") or ""),
            virtual_path=str(item.get("virtual_path") or ""),
            sha256=str(item.get("sha256") or ""),
            exists=item.get("exists") is True,
        )
        for item in (
            _mapping(value, "file_proof")
            for value in _sequence(document.get("file_proofs"), "file_proofs")
        )
    )


def checkpoint_document_artifact_proofs(
    document: Mapping[str, object],
) -> tuple[RunRecoveryArtifactProof, ...]:
    return tuple(
        RunRecoveryArtifactProof(
            workspace_id=str(item.get("workspace_id") or ""),
            artifact_id=str(item.get("artifact_id") or ""),
            sha256=str(item.get("sha256") or ""),
            size_bytes=int(item.get("size_bytes") or 0),
            media_type=str(item.get("media_type") or "application/octet-stream"),
        )
        for item in (
            _mapping(value, "artifact_proof")
            for value in _sequence(
                document.get("artifact_proofs"),
                "artifact_proofs",
            )
        )
    )


def version_proof_mapping(plans: tuple[RuntimePlanRef, ...]) -> Mapping[str, str]:
    return MappingProxyType({item.kind.value: item.sha256 for item in plans})


__all__ = [
    "RUN_CHECKPOINT_MAX_STATE_BYTES",
    "RUN_CHECKPOINT_PAYLOAD_ENCODING",
    "RUN_CHECKPOINT_SCHEMA_VERSION",
    "RunCheckpointState",
    "RunRecoveryAccessDenied",
    "RunRecoveryArtifactProof",
    "RunRecoveryConflict",
    "RunRecoveryError",
    "RunRecoveryFileProof",
    "RunRecoveryIntegrityError",
    "RunRecoveryNotFound",
    "RunRecoveryPreflight",
    "RunRecoveryPreflightDenied",
    "RunRecoveryPreparedOperation",
    "canonical_json_bytes",
    "canonical_sha256",
    "checkpoint_document_artifact_proofs",
    "checkpoint_document_file_proofs",
    "checkpoint_document_identity",
    "checkpoint_document_messages",
    "checkpoint_document_model_route",
    "checkpoint_document_plans",
    "checkpoint_state_document",
    "decode_checkpoint_document",
    "encode_checkpoint_document",
    "require_sha256",
    "sanitize_checkpoint_value",
    "sha256_text",
    "version_proof_mapping",
]
