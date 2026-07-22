import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from core.database import SemanticIndexItem, SemanticIndexJob
from core.semantic.adapters import SemanticChunk
from core.semantic.schema import ensure_semantic_schema


def _local_now() -> datetime:
    # SemanticIndexJob DateTime fixture 保持 naive 本地墙钟时间语义。
    return datetime.now()  # noqa: DTZ005


def _chunk(text_value: str = "8000 端口被占用") -> SemanticChunk:
    return SemanticChunk(
        source_type="memory_digest",
        source_id="11",
        source_sub_id="card:0",
        title="端口冲突",
        text=text_value,
        lexical_text=f"端口冲突 {text_value} uvicorn",
        embedding_text=f"端口冲突 {text_value} uvicorn",
        metadata={"user_id": "u1", "session_id": "s1"},
    )


def test_claim_job_is_atomic(db_session):
    from core.semantic.jobs import claim_next_job, enqueue_index_job

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(db_session, source_type="memory_digest", source_id="11", index_version="fake:v1:v1")

    first = claim_next_job(db_session, worker_id="worker-a")
    second = claim_next_job(db_session, worker_id="worker-b")

    assert first is not None
    assert first.status == "running"
    assert first.locked_by == "worker-a"
    assert second is None


def test_claim_creates_lease_and_retryable_failure_returns_pending(db_session):
    from core.semantic.jobs import claim_next_job, enqueue_index_job, fail_job

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
    )
    now = datetime(2026, 7, 17, 12, 0, 0)  # noqa: DTZ001

    claimed = claim_next_job(
        db_session,
        worker_id="worker-a",
        lease_seconds=60,
        now=now,
    )

    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.locked_by == "worker-a"
    assert len(claimed.lease_token) == 64
    assert claimed.lease_expires_at == now + timedelta(seconds=60)
    assert claimed.attempt_count == 1

    failed = fail_job(
        db_session,
        job_id=claimed.id,
        lease_token=claimed.lease_token,
        error="temporary embedding failure",
        retryable=True,
        now=now + timedelta(seconds=1),
    )

    assert failed is not None
    assert failed.status == "pending"
    assert failed.retry_count == 1
    assert failed.next_retry_at is not None
    assert failed.finished_at is None
    assert failed.locked_by == ""
    assert failed.lease_token == ""
    assert failed.lease_expires_at is None


def test_retry_budget_allows_initial_attempt_plus_three_automatic_retries(
    db_session,
):
    from core.semantic.jobs import claim_next_job, enqueue_index_job, fail_job

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="retry-budget",
        index_version="fake:v1:v1",
        max_retry=3,
    )
    started_at = datetime(2026, 7, 17, 12, 0, 0)  # noqa: DTZ001

    for retry_count in range(1, 4):
        attempt_at = started_at + timedelta(minutes=retry_count * 10)
        claimed = claim_next_job(
            db_session,
            worker_id=f"worker-{retry_count}",
            now=attempt_at,
        )
        failed = fail_job(
            db_session,
            job_id=claimed.id,
            lease_token=claimed.lease_token,
            error="temporary provider failure",
            retryable=True,
            now=attempt_at + timedelta(seconds=1),
        )
        assert failed.status == "pending"
        assert failed.retry_count == retry_count

    final_attempt_at = started_at + timedelta(minutes=40)
    claimed = claim_next_job(
        db_session,
        worker_id="worker-final",
        now=final_attempt_at,
    )
    failed = fail_job(
        db_session,
        job_id=claimed.id,
        lease_token=claimed.lease_token,
        error="temporary provider failure",
        retryable=True,
        now=final_attempt_at + timedelta(seconds=1),
    )

    assert failed.status == "failed"
    assert failed.retry_count == 3
    assert failed.attempt_count == 4


def test_permanent_worker_error_fails_without_consuming_retry_budget(db_session):
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="permanent-error",
        index_version="fake:v1:v1",
    )
    job = claim_next_job(db_session, worker_id="worker-permanent")

    result = process_semantic_index_job(
        db_session,
        job,
        chunk_loader=lambda _job: (_ for _ in ()).throw(
            ValueError("semantic_reconcile_identity_invalid")
        ),
    )

    assert result is not None
    assert result.status == "failed"
    assert result.retry_count == 0
    assert result.next_retry_at is None


