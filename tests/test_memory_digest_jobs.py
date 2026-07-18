import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from tests.async_helpers import run_async


def _log(
    *,
    content: str,
    log_id: int | None = None,
    user_id: str = "digest-user",
    session_id: str = "digest-session",
    sender_name: str = "测试用户",
):
    from core.database import ChatLog

    return ChatLog(
        id=log_id,
        user_id=user_id,
        session_id=session_id,
        role="user",
        sender_name=sender_name,
        content=content,
        created_at=datetime(2026, 7, 18, 12, 0, 0),
    )


def _success_payload(evidence_ids: list[int], evidence_text: str) -> str:
    return json.dumps(
        {
            "preview": {
                "brief": "讨论了 MemoryDigest 运行治理。",
                "keywords": ["MemoryDigest", "运行治理"],
                "participants": ["测试用户"],
            },
            "long_summary": {
                "topic_flow": "讨论聚焦异步生成、失败重试和幂等 claim。",
                "important_details": ["LLM 调用必须位于数据库事务外。"],
                "conclusions": ["成功摘要与索引任务需要原子写入。"],
                "open_loops": [],
            },
            "recall_cards": [
                {
                    "card_id": "card_1",
                    "type": "design_rule",
                    "text": evidence_text,
                    "keywords": [],
                    "importance": 0.9,
                    "evidence_log_ids": evidence_ids,
                }
            ],
            "quality": {"score": 0.9, "issues": []},
        },
        ensure_ascii=False,
    )


def _use_test_session_factory(monkeypatch):
    from core import daily_digest
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(daily_digest, "SessionLocal", TestingSessionLocal)
    return daily_digest


