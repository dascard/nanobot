import json
import os
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core.database import AdminAuditLog, Base, GroupMemory, RagDebugRun, SemanticIndexItem


def _auth_header():
    return {"Authorization": "Bearer test-token"}


def _file_db(path: Path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS semantic_index_fts USING fts5("
            "title, text, lexical_text, source_type UNINDEXED, source_id UNINDEXED, source_sub_id UNINDEXED, "
            "tokenize = 'trigram')"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
        ))
        conn.execute(text(
            "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) "
            "VALUES (1, 'baseline', '2026-05-28T00:00:00')"
        ))
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _seed_memory_case(db):
    row = SemanticIndexItem(
        id=100,
        source_type="memory_digest",
        source_id="42",
        source_sub_id="digest:level2",
        status="active",
        visibility="recall",
        title="RAG benchmark",
        text="RAG benchmark readonly case",
        lexical_text="RAG benchmark readonly case",
    )
    db.add(row)
    db.add(RagDebugRun(trace_id="before", source_type="memory", query="before"))
    db.commit()
    db.execute(text(
        "INSERT INTO semantic_index_fts(rowid, title, text, lexical_text, source_type, source_id, source_sub_id) "
        "VALUES (100, 'RAG benchmark', 'RAG benchmark readonly case', "
        "'RAG benchmark readonly case', 'memory_digest', '42', 'digest:level2')"
    ))
    db.commit()


def _configure_paths(monkeypatch, tmp_path, db_path):
    import api.admin.rag_benchmark_routes as routes

    manual = tmp_path / "manual"
    generated = tmp_path / "generated"
    reports = tmp_path / "reports"
    backups = tmp_path / "backups"
    trash = tmp_path / "trash"
    for path in (manual, generated, reports, backups, trash):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(routes, "BENCHMARK_MANUAL_DIR", manual)
    monkeypatch.setattr(routes, "BENCHMARK_GENERATED_DIR", generated)
    monkeypatch.setattr(routes, "BENCHMARK_REPORT_DIR", reports)
    monkeypatch.setattr(routes, "BENCHMARK_CASE_BACKUP_DIR", backups)
    monkeypatch.setattr(routes, "BENCHMARK_CASE_TRASH_DIR", trash)
    monkeypatch.setattr(routes, "get_benchmark_db_path", lambda: db_path)
    return routes, manual, generated, reports, backups, trash


