import pytest
from sqlalchemy import create_engine, text


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 劫持 DATABASE_URL 到内存数据库进行测试
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["NANOBOT_API_TOKEN"] = "" # 测试环境禁用 API Token
os.environ["NEW_API_KEY"] = "test-key-for-ci"  # Prevent KT init crash
os.environ["NANOBOT_TESTING"] = "1"  # 测试环境跳过生产启动副作用

from core.database import Base, get_db
from core import database
from server import app
from sqlalchemy.pool import StaticPool

# 创建测试专用的 engine 和 session（使用 StaticPool 允许多线程共享同一块 memoryDB）
test_engine = create_engine(
    "sqlite:///:memory:", 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def _drop_test_virtual_tables():
    with test_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS semantic_index_fts"))

# 暴力替换数据库引擎，确保连未做依赖注入的写库（如 evolution）也会打到这里
database.engine = test_engine
database.SessionLocal = TestingSessionLocal

@pytest.fixture(scope="function")
def db_session():
    """提供一个干净的内存数据库 session"""
    _drop_test_virtual_tables()
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)
        _drop_test_virtual_tables()

@pytest.fixture(scope="function")
def client(db_session):
    """提供 FastAPI TestClient，并覆盖 get_db 依赖"""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
            
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow", action="store_true", default=False,
        help="run tests that require external models or slow resources",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="need --run-slow to run external model tests")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
