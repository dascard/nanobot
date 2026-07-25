"""阶段 7C：群分析共享处理链与正式记忆晋级测试。"""

from __future__ import annotations

CHAT_STREAM_ID = "qq:42:group"


def _request(
    *,
    aspects: tuple[str, ...],
    trigger: str = "schedule",
):
    from app.group_learning.candidate_service import (
        GroupLearningCandidateBatchRequest,
        GroupLearningMessage,
    )

    return GroupLearningCandidateBatchRequest(
        run_id=f"glr_pipeline_{trigger}_{aspects[0]}",
        idempotency_key=(
            f"group-learning:pipeline:{trigger}:{aspects[0]}"
        ),
        chat_stream_id=CHAT_STREAM_ID,
        trigger=trigger,
        aspects=aspects,
        cursor_start_chat_log_id=100,
        cursor_end_chat_log_id=103,
        context_start_chat_log_id=90,
        context_end_chat_log_id=99,
        messages=(
            GroupLearningMessage(
                chat_log_id=99,
                sender_id="context-user",
                content="上一批只作为上下文的消息",
                context_only=True,
            ),
            GroupLearningMessage(
                chat_log_id=101,
                sender_id="u1",
                content="我们把短暂休息叫做摸鱼",
            ),
            GroupLearningMessage(
                chat_log_id=102,
                sender_id="u2",
                content="今天也想摸鱼一会儿",
            ),
            GroupLearningMessage(
                chat_log_id=103,
                sender_id="u3",
                content="大家继续讨论部署方案",
            ),
        ),
    )


def _task_result(
    parsed_value,
    *,
    run_id: str = "task_group_learning_1",
    contract_version: str = "group_memory_learning_v1",
    route_key: str = "group_memory_learning",
    failure=None,
):
    from core.task_runtime import TaskResult

    return TaskResult(
        parsed_value=parsed_value,
        contract_version=contract_version,
        route_key=route_key,
        provider="test-provider",
        model="test-model",
        attempt_count=1,
        latency_ms=25,
        failure=failure,
        raw_output_sha256="a" * 64,
        raw_output_bytes=128,
        validation_diagnostics=(),
        run_id=run_id,
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
        },
    )


def _topic_analysis():
    return {
        "topics": {
            "_generator": "llm",
            "_task_provenance": {
                "run_id": "task_topics_1",
                "contract_version": "group_analysis_topics_v1",
                "route_key": "group_analysis_topics",
                "provider": "test-provider",
                "model": "test-model",
                "attempt_count": 1,
                "latency_ms": 20,
                "raw_output_sha256": "b" * 64,
                "raw_output_bytes": 96,
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "total_tokens": 100,
                },
            },
            "topics": [
                {
                    "topic": "部署方案",
                    "detail": "群成员持续讨论服务部署",
                    "contributors": ["u1", "u2"],
                    "evidence_log_ids": [101, 102],
                }
            ],
        }
    }


def test_model_can_discover_candidate_when_regex_produces_none(
    db_session,
):
    from app.group_learning.pipeline_service import (
        build_group_learning_processor,
    )
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningStreamState,
        GroupMemory,
    )

    invocations = []

    def task_executor(invocation):
        invocations.append(invocation)
        return _task_result({
            "reviews": [],
            "discoveries": [
                {
                    "candidate_type": "slang",
                    "content": "摸鱼",
                    "meaning": "短暂休息",
                    "evidence_log_ids": [101, 102],
                    "reason": "两名成员在同一语境中使用",
                }
            ],
        })

    outcome = build_group_learning_processor(
        db_session,
        task_executor=task_executor,
        enabled=lambda: True,
    ).process(_request(aspects=("slang",)))

    assert outcome.status == "succeeded"
    assert len(invocations) == 1
    assert invocations[0].request_context[
        "allowed_candidate_ids"
    ] == ()
    candidate = db_session.query(GroupLearningCandidate).one()
    assert candidate.source == "model"
    assert candidate.model_decision == "new"
    assert candidate.model_review_run_id == "task_group_learning_1"
    memory = db_session.query(GroupMemory).one()
    assert memory.memory_type == "slang"
    assert memory.status == "active"
    state = db_session.get(
        GroupLearningStreamState,
        CHAT_STREAM_ID,
    )
    assert state.last_success_chat_log_id == 103


