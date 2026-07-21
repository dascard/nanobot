import pytest
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 劫持 DATABASE_URL 到内存数据库进行测试
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["NANOBOT_API_TOKEN"] = "" # 测试环境禁用 API Token
os.environ["NEW_API_KEY"] = "test-key-for-ci"  # Prevent KT init crash
os.environ["NANOBOT_TESTING"] = "1"  # 测试环境跳过生产启动副作用
os.environ.setdefault("RAG_LOCAL_RERANKER_MODEL", "./models/not-present-reranker")

from core.database import get_db  # noqa: E402
from core import database  # noqa: E402
from server import app  # noqa: E402
from tests.sqlite_test_utils import restore_in_memory_base_schema  # noqa: E402

# 创建测试专用的 engine 和 session（使用 StaticPool 允许多线程共享同一块 memoryDB）
test_engine = create_engine(
    "sqlite:///:memory:", 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# 首次也通过快照安装完整 Schema；之后每个用例恢复同一份干净快照。
restore_in_memory_base_schema(test_engine)


@pytest.fixture(autouse=True)
def isolate_semantic_provider_factory_cache():
    """避免测试之间复用真实本地模型 provider，导致 CI 下载大模型。"""
    try:
        from core.semantic.provider_factory import get_reranker_provider

        get_reranker_provider.cache_clear()
    except Exception:
        pass
    yield
    try:
        from core.semantic.provider_factory import get_reranker_provider

        get_reranker_provider.cache_clear()
    except Exception:
        pass

# 暴力替换数据库引擎，确保连未做依赖注入的写库（如 evolution）也会打到这里
database.engine = test_engine
database.SessionLocal = TestingSessionLocal

@pytest.fixture(scope="function")
def db_session():
    """提供一个干净的内存数据库 session"""
    restore_in_memory_base_schema(test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        restore_in_memory_base_schema(test_engine)

@pytest.fixture(scope="function")
def client(db_session):
    """提供 FastAPI TestClient，并覆盖 get_db 依赖"""
    from api import routes

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[routes.verify_token] = lambda: None
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()
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