def test_memory_digest_api_route_awaits_structured_async_report(client, monkeypatch):
    from api import memory_routes

    calls = []

    async def fake_report(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "target_date": "2026-07-18",
            "created_sessions": 1,
            "counts": {
                "created": 1,
                "skipped": 0,
                "no_input": 0,
                "failed": 0,
                "in_progress": 0,
            },
            "results": [
                {
                    "session_id": "digest-session",
                    "status": "created",
                    "job_id": 1,
                    "retryable": False,
                    "error_type": "Authorization: Bearer SECRET_SENTINEL",
                    "prompt": "SECRET_SENTINEL",
                }
            ],
            "prompt": "SECRET_SENTINEL",
        }

    monkeypatch.setattr(
        memory_routes,
        "generate_daily_digest_for_date_report",
        fake_report,
    )

    response = client.post(
        "/api/v1/memory/digests/run",
        json={
            "target_date": "2026-07-18",
            "user_id": "digest-user",
            "force": False,
            "retry_failed": False,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["counts"]["created"] == 1
    assert "SECRET_SENTINEL" not in response.text
    assert calls == [
        {
            "target_date": "2026-07-18",
            "user_id": "digest-user",
            "force": False,
            "retry_failed": False,
        }
    ]


def test_memory_digest_api_route_requires_scope_and_reserves_governance_for_admin(
    client,
):
    missing_scope = client.post(
        "/api/v1/memory/digests/run",
        json={"target_date": "2026-07-18"},
    )
    force = client.post(
        "/api/v1/memory/digests/run",
        json={
            "target_date": "2026-07-18",
            "user_id": "digest-user",
            "force": True,
        },
    )
    retry = client.post(
        "/api/v1/memory/digests/run",
        json={
            "target_date": "2026-07-18",
            "user_id": "digest-user",
            "retry_failed": True,
        },
    )

    assert missing_scope.status_code == 422
    assert force.status_code == 403
    assert retry.status_code == 403


def test_memory_digest_routes_reject_invalid_calendar_date(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    public = client.post(
        "/api/v1/memory/digests/run",
        json={"target_date": "2026-02-30", "user_id": "digest-user"},
    )
    admin = client.post(
        "/api/v1/admin/session-memory/digest-session/digests/run",
        headers={"Authorization": "Bearer test-token"},
        json={"target_date": "2026-02-30", "user_id": "digest-user"},
    )
    overflow = client.post(
        "/api/v1/memory/digests/run",
        json={"target_date": "9999-12-31", "user_id": "digest-user"},
    )

    assert public.status_code == 400
    assert admin.status_code == 400
    assert overflow.status_code == 400


def test_admin_memory_digest_route_awaits_structured_async_report(
    client,
    db_session,
    monkeypatch,
):
    from api.admin import session_memory_routes
    from core.database import MemoryDigest

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_session.add(
        MemoryDigest(
            session_id="digest-session",
            user_id="digest-user",
            digest_date="2026-07-18",
            level=1,
            content="旧摘要",
            meta_json='{"status":"active"}',
        )
    )
    db_session.commit()
    calls = []

    async def fake_report(**kwargs):
        calls.append(kwargs)
        return {
            "status": "failed",
            "created_sessions": 0,
            "counts": {
                "created": 0,
                "skipped": 0,
                "no_input": 0,
                "failed": 1,
                "in_progress": 0,
            },
            "results": [
                {
                    "session_id": "digest-session",
                    "status": "failed",
                    "job_id": 1,
                    "retryable": True,
                    "error_type": "model_error",
                }
            ],
        }

    monkeypatch.setattr(
        session_memory_routes,
        "generate_daily_digest_for_date_report",
        fake_report,
    )

    response = client.post(
        "/api/v1/admin/session-memory/digest-session/digests/run",
        headers={"Authorization": "Bearer test-token"},
        json={"force": True, "retry_failed": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["counts"]["failed"] == 1
    assert calls == [
        {
            "target_date": "2026-07-18",
            "user_id": None,
            "session_id": "digest-session",
            "force": True,
            "retry_failed": True,
        }
    ]
    assert response.json()["user_id"] == ""


def test_async_digest_failure_is_recorded_and_explicit_retry_reuses_job(
    db_session,
    monkeypatch,
):
    from app.memory_digest.llm_builder import MemoryDigestModelError
    from core.database import MemoryDigest, MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(content="摘要失败必须留下可重试账本")
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)
    calls = 0

    async def broken_summarizer(_messages):
        nonlocal calls
        calls += 1
        raise MemoryDigestModelError("provider unavailable")

    failed = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=broken_summarizer,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    assert failed["counts"]["failed"] == 1
    assert failed["results"][0]["job_id"] == job.id
    assert failed["results"][0]["retryable"] is True
    assert job.status == "failed"
    assert job.attempt_count == 1
    assert job.retry_count == 0
    assert db_session.query(MemoryDigest).count() == 0

    not_retried = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=broken_summarizer,
        )
    )
    assert calls == 1
    assert not_retried["counts"]["failed"] == 1
    assert not_retried["results"][0]["retryable"] is True

    async def successful_summarizer(_messages):
        nonlocal calls
        calls += 1
        return _success_payload([source_id], "摘要失败必须留下可重试账本")

    retried = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            retry_failed=True,
            llm_summarizer=successful_summarizer,
        )
    )

    db_session.expire_all()
    retried_job = db_session.get(MemoryDigestJob, job.id)
    digests = db_session.query(MemoryDigest).order_by(MemoryDigest.id.asc()).all()
    assert calls == 2
    assert retried["counts"]["created"] == 1
    assert db_session.query(MemoryDigestJob).count() == 1
    assert retried_job.status == "done"
    assert retried_job.attempt_count == 2
    assert retried_job.retry_count == 1
    assert retried_job.result_digest_count >= 3
    assert retried_job.result_root_digest_id == digests[0].id
    assert retried_job.result_semantic_job_id is not None
    assert retried_job.result_source_id
    assert {row.generation_job_id for row in digests} == {retried_job.id}


def test_async_digest_concurrent_claim_calls_summarizer_once(db_session, monkeypatch):
    from core.database import MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(content="并发请求只能有一个执行者进入 LLM")
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def gated_summarizer(_messages):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return _success_payload([source_id], "并发请求只能有一个执行者进入 LLM")

    async def run_case():
        first = asyncio.create_task(
            daily_digest.generate_daily_digest_for_date_report(
                "2026-07-18",
                session_id="digest-session",
                llm_summarizer=gated_summarizer,
            )
        )
        await entered.wait()
        second = await daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=gated_summarizer,
        )
        release.set()
        return await first, second

    first, second = run_async(run_case())

    db_session.expire_all()
    assert calls == 1
    assert first["counts"]["created"] == 1
    assert second["status"] == "partial"
    assert second["counts"]["in_progress"] == 1
    assert db_session.query(MemoryDigestJob).count() == 1


