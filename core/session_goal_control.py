"""Session Goal 控制面与不可变 Gateway Run 身份绑定。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.db.models.gateway_control import (
    GatewayRunBindingRow,
    GatewaySessionBindingRow,
)
from core.session_goal import SessionGoalPrincipal, SessionGoalValidationError


class SessionGoalControlIdentityError(RuntimeError):
    """Session Goal 控制身份无法建立。"""


class SessionGoalControlIdentityNotFound(SessionGoalControlIdentityError):
    """Gateway Run 不存在或不再可用于控制。"""


class SessionGoalControlIdentityIntegrityError(SessionGoalControlIdentityError):
    """Gateway Run 与 Session binding 的服务端事实不一致。"""


@dataclass(frozen=True, slots=True)
class SessionGoalControlIdentity:
    """由服务端 Gateway 事实派生的目标 owner 与真实 actor。"""

    principal: SessionGoalPrincipal
    actor_id: str
    gateway_run_id: str
    gateway_binding_id: str
    transport: str


def _required_text(value: object, name: str, *, maximum: int) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(character in normalized for character in "\r\n\x00")
    ):
        raise SessionGoalControlIdentityIntegrityError(f"{name} 无效")
    return normalized


def resolve_session_goal_control_identity(
    db: Session,
    gateway_run_id: str,
) -> SessionGoalControlIdentity:
    """只从不可变 Run binding 派生身份，并核对当前 Session binding。"""

    if not isinstance(db, Session):
        raise TypeError("db 必须是 SQLAlchemy Session")
    run_id = _required_text(gateway_run_id, "gateway_run_id", maximum=160)
    run = db.get(GatewayRunBindingRow, run_id)
    if run is None:
        raise SessionGoalControlIdentityNotFound("Gateway Run 不存在")
    binding_id = _required_text(run.binding_id, "binding_id", maximum=64)
    binding = db.get(GatewaySessionBindingRow, binding_id)
    if binding is None:
        raise SessionGoalControlIdentityIntegrityError(
            "Gateway Session binding 缺失"
        )
    run_facts = (
        str(run.transport),
        str(run.owner_platform),
        str(run.owner_type),
        str(run.owner_id),
        str(run.chat_type),
        str(run.chat_stream_id),
        str(run.runtime_session_id),
    )
    session_facts = (
        str(binding.transport),
        str(binding.owner_platform),
        str(binding.owner_type),
        str(binding.owner_id),
        str(binding.chat_type),
        str(binding.chat_stream_id),
        str(binding.runtime_session_id),
    )
    if run_facts != session_facts:
        raise SessionGoalControlIdentityIntegrityError(
            "Gateway Run 与 Session binding 不一致"
        )
    try:
        principal = SessionGoalPrincipal(
            str(run.owner_platform),
            str(run.owner_type),
            str(run.owner_id),
            str(run.runtime_session_id),
        )
    except SessionGoalValidationError as exc:
        raise SessionGoalControlIdentityIntegrityError(
            "Gateway owner 事实无效"
        ) from exc
    actor_id = _required_text(run.actor_id, "actor_id", maximum=255)
    if (
        str(run.chat_type) == "private"
        and principal.owner_type == "user"
        and actor_id != principal.owner_id
    ):
        raise SessionGoalControlIdentityIntegrityError(
            "私聊 Gateway actor 与 owner 不一致"
        )
    return SessionGoalControlIdentity(
        principal=principal,
        actor_id=actor_id,
        gateway_run_id=run_id,
        gateway_binding_id=binding_id,
        transport=_required_text(run.transport, "transport", maximum=32),
    )


__all__ = [
    "SessionGoalControlIdentity",
    "SessionGoalControlIdentityError",
    "SessionGoalControlIdentityIntegrityError",
    "SessionGoalControlIdentityNotFound",
    "resolve_session_goal_control_identity",
]