def test_permanent_worker_error_never_persists_value_error_text(db_session):
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    secret_sentinel = "api_key_SECRET123456"
    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="permanent-secret-error",
        index_version="fake:v1:v1",
    )
    job = claim_next_job(db_session, worker_id="worker-permanent-secret")

    result = process_semantic_index_job(
        db_session,
        job,
        chunk_loader=lambda _job: (_ for _ in ()).throw(
            ValueError(secret_sentinel)
        ),
    )

    assert result is not None
    assert result.status == "failed"
    assert result.error == "semantic_index_permanent_error:ValueError"
    assert secret_sentinel not in result.error


def test_manual_retry_failure_grants_only_one_attempt(db_session):
    from core.semantic.jobs import (
        claim_next_job,
        enqueue_index_job,
        fail_job,
        retry_semantic_index_job,
    )

    ensure_semantic_schema(db_session.bind)
    base = datetime(2026, 7, 17, 12, 0, 0)  # noqa: DTZ001
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="manual-failure",
        index_version="fake:v1:v1",
    )
    initial = claim_next_job(db_session, worker_id="worker-initial", now=base)
    terminal = fail_job(
        db_session,
        job_id=initial.id,
        lease_token=initial.lease_token,
        error="invalid source",
        retryable=False,
        now=base + timedelta(seconds=1),
    )
    retried = retry_semantic_index_job(
        db_session,
        job_id=terminal.id,
        expected_status="failed",
        expected_updated_at=terminal.updated_at,
        reason="人工确认后重试一次",
        now=base + timedelta(minutes=1),
        commit=True,
    )
    claimed = claim_next_job(
        db_session,
        worker_id="worker-manual",
        now=base + timedelta(minutes=2),
    )
    failed = fail_job(
        db_session,
        job_id=claimed.id,
        lease_token=claimed.lease_token,
        error="temporary but manual attempt exhausted",
        retryable=True,
        now=base + timedelta(minutes=2, seconds=1),
    )

    assert retried.manual_retry_count == 1
    assert failed.status == "failed"
    assert failed.manual_retry_count == 1
    assert failed.retry_count == 0
    assert failed.next_retry_at is None


def test_manual_retry_timeout_does_not_return_to_pending(db_session):
    from core.semantic.jobs import (
        claim_next_job,
        enqueue_index_job,
        fail_job,
        recover_timed_out_jobs,
        retry_semantic_index_job,
    )

    ensure_semantic_schema(db_session.bind)
    base = datetime(2026, 7, 17, 12, 0, 0)  # noqa: DTZ001
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="manual-timeout",
        index_version="fake:v1:v1",
    )
    initial = claim_next_job(db_session, worker_id="worker-initial", now=base)
    terminal = fail_job(
        db_session,
        job_id=initial.id,
        lease_token=initial.lease_token,
        error="invalid source",
        retryable=False,
        now=base + timedelta(seconds=1),
    )
    retry_semantic_index_job(
        db_session,
        job_id=terminal.id,
        expected_status="failed",
        expected_updated_at=terminal.updated_at,
        reason="人工确认后重试一次",
        now=base + timedelta(minutes=1),
        commit=True,
    )
    claimed = claim_next_job(
        db_session,
        worker_id="worker-manual",
        lease_seconds=60,
        now=base + timedelta(minutes=2),
    )

    recovered = recover_timed_out_jobs(
        db_session,
        timeout_seconds=60,
        now=base + timedelta(minutes=3, seconds=1),
    )
    current = db_session.get(SemanticIndexJob, claimed.id)

    assert recovered == 1
    assert current.status == "failed"
    assert current.manual_retry_count == 1
    assert current.retry_count == 0
    assert current.next_retry_at is None


