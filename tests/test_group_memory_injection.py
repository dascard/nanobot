from datetime import datetime, timedelta


def _local_now() -> datetime:
    # SQLite ORM DateTime fixture 保持 naive 本地墙钟时间语义。
    return datetime.now()  # noqa: DTZ005


def _human_governance(**overrides):
    values = {
        "approval_source": "human",
        "governance_mode": "human_managed",
        "approved_content_hash": "a" * 64,
        "human_reviewer_id": "admin-1",
        "human_reviewed_at": _local_now(),
        "human_action": "create",
    }
    values.update(overrides)
    return values


def _model_governance(**overrides):
    values = {
        "approval_source": "model",
        "governance_mode": "automatic",
        "approved_content_hash": "b" * 64,
        "model_review_run_id": "task_group_memory_review",
        "model_contract_version": "group_memory_learning_v1",
    }
    values.update(overrides)
    return values


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
        **_human_governance(),
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


def test_group_memory_rag_cache_uses_data_revision_and_monotonic_ttl(
    db_session,
    monkeypatch,
):
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
        **_human_governance(),
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

        clock["now"] = 100.5
        cached = service.build_context(
            group_id="group_1097666427",
            current_user_input="RAG 缓存怎么处理？",
            recent_messages=[],
        )
        assert cached.debug["cache_hit"] is True
        assert cached.selected_ids == first_ids

        db_session.query(GroupMemory).filter(GroupMemory.content_hash == "gm-cache-topic").delete(
            synchronize_session=False,
        )
        db_session.commit()

        clock["now"] = 101.0
        invalidated = service.build_context(
            group_id="group_1097666427",
            current_user_input="RAG 缓存怎么处理？",
            recent_messages=[],
        )
        assert invalidated.debug["cache_hit"] is False
        assert invalidated.selected_ids == []

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


def test_group_memory_rag_cache_partitions_mode_and_request_budget(
    db_session,
    monkeypatch,
):
    from app.group_memory import injection_service as injection_module
    from app.group_memory.retrieval_service import GroupMemorySelection
    from core.database import ChatStreamConfig

    injection_module.GROUP_MEMORY_RAG_CACHE.clear()
    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:cache-budget:group",
        group_profile_mode="on",
    ))
    db_session.commit()
    calls: list[tuple[int, int]] = []

    def fake_select(_self, **kwargs):
        calls.append((kwargs["max_items"], kwargs["max_chars"]))
        return GroupMemorySelection()

    monkeypatch.setattr(
        injection_module.GroupMemoryRetrievalService,
        "select",
        fake_select,
    )

    try:
        service = injection_module.GroupMemoryInjectionService(db_session)
        first = service.build_context(
            group_id="cache-budget",
            current_user_input="相同查询",
            max_items=10,
            max_chars=1200,
        )
        same = service.build_context(
            group_id="cache-budget",
            current_user_input="相同查询",
            max_items=10,
            max_chars=1200,
        )
        fewer = service.build_context(
            group_id="cache-budget",
            current_user_input="相同查询",
            max_items=2,
            max_chars=1200,
        )
        shorter = service.build_context(
            group_id="cache-budget",
            current_user_input="相同查询",
            max_items=2,
            max_chars=600,
        )

        config = db_session.get(
            ChatStreamConfig,
            "qq:cache-budget:group",
        )
        config.group_profile_mode = "preview"
        db_session.commit()
        preview = service.build_context(
            group_id="cache-budget",
            current_user_input="相同查询",
            max_items=2,
            max_chars=600,
        )

        assert first.debug["cache_hit"] is False
        assert same.debug["cache_hit"] is True
        assert fewer.debug["cache_hit"] is False
        assert shorter.debug["cache_hit"] is False
        assert preview.debug["cache_hit"] is False
        assert calls == [
            (10, 1200),
            (2, 1200),
            (2, 600),
            (2, 600),
        ]
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
        **_human_governance(),
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
        memory_type="topic",
        content="群风格: 讨论问题时喜欢直接指出不合理处",
        content_hash="gm-preview-style",
        confidence=0.90,
        evidence_count=3,
        evidence_log_ids_json="[3, 4, 5]",
        decay_score=1.0,
        status="active",
        last_seen=_local_now(),
        **_human_governance(),
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