def test_model_failure_preserves_pending_and_does_not_advance_success(
    db_session,
):
    from app.group_learning.pipeline_service import (
        build_group_learning_processor,
    )
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningStreamState,
        GroupMemory,
    )
    from core.task_runtime import (
        TaskFailureCode,
        TaskFailureStage,
        TaskTerminalAction,
        TaskTypedFailure,
    )

    failure = TaskTypedFailure(
        code=TaskFailureCode.PROVIDER_UNAVAILABLE,
        stage=TaskFailureStage.PROVIDER,
        retryable=True,
        summary="Provider 暂不可用",
        terminal_action=TaskTerminalAction.PRESERVE_PENDING,
    )

    outcome = build_group_learning_processor(
        db_session,
        task_executor=lambda _invocation: _task_result(
            None,
            failure=failure,
        ),
        enabled=lambda: True,
    ).process(_request(aspects=("slang",)))

    assert outcome.status == "failed"
    assert outcome.error_code == "provider_unavailable"
    assert outcome.retryable is True
    assert db_session.query(GroupMemory).count() == 0
    assert db_session.query(GroupLearningCandidate).count() == 0
    state = db_session.get(
        GroupLearningStreamState,
        CHAT_STREAM_ID,
    )
    assert state is not None
    assert state.last_success_chat_log_id == 0


def test_topic_task_provenance_promotes_topic_without_second_model(
    db_session,
):
    from app.group_learning.pipeline_service import (
        build_group_learning_processor,
    )
    from core.db.models import GroupLearningCandidate, GroupMemory

    def forbidden_task(_invocation):
        raise AssertionError("预计算 topics 不得再调用第二个模型")

    processor = build_group_learning_processor(
        db_session,
        task_executor=forbidden_task,
        enabled=lambda: True,
    )

    outcome = processor.process_with_analysis(
        _request(aspects=("topics",)),
        analysis=_topic_analysis(),
    )

    assert outcome.status == "succeeded"
    candidate = db_session.query(GroupLearningCandidate).one()
    assert candidate.candidate_type == "topic"
    assert candidate.model_decision == "new"
    assert candidate.model_review_run_id == "task_topics_1"
    assert candidate.model_contract_version == (
        "group_analysis_topics_v1"
    )
    memory = db_session.query(GroupMemory).one()
    assert memory.memory_type == "topic"
    assert memory.model_review_run_id == "task_topics_1"


def test_pipeline_can_execute_topics_task_and_promote_result(db_session):
    from app.group_learning.pipeline_service import (
        build_group_learning_processor,
    )
    from core.db.models import GroupMemory

    invocations = []

    def task_executor(invocation):
        invocations.append(invocation)
        return _task_result(
            {
                "topics": [
                    {
                        "topic": "部署方案",
                        "detail": "群成员持续讨论服务部署",
                        "contributors": ["u1", "u2"],
                        "evidence_log_ids": [101, 102],
                    }
                ]
            },
            run_id="task_topics_execute",
            contract_version="group_analysis_topics_v1",
            route_key="group_analysis_topics",
        )

    outcome = build_group_learning_processor(
        db_session,
        task_executor=task_executor,
        enabled=lambda: True,
    ).process(_request(aspects=("topics",)))

    assert outcome.status == "succeeded"
    assert len(invocations) == 1
    assert invocations[0].route_key == "group_analysis_topics"
    assert invocations[0].request_context[
        "allowed_evidence_log_ids"
    ] == (101, 102, 103)
    memory = db_session.query(GroupMemory).one()
    assert memory.memory_type == "topic"
    assert memory.model_review_run_id == "task_topics_execute"


