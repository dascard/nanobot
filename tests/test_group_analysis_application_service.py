"""阶段 7C：群分析 Tool／Scheduler 共享应用服务测试。"""

from __future__ import annotations

from tests.async_helpers import run_async


CHAT_STREAM_ID = "qq:42:group"


def _learning_request():
    from app.group_learning.candidate_service import (
        GroupLearningCandidateBatchRequest,
        GroupLearningMessage,
    )

    return GroupLearningCandidateBatchRequest(
        run_id="glr_shared_service",
        idempotency_key="group-learning:shared-service:100:103",
        chat_stream_id=CHAT_STREAM_ID,
        trigger="schedule",
        aspects=("topics", "slang"),
        cursor_start_chat_log_id=100,
        cursor_end_chat_log_id=103,
        context_start_chat_log_id=90,
        context_end_chat_log_id=99,
        messages=(
            GroupLearningMessage(
                chat_log_id=99,
                sender_id="context-user",
                content="上一批上下文",
                context_only=True,
            ),
            GroupLearningMessage(
                chat_log_id=101,
                sender_id="u1",
                content="我们把短暂休息叫摸鱼",
            ),
            GroupLearningMessage(
                chat_log_id=102,
                sender_id="u2",
                content="今天也想摸鱼一会儿",
            ),
            GroupLearningMessage(
                chat_log_id=103,
                sender_id="u3",
                content="继续讨论部署方案",
            ),
        ),
    )


def _analysis():
    return {
        "topics": {
            "_generator": "llm",
            "_task_provenance": {
                "run_id": "task_topics_shared",
                "contract_version": "group_analysis_topics_v1",
                "route_key": "group_analysis_topics",
                "provider": "test-provider",
                "model": "test-model",
                "attempt_count": 1,
                "latency_ms": 20,
                "raw_output_sha256": "b" * 64,
                "raw_output_bytes": 80,
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70,
                },
            },
            "topics": [],
        }
    }


def test_task_success_exposes_only_safe_provenance(monkeypatch):
    from app.group_analysis import analyzer
    from core.task_runtime import TaskResult

    monkeypatch.setattr(
        "core.task_runtime.execute_task",
        lambda _invocation: TaskResult(
            parsed_value={"topics": []},
            contract_version="group_analysis_topics_v1",
            route_key="group_analysis_topics",
            provider="test-provider",
            model="test-model",
            attempt_count=1,
            latency_ms=12,
            failure=None,
            raw_output_sha256="a" * 64,
            raw_output_bytes=48,
            validation_diagnostics=(),
            run_id="task_topics_provenance",
            usage={
                "prompt_tokens": 30,
                "completion_tokens": 5,
                "total_tokens": 35,
            },
        ),
    )

    result = run_async(analyzer._call_llm_with_retry(
        None,
        "system",
        "prompt",
        prompt_key="group_analysis_topics",
        prompt_vars={"allowed_evidence_log_ids": [101]},
    ))

    assert result["topics"] == []
    assert result["_task_provenance"] == {
        "run_id": "task_topics_provenance",
        "contract_version": "group_analysis_topics_v1",
        "route_key": "group_analysis_topics",
        "provider": "test-provider",
        "model": "test-model",
        "attempt_count": 1,
        "latency_ms": 12,
        "raw_output_sha256": "a" * 64,
        "raw_output_bytes": 48,
        "usage": {
            "prompt_tokens": 30,
            "completion_tokens": 5,
            "total_tokens": 35,
        },
    }
    assert "prompt" not in result["_task_provenance"]
    assert "raw_output" not in result["_task_provenance"]


def test_shared_service_analyzes_once_and_excludes_context_from_evidence():
    from app.group_analysis.application_service import (
        GroupAnalysisApplicationService,
    )
    from app.group_learning.scheduler import (
        GroupLearningProcessingOutcome,
    )

    captured = {}

    async def analyze(payload, instructions, *, aspects):
        captured["payload"] = payload
        captured["instructions"] = instructions
        captured["aspects"] = aspects
        return _analysis()

    class Pipeline:
        def process_with_analysis(self, request, *, analysis):
            captured["request"] = request
            captured["analysis"] = analysis
            return GroupLearningProcessingOutcome.succeeded(
                run_id=request.run_id
            )

    result = run_async(
        GroupAnalysisApplicationService(
            learning_pipeline=Pipeline(),
            analyzer=analyze,
        ).process_learning_batch(
            _learning_request(),
            group_name="测试群",
            instructions="关注技术讨论",
        )
    )

    assert captured["aspects"] == ("topics", "slang")
    assert captured["instructions"] == "关注技术讨论"
    assert captured["payload"]["source_log_ids"] == [
        99,
        101,
        102,
        103,
    ]
    assert captured["payload"]["trusted_source_log_ids"] == [
        101,
        102,
        103,
    ]
    assert captured["payload"]["trusted_source_speakers"] == {
        "101": "u1",
        "102": "u2",
        "103": "u3",
    }
    assert captured["analysis"] == _analysis()
    assert result.learning_outcome.status == "succeeded"
    assert result.analysis == _analysis()
    assert "话题总结" in result.report
    assert "群友画像" not in result.report