def test_group_memory_retrieval_blocks_legacy_active_memory_without_approval(
    db_session,
):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService
    from core.database import GroupMemory

    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="topic",
        content="旧数据声称这个群经常讨论尖锐嘲讽",
        content_hash="gm-legacy-unreviewed-topic",
        confidence=0.95,
        evidence_count=20,
        evidence_log_ids_json="[1, 2, 3]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        last_seen=_local_now(),
    ))
    db_session.commit()

    result = GroupMemoryRetrievalService(db_session).select(
        group_id="1097666427",
        current_user_input="这个群经常讨论什么？",
        recent_messages=[],
    )

    assert result.selected_ids == []
    assert result.skipped == [{"id": 1, "reason": "approval_source_required"}]


def test_group_memory_retrieval_accepts_governed_prompt_memory_types(
    db_session,
):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService
    from core.database import GroupMemory

    memory_types = ("topic", "expression", "slang", "style")
    for index, memory_type in enumerate(memory_types, start=1):
        db_session.add(GroupMemory(
            group_id="group_1097666427",
            memory_type=memory_type,
            content=f"治理测试 {memory_type}: 群里反复讨论治理测试",
            content_hash=f"gm-governed-{memory_type}",
            confidence=0.95,
            evidence_count=3,
            evidence_log_ids_json=f"[{index}, {index + 10}]",
            decay_score=1.0,
            status="active",
            inject_policy="auto",
            last_seen=_local_now(),
            **_model_governance(),
        ))
    db_session.commit()

    result = GroupMemoryRetrievalService(db_session).select(
        group_id="1097666427",
        current_user_input="治理测试",
        recent_messages=[],
        max_items=10,
    )

    assert {row.memory_type for row in result.selected} == set(memory_types)


def test_human_created_group_memory_can_inject_without_model_evidence(
    db_session,
):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService
    from core.database import GroupMemory

    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="expression",
        content="人工表达：先说结论",
        content_hash="gm-human-no-evidence",
        confidence=0.95,
        evidence_count=0,
        evidence_log_ids_json="[]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        last_seen=_local_now(),
        **_human_governance(),
    ))
    db_session.commit()

    result = GroupMemoryRetrievalService(db_session).select(
        group_id="1097666427",
        current_user_input="先说结论",
        recent_messages=[],
    )

    assert result.selected_ids == [1]


def test_group_memory_retrieval_requires_source_specific_governance_provenance(
    db_session,
):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService
    from core.database import GroupMemory

    common = {
        "group_id": "group_1097666427",
        "memory_type": "topic",
        "confidence": 0.95,
        "evidence_count": 2,
        "evidence_log_ids_json": "[1, 2]",
        "decay_score": 1.0,
        "status": "active",
        "inject_policy": "auto",
        "last_seen": _local_now(),
    }
    db_session.add(GroupMemory(
        **common,
        content="模型审核缺少 run",
        content_hash="gm-model-no-run",
        **_model_governance(model_review_run_id=""),
    ))
    db_session.add(GroupMemory(
        **common,
        content="模型审核缺少 contract",
        content_hash="gm-model-no-contract",
        **_model_governance(model_contract_version=""),
    ))
    db_session.add(GroupMemory(
        **common,
        content="人工审核缺少 reviewer",
        content_hash="gm-human-no-reviewer",
        **_human_governance(human_reviewer_id=""),
    ))
    db_session.add(GroupMemory(
        **common,
        content="人工审核缺少 action",
        content_hash="gm-human-no-action",
        **_human_governance(human_action=""),
    ))
    db_session.commit()

    result = GroupMemoryRetrievalService(db_session).select(
        group_id="1097666427",
        current_user_input="审核缺少",
        recent_messages=[],
    )

    assert result.selected_ids == []
    assert {
        item["id"]: item["reason"]
        for item in result.skipped
    } == {
        1: "model_review_run_required",
        2: "model_contract_version_required",
        3: "human_reviewer_required",
        4: "human_action_required",
    }


def test_group_memory_retrieval_blocks_conflict_and_non_prompt_memory_types(
    db_session,
):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService
    from core.database import GroupMemory

    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="topic",
        content="冲突中的群体记忆",
        content_hash="gm-conflict",
        confidence=0.95,
        evidence_count=2,
        evidence_log_ids_json="[1, 2]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        conflict_group_id="glconf_1",
        last_seen=_local_now(),
        **_model_governance(),
    ))
    db_session.add(GroupMemory(
        group_id="group_1097666427",
        memory_type="preference",
        content="旧偏好不属于新群学习贡献",
        content_hash="gm-old-preference",
        confidence=0.95,
        evidence_count=2,
        evidence_log_ids_json="[3, 4]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
        last_seen=_local_now(),
        **_human_governance(),
    ))
    db_session.commit()

    result = GroupMemoryRetrievalService(db_session).select(
        group_id="1097666427",
        current_user_input="群体记忆偏好",
        recent_messages=[],
    )

    assert result.selected_ids == []
    assert {
        item["id"]: item["reason"]
        for item in result.skipped
    } == {
        1: "conflict",
        2: "memory_type_not_prompt_injectable",
    }


