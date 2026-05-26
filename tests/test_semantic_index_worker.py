from datetime import datetime, timedelta

from sqlalchemy import text

from core.database import SemanticIndexItem, SemanticIndexJob
from core.semantic.adapters import SemanticChunk
from core.semantic.schema import ensure_semantic_schema


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


def test_embedding_failure_marks_done_with_warning(db_session):
    from core.semantic.jobs import claim_next_job, enqueue_index_job
    from workers.semantic_index_worker import process_semantic_index_job

    class BrokenEmbeddingProvider:
        def embed(self, _texts):
            raise RuntimeError("embedding down")

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
    assert "embedding down" in result.error
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

    result = process_semantic_index_job(db_session, job, chunk_loader=lambda _job: [])

    assert result.status == "done"
    row = db_session.query(SemanticIndexItem).one()
    assert row.status == "deleted"
    assert row.deleted_at is not None
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
        locked_at=datetime.now() - timedelta(minutes=10),
    ))
    db_session.commit()

    recovered = recover_timed_out_jobs(db_session, timeout_seconds=60)

    job = db_session.query(SemanticIndexJob).one()
    assert recovered == 1
    assert job.status == "pending"
    assert job.locked_by == ""
    assert job.locked_at is None


def test_item_and_fts_write_are_same_transaction(db_session):
    from workers.semantic_index_worker import process_semantic_index_job

    ensure_semantic_schema(db_session.bind)
    job = SemanticIndexJob(
        source_type="memory_digest",
        source_id="11",
        index_version="fake:v1:v1",
        status="running",
        locked_by="worker-a",
        locked_at=datetime.now(),
    )
    db_session.add(job)
    db_session.commit()

    def broken_fts_execute(statement, *args, **kwargs):
        sql = str(statement)
        if "semantic_index_fts" in sql and "INSERT INTO" in sql:
            raise RuntimeError("fts insert failed")
        return original_execute(statement, *args, **kwargs)

    original_execute = db_session.execute
    db_session.execute = broken_fts_execute

    result = process_semantic_index_job(db_session, job, chunk_loader=lambda _job: [_chunk("事务测试")])

    assert result.status == "failed"
    assert "fts insert failed" in result.error
    assert db_session.query(SemanticIndexItem).count() == 0
