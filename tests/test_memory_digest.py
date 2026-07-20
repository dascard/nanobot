from tests.async_helpers import run_async
import json
from datetime import datetime

import pytest

from core.database import ChatLog, MemoryDigest, RollingSessionSummary


def _db_time(year: int, month: int, day: int, hour: int, minute: int, second: int) -> datetime:
    # SQLite ORM DateTime fixture 保持 naive 本地墙钟时间语义。
    return datetime(year, month, day, hour, minute, second)  # noqa: DTZ001


def _log(**kwargs):
    defaults = {
        "user_id": "group_42",
        "session_id": "group_42",
        "role": "ambient",
        "sender_name": "甲",
        "content": "KohakuVQ 这个 VQ codebook 的 usage 很高，图像生成效果也不错",
        "created_at": _db_time(2026, 5, 22, 12, 0, 0),
    }
    defaults.update(kwargs)
    return ChatLog(**defaults)


def _add_digest(db, *, digest_id: int | None = None, status="active", schema_version=2, content=""):
    meta = {
        "schema_version": schema_version,
        "status": status,
        "generator": "llm",
        "llm_status": "success",
        "preview": {
            "brief": "群里讨论了 KohakuVQ 和 Discrete AR 图像生成。",
            "keywords": ["KohakuVQ", "Discrete AR", "图像生成"],
            "participants": ["甲"],
        },
        "long_summary": {
            "topic_flow": "群友围绕 KohakuVQ 的 codebook usage 和图像生成效果展开讨论。",
            "important_details": ["提到 VQ codebook usage 较高"],
            "conclusions": [],
            "open_loops": [],
        },
        "recall_cards": [
            {
                "card_id": "card_1",
                "type": "episode_topic",
                "text": "群里讨论过 KohakuVQ 和 Discrete AR 图像生成技术。",
                "keywords": ["KohakuVQ", "Discrete AR"],
                "importance": 0.8,
                "evidence_log_ids": [1],
            }
        ],
        "quality": {"score": 0.9, "issues": [], "should_inject_preview": True},
    }
    row = MemoryDigest(
        id=digest_id,
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        level=2,
        content=content or "[card] KohakuVQ / Discrete AR：群里讨论过图像生成技术。",
        meta_json=json.dumps(meta, ensure_ascii=False),
        source_start_log_id=1,
        source_end_log_id=3,
    )
    db.add(row)
    db.commit()
    return row


def test_memory_digest_builder_generates_schema_v2_cards_and_filters_noise():
    from app.memory_digest.builder import MemoryDigestBuilder

    logs = [
        _log(id=1, sender_name="甲", content="KohakuVQ 这个 VQ codebook 的 usage 很高"),
        _log(id=2, sender_name="乙", content="Discrete AR 图像生成效果看起来不错"),
        _log(id=3, sender_name="机器人", content="签到"),
        _log(id=4, sender_name="丙", content="[图片:1张]"),
    ]

    result = MemoryDigestBuilder().build(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=logs,
    )

    assert result.status == "active"
    assert result.meta["schema_version"] == 2
    assert result.meta["source_stats"]["raw_log_count"] == 4
    assert result.meta["source_stats"]["valid_log_count"] == 2
    assert result.meta["source_stats"]["filtered_by_reason"]["bot_command"] == 1
    assert result.meta["source_stats"]["filtered_by_reason"]["image_placeholder"] == 1
    assert result.level_contents[2].startswith("[card]")
    assert "KohakuVQ" in result.level_contents[2]
    assert result.meta["recall_cards"][0]["evidence_log_ids"]
    assert "[System: Older context truncated" not in result.level_contents[0]


def test_memory_digest_recall_cards_stay_compact():
    from app.memory_digest.builder import MemoryDigestBuilder

    result = MemoryDigestBuilder().build(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(
                id=1,
                content=(
                    "KohakuVQ Discrete AR 图像生成 codebook usage reconstruction quality "
                    "这个话题聊得很细，还比较了训练成本、推理速度和不同模型结构"
                ),
            ),
            _log(id=2, sender_name="乙", content="还提到了 token budget 和工程实现方式"),
        ],
    )

    assert all(len(card["text"]) <= 160 for card in result.meta["recall_cards"])
    assert all(len(line) <= 120 for line in result.level_contents[2].splitlines() if line)


def test_memory_digest_builder_skips_when_only_noise():
    from app.memory_digest.builder import MemoryDigestBuilder

    logs = [
        _log(id=1, sender_name="机器人", content="签到"),
        _log(id=2, sender_name="机器人", content="[图片:1张]"),
        _log(id=3, role="tool", sender_name="tool", content="internal tool output"),
    ]

    result = MemoryDigestBuilder().build(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=logs,
    )

    assert result.status == "skipped"
    assert result.meta["quality"]["should_inject_preview"] is False
    assert result.level_contents[2] == ""


@pytest.mark.parametrize("moderation_flag", ["no_learn", "no_context"])
def test_memory_digest_builder_excludes_moderation_blocked_messages(moderation_flag):
    from app.memory_digest.builder import MemoryDigestBuilder

    blocked = _log(
        id=1,
        content="这条敏感内容不得进入长期摘要",
        meta_json=json.dumps({"moderation": {moderation_flag: True}}),
    )
    allowed = _log(id=2, content="这条正常内容可以进入长期摘要")

    result = MemoryDigestBuilder().build(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[blocked, allowed],
    )

    assert result.status == "active"
    assert result.meta["source_stats"]["filtered_by_reason"]["meta_flag"] == 1
    assert "敏感内容" not in result.level_contents[0]
    assert "正常内容" in result.level_contents[0]


def test_memory_digest_quality_requires_threshold_and_clean_issues():
    from app.memory_digest.quality import build_quality

    assert build_quality(score=0.69, issues=[], should_inject_preview=True)["should_inject_preview"] is False
    assert build_quality(score=0.9, issues=["json_parse_failed"], should_inject_preview=True)["should_inject_preview"] is False
    assert build_quality(score=0.7, issues=[], should_inject_preview=True)["should_inject_preview"] is True


def test_memory_digest_prompt_v2_templates_load_without_hardcoded_main_prompt():
    from app.memory_digest.llm_builder import build_llm_digest_messages
    from app.memory_digest.builder import MemoryDigestBuilder

    fallback = MemoryDigestBuilder().build(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[_log(id=1, content="长期摘要 prompt 应迁移到 Prompt V2 模板体系")],
    )

    messages, prompt_meta = build_llm_digest_messages(
        session_id="group_42",
        digest_date="2026-05-22",
        fallback=fallback,
        source_rows=[
            {
                "log_id": 1,
                "line": "[log_id=1][12:00] 甲: 长期摘要 prompt 应迁移到 Prompt V2 模板体系",
            }
        ],
    )

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "three-level memory digest" in messages[0]["content"]
    assert "validator requires lexical grounding" in messages[0]["content"]
    assert "must occur verbatim in the cited evidence" in messages[1]["content"]
    assert "{{" not in messages[1]["content"]
    assert prompt_meta["template"] == "tasks/memory_digest_system + tasks/memory_digest_user"
    assert prompt_meta["system_source"] in {"default", "runtime"}


def test_memory_digest_prompt_v2_missing_template_fails_closed(
    tmp_path,
    monkeypatch,
):
    from app.memory_digest.llm_builder import build_llm_digest_messages
    from app.memory_digest.builder import MemoryDigestBuilder
    from core.prompt_v2.task_templates import TaskTemplateUnavailableError

    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(tmp_path / "default"))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(tmp_path / "runtime"))

    fallback = MemoryDigestBuilder().build(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[_log(id=1, content="模板缺失时不能让 daily digest 崩溃")],
    )

    with pytest.raises(TaskTemplateUnavailableError):
        build_llm_digest_messages(
            session_id="group_42",
            digest_date="2026-05-22",
            fallback=fallback,
            source_rows=[
                {
                    "log_id": 1,
                    "line": "[log_id=1][12:00] 甲: 模板缺失时不能继续调用模型",
                }
            ],
        )