def test_benchmark_status_reports_preflight_and_writable_dirs(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    _engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    _configure_paths(monkeypatch, tmp_path, db_path)

    response = client.get("/api/v1/admin/rag/benchmark/status", headers=_auth_header())

    assert response.status_code == 200
    data = response.json()
    assert data["db_readonly_supported"] is True
    assert data["manual_dir_writable"] is True
    assert data["generated_dir_writable"] is True
    assert data["reports_dir_writable"] is True
    assert data["preflight"]["ok"] is True
    assert data["db_fingerprint"]["semantic_index_count"] == 1


def test_benchmark_run_is_readonly_and_does_not_ensure_schema(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    routes, manual, _generated, _reports, _backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)
    (manual / "memory_case.json").write_text(json.dumps({
        "id": "memory_case",
        "suite": "rag_benchmark",
        "source_type": "memory",
        "case_type": "positive",
        "query": "RAG benchmark readonly",
        "expected": {"candidate_ids": ["memory_digest:42:digest:level2"], "hit_at": 5},
        "meta": {"origin": "manual"},
    }, ensure_ascii=False), encoding="utf-8")

    def fail_ensure(_bind):
        raise AssertionError("benchmark readonly run must not ensure semantic schema")

    monkeypatch.setattr("core.semantic.retriever.ensure_semantic_schema", fail_ensure)
    before_tables = set(name for (name,) in engine.connect().execute(text(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    )).fetchall())
    before_runs = engine.connect().execute(text("SELECT COUNT(*) FROM rag_debug_runs")).scalar()
    before_migrations = engine.connect().execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar()

    response = client.post(
        "/api/v1/admin/rag/benchmark/run",
        headers=_auth_header(),
        json={"provider_mode": "deterministic", "include_generated": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["metrics"]["overall"]["total_cases"] == 1
    assert data["metrics"]["overall"]["degraded_rate"] == 0
    after_tables = set(name for (name,) in engine.connect().execute(text(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    )).fetchall())
    assert after_tables == before_tables
    assert engine.connect().execute(text("SELECT COUNT(*) FROM rag_debug_runs")).scalar() == before_runs
    assert engine.connect().execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar() == before_migrations


def test_benchmark_run_returns_readable_case_results(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    _engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    _routes, manual, _generated, _reports, _backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)
    (manual / "memory_case.json").write_text(json.dumps({
        "id": "memory_case",
        "suite": "rag_benchmark",
        "source_type": "memory",
        "case_type": "positive",
        "query": "RAG benchmark readonly",
        "expected": {"candidate_ids": ["memory_digest:42:digest:level2"], "hit_at": 5},
        "meta": {"origin": "manual"},
    }, ensure_ascii=False), encoding="utf-8")

    response = client.post(
        "/api/v1/admin/rag/benchmark/run",
        headers=_auth_header(),
        json={"provider_mode": "deterministic", "include_generated": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["case_results"][0]["case_id"] == "memory_case"
    assert data["case_results"][0]["query_preview"] == "RAG benchmark readonly"
    assert data["case_results"][0]["ok"] is True
    assert data["case_results"][0]["expected_candidate_ids"] == ["memory_digest:42:digest:level2"]
    assert data["case_results"][0]["candidates"][0]["candidate_id"] == "memory_digest:42:digest:level2"
    assert data["case_results"][0]["candidates"][0]["title"] == "RAG benchmark"
    assert data["case_results"][0]["candidates"][0]["text_preview"] == "RAG benchmark readonly case"


def test_group_memory_case_results_include_time_and_metadata(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    _engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.add(GroupMemory(
        id=1,
        group_id="group_1",
        memory_type="topic",
        content="小说《灵歌》完结与番外讨论",
        content_hash="gm1",
        evidence_log_ids_json="[11, 12]",
        confidence=0.8,
        evidence_count=2,
        first_seen=datetime(2026, 5, 1, 10, 0, 0),
        last_seen=datetime(2026, 5, 27, 21, 0, 0),
        updated_at=datetime(2026, 5, 27, 21, 30, 0),
        decay_score=0.9,
        status="active",
        inject_policy="auto",
        source="group_analysis",
        injected_count=3,
    ))
    db.commit()
    db.close()
    _routes, manual, _generated, _reports, _backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)
    (manual / "group_memory_case.json").write_text(json.dumps({
        "id": "group_memory_case",
        "suite": "rag_benchmark",
        "source_type": "group_memory",
        "case_type": "positive",
        "query": "灵歌 完结 番外",
        "filters": {"group_id": "group_1"},
        "expected": {"candidate_ids": ["group_memory:1:memory"], "hit_at": 5},
        "meta": {"origin": "manual"},
    }, ensure_ascii=False), encoding="utf-8")

    response = client.post(
        "/api/v1/admin/rag/benchmark/run",
        headers=_auth_header(),
        json={"provider_mode": "no_reranker_baseline", "include_generated": False},
    )

    assert response.status_code == 200
    candidate = response.json()["case_results"][0]["candidates"][0]
    assert candidate["metadata"]["first_seen"] == "2026-05-01T10:00:00"
    assert candidate["metadata"]["last_seen"] == "2026-05-27T21:00:00"
    assert candidate["metadata"]["updated_at"] == "2026-05-27T21:30:00"
    assert candidate["metadata"]["evidence_count"] == 2
    assert candidate["metadata"]["confidence"] == 0.8
    assert candidate["metadata"]["decay_score"] == 0.9
    assert candidate["metadata"]["inject_policy"] == "auto"
    assert candidate["metadata"]["evidence_log_ids"] == [11, 12]


def test_benchmark_case_results_redact_and_clip_candidate_metadata(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    _engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    _routes, manual, _generated, _reports, _backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)
    (manual / "memory_case.json").write_text(json.dumps({
        "id": "memory_case",
        "suite": "rag_benchmark",
        "source_type": "memory",
        "case_type": "positive",
        "query": "RAG benchmark readonly",
        "expected": {"candidate_ids": ["memory_digest:42:digest:level2"], "hit_at": 5},
        "meta": {"origin": "manual"},
    }, ensure_ascii=False), encoding="utf-8")

    from evals.rag_benchmark.schema import BenchmarkCandidate, BenchmarkResult

    def fake_adapter(_db, case, **_kwargs):
        return BenchmarkResult(
            case_id=case.id,
            source_type=case.source_type,
            candidate_ids=["memory_digest:42:digest:level2"],
            candidates=[
                BenchmarkCandidate(
                    candidate_id="memory_digest:42:digest:level2",
                    source_type="memory",
                    rank=1,
                    title="T" * 200,
                    text_preview="X" * 600,
                    metadata={
                        "authorization": "Bearer secret-token",
                        "nested": {"api_key": "secret-api-key"},
                        "notes": "Y" * 1000,
                    },
                )
            ],
            merged_candidates_count=1,
            reranker_candidates_count=1,
        )

    monkeypatch.setattr("api.admin.rag_benchmark_routes.run_case_with_adapter", fake_adapter)

    response = client.post(
        "/api/v1/admin/rag/benchmark/run",
        headers=_auth_header(),
        json={"provider_mode": "deterministic", "include_generated": False},
    )

    assert response.status_code == 200
    candidate = response.json()["case_results"][0]["candidates"][0]
    assert candidate["title"].endswith("...")
    assert candidate["text_preview"].endswith("...")
    assert candidate["metadata"]["authorization"] == "[REDACTED]"
    assert candidate["metadata"]["nested"]["api_key"] == "[REDACTED]"
    assert candidate["metadata"]["notes"].endswith("...")
    assert "secret-token" not in json.dumps(candidate, ensure_ascii=False)
    assert "secret-api-key" not in json.dumps(candidate, ensure_ascii=False)


def test_benchmark_missing_fts_returns_preflight_error_without_creating_table(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "missing_fts.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    routes, manual, _generated, _reports, _backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)
    (manual / "memory_case.json").write_text(json.dumps({
        "id": "memory_case",
        "source_type": "memory",
        "query": "missing fts",
        "expected": {"candidate_ids": ["memory_digest:1:digest:level2"]},
        "meta": {"origin": "manual"},
    }), encoding="utf-8")

    response = client.post(
        "/api/v1/admin/rag/benchmark/run",
        headers=_auth_header(),
        json={"provider_mode": "deterministic", "include_generated": False},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["preflight"]["ok"] is False
    assert "semantic_index_fts" in data["preflight"]["errors"][0]
    tables = {name for (name,) in engine.connect().execute(text(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    )).fetchall()}
    assert "semantic_index_fts" not in tables


def test_manual_case_save_update_and_delete_are_audited(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    _engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    _routes, manual, _generated, _reports, backups, trash = _configure_paths(monkeypatch, tmp_path, db_path)
    payload = {
        "case": {
            "id": "memory_manual_001",
            "suite": "rag_benchmark",
            "source_type": "memory",
            "case_type": "positive",
            "query": "RAG benchmark",
            "expected": {"candidate_ids": ["memory_digest:42:digest:level2"]},
            "meta": {"origin": "generated_exact"},
        }
    }

    created = client.put("/api/v1/admin/rag/benchmark/cases/memory_manual_001", headers=_auth_header(), json=payload)
    updated = client.put("/api/v1/admin/rag/benchmark/cases/memory_manual_001", headers=_auth_header(), json=payload)
    deleted = client.delete("/api/v1/admin/rag/benchmark/cases/memory_manual_001", headers=_auth_header())

    assert created.status_code == 200
    assert updated.status_code == 200
    assert deleted.status_code == 200
    assert not (manual / "memory_manual_001.json").exists()
    assert list(backups.glob("memory_manual_001.*.json"))
    assert list(trash.glob("memory_manual_001.*.json"))
    actions = [row.action for row in db_session.query(AdminAuditLog).order_by(AdminAuditLog.id).all()]
    assert actions == [
        "create_rag_benchmark_case",
        "update_rag_benchmark_case",
        "delete_rag_benchmark_case",
    ]
    trashed = json.loads(list(trash.glob("memory_manual_001.*.json"))[0].read_text(encoding="utf-8"))
    assert trashed["meta"]["origin"] == "manual"


def test_manual_case_detail_preserves_full_query_when_saved(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    _engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    _routes, manual, _generated, _reports, _backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)
    long_query = "RAG benchmark 长查询 " + ("上下文" * 160)
    (manual / "long_query_case.json").write_text(json.dumps({
        "id": "long_query_case",
        "suite": "rag_benchmark",
        "source_type": "memory",
        "case_type": "positive",
        "query": long_query,
        "expected": {"candidate_ids": ["memory_digest:42:digest:level2"]},
        "meta": {"origin": "manual"},
    }, ensure_ascii=False), encoding="utf-8")

    detail = client.get("/api/v1/admin/rag/benchmark/cases/long_query_case", headers=_auth_header())
    assert detail.status_code == 200
    case_payload = detail.json()["case"]
    assert case_payload["query"] == long_query
    assert case_payload["query_preview"] != long_query
    saved = client.put(
        "/api/v1/admin/rag/benchmark/cases/long_query_case",
        headers=_auth_header(),
        json={"case": case_payload},
    )

    assert saved.status_code == 200
    persisted = json.loads((manual / "long_query_case.json").read_text(encoding="utf-8"))
    assert persisted["query"] == long_query


def test_benchmark_sample_marks_fingerprint_and_run_skips_stale_generated_without_overwriting_latest(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    engine, db = _file_db(db_path)
    db.add(GroupMemory(
        id=1,
        group_id="group_1",
        memory_type="topic",
        content="RAG benchmark generated",
        content_hash="gm",
        evidence_log_ids_json="[1]",
        confidence=0.8,
        evidence_count=1,
        decay_score=0.9,
        status="active",
        inject_policy="auto",
    ))
    _seed_memory_case(db)
    db.commit()
    db.close()
    _routes, _manual, generated, reports, _backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "latest.json").write_text('{"marker":"keep-json"}', encoding="utf-8")
    (reports / "latest.md").write_text("keep markdown", encoding="utf-8")

    sampled = client.post(
        "/api/v1/admin/rag/benchmark/sample",
        headers=_auth_header(),
        json={"per_source": 1},
    )
    assert sampled.status_code == 200
    generated_text = "\n".join(path.read_text(encoding="utf-8") for path in generated.glob("*.jsonl"))
    assert "db_fingerprint" in generated_text
    assert "generator_version" in generated_text

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO semantic_index_items(id, source_type, source_id, source_sub_id, status, visibility, title, text) "
            "VALUES (999, 'memory_digest', '999', 'digest:level2', 'active', 'recall', 'new', 'new')"
        ))
    run = client.post(
        "/api/v1/admin/rag/benchmark/run",
        headers=_auth_header(),
        json={"include_manual": False, "include_generated": True, "provider_mode": "deterministic"},
    )

    assert run.status_code == 200
    data = run.json()
    assert data["stale_generated_cases"]
    assert data["metrics"]["overall"]["total_cases"] == 0
    assert data["ok"] is False
    assert "no_cases_executed" in data["warnings"]
    assert data["report_id"] == ""
    assert data["report_paths"] == {}
    assert (reports / "latest.json").read_text(encoding="utf-8") == '{"marker":"keep-json"}'
    assert (reports / "latest.md").read_text(encoding="utf-8") == "keep markdown"


def test_benchmark_run_clears_stale_lock(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    _engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    routes, manual, _generated, _reports, _backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)
    lock_path = tmp_path / "run.lock"
    monkeypatch.setattr(routes, "BENCHMARK_RUN_LOCK", lock_path)
    monkeypatch.setattr(routes, "RUN_LOCK_STALE_SECONDS", 1)
    lock_path.write_text(json.dumps({"pid": 999999, "started_at": time.time() - 120}), encoding="utf-8")
    (manual / "memory_case.json").write_text(json.dumps({
        "id": "memory_case",
        "suite": "rag_benchmark",
        "source_type": "memory",
        "case_type": "positive",
        "query": "RAG benchmark readonly",
        "expected": {"candidate_ids": ["memory_digest:42:digest:level2"], "hit_at": 5},
        "meta": {"origin": "manual"},
    }, ensure_ascii=False), encoding="utf-8")

    response = client.post(
        "/api/v1/admin/rag/benchmark/run",
        headers=_auth_header(),
        json={"provider_mode": "deterministic", "include_generated": False},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert not lock_path.exists()


def test_benchmark_rejects_unsafe_case_id(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    _engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    _configure_paths(monkeypatch, tmp_path, db_path)

    response = client.put(
        "/api/v1/admin/rag/benchmark/cases/.hidden",
        headers=_auth_header(),
        json={"case": {"id": ".hidden", "source_type": "memory", "query": "x"}},
    )

    assert response.status_code == 400