def test_stale_lease_cannot_heartbeat_finish_or_fail_after_reclaim(db_session):
    from core.semantic.jobs import (
        claim_next_job,
        enqueue_index_job,
        fail_job,
        finish_job,
        heartbeat_job,
        recover_timed_out_jobs,
    )

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
    )
    started_at = datetime(2026, 7, 17, 12, 0, 0)  # noqa: DTZ001
    first = claim_next_job(
        db_session,
        worker_id="worker-a",
        lease_seconds=60,
        now=started_at,
    )
    first_token = first.lease_token
    reclaimed_at = started_at + timedelta(seconds=61)

    assert recover_timed_out_jobs(
        db_session,
        timeout_seconds=60,
        now=reclaimed_at,
    ) == 1
    second = claim_next_job(
        db_session,
        worker_id="worker-b",
        lease_seconds=60,
        now=reclaimed_at,
    )
    second_token = second.lease_token

    assert heartbeat_job(
        db_session,
        job_id=first.id,
        lease_token=first_token,
        lease_seconds=60,
        now=reclaimed_at + timedelta(seconds=1),
    ) is None
    assert finish_job(
        db_session,
        job_id=first.id,
        lease_token=first_token,
        status="done",
        now=reclaimed_at + timedelta(seconds=1),
    ) is None
    assert fail_job(
        db_session,
        job_id=first.id,
        lease_token=first_token,
        error="late worker failure",
        retryable=True,
        now=reclaimed_at + timedelta(seconds=1),
    ) is None

    db_session.expire_all()
    current = db_session.get(SemanticIndexJob, second.id)
    assert current.status == "running"
    assert current.locked_by == "worker-b"
    assert current.lease_token == second_token
    assert current.attempt_count == 2
    assert current.retry_count == 1


def test_embedding_failure_marks_done_with_warning(db_session):
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    class BrokenEmbeddingProvider:
        def embed(self, _texts):
            raise RuntimeError("embedding down: api-key-must-not-leak")

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(db_session, source_type="memory_digest", source_id="11", index_version="fake:v1:v1")
    job = claim_next_job(db_session, worker_id="worker-a")

    result = process_semantic_index_job(
        db_session,
        job,
        chunk_loader=lambda _job: [_chunk()],
        embedding_provider=BrokenEmbeddingProvider(),
    )

    assert result.status == "done_with_warning"
    assert result.error == "embedding_provider_error:RuntimeError"
    assert "api-key-must-not-leak" not in result.error
    row = db_session.query(SemanticIndexItem).one()
    assert row.embedding_status == "failed"
    assert db_session.execute(text("SELECT COUNT(*) FROM semantic_index_fts")).scalar() == 1


def test_embedding_bytes_are_json_parseable(db_session):
    from core.semantic.retriever import parse_embedding
    from workers.semantic_index_worker import _embedding_bytes_by_sub_id

    class FloatLike:
        def __init__(self, value):
            self.value = value

        def __float__(self):
            return float(self.value)

        def __repr__(self):
            return f"np.float32({self.value})"

    class NumpyLikeEmbeddingProvider:
        def embed(self, _texts):
            return [[FloatLike(0.25), FloatLike(0.75)]]

    embeddings, error = _embedding_bytes_by_sub_id([_chunk()], NumpyLikeEmbeddingProvider())

    assert error == ""
    assert parse_embedding(embeddings["card:0"]) == [0.25, 0.75]


def test_short_embedding_response_marks_all_chunks_failed(db_session):
    from dataclasses import replace

    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    class ShortEmbeddingProvider:
        def embed(self, _texts):
            return [[0.25, 0.75]]

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
    )
    job = claim_next_job(db_session, worker_id="worker-short-embedding")
    chunks = [
        _chunk("第一张卡片"),
        replace(_chunk("第二张卡片"), source_sub_id="card:1"),
    ]

    result = process_semantic_index_job(
        db_session,
        job,
        chunk_loader=lambda _job: chunks,
        embedding_provider=ShortEmbeddingProvider(),
    )

    assert result is not None
    assert result.status == "done_with_warning"
    assert result.error == "embedding_vector_count_mismatch"
    rows = db_session.query(SemanticIndexItem).order_by(
        SemanticIndexItem.source_sub_id.asc(),
    ).all()
    assert len(rows) == 2
    assert {row.embedding_status for row in rows} == {"failed"}


def test_lazy_embedding_error_is_sanitized_and_degraded(db_session):
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    secret_sentinel = "Authorization: Bearer secret-token-must-not-leak"

    class LazyBrokenEmbeddingProvider:
        def embed(self, _texts):
            def vectors():
                raise RuntimeError(secret_sentinel)
                yield [0.1, 0.2]

            return vectors()

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
    )
    job = claim_next_job(db_session, worker_id="worker-lazy-embedding")

    result = process_semantic_index_job(
        db_session,
        job,
        chunk_loader=lambda _job: [_chunk()],
        embedding_provider=LazyBrokenEmbeddingProvider(),
    )

    assert result is not None
    assert result.status == "done_with_warning"
    assert result.error == "embedding_provider_error:RuntimeError"
    assert secret_sentinel not in result.error
    assert db_session.query(SemanticIndexItem).one().embedding_status == "failed"