def test_llm_memory_digest_builder_promotes_clean_llm_summary():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    def fake_summarizer(messages):
        assert messages[0]["role"] == "system"
        assert "Cleaned digest_source" in messages[1]["content"]
        return json.dumps(
            {
                "preview": {
                    "brief": "群里认真讨论了 PCL、pagefile 和虚拟内存交换原理。",
                    "keywords": ["PCL", "pagefile", "虚拟内存"],
                    "participants": ["甲", "乙"],
                },
                "long_summary": {
                    "topic_flow": "讨论从 PCL 的原理问题展开，延伸到 pagefile、缺页中断和虚拟内存交换。",
                    "important_details": [
                        "有人追问 PCL 的实现原理。",
                        "群友解释它可能通过申请大量内存触发系统 pagefile 交换。",
                    ],
                    "conclusions": ["PCL 话题与虚拟内存/pagefile 机制有关。"],
                    "open_loops": [],
                },
                "recall_cards": [
                    {
                        "card_id": "card_1",
                        "type": "episode_topic",
                        "text": "群里讨论过 PCL 与 pagefile、虚拟内存交换的关系。",
                        "keywords": ["PCL", "pagefile", "虚拟内存"],
                        "importance": 0.86,
                        "evidence_log_ids": [1, 2],
                    }
                ],
                "quality": {"score": 0.9, "issues": []},
            },
            ensure_ascii=False,
        )

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=1, sender_name="甲", content="PCL这是什么原理"),
            _log(id=2, sender_name="乙", content="好像是申请一大堆内存然后触发系统转储虚拟内存吧"),
        ],
        summarizer=fake_summarizer,
    )

    assert result.status == "active"
    assert result.meta["generator"] == "llm"
    assert result.meta["llm_status"] == "success"
    assert result.meta["llm_model"] == "custom_summarizer"
    assert result.meta["quality"]["should_inject_preview"] is True
    assert "PCL 与 pagefile" in result.level_contents[2]


def test_llm_digest_collects_all_valid_source_rows_instead_of_first_eighty():
    from app.memory_digest.llm_builder import _collect_source_rows, _source_id

    logs = [
        _log(id=index, content=f"第 {index} 条长期有效消息，讨论 memory digest 批处理")
        for index in range(1, 102)
    ]

    rows = _collect_source_rows(logs)

    assert len(rows) == 101
    assert rows[-1]["log_id"] == 101
    full_id = _source_id(session_id="group_42", digest_date="2026-05-22", source_rows=rows)
    truncated_id = _source_id(session_id="group_42", digest_date="2026-05-22", source_rows=rows[:80])
    assert full_id != truncated_id


def test_llm_digest_summarizes_all_source_rows_in_audited_batches():
    import re

    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    calls: list[list[int]] = []

    def summarizer(messages):
        prompt = messages[-1]["content"]
        ids = [int(item) for item in re.findall(r"\[log_id=(\d+)\]", prompt)]
        calls.append(ids)
        first_id = ids[0]
        return json.dumps({
            "preview": {"brief": f"批次 {len(calls)} 摘要", "keywords": [f"批次{first_id}"]},
            "long_summary": {"topic_flow": f"本批覆盖从批次{first_id}开始的长期摘要消息。"},
            "recall_cards": [{
                "card_id": "card_1",
                "type": "fact",
                "text": f"批次{first_id}包含长期摘要分批处理事实。",
                "keywords": [f"批次{first_id}", "长期摘要"],
                "importance": 0.8,
                "evidence_log_ids": [first_id],
            }],
            "quality": {"score": 0.9, "issues": []},
        }, ensure_ascii=False)

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=index, content=f"批次{index}包含长期摘要分批处理事实")
            for index in range(1, 162)
        ],
        summarizer=summarizer,
    )

    assert result.status == "active"
    assert len(calls) == 3
    assert [len(batch) for batch in calls] == [80, 80, 1]
    assert result.meta["message_count"] == 161
    assert result.meta["batch_count"] == 3
    assert result.meta["source_range"] == "log_id 1-161"
    assert {card["evidence_log_ids"][0] for card in result.meta["recall_cards"]} == {1, 81, 161}


def test_async_llm_digest_heartbeats_before_and_after_every_batch():
    import re

    from app.memory_digest.llm_builder import build_memory_digest_with_llm_async

    heartbeat_calls: list[int] = []
    batch_calls: list[list[int]] = []

    async def heartbeat():
        heartbeat_calls.append(len(batch_calls))

    async def summarizer(messages):
        ids = [
            int(item)
            for item in re.findall(r"\[log_id=(\d+)\]", messages[-1]["content"])
        ]
        batch_calls.append(ids)
        first_id = ids[0]
        return json.dumps({
            "preview": {"brief": f"批次 {len(batch_calls)} 摘要", "keywords": [f"批次{first_id}"]},
            "long_summary": {"topic_flow": f"本批覆盖从批次{first_id}开始的长期摘要消息。"},
            "recall_cards": [{
                "card_id": "card_1",
                "type": "fact",
                "text": f"批次{first_id}包含长期摘要分批处理事实。",
                "keywords": [f"批次{first_id}", "长期摘要"],
                "importance": 0.8,
                "evidence_log_ids": [first_id],
            }],
            "quality": {"score": 0.9, "issues": []},
        }, ensure_ascii=False)

    result = run_async(build_memory_digest_with_llm_async(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=index, content=f"批次{index}包含长期摘要分批处理事实")
            for index in range(1, 82)
        ],
        summarizer=summarizer,
        heartbeat=heartbeat,
    ))

    assert result.status == "active"
    assert [len(batch) for batch in batch_calls] == [80, 1]
    assert len(heartbeat_calls) == 4


def test_async_llm_digest_keeps_all_request_audit_when_final_merge_fails(
    monkeypatch,
):
    from app.memory_digest import llm_builder
    from app.memory_digest.builder import MemoryDigestBuildResult

    calls = 0

    async def summarizer(_messages):
        nonlocal calls
        calls += 1
        return llm_builder.MemoryDigestLlmOutput(
            content="{}",
            model=f"actual-model-{calls}",
            requested_model="requested-model",
            request_log_id=100 + calls,
            actual_model_observed=True,
        )

    monkeypatch.setattr(
        llm_builder,
        "_build_memory_digest_result_from_raw",
        lambda *_args, **_kwargs: MemoryDigestBuildResult(
            status="active",
            meta={
                "generator": "llm",
                "llm_status": "success",
                "quality": {"score": 0.9, "issues": []},
            },
            level_contents={0: "详细", 1: "预览", 2: "召回"},
        ),
    )
    monkeypatch.setattr(
        llm_builder,
        "_merge_batch_results",
        lambda *_args, **_kwargs: MemoryDigestBuildResult(
            status="failed",
            meta={
                "generator": "deterministic_fallback",
                "llm_status": "failed",
            },
            level_contents={},
        ),
    )

    result = run_async(llm_builder.build_memory_digest_with_llm_async(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=index, content=f"第 {index} 条长期有效摘要来源")
            for index in range(1, 82)
        ],
        summarizer=summarizer,
    ))

    assert calls == 2
    assert result.status == "failed"
    assert result.meta["llm_models"] == ["actual-model-1", "actual-model-2"]
    assert result.meta["llm_requested_models"] == ["requested-model"]
    assert result.meta["llm_request_log_ids"] == [101, 102]
    assert result.meta["llm_actual_model_observed"] is True


def test_llm_digest_rejects_card_whose_named_evidence_does_not_support_it():
    from app.memory_digest.llm_builder import audit_llm_digest_meta

    meta = {
        "status": "active",
        "preview": {"brief": "技术偏好摘要"},
        "long_summary": {"topic_flow": "讨论了 Python 与部署。"},
        "recall_cards": [{
            "card_id": "card_1",
            "type": "preference",
            "text": "用户长期偏好 Rust，不使用 Python。",
            "keywords": ["Rust"],
            "importance": 0.9,
            "evidence_log_ids": [1],
        }],
        "quality": {"score": 0.9, "issues": []},
    }
    source_rows = [
        {"log_id": 1, "line": "[log_id=1][role=user] 用户明确说长期使用 Python"},
        {"log_id": 2, "line": "[log_id=2][role=assistant] 助手建议尝试 Rust"},
    ]

    ok, issues = audit_llm_digest_meta(meta, source_rows=source_rows)

    assert ok is False
    assert "recall_card_evidence_not_grounded" in issues


