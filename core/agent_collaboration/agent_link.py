"""Agent Link 协作帧到持久任务板服务的 Adapter。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from sqlalchemy.orm import Session

from core.agent_collaboration.contracts import AgentCollaborationError
from core.agent_collaboration.service import SqlAlchemyAgentCollaborationService
from core.agent_link.protocol import AgentLinkProtocolError
from core.db.session import run_session_phase_async
from core.lifecycle import FeatureScope


def _required(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    normalized = str(value or "").strip()
    if not normalized:
        raise AgentLinkProtocolError(
            "INVALID_COLLABORATION_REQUEST",
            f"协作请求缺少 {name}",
        )
    return normalized


def _positive_int(payload: Mapping[str, object], name: str) -> int:
    value = payload.get(name)
    if type(value) is not int or value <= 0:
        raise AgentLinkProtocolError(
            "INVALID_COLLABORATION_REQUEST",
            f"协作请求的 {name} 必须是正整数",
        )
    return value


class SqlAlchemyAgentLinkCollaborationAdapter:
    """每帧使用独立事务；认领 token 只返回当前私有连接。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    async def handle_collaboration(
        self,
        *,
        actor_id: str,
        message_type: str,
        request_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        if not isinstance(payload, Mapping):
            raise AgentLinkProtocolError(
                "INVALID_COLLABORATION_REQUEST",
                "协作请求 payload 必须是对象",
            )

        def operation(db: Session) -> Mapping[str, object]:
            try:
                service = SqlAlchemyAgentCollaborationService(
                    db,
                    session_factory=self._session_factory,
                )
                if message_type == "collaboration.status":
                    return service.agent_status(
                        board_id=_required(payload, "board_id"),
                        actor_id=actor_id,
                        scope=FeatureScope.PRIVATE_SESSION,
                    )
                if message_type == "collaboration.claim":
                    claim = service.claim_task(
                        board_id=_required(payload, "board_id"),
                        task_id=_required(payload, "task_id"),
                        actor_id=actor_id,
                        idempotency_key=request_id,
                        require_invitation=True,
                        scope=FeatureScope.PRIVATE_SESSION,
                    )
                    db.commit()
                    return claim.to_dict()
                if message_type == "collaboration.deliver":
                    output = payload.get("output")
                    if not isinstance(output, Mapping):
                        raise AgentLinkProtocolError(
                            "INVALID_COLLABORATION_REQUEST",
                            "协作交付缺少 output 对象",
                        )
                    result = service.submit_delivery(
                        board_id=_required(payload, "board_id"),
                        task_id=_required(payload, "task_id"),
                        actor_id=actor_id,
                        lease_token=_required(payload, "lease_token"),
                        lease_generation=_positive_int(
                            payload,
                            "lease_generation",
                        ),
                        attempt_no=_positive_int(payload, "attempt_no"),
                        output_payload=output,
                        idempotency_key=request_id,
                        scope=FeatureScope.PRIVATE_SESSION,
                    )
                    db.commit()
                    return result
                raise AgentLinkProtocolError(
                    "UNSUPPORTED_COLLABORATION_MESSAGE",
                    "不支持该协作消息类型",
                )
            except AgentLinkProtocolError:
                raise
            except AgentCollaborationError as exc:
                raise AgentLinkProtocolError(
                    exc.code,
                    exc.safe_message,
                ) from exc
            except (TypeError, ValueError) as exc:
                raise AgentLinkProtocolError(
                    "INVALID_COLLABORATION_REQUEST",
                    "协作请求字段无效",
                ) from exc
            except Exception as exc:
                raise AgentLinkProtocolError(
                    "COLLABORATION_UNAVAILABLE",
                    "协作任务板服务暂时不可用",
                ) from exc

        return await run_session_phase_async(
            operation,
            session_factory=self._session_factory,
        )


__all__ = ["SqlAlchemyAgentLinkCollaborationAdapter"]