def test_embedding_float_conversion_error_is_sanitized(db_session):
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    secret_sentinel = "api-key-in-float-conversion-must-not-leak"

    class BrokenFloat:
        def __float__(self):
            raise RuntimeError(secret_sentinel)

    class BrokenFloatEmbeddingProvider:
        def embed(self, _texts):
            return [[BrokenFloat()]]

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
    )
    job = claim_next_job(db_session, worker_id="worker-float-embedding")

    result = process_semantic_index_job(
        db_session,
        job,
        chunk_loader=lambda _job: [_chunk()],
        embedding_provider=BrokenFloatEmbeddingProvider(),
    )

    assert result is not None
    assert result.status == "done_with_warning"
    assert result.error == "embedding_provider_error:RuntimeError"
    assert secret_sentinel not in result.error
    assert db_session.query(SemanticIndexItem).one().embedding_status == "failed"


@pytest.mark.parametrize(
    ("vector", "expected_error"),
    [
        (b"not-json-vector", "embedding_vector_invalid"),
        ([float("nan"), 0.5], "embedding_vector_non_finite"),
        ([float("inf"), 0.5], "embedding_vector_non_finite"),
        ([0.0, 0.0], "embedding_vector_zero_norm"),
    ],
)
def test_invalid_embedding_vector_is_degraded(
    db_session,
    vector,
    expected_error,
):
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    class InvalidEmbeddingProvider:
        def embed(self, _texts):
            return [vector]

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
    )
    job = claim_next_job(db_session, worker_id="worker-invalid-embedding")

    result = process_semantic_index_job(
        db_session,
        job,
        chunk_loader=lambda _job: [_chunk()],
        embedding_provider=InvalidEmbeddingProvider(),
    )

    assert result is not None
    assert result.status == "done_with_warning"
    assert result.error == expected_error
    row = db_session.query(SemanticIndexItem).one()
    assert row.embedding_status == "failed"
    assert row.embedding is None


def test_run_once_claims_and_processes_next_job(db_session):
    from core.semantic.jobs import enqueue_index_job
    from workers.semantic_index_worker import run_once

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(db_session, source_type="memory_digest", source_id="11", index_version="fake:v1:v1")

    processed = run_once(
        db=db_session,
        worker_id="worker-a",
        chunk_loader=lambda _job: [_chunk()],
    )

    assert processed is True
    job = db_session.query(SemanticIndexJob).one()
    assert job.status == "done"
    assert db_session.query(SemanticIndexItem).count() == 1


def test_run_once_does_not_claim_job_when_semantic_index_is_disabled(
    db_session,
    monkeypatch,
):
    from core.semantic.jobs import enqueue_index_job
    from workers.semantic_index_worker import run_once

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
    )
    monkeypatch.setenv("SEMANTIC_INDEX_ENABLED", "0")

    processed = run_once(db=db_session, worker_id="worker-a")

    assert processed is False
    job = db_session.query(SemanticIndexJob).one()
    assert job.status == "pending"
    assert job.locked_by == ""


def test_deleted_source_marks_index_deleted(db_session):
    from core.semantic.indexer import upsert_semantic_chunks
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    ensure_semantic_schema(db_session.bind)
    upsert_semantic_chunks(db_session, [_chunk()], index_version="fake:v1:v1")
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        job_type="delete",
        index_version="fake:v1:v1",
    )
    job = claim_next_job(db_session, worker_id="worker-a")

    def fail_loader(_job):
        raise AssertionError("delete job 不应读取业务源")

    class FailEmbeddingProvider:
        def embed(self, _texts):
            raise AssertionError("delete job 不应调用 embedding provider")

    result = process_semantic_index_job(
        db_session,
        job,
        chunk_loader=fail_loader,
        embedding_provider=FailEmbeddingProvider(),
    )

    assert result.status == "done"
    row = db_session.query(SemanticIndexItem).one()
    assert row.status == "deleted"
    assert row.deleted_at is not None
    assert db_session.execute(text("SELECT COUNT(*) FROM semantic_index_fts")).scalar() == 0


