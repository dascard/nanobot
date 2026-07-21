"""SQLite 测试库的快速、等价 Schema 初始化。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from core.database import Base


@lru_cache(maxsize=1)
def base_schema_snapshot() -> bytes:
    """只构建一次完整空 Schema，并返回可直接写入 SQLite 文件的快照。"""

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        Base.metadata.create_all(bind=engine)
        connection = engine.raw_connection()
        try:
            return bytes(connection.driver_connection.serialize())
        finally:
            connection.close()
    finally:
        engine.dispose()


def install_base_schema(engine: Engine) -> None:
    """为全新测试 Engine 安装完整 Base Schema。

    文件型 SQLite 直接写入已缓存的空库快照，避免每个测试重复执行数十条
    同步 DDL；内存库仍使用 SQLAlchemy 的标准建表路径。
    """

    if engine.url.get_backend_name() != "sqlite":
        raise ValueError("测试 Schema 快照只支持 SQLite")
    database = engine.url.database
    if not database or database == ":memory:":
        Base.metadata.create_all(bind=engine)
        return

    database_path = Path(database)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists() and database_path.stat().st_size:
        raise RuntimeError("测试数据库必须为空，不能覆盖已有数据")
    database_path.write_bytes(base_schema_snapshot())


def restore_in_memory_base_schema(engine: Engine) -> None:
    """把无活动事务的内存测试库恢复为干净的完整 Base Schema。"""

    if engine.url.get_backend_name() != "sqlite" or engine.url.database != ":memory:":
        raise ValueError("内存 Schema 恢复只支持 SQLite :memory: Engine")
    connection = engine.raw_connection()
    try:
        connection.rollback()
        connection.driver_connection.deserialize(base_schema_snapshot())
    finally:
        connection.close()


__all__ = [
    "base_schema_snapshot",
    "install_base_schema",
    "restore_in_memory_base_schema",
]
