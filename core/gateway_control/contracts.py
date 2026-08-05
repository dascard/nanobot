"""多渠道 Gateway 会话绑定与远程 Run 控制合同。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from core.agent_runtime import RuntimeOwnerType, RuntimePrincipal


_TRANSPORT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def required_text(value: object, name: str, *, max_chars: int = 512) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} 不能为空")
    if len(normalized) > max_chars or any(
        character in normalized for character in "\r\n\x00"
    ):
        raise ValueError(f"{name} 非法")
    return normalized


def normalize_transport(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if _TRANSPORT_RE.fullmatch(normalized) is None:
        raise ValueError("transport 非法")
    return normalized


class GatewayPendingKind(StrEnum):
    NONE = "none"
    APPROVAL = "approval"
    QUESTION = "question"


class GatewayControlError(RuntimeError):
    code = "gateway_control_error"


class GatewayControlNotFound(GatewayControlError):
    code = "gateway_control_not_found"


class GatewayControlAccessDenied(GatewayControlError):
    code = "gateway_control_access_denied"


class GatewayControlConflict(GatewayControlError):
    code = "gateway_control_conflict"


class GatewayControlIntegrityError(GatewayControlError):
    code = "gateway_control_integrity_failed"


@dataclass(frozen=True, slots=True)
class GatewayControlPrincipal:
    """由渠道 Adapter 或管理鉴权派生的控制主体。"""

    principal: RuntimePrincipal
    actor_id: str
    is_admin: bool = False
    transport: str = ""
    runtime_session_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.principal, RuntimePrincipal):
            raise TypeError("principal 必须是 RuntimePrincipal")
        object.__setattr__(
            self,
            "actor_id",
            required_text(self.actor_id, "actor_id"),
        )
        if type(self.is_admin) is not bool:
            raise TypeError("is_admin 必须是 bool")
        transport = str(self.transport or "").strip()
        object.__setattr__(
            self,
            "transport",
            normalize_transport(transport) if transport else "",
        )
        runtime_session_id = str(self.runtime_session_id or "").strip()
        if runtime_session_id:
            runtime_session_id = required_text(
                runtime_session_id,
                "runtime_session_id",
                max_chars=160,
            )
        object.__setattr__(
            self,
            "runtime_session_id",
            runtime_session_id,
        )

    @classmethod
    def admin(cls, actor_id: str) -> "GatewayControlPrincipal":
        return cls(
            RuntimePrincipal(
                platform="web",
                owner_type=RuntimeOwnerType.SYSTEM,
                owner_id="nanobot-admin",
            ),
            actor_id=actor_id,
            is_admin=True,
        )


@dataclass(frozen=True, slots=True)
class GatewayRunAdmission:
    """从受信消息合同生成、随 Run 接纳原子保存的会话绑定。"""

    binding_id: str
    transport: str
    principal: RuntimePrincipal
    actor_id: str
    chat_type: str
    chat_stream_id: str
    runtime_session_id: str

    def __post_init__(self) -> None:
        binding_id = required_text(
            self.binding_id,
            "binding_id",
            max_chars=64,
        ).lower()
        if len(binding_id) != 64 or any(
            character not in "0123456789abcdef"
            for character in binding_id
        ):
            raise ValueError("binding_id 必须是 SHA-256")
        object.__setattr__(self, "binding_id", binding_id)
        object.__setattr__(
            self,
            "transport",
            normalize_transport(self.transport),
        )
        if not isinstance(self.principal, RuntimePrincipal):
            raise TypeError("principal 必须是 RuntimePrincipal")
        object.__setattr__(
            self,
            "actor_id",
            required_text(self.actor_id, "actor_id"),
        )
        chat_type = str(self.chat_type or "").strip().lower()
        if chat_type not in {"private", "group"}:
            raise ValueError("chat_type 非法")
        object.__setattr__(self, "chat_type", chat_type)
        object.__setattr__(
            self,
            "chat_stream_id",
            required_text(
                self.chat_stream_id,
                "chat_stream_id",
                max_chars=640,
            ),
        )
        object.__setattr__(
            self,
            "runtime_session_id",
            required_text(
                self.runtime_session_id,
                "runtime_session_id",
                max_chars=160,
            ),
        )


__all__ = [
    "GatewayControlAccessDenied",
    "GatewayControlConflict",
    "GatewayControlError",
    "GatewayControlIntegrityError",
    "GatewayControlNotFound",
    "GatewayControlPrincipal",
    "GatewayPendingKind",
    "GatewayRunAdmission",
    "normalize_transport",
    "required_text",
]
