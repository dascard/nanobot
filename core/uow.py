"""最小 Unit of Work 封装。"""

from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session

from core.database import SessionLocal


class UnitOfWork:
    """统一管理一次业务操作中的数据库 session 生命周期。"""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal):
        self._session_factory = session_factory
        self.db: Session | None = None

    def __enter__(self) -> "UnitOfWork":
        self.db = self._session_factory()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.db is None:
            return
        try:
            if exc_type is not None:
                self.rollback()
        finally:
            self.close()

    def commit(self) -> None:
        if self.db is None:
            raise RuntimeError("UnitOfWork session is not open")
        self.db.commit()

    def rollback(self) -> None:
        if self.db is None:
            return
        self.db.rollback()

    def close(self) -> None:
        if self.db is None:
            return
        self.db.close()
        self.db = None