def test_second_retry_request_observes_active_retry_as_in_progress(
    db_session,
    monkeypatch,
):
    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(content="并发 retry 必须识别已运行租约")
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)

    async def failed_summarizer(_messages):
        raise TimeoutError("controlled initial failure")

    failed = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=failed_summarizer,
        )
    )
    assert failed["counts"]["failed"] == 1

    entered = asyncio.Event()
    release = asyncio.Event()

    async def gated_summarizer(_messages):
        entered.set()
        await release.wait()
        return _success_payload(
            [source_id],
            "并发 retry 必须识别已运行租约",
        )

    async def run_case():
        first = asyncio.create_task(
            daily_digest.generate_daily_digest_for_date_report(
                "2026-07-18",
                session_id="digest-session",
                retry_failed=True,
                llm_summarizer=gated_summarizer,
            )
        )
        await entered.wait()
        second = await daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            retry_failed=True,
            llm_summarizer=gated_summarizer,
        )
        release.set()
        return await first, second

    first, second = run_async(run_case())

    assert first["counts"]["created"] == 1
    assert second["status"] == "partial"
    assert second["counts"]["in_progress"] == 1
    assert second["results"][0]["error_type"] == ""


def test_async_digest_rejects_stale_source_snapshot(db_session, monkeypatch):
    from core.database import MemoryDigest, MemoryDigestJob
    from tests.conftest import TestingSessionLocal

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(content="LLM 前读取来源快照")
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)

    async def mutate_source_during_llm(_messages):
        other = TestingSessionLocal()
        try:
            other.query(MemoryDigestJob).update({MemoryDigestJob.max_retry: 0})
            other.add(_log(content="LLM 期间新增的同日来源"))
            other.commit()
        finally:
            other.close()
        return _success_payload([source_id], "LLM 前读取来源快照")

    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=mutate_source_during_llm,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    assert report["counts"]["failed"] == 1
    assert report["results"][0]["error_type"] == "source_changed"
    assert report["results"][0]["retryable"] is False
    assert job.status == "failed"
    assert job.error_type == "source_changed"
    assert db_session.query(MemoryDigest).count() == 0


def test_async_digest_no_input_is_structured_and_does_not_create_fake_job(
    db_session,
    monkeypatch,
):
    from core.database import MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)

    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="missing-session",
        )
    )

    assert report["status"] == "no_input"
    assert report["counts"]["no_input"] == 1
    assert report["results"] == []
    assert db_session.query(MemoryDigestJob).count() == 0


def test_retry_failed_requires_existing_failed_job_and_preserves_active_digest(
    db_session,
    monkeypatch,
):
    from core.database import MemoryDigest, MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    db_session.add(_log(content="历史 active 摘要不能被伪 retry 重建"))
    existing = MemoryDigest(
        user_id="digest-user",
        session_id="digest-session",
        digest_date="2026-07-18",
        level=2,
        content="旧摘要",
        meta_json='{"status":"active","generator":"llm"}',
    )
    db_session.add(existing)
    db_session.commit()
    existing_id = int(existing.id)

    async def forbidden_summarizer(_messages):
        raise AssertionError("无 failed job 时不得进入模型")

    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            retry_failed=True,
            llm_summarizer=forbidden_summarizer,
        )
    )

    db_session.expire_all()
    assert report["counts"]["failed"] == 1
    assert report["results"][0]["error_type"] == "retry_job_not_found"
    assert db_session.query(MemoryDigestJob).count() == 0
    assert json.loads(db_session.get(MemoryDigest, existing_id).meta_json)["status"] == "active"


def test_retry_failed_rejects_done_job_without_regenerating(
    db_session,
    monkeypatch,
):
    from core.database import MemoryDigest, MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(content="done 作业不能通过 retry_failed 重复生成")
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)

    async def successful_summarizer(_messages):
        return _success_payload(
            [source_id],
            "done 作业不能通过 retry_failed 重复生成",
        )

    created = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=successful_summarizer,
        )
    )
    assert created["counts"]["created"] == 1
    digest_ids = {
        int(row.id)
        for row in db_session.query(MemoryDigest).all()
    }

    async def forbidden_summarizer(_messages):
        raise AssertionError("done job 不得再次调用模型")

    retried = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            retry_failed=True,
            llm_summarizer=forbidden_summarizer,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    assert retried["counts"]["failed"] == 1
    assert retried["results"][0]["error_type"] == "retry_status_conflict"
    assert job.status == "done"
    assert {
        int(row.id)
        for row in db_session.query(MemoryDigest).all()
    } == digest_ids