def test_worker_stops_before_index_write_when_heartbeat_loses_lease(
    db_session,
    monkeypatch,
):
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers import semantic_index_worker as worker

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
    )
    job = claim_next_job(db_session, worker_id="worker-a")
    monkeypatch.setattr(worker, "heartbeat_job", lambda *_args, **_kwargs: None)

    result = worker.process_semantic_index_job(
        db_session,
        job,
        chunk_loader=lambda _job: [_chunk("失租不得写入")],
    )

    assert result is None
    assert db_session.query(SemanticIndexItem).count() == 0
    assert db_session.execute(text("SELECT COUNT(*) FROM semantic_index_fts")).scalar() == 0


def test_running_job_timeout_recovers_to_pending(db_session):
    from core.semantic.jobs import recover_timed_out_jobs

    ensure_semantic_schema(db_session.bind)
    db_session.add(SemanticIndexJob(
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
        status="running",
        locked_by="old-worker",
        locked_at=_local_now() - timedelta(minutes=10),
    ))
    db_session.commit()

    recovered = recover_timed_out_jobs(db_session, timeout_seconds=60)

    job = db_session.query(SemanticIndexJob).one()
    assert recovered == 1
    assert job.status == "pending"
    assert job.locked_by == ""
    assert job.locked_at is None


def test_item_and_fts_write_are_same_transaction(db_session):
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    ensure_semantic_schema(db_session.bind)
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
    )
    job = claim_next_job(db_session, worker_id="worker-a")

    def broken_fts_execute(statement, *args, **kwargs):
        sql = str(statement)
        if "semantic_index_fts" in sql and "INSERT INTO" in sql:
            raise RuntimeError("fts insert failed")
        return original_execute(statement, *args, **kwargs)

    original_execute = db_session.execute
    db_session.execute = broken_fts_execute

    result = process_semantic_index_job(db_session, job, chunk_loader=lambda _job: [_chunk("事务测试")])

    assert result.status == "pending"
    assert result.error == "semantic_index_worker_error:RuntimeError"
    assert db_session.query(SemanticIndexItem).count() == 0


def test_reconcile_replaces_stale_sub_ids_and_records_revision(db_session):
    from core.semantic.adapters import SemanticChunk
    from core.semantic.indexer import upsert_semantic_chunks
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    ensure_semantic_schema(db_session.bind)
    old_chunks = [
        _chunk("保留但更新"),
        SemanticChunk(
            source_type="memory_digest",
            source_id="11",
            source_sub_id="card:stale",
            title="旧卡片",
            text="应被软删除",
            lexical_text="旧卡片 应被软删除",
            embedding_text="旧卡片 应被软删除",
        ),
    ]
    upsert_semantic_chunks(
        db_session,
        old_chunks,
        index_version="legacy:v1:v1",
        source_revision="revision-old",
    )
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
        source_revision="revision-new",
    )
    job = claim_next_job(db_session, worker_id="worker-a")

    result = process_semantic_index_job(
        db_session,
        job,
        chunk_loader=lambda _job: [_chunk("已更新")],
    )

    assert result.status == "done"
    rows = db_session.query(SemanticIndexItem).all()
    active = [row for row in rows if row.status == "active"]
    legacy = [row for row in rows if row.index_version == "legacy:v1:v1"]
    assert len(active) == 1
    assert active[0].source_sub_id == "card:0"
    assert active[0].text == "已更新"
    assert active[0].source_revision == "revision-new"
    assert active[0].index_version == "fake:v1:v1"
    assert len(legacy) == 2
    assert all(row.status == "deleted" for row in legacy)
    assert all(row.deleted_at is not None for row in legacy)
    assert db_session.execute(text(
        "SELECT COUNT(*) FROM semantic_index_fts"
    )).scalar_one() == 1


