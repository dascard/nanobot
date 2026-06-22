"""聊天请求契约与请求元信息 helper。"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from core.client_meta import ClientMetaValidationError, normalize_client_meta


class ChatProxyRequest(BaseModel):
    user_id: str = "default_user"
    session_id: str = "default_session"
    query: str = ""
    files: Optional[List[str]] = None
    sender_name: Optional[str] = None
    session_name: Optional[str] = None
    stream: bool = False
    classification_request: bool = False
    merged_messages: list[str] | None = None
    message_id: str | None = None
    source_message_ids: list[str] | None = None
    client_meta: dict | None = None


def clone_chat_request(req: ChatProxyRequest, **updates) -> ChatProxyRequest:
    if hasattr(req, "model_dump"):
        data = req.model_dump()
    else:
        data = req.dict()
    data.update(updates)
    return ChatProxyRequest(**data)


def resolve_push_target_id(req: ChatProxyRequest, is_group: bool) -> str:
    if not is_group:
        return req.user_id
    session_id = str(req.session_id or "")
    if session_id.startswith("group_"):
        return session_id[len("group_"):]
    return session_id or req.user_id


def extract_group_id_from_chat_request(req: ChatProxyRequest) -> str:
    session_id = str(req.session_id or "").strip()
    if session_id.startswith("group_"):
        return session_id[len("group_"):]
    return session_id or str(req.user_id or "").strip()


def chat_request_platform(req: ChatProxyRequest) -> str:
    client_meta = req.client_meta if isinstance(req.client_meta, dict) else {}
    return str(client_meta.get("platform") or "qq").strip().lower() or "qq"


def chat_request_type(req: ChatProxyRequest) -> str:
    return "private" if str(req.session_id).startswith("private_") else "group"


def normalize_request_client_meta(req: Any, *, expected_chat_type: str) -> dict[str, Any]:
    try:
        normalized = normalize_client_meta(
            getattr(req, "client_meta", None),
            expected_chat_type=expected_chat_type,
        )
    except ClientMetaValidationError as exc:
        raise HTTPException(400, f"invalid client_meta: {exc}") from exc
    req.client_meta = normalized
    return normalized


def private_prompt_audit_failure_meta() -> dict[str, Any]:
    return {
        "kind": "empty_reply",
        "no_context": True,
        "no_send": True,
        "agent_result": "prompt_v2_audit_failed",
    }


def private_timing_meta(decision: Any | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    scoring = getattr(decision, "timing_scoring", None)
    if not isinstance(scoring, dict):
        return None
    return {
        "mode": "private",
        "action": str(getattr(decision, "action", "") or ""),
        "reason": str(getattr(decision, "reason", "") or ""),
        "effort": str(getattr(decision, "effort", "") or ""),
        "runtime_preset": str(getattr(decision, "runtime_preset", "") or ""),
        "scoring": scoring,
    }