def test_non_retryable_failure_rejects_retry_but_force_can_override(
    db_session,
    monkeypatch,
):
    from core.database import MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(
        content="MemoryDigest 运行治理中，禁用 LLM 的失败不能伪装成自动可重试"
    )
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)

    first = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            use_llm=False,
        )
    )
    observed_again = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
        )
    )

    async def forbidden_summarizer(_messages):
        raise AssertionError("非可重试失败不得进入模型")

    rejected_retry = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            retry_failed=True,
            llm_summarizer=forbidden_summarizer,
        )
    )
    job_before_force = db_session.query(MemoryDigestJob).one()
    attempt_count_before_force = int(job_before_force.attempt_count)

    async def successful_summarizer(_messages):
        return _success_payload(
            [source_id],
            "禁用 LLM 的失败不能伪装成自动可重试",
        )

    forced = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            force=True,
            llm_summarizer=successful_summarizer,
        )
    )

    assert first["results"][0]["error_type"] == "generator_not_llm"
    assert first["results"][0]["retryable"] is False
    assert observed_again["results"][0]["retryable"] is False
    assert rejected_retry["results"][0]["retryable"] is False
    assert rejected_retry["results"][0]["error_type"] == "generator_not_llm"
    assert attempt_count_before_force == 1
    assert forced["counts"]["created"] == 1


def test_async_digest_skips_noise_without_calling_model(db_session, monkeypatch):
    from core.database import MemoryDigest, MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    db_session.add_all([
        _log(content="签到"),
        _log(content="[图片:1张]"),
    ])
    db_session.commit()

    async def forbidden_summarizer(_messages):
        raise AssertionError("无有效日志时不得调用模型")

    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=forbidden_summarizer,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    assert report["counts"]["skipped"] == 1
    assert job.status == "skipped"
    assert db_session.query(MemoryDigest).count() == 0


def test_async_digest_semantic_enqueue_failure_rolls_back_rows_and_fails_job(
    db_session,
    monkeypatch,
):
    from app.memory_digest.builder import MemoryDigestBuildResult
    from core.database import MemoryDigest, MemoryDigestJob, SemanticIndexJob

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(content="摘要与 semantic job 必须原子提交")
    db_session.add(source)
    db_session.commit()

    async def fake_build(**_kwargs):
        return MemoryDigestBuildResult(
            status="active",
            meta={
                "status": "active",
                "generator": "llm",
                "llm_status": "success",
                "recall_cards": [
                    {
                        "card_id": "card_1",
                        "type": "design_rule",
                        "text": "摘要与 semantic job 必须原子提交",
                        "keywords": [],
                        "importance": 0.9,
                        "evidence_log_ids": [int(source.id)],
                    }
                ],
            },
            level_contents={0: "详细摘要", 1: "预览摘要", 2: "召回卡"},
        )

    monkeypatch.setattr(
        daily_digest,
        "_build_memory_digest_result_async",
        fake_build,
    )
    monkeypatch.setattr(
        "core.semantic.jobs.enqueue_index_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("semantic enqueue failed")
        ),
    )

    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    assert report["counts"]["failed"] == 1
    assert report["results"][0]["error_type"] == "write_failed"
    assert job.status == "failed"
    assert db_session.query(MemoryDigest).count() == 0
    assert db_session.query(SemanticIndexJob).count() == 0


def test_async_digest_accepts_multi_sender_group_without_false_source_change(
    db_session,
    monkeypatch,
):
    from core.database import MemoryDigest, MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    first = _log(
        content="甲讨论了 MemoryDigest 运行治理，LLM 调用必须位于数据库事务外",
        user_id="group-user-a",
        session_id="group_42",
    )
    second = _log(
        content="乙补充成功摘要与索引任务需要原子写入",
        user_id="group-user-b",
        session_id="group_42",
    )
    db_session.add_all([first, second])
    db_session.commit()

    async def successful_summarizer(_messages):
        return _success_payload(
            [int(first.id)],
            "LLM 调用必须位于数据库事务外",
        )

    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="group_42",
            llm_summarizer=successful_summarizer,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    assert report["counts"]["created"] == 1, report["results"][0]["error_type"]
    assert job.status == "done"
    assert job.source_log_count == 2
    assert db_session.query(MemoryDigest).count() >= 3