def test_reconcile_rolls_back_items_fts_and_job_on_fts_failure(db_session):
    from core.semantic.indexer import reconcile_semantic_source, upsert_semantic_chunks
    from core.semantic.jobs import claim_next_job, enqueue_index_job, semantic_job_lease

    ensure_semantic_schema(db_session.bind)
    upsert_semantic_chunks(
        db_session,
        [_chunk("事务前旧值")],
        index_version="fake:v1:v1",
        source_revision="revision-old",
    )
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
        source_revision="revision-new",
    )
    job = claim_next_job(db_session, worker_id="worker-a")
    lease = semantic_job_lease(job)
    original_execute = db_session.execute

    def broken_fts_execute(statement, *args, **kwargs):
        sql = str(statement)
        if "semantic_index_fts" in sql and "INSERT INTO" in sql:
            raise RuntimeError("reconcile fts insert failed")
        return original_execute(statement, *args, **kwargs)

    db_session.execute = broken_fts_execute
    with pytest.raises(RuntimeError, match="reconcile fts insert failed"):
        reconcile_semantic_source(
            db_session,
            source_type="memory_digest",
            source_id="11",
            source_revision="revision-new",
            index_version="fake:v1:v1",
            expected_chunks=[_chunk("事务中新值")],
            delete_source_ids=(),
            lease=lease,
        )
    db_session.rollback()

    row = db_session.query(SemanticIndexItem).one()
    current_job = db_session.get(SemanticIndexJob, job.id)
    assert row.status == "active"
    assert row.text == "事务前旧值"
    assert row.source_revision == "revision-old"
    assert current_job.status == "running"
    assert current_job.lease_token == lease.lease_token
    assert db_session.execute(text(
        "SELECT text FROM semantic_index_fts"
    )).scalar_one() == "事务前旧值"


def test_default_worker_loader_aggregates_memory_digest_logical_source(db_session):
    import json

    from core.database import MemoryDigest
    from core.semantic.adapters import memory_digest_source_revision
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import _default_chunk_loader, process_semantic_index_job

    ensure_semantic_schema(db_session.bind)
    shared = {
        "schema_version": 2,
        "status": "active",
        "generator": "llm",
        "llm_status": "success",
        "quality": {"score": 0.9, "issues": []},
        "source_id": "digest-logical-source",
    }
    rows = [
        MemoryDigest(
            user_id="u1",
            session_id="s1",
            digest_date="2026-07-17",
            level=0,
            content="详细摘要",
            meta_json=json.dumps({**shared, "summary_type": "detailed_digest"}),
        ),
        MemoryDigest(
            user_id="u1",
            session_id="s1",
            digest_date="2026-07-17",
            level=1,
            content="预览摘要",
            meta_json=json.dumps({**shared, "summary_type": "preview_digest"}),
        ),
        MemoryDigest(
            user_id="u1",
            session_id="s1",
            digest_date="2026-07-17",
            level=2,
            content="召回卡片",
            meta_json=json.dumps({
                **shared,
                "summary_type": "recall_card",
                "recall_cards": [{
                    "type": "fact",
                    "text": "语义 worker 必须聚合同一逻辑源。",
                    "evidence_log_ids": [1, 2],
                }],
            }, ensure_ascii=False),
        ),
    ]
    db_session.add_all(rows)
    db_session.flush()
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="digest-logical-source",
        source_revision=memory_digest_source_revision(rows),
        meta={
            "contract_version": 2,
            "document_ids": [row.id for row in rows],
            "delete_source_ids": [str(row.id) for row in rows],
        },
    )
    job = claim_next_job(db_session, worker_id="worker-a")

    result = process_semantic_index_job(
        db_session,
        job,
        chunk_loader=_default_chunk_loader(db_session),
    )

    assert result.status == "done"
    items = db_session.query(SemanticIndexItem).order_by(SemanticIndexItem.source_sub_id).all()
    assert len(items) == 3
    assert {item.source_id for item in items} == {"digest-logical-source"}
    assert {item.visibility for item in items} == {"expand_only", "recall"}
    assert db_session.execute(text(
        "SELECT COUNT(*) FROM semantic_index_fts"
    )).scalar_one() == 3


def test_default_worker_loader_does_not_index_archived_group_memory(db_session):
    from core.database import GroupMemory
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import (
        _default_chunk_loader,
        process_semantic_index_job,
    )

    ensure_semantic_schema(db_session.bind)
    row = GroupMemory(
        group_id="group_42",
        memory_type="style",
        content="已归档群记忆不得重新进入索引",
        content_hash="archived-group-memory",
        status="archived",
    )
    db_session.add(row)
    db_session.flush()
    enqueue_index_job(
        db_session,
        source_type="group_memory",
        source_id=str(row.id),
        source_revision="archived-revision",
    )
    job = claim_next_job(db_session, worker_id="worker-a")

    result = process_semantic_index_job(
        db_session,
        job,
        chunk_loader=_default_chunk_loader(db_session),
    )

    assert result.status == "done"
    assert db_session.query(SemanticIndexItem).count() == 0