def test_llm_digest_keeps_grounded_cards_and_records_rejected_candidates():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    def fake_summarizer(_messages):
        return json.dumps({
            "preview": {"brief": "长期摘要来源治理"},
            "long_summary": {"topic_flow": "确认长期摘要的数据来源。"},
            "recall_cards": [
                {
                    "card_id": "card_1",
                    "type": "design_rule",
                    "text": "长期摘要必须从 ChatLog 生成。",
                    "keywords": ["ChatLog"],
                    "importance": 0.9,
                    "evidence_log_ids": [1],
                },
                {
                    "card_id": "card_2",
                    "type": "preference",
                    "text": "用户偏好 Rust。",
                    "keywords": ["ChatLog"],
                    "importance": 0.9,
                    "evidence_log_ids": [1],
                },
            ],
            "quality": {"score": 0.9, "issues": []},
        }, ensure_ascii=False)

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=1, content="长期摘要从 ChatLog 生成"),
            _log(id=2, content="召回卡必须逐条检查证据"),
        ],
        summarizer=fake_summarizer,
    )

    assert result.status == "active"
    assert [card["card_id"] for card in result.meta["recall_cards"]] == ["card_1"]
    assert result.meta["quality"]["issues"] == []
    assert result.meta["recall_card_governance"] == {
        "generated_count": 2,
        "evidence_accepted_count": 1,
        "evidence_rejected_count": 1,
        "retained_count": 1,
        "rejection_reason_counts": {"recall_card_evidence_not_grounded": 1},
    }
    assert "Rust" not in result.level_contents[2]


def test_llm_digest_still_fails_when_all_recall_cards_are_rejected():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    def fake_summarizer(_messages):
        return json.dumps({
            "preview": {"brief": "长期摘要来源治理"},
            "long_summary": {"topic_flow": "确认长期摘要的数据来源。"},
            "recall_cards": [{
                "card_id": "card_1",
                "type": "preference",
                "text": "用户偏好 Rust。",
                "keywords": ["ChatLog"],
                "importance": 0.9,
                "evidence_log_ids": [1],
            }],
            "quality": {"score": 0.9, "issues": []},
        }, ensure_ascii=False)

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=1, content="长期摘要从 ChatLog 生成"),
            _log(id=2, content="召回卡必须逐条检查证据"),
        ],
        summarizer=fake_summarizer,
    )

    assert result.status == "failed"
    assert result.meta["generator"] == "deterministic_fallback"
    assert "recall_card_evidence_not_grounded" in result.meta["fallback_reason"]
    assert "recall_cards_empty" in result.meta["fallback_reason"]
@pytest.mark.parametrize("card_type", ["fact", "preference"])
def test_llm_digest_rejects_recall_card_without_direct_evidence(card_type):
    from app.memory_digest.llm_builder import audit_llm_digest_meta

    meta = {
        "status": "active",
        "preview": {"brief": "Python 服务开发偏好"},
        "long_summary": {"topic_flow": "用户说明了长期使用的开发技术。"},
        "recall_cards": [{
            "card_id": "card_1",
            "type": card_type,
            "text": "用户长期使用 Python 进行服务开发。",
            "keywords": ["Python", "服务开发"],
            "importance": 0.9,
            "evidence_log_ids": [],
        }],
        "quality": {"score": 0.9, "issues": []},
    }
    source_rows = [{
        "log_id": 1,
        "role": "user",
        "is_bot": False,
        "line": "[log_id=1][role=user] 用户长期使用 Python 进行服务开发。",
    }]

    ok, issues = audit_llm_digest_meta(meta, source_rows=source_rows)

    assert ok is False
    assert "recall_card_evidence_missing" in issues


def test_llm_digest_accepts_recall_card_with_one_to_eight_grounded_evidence_ids():
    from app.memory_digest.llm_builder import audit_llm_digest_meta

    evidence_ids = list(range(1, 9))
    meta = {
        "status": "active",
        "preview": {"brief": "Python 服务开发偏好"},
        "long_summary": {"topic_flow": "用户多次说明了长期使用的开发技术。"},
        "recall_cards": [{
            "card_id": "card_1",
            "type": "preference",
            "text": "用户长期使用 Python 进行服务开发。",
            "keywords": ["Python", "服务开发"],
            "importance": 0.9,
            "evidence_log_ids": evidence_ids,
        }],
        "quality": {"score": 0.9, "issues": []},
    }
    source_rows = [
        {
            "log_id": log_id,
            "role": "user",
            "is_bot": False,
            "line": f"[log_id={log_id}][role=user] 用户长期使用 Python 进行服务开发。",
        }
        for log_id in evidence_ids
    ]

    ok, issues = audit_llm_digest_meta(meta, source_rows=source_rows)

    assert ok is True
    assert issues == []


def test_llm_digest_rejects_recall_card_with_more_than_eight_evidence_ids():
    from app.memory_digest.llm_builder import audit_llm_digest_meta

    evidence_ids = list(range(1, 10))
    meta = {
        "status": "active",
        "preview": {"brief": "Python 服务开发事实"},
        "long_summary": {"topic_flow": "用户多次说明了长期使用的开发技术。"},
        "recall_cards": [{
            "card_id": "card_1",
            "type": "fact",
            "text": "用户长期使用 Python 进行服务开发。",
            "keywords": ["Python", "服务开发"],
            "importance": 0.9,
            "evidence_log_ids": evidence_ids,
        }],
        "quality": {"score": 0.9, "issues": []},
    }
    source_rows = [
        {
            "log_id": log_id,
            "role": "user",
            "is_bot": False,
            "line": f"[log_id={log_id}][role=user] 用户长期使用 Python 进行服务开发。",
        }
        for log_id in evidence_ids
    ]

    ok, issues = audit_llm_digest_meta(meta, source_rows=source_rows)

    assert ok is False
    assert "recall_card_evidence_too_many" in issues


def test_llm_digest_rejects_credentials_and_assistant_as_user_preference_evidence():
    from app.memory_digest.llm_builder import audit_llm_digest_meta

    meta = {
        "status": "active",
        "preview": {"brief": "不安全摘要"},
        "long_summary": {"topic_flow": "助手输出了测试凭证。"},
        "recall_cards": [{
            "card_id": "card_1",
            "type": "preference",
            "text": "用户偏好使用 api_key=sk-secret-value 调用服务。",
            "keywords": ["api_key", "服务"],
            "importance": 0.9,
            "evidence_log_ids": [7],
        }],
        "quality": {"score": 0.9, "issues": []},
    }
    source_rows = [{
        "log_id": 7,
        "role": "assistant",
        "is_bot": True,
        "line": "[log_id=7][role=assistant][source=bot] api_key=sk-secret-value",
    }]

    ok, issues = audit_llm_digest_meta(meta, source_rows=source_rows)

    assert ok is False
    assert "contains_credential_material" in issues
    assert "preference_evidence_invalid_role" in issues


def test_llm_memory_digest_sync_builder_rejects_awaitable_summarizer():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    async def async_summarizer(_messages):
        return json.dumps(
            {
                "preview": "同步 builder 不应 await async summarizer。",
                "long_summary": "同步 builder 收到 awaitable 时应 fallback，而不是自行创建事件循环。",
                "recall_cards": ["同步 builder 遇到 awaitable 时必须要求调用方使用 async builder。"],
                "quality": {"score": 0.9, "reason": "边界清晰。"},
            },
            ensure_ascii=False,
        )

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=1, content="同步 builder 不能偷偷 await"),
            _log(id=2, sender_name="乙", content="需要改成 async builder"),
        ],
        summarizer=async_summarizer,
    )

    assert result.meta["generator"] == "deterministic_fallback"
    assert result.meta["llm_status"] == "fallback"
    assert "sync_summarizer_returned_awaitable" in result.meta["fallback_reason"]


def test_llm_memory_digest_async_builder_awaits_async_summarizer():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm_async

    async def async_summarizer(messages):
        assert messages[0]["role"] == "system"
        return json.dumps(
            {
                "preview": {
                    "brief": "async builder 可以直接 await LLM summarizer。",
                    "keywords": ["async builder", "LLM"],
                    "participants": ["甲", "乙"],
                },
                "long_summary": {
                    "topic_flow": "讨论确认 LLM memory digest 的默认实现只能挂在 async builder 上。",
                    "important_details": ["同步 builder 不再桥接 awaitable。"],
                    "conclusions": ["调用链应把 async 边界上移。"],
                    "open_loops": [],
                },
                "recall_cards": [
                    {
                        "card_id": "card_1",
                        "type": "episode_topic",
                        "text": "LLM memory digest 默认实现通过 async builder 直接 await。",
                        "keywords": ["async builder", "memory digest"],
                        "importance": 0.88,
                        "evidence_log_ids": [1, 2],
                    }
                ],
                "quality": {"score": 0.9, "issues": []},
            },
            ensure_ascii=False,
        )

    result = run_async(
        build_memory_digest_with_llm_async(
            user_id="group_42",
            session_id="group_42",
            digest_date="2026-05-22",
            logs=[
                _log(id=1, content="LLM memory digest 默认实现需要 async builder"),
                _log(id=2, sender_name="乙", content="同步 builder 不应该再桥接 awaitable"),
            ],
            summarizer=async_summarizer,
        )
    )

    assert result.meta["generator"] == "llm"
    assert result.meta["llm_status"] == "success"
    assert "async builder" in result.level_contents[2]


