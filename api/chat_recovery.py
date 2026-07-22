"""私聊入站请求指纹与已持久化完成结果恢复。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from core.db import chat_persistence_repository
from core.inbound_idempotency import (
    CompletedInboundResponse,
    InboundClaimKey,
    decode_completed_inbound_response,
    encode_completed_inbound_response,
)


REQUEST_META_FIELD = "inbound_request"
RECOVERY_META_FIELD = "inbound_claim_recovery"
REQUEST_CANONICALIZER = "private-business-input-v1"
REQUEST_JOURNAL_KIND = "private_inbound_request"

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_REQUEST_FIELDS = {"schema_version", "canonicalizer", "sha256"}
_RECOVERY_FIELDS = {
    "schema_version",
    "claim_key_sha256",
    "request_sha256",
    "completed_response",
}


class PrivateRecoveryCorruptError(ValueError):
    """恢复 journal/marker 缺失、损坏、冲突或身份不匹配。"""


class PrivateRequestMismatchError(ValueError):
    """takeover 的当前业务输入与首次私聊请求不一致。"""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"非法 JSON 常量: {value}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _copy_json_mapping(value: Mapping[str, Any], *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} 必须是 Mapping")
    copied = json.loads(
        _canonical_json(dict(value)),
        parse_constant=_reject_json_constant,
    )
    if type(copied) is not dict:
        raise TypeError(f"{field_name} 必须是 object")
    return copied


def _require_sha256(value: Any, *, field_name: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise PrivateRecoveryCorruptError(
            f"{field_name} 必须是 64 位小写 SHA-256"
        )
    return value


def private_business_input_sha256(payload: Mapping[str, Any]) -> str:
    """为规范化、JSON-safe 的私聊业务输入生成稳定指纹。"""

    if not isinstance(payload, Mapping):
        raise TypeError("payload 必须是 Mapping")
    encoded = _canonical_json(dict(payload)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_private_business_input(
    req: Any,
    *,
    key: InboundClaimKey,
) -> dict[str, Any]:
    """构造排除传输噪声的版本化私聊业务输入。"""

    if type(key) is not InboundClaimKey:
        raise TypeError("key 必须是 InboundClaimKey")
    client_meta = getattr(req, "client_meta", None)
    client_meta = client_meta if isinstance(client_meta, Mapping) else {}
    payload = {
        "schema_version": 1,
        "claim": {
            "platform": key.platform,
            "chat_type": key.chat_type,
            "session_id": key.session_id,
            "message_id": key.message_id,
        },
        "user_id": str(getattr(req, "user_id", "") or ""),
        "query": str(getattr(req, "query", "") or ""),
        "files": list(getattr(req, "files", None) or []),
        "sender_name": str(getattr(req, "sender_name", "") or ""),
        "session_name": str(getattr(req, "session_name", "") or ""),
        "source_message_ids": list(
            getattr(req, "source_message_ids", None) or []
        ),
        "classification_request": bool(
            getattr(req, "classification_request", False)
        ),
        "merged_messages": list(getattr(req, "merged_messages", None) or []),
        "client": {
            "platform": str(client_meta.get("platform") or key.platform),
            "chat_type": str(client_meta.get("chat_type") or key.chat_type),
        },
    }
    return _copy_json_mapping(payload, field_name="private_business_input")


def claim_key_sha256(key: InboundClaimKey) -> str:
    """生成包含平台和 chat type 的 canonical claim identity 指纹。"""

    if type(key) is not InboundClaimKey:
        raise TypeError("key 必须是 InboundClaimKey")
    return private_business_input_sha256(
        {
            "platform": key.platform,
            "chat_type": key.chat_type,
            "session_id": key.session_id,
            "message_id": key.message_id,
        }
    )


def attach_private_request_fingerprint(
    meta: Mapping[str, Any],
    request_sha256: str,
) -> dict[str, Any]:
    """给 request journal 附加版本化业务输入指纹。"""

    fingerprint = _require_sha256(
        request_sha256,
        field_name="request_sha256",
    )
    copied = _copy_json_mapping(meta, field_name="meta")
    copied[REQUEST_META_FIELD] = {
        "schema_version": 1,
        "canonicalizer": REQUEST_CANONICALIZER,
        "sha256": fingerprint,
    }
    return copied


def read_private_request_sha256(meta: Mapping[str, Any]) -> str:
    """严格读取 request journal 中的业务输入指纹。"""

    try:
        marker = meta[REQUEST_META_FIELD]
        if type(marker) is not dict or set(marker) != _REQUEST_FIELDS:
            raise PrivateRecoveryCorruptError(
                "inbound_request 字段不匹配 schema v1"
            )
        if type(marker["schema_version"]) is not int or marker["schema_version"] != 1:
            raise PrivateRecoveryCorruptError(
                "未知 inbound_request schema_version"
            )
        if marker["canonicalizer"] != REQUEST_CANONICALIZER:
            raise PrivateRecoveryCorruptError("未知私聊请求 canonicalizer")
        return _require_sha256(
            marker["sha256"],
            field_name="inbound_request.sha256",
        )
    except PrivateRecoveryCorruptError:
        raise
    except (KeyError, TypeError) as exc:
        raise PrivateRecoveryCorruptError(
            "缺少有效 inbound_request marker"
        ) from exc


def decode_private_meta_json(payload: Any) -> dict[str, Any]:
    """严格解析需要参与恢复裁决的 ChatLog meta。"""

    if type(payload) is not str:
        raise PrivateRecoveryCorruptError(
            "ChatLog.meta_json 必须是 JSON 字符串"
        )
    try:
        body = json.loads(payload, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PrivateRecoveryCorruptError("ChatLog.meta_json 损坏") from exc
    if type(body) is not dict:
        raise PrivateRecoveryCorruptError(
            "ChatLog.meta_json 根节点必须是 object"
        )
    return body


def verify_private_request_sha256(
    meta_json: str,
    expected_sha256: str,
) -> dict[str, Any]:
    """验证 takeover 与首次 request journal 使用相同业务输入。"""

    meta = decode_private_meta_json(meta_json)
    stored_sha256 = read_private_request_sha256(meta)
    expected = _require_sha256(
        expected_sha256,
        field_name="expected_request_sha256",
    )
    if stored_sha256 != expected:
        raise PrivateRequestMismatchError(
            "takeover 业务输入指纹不一致"
        )
    return meta


def attach_private_completion_recovery(
    meta: Mapping[str, Any],
    *,
    key: InboundClaimKey,
    request_sha256: str,
    completion: CompletedInboundResponse,
) -> dict[str, Any]:
    """给 request journal 附加传输无关的完成结果。"""

    request_hash = _require_sha256(
        request_sha256,
        field_name="request_sha256",
    )
    completion_body = json.loads(
        encode_completed_inbound_response(completion),
        parse_constant=_reject_json_constant,
    )
    copied = _copy_json_mapping(meta, field_name="meta")
    copied[RECOVERY_META_FIELD] = {
        "schema_version": 1,
        "claim_key_sha256": claim_key_sha256(key),
        "request_sha256": request_hash,
        "completed_response": completion_body,
    }
    return copied


def decode_private_completion_recovery(
    meta: Mapping[str, Any],
    *,
    key: InboundClaimKey,
    request_sha256: str,
) -> CompletedInboundResponse:
    """严格解码并验证 request journal 中的完成结果。"""

    try:
        marker = meta[RECOVERY_META_FIELD]
        if type(marker) is not dict or set(marker) != _RECOVERY_FIELDS:
            raise PrivateRecoveryCorruptError(
                "recovery 字段不匹配 schema v1"
            )
        if type(marker["schema_version"]) is not int or marker["schema_version"] != 1:
            raise PrivateRecoveryCorruptError("未知 recovery schema_version")
        if _require_sha256(
            marker["claim_key_sha256"],
            field_name="claim_key_sha256",
        ) != claim_key_sha256(key):
            raise PrivateRecoveryCorruptError(
                "recovery claim identity 不匹配"
            )
        persisted_request_hash = _require_sha256(
            marker["request_sha256"],
            field_name="request_sha256",
        )
        expected_request_hash = _require_sha256(
            request_sha256,
            field_name="expected_request_sha256",
        )
        if persisted_request_hash != expected_request_hash:
            raise PrivateRecoveryCorruptError(
                "recovery request fingerprint 不匹配"
            )
        return decode_completed_inbound_response(
            _canonical_json(marker["completed_response"])
        )
    except PrivateRecoveryCorruptError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PrivateRecoveryCorruptError(str(exc)) from exc


def load_private_request_journal(
    db: Any,
    *,
    key: InboundClaimKey,
    request_sha256: str,
) -> tuple[Any, dict[str, Any]] | None:
    """加载并验证唯一的私聊 request journal。"""

    repository = chat_persistence_repository(db)
    rows = repository.find_chat_logs(
        session_id=key.session_id,
        message_id=key.message_id,
        role="user",
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise PrivateRecoveryCorruptError(
            "同一入站 identity 存在多个 private request journal 候选"
        )
    meta = verify_private_request_sha256(rows[0].meta_json, request_sha256)
    if meta.get("kind") != REQUEST_JOURNAL_KIND:
        raise PrivateRecoveryCorruptError("private request journal kind 不匹配")
    return rows[0], meta


def load_private_recoverable_completion(
    db: Any,
    *,
    key: InboundClaimKey,
    request_sha256: str,
) -> CompletedInboundResponse | None:
    """按完整入站 identity 加载唯一、严格校验的私聊完成结果。"""

    repository = chat_persistence_repository(db)
    loaded = load_private_request_journal(
        repository,
        key=key,
        request_sha256=request_sha256,
    )
    if loaded is None:
        return None
    _journal, meta = loaded
    if RECOVERY_META_FIELD not in meta:
        return None
    completion = decode_private_completion_recovery(
        meta,
        key=key,
        request_sha256=request_sha256,
    )
    if completion.outcome != "respond":
        return completion

    rows = repository.find_chat_logs(
        session_id=key.session_id,
        message_id=key.message_id,
        role="assistant",
    )
    if len(rows) != 1:
        raise PrivateRecoveryCorruptError(
            "respond recovery 必须存在唯一 assistant ChatLog"
        )
    if type(rows[0].content) is not str or rows[0].content != completion.reply:
        raise PrivateRecoveryCorruptError(
            "private recovery reply 与 ChatLog.content 不一致"
        )
    return completion