def test_older_source_revision_job_is_superseded_without_index_writes(db_session):
    from core.semantic.indexer import upsert_semantic_chunks
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    ensure_semantic_schema(db_session.bind)
    upsert_semantic_chunks(
        db_session,
        [_chunk("当前索引不得被旧任务覆盖")],
        index_version="fake:v1:v1",
        source_revision="revision-current",
    )
    old_job = enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
        source_revision="revision-old",
    )
    claimed_old = claim_next_job(db_session, worker_id="worker-old")
    new_job = enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
        source_revision="revision-new",
    )

    result = process_semantic_index_job(
        db_session,
        claimed_old,
        chunk_loader=lambda _job: [_chunk("旧任务输出")],
    )

    assert result.status == "superseded"
    db_session.refresh(old_job)
    db_session.refresh(new_job)
    row = db_session.query(SemanticIndexItem).one()
    assert old_job.status == "superseded"
    assert new_job.status == "pending"
    assert row.status == "active"
    assert row.text == "当前索引不得被旧任务覆盖"
    assert row.source_revision == "revision-current"
    assert db_session.execute(text(
        "SELECT text FROM semantic_index_fts"
    )).scalar_one() == "当前索引不得被旧任务覆盖"


def test_later_backfill_job_cannot_supersede_or_overwrite_business_revision(
    db_session,
):
    from core.semantic.indexer import upsert_semantic_chunks
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    ensure_semantic_schema(db_session.bind)
    business_job = enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        job_type="replace",
        index_version="fake:v1:v1",
        source_revision="revision-business-new",
        meta={"job_origin": "business"},
    )
    backfill_job = enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="11",
        job_type="replace",
        index_version="fake:v1:v1",
        source_revision="revision-backfill-old",
        meta={
            "job_origin": "backfill",
            "backfill_category": "stale",
        },
    )

    claimed_business = claim_next_job(db_session, worker_id="worker-business")
    business_result = process_semantic_index_job(
        db_session,
        claimed_business,
        chunk_loader=lambda _job: [_chunk("新版业务内容")],
    )

    assert business_result is not None
    assert business_result.status == "done"
    upsert_semantic_chunks(
        db_session,
        [_chunk("其他版本也不得被旧 backfill 删除")],
        index_version="other:v1",
        source_revision="revision-business-new",
    )
    claimed_backfill = claim_next_job(db_session, worker_id="worker-backfill")
    backfill_result = process_semantic_index_job(
        db_session,
        claimed_backfill,
        chunk_loader=lambda _job: [_chunk("旧 snapshot 内容")],
    )

    db_session.refresh(business_job)
    db_session.refresh(backfill_job)
    assert backfill_result is not None
    assert business_job.status == "done"
    assert backfill_job.status == "superseded"
    assert {
        (row.index_version, row.text, row.status)
        for row in db_session.query(SemanticIndexItem).all()
    } == {
        ("fake:v1:v1", "新版业务内容", "active"),
        ("other:v1", "其他版本也不得被旧 backfill 删除", "active"),
    }
    fts_texts = {
        row[0]
        for row in db_session.execute(text(
            "SELECT text FROM semantic_index_fts"
        )).all()
    }
    assert fts_texts == {
        "新版业务内容",
        "其他版本也不得被旧 backfill 删除",
    }


def test_backfill_orphan_delete_uses_business_head_observed_at_scan(db_session):
    from core.semantic.backfill import enqueue_semantic_index_backfill
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    ensure_semantic_schema(db_session.bind)
    source_chunk = SemanticChunk(
        source_type="group_memory",
        source_id="1",
        source_sub_id="memory",
        title="已经删除的业务源",
        text="残留索引必须由正式 backfill 清理",
        lexical_text="残留索引 backfill 清理",
        embedding_text="残留索引 backfill 清理",
    )
    business_job = enqueue_index_job(
        db_session,
        source_type="group_memory",
        source_id="1",
        job_type="replace",
        index_version="target:v2",
        source_revision="business-revision-before-delete",
        meta={"job_origin": "business"},
    )
    claimed_business = claim_next_job(
        db_session,
        worker_id="business-worker",
    )
    business_result = process_semantic_index_job(
        db_session,
        claimed_business,
        chunk_loader=lambda _job: [source_chunk],
    )
    assert business_result is not None
    assert business_result.status == "done"

    cursor = ""
    enqueued = 0
    while True:
        page = enqueue_semantic_index_backfill(
            db_session,
            source_type="group_memory",
            limit=10,
            cursor=cursor,
            index_version="target:v2",
        )
        enqueued += page["enqueued"]
        if page["done"]:
            break
        cursor = page["next_cursor"]
    db_session.commit()
    assert enqueued == 1

    backfill_job = db_session.query(SemanticIndexJob).filter(
        SemanticIndexJob.id != business_job.id,
    ).one()
    backfill_meta = json.loads(backfill_job.meta_json)
    assert backfill_meta["delete_item_ids"] == backfill_meta["document_ids"]
    claimed_backfill = claim_next_job(
        db_session,
        worker_id="backfill-worker",
    )
    backfill_result = process_semantic_index_job(
        db_session,
        claimed_backfill,
        chunk_loader=lambda _job: [],
    )

    assert backfill_result is not None
    assert backfill_job.job_type == "delete"
    assert backfill_result.status == "done"
    assert db_session.query(SemanticIndexItem).filter(
        SemanticIndexItem.source_type == "group_memory",
        SemanticIndexItem.source_id == "1",
        SemanticIndexItem.status == "active",
    ).count() == 0
    assert db_session.execute(text(
        "SELECT COUNT(*) FROM semantic_index_fts"
    )).scalar_one() == 0


