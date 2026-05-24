from datetime import datetime


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
        last_seen=datetime.now(),
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
        last_seen=datetime.now(),
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
        last_seen=datetime.now(),
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
        last_seen=datetime.now(),
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
        last_seen=datetime.now(),
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
