"""数据库子域的框架无关 Port。

合同层不导入 SQLAlchemy 或具体 ORM Model；SQLAlchemy 映射集中在 adapter.py。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TransactionPort(Protocol):
    def flush(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@runtime_checkable
class ChatPersistenceRepositoryPort(TransactionPort, Protocol):
    def add_chat_log(self, **values: Any) -> Any: ...

    def add_conversation_turn(self, **values: Any) -> Any: ...

    def add_sensitive_data(self, **values: Any) -> Any: ...

    def get_chat_log(self, row_id: int) -> Any | None: ...

    def find_chat_logs(
        self,
        *,
        session_id: str,
        message_id: str,
        role: str,
    ) -> Sequence[Any]: ...

    def count_pending_chat_logs(self, user_id: str) -> int: ...


@runtime_checkable
class PersonaFactRepositoryPort(TransactionPort, Protocol):
    def list_for_user(self, user_id: str, *, limit: int = 120) -> Sequence[Any]: ...

    def list_by_ids(self, ids: Sequence[int]) -> Sequence[Any]: ...
