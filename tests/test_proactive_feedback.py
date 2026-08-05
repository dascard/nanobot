"""主动外呼运行证据与反馈采样回归。"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from core.database import EvalCandidate, ProactiveOutreachLog
from core.eval_sampling.db_sampler import (
    proactive_outreach_case_id,
    sample_proactive_outreach_evidence,
)
from core.eval_sampling.store import upsert_candidate
from core.proactive.feedback import (
    ProactiveFeedbackConflict,
    record_proactive_outreach_feedback,
)


def _sent_outreach(db_session) -> ProactiveOutreachLog:
    row = ProactiveOutreachLog(
        user_id="feedback-user-secret",
        idempotency_key="outreach:feedback-user-secret:1",
        grounding_json=json.dumps({
            "_trigger_runtime": {
                "schema_version": 1,
                "trigger_id": "trigger-safe-id",
                "trigger_type": "heartbeat",
                "source_type": "proactive_outreach",
                "source_ref_sha256": "a" * 64,
                "idempotency_sha256": "b" * 64,
                "owner_sha256": "c" * 64,
                "governance_sha256": "d" * 64,
                "trigger_sha256": "e" * 64,
                "run_id": "run-safe-id",
            },
            "recent_messages": [{"content": "不得进入评测证据的聊天正文"}],
        }, ensure_ascii=False),
        judge_should=True,
        judge_reason="不得进入评测证据的 Judge 原文",
        message="不得进入评测证据的主动外呼正文",
        status="sent",
        forced=False,
        created_at=datetime(2026, 8, 5, 12, 0, 0),
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_proactive_evidence_sampler_redacts_content_and_does_not_skip_old_terminal(
    db_session,
):
    row = _sent_outreach(db_session)

    candidates = sample_proactive_outreach_evidence(db_session, limit=10)

    assert len(candidates) == 1
    candidate = candidates[0]
    serialized = json.dumps(candidate, ensure_ascii=False)
    assert candidate["source_ref"] == f"proactive_outreach_log:{row.id}:sent"
    assert candidate["input"]["trigger"]["trigger_id"] == "trigger-safe-id"
    assert candidate["input"]["message_chars"] > 0
    assert candidate["input"]["message_sha256"]
    for secret in (
        "feedback-user-secret",
        "不得进入评测证据的聊天正文",
        "不得进入评测证据的 Judge 原文",
        "不得进入评测证据的主动外呼正文",
    ):
        assert secret not in serialized

    assert upsert_candidate(db_session, candidate) is True
    assert sample_proactive_outreach_evidence(db_session, limit=10) == []


def test_proactive_evidence_sampler_does_not_starve_behind_sampled_rows(
    db_session,
):
    old_rows = [
        ProactiveOutreachLog(
            user_id="sampled-user",
            idempotency_key=f"sampled-{index}",
            grounding_json="{}",
            message="已采样",
            status="sent",
            created_at=datetime(2026, 8, 4, 12, index, 0),
        )
        for index in range(31)
    ]
    db_session.add_all(old_rows)
    db_session.flush()
    db_session.add_all([
        EvalCandidate(
            case_id=proactive_outreach_case_id(int(row.id), "sent"),
            suite="proactive_outreach",
            source="db",
            source_ref=f"proactive_outreach_log:{row.id}:sent",
        )
        for row in old_rows
    ])
    newest = ProactiveOutreachLog(
        user_id="new-user",
        idempotency_key="new-unsampled-row",
        grounding_json="{}",
        message="待采样",
        status="sent",
        created_at=datetime(2026, 8, 5, 12, 0, 0),
    )
    db_session.add(newest)
    db_session.commit()
    db_session.refresh(newest)

    candidates = sample_proactive_outreach_evidence(db_session, limit=10)

    assert [candidate["case_id"] for candidate in candidates] == [
        proactive_outreach_case_id(int(newest.id), "sent")
    ]


def test_proactive_feedback_is_idempotent_hashed_and_conflict_safe(db_session):
    row = _sent_outreach(db_session)
    evidence_ref = "qq-message:raw-user-feedback-reference"

    first = record_proactive_outreach_feedback(
        db_session,
        log_id=int(row.id),
        label="helpful",
        source="user_reported",
        evidence_ref=evidence_ref,
    )
    db_session.commit()
    second = record_proactive_outreach_feedback(
        db_session,
        log_id=int(row.id),
        label="helpful",
        source="user_reported",
        evidence_ref=evidence_ref,
    )

    candidate = (
        db_session.query(EvalCandidate)
        .filter(EvalCandidate.case_id == first.case_id)
        .one()
    )
    payload = json.loads(candidate.expected_json)
    assert first.created is True
    assert second.deduplicated is True
    assert candidate.status == "labeled"
    assert payload["feedback"] == {
        "label": "helpful",
        "source": "user_reported",
        "evidence_sha256": first.evidence_sha256,
    }
    assert evidence_ref not in candidate.expected_json
    with pytest.raises(ProactiveFeedbackConflict):
        record_proactive_outreach_feedback(
            db_session,
            log_id=int(row.id),
            label="intrusive",
            source="user_reported",
            evidence_ref="qq-message:another-reference",
        )
