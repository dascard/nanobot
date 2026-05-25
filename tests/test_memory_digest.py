import asyncio
import json
from datetime import datetime

from core.database import ChatLog, MemoryDigest


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

    assert all(len(card["text"]) <= 80 for card in result.meta["recall_cards"])
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


def test_generate_daily_digest_writes_v2_recall_card_rows(db_session, monkeypatch):
    from core import daily_digest

    db_session.add_all([
        _log(id=1, content="KohakuVQ 技术预览里提到了 VQ codebook usage"),
        _log(id=2, sender_name="乙", content="Discrete AR 图像生成效果很强"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    created = daily_digest.generate_daily_digest_for_date("2026-05-22")

    assert created == 1
    row = db_session.query(MemoryDigest).filter_by(session_id="group_42", level=2).one()
    meta = json.loads(row.meta_json)
    assert meta["schema_version"] == 2
    assert meta["status"] == "active"
    assert row.content.startswith("[card]")
    assert meta["recall_cards"][0]["evidence_log_ids"]


def test_generate_daily_digest_force_can_replace_skipped_digest(db_session, monkeypatch):
    from core import daily_digest

    db_session.add_all([
        _log(id=1, content="签到"),
        _log(id=2, sender_name="乙", content="[图片:1张]"),
    ])
    db_session.commit()
    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)

    assert daily_digest.generate_daily_digest_for_date("2026-05-22") == 0
    skipped = db_session.query(MemoryDigest).filter_by(session_id="group_42", level=2).one()
    assert json.loads(skipped.meta_json)["status"] == "skipped"

    db_session.add(_log(id=3, content="KohakuVQ 后续补充了有效讨论"))
    db_session.commit()

    assert daily_digest.generate_daily_digest_for_date("2026-05-22") == 0
    assert daily_digest.generate_daily_digest_for_date("2026-05-22", force=True) == 1

    rows = db_session.query(MemoryDigest).filter_by(session_id="group_42", level=2).all()
    statuses = [json.loads(row.meta_json)["status"] for row in rows]
    assert statuses.count("active") == 1
    assert "archived" in statuses


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
