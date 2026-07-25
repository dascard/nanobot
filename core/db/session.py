"""SQLAlchemy Engine、Session 与短事务执行边界。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import TextClause

from config import DATABASE_URL
from core.db.base import Base
from core.db import models as _models  # noqa: F401


DB_DIR = os.path.abspath("./data")
DB_PATH = os.path.join(DB_DIR, "nanobot.db")


def _is_sqlite_database_url(database_url: str) -> bool:
    try:
        return make_url(database_url).drivername.startswith("sqlite")
    except Exception:
        return False


def _sqlite_busy_timeout_ms() -> int:
    try:
        return max(
            1000,
            int(
                float(
                    os.environ.get(
                        "SQLITE_BUSY_TIMEOUT_MS",
                        "5000",
                    )
                )
            ),
        )
    except (TypeError, ValueError):
        return 5000


def sqlite_connect_args_for_url(database_url: str) -> dict[str, object]:
    if not _is_sqlite_database_url(database_url):
        return {}
    return {
        "check_same_thread": False,
        "timeout": _sqlite_busy_timeout_ms() / 1000.0,
    }


_SESSION_WRITE_TRANSACTION_IDS = "nanobot_write_transaction_ids"
_SESSION_COMMITTED_NESTED_IDS = "nanobot_committed_nested_ids"


def _current_session_write_transaction(db):
    nested = getattr(db, "get_nested_transaction", None)
    if callable(nested):
        nested_transaction = nested()
        if nested_transaction is not None:
            return nested_transaction
    root = getattr(db, "get_transaction", None)
    return root() if callable(root) else None


def _mark_session_transaction_write(db) -> None:
    transaction = _current_session_write_transaction(db)
    if transaction is None:
        return
    db.info.setdefault(_SESSION_WRITE_TRANSACTION_IDS, set()).add(
        id(transaction)
    )


def _text_sql_is_proven_read_only(raw_sql: str) -> bool:
    """只放行可保守识别的单条 SELECT。"""

    remaining = str(raw_sql or "")
    if ";" in remaining:
        return False
    while True:
        remaining = remaining.lstrip()
        if remaining.startswith("--"):
            line_ends = [
                position
                for position in (
                    remaining.find("\n", 2),
                    remaining.find("\r", 2),
                )
                if position >= 0
            ]
            if not line_ends:
                return False
            remaining = remaining[min(line_ends) + 1:]
            continue
        if remaining.startswith("/*"):
            comment_end = remaining.find("*/", 2)
            if comment_end < 0:
                return False
            remaining = remaining[comment_end + 2:]
            continue
        break
    first_token = remaining.split(None, 1)
    return bool(first_token) and first_token[0].upper() == "SELECT"


def _orm_execute_may_write(execute_state) -> bool:
    if (
        execute_state.is_insert
        or execute_state.is_update
        or execute_state.is_delete
    ):
        return True
    statement = execute_state.statement
    if isinstance(statement, (Insert, Update, Delete)):
        return True
    if isinstance(statement, TextClause):
        return not _text_sql_is_proven_read_only(statement.text)
    return False


@event.listens_for(OrmSession, "do_orm_execute", retval=True)
def _remember_session_execute_writes(execute_state):
    result = execute_state.invoke_statement()
    if _orm_execute_may_write(execute_state):
        _mark_session_transaction_write(execute_state.session)
    return result


@event.listens_for(OrmSession, "after_flush")
def _remember_flushed_session_writes(db, _flush_context) -> None:
    if db.new or db.dirty or db.deleted:
        _mark_session_transaction_write(db)


@event.listens_for(OrmSession, "after_commit")
def _remember_committed_nested_transaction(db) -> None:
    nested = getattr(db, "get_nested_transaction", None)
    transaction = nested() if callable(nested) else None
    if transaction is not None:
        db.info.setdefault(_SESSION_COMMITTED_NESTED_IDS, set()).add(
            id(transaction)
        )


@event.listens_for(OrmSession, "after_transaction_end")
def _clear_flushed_session_writes(db, transaction) -> None:
    parent = getattr(
        transaction,
        "parent",
        getattr(transaction, "_parent", None),
    )
    if parent is None:
        db.info.pop(_SESSION_WRITE_TRANSACTION_IDS, None)
        db.info.pop(_SESSION_COMMITTED_NESTED_IDS, None)
        return
    if not bool(getattr(transaction, "nested", False)):
        return

    write_ids = db.info.setdefault(_SESSION_WRITE_TRANSACTION_IDS, set())
    committed_ids = db.info.setdefault(
        _SESSION_COMMITTED_NESTED_IDS,
        set(),
    )
    transaction_id = id(transaction)
    had_writes = transaction_id in write_ids
    committed = transaction_id in committed_ids
    write_ids.discard(transaction_id)
    committed_ids.discard(transaction_id)
    if had_writes and committed:
        write_ids.add(id(parent))


def release_clean_session_transaction(
    db,
    *,
    label: str = "",
    logger=None,
) -> bool:
    """释放只读 Session 事务，避免跨长 await 持有 SQLite 事务。"""

    try:
        in_transaction = getattr(db, "in_transaction", None)
        if not callable(in_transaction) or not in_transaction():
            return False
        new_count = len(getattr(db, "new", ()) or ())
        dirty_count = len(getattr(db, "dirty", ()) or ())
        deleted_count = len(getattr(db, "deleted", ()) or ())
        pending_count = new_count + dirty_count + deleted_count
        write_ids = getattr(db, "info", {}).get(
            _SESSION_WRITE_TRANSACTION_IDS,
            set(),
        )
        root_transaction = getattr(
            db,
            "get_transaction",
            lambda: None,
        )()
        nested_transaction = getattr(
            db,
            "get_nested_transaction",
            lambda: None,
        )()
        flushed_writes = any(
            transaction is not None and id(transaction) in write_ids
            for transaction in (root_transaction, nested_transaction)
        )
        if pending_count or flushed_writes:
            if logger is not None:
                logger.warning(
                    "[DB] skip releasing session transaction "
                    "label=%s pending=%d new=%d dirty=%d deleted=%d "
                    "flushed=%d",
                    label or "unknown",
                    pending_count,
                    new_count,
                    dirty_count,
                    deleted_count,
                    int(flushed_writes),
                )
            return False
        db.rollback()
        if logger is not None:
            debug = getattr(logger, "debug", None)
            if callable(debug):
                debug(
                    "[DB] released clean session transaction before "
                    "await label=%s",
                    label or "unknown",
                )
        return True
    except Exception as exc:
        if logger is not None:
            warning = getattr(logger, "warning", None)
            if callable(warning):
                warning(
                    "[DB] failed to release session transaction "
                    "label=%s: %s",
                    label or "unknown",
                    exc,
                )
        return False


def sqlite_path_from_database_url(database_url: str) -> str | None:
    try:
        url = make_url(database_url)
    except Exception:
        return None
    if not url.drivername.startswith("sqlite"):
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    return os.path.abspath(database)


def configure_sqlite_connection(
    dbapi_connection,
    *,
    database_url: str = DATABASE_URL,
) -> None:
    if not _is_sqlite_database_url(database_url):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={_sqlite_busy_timeout_ms()}")
        if sqlite_path_from_database_url(database_url):
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


engine = create_engine(
    DATABASE_URL,
    connect_args=sqlite_connect_args_for_url(DATABASE_URL),
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    del connection_record
    configure_sqlite_connection(
        dbapi_connection,
        database_url=DATABASE_URL,
    )


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

_PhaseResultT = TypeVar("_PhaseResultT")


def session_factory_from_session(
    session: OrmSession,
) -> Callable[[], OrmSession]:
    if not isinstance(session, OrmSession):
        raise TypeError("session 必须是 SQLAlchemy Session")
    bind = session.get_bind()
    if bind is None:
        raise RuntimeError("请求 Session 缺少数据库绑定")
    return sessionmaker(
        bind=bind,
        autocommit=False,
        autoflush=session.autoflush,
        expire_on_commit=session.expire_on_commit,
    )


def run_session_phase(
    operation: Callable[[OrmSession], _PhaseResultT],
    *,
    session_factory: Callable[[], OrmSession] | None = None,
) -> _PhaseResultT:
    factory = session_factory or SessionLocal
    db = factory()
    try:
        return operation(db)
    except BaseException:
        try:
            db.rollback()
        except BaseException:
            pass
        raise
    finally:
        db.close()


async def run_session_phase_async(
    operation: Callable[[OrmSession], _PhaseResultT],
    *,
    session_factory: Callable[[], OrmSession] | None = None,
) -> _PhaseResultT:
    return await asyncio.to_thread(
        run_session_phase,
        operation,
        session_factory=session_factory,
    )


def init_db() -> None:
    db_path = sqlite_path_from_database_url(DATABASE_URL)
    if db_path:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    else:
        os.makedirs(DB_DIR, exist_ok=True)

    from core.schema_migrations import run_schema_migrations

    run_schema_migrations(engine, db_path=db_path)
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = [
    "DB_DIR",
    "DB_PATH",
    "SessionLocal",
    "configure_sqlite_connection",
    "engine",
    "get_db",
    "init_db",
    "release_clean_session_transaction",
    "run_session_phase",
    "run_session_phase_async",
    "session_factory_from_session",
    "sqlite_connect_args_for_url",
    "sqlite_path_from_database_url",
]
