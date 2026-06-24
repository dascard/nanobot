from datetime import datetime, timedelta


def _local_now() -> datetime:
    # SQLite ORM DateTime fixture 保持 naive 本地墙钟时间语义。
    return datetime.now()  # noqa: DTZ005


def test_group_memory_injection_uses_stream_config_for_group_id(db_session):
    from app.group_memory.injection_service import GroupMemoryInjectionService
    from core.database import ChatStreamConfig, GroupMemory

    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:1097666427:group",
        group_profile_mode="on",
    ))
    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="topic",
        content="项目开发与UI设计: 群里经常讨论移动端界面层次",
        content_hash="gm-inject-topic",
        confidence=0.86,
        evidence_count=2,
        evidence_log_ids_json="[1, 2]",
        decay_score=1.0,
        status="active",
        source="manual_group_memory_extract",
        last_seen=_local_now(),
    ))
    db_session.commit()

    result = GroupMemoryInjectionService(db_session).build_context(
        group_id="group_1097666427",
        current_user_input="现在 UI 层次还是乱，移动端怎么改？",
        recent_messages=[],
    )

    assert result.debug["group_profile_mode"] == "on"
    assert result.debug["group_memory_injected"] is True
    assert result.selected_ids
    assert result.context.startswith('<group_memory_context group_id="group_1097666427"')
    assert "[GroupProfileContext]" not in result.context
    assert "项目开发与UI设计" in result.context
    assert result.debug["group_memory_context_chars"] == len(result.context)

    memory = db_session.query(GroupMemory).filter(GroupMemory.content_hash == "gm-inject-topic").first()
    assert memory.injected_count == 0
    assert memory.last_injected_at is None


def test_group_memory_rag_cache_uses_monotonic_ttl(db_session, monkeypatch):
    from app.group_memory import injection_service as injection_module
    from core.database import ChatStreamConfig, GroupMemory

    injection_module.GROUP_MEMORY_RAG_CACHE.clear()
    clock = {"now": 100.0}
    monkeypatch.setattr(injection_module.time, "monotonic", lambda: clock["now"])

    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:1097666427:group",
        group_profile_mode="on",
    ))
    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="topic",
        content="缓存测试: 群里持续讨论 RAG 缓存与召回",
        content_hash="gm-cache-topic",
        confidence=0.90,
        evidence_count=3,
        evidence_log_ids_json="[1, 2, 3]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        last_seen=_local_now(),
    ))
    db_session.commit()

    try:
        service = injection_module.GroupMemoryInjectionService(db_session)
        first = service.build_context(
            group_id="group_1097666427",
            current_user_input="RAG 缓存怎么处理？",
            recent_messages=[],
        )
        first_ids = list(first.selected_ids)
        deadline = next(iter(injection_module.GROUP_MEMORY_RAG_CACHE.values()))[0]

        assert first.debug["cache_hit"] is False
        assert first_ids
        assert deadline == 220.0

        db_session.query(GroupMemory).filter(GroupMemory.content_hash == "gm-cache-topic").delete(
            synchronize_session=False,
        )
        db_session.commit()

        clock["now"] = 101.0
        cached = service.build_context(
            group_id="group_1097666427",
            current_user_input="RAG 缓存怎么处理？",
            recent_messages=[],
        )
        assert cached.debug["cache_hit"] is True
        assert cached.selected_ids == first_ids

        clock["now"] = 221.0
        expired = service.build_context(
            group_id="group_1097666427",
            current_user_input="RAG 缓存怎么处理？",
            recent_messages=[],
        )
        assert expired.debug["cache_hit"] is False
        assert expired.selected_ids == []
    finally:
        injection_module.GROUP_MEMORY_RAG_CACHE.clear()


def test_group_memory_record_injected_updates_stats_explicitly(db_session):
    from app.group_memory.injection_service import GroupMemoryInjectionService
    from core.database import GroupMemory

    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="topic",
        content="模型部署: 群里经常讨论本地模型部署",
        content_hash="gm-record-topic",
        confidence=0.86,
        evidence_count=2,
        evidence_log_ids_json="[1, 2]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        last_seen=_local_now(),
    ))
    db_session.commit()

    memory = db_session.query(GroupMemory).filter(GroupMemory.content_hash == "gm-record-topic").first()
    assert memory.injected_count == 0
    assert memory.last_injected_at is None

    updated = GroupMemoryInjectionService(db_session).record_injected([memory.id])
    db_session.commit()
    db_session.refresh(memory)

    assert updated == 1
    assert memory.injected_count == 1
    assert memory.last_injected_at is not None


