"""根据受信请求上下文派生 Sandbox owner。"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from foundation.identity import Principal
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError


OwnerType = Literal["user", "group", "project"]


def _required_identity(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or "\x00" in normalized or len(normalized) > 255:
        raise SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "无法从受信请求上下文确认 Sandbox 身份",
            hint=f"请求上下文缺少有效的 {field}",
        )
    return normalized


def derive_principal(
    context: Mapping[str, Any],
    *,
    group_enabled: bool = False,
) -> Principal:
    """忽略模型参数，只读取 Bridge 写入的请求级身份字段。"""

    platform = _required_identity(context.get("platform"), "platform")
    chat_type = str(context.get("chat_type") or "").strip().lower()
    if chat_type in {"private", "private_superuser"}:
        return Principal(
            platform=platform,
            owner_type="user",
            owner_id=_required_identity(context.get("user_id"), "user_id"),
        )
    if chat_type == "group":
        if not group_enabled:
            raise SandboxServiceError(
                SandboxErrorCode.SANDBOX_NOT_ENABLED,
                "群聊 Workspace 当前未启用",
                hint="停止重试，并告知用户当前只开放私聊 Workspace",
            )
        return Principal(
            platform=platform,
            owner_type="group",
            owner_id=_required_identity(context.get("group_id"), "group_id"),
        )
    raise SandboxServiceError(
        SandboxErrorCode.AUTHORIZATION_FAILED,
        "无法从受信请求上下文确认 Sandbox 身份",
        hint="请求上下文中的 chat_type 无效",
    )