def test_default_memory_digest_summarizer_returns_resolved_model_name(monkeypatch):
    from app.memory_digest.llm_builder import (
        MemoryDigestLlmOutput,
        default_llm_memory_digest_summarizer_async,
    )

    monkeypatch.setattr(
        "clients.classifier_client.resolve_model_route",
        lambda _key: {
            "api_key": "test",
            "base_url": "http://example.invalid/v1",
            "temperature": 0.1,
            "model": "summary-model-v2",
            "max_tokens": 8192,
            "enable_thinking": "false",
        },
    )

    async def fake_chat_completion(self, **_kwargs):
        return {
            "choices": [{"message": {"content": "{}"}}],
            "model": "actual-summary-model",
            "_nanobot_requested_model": "summary-model-v2",
            "_nanobot_request_log_id": 321,
        }

    monkeypatch.setattr(
        "clients.new_api_client.NewAPIClient.chat_completion",
        fake_chat_completion,
    )

    result = run_async(default_llm_memory_digest_summarizer_async([
        {"role": "user", "content": "测试"},
    ]))

    assert isinstance(result, MemoryDigestLlmOutput)
    assert result.model == "actual-summary-model"
    assert result.requested_model == "summary-model-v2"
    assert result.request_log_id == 321
    assert result.content == "{}"


def test_default_memory_digest_summarizer_does_not_invent_actual_model(monkeypatch):
    from app.memory_digest.llm_builder import default_llm_memory_digest_summarizer_async

    monkeypatch.setattr(
        "clients.classifier_client.resolve_model_route",
        lambda _key: {
            "api_key": "test",
            "base_url": "http://example.invalid/v1",
            "temperature": 0.1,
            "model": "requested-summary-model",
            "max_tokens": 8192,
            "enable_thinking": "false",
        },
    )

    async def fake_chat_completion(self, **_kwargs):
        return {
            "choices": [{"message": {"content": "{}"}}],
            "_nanobot_model_id": "requested-summary-model",
            "_nanobot_requested_model": "requested-summary-model",
            "_nanobot_request_log_id": 654,
        }

    monkeypatch.setattr(
        "clients.new_api_client.NewAPIClient.chat_completion",
        fake_chat_completion,
    )

    result = run_async(default_llm_memory_digest_summarizer_async([
        {"role": "user", "content": "测试"},
    ]))

    assert result.model == "unknown"
    assert result.actual_model_observed is False
    assert result.requested_model == "requested-summary-model"
    assert result.request_log_id == 654


def test_default_memory_digest_summarizer_rejects_length_truncation(monkeypatch):
    from app.memory_digest.llm_builder import (
        MemoryDigestCapacityError,
        default_llm_memory_digest_summarizer_async,
    )

    monkeypatch.setattr(
        "clients.classifier_client.resolve_model_route",
        lambda _key: {
            "api_key": "test",
            "base_url": "http://example.invalid/v1",
            "temperature": 0.1,
            "model": "summary-model-v2",
            "max_tokens": 8192,
            "enable_thinking": "false",
        },
    )

    async def fake_chat_completion(self, **kwargs):
        assert kwargs["max_tokens"] == 8192
        return {
            "choices": [{
                "finish_reason": "length",
                "message": {"content": '{"preview":{"brief":"被截断"}'},
            }],
            "model": "actual-summary-model",
        }

    monkeypatch.setattr(
        "clients.new_api_client.NewAPIClient.chat_completion",
        fake_chat_completion,
    )

    with pytest.raises(
        MemoryDigestCapacityError,
        match="^memory_digest_output_capacity_exceeded$",
    ):
        run_async(default_llm_memory_digest_summarizer_async([
            {"role": "user", "content": "测试"},
        ]))


def test_memory_digest_capacity_error_is_non_retryable_failure():
    from app.memory_digest.llm_builder import (
        MemoryDigestCapacityError,
        build_memory_digest_with_llm,
    )

    def truncated_summarizer(_messages):
        raise MemoryDigestCapacityError(
            "memory_digest_output_capacity_exceeded"
        )

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[_log(id=1, content="长期摘要输出被服务端截断")],
        summarizer=truncated_summarizer,
    )

    assert result.status == "failed"
    assert result.meta["failure_type"] == "output_capacity_exceeded"


def test_memory_digest_full_declared_output_contract_fits_internal_budget():
    from app.memory_digest import llm_builder

    payload = {
        "preview": {
            "brief": "预" * 200,
            "keywords": ["关" * 32 for _ in range(8)],
            "participants": ["参" * 32 for _ in range(8)],
        },
        "long_summary": {
            "topic_flow": "主" * 600,
            "important_details": ["细" * 140 for _ in range(8)],
            "conclusions": ["结" * 120 for _ in range(6)],
            "open_loops": ["待" * 120 for _ in range(6)],
        },
        "recall_cards": [
            {
                "card_id": f"card_{index}",
                "type": "design_rule",
                "text": "卡" * 120,
                "keywords": ["词" * 32 for _ in range(6)],
                "importance": 0.8,
                "evidence_log_ids": list(range(1, 9)),
            }
            for index in range(8)
        ],
        "quality": {"score": 0.9, "reason": "理" * 180},
    }

    llm_builder._validate_llm_digest_output_budget(payload)


def test_memory_digest_source_id_covers_full_source_manifest():
    from app.memory_digest.llm_builder import _source_id

    common = {
        "session_id": "group_42",
        "digest_date": "2026-05-22",
    }
    first = _source_id(
        **common,
        source_rows=[
            {"log_id": 1, "line": "第一条"},
            {"log_id": 3, "line": "第三条"},
        ],
    )
    changed_middle = _source_id(
        **common,
        source_rows=[
            {"log_id": 1, "line": "第一条"},
            {"log_id": 2, "line": "第二条"},
            {"log_id": 3, "line": "第三条"},
        ],
    )
    changed_content = _source_id(
        **common,
        source_rows=[
            {"log_id": 1, "line": "第一条被修改"},
            {"log_id": 3, "line": "第三条"},
        ],
    )

    assert first != changed_middle
    assert first != changed_content


@pytest.mark.parametrize(
    "error_type",
    [TypeError, ValueError, RuntimeError, FileNotFoundError],
)
def test_llm_memory_digest_sync_propagates_summarizer_programming_error(
    error_type,
):
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    def broken_summarizer(_messages):
        raise error_type("PROGRAMMING_BUG")

    with pytest.raises(error_type, match="PROGRAMMING_BUG"):
        build_memory_digest_with_llm(
            user_id="group_42",
            session_id="group_42",
            digest_date="2026-05-22",
            logs=[_log(id=1, content="同步摘要器编程错误不能变成成功摘要")],
            summarizer=broken_summarizer,
        )


@pytest.mark.parametrize(
    "error_type",
    [TypeError, ValueError, RuntimeError, FileNotFoundError],
)
def test_llm_memory_digest_async_propagates_summarizer_programming_error(
    error_type,
):
    from app.memory_digest.llm_builder import build_memory_digest_with_llm_async

    async def broken_summarizer(_messages):
        raise error_type("PROGRAMMING_BUG")

    with pytest.raises(error_type, match="PROGRAMMING_BUG"):
        run_async(
            build_memory_digest_with_llm_async(
                user_id="group_42",
                session_id="group_42",
                digest_date="2026-05-22",
                logs=[_log(id=1, content="异步摘要器编程错误不能变成成功摘要")],
                summarizer=broken_summarizer,
            )
        )


