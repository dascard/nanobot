"""显式 SQLAlchemy Unit of Work Adapter。"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session


class UnitOfWork:
    """统一管理一次业务操作中的数据库 session 生命周期。"""

    def __init__(self, session_factory: Callable[[], Session] | None = None):
        if session_factory is None:
            from core import database

            session_factory = database.SessionLocal
        self._session_factory = session_factory
        self.db: Session | None = None

    def __enter__(self) -> "UnitOfWork":
        self.open()
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

    def open(self) -> Session:
        """显式打开一次工作单元，供不便整体缩进的应用服务管理生命周期。"""

        if self.db is not None:
            raise RuntimeError("UnitOfWork session is already open")
        self.db = self._session_factory()
        return self.db

    def rollback(self) -> None:
        if self.db is None:
            return
        self.db.rollback()

    def close(self) -> None:
        if self.db is None:
            return
        self.db.close()
        self.db = None
