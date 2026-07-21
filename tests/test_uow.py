from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import User
from tests.sqlite_test_utils import install_base_schema


def _session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'uow.db'}",
        connect_args={"check_same_thread": False},
    )
    install_base_schema(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_unit_of_work_commits_and_closes_session(tmp_path):
    from core.uow import UnitOfWork

    factory = _session_factory(tmp_path)

    with UnitOfWork(session_factory=factory) as uow:
        uow.db.add(User(id="u1", name="雀"))
        uow.commit()

    db = factory()
    try:
        row = db.query(User).filter(User.id == "u1").first()
        assert row is not None
        assert row.name == "雀"
    finally:
        db.close()


def test_unit_of_work_rolls_back_on_exception(tmp_path):
    from core.uow import UnitOfWork

    factory = _session_factory(tmp_path)

    try:
        with UnitOfWork(session_factory=factory) as uow:
            uow.db.add(User(id="u2", name="临时"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    db = factory()
    try:
        assert db.query(User).filter(User.id == "u2").first() is None
    finally:
        db.close()


def test_unit_of_work_default_session_factory_is_late_bound(monkeypatch):
    from core import database
    from core.uow import UnitOfWork

    calls = []

    class FakeSession:
        def commit(self):
            calls.append("commit")

        def rollback(self):
            calls.append("rollback")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(database, "SessionLocal", lambda: FakeSession())

    with UnitOfWork() as uow:
        assert isinstance(uow.db, FakeSession)

    assert calls == ["close"]


def test_new_service_modules_do_not_import_sessionlocal():
    roots = [Path("app")]
    roots.extend(Path("api/services").glob("*.py") if Path("api/services").exists() else [])
    runtime_files = list(Path("nanobot_kt").glob("*_runtime.py"))
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(root.rglob("*.py"))
        elif root.is_file():
            files.append(root)
    files.extend(runtime_files)

    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if "SessionLocal" in text:
            offenders.append(str(path))

    assert offenders == []
