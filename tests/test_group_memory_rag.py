from datetime import datetime, timedelta


def _local_now() -> datetime:
    # SQLite ORM DateTime fixture 保持 naive 本地墙钟时间语义。
    return datetime.now()  # noqa: DTZ005


class FixedGroupReranker:
    def __init__(self, scores):
        self.scores = scores

    def rerank(self, query, candidates, *, top_k=None):
        from core.semantic.reranker import RerankResult

        limited = candidates[:top_k] if top_k else candidates
        return [
            RerankResult(
                candidate_id=candidate.candidate_id,
                raw_score=self.scores.get(candidate.candidate_id, 0.0),
                score=self.scores.get(candidate.candidate_id, 0.0),
                model="fixed-group-reranker",
                score_mode="identity",
            )
            for candidate in limited
        ]


def _memory(db, **kwargs):
    from core.database import GroupMemory

    defaults = {
        "group_id": "group_1097666427",
        "memory_type": "topic",
        "content": "模型部署: 群里经常讨论本地模型部署和量化",
        "content_hash": f"gm-{kwargs.get('id', '')}-{len(db.new)}",
        "confidence": 0.86,
        "evidence_count": 3,
        "evidence_log_ids_json": "[1, 2, 3]",
        "decay_score": 1.0,
        "status": "active",
        "inject_policy": "auto",
        "last_seen": _local_now(),
    }
    defaults.update(kwargs)
    row = GroupMemory(**defaults)
    db.add(row)
    return row


def test_group_memory_does_not_apply_top100_before_relevance(db_session):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService

    now = _local_now()
    for idx in range(120):
        _memory(
            db_session,
            content=f"闲聊记忆 {idx}: 大家讨论咖啡天气和午饭",
            content_hash=f"gm-noise-{idx}",
            confidence=0.99 - idx * 0.001,
        )
    relevant = _memory(
        db_session,
        content="老记忆: 本地模型部署时要先检查量化参数和显存",
        content_hash="gm-old-relevant",
        confidence=0.60,
        last_seen=now - timedelta(days=120),
    )
    db_session.commit()

    result = GroupMemoryRetrievalService(db_session).select(
        group_id="1097666427",
        current_user_input="本地模型部署量化参数怎么调？",
        recent_messages=[],
        max_items=5,
    )

    assert relevant.id in result.selected_ids


def test_old_but_relevant_memory_can_be_selected(db_session):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService

    now = _local_now()
    old = _memory(
        db_session,
        content="老项目记忆: RAG reranker 服务部署在独立进程",
        content_hash="gm-old-rag",
        confidence=0.66,
        last_seen=now - timedelta(days=180),
    )
    _memory(
        db_session,
        content="新近闲聊: 大家讨论晚饭吃什么",
        content_hash="gm-new-food",
        confidence=0.99,
        last_seen=now,
    )
    db_session.commit()

    result = GroupMemoryRetrievalService(db_session).select(
        group_id="1097666427",
        current_user_input="RAG reranker 独立部署怎么做？",
        recent_messages=[],
        max_items=3,
    )

    assert old.id in result.selected_ids


def test_disabled_or_manual_memory_never_injected(db_session):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService

    disabled = _memory(db_session, status="disabled", content_hash="gm-disabled")
    manual = _memory(db_session, inject_policy="manual_only", content_hash="gm-manual")
    db_session.commit()

    result = GroupMemoryRetrievalService(db_session).select(
        group_id="1097666427",
        current_user_input="本地模型部署量化参数怎么调？",
        recent_messages=[],
    )

    assert disabled.id not in result.selected_ids
    assert manual.id not in result.selected_ids
    skipped = {item["id"]: item["reason"] for item in result.skipped}
    assert skipped[disabled.id] == "inactive_status"
    assert skipped[manual.id] == "manual_only"


def test_source_prior_does_not_bypass_relevance_gate(db_session):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService

    irrelevant = _memory(
        db_session,
        content="高置信闲聊: 大家喜欢讨论咖啡口味",
        content_hash="gm-irrelevant",
        confidence=0.99,
        evidence_count=20,
    )
    db_session.commit()

    result = GroupMemoryRetrievalService(
        db_session,
        reranker_provider=FixedGroupReranker({f"group_memory:{irrelevant.id}:memory": 0.2}),
    ).select(
        group_id="1097666427",
        current_user_input="RAG reranker 独立部署怎么做？",
        recent_messages=[],
    )

    assert irrelevant.id not in result.selected_ids
    skipped = {item["id"]: item["reason"] for item in result.skipped}
    assert skipped[irrelevant.id] == "low_reranker"


def test_group_memory_preview_does_not_record_injection(db_session, monkeypatch):
    from app.group_memory.injection_service import GroupMemoryInjectionService
    from core.database import ChatStreamConfig
    import core.semantic.provider_factory as provider_factory

    db_session.add(ChatStreamConfig(chat_stream_id="qq:1097666427:group", group_profile_mode="preview"))
    memory = _memory(db_session, content_hash="gm-preview-rag")
    db_session.commit()
    monkeypatch.setattr(
        provider_factory,
        "get_reranker_provider",
        lambda: FixedGroupReranker({f"group_memory:{memory.id}:memory": 0.92}),
    )

    result = GroupMemoryInjectionService(db_session).build_context(
        group_id="1097666427",
        current_user_input="本地模型部署量化参数怎么调？",
        recent_messages=[],
        record_injection=True,
    )
    db_session.refresh(memory)

    assert result.context == ""
    assert result.selected_ids == [memory.id]
    assert memory.injected_count == 0
    assert memory.last_injected_at is None