def test_llm_memory_digest_builder_accepts_summary_strings_and_records_prompt_metadata():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    def fake_summarizer(_messages):
        return json.dumps(
            {
                "preview": "讨论确认长期摘要应从 ChatLog 按 date + session_id 生成。",
                "long_summary": "本次讨论明确 memory_digests 是长期摘要层，ChatLog 是主要数据源，level 2 recall cards 是 RAG 主召回入口。",
                "recall_cards": [
                    {
                        "type": "design_rule",
                        "text": "memory_digests 应从 ChatLog 按 date + session_id 聚合生成长期摘要。",
                        "keywords": ["memory_digests", "ChatLog", "session_id"],
                        "evidence_log_ids": [1],
                    },
                    {
                        "type": "design_rule",
                        "text": "memory_digests 的 level 2 recall cards 应作为 RAG 主召回层。",
                        "keywords": ["memory_digests", "recall cards", "RAG"],
                        "evidence_log_ids": [2],
                    },
                ],
                "quality": {"score": 0.9, "reason": "结构完整，召回卡片具体。"},
            },
            ensure_ascii=False,
        )

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=1, sender_name="甲", content="长期摘要应该从 ChatLog 按 date + session_id 生成"),
            _log(id=2, sender_name="乙", content="level 2 recall cards 应该作为 RAG 主召回层"),
        ],
        summarizer=fake_summarizer,
    )

    assert result.status == "active"
    assert result.meta["generator"] == "llm"
    assert result.meta["source_type"] == "date_session"
    assert result.meta["source_id"]
    assert result.meta["prompt_template"] == "tasks/memory_digest_system + tasks/memory_digest_user"
    assert result.meta["prompt_version"]["system_source"] in {"default", "runtime"}
    assert result.meta["fallback_reason"] is None
    assert result.meta["preview"]["brief"].startswith("讨论确认长期摘要")
    assert result.meta["long_summary"]["topic_flow"].startswith("本次讨论明确")
    assert len(result.meta["recall_cards"]) == 2


def test_llm_memory_digest_builder_fails_closed_when_audit_rejects_url_card():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    def fake_summarizer(_messages):
        return json.dumps(
            {
                "preview": {
                    "brief": "群里讨论了链接。",
                    "keywords": ["https", "bilibili"],
                    "participants": ["甲"],
                },
                "long_summary": {
                    "topic_flow": "群里围绕链接展开。",
                    "important_details": ["https://www.bilibili.com/video/BV1bad"],
                    "conclusions": [],
                    "open_loops": [],
                },
                "recall_cards": [
                    {
                        "card_id": "card_1",
                        "type": "episode_topic",
                        "text": "用户发了 https://www.bilibili.com/video/BV1bad",
                        "keywords": ["https"],
                        "importance": 0.8,
                        "evidence_log_ids": [1],
                    }
                ],
                "quality": {"score": 0.9, "issues": []},
            },
            ensure_ascii=False,
        )

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=1, sender_name="甲", content="https://www.bilibili.com/video/BV1bad"),
            _log(id=2, sender_name="乙", content="这个视频主要讲英语阅读刻板印象"),
        ],
        summarizer=fake_summarizer,
    )

    assert result.status == "failed"
    assert result.meta["generator"] == "deterministic_fallback"
    assert result.meta["llm_status"] == "fallback"
    assert "contains_url" in result.meta["llm_error"]
    assert "bilibili.com" not in result.level_contents[2]


def test_llm_memory_digest_builder_falls_back_for_invalid_json():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=1, content="长期摘要需要 LLM 输出严格 JSON"),
            _log(id=2, sender_name="乙", content="非法 JSON 必须 fallback"),
        ],
        summarizer=lambda _messages: "不是 JSON",
    )

    assert result.meta["generator"] == "deterministic_fallback"
    assert result.meta["llm_status"] == "fallback"
    assert "json_parse_failed" in result.meta["fallback_reason"]


def test_llm_memory_digest_builder_falls_back_for_missing_fields():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    def fake_summarizer(_messages):
        return json.dumps(
            {
                "preview": "只有预览，没有长期摘要和召回卡。",
                "quality": {"score": 0.5, "reason": "信息不足"},
            },
            ensure_ascii=False,
        )

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=1, content="长期摘要缺字段时不能写 LLM 结果"),
            _log(id=2, sender_name="乙", content="结构不完整的摘要必须 fallback"),
        ],
        summarizer=fake_summarizer,
    )

    assert result.meta["generator"] == "deterministic_fallback"
    assert "topic_flow_empty" in result.meta["fallback_reason"]
    assert "recall_cards_empty" in result.meta["fallback_reason"]


def test_llm_memory_digest_builder_accepts_grounded_low_self_score():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    def fake_summarizer(_messages):
        return json.dumps(
            {
                "preview": {
                    "brief": "低价值闲聊仍需忠实摘要。",
                    "keywords": ["低价值闲聊"],
                    "participants": ["甲"],
                },
                "long_summary": {
                    "topic_flow": "对话内容稀疏，但摘要忠实保留了原始信息。",
                    "important_details": [],
                    "conclusions": [],
                    "open_loops": [],
                },
                "recall_cards": [
                    {
                        "card_id": "card_1",
                        "type": "fact",
                        "text": "低价值闲聊仍需忠实摘要",
                        "keywords": ["低价值闲聊"],
                        "importance": 0.2,
                        "evidence_log_ids": [1],
                    }
                ],
                "quality": {
                    "score": 0.3,
                    "reason": "源内容缺少长期价值",
                },
            },
            ensure_ascii=False,
        )

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=1, content="低价值闲聊仍需忠实摘要"),
            _log(id=2, sender_name="乙", content="没有待办或长期决策"),
        ],
        summarizer=fake_summarizer,
    )

    assert result.status == "active"
    assert result.meta["generator"] == "llm"
    assert result.meta["quality"]["score"] == 0.3
    assert result.meta["quality"]["should_inject_preview"] is False


def test_llm_memory_digest_builder_falls_back_when_card_contains_log_path():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    def fake_summarizer(_messages):
        return json.dumps(
            {
                "preview": "讨论了日志路径。",
                "long_summary": "讨论中错误地把日志路径当成了长期记忆。",
                "recall_cards": ["报错位置在 /var/log/nanobot/server.log，需要查看 app.py:123"],
                "quality": {"score": 0.9, "reason": "包含污染路径"},
            },
            ensure_ascii=False,
        )

    result = build_memory_digest_with_llm(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[
            _log(id=1, content="recall card 不能包含日志路径堆砌"),
            _log(id=2, sender_name="乙", content="日志路径应该被审计拦截"),
        ],
        summarizer=fake_summarizer,
    )

    assert result.meta["generator"] == "deterministic_fallback"
    assert "recall_card_contains_log_path" in result.meta["fallback_reason"]


