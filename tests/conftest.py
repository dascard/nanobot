import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

# 劫持 DATABASE_URL 到内存数据库进行测试
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["NANOBOT_API_TOKEN"] = "" # 测试环境禁用 API Token

from database import Base, get_db
import database
from server import app
from sqlalchemy.pool import StaticPool

# 创建测试专用的 engine 和 session（使用 StaticPool 允许多线程共享同一块 memoryDB）
test_engine = create_engine(
    "sqlite:///:memory:", 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# 暴力替换数据库引擎，确保连未做依赖注入的写库（如 evolution）也会打到这里
database.engine = test_engine
database.SessionLocal = TestingSessionLocal

@pytest.fixture(scope="function")
def db_session():
    """提供一个干净的内存数据库 session"""
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)

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
