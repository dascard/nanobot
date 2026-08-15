import pytest
import os
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
    config.addinivalue_line(
        "markers",
        "database_only_runtime: 使用生产默认的仅入库会话模式",
    )

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# 劫持 DATABASE_URL 到内存数据库进行测试
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["NANOBOT_API_TOKEN"] = "" # 测试环境禁用 API Token
os.environ["NEW_API_KEY"] = "test-key-for-ci"  # Prevent KT init crash
os.environ["NANOBOT_TESTING"] = "1"  # 测试环境跳过生产启动副作用
os.environ.setdefault("RAG_LOCAL_RERANKER_MODEL", "./models/not-present-reranker")
# 不继承开发机 .env 的生产路径，避免测试写入运行时目录或读取宿主开关。
_TEST_RUNTIME_DIR = tempfile.TemporaryDirectory(prefix="nanobot-pytest-")
_TEST_RUNTIME_ROOT = Path(_TEST_RUNTIME_DIR.name)
shutil.copytree(
    ROOT_DIR / "prompts.v2.default",
    _TEST_RUNTIME_ROOT / "prompts_v2",
)
os.environ["LOG_DIR"] = str(_TEST_RUNTIME_ROOT / "logs")
os.environ["NANOBOT_DATA_DIR"] = str(_TEST_RUNTIME_ROOT / "data")
os.environ["NANOBOT_TEMP_DIR"] = str(_TEST_RUNTIME_ROOT / "tmp")
os.environ["NANOBOT_PROMPT_DEFAULT_DIR"] = ""
os.environ["NANOBOT_PROMPT_RUNTIME_DIR"] = ""
os.environ["NANOBOT_PROMPT_TEMPLATE_STATE_DIR"] = ""
os.environ["NANOBOT_PROMPT_V2_DIR"] = str(ROOT_DIR / "prompts.v2.default")
os.environ["NANOBOT_PROMPT_V2_RUNTIME_DIR"] = str(
    _TEST_RUNTIME_ROOT / "prompts_v2"
)
os.environ["NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE"] = str(
    ROOT_DIR / "config" / "sandbox-execution-profiles.v1.json"
)
os.environ["NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED"] = "false"
os.environ["NANOBOT_SANDBOX_SESSION_EXECUTION_ALLOWED"] = "false"
os.environ["NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED"] = "false"
os.environ["NANOBOT_SANDBOX_ENABLED"] = "false"
os.environ["NANOBOT_SANDBOX_EXEC_ENABLED"] = "false"
os.environ["NANOBOT_SANDBOX_GROUP_ENABLED"] = "false"

from core.database import get_db  # noqa: E402
from core.db import get_db as canonical_get_db  # noqa: E402
from core import database  # noqa: E402
from server import app  # noqa: E402
from tests.sqlite_test_utils import restore_in_memory_base_schema  # noqa: E402


@pytest.fixture(autouse=True)
def legacy_tests_use_model_runtime(request, monkeypatch):
    """旧用例显式使用模型模式；仅入库契约用例保留生产默认行为。"""
    if request.node.get_closest_marker("database_only_runtime") is not None:
        yield
        return

    model_runtime = lambda *_args, **_kwargs: False
    monkeypatch.setattr("api.routes.is_database_only_enabled", model_runtime)
    monkeypatch.setattr(
        "app.group_ingress.service.is_database_only_enabled",
        model_runtime,
    )
    monkeypatch.setattr(
        "api.group_utility_routes.is_database_only_enabled",
        model_runtime,
    )
    yield


@pytest.fixture(scope="function", autouse=True)
def proactive_runtime_identity():
    """测试 composition root 显式安装主动外呼进程身份。"""
    from core.proactive.runtime_identity import (
        ProactiveProcessIdentity,
        start_proactive_runtime,
        stop_proactive_runtime,
    )

    start_proactive_runtime(ProactiveProcessIdentity(
        owner="proactive-outreach:pytest",
        writer_token="pytest-writer-token-0123456789abcdef",
    ))
    try:
        yield
    finally:
        stop_proactive_runtime()

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
    from api.common_auth import AuthenticatedApiPrincipal

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[canonical_get_db] = override_get_db
    app.dependency_overrides[routes.verify_token] = lambda: (
        AuthenticatedApiPrincipal(
            subject="pytest-api-gateway",
            kind="gateway",
            scopes=frozenset({"api:access", "session_goal:control"}),
        )
    )
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
