from __future__ import annotations


def test_sqlite_maintenance_passive_then_conditionally_truncates(tmp_path, monkeypatch):
    from sqlalchemy import create_engine

    from core.sqlite_maintenance import SQLiteMaintenanceWorker

    database_path = tmp_path / "maintenance.db"
    engine = create_engine(f"sqlite:///{database_path}")
    worker = SQLiteMaintenanceWorker(
        engine=engine,
        truncate_threshold_bytes=100,
    )
    modes: list[str] = []

    monkeypatch.setattr(worker, "_wal_size_bytes", lambda: 200)

    def checkpoint(mode: str):
        modes.append(mode)
        return (0, 4, 4) if mode == "PASSIVE" else (0, 0, 0)

    monkeypatch.setattr(worker, "_checkpoint", checkpoint)
    result = worker.run_once()

    assert modes == ["PASSIVE", "TRUNCATE"]
    assert result.mode == "truncate"
    assert result.error == ""


def test_sqlite_maintenance_does_not_truncate_when_checkpoint_busy(tmp_path, monkeypatch):
    from sqlalchemy import create_engine

    from core.sqlite_maintenance import SQLiteMaintenanceWorker

    database_path = tmp_path / "busy.db"
    engine = create_engine(f"sqlite:///{database_path}")
    worker = SQLiteMaintenanceWorker(
        engine=engine,
        truncate_threshold_bytes=1,
    )
    modes: list[str] = []
    monkeypatch.setattr(worker, "_wal_size_bytes", lambda: 1024)

    def checkpoint(mode: str):
        modes.append(mode)
        return 1, 8, 3

    monkeypatch.setattr(worker, "_checkpoint", checkpoint)
    result = worker.run_once()

    assert modes == ["PASSIVE"]
    assert result.mode == "passive"
    assert result.busy == 1


def test_sqlite_maintenance_is_disabled_for_memory_database():
    from sqlalchemy import create_engine

    from core.sqlite_maintenance import SQLiteMaintenanceWorker

    worker = SQLiteMaintenanceWorker(engine=create_engine("sqlite:///:memory:"))
    assert worker.enabled is False
    result = worker.run_once()
    assert result.mode == "disabled"
    assert result.error == ""


def test_sqlite_maintenance_resolves_default_engine_at_call_time(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine

    from core import database
    from core.sqlite_maintenance import SQLiteMaintenanceWorker

    replacement = create_engine(f"sqlite:///{tmp_path / 'replacement.db'}")
    monkeypatch.setattr(database, "engine", replacement)

    worker = SQLiteMaintenanceWorker()

    assert worker.engine is replacement
    assert worker.database_path == str(tmp_path / "replacement.db")