def test_scheduler_adapter_calls_shared_application_service_once():
    from app.group_analysis.application_service import (
        GroupAnalysisScheduleProcessor,
    )
    from app.group_learning.scheduler import (
        GroupLearningProcessingOutcome,
    )

    calls = []

    class Service:
        async def process_learning_batch(self, request):
            calls.append(request)

            class Result:
                learning_outcome = (
                    GroupLearningProcessingOutcome.succeeded(
                        run_id=request.run_id
                    )
                )

            return Result()

    outcome = GroupAnalysisScheduleProcessor(Service()).process(
        _learning_request()
    )

    assert outcome.status == "succeeded"
    assert calls == [_learning_request()]


def test_tool_learning_request_uses_only_trusted_messages_and_is_stable():
    from app.group_analysis.application_service import (
        build_group_analysis_learning_request,
    )

    payload = {
        "messages": [
            {
                "log_id": 101,
                "speaker_id": "u1",
                "content": "今天讨论部署方案",
                "memory_evidence_trusted": True,
            },
            {
                "log_id": 102,
                "speaker_id": "bot",
                "content": "机器人转发内容",
                "memory_evidence_trusted": False,
            },
            {
                "log_id": 103,
                "speaker_id": "u2",
                "content": "摸鱼就是短暂休息",
                "memory_evidence_trusted": True,
            },
        ],
    }

    first = build_group_analysis_learning_request(
        chat_stream_id=CHAT_STREAM_ID,
        aspects=("topics", "slang"),
        payload=payload,
        trigger="tool",
        cursor_start_chat_log_id=0,
        cursor_end_chat_log_id=103,
    )
    second = build_group_analysis_learning_request(
        chat_stream_id=CHAT_STREAM_ID,
        aspects=("topics", "slang"),
        payload=payload,
        trigger="tool",
        cursor_start_chat_log_id=0,
        cursor_end_chat_log_id=103,
    )

    assert first is not None
    assert second is not None
    assert first.run_id == second.run_id
    assert first.idempotency_key == second.idempotency_key
    assert first.chat_stream_id == CHAT_STREAM_ID
    assert first.trigger == "tool"
    assert tuple(
        message.chat_log_id for message in first.messages
    ) == (101, 103)
    assert tuple(
        message.sender_id for message in first.messages
    ) == ("u1", "u2")


def test_tool_learning_request_accepts_500_and_skips_501_without_raising():
    from app.group_analysis.application_service import (
        build_group_analysis_learning_request,
    )
    from app.group_learning.candidate_service import MAX_BATCH_MESSAGES

    messages = [
        {
            "log_id": index,
            "speaker_id": f"u{index}",
            "content": "有效群聊消息",
            "memory_evidence_trusted": True,
        }
        for index in range(1, MAX_BATCH_MESSAGES + 2)
    ]
    at_limit = build_group_analysis_learning_request(
        chat_stream_id=CHAT_STREAM_ID,
        aspects=("topics",),
        payload={"messages": messages[:MAX_BATCH_MESSAGES]},
        trigger="tool",
        cursor_start_chat_log_id=0,
        cursor_end_chat_log_id=MAX_BATCH_MESSAGES,
    )
    over_limit = build_group_analysis_learning_request(
        chat_stream_id=CHAT_STREAM_ID,
        aspects=("topics",),
        payload={"messages": messages},
        trigger="tool",
        cursor_start_chat_log_id=0,
        cursor_end_chat_log_id=MAX_BATCH_MESSAGES + 1,
    )

    assert at_limit is not None
    assert len(at_limit.messages) == MAX_BATCH_MESSAGES
    assert over_limit is None


def test_tool_learning_request_skips_character_overflow_without_raising():
    from app.group_analysis.application_service import (
        build_group_analysis_learning_request,
    )
    from app.group_learning.candidate_service import MAX_BATCH_CHARS

    content = "群" * 2000
    payload = {
        "messages": [
            {
                "log_id": index,
                "speaker_id": f"u{index}",
                "content": content,
                "memory_evidence_trusted": True,
            }
            for index in range(1, MAX_BATCH_CHARS // len(content) + 2)
        ],
    }

    request = build_group_analysis_learning_request(
        chat_stream_id=CHAT_STREAM_ID,
        aspects=("topics",),
        payload=payload,
        trigger="tool",
        cursor_start_chat_log_id=0,
        cursor_end_chat_log_id=len(payload["messages"]),
    )

    assert request is None


def test_shared_service_can_render_report_without_learning_evidence():
    from app.group_analysis.application_service import (
        GroupAnalysisApplicationService,
    )

    pipeline_called = False

    async def analyze(_payload, _instructions, *, aspects):
        assert aspects == ("quality",)
        return {
                "quality": {
                    "title": "质量在线",
                    "subtitle": "",
                    "dimensions": [{
                        "name": "信息密度",
                        "percentage": 80,
                        "comment": "讨论集中",
                    }],
                    "summary": "只有报告，不产生长期记忆。",
                }
        }

    class Pipeline:
        def process_with_analysis(self, *_args, **_kwargs):
            nonlocal pipeline_called
            pipeline_called = True
            raise AssertionError("没有可信 Evidence 时不得调用学习 Pipeline")

    result = run_async(
        GroupAnalysisApplicationService(
            learning_pipeline=Pipeline(),
            analyzer=analyze,
        ).process_payload(
            aspects=("quality",),
            payload={"group_stats": {}},
            group_name="测试群",
            learning_request=None,
        )
    )

    assert result.learning_outcome is None
    assert pipeline_called is False
    assert "聊天质量锐评" in result.report