def test_generate_daily_digest_does_not_persist_deterministic_preview(db_session, monkeypatch):
    from core import daily_digest

    db_session.add_all([
        _log(id=1, content="KohakuVQ 技术预览里提到了 VQ codebook usage"),
        _log(id=2, sender_name="乙", content="Discrete AR 图像生成效果很强"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    created = daily_digest.generate_daily_digest_for_date("2026-05-22", use_llm=False)

    assert created == 0
    rows = db_session.query(MemoryDigest).filter_by(session_id="group_42", level=2).all()
    assert rows == []


def test_generate_daily_digest_skipped_source_remains_retryable_without_empty_rows(
    db_session,
    monkeypatch,
):
    from core import daily_digest

    db_session.add_all([
        _log(id=1, content="签到"),
        _log(id=2, sender_name="乙", content="[图片:1张]"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    assert daily_digest.generate_daily_digest_for_date("2026-05-22", use_llm=False) == 0
    assert db_session.query(MemoryDigest).filter_by(session_id="group_42").all() == []
    assert daily_digest._already_digested(db_session, "group_42", "2026-05-22") is False

    db_session.add(_log(id=3, content="KohakuVQ 后续补充了有效讨论"))
    db_session.commit()

    assert daily_digest.generate_daily_digest_for_date("2026-05-22", use_llm=False) == 0
    assert db_session.query(MemoryDigest).filter_by(session_id="group_42").all() == []


def test_generate_daily_digest_uses_llm_memory_digest_by_default(db_session, monkeypatch):
    from core import daily_digest

    def fake_summarizer(_messages):
        return json.dumps(
            {
                "preview": {
                    "brief": "群里总结了 KohakuVQ 与 Discrete AR 图像生成。",
                    "keywords": ["KohakuVQ", "Discrete AR", "图像生成"],
                    "participants": ["甲", "乙"],
                },
                "long_summary": {
                    "topic_flow": "讨论集中在 VQ codebook usage 和 Discrete AR 图像生成效果。",
                    "important_details": ["KohakuVQ 的 codebook usage 被认为较高。"],
                    "conclusions": ["这是一次图像生成技术讨论。"],
                    "open_loops": [],
                },
                "recall_cards": [
                    {
                        "card_id": "card_1",
                        "type": "episode_topic",
                        "text": "群里讨论过 KohakuVQ、Discrete AR 和图像生成效果。",
                        "keywords": ["KohakuVQ", "Discrete AR"],
                        "importance": 0.88,
                        "evidence_log_ids": [1, 2],
                    }
                ],
                "quality": {"score": 0.92, "issues": []},
            },
            ensure_ascii=False,
        )

    db_session.add_all([
        _log(id=1, content="KohakuVQ 技术预览里提到了 VQ codebook usage"),
        _log(id=2, sender_name="乙", content="Discrete AR 图像生成效果很强"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    created = daily_digest.generate_daily_digest_for_date(
        "2026-05-22",
        llm_summarizer=fake_summarizer,
    )

    assert created == 1
    row = db_session.query(MemoryDigest).filter_by(session_id="group_42", level=2).first()
    meta = json.loads(row.meta_json)
    assert meta["generator"] == "llm"
    assert meta["llm_status"] == "success"
    assert "KohakuVQ、Discrete AR" in row.content


def test_generate_daily_digest_contract_failure_remains_retryable(
    db_session,
    monkeypatch,
):
    from core import daily_digest

    calls = 0

    def forbidden_summarizer(_messages):
        nonlocal calls
        calls += 1
        return "{}"

    db_session.add(_log(id=1, content="有效日志应保留确定性摘要但不能绕过输入合同"))
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(
        "app.memory_digest.llm_builder._collect_source_rows",
        lambda _logs: [{"log_id": 1, "line": ""}],
    )

    created = daily_digest.generate_daily_digest_for_date(
        "2026-05-22",
        llm_summarizer=forbidden_summarizer,
    )

    rows = db_session.query(MemoryDigest).filter_by(session_id="group_42").all()
    assert calls == 0
    assert created == 0
    assert rows == []
    assert daily_digest._already_digested(db_session, "group_42", "2026-05-22") is False


def test_generate_daily_digest_force_failure_preserves_existing_active_digest(
    db_session,
    monkeypatch,
):
    from core import daily_digest

    calls = 0

    def forbidden_summarizer(_messages):
        nonlocal calls
        calls += 1
        return "{}"

    existing = _add_digest(db_session, status="active")
    existing_id = existing.id
    db_session.add(_log(id=1, content="强制重建失败时不能归档既有有效摘要"))
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(
        "app.memory_digest.llm_builder._collect_source_rows",
        lambda _logs: [{"log_id": 1, "line": ""}],
    )

    created = daily_digest.generate_daily_digest_for_date(
        "2026-05-22",
        force=True,
        llm_summarizer=forbidden_summarizer,
    )

    rows = db_session.query(MemoryDigest).filter_by(session_id="group_42").all()
    existing_meta = json.loads(db_session.get(MemoryDigest, existing_id).meta_json)
    new_statuses = {
        json.loads(row.meta_json)["status"]
        for row in rows
        if row.id != existing_id
    }
    assert calls == 0
    assert created == 0
    assert existing_meta["status"] == "active"
    assert new_statuses == set()


def test_generate_daily_digest_sync_without_summarizer_does_not_call_default_async(
    db_session,
    monkeypatch,
):
    from core import daily_digest

    called = False

    async def forbidden_default_async(_messages):
        nonlocal called
        called = True
        return "{}"

    db_session.add_all([
        _log(id=1, content="同步 daily digest 没有显式 summarizer"),
        _log(id=2, sender_name="乙", content="不能偷偷调用默认 async LLM"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(
        "app.memory_digest.llm_builder.default_llm_memory_digest_summarizer_async",
        forbidden_default_async,
    )

    created = daily_digest.generate_daily_digest_for_date("2026-05-22")

    assert created == 0
    assert called is False
    row = db_session.query(MemoryDigest).filter_by(session_id="group_42", level=2).first()
    assert row is None


def test_generate_daily_digest_async_uses_async_llm_summarizer(db_session, monkeypatch):
    from core import daily_digest

    async def async_summarizer(_messages):
        return json.dumps(
            {
                "preview": "async daily digest 直接 await LLM summarizer。",
                "long_summary": "异步 daily digest 入口用于默认 LLM 摘要路径，避免同步函数内部运行 awaitable。",
                "recall_cards": [
                    {
                        "type": "design_rule",
                        "text": "async daily digest 入口负责运行 LLM memory digest summarizer。",
                        "keywords": ["async daily digest", "summarizer"],
                        "evidence_log_ids": [1],
                    }
                ],
                "quality": {"score": 0.9, "reason": "边界清晰。"},
            },
            ensure_ascii=False,
        )

    db_session.add_all([
        _log(id=1, content="async daily digest 需要 await summarizer"),
        _log(id=2, sender_name="乙", content="同步入口不能偷偷桥接"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    created = run_async(
        daily_digest.generate_daily_digest_for_date_async(
            "2026-05-22",
            llm_summarizer=async_summarizer,
        )
    )

    assert created == 1
    row = db_session.query(MemoryDigest).filter_by(session_id="group_42", level=2).first()
    meta = json.loads(row.meta_json)
    assert meta["generator"] == "llm"
    assert meta["llm_status"] == "success"
    assert "async daily digest" in row.content


def test_scheduled_memory_digest_entry_uses_async_llm_generation(monkeypatch):
    from core import daily_digest

    calls: list[str] = []

    async def fake_generate(target_date, **_kwargs):
        calls.append(target_date)
        return 3

    monkeypatch.setattr(daily_digest, "generate_daily_digest_for_date_async", fake_generate)
    monkeypatch.setattr(daily_digest, "db_now_naive", lambda: datetime(2026, 5, 23, 4, 0, 0))

    created = daily_digest.run_daily_digest_once()

    assert created == 3
    assert calls == ["2026-05-22"]


def test_generate_daily_digest_writes_one_level0_one_level1_and_multiple_level2_cards(db_session, monkeypatch):
    from core import daily_digest

    def fake_summarizer(_messages):
        return json.dumps(
            {
                "preview": "讨论确认 memory_digests 的三级摘要结构。",
                "long_summary": "本次讨论明确同一个 digest_source 应生成一条 level 0 详细摘要、一条 level 1 预览摘要和多条 level 2 原子召回卡片。",
                "recall_cards": [
                    {
                        "type": "design_rule",
                        "text": "memory_digests 的 level 0 应是每个 digest_source 一条详细摘要。",
                        "keywords": ["digest_source", "level 0"],
                        "evidence_log_ids": [1],
                    },
                    {
                        "type": "design_rule",
                        "text": "memory_digests 的 level 1 应是每个 digest_source 一条 WebUI 预览摘要。",
                        "keywords": ["digest_source", "level 1"],
                        "evidence_log_ids": [2],
                    },
                    {
                        "type": "design_rule",
                        "text": "memory_digests 的 level 2 应是每个 digest_source 多条原子 recall cards。",
                        "keywords": ["digest_source", "level 2", "recall cards"],
                        "evidence_log_ids": [3],
                    },
                ],
                "quality": {"score": 0.93, "reason": "三级结构明确。"},
            },
            ensure_ascii=False,
        )

    db_session.add_all([
        _log(id=1, content="同一个 digest_source 只生成一条 level 0"),
        _log(id=2, sender_name="乙", content="同一个 digest_source 只生成一条 level 1"),
        _log(id=3, sender_name="丙", content="同一个 digest_source 要生成多条 level 2 recall cards"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    created = daily_digest.generate_daily_digest_for_date(
        "2026-05-22",
        llm_summarizer=fake_summarizer,
    )

    assert created == 1
    rows = db_session.query(MemoryDigest).filter_by(session_id="group_42").order_by(MemoryDigest.level, MemoryDigest.id).all()
    level0 = [row for row in rows if row.level == 0]
    level1 = [row for row in rows if row.level == 1]
    level2 = [row for row in rows if row.level == 2]
    assert len(level0) == 1
    assert len(level1) == 1
    assert len(level2) == 3

    level0_meta = json.loads(level0[0].meta_json)
    level1_meta = json.loads(level1[0].meta_json)
    level2_meta = [json.loads(row.meta_json) for row in level2]
    assert level0_meta["summary_type"] == "detailed_digest"
    assert level1_meta["summary_type"] == "preview_digest"
    assert [meta["summary_type"] for meta in level2_meta] == ["recall_card", "recall_card", "recall_card"]
    assert len({meta["source_id"] for meta in [level0_meta, level1_meta, *level2_meta]}) == 1
    assert all(meta["generator"] == "llm" for meta in [level0_meta, level1_meta, *level2_meta])
    assert all(meta["prompt_template"] == "tasks/memory_digest_system + tasks/memory_digest_user" for meta in [level0_meta, level1_meta, *level2_meta])
    assert all(meta.get("fallback_reason") is None for meta in [level0_meta, level1_meta, *level2_meta])
    assert all(row.parent_id == level1[0].id for row in level2)
    assert level1[0].parent_id == level0[0].id


def test_generate_daily_digest_falls_back_when_llm_summarizer_raises(db_session, monkeypatch):
    from core import daily_digest
    from app.memory_digest.llm_builder import MemoryDigestModelError

    def broken_summarizer(_messages):
        raise MemoryDigestModelError("llm gateway unavailable")

    db_session.add_all([
        _log(id=1, content="KohakuVQ 技术预览里提到了 VQ codebook usage"),
        _log(id=2, sender_name="乙", content="Discrete AR 图像生成效果很强"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    created = daily_digest.generate_daily_digest_for_date(
        "2026-05-22",
        llm_summarizer=broken_summarizer,
    )

    assert created == 0
    assert db_session.query(MemoryDigest).filter_by(session_id="group_42").count() == 0


def test_successful_daily_digest_enqueues_one_logical_source_for_semantic_index(
    db_session, monkeypatch
):
    from core import daily_digest
    from core.database import SemanticIndexJob

    def fake_summarizer(_messages):
        return json.dumps({
            "preview": {"brief": "索引闭环", "keywords": ["semantic index"]},
            "long_summary": {"topic_flow": "确认摘要写入后必须自动创建语义索引任务。"},
            "recall_cards": [{
                "card_id": "card_1",
                "type": "design_rule",
                "text": "MemoryDigest 成功写入后自动 enqueue 语义索引任务。",
                "keywords": ["MemoryDigest", "semantic index"],
                "importance": 0.9,
                "evidence_log_ids": [1, 2],
            }],
            "quality": {"score": 0.9, "issues": []},
        }, ensure_ascii=False)

    db_session.add_all([
        _log(id=1, content="MemoryDigest 写入之后需要自动 enqueue"),
        _log(id=2, content="semantic index worker 才能收到新任务"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    created = daily_digest.generate_daily_digest_for_date(
        "2026-05-22",
        llm_summarizer=fake_summarizer,
    )

    digest_ids = [row.id for row in db_session.query(MemoryDigest).order_by(MemoryDigest.id).all()]
    digest_source_ids = {
        json.loads(row.meta_json)["source_id"]
        for row in db_session.query(MemoryDigest).all()
    }
    jobs = db_session.query(SemanticIndexJob).order_by(SemanticIndexJob.id).all()
    assert created == 1
    assert len(jobs) == 1
    assert jobs[0].source_type == "memory_digest"
    assert {jobs[0].source_id} == digest_source_ids
    assert jobs[0].job_type == "replace"
    assert jobs[0].source_revision
    job_meta = json.loads(jobs[0].meta_json)
    assert job_meta["contract_version"] == 2
    assert job_meta["job_origin"] == "business"
    assert job_meta["document_ids"] == digest_ids
    assert set(job_meta["delete_source_ids"]) == {str(item) for item in digest_ids}


def test_memory_recall_excludes_legacy_by_default(client, db_session):
    legacy = MemoryDigest(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-21",
        level=2,
        content="KohakuVQ legacy raw digest",
        meta_json=json.dumps({"status": "legacy"}, ensure_ascii=False),
    )
    db_session.add(legacy)
    db_session.commit()
    active = _add_digest(db_session, digest_id=22)

    r = client.get("/api/v1/memory/recall?keyword=KohakuVQ&session_id=group_42&include_content=true")

    assert r.status_code == 200
    data = r.json()
    assert data["digest_hits"] == 1
    assert data["items"][0]["digest_id"] == active.id


def test_memory_digest_retrieval_supports_date_range(db_session):
    from app.memory_digest.retrieval_service import MemoryDigestRetrievalService

    _add_digest(db_session, digest_id=41)
    older = _add_digest(db_session, digest_id=42)
    older.digest_date = "2026-05-20"
    newer = _add_digest(db_session, digest_id=43)
    newer.digest_date = "2026-05-24"
    db_session.commit()

    rows = MemoryDigestRetrievalService(db_session).list_digests(
        session_id="group_42",
        date_start="2026-05-21",
        date_end="2026-05-23",
        level=2,
        include_legacy=False,
    )

    assert [row["id"] for row in rows] == [41]


def test_memory_digest_list_filters_legacy_after_fetching_enough_rows(db_session):
    from app.memory_digest.retrieval_service import MemoryDigestRetrievalService

    active = _add_digest(db_session, digest_id=60)
    for digest_id in range(61, 68):
        _add_digest(db_session, digest_id=digest_id, schema_version=1, status="legacy")

    rows = MemoryDigestRetrievalService(db_session).list_digests(
        session_id="group_42",
        level=2,
        limit=2,
        include_legacy=False,
    )

    assert [row["id"] for row in rows] == [active.id]


def test_memory_digest_recall_filters_legacy_after_fetching_enough_rows(db_session):
    from app.memory_digest.retrieval_service import MemoryDigestRetrievalService

    active = _add_digest(db_session, digest_id=70, content="[card] KohakuVQ active digest")
    for digest_id in range(71, 78):
        _add_digest(
            db_session,
            digest_id=digest_id,
            schema_version=1,
            status="legacy",
            content="[card] KohakuVQ legacy digest",
        )

    rows = MemoryDigestRetrievalService(db_session).recall(
        keyword="KohakuVQ",
        session_id="group_42",
        limit=1,
        include_legacy=False,
    )

    assert [row["digest_id"] for row in rows] == [active.id]


def test_memory_digest_recall_folds_matching_rows_by_source_before_limit(db_session):
    from app.memory_digest.retrieval_service import MemoryDigestRetrievalService

    def add_card(digest_id: int, source_id: str, card_text: str) -> None:
        meta = {
            "schema_version": 2,
            "status": "active",
            "generator": "llm",
            "llm_status": "success",
            "source_id": source_id,
            "preview": {"brief": f"{source_id} 预览"},
            "long_summary": {"topic_flow": "只在长摘要中出现的折叠探针"},
            "recall_cards": [{"card_id": str(digest_id), "text": card_text}],
            "quality": {"score": 0.9, "issues": []},
        }
        db_session.add(MemoryDigest(
            id=digest_id,
            user_id="group_42",
            session_id="group_42",
            digest_date="2026-05-22",
            level=2,
            content=card_text,
            meta_json=json.dumps(meta, ensure_ascii=False),
            source_start_log_id=1,
            source_end_log_id=20,
        ))

    add_card(201, "logical-a", "A 的第一张卡")
    add_card(202, "logical-a", "A 的第二张卡")
    add_card(203, "logical-b", "B 的第一张卡")
    db_session.commit()

    rows = MemoryDigestRetrievalService(db_session).recall(
        keyword="折叠探针",
        session_id="group_42",
        limit=2,
    )

    assert len(rows) == 2
    assert {row["digest_source_id"] for row in rows} == {"logical-a", "logical-b"}
    logical_a = next(row for row in rows if row["digest_source_id"] == "logical-a")
    assert logical_a["matched_digest_row_ids"] == [202, 201]
    assert {
        card["card_id"] for card in logical_a["meta"]["recall_cards"]
    } == {"201", "202"}
    assert all("meta" not in node for node in logical_a["revealed_chain"])


def test_expand_by_source_deduplicates_shared_meta_and_keeps_compact_chain(db_session):
    from app.memory_digest.retrieval_service import MemoryDigestRetrievalService

    source_id = "logical-expand"
    base_meta = {
        "schema_version": 2,
        "status": "active",
        "generator": "llm",
        "llm_status": "success",
        "source_id": source_id,
        "preview": {"brief": "展开预览"},
        "long_summary": {"topic_flow": "展开详情"},
        "recall_cards": [
            {"card_id": "a", "text": "卡片 A"},
            {"card_id": "b", "text": "卡片 B"},
        ],
        "quality": {"score": 0.9, "issues": []},
    }
    rows = [
        MemoryDigest(
            id=211,
            user_id="group_42",
            session_id="group_42",
            digest_date="2026-05-22",
            level=0,
            content="L0",
            meta_json=json.dumps({**base_meta, "summary_type": "detailed_digest"}, ensure_ascii=False),
        ),
        MemoryDigest(
            id=212,
            user_id="group_42",
            session_id="group_42",
            digest_date="2026-05-22",
            level=1,
            parent_id=211,
            content="L1",
            meta_json=json.dumps({**base_meta, "summary_type": "preview_digest"}, ensure_ascii=False),
        ),
        MemoryDigest(
            id=213,
            user_id="group_42",
            session_id="group_42",
            digest_date="2026-05-22",
            level=2,
            parent_id=212,
            content="卡片 A",
            meta_json=json.dumps({
                **base_meta,
                "summary_type": "recall_card",
                "recall_cards": [{"card_id": "a", "text": "卡片 A"}],
            }, ensure_ascii=False),
        ),
        MemoryDigest(
            id=214,
            user_id="group_42",
            session_id="group_42",
            digest_date="2026-05-22",
            level=2,
            parent_id=212,
            content="卡片 B",
            meta_json=json.dumps({
                **base_meta,
                "summary_type": "recall_card",
                "recall_cards": [{"card_id": "b", "text": "卡片 B"}],
            }, ensure_ascii=False),
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()

    item = MemoryDigestRetrievalService(db_session).expand_by_source(source_id=source_id)

    assert item is not None
    assert [card["card_id"] for card in item["recall_cards"]] == ["a", "b"]
    assert all("meta" not in node for node in item["chain"])
    assert "long_summary" not in json.dumps(item["chain"], ensure_ascii=False)


def test_digest_status_respects_explicit_archived_for_legacy_meta():
    from app.memory_digest.retrieval_service import digest_status

    assert digest_status({"status": "archived"}) == "archived"
    assert digest_status({"schema_version": 1, "status": "archived"}) == "archived"


def test_memory_recall_rejects_invalid_date_filters(client):
    malformed = client.get(
        "/api/v1/memory/recall?keyword=KohakuVQ&date_start=2026-5-2"
    )
    invalid_calendar = client.get(
        "/api/v1/memory/recall?keyword=KohakuVQ&date_start=2026-02-30"
    )
    reversed_range = client.get(
        "/api/v1/memory/recall?keyword=KohakuVQ"
        "&date_start=2026-05-03&date_end=2026-05-02"
    )

    assert malformed.status_code == 400
    assert "date_start" in malformed.json()["detail"]
    assert invalid_calendar.status_code == 400
    assert "date_start" in invalid_calendar.json()["detail"]
    assert reversed_range.status_code == 400
    assert "date_start" in reversed_range.json()["detail"]


def test_memory_query_tool_search_and_expand(db_session, monkeypatch):
    from creatures.nanobot.prompts.skills.memory_query.tool import MemoryQueryTool
    from core import database

    row = _add_digest(db_session, digest_id=31)
    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)

    tool = MemoryQueryTool()
    search_result = run_async(tool._execute({
        "mode": "search",
        "query": "KohakuVQ",
        "session_id": "group_42",
        "limit": 5,
    }))
    expand_result = run_async(tool._execute({
        "mode": "expand",
        "digest_id": row.id,
        "include_detail": True,
    }))

    assert search_result.exit_code == 0
    assert "digest_id=31" in search_result.output
    assert "KohakuVQ" in search_result.output
    assert search_result.metadata["structured_content"]["mode"] == "search"
    assert search_result.metadata["structured_content"]["items"][0]["digest_id"] == 31
    assert expand_result.exit_code == 0
    assert "topic_flow" in expand_result.output
    assert expand_result.metadata["structured_content"]["mode"] == "expand"
    assert "source logs" not in expand_result.output.lower()


def test_memory_query_tool_time_accepts_date_range(db_session, monkeypatch):
    from creatures.nanobot.prompts.skills.memory_query.tool import MemoryQueryTool
    from core import database

    _add_digest(db_session, digest_id=51)
    older = _add_digest(db_session, digest_id=52)
    older.digest_date = "2026-05-20"
    db_session.commit()
    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)

    tool = MemoryQueryTool()
    result = run_async(tool._execute({
        "mode": "time",
        "session_id": "group_42",
        "date_start": "2026-05-21",
        "date_end": "2026-05-23",
        "limit": 10,
    }))

    assert result.exit_code == 0
    assert "digest_id=51" in result.output
    assert "digest_id=52" not in result.output
    assert result.metadata["structured_content"]["date_start"] == "2026-05-21"


def test_memory_query_tool_rejects_invalid_date_range(monkeypatch):
    from creatures.nanobot.prompts.skills.memory_query.tool import MemoryQueryTool

    tool = MemoryQueryTool()
    malformed = run_async(tool._execute({
        "mode": "time",
        "date_start": "2026-5-2",
    }))
    reversed_range = run_async(tool._execute({
        "mode": "time",
        "date_start": "2026-05-03",
        "date_end": "2026-05-02",
    }))

    assert malformed.error
    assert "date_start" in malformed.error
    assert reversed_range.error
    assert "date_start" in reversed_range.error


def test_memory_query_tool_session_summary_search_and_expand(db_session, monkeypatch):
    from creatures.nanobot.prompts.skills.memory_query.tool import MemoryQueryTool
    from core import database

    row = RollingSessionSummary(
        session_id="s1",
        user_id="u1",
        chat_type="private",
        status="active",
        summary_kind="llm_episode",
        summary_text="用户持续讨论 rolling session summary 的异步 worker 和审计边界。",
        summary_json=json.dumps({
            "summary": "用户持续讨论 rolling session summary 的异步 worker 和审计边界。",
            "keywords": ["rolling summary", "worker"],
            "quality": {"score": 0.86, "issues": []},
        }, ensure_ascii=False),
        covered_from_turn_id=11,
        covered_until_turn_id=20,
        source_turn_count=10,
        quality_score=0.86,
        created_at=_db_time(2026, 5, 26, 12, 0, 0),
    )
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)

    tool = MemoryQueryTool()
    search_result = run_async(tool._execute({
        "source": "session_summary",
        "mode": "search",
        "query": "worker",
        "session_id": "s1",
        "limit": 5,
    }))
    expand_result = run_async(tool._execute({
        "source": "session_summary",
        "mode": "expand",
        "summary_id": row.id,
    }))

    assert search_result.exit_code == 0
    assert f"summary_id={row.id}" in search_result.output
    assert search_result.metadata["structured_content"]["source"] == "session_summary"
    assert search_result.metadata["structured_content"]["items"][0]["summary_kind"] == "llm_episode"
    assert expand_result.exit_code == 0
    assert "covered_turns=11..20" in expand_result.output
    assert "原始 ChatLog" not in expand_result.output


def test_expand_by_source_matches_default_json_dumps_spacing(db_session):
    """回归：expand_by_source 必须匹配 json.dumps(ensure_ascii=False) 的带空格格式。"""
    from app.memory_digest.retrieval_service import MemoryDigestRetrievalService

    source_id = "20260601_group_42_test_spacing_v2"
    meta = {
        "schema_version": 2,
        "status": "active",
        "generator": "llm",
        "llm_status": "success",
        "source_id": source_id,
        "preview": {"brief": "测试预览", "keywords": ["测试"], "participants": []},
        "long_summary": {"topic_flow": "测试详情", "important_details": [], "conclusions": [], "open_loops": []},
        "recall_cards": [],
        "quality": {"score": 0.9, "issues": [], "reason": "test"},
    }

    row = MemoryDigest(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-06-01",
        level=1,
        parent_id=None,
        content="测试预览内容",
        meta_json=json.dumps(meta, ensure_ascii=False),
    )
    db_session.add(row)
    db_session.commit()

    svc = MemoryDigestRetrievalService(db_session)
    item = svc.expand_by_source(source_id=source_id)
    assert item is not None, f"expand_by_source 应匹配到 source_id={source_id}"
    assert item["digest_source_id"] == source_id
    assert item["preview"]["brief"] == "测试预览"