def test_pipeline_topics_task_failure_is_typed_and_does_not_promote(
    db_session,
):
    from app.group_learning.pipeline_service import (
        build_group_learning_processor,
    )
    from core.db.models import GroupLearningRun, GroupMemory
    from core.task_runtime import (
        TaskFailureCode,
        TaskFailureStage,
        TaskTerminalAction,
        TaskTypedFailure,
    )

    failure = TaskTypedFailure(
        code=TaskFailureCode.PROVIDER_UNAVAILABLE,
        stage=TaskFailureStage.PROVIDER,
        retryable=True,
        summary="Provider 暂不可用",
        terminal_action=TaskTerminalAction.PRESERVE_PENDING,
    )

    outcome = build_group_learning_processor(
        db_session,
        task_executor=lambda _invocation: _task_result(
            None,
            run_id="task_topics_failed",
            contract_version="group_analysis_topics_v1",
            route_key="group_analysis_topics",
            failure=failure,
        ),
        enabled=lambda: True,
    ).process(_request(aspects=("topics",)))

    assert outcome.status == "failed"
    assert outcome.error_code == "provider_unavailable"
    assert outcome.retryable is True
    assert db_session.query(GroupMemory).count() == 0
    run = db_session.get(
        GroupLearningRun,
        "glr_pipeline_schedule_topics",
    )
    assert run.status == "failed"
    assert run.error_code == "provider_unavailable"


def test_topic_without_task_provenance_never_activates(db_session):
    from app.group_learning.pipeline_service import (
        build_group_learning_processor,
    )
    from core.db.models import GroupMemory

    outcome = build_group_learning_processor(
        db_session,
        task_executor=lambda _invocation: _task_result({
            "reviews": [],
            "discoveries": [],
        }),
        enabled=lambda: True,
    ).process_with_analysis(
        _request(aspects=("topics",)),
        analysis={
            "topics": {
                "_generator": "llm",
                "topics": _topic_analysis()["topics"]["topics"],
            }
        },
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "topic_provenance_missing"
    assert db_session.query(GroupMemory).count() == 0


def test_report_only_aspects_create_no_memory_and_advance_schedule_cursor(
    db_session,
):
    from app.group_learning.pipeline_service import (
        build_group_learning_processor,
    )
    from core.db.models import (
        GroupLearningStreamState,
        GroupMemory,
    )

    outcome = build_group_learning_processor(
        db_session,
        task_executor=lambda _invocation: (_ for _ in ()).throw(
            AssertionError("报告-only 结算不得调用群学习模型")
        ),
        enabled=lambda: True,
    ).process_with_analysis(
        _request(aspects=("titles", "quotes", "quality")),
        analysis={
            "titles": {"users": []},
            "quotes": {"quotes": []},
            "quality": {"dimensions": [], "summary": ""},
        },
    )

    assert outcome.status == "succeeded"
    assert db_session.query(GroupMemory).count() == 0
    state = db_session.get(
        GroupLearningStreamState,
        CHAT_STREAM_ID,
    )
    assert state.last_success_chat_log_id == 103


def test_tool_trigger_never_advances_automatic_success_cursor(
    db_session,
):
    from app.group_learning.pipeline_service import (
        build_group_learning_processor,
    )
    from core.db.models import (
        GroupLearningStreamState,
        GroupMemory,
    )

    processor = build_group_learning_processor(
        db_session,
        task_executor=lambda _invocation: (_ for _ in ()).throw(
            AssertionError("预计算 topics 不得再调用模型")
        ),
        enabled=lambda: True,
    )

    outcome = processor.process_with_analysis(
        _request(aspects=("topics",), trigger="tool"),
        analysis=_topic_analysis(),
    )

    assert outcome.status == "succeeded"
    assert db_session.query(GroupMemory).count() == 1
    state = db_session.get(
        GroupLearningStreamState,
        CHAT_STREAM_ID,
    )
    assert state.last_success_chat_log_id == 0