def test_group_memory_injection_preview_mode_reports_without_context(db_session):
    from app.group_memory.injection_service import GroupMemoryInjectionService
    from core.database import ChatStreamConfig, GroupMemory

    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:1097666427:group",
        group_profile_mode="preview",
    ))
    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="style",
        content="群风格: 讨论问题时喜欢直接指出不合理处",
        content_hash="gm-preview-style",
        confidence=0.90,
        evidence_count=3,
        evidence_log_ids_json="[3, 4, 5]",
        decay_score=1.0,
        status="active",
        last_seen=_local_now(),
    ))
    db_session.commit()

    result = GroupMemoryInjectionService(db_session).build_context(
        group_id="1097666427",
        current_user_input="这个方案哪里不合理？",
        recent_messages=[],
    )

    assert result.context == ""
    assert result.debug["group_profile_mode"] == "preview"
    assert result.debug["group_memory_injected"] is False
    assert result.selected_ids
    assert result.debug["group_memory_ids"] == result.selected_ids


def test_group_memory_retrieval_skips_manual_policy_and_low_relevance(db_session):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService
    from core.database import GroupMemory

    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="topic",
        content="模型部署: 群里经常讨论本地模型部署和量化",
        content_hash="gm-retrieval-topic",
        confidence=0.88,
        evidence_count=3,
        evidence_log_ids_json="[1, 2, 3]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        last_seen=_local_now(),
    ))
    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="relationship",
        content="Alice 和 Bob 经常互相开玩笑",
        content_hash="gm-retrieval-manual",
        confidence=0.92,
        evidence_count=4,
        evidence_log_ids_json="[4, 5, 6, 7]",
        decay_score=1.0,
        status="active",
        inject_policy="manual_only",
        last_seen=_local_now(),
    ))
    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="event",
        content="上周讨论过一次演唱会门票",
        content_hash="gm-retrieval-event",
        confidence=0.80,
        evidence_count=2,
        evidence_log_ids_json="[8, 9]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        last_seen=_local_now(),
    ))
    db_session.commit()

    result = GroupMemoryRetrievalService(db_session).select(
        group_id="1097666427",
        current_user_input="本地模型部署怎么做量化？",
        recent_messages=[],
        max_items=10,
        max_chars=1200,
    )

    assert [m.id for m in result.selected] == [1]
    skipped = {item["id"]: item["reason"] for item in result.skipped}
    assert skipped[2] == "manual_only"
    assert skipped[3] == "low_relevance"
    assert result.score_components["1"]["final"] > 0
    assert result.score_components["2"]["skip_reason"] == "manual_only"
    assert result.score_components["3"]["skip_reason"] == "low_relevance"
    assert result.score_components["3"]["relevance"] == 0.0


def test_group_memory_retrieval_budget_includes_rendering_overhead(db_session):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService
    from app.group_memory.renderer import render_group_memory_context
    from core.database import GroupMemory

    for idx in range(1, 6):
        db_session.add(GroupMemory(
            group_id="group_1097666427",
            memory_type="topic",
            content=f"模型部署记忆{idx}: 这个群经常讨论本地模型部署和量化参数",
            content_hash=f"gm-budget-{idx}",
            confidence=0.88,
            evidence_count=3,
            evidence_log_ids_json="[1, 2, 3]",
            decay_score=1.0,
            status="active",
            inject_policy="auto",
            last_seen=_local_now(),
        ))
    db_session.commit()

    result = GroupMemoryRetrievalService(db_session).select(
        group_id="1097666427",
        current_user_input="本地模型部署和量化参数怎么调？",
        recent_messages=[],
        max_items=10,
        max_chars=260,
    )
    context = render_group_memory_context("group_1097666427", result.selected)

    assert len(context) <= 260
    assert any(item["reason"] == "over_budget" for item in result.skipped)


def test_group_memory_overview_recent_injected_ids_sort_by_injected_at(db_session):
    from app.group_memory.extraction_service import build_group_memory_overview
    from core.database import GroupMemory

    now = _local_now()
    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="topic",
        content="较新的 last_seen 但更早注入",
        content_hash="gm-overview-older-inject",
        confidence=0.86,
        evidence_count=2,
        evidence_log_ids_json="[1, 2]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        last_seen=now,
        last_injected_at=now - timedelta(hours=2),
        injected_count=1,
    ))
    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="topic",
        content="较旧的 last_seen 但刚注入",
        content_hash="gm-overview-newer-inject",
        confidence=0.86,
        evidence_count=2,
        evidence_log_ids_json="[3, 4]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        last_seen=now - timedelta(days=7),
        last_injected_at=now,
        injected_count=1,
    ))
    db_session.commit()

    overview = build_group_memory_overview(db_session)
    item = next(row for row in overview if row["group_id"] == "group_1097666427")

    assert item["recent_injected_ids"] == [2, 1]