def test_force_retries_failed_job_even_after_retry_budget_is_exhausted(
    db_session,
    monkeypatch,
):
    from core.database import MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(content="Admin force 可以显式重跑耗尽预算的失败作业")
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)

    async def failed_summarizer(_messages):
        raise TimeoutError("controlled timeout")

    first = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=failed_summarizer,
        )
    )
    assert first["counts"]["failed"] == 1

    job = db_session.query(MemoryDigestJob).one()
    job.max_retry = 0
    db_session.commit()

    async def successful_summarizer(_messages):
        return _success_payload(
            [source_id],
            "Admin force 可以显式重跑耗尽预算的失败作业",
        )

    retried = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            force=True,
            llm_summarizer=successful_summarizer,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    assert retried["counts"]["created"] == 1
    assert job.status == "done"
    assert job.retry_count == 1


def test_force_failure_preserves_active_digest_and_clears_current_result_fields(
    db_session,
    monkeypatch,
):
    from core.database import MemoryDigest, MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(content="force 失败时保留旧 active 摘要")
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)

    async def successful_summarizer(_messages):
        return _success_payload(
            [source_id],
            "force 失败时保留旧 active 摘要",
        )

    created = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=successful_summarizer,
        )
    )
    assert created["counts"]["created"] == 1
    original_ids = {
        int(row.id)
        for row in db_session.query(MemoryDigest).all()
    }

    async def failed_summarizer(_messages):
        raise TimeoutError("controlled force failure")

    failed = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            force=True,
            llm_summarizer=failed_summarizer,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    remaining = db_session.query(MemoryDigest).all()
    assert failed["counts"]["failed"] == 1
    assert job.status == "failed"
    assert job.result_digest_count == 0
    assert job.result_source_id == ""
    assert job.result_root_digest_id is None
    assert job.result_semantic_job_id is None
    assert {int(row.id) for row in remaining} == original_ids
    assert {
        json.loads(row.meta_json).get("status", "active")
        for row in remaining
    } == {"active"}


def test_retry_after_force_failure_replaces_old_active_digest_generation(
    db_session,
    monkeypatch,
):
    from app.memory_digest.retrieval_service import digest_status, safe_digest_meta
    from core.database import MemoryDigest

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(content="失败后的显式 retry 必须替换旧 active 摘要")
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)

    async def successful_summarizer(_messages):
        return _success_payload(
            [source_id],
            "失败后的显式 retry 必须替换旧 active 摘要",
        )

    first = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=successful_summarizer,
        )
    )
    assert first["counts"]["created"] == 1
    first_ids = {
        int(row.id)
        for row in db_session.query(MemoryDigest).all()
    }

    async def failed_summarizer(_messages):
        raise TimeoutError("controlled force failure")

    failed = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            force=True,
            llm_summarizer=failed_summarizer,
        )
    )
    assert failed["counts"]["failed"] == 1

    retried = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            retry_failed=True,
            llm_summarizer=successful_summarizer,
        )
    )

    db_session.expire_all()
    rows = db_session.query(MemoryDigest).order_by(MemoryDigest.id.asc()).all()
    active = [
        row for row in rows
        if digest_status(safe_digest_meta(row.meta_json)) == "active"
    ]
    archived = [
        row for row in rows
        if digest_status(safe_digest_meta(row.meta_json)) == "archived"
    ]
    assert retried["counts"]["created"] == 1
    assert {int(row.id) for row in archived} == first_ids
    assert len(active) == 3
    assert len(archived) == 3