def test_group_memory_injection_blocks_when_reranker_required_unavailable(db_session, monkeypatch):
    from app.group_memory.injection_service import GroupMemoryInjectionService
    from core.database import ChatStreamConfig
    import core.semantic.provider_factory as provider_factory

    monkeypatch.setenv("RAG_ALLOW_DEGRADED", "0")
    monkeypatch.setenv("RAG_RERANKER_ENABLED", "1")
    monkeypatch.setenv("RAG_RERANKER_URL", "")
    monkeypatch.setenv("RAG_LOCAL_RERANKER_MODEL", "./models/not-present-reranker")
    provider_factory.get_reranker_provider.cache_clear()
    db_session.add(ChatStreamConfig(chat_stream_id="qq:1097666427:group", group_profile_mode="on"))
    _memory(db_session, content_hash="gm-strict-reranker")
    db_session.commit()

    result = GroupMemoryInjectionService(db_session).build_context(
        group_id="1097666427",
        current_user_input="本地模型部署量化参数怎么调？",
        recent_messages=[],
    )

    assert result.context == ""
    assert result.selected_ids == []
    assert result.debug["degraded_blocked"] is True
    assert result.debug["blocked_reason"] == "reranker_unavailable"
    assert result.debug["group_memory_skipped"] == [{"reason": "reranker_unavailable"}]


def test_group_memory_injection_uses_factory_reranker_provider(db_session, monkeypatch):
    from app.group_memory.injection_service import GroupMemoryInjectionService
    from core.database import ChatStreamConfig
    import core.semantic.provider_factory as provider_factory

    db_session.add(ChatStreamConfig(chat_stream_id="qq:1097666427:group", group_profile_mode="on"))
    memory = _memory(
        db_session,
        content="低词面相关但 reranker 确认可注入的群体记忆",
        content_hash="gm-factory-reranker",
    )
    db_session.commit()
    monkeypatch.setattr(
        provider_factory,
        "get_reranker_provider",
        lambda: FixedGroupReranker({f"group_memory:{memory.id}:memory": 0.92}),
    )

    result = GroupMemoryInjectionService(db_session).build_context(
        group_id="1097666427",
        current_user_input="需要上下文",
        recent_messages=[],
    )

    assert result.selected_ids == [memory.id]
    assert result.score_components[str(memory.id)]["reranker"] == 0.92


def test_no_model_group_memory_does_not_reuse_reranker_cache(
    db_session,
    monkeypatch,
):
    from app.group_memory.injection_service import (
        GROUP_MEMORY_RAG_CACHE,
        GroupMemoryInjectionService,
    )
    from core.database import ChatStreamConfig
    import core.semantic.provider_factory as provider_factory

    calls = []
    GROUP_MEMORY_RAG_CACHE.clear()
    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:no-model-cache:group",
        group_profile_mode="on",
    ))
    memory = _memory(
        db_session,
        group_id="group_no-model-cache",
        content="缓存隔离测试群体记忆",
        content_hash="gm-no-model-cache",
    )
    db_session.commit()

    class CountingReranker(FixedGroupReranker):
        def rerank(self, query, candidates, *, top_k=None):
            calls.append(query)
            return super().rerank(query, candidates, top_k=top_k)

    provider = CountingReranker({
        f"group_memory:{memory.id}:memory": 0.92,
    })
    monkeypatch.setattr(
        provider_factory,
        "get_reranker_provider",
        lambda: provider,
    )
    service = GroupMemoryInjectionService(db_session)

    live = service.build_context(
        group_id="no-model-cache",
        current_user_input="缓存隔离测试",
    )
    preview = service.build_context(
        group_id="no-model-cache",
        current_user_input="缓存隔离测试",
        allow_model_calls=False,
    )

    assert live.context
    assert calls == ["缓存隔离测试"]
    assert preview.context == ""
    assert preview.debug["cache_hit"] is False
    assert preview.debug["model_calls_allowed"] is False
    assert preview.debug["group_memory_skipped"] == [
        {"reason": "model_calls_forbidden"}
    ]
    assert preview.debug["model_dependent_retrieval_skipped"] is True


def test_group_memory_rag_timeout_marks_fallback(db_session, monkeypatch):
    import time

    from app.group_memory import injection_service
    from app.group_memory.injection_service import GroupMemoryInjectionService
    from app.group_memory.retrieval_service import GroupMemorySelection
    from core.database import ChatStreamConfig
    import core.semantic.provider_factory as provider_factory

    db_session.add(ChatStreamConfig(chat_stream_id="qq:1097666427:group", group_profile_mode="on"))
    db_session.commit()
    monkeypatch.setattr(provider_factory, "get_reranker_provider", lambda: FixedGroupReranker({}))

    def slow_select(self, **_kwargs):
        time.sleep(0.01)
        return GroupMemorySelection()

    monkeypatch.setattr(injection_service.GroupMemoryRetrievalService, "select", slow_select)

    result = GroupMemoryInjectionService(db_session).build_context(
        group_id="1097666427",
        current_user_input="本地模型部署量化参数怎么调？",
        recent_messages=[],
        rag_timeout_ms=1,
    )

    assert result.context == ""
    assert result.debug["timeout_fallback"] is True
    assert result.debug["latency_ms"] >= 1
