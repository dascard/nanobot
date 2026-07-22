"""SQLAlchemy 到数据库子域 Port 的唯一 ORM Adapter。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from core.db.contracts import (
    ChatPersistenceRepositoryPort,
    PersonaFactRepositoryPort,
)


class SqlAlchemyChatPersistenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_chat_log(self, **values: Any) -> Any:
        from core.db.models.chat import ChatLog

        row = ChatLog(**values)
        self._session.add(row)
        return row

    def add_conversation_turn(self, **values: Any) -> Any:
        from core.db.models.chat import ConversationTurn

        row = ConversationTurn(**values)
        self._session.add(row)
        return row

    def add_sensitive_data(self, **values: Any) -> Any:
        from core.db.models.chat import SensitiveData

        row = SensitiveData(**values)
        self._session.add(row)
        return row

    def get_chat_log(self, row_id: int) -> Any | None:
        from core.db.models.chat import ChatLog

        return self._session.get(ChatLog, int(row_id))

    def find_chat_logs(
        self,
        *,
        session_id: str,
        message_id: str,
        role: str,
    ) -> Sequence[Any]:
        from core.db.models.chat import ChatLog

        return tuple(
            self._session.query(ChatLog)
            .filter(
                ChatLog.session_id == session_id,
                ChatLog.message_id == message_id,
                ChatLog.role == role,
            )
            .order_by(ChatLog.created_at.asc(), ChatLog.id.asc())
            .all()
        )

    def count_pending_chat_logs(self, user_id: str) -> int:
        from core.db.models.chat import ChatLog

        return int(
            self._session.query(ChatLog)
            .filter(
                ChatLog.user_id == user_id,
                ChatLog.processed == 0,
            )
            .count()
        )

    def flush(self) -> None:
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


class SqlAlchemyPersonaFactRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_user(self, user_id: str, *, limit: int = 120) -> Sequence[Any]:
        from core.db.models.persona import PersonaFact

        return tuple(
            self._session.query(PersonaFact)
            .filter(PersonaFact.user_id == user_id)
            .order_by(
                PersonaFact.evidence_count.desc(),
                PersonaFact.last_seen.desc(),
                PersonaFact.id.asc(),
            )
            .limit(max(1, int(limit)))
            .all()
        )

    def list_by_ids(self, ids: Sequence[int]) -> Sequence[Any]:
        from core.db.models.persona import PersonaFact

        normalized = tuple(sorted({int(item) for item in ids if int(item) > 0}))
        if not normalized:
            return ()
        return tuple(
            self._session.query(PersonaFact)
            .filter(PersonaFact.id.in_(normalized))
            .all()
        )

    def flush(self) -> None:
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


def chat_persistence_repository(
    value: Session | ChatPersistenceRepositoryPort,
) -> ChatPersistenceRepositoryPort:
    if isinstance(value, ChatPersistenceRepositoryPort):
        return value
    return SqlAlchemyChatPersistenceRepository(value)


def persona_fact_repository(
    value: Session | PersonaFactRepositoryPort,
) -> PersonaFactRepositoryPort:
    if isinstance(value, PersonaFactRepositoryPort):
        return value
    return SqlAlchemyPersonaFactRepository(value)