def test_sender_name_change_during_llm_invalidates_source_snapshot(
    db_session,
    monkeypatch,
):
    from core.database import ChatLog, MemoryDigest, MemoryDigestJob
    from tests.conftest import TestingSessionLocal

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(
        content="昵称是摘要 Prompt 来源的一部分",
        sender_name="旧昵称",
    )
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)

    async def rename_sender_during_llm(_messages):
        other = TestingSessionLocal()
        try:
            other.query(ChatLog).filter(ChatLog.id == source_id).update({
                ChatLog.sender_name: "新昵称",
            })
            other.commit()
        finally:
            other.close()
        return _success_payload(
            [source_id],
            "昵称是摘要 Prompt 来源的一部分",
        )

    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=rename_sender_during_llm,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    assert report["counts"]["failed"] == 1
    assert report["results"][0]["error_type"] == "source_changed"
    assert job.status == "failed"
    assert db_session.query(MemoryDigest).count() == 0


def test_memory_digest_job_heartbeat_renews_only_current_fencing_token(
    db_session,
):
    from app.memory_digest.jobs import (
        MemoryDigestJobLeaseLost,
        claim_memory_digest_job,
        heartbeat_memory_digest_job,
        memory_digest_source_snapshot,
    )
    from core.database import MemoryDigestJob

    source = _log(content="heartbeat 只能续租当前 fencing token")
    db_session.add(source)
    db_session.commit()
    snapshot = memory_digest_source_snapshot(
        session_id="digest-session",
        digest_date="2026-07-18",
        logs=[source],
    )
    started_at = datetime(2026, 7, 18, 12, 0, 0)
    first = claim_memory_digest_job(
        db_session,
        snapshot,
        lease_seconds=60,
        now=started_at,
    )

    heartbeat_memory_digest_job(
        db_session,
        first,
        lease_seconds=60,
        now=started_at + timedelta(seconds=20),
    )
    db_session.expire_all()
    assert db_session.get(MemoryDigestJob, first.job_id).lease_expires_at == (
        started_at + timedelta(seconds=80)
    )

    replacement = claim_memory_digest_job(
        db_session,
        snapshot,
        force=True,
        lease_seconds=60,
        now=started_at + timedelta(seconds=81),
    )
    assert replacement.decision == "claimed"
    assert replacement.lease_token != first.lease_token
    with pytest.raises(MemoryDigestJobLeaseLost):
        heartbeat_memory_digest_job(
            db_session,
            first,
            lease_seconds=60,
            now=started_at + timedelta(seconds=82),
        )


def test_expired_lease_respects_retry_budget_but_force_can_reclaim(
    db_session,
):
    from app.memory_digest.jobs import (
        claim_memory_digest_job,
        memory_digest_source_snapshot,
    )
    from core.database import MemoryDigestJob

    source = _log(content="过期租约不能无限突破自动重试预算")
    db_session.add(source)
    db_session.commit()
    snapshot = memory_digest_source_snapshot(
        session_id="digest-session",
        digest_date="2026-07-18",
        logs=[source],
    )
    started_at = datetime(2026, 7, 18, 12, 0, 0)
    first = claim_memory_digest_job(
        db_session,
        snapshot,
        lease_seconds=30,
        now=started_at,
    )
    job = db_session.get(MemoryDigestJob, first.job_id)
    job.max_retry = 0
    db_session.commit()

    exhausted = claim_memory_digest_job(
        db_session,
        snapshot,
        now=started_at + timedelta(seconds=31),
    )
    assert exhausted.decision == "failed"
    assert exhausted.error_type == "lease_expired_exhausted"

    forced = claim_memory_digest_job(
        db_session,
        snapshot,
        force=True,
        now=started_at + timedelta(seconds=32),
    )
    assert forced.decision == "claimed"


def test_reclaimed_lease_fences_old_llm_result_and_derived_rows(
    db_session,
    monkeypatch,
):
    from app.memory_digest.jobs import (
        claim_memory_digest_job,
        memory_digest_source_snapshot,
    )
    from core.database import MemoryDigest, MemoryDigestJob, SemanticIndexJob
    from core.time_utils import db_now_naive
    from tests.conftest import TestingSessionLocal

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(content="旧 fencing token 不能写入摘要或索引")
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)
    snapshot = memory_digest_source_snapshot(
        session_id="digest-session",
        digest_date="2026-07-18",
        logs=[source],
    )
    replacement_tokens: list[str] = []

    async def reclaim_during_llm(_messages):
        other = TestingSessionLocal()
        try:
            other.query(MemoryDigestJob).update({
                MemoryDigestJob.lease_expires_at: db_now_naive() - timedelta(seconds=1),
            })
            other.commit()
            replacement = claim_memory_digest_job(
                other,
                snapshot,
                force=True,
            )
            assert replacement.decision == "claimed"
            replacement_tokens.append(replacement.lease_token)
        finally:
            other.close()
        return _success_payload(
            [source_id],
            "旧 fencing token 不能写入摘要或索引",
        )

    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=reclaim_during_llm,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    assert replacement_tokens
    assert report["counts"]["in_progress"] == 1
    assert report["results"][0]["error_type"] == "lease_lost"
    assert job.status == "running"
    assert job.lease_token == replacement_tokens[0]
    assert db_session.query(MemoryDigest).count() == 0
    assert db_session.query(SemanticIndexJob).count() == 0


