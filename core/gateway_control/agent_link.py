"""Agent Link 会话控制帧到统一 Gateway 控制服务的 Adapter。"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from sqlalchemy.orm import Session

from core.agent_link.protocol import AgentLinkProtocolError
from core.agent_runtime import RuntimeOwnerType, RuntimePrincipal
from core.db.session import run_session_phase_async
from core.gateway_control.contracts import (
    GatewayControlError,
    GatewayControlIntegrityError,
    GatewayControlPrincipal,
)
from core.gateway_control.model_profiles import GatewayModelProfilePort
from core.gateway_control.service import SqlAlchemyGatewayControlService


def _required(payload: Mapping[str, object], name: str) -> str:
    normalized = str(payload.get(name) or "").strip()
    if not normalized:
        raise AgentLinkProtocolError(
            "INVALID_SESSION_CONTROL_REQUEST",
            f"会话控制请求缺少 {name}",
        )
    return normalized


def _positive_int(payload: Mapping[str, object], name: str) -> int:
    value = payload.get(name)
    if type(value) is not int or value <= 0:
        raise AgentLinkProtocolError(
            "INVALID_SESSION_CONTROL_REQUEST",
            f"会话控制请求的 {name} 必须是正整数",
        )
    return value


class SqlAlchemyAgentLinkSessionControlAdapter:
    """每个控制帧使用独立短事务，并从握手后的 peer 派生 ACL。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        model_profiles: GatewayModelProfilePort,
    ) -> None:
        self._session_factory = session_factory
        self._model_profiles = model_profiles

    def _profile_payloads(self) -> list[dict[str, object]]:
        return [
            profile.to_payload()
            for profile in self._model_profiles.list_profiles()
        ]

    async def handle_session_control(
        self,
        *,
        platform_id: str,
        owner_id: str,
        actor_id: str,
        runtime_session_id: str,
        message_type: str,
        request_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        if not isinstance(payload, Mapping):
            raise AgentLinkProtocolError(
                "INVALID_SESSION_CONTROL_REQUEST",
                "会话控制请求 payload 必须是对象",
            )
        principal = GatewayControlPrincipal(
            principal=RuntimePrincipal(
                platform=platform_id,
                owner_type=RuntimeOwnerType.USER,
                owner_id=owner_id,
            ),
            actor_id=actor_id,
            transport="agent_link",
            runtime_session_id=runtime_session_id,
        )

        def operation(db: Session) -> Mapping[str, object]:
            try:
                service = SqlAlchemyGatewayControlService(db)
                run_id = _required(payload, "run_id")
                if message_type == "session.status":
                    result = service.status(run_id, principal)
                    try:
                        profiles = self._profile_payloads()
                        profiles_available = True
                    except Exception:
                        profiles = []
                        profiles_available = False
                    return {
                        **result,
                        "available_model_profiles": profiles,
                        "model_profiles_available": profiles_available,
                    }
                if message_type == "session.stop":
                    return service.stop(
                        run_id=run_id,
                        request_id=request_id,
                        reason_code=_required(payload, "reason_code"),
                        principal=principal,
                    )
                if message_type == "session.resume":
                    return service.authorize_resume(
                        run_id=run_id,
                        request_id=request_id,
                        principal=principal,
                    )
                if message_type == "session.model_switch":
                    profiles = self._model_profiles.list_profiles()
                    return service.switch_model(
                        run_id=run_id,
                        request_id=request_id,
                        profile_id=_required(payload, "profile_id"),
                        expected_generation=_positive_int(
                            payload,
                            "expected_generation",
                        ),
                        available_profile_ids=[
                            profile.profile_id
                            for profile in profiles
                        ],
                        principal=principal,
                    )
                raise AgentLinkProtocolError(
                    "UNSUPPORTED_SESSION_CONTROL_MESSAGE",
                    "不支持该会话控制消息类型",
                )
            except AgentLinkProtocolError:
                raise
            except GatewayControlIntegrityError as exc:
                raise AgentLinkProtocolError(
                    exc.code,
                    "Gateway 控制事实暂不可用",
                ) from exc
            except GatewayControlError as exc:
                raise AgentLinkProtocolError(
                    exc.code,
                    str(exc),
                ) from exc
            except (TypeError, ValueError) as exc:
                raise AgentLinkProtocolError(
                    "INVALID_SESSION_CONTROL_REQUEST",
                    "会话控制请求字段无效",
                ) from exc
            except RuntimeError as exc:
                raise AgentLinkProtocolError(
                    "SESSION_MODEL_PROFILES_UNAVAILABLE",
                    "当前没有可用于会话切换的模型 Profile",
                ) from exc
            except Exception as exc:
                raise AgentLinkProtocolError(
                    "SESSION_CONTROL_UNAVAILABLE",
                    "Gateway 会话控制服务暂时不可用",
                ) from exc

        return await run_session_phase_async(
            operation,
            session_factory=self._session_factory,
        )


__all__ = ["SqlAlchemyAgentLinkSessionControlAdapter"]