def test_legacy_empty_source_orphan_delete_uses_exact_item_ids(db_session):
    from core.semantic.indexer import upsert_semantic_chunks
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    ensure_semantic_schema(db_session.bind)
    chunks = [
        SemanticChunk(
            source_type="memory_digest",
            source_id="",
            source_sub_id=f"legacy:{index}",
            title="历史孤儿索引",
            text=f"历史孤儿索引 {index}",
            lexical_text=f"历史孤儿索引 {index}",
            embedding_text=f"历史孤儿索引 {index}",
        )
        for index in range(3)
    ]
    rows = upsert_semantic_chunks(
        db_session,
        chunks,
        index_version="legacy:v1",
        source_revision="legacy-empty-source",
    )
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="",
        job_type="delete",
        index_version="legacy:v1",
        source_revision="orphan-legacy-empty-source",
        meta={
            "job_origin": "backfill",
            "backfill_category": "orphan",
            "document_ids": [rows[0].id, rows[1].id],
        },
    )
    claimed = claim_next_job(db_session, worker_id="legacy-orphan-worker")

    result = process_semantic_index_job(
        db_session,
        claimed,
        chunk_loader=lambda _job: (_ for _ in ()).throw(
            AssertionError("delete 任务不得加载业务正文")
        ),
        embedding_provider=lambda: (_ for _ in ()).throw(
            AssertionError("delete 任务不得调用 embedding")
        ),
    )

    assert result is not None
    assert result.status == "done"
    db_session.expire_all()
    assert {
        row.id: row.status
        for row in db_session.query(SemanticIndexItem).order_by(SemanticIndexItem.id).all()
    } == {
        rows[0].id: "deleted",
        rows[1].id: "deleted",
        rows[2].id: "active",
    }
    assert db_session.execute(text(
        "SELECT rowid FROM semantic_index_fts ORDER BY rowid"
    )).scalars().all() == [rows[2].id]


def test_business_delete_cannot_use_empty_source_id(db_session):
    from core.semantic.indexer import upsert_semantic_chunks
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    ensure_semantic_schema(db_session.bind)
    row = upsert_semantic_chunks(
        db_session,
        [SemanticChunk(
            source_type="memory_digest",
            source_id="",
            source_sub_id="business-empty-source",
            title="不得由业务任务删除",
            text="不得由业务任务删除",
            lexical_text="不得由业务任务删除",
            embedding_text="不得由业务任务删除",
        )],
        index_version="legacy:v1",
        source_revision="business-empty-source",
    )[0]
    enqueue_index_job(
        db_session,
        source_type="memory_digest",
        source_id="",
        job_type="delete",
        index_version="legacy:v1",
        source_revision="business-delete-empty-source",
        meta={"delete_item_ids": [row.id]},
    )
    claimed = claim_next_job(db_session, worker_id="business-delete-worker")

    result = process_semantic_index_job(
        db_session,
        claimed,
        chunk_loader=lambda _job: [],
    )

    assert result is not None
    assert result.status == "failed"
    assert result.error == "semantic_index_permanent_error:ValueError"
    db_session.refresh(row)
    assert row.status == "active"
    assert db_session.execute(text(
        "SELECT COUNT(*) FROM semantic_index_fts"
    )).scalar_one() == 1
