"""共享测试夹具——临时 SQLite session、TestClient 等。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, get_db


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    """独立 SQLite 测试库。"""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'eval.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def eval_client(tmp_path, monkeypatch):
    """带独立 SQLite 的 FastAPI TestClient。"""
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "eval-token")
    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "eval-token")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'eval_client.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from server import app
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
