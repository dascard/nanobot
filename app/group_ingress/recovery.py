"""群聊入站请求指纹与已持久化完成结果恢复。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from core.inbound_idempotency import (
    CompletedInboundResponse,
    InboundClaimKey,
    decode_completed_inbound_response,
    encode_completed_inbound_response,
)


REQUEST_META_FIELD = "inbound_request"
RECOVERY_META_FIELD = "inbound_claim_recovery"
REQUEST_CANONICALIZER = "group-business-input-v1"

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_REQUEST_FIELDS = {"schema_version", "canonicalizer", "sha256"}
_RECOVERY_FIELDS = {
    "schema_version",
    "claim_key_sha256",
    "request_sha256",
    "completed_response",
}


class GroupRecoveryCorruptError(ValueError):
    """恢复 marker 缺失、损坏、冲突或身份不匹配。"""


class GroupRequestMismatchError(ValueError):
    """failed takeover 的当前业务输入与首次 ambient 不一致。"""


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
        raise GroupRecoveryCorruptError(
            f"{field_name} 必须是 64 位小写 SHA-256"
        )
    return value


def group_business_input_sha256(payload: Mapping[str, Any]) -> str:
    """为规范化、JSON-safe 的群聊业务输入生成稳定指纹。"""

    if not isinstance(payload, Mapping):
        raise TypeError("payload 必须是 Mapping")
    encoded = _canonical_json(dict(payload)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_group_business_input(
    req: Any,
    *,
    key: InboundClaimKey,
    message_text: str,
    message_meta: Mapping[str, Any],
    sticker_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造只含群聊业务语义的版本化、可稳定哈希输入。"""

    if type(key) is not InboundClaimKey:
        raise TypeError("key 必须是 InboundClaimKey")
    if not isinstance(message_meta, Mapping):
        raise TypeError("message_meta 必须是 Mapping")
    if type(sticker_payloads) is not list:
        raise TypeError("sticker_payloads 必须是 list")

    sender = message_meta.get("sender")
    bot = message_meta.get("bot")
    directed = message_meta.get("directed")
    payload = {
        "schema_version": 1,
        "claim": {
            "platform": key.platform,
            "chat_type": key.chat_type,
            "session_id": key.session_id,
            "message_id": key.message_id,
        },
        "sender": dict(sender) if isinstance(sender, Mapping) else {},
        "session_name": str(getattr(req, "session_name", "") or ""),
        "message_text": str(message_text),
        "segments": list(message_meta.get("segments") or []),
        "mentions": list(message_meta.get("mentions") or []),
        "reply_to": message_meta.get("reply_to"),
        "directed": dict(directed) if isinstance(directed, Mapping) else {},
        "files": list(message_meta.get("files") or []),
        "stickers": list(sticker_payloads),
        "bot": dict(bot) if isinstance(bot, Mapping) else {},
        "bot_aliases": [
            str(item)
            for item in (getattr(req, "bot_aliases", None) or [])
        ],
    }
    return _copy_json_mapping(payload, field_name="group_business_input")


def claim_key_sha256(key: InboundClaimKey) -> str:
    """生成包含平台和 chat type 的 canonical claim identity 指纹。"""

    if type(key) is not InboundClaimKey:
        raise TypeError("key 必须是 InboundClaimKey")
    return group_business_input_sha256(
        {
            "platform": key.platform,
            "chat_type": key.chat_type,
            "session_id": key.session_id,
            "message_id": key.message_id,
        }
    )


def attach_group_request_fingerprint(
    meta: Mapping[str, Any],
    request_sha256: str,
) -> dict[str, Any]:
    """给 ambient meta 附加版本化业务输入指纹。"""

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


def read_group_request_sha256(meta: Mapping[str, Any]) -> str:
    """严格读取 ambient meta 中的业务输入指纹。"""

    try:
        marker = meta[REQUEST_META_FIELD]
        if type(marker) is not dict or set(marker) != _REQUEST_FIELDS:
            raise GroupRecoveryCorruptError(
                "inbound_request 字段不匹配 schema v1"
            )
        if type(marker["schema_version"]) is not int or marker["schema_version"] != 1:
            raise GroupRecoveryCorruptError(
                "未知 inbound_request schema_version"
            )
        if marker["canonicalizer"] != REQUEST_CANONICALIZER:
            raise GroupRecoveryCorruptError("未知群请求 canonicalizer")
        return _require_sha256(
            marker["sha256"],
            field_name="inbound_request.sha256",
        )
    except GroupRecoveryCorruptError:
        raise
    except (KeyError, TypeError) as exc:
        raise GroupRecoveryCorruptError(
            "缺少有效 inbound_request marker"
        ) from exc


