import asyncio
import json
from datetime import datetime

from core.database import ChatLog, MemoryDigest, RollingSessionSummary


def _log(**kwargs):
    defaults = {
        "user_id": "group_42",
        "session_id": "group_42",
        "role": "ambient",
        "sender_name": "甲",
        "content": "KohakuVQ 这个 VQ codebook 的 usage 很高，图像生成效果也不错",
        "created_at": datetime(2026, 5, 22, 12, 0, 0),
    }
    defaults.update(kwargs)
    return ChatLog(**defaults)


def _add_digest(db, *, digest_id: int | None = None, status="active", schema_version=2, content=""):
    meta = {
        "schema_version": schema_version,
        "status": status,
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
    assert "{{" not in messages[1]["content"]
    assert prompt_meta["template"] == "tasks/memory_digest_system + tasks/memory_digest_user"
    assert prompt_meta["system_source"] in {"default", "runtime"}


def test_memory_digest_prompt_v2_missing_template_uses_short_fallback(monkeypatch):
    from app.memory_digest.llm_builder import build_llm_digest_messages
    from app.memory_digest.builder import MemoryDigestBuilder

    def missing_template(*_args, **_kwargs):
        raise FileNotFoundError("template missing")

    monkeypatch.setattr("app.memory_digest.llm_builder.load_template", missing_template)

    fallback = MemoryDigestBuilder().build(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-22",
        logs=[_log(id=1, content="模板缺失时不能让 daily digest 崩溃")],
    )

    messages, prompt_meta = build_llm_digest_messages(
        session_id="group_42",
        digest_date="2026-05-22",
        fallback=fallback,
        source_rows=[
            {
                "log_id": 1,
                "line": "[log_id=1][12:00] 甲: 模板缺失时不能让 daily digest 崩溃",
            }
        ],
    )

    assert messages[0]["content"].startswith("生成长期记忆摘要")
    assert prompt_meta["system_source"] == "fallback"
    assert prompt_meta["user_source"] == "fallback"


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
    assert result.meta["quality"]["should_inject_preview"] is True
    assert "PCL 与 pagefile" in result.level_contents[2]


def test_llm_memory_digest_builder_accepts_goal_string_json_shape_and_records_prompt_metadata():
    from app.memory_digest.llm_builder import build_memory_digest_with_llm

    def fake_summarizer(_messages):
        return json.dumps(
            {
                "preview": "讨论确认长期摘要应从 ChatLog 按 date + session_id 生成。",
                "long_summary": "本次讨论明确 memory_digests 是长期摘要层，ChatLog 是主要数据源，level 2 recall cards 是 RAG 主召回入口。",
                "recall_cards": [
                    "memory_digests 应从 ChatLog 按 date + session_id 聚合生成长期摘要。",
                    "memory_digests 的 level 2 recall cards 应作为 RAG 主召回层。",
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


def test_llm_memory_digest_builder_falls_back_when_audit_rejects_url_card():
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

    assert result.status == "active"
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


def test_llm_memory_digest_builder_falls_back_for_missing_fields_and_low_quality():
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
            _log(id=2, sender_name="乙", content="低质量摘要也必须 fallback"),
        ],
        summarizer=fake_summarizer,
    )

    assert result.meta["generator"] == "deterministic_fallback"
    assert "topic_flow_empty" in result.meta["fallback_reason"]
    assert "recall_cards_empty" in result.meta["fallback_reason"]
    assert "quality_score_below_threshold" in result.meta["fallback_reason"]


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


def test_generate_daily_digest_writes_v2_recall_card_rows(db_session, monkeypatch):
    from core import daily_digest

    db_session.add_all([
        _log(id=1, content="KohakuVQ 技术预览里提到了 VQ codebook usage"),
        _log(id=2, sender_name="乙", content="Discrete AR 图像生成效果很强"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    created = daily_digest.generate_daily_digest_for_date("2026-05-22", use_llm=False)

    assert created == 1
    rows = db_session.query(MemoryDigest).filter_by(session_id="group_42", level=2).all()
    assert len(rows) >= 1
    meta = json.loads(rows[0].meta_json)
    assert meta["schema_version"] == 2
    assert meta["status"] == "active"
    assert rows[0].content.startswith("[card]")
    assert meta["recall_cards"][0]["evidence_log_ids"]


def test_generate_daily_digest_force_can_replace_skipped_digest(db_session, monkeypatch):
    from core import daily_digest

    db_session.add_all([
        _log(id=1, content="签到"),
        _log(id=2, sender_name="乙", content="[图片:1张]"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    assert daily_digest.generate_daily_digest_for_date("2026-05-22", use_llm=False) == 0
    skipped = db_session.query(MemoryDigest).filter_by(session_id="group_42", level=2).one()
    assert json.loads(skipped.meta_json)["status"] == "skipped"

    db_session.add(_log(id=3, content="KohakuVQ 后续补充了有效讨论"))
    db_session.commit()

    assert daily_digest.generate_daily_digest_for_date("2026-05-22", use_llm=False) == 0
    assert daily_digest.generate_daily_digest_for_date("2026-05-22", force=True, use_llm=False) == 1

    rows = db_session.query(MemoryDigest).filter_by(session_id="group_42", level=2).all()
    metas = [json.loads(row.meta_json) for row in rows]
    statuses = [meta["status"] for meta in metas]
    assert statuses.count("active") >= 1
    active_source_ids = {meta["source_id"] for meta in metas if meta["status"] == "active"}
    assert len(active_source_ids) == 1
    assert "archived" in statuses


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


def test_generate_daily_digest_writes_one_level0_one_level1_and_multiple_level2_cards(db_session, monkeypatch):
    from core import daily_digest

    def fake_summarizer(_messages):
        return json.dumps(
            {
                "preview": "讨论确认 memory_digests 的三级摘要结构。",
                "long_summary": "本次讨论明确同一个 digest_source 应生成一条 level 0 详细摘要、一条 level 1 预览摘要和多条 level 2 原子召回卡片。",
                "recall_cards": [
                    "memory_digests 的 level 0 应是每个 digest_source 一条详细摘要。",
                    "memory_digests 的 level 1 应是每个 digest_source 一条 WebUI 预览摘要。",
                    "memory_digests 的 level 2 应是每个 digest_source 多条原子 recall cards。",
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

    def broken_summarizer(_messages):
        raise RuntimeError("llm gateway unavailable")

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

    assert created == 1
    row = db_session.query(MemoryDigest).filter_by(session_id="group_42", level=2).first()
    meta = json.loads(row.meta_json)
    assert meta["generator"] == "deterministic_fallback"
    assert meta["llm_status"] == "fallback"
    assert "llm gateway unavailable" in meta["llm_error"]
    assert "KohakuVQ" in row.content


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


def test_digest_status_respects_explicit_archived_for_legacy_meta():
    from app.memory_digest.retrieval_service import digest_status

    assert digest_status({"status": "archived"}) == "archived"
    assert digest_status({"schema_version": 1, "status": "archived"}) == "archived"


def test_memory_recall_rejects_invalid_date_filters(client):
    r = client.get("/api/v1/memory/recall?keyword=KohakuVQ&date_start=2026-5-2")

    assert r.status_code == 400
    assert "date_start" in r.json()["detail"]


def test_memory_query_tool_search_and_expand(db_session, monkeypatch):
    from creatures.nanobot.prompts.skills.memory_query.tool import MemoryQueryTool
    from core import database

    row = _add_digest(db_session, digest_id=31)
    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)

    tool = MemoryQueryTool()
    search_result = asyncio.run(tool._execute({
        "mode": "search",
        "query": "KohakuVQ",
        "session_id": "group_42",
        "limit": 5,
    }))
    expand_result = asyncio.run(tool._execute({
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
    result = asyncio.run(tool._execute({
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
    result = asyncio.run(tool._execute({
        "mode": "time",
        "date_start": "2026-5-2",
    }))

    assert result.error
    assert "date_start" in result.error


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
        created_at=datetime(2026, 5, 26, 12, 0, 0),
    )
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)

    tool = MemoryQueryTool()
    search_result = asyncio.run(tool._execute({
        "source": "session_summary",
        "mode": "search",
        "query": "worker",
        "session_id": "s1",
        "limit": 5,
    }))
    expand_result = asyncio.run(tool._execute({
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