def test_multi_session_claim_failure_returns_partial_and_continues(
    db_session,
    monkeypatch,
):
    from app.memory_digest.jobs import claim_memory_digest_job as real_claim
    from core.database import MemoryDigest

    daily_digest = _use_test_session_factory(monkeypatch)
    broken = _log(
        content="该 session 的 claim 被受控数据库异常中断",
        session_id="broken-session",
    )
    healthy = _log(
        content="其他 session 仍应完成摘要",
        session_id="healthy-session",
    )
    db_session.add_all([broken, healthy])
    db_session.commit()

    def controlled_claim(db, snapshot, **kwargs):
        if snapshot.session_id == "broken-session":
            raise OperationalError("controlled claim", {}, RuntimeError("locked"))
        return real_claim(db, snapshot, **kwargs)

    async def successful_summarizer(_messages):
        return _success_payload(
            [int(healthy.id)],
            "其他 session 仍应完成摘要",
        )

    monkeypatch.setattr(daily_digest, "claim_memory_digest_job", controlled_claim)
    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            llm_summarizer=successful_summarizer,
        )
    )

    assert report["status"] == "partial"
    assert report["counts"]["created"] == 1
    assert report["counts"]["failed"] == 1
    by_session = {item["session_id"]: item for item in report["results"]}
    assert by_session["broken-session"]["error_type"] == "claim_failed"
    assert by_session["healthy-session"]["status"] == "created"
    assert db_session.query(MemoryDigest).count() >= 3


def test_multi_session_skipped_settlement_failure_returns_partial_and_continues(
    db_session,
    monkeypatch,
):
    from app.memory_digest.jobs import (
        settle_memory_digest_job_without_rows as real_settle,
    )
    from core.database import MemoryDigest, MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    noise = _log(content="签到", session_id="noise-session")
    healthy = _log(
        content="其他 session 仍应完成摘要",
        session_id="healthy-session",
    )
    db_session.add_all([noise, healthy])
    db_session.commit()

    def controlled_settle(db, claim, **kwargs):
        if kwargs.get("status") == "skipped":
            raise OperationalError("controlled settle", {}, RuntimeError("locked"))
        return real_settle(db, claim, **kwargs)

    async def successful_summarizer(_messages):
        return _success_payload(
            [int(healthy.id)],
            "其他 session 仍应完成摘要",
        )

    monkeypatch.setattr(
        daily_digest,
        "settle_memory_digest_job_without_rows",
        controlled_settle,
    )
    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            llm_summarizer=successful_summarizer,
        )
    )

    db_session.expire_all()
    jobs = {
        row.session_id: row
        for row in db_session.query(MemoryDigestJob).all()
    }
    assert report["status"] == "partial"
    assert report["counts"]["created"] == 1
    assert report["counts"]["failed"] == 1
    by_session = {item["session_id"]: item for item in report["results"]}
    assert by_session["noise-session"]["error_type"] == "job_settlement_failed"
    assert jobs["noise-session"].status == "failed"
    assert jobs["healthy-session"].status == "done"
    assert db_session.query(MemoryDigest).count() >= 3