def decode_group_meta_json(payload: Any) -> dict[str, Any]:
    """严格解析需要参与恢复裁决的 ChatLog meta。"""

    if type(payload) is not str:
        raise GroupRecoveryCorruptError(
            "ChatLog.meta_json 必须是 JSON 字符串"
        )
    try:
        body = json.loads(payload, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GroupRecoveryCorruptError("ChatLog.meta_json 损坏") from exc
    if type(body) is not dict:
        raise GroupRecoveryCorruptError(
            "ChatLog.meta_json 根节点必须是 object"
        )
    return body


def verify_group_request_sha256(
    meta_json: str,
    expected_sha256: str,
) -> dict[str, Any]:
    """验证 failed takeover 与首次 ambient 使用相同业务输入。"""

    meta = decode_group_meta_json(meta_json)
    stored_sha256 = read_group_request_sha256(meta)
    expected = _require_sha256(
        expected_sha256,
        field_name="expected_request_sha256",
    )
    if stored_sha256 != expected:
        raise GroupRequestMismatchError(
            "failed takeover 业务输入指纹不一致"
        )
    return meta


def attach_group_completion_recovery(
    meta: Mapping[str, Any],
    *,
    key: InboundClaimKey,
    request_sha256: str,
    completion: CompletedInboundResponse,
) -> dict[str, Any]:
    """给 assistant reply meta 附加传输无关的完成结果。"""

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


def decode_group_completion_recovery(
    meta: Mapping[str, Any],
    *,
    key: InboundClaimKey,
    request_sha256: str,
) -> CompletedInboundResponse:
    """严格解码并验证 assistant reply 中的完成结果。"""

    try:
        marker = meta[RECOVERY_META_FIELD]
        if type(marker) is not dict or set(marker) != _RECOVERY_FIELDS:
            raise GroupRecoveryCorruptError(
                "recovery 字段不匹配 schema v1"
            )
        if type(marker["schema_version"]) is not int or marker["schema_version"] != 1:
            raise GroupRecoveryCorruptError("未知 recovery schema_version")
        persisted_key_hash = _require_sha256(
            marker["claim_key_sha256"],
            field_name="claim_key_sha256",
        )
        if persisted_key_hash != claim_key_sha256(key):
            raise GroupRecoveryCorruptError(
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
            raise GroupRecoveryCorruptError(
                "recovery request fingerprint 不匹配"
            )
        return decode_completed_inbound_response(
            _canonical_json(marker["completed_response"])
        )
    except GroupRecoveryCorruptError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GroupRecoveryCorruptError(str(exc)) from exc


def load_group_recoverable_completion(
    db: Any,
    *,
    key: InboundClaimKey,
    request_sha256: str,
) -> CompletedInboundResponse | None:
    """按完整入站 identity 加载唯一、严格校验的群聊完成结果。"""

    from core.database import ChatLog

    rows = (
        db.query(ChatLog)
        .filter(
            ChatLog.session_id == key.session_id,
            ChatLog.message_id == key.message_id,
            ChatLog.role == "assistant",
        )
        .order_by(ChatLog.created_at.asc(), ChatLog.id.asc())
        .all()
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise GroupRecoveryCorruptError(
            "同一入站 identity 存在多个 assistant 恢复候选"
        )

    meta = decode_group_meta_json(rows[0].meta_json)
    if meta.get("kind") != "group_reply":
        raise GroupRecoveryCorruptError("assistant 恢复候选 kind 不匹配")
    if RECOVERY_META_FIELD not in meta:
        raise GroupRecoveryCorruptError(
            "已持久化 group_reply 缺少 recovery marker"
        )
    completion = decode_group_completion_recovery(
        meta,
        key=key,
        request_sha256=request_sha256,
    )
    if completion.outcome != "respond":
        raise GroupRecoveryCorruptError(
            "group_reply recovery completion 必须为 respond"
        )
    if type(rows[0].content) is not str or completion.reply != rows[0].content:
        raise GroupRecoveryCorruptError(
            "group_reply recovery reply 与 ChatLog.content 不一致"
        )
    return completion