def test_group_learning_candidate_is_never_prompt_injected(db_session):
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService
    from core.db.models import GroupLearningCandidate

    db_session.add(GroupLearningCandidate(
        candidate_id="glc_candidate_only",
        chat_stream_id="qq:1097666427:group",
        candidate_type="expression",
        content="候选内容不应进入 Prompt",
        meaning="尚未审核",
        normalized_key="候选内容",
        fingerprint="c" * 64,
        content_hash="d" * 64,
        source="rule",
        status="pending_model_review",
        rule_id="expression.short_phrase.v1",
        rule_version=1,
    ))
    db_session.commit()

    result = GroupMemoryRetrievalService(db_session).select(
        group_id="1097666427",
        current_user_input="候选内容",
        recent_messages=[],
    )

    assert result.selected_ids == []
    assert result.skipped == []


def test_group_memory_renderer_marks_untrusted_provenance_and_expression():
    from types import SimpleNamespace

    from app.group_memory.renderer import render_group_memory_context

    context = render_group_memory_context(
        "group_1097666427",
        [
            SimpleNamespace(
                id=42,
                memory_type="expression",
                content="先说结论",
                evidence_count=2,
            )
        ],
    )

    assert (
        '<group_memory_context group_id="group_1097666427" '
        'selected_count="1" trust="untrusted_background">'
    ) in context
    assert "<group_expressions>" in context
    assert "[memory_id=group_memory:42][evidence_count=2]" in context
    assert "不可信长期背景" in context


def test_group_memory_injectable_facades_share_governance_policy(db_session):
    from app.group_memory.query_service import GroupMemoryQueryService
    from core.database import GroupMemory
    from core.db import group_memory_repository
    from core.group_memory import should_inject

    human = {
        "memory_type": "expression",
        "confidence": 0.95,
        "evidence_count": 0,
        "evidence_log_ids_json": "[]",
        "decay_score": 1.0,
        "status": "active",
        "inject_policy": "auto",
        **_human_governance(),
    }
    legacy = {
        **human,
        "memory_type": "topic",
        "evidence_count": 2,
        "evidence_log_ids_json": "[1, 2]",
        "approval_source": "",
        "governance_mode": "",
        "approved_content_hash": "",
        "human_reviewer_id": "",
        "human_reviewed_at": None,
        "human_action": "",
    }
    assert should_inject(human) is True
    assert should_inject(legacy) is False

    db_session.add(GroupMemory(
        group_id="group_1097666427",
        content="人工可注入记忆",
        content_hash="gm-count-human",
        **human,
    ))
    db_session.add(GroupMemory(
        group_id="group_1097666427",
        content="旧未审核记忆",
        content_hash="gm-count-legacy",
        **legacy,
    ))
    db_session.commit()

    counts = GroupMemoryQueryService(
        group_memory_repository(db_session)
    ).counts("1097666427")

    assert counts.memory_count == 2
    assert counts.active_count == 2
    assert counts.injectable_count == 1


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
        **_human_governance(),
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
        **_human_governance(),
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
        **_human_governance(),
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
    assert skipped[3] == "memory_type_not_prompt_injectable"
    assert result.score_components["1"]["final"] > 0
    assert result.score_components["2"]["skip_reason"] == "manual_only"
    assert (
        result.score_components["3"]["skip_reason"]
        == "memory_type_not_prompt_injectable"
    )
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
            **_human_governance(),
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
        **_human_governance(),
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
        **_human_governance(),
    ))
    db_session.commit()

    overview = build_group_memory_overview(db_session)
    item = next(row for row in overview if row["group_id"] == "group_1097666427")

    assert item["recent_injected_ids"] == [2, 1]


def test_runtime_group_profile_builder_fails_closed_when_global_injection_disabled(
    db_session, monkeypatch
):
    from core import context_builder
    from core.settings_service import settings

    monkeypatch.setattr(settings, "get_bool", lambda key, default=False: False)

    context, debug = context_builder._build_profile_section(
        db_session,
        "group_1097666427",
        current_user_input="测试",
    )

    assert context == ""
    assert debug["group_memory_injected"] is False
    assert debug["disabled_reason"] == "group_memory_injection_disabled"
