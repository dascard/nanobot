"""Eval Sampling 只读取新群学习候选事实源。"""

from __future__ import annotations


def _candidate(**overrides):
    from core.db.models import GroupLearningCandidate

    values = {
        "candidate_id": "glc_eval_1",
        "chat_stream_id": "qq:42:group",
        "candidate_type": "slang",
        "content": "摸鱼",
        "meaning": "上班时短暂休息",
        "normalized_key": "摸鱼",
        "fingerprint": "a" * 64,
        "content_hash": "b" * 64,
        "source": "rule",
        "status": "pending_model_review",
        "rule_id": "slang.definition.v1",
        "rule_version": 1,
    }
    values.update(overrides)
    return GroupLearningCandidate(**values)


def test_memory_learning_sampler_reads_new_candidates_not_legacy_tables(
    db_session,
):
    from core.db.models import ExpressionMemory, JargonMemory
    from core.eval_sampling.db_sampler import sample_memory_learning

    db_session.add_all([
        ExpressionMemory(
            chat_stream_id="qq:42:group",
            expression="旧表达",
            confidence=0.1,
            status="candidate",
        ),
        JargonMemory(
            chat_stream_id="qq:42:group",
            term="旧黑话",
            meaning="旧释义",
            confidence=0.1,
            status="candidate",
        ),
        _candidate(),
        _candidate(
            candidate_id="glc_eval_2",
            candidate_type="expression",
            content="芜湖",
            meaning="",
            normalized_key="芜湖",
            fingerprint="c" * 64,
            content_hash="d" * 64,
            status="waiting_for_evidence",
            rule_id="expression.short_phrase.v1",
        ),
    ])
    db_session.commit()

    slang = sample_memory_learning(
        db_session,
        candidate_type="slang",
    )
    expressions = sample_memory_learning(
        db_session,
        candidate_type="expression",
    )

    assert [item["source_ref"] for item in slang] == [
        "group_learning_candidate:1"
    ]
    assert slang[0]["input"]["content"] == "摸鱼"
    assert slang[0]["input"]["meaning"] == "上班时短暂休息"
    assert slang[0]["input"]["status"] == "pending_model_review"
    assert slang[0]["tags"][-2:] == [
        "slang",
        "pending_model_review",
    ]
    assert [item["source_ref"] for item in expressions] == [
        "group_learning_candidate:2"
    ]
    serialized = str(slang + expressions)
    assert "旧表达" not in serialized
    assert "旧黑话" not in serialized


def test_memory_learning_sampler_cursor_and_type_are_strict(db_session):
    from core.eval_sampling.db_sampler import sample_memory_learning

    db_session.add_all([
        _candidate(),
        _candidate(
            candidate_id="glc_eval_2",
            fingerprint="c" * 64,
            content_hash="d" * 64,
            content="划水",
        ),
    ])
    db_session.commit()

    result = sample_memory_learning(
        db_session,
        after_latest=1,
        candidate_type="slang",
        limit=1,
    )

    assert [item["source_ref"] for item in result] == [
        "group_learning_candidate:2"
    ]


def test_offline_eval_sampler_reads_same_new_candidate_source(db_session):
    from evals.sample_from_db import _sample_memory_learning

    db_session.add(_candidate())
    db_session.commit()

    result = _sample_memory_learning(db_session, 10)

    assert [item["source_ref"] for item in result] == [
        "group_learning_candidate:1"
    ]
    assert result[0]["input"]["candidate_type"] == "slang"