def test_finish_failure_after_semantic_flush_rolls_back_all_derived_rows(
    db_session,
    monkeypatch,
):
    from core.database import MemoryDigest, MemoryDigestJob, SemanticIndexJob

    daily_digest = _use_test_session_factory(monkeypatch)
    source = _log(content="semantic job flush 后 finish 失败必须整体回滚")
    db_session.add(source)
    db_session.commit()
    source_id = int(source.id)

    async def successful_summarizer(_messages):
        return _success_payload(
            [source_id],
            "semantic job flush 后 finish 失败必须整体回滚",
        )

    monkeypatch.setattr(
        daily_digest,
        "finish_memory_digest_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("controlled finish failure")
        ),
    )
    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=successful_summarizer,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    assert report["results"][0]["error_type"] == "write_failed"
    assert job.status == "failed"
    assert db_session.query(MemoryDigest).count() == 0
    assert db_session.query(SemanticIndexJob).count() == 0


def test_failed_digest_job_keeps_safe_model_and_request_log_audit(
    db_session,
    monkeypatch,
):
    from app.memory_digest.llm_builder import MemoryDigestLlmOutput
    from core.database import MemoryDigest, MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    db_session.add(_log(content="无效 JSON 仍要保留安全的模型请求审计"))
    db_session.commit()

    async def invalid_summarizer(_messages):
        return MemoryDigestLlmOutput(
            content="not-json",
            model="actual-summary-model",
            requested_model="requested-summary-model",
            request_log_id=987,
            actual_model_observed=True,
        )

    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=invalid_summarizer,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    meta = json.loads(job.meta_json)
    assert report["results"][0]["error_type"] == "output_invalid"
    assert job.status == "failed"
    assert meta["llm_models"] == ["actual-summary-model"]
    assert meta["llm_requested_models"] == ["requested-summary-model"]
    assert meta["llm_request_log_ids"] == [987]
    assert meta["llm_actual_model_observed"] is True
    assert db_session.query(MemoryDigest).count() == 0


def test_model_error_uses_structured_retryable_type_and_attempt_audit(
    db_session,
    monkeypatch,
):
    from app.memory_digest.llm_builder import MemoryDigestModelError
    from core.database import MemoryDigest, MemoryDigestJob

    daily_digest = _use_test_session_factory(monkeypatch)
    db_session.add(_log(content="瞬时模型失败必须保持可重试"))
    db_session.commit()

    async def failed_summarizer(_messages):
        raise MemoryDigestModelError(
            "down",
            requested_models=("requested-summary-model",),
            request_log_ids=(4321,),
        )

    report = run_async(
        daily_digest.generate_daily_digest_for_date_report(
            "2026-07-18",
            session_id="digest-session",
            llm_summarizer=failed_summarizer,
        )
    )

    db_session.expire_all()
    job = db_session.query(MemoryDigestJob).one()
    meta = json.loads(job.meta_json)
    assert report["results"][0]["error_type"] == "model_error"
    assert report["results"][0]["retryable"] is True
    assert job.status == "failed"
    assert meta["llm_models"] == []
    assert meta["llm_requested_models"] == ["requested-summary-model"]
    assert meta["llm_request_log_ids"] == [4321]
    assert meta["llm_actual_model_observed"] is False
    assert db_session.query(MemoryDigest).count() == 0


def test_file_sqlite_concurrent_claim_has_single_lease_owner(tmp_path):
    from app.memory_digest.jobs import (
        MemoryDigestSourceSnapshot,
        claim_memory_digest_job,
    )
    from core.database import Base, MemoryDigestJob

    engine = create_engine(
        f"sqlite:///{tmp_path / 'memory-digest-claim.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.tables[MemoryDigestJob.__tablename__].create(engine)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    snapshot = MemoryDigestSourceSnapshot(
        user_id="digest-user",
        session_id="digest-session",
        digest_date="2026-07-18",
        source_start_log_id=1,
        source_end_log_id=2,
        source_log_count=2,
        source_revision="a" * 64,
    )
    barrier = threading.Barrier(2)

    def claim(worker_id: str):
        db = session_factory()
        try:
            barrier.wait(timeout=5)
            return claim_memory_digest_job(
                db,
                snapshot,
                worker_id=worker_id,
            )
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            decisions = [
                future.result(timeout=10).decision
                for future in (
                    pool.submit(claim, "worker-a"),
                    pool.submit(claim, "worker-b"),
                )
            ]
        verify_db = session_factory()
        try:
            assert sorted(decisions) == ["claimed", "in_progress"]
            assert verify_db.query(MemoryDigestJob).count() == 1
            assert verify_db.query(MemoryDigestJob).one().lease_token
        finally:
            verify_db.close()
    finally:
        engine.dispose()
