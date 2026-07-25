"""阶段 7C：受控治理晋级、人工权威与事务结算测试。"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json

import pytest


CHAT_STREAM_ID = "qq:42:group"


def _sha256(*parts: object) -> str:
    return hashlib.sha256(
        "\0".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()


def _memory_content(content: str, meaning: str) -> str:
    return f"{content}：{meaning}" if meaning else content


def _seed_model_candidate(
    db_session,
    *,
    run_id: str = "glr_stage7c",
    candidate_type: str = "expression",
    content: str = "摸鱼",
    meaning: str = "上班时偷懒",
    action: str = "new",
    evidence: tuple[
        tuple[int, str, str, str],
        ...,
    ] = (
        (101, "u1", "batch-1", "repeated_usage"),
        (102, "u2", "batch-1", "repeated_usage"),
    ),
    target_memory_id: int | None = None,
):
    from app.group_learning.candidate_service import (
        group_learning_candidate_identity,
    )
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningEvidence,
        GroupLearningRun,
        GroupLearningStreamState,
    )

    (
        normalized_key,
        fingerprint,
        content_hash,
        candidate_id,
    ) = group_learning_candidate_identity(
        chat_stream_id=CHAT_STREAM_ID,
        candidate_type=candidate_type,
        content=content,
        meaning=meaning,
    )
    aspect = {
        "topic": "topics",
        "expression": "expressions",
        "slang": "slang",
        "style": "style",
    }[candidate_type]
    cursor_end = max(item[0] for item in evidence)
    run = GroupLearningRun(
        run_id=run_id,
        idempotency_key=f"idempotency:{run_id}",
        chat_stream_id=CHAT_STREAM_ID,
        trigger="schedule",
        mode="candidate_only",
        selected_aspects_json=json.dumps([aspect]),
        cursor_start_chat_log_id=100,
        cursor_end_chat_log_id=cursor_end,
        context_start_chat_log_id=90,
        context_end_chat_log_id=99,
        candidate_watermark=1,
        rules_generation=1,
        task_contract_version="group_memory_learning_v1",
        model_route="group_memory_learning",
        provider="test-provider",
        model="test-model",
        task_run_id=f"task_{run_id}",
        status="succeeded",
        raw_message_count=len(evidence),
        cleaned_message_count=len(evidence),
        eligible_message_count=len(evidence),
        candidate_count=1,
        accepted_count=int(
            action in {"new", "merge_into", "add_alias"}
        ),
        rejected_count=int(action == "reject"),
        conflict_count=int(action == "conflict_with"),
        completed_at=datetime(2026, 7, 24, 12, 0, 0),
    )
    candidate = GroupLearningCandidate(
        candidate_id=candidate_id,
        chat_stream_id=CHAT_STREAM_ID,
        candidate_type=candidate_type,
        content=content,
        meaning=meaning,
        normalized_key=normalized_key,
        fingerprint=fingerprint,
        content_hash=content_hash,
        source="rule",
        status="pending_model_review",
        rule_id="test.rule.v1",
        rule_version=1,
        source_run_id=run_id,
        model_decision=action,
        model_contract_version="group_memory_learning_v1",
        model_review_run_id=f"task_{run_id}",
        reviewed_content=content,
        reviewed_meaning=meaning,
        reviewed_content_hash=_sha256(content, meaning),
        merge_target_memory_id=(
            target_memory_id if action == "merge_into" else None
        ),
        alias_target_memory_id=(
            target_memory_id if action == "add_alias" else None
        ),
        conflict_group_id=(
            f"glconf_{candidate_id[-16:]}"
            if action == "conflict_with"
            else None
        ),
    )
    state = db_session.get(
        GroupLearningStreamState,
        CHAT_STREAM_ID,
    )
    if state is None:
        state = GroupLearningStreamState(
            chat_stream_id=CHAT_STREAM_ID,
            last_scanned_chat_log_id=cursor_end,
            last_success_chat_log_id=100,
            last_candidate_watermark=1,
            rules_generation=1,
        )
    else:
        state.last_scanned_chat_log_id = cursor_end
    evidence_rows = [
        GroupLearningEvidence(
            evidence_id=f"gle_{run_id}_{chat_log_id}",
            candidate_id=candidate_id,
            chat_log_id=chat_log_id,
            sender_id=sender_id,
            source_run_id=run_id,
            batch_id=batch_id,
            evidence_hash=_sha256(
                candidate_id,
                chat_log_id,
                sender_id,
                evidence_kind,
            ),
            evidence_kind=evidence_kind,
        )
        for chat_log_id, sender_id, batch_id, evidence_kind in evidence
    ]
    db_session.add_all([run, candidate, state, *evidence_rows])
    db_session.commit()
    return run_id, candidate_id


def _service(db_session, *, enabled: bool = True):
    from app.group_learning.governance_service import (
        GroupLearningGovernanceService,
    )
    from core.db.group_learning_governance_adapter import (
        SqlAlchemyGroupLearningGovernanceRepository,
    )

    return GroupLearningGovernanceService(
        repository=SqlAlchemyGroupLearningGovernanceRepository(
            db_session
        ),
        enabled=lambda: enabled,
    )


def test_model_new_and_evidence_policy_create_active_group_memory(
    db_session,
):
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningRun,
        GroupLearningStreamState,
        GroupMemory,
    )

    run_id, candidate_id = _seed_model_candidate(db_session)

    result = _service(db_session).settle_model_run(
        run_id=run_id,
        chat_stream_id=CHAT_STREAM_ID,
    )

    assert result.status == "succeeded"
    assert result.promoted_count == 1
    memory = db_session.query(GroupMemory).one()
    assert memory.chat_stream_id == CHAT_STREAM_ID
    assert memory.group_id == "group_42"
    assert memory.memory_type == "expression"
    assert memory.content == "摸鱼：上班时偷懒"
    assert memory.status == "active"
    assert memory.inject_policy == "auto"
    assert memory.approval_source == "model"
    assert memory.governance_mode == "automatic"
    assert memory.model_review_run_id == f"task_{run_id}"
    assert memory.model_contract_version == (
        "group_memory_learning_v1"
    )
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one()
    assert candidate.status == "accepted"
    assert candidate.approval_source == "model"
    assert candidate.promoted_group_memory_id == memory.id
    run = db_session.get(GroupLearningRun, run_id)
    assert run.mode == "active"
    assert run.status == "succeeded"
    state = db_session.get(
        GroupLearningStreamState,
        CHAT_STREAM_ID,
    )
    assert state.last_success_chat_log_id == 102
    assert state.last_success_run_id == run_id


def test_model_accept_with_insufficient_evidence_waits_without_memory(
    db_session,
):
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningRun,
        GroupLearningStreamState,
        GroupMemory,
    )

    run_id, candidate_id = _seed_model_candidate(
        db_session,
        evidence=((101, "u1", "batch-1", "repeated_usage"),),
    )

    result = _service(db_session).settle_model_run(
        run_id=run_id,
        chat_stream_id=CHAT_STREAM_ID,
    )

    assert result.status == "succeeded"
    assert result.promoted_count == 0
    assert result.waiting_count == 1
    assert db_session.query(GroupMemory).count() == 0
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one()
    assert candidate.status == "waiting_for_evidence"
    assert candidate.waiting_reason_code == "insufficient_evidence"
    run = db_session.get(GroupLearningRun, run_id)
    assert run.waiting_count == 1
    state = db_session.get(
        GroupLearningStreamState,
        CHAT_STREAM_ID,
    )
    assert state.last_success_chat_log_id == 101


def test_model_reject_and_conflict_never_create_group_memory(
    db_session,
):
    from core.db.models import GroupLearningCandidate, GroupMemory

    reject_run, reject_candidate = _seed_model_candidate(
        db_session,
        run_id="glr_reject",
        action="reject",
    )
    reject = _service(db_session).settle_model_run(
        run_id=reject_run,
        chat_stream_id=CHAT_STREAM_ID,
    )
    assert reject.rejected_count == 1
    assert db_session.query(GroupMemory).count() == 0
    assert db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=reject_candidate
    ).one().status == "rejected"

    conflict_run, conflict_candidate = _seed_model_candidate(
        db_session,
        run_id="glr_conflict",
        content="摸鱼",
        meaning="钓鱼活动",
        action="conflict_with",
        evidence=(
            (201, "u1", "batch-2", "repeated_usage"),
            (202, "u2", "batch-2", "repeated_usage"),
        ),
    )
    conflict = _service(db_session).settle_model_run(
        run_id=conflict_run,
        chat_stream_id=CHAT_STREAM_ID,
    )
    assert conflict.conflict_count == 1
    assert db_session.query(GroupMemory).count() == 0
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=conflict_candidate
    ).one()
    assert candidate.status == "conflict"
    assert candidate.conflict_group_id


def test_human_accept_promotes_directly_without_evidence_or_model_review(
    db_session,
):
    from core.db.models import GroupLearningCandidate, GroupMemory

    _run_id, candidate_id = _seed_model_candidate(
        db_session,
        run_id="glr_human",
        action="new",
        evidence=((101, "u1", "batch-1", "message"),),
    )
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one()
    candidate.model_decision = ""
    candidate.model_contract_version = ""
    candidate.model_review_run_id = ""
    candidate.reviewed_content = None
    candidate.reviewed_meaning = None
    candidate.reviewed_content_hash = None
    db_session.commit()

    result = _service(db_session).review_human_candidate(
        candidate_id=candidate_id,
        reviewer_id="admin-1",
        action="edit_accept",
        reviewed_content="摸鱼一下",
        reviewed_meaning="短暂休息",
        reviewed_at=datetime(2026, 7, 24, 13, 0, 0),
    )

    assert result.status == "succeeded"
    assert result.promoted_count == 1
    memory = db_session.query(GroupMemory).one()
    assert memory.content == "摸鱼一下：短暂休息"
    assert memory.approval_source == "human"
    assert memory.governance_mode == "human_managed"
    assert memory.human_reviewer_id == "admin-1"
    assert memory.human_action == "edit_accept"
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one()
    assert candidate.approval_source == "human"
    assert candidate.promoted_group_memory_id == memory.id


def test_human_merge_requires_same_session_type_active_target(
    db_session,
):
    from core.db.models import GroupLearningCandidate, GroupMemory

    _run_id, candidate_id = _seed_model_candidate(
        db_session,
        run_id="glr_human_merge",
        evidence=((101, "u1", "batch-1", "message"),),
    )
    target = GroupMemory(
        chat_stream_id=CHAT_STREAM_ID,
        group_id="group_42",
        memory_type="expression",
        content="原有表达",
        content_hash=hashlib.sha256(
            "原有表达".encode("utf-8")
        ).hexdigest(),
        status="active",
        inject_policy="auto",
        approval_source="model",
        governance_mode="automatic",
        approved_content_hash=_sha256("原有表达", ""),
    )
    wrong_scope = GroupMemory(
        chat_stream_id="qq:43:group",
        group_id="group_43",
        memory_type="expression",
        content="其他群表达",
        content_hash=hashlib.sha256(
            "其他群表达".encode("utf-8")
        ).hexdigest(),
        status="active",
        inject_policy="auto",
    )
    db_session.add_all([target, wrong_scope])
    db_session.commit()

    with pytest.raises(ValueError, match="当前会话"):
        _service(db_session).review_human_candidate(
            candidate_id=candidate_id,
            reviewer_id="admin-1",
            action="merge",
            reviewed_content="摸鱼",
            reviewed_meaning="上班时偷懒",
            target_memory_id=wrong_scope.id,
        )

    result = _service(db_session).review_human_candidate(
        candidate_id=candidate_id,
        reviewer_id="admin-1",
        action="merge",
        reviewed_content="摸鱼",
        reviewed_meaning="上班时偷懒",
        target_memory_id=target.id,
    )

    assert result.merged_count == 1
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one()
    assert candidate.status == "merged"
    assert candidate.approval_source == "human"
    assert candidate.human_action == "merge"
    assert candidate.promoted_group_memory_id == target.id
    assert db_session.get(GroupMemory, target.id).content == "原有表达"


def test_human_resolve_conflict_can_replace_target_without_model_review(
    db_session,
):
    from core.db.models import GroupLearningCandidate, GroupMemory

    _run_id, candidate_id = _seed_model_candidate(
        db_session,
        run_id="glr_human_conflict",
        content="摸鱼",
        meaning="短暂休息",
        action="conflict_with",
        evidence=((101, "u1", "batch-1", "message"),),
    )
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one()
    candidate.status = "conflict"
    candidate.conflict_group_id = "glconf_manual"
    target = GroupMemory(
        chat_stream_id=CHAT_STREAM_ID,
        group_id="group_42",
        memory_type="expression",
        content="摸鱼：上班时偷懒",
        content_hash=hashlib.sha256(
            "摸鱼：上班时偷懒".encode("utf-8")
        ).hexdigest(),
        cluster_key="摸鱼",
        status="active",
        inject_policy="auto",
        approval_source="model",
        governance_mode="automatic",
        approved_content_hash=_sha256("摸鱼", "上班时偷懒"),
        conflict_group_id="glconf_manual",
    )
    db_session.add(target)
    db_session.commit()

    result = _service(db_session).review_human_candidate(
        candidate_id=candidate_id,
        reviewer_id="admin-2",
        action="resolve_conflict",
        reviewed_content="摸鱼",
        reviewed_meaning="短暂休息",
        target_memory_id=target.id,
        conflict_resolution="replace_target",
    )

    assert result.promoted_count == 1
    resolved = db_session.get(GroupMemory, target.id)
    assert resolved.content == "摸鱼：短暂休息"
    assert resolved.approval_source == "human"
    assert resolved.governance_mode == "human_managed"
    assert resolved.human_action == "resolve_conflict_replace_target"
    assert resolved.conflict_group_id is None
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one()
    assert candidate.status == "accepted"
    assert candidate.conflict_group_id is None
    assert candidate.promoted_group_memory_id == target.id
    assert candidate.model_review_run_id


def test_model_cannot_overwrite_existing_human_managed_memory(
    db_session,
):
    from core.db.models import GroupLearningCandidate, GroupMemory

    content = _memory_content("摸鱼", "上班时偷懒")
    memory = GroupMemory(
        chat_stream_id=CHAT_STREAM_ID,
        group_id="group_42",
        memory_type="expression",
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        status="active",
        inject_policy="auto",
        approval_source="human",
        governance_mode="human_managed",
        approved_content_hash=_sha256("摸鱼", "上班时偷懒"),
        human_reviewer_id="admin-1",
        human_action="create",
        meta_json=json.dumps({
            "normalized_key": "摸鱼",
            "meaning": "上班时偷懒",
        }),
    )
    db_session.add(memory)
    db_session.commit()
    run_id, candidate_id = _seed_model_candidate(
        db_session,
        run_id="glr_human_duplicate",
    )

    result = _service(db_session).settle_model_run(
        run_id=run_id,
        chat_stream_id=CHAT_STREAM_ID,
    )

    assert result.merged_count == 1
    assert db_session.query(GroupMemory).count() == 1
    db_session.refresh(memory)
    assert memory.governance_mode == "human_managed"
    assert memory.human_reviewer_id == "admin-1"
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one()
    assert candidate.status == "merged"
    assert candidate.promoted_group_memory_id == memory.id


def test_merge_and_alias_only_link_valid_same_scope_target(db_session):
    from core.db.models import GroupLearningCandidate, GroupMemory

    target = GroupMemory(
        chat_stream_id=CHAT_STREAM_ID,
        group_id="group_42",
        memory_type="expression",
        content="摸鱼：上班时偷懒",
        content_hash=hashlib.sha256(
            "摸鱼：上班时偷懒".encode()
        ).hexdigest(),
        status="active",
        inject_policy="auto",
        approval_source="human",
        governance_mode="human_managed",
        approved_content_hash=_sha256("摸鱼", "上班时偷懒"),
        meta_json=json.dumps({
            "normalized_key": "摸鱼",
            "meaning": "上班时偷懒",
        }),
    )
    db_session.add(target)
    db_session.commit()

    merge_run, merge_candidate = _seed_model_candidate(
        db_session,
        run_id="glr_merge",
        content="开摆",
        meaning="开始摸鱼",
        action="merge_into",
        target_memory_id=target.id,
    )
    merge = _service(db_session).settle_model_run(
        run_id=merge_run,
        chat_stream_id=CHAT_STREAM_ID,
    )
    assert merge.merged_count == 1

    alias_run, alias_candidate = _seed_model_candidate(
        db_session,
        run_id="glr_alias",
        content="划水",
        meaning="上班时偷懒",
        action="add_alias",
        target_memory_id=target.id,
        evidence=(
            (201, "u1", "batch-2", "repeated_usage"),
            (202, "u2", "batch-2", "repeated_usage"),
        ),
    )
    alias = _service(db_session).settle_model_run(
        run_id=alias_run,
        chat_stream_id=CHAT_STREAM_ID,
    )

    assert alias.alias_count == 1
    assert db_session.query(GroupMemory).count() == 1
    merged = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=merge_candidate
    ).one()
    aliased = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=alias_candidate
    ).one()
    assert merged.status == "merged"
    assert merged.promoted_group_memory_id == target.id
    assert aliased.status == "alias"
    assert aliased.promoted_group_memory_id == target.id
    db_session.refresh(target)
    assert target.governance_mode == "human_managed"


def test_same_term_with_different_meaning_becomes_conflict(
    db_session,
):
    from core.db.models import GroupLearningCandidate, GroupMemory

    existing_content = _memory_content("摸鱼", "上班时偷懒")
    existing = GroupMemory(
        chat_stream_id=CHAT_STREAM_ID,
        group_id="group_42",
        memory_type="slang",
        content=existing_content,
        content_hash=hashlib.sha256(
            existing_content.encode()
        ).hexdigest(),
        status="active",
        inject_policy="auto",
        approval_source="model",
        governance_mode="automatic",
        approved_content_hash=_sha256("摸鱼", "上班时偷懒"),
        meta_json=json.dumps({
            "normalized_key": "摸鱼",
            "meaning": "上班时偷懒",
        }),
    )
    db_session.add(existing)
    db_session.commit()
    run_id, candidate_id = _seed_model_candidate(
        db_session,
        run_id="glr_same_term_conflict",
        candidate_type="slang",
        content="摸鱼",
        meaning="进行钓鱼活动",
        evidence=(
            (101, "u1", "batch-1", "explicit_definition"),
        ),
    )

    result = _service(db_session).settle_model_run(
        run_id=run_id,
        chat_stream_id=CHAT_STREAM_ID,
    )

    assert result.conflict_count == 1
    assert db_session.query(GroupMemory).count() == 1
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one()
    assert candidate.status == "conflict"
    assert candidate.conflict_group_id
    assert candidate.promoted_group_memory_id is None


def test_model_governance_settlement_is_idempotent(db_session):
    from core.db.models import GroupMemory

    run_id, _candidate_id = _seed_model_candidate(
        db_session,
        run_id="glr_replay",
    )
    service = _service(db_session)

    first = service.settle_model_run(
        run_id=run_id,
        chat_stream_id=CHAT_STREAM_ID,
    )
    second = service.settle_model_run(
        run_id=run_id,
        chat_stream_id=CHAT_STREAM_ID,
    )

    assert first.status == "succeeded"
    assert second.status == "replayed"
    assert second.promoted_count == 1
    assert db_session.query(GroupMemory).count() == 1


def test_governance_commit_failure_rolls_back_memory_candidate_and_cursor(
    db_session,
    monkeypatch,
):
    import pytest

    from app.group_learning.governance_service import (
        GroupLearningGovernanceService,
    )
    from core.db.group_learning_governance_adapter import (
        SqlAlchemyGroupLearningGovernanceRepository,
    )
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningRun,
        GroupLearningStreamState,
        GroupMemory,
    )

    run_id, candidate_id = _seed_model_candidate(
        db_session,
        run_id="glr_commit_failure",
    )
    repository = SqlAlchemyGroupLearningGovernanceRepository(
        db_session
    )

    def fail_commit():
        raise RuntimeError("模拟提交失败")

    monkeypatch.setattr(repository, "commit", fail_commit)
    service = GroupLearningGovernanceService(
        repository=repository,
        enabled=lambda: True,
    )

    with pytest.raises(RuntimeError, match="模拟提交失败"):
        service.settle_model_run(
            run_id=run_id,
            chat_stream_id=CHAT_STREAM_ID,
        )

    db_session.expire_all()
    assert db_session.query(GroupMemory).count() == 0
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one()
    assert candidate.status == "pending_model_review"
    assert candidate.promoted_group_memory_id is None
    run = db_session.get(GroupLearningRun, run_id)
    assert run.mode == "candidate_only"
    state = db_session.get(
        GroupLearningStreamState,
        CHAT_STREAM_ID,
    )
    assert state.last_success_chat_log_id == 100


def test_tool_report_only_run_settles_without_advancing_schedule_cursor(
    db_session,
):
    from core.db.models import (
        GroupLearningRun,
        GroupLearningStreamState,
        GroupMemory,
    )

    run = GroupLearningRun(
        run_id="glr_report_only",
        idempotency_key="idempotency:glr_report_only",
        chat_stream_id=CHAT_STREAM_ID,
        trigger="tool",
        mode="candidate_only",
        selected_aspects_json='["titles","quality"]',
        cursor_start_chat_log_id=100,
        cursor_end_chat_log_id=103,
        context_start_chat_log_id=90,
        context_end_chat_log_id=99,
        rules_generation=1,
        status="succeeded",
        raw_message_count=3,
        cleaned_message_count=3,
        eligible_message_count=3,
    )
    state = GroupLearningStreamState(
        chat_stream_id=CHAT_STREAM_ID,
        last_scanned_chat_log_id=103,
        last_success_chat_log_id=100,
        rules_generation=1,
    )
    db_session.add_all([run, state])
    db_session.commit()

    result = _service(db_session).settle_model_run(
        run_id=run.run_id,
        chat_stream_id=CHAT_STREAM_ID,
    )

    assert result.status == "succeeded"
    assert result.promoted_count == 0
    assert db_session.query(GroupMemory).count() == 0
    db_session.refresh(run)
    assert run.mode == "active"
    db_session.refresh(state)
    assert state.last_success_chat_log_id == 100
    assert state.last_success_run_id == ""


def test_feature_off_stops_new_governance_writes_immediately(
    db_session,
):
    from core.db.models import (
        GroupLearningCandidate,
        GroupLearningRun,
        GroupMemory,
    )

    run_id, candidate_id = _seed_model_candidate(
        db_session,
        run_id="glr_disabled",
    )

    result = _service(
        db_session,
        enabled=False,
    ).settle_model_run(
        run_id=run_id,
        chat_stream_id=CHAT_STREAM_ID,
    )

    assert result.status == "disabled"
    assert db_session.query(GroupMemory).count() == 0
    assert db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one().status == "pending_model_review"
    run = db_session.get(GroupLearningRun, run_id)
    assert run.mode == "candidate_only"
