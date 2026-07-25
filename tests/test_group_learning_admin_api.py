"""阶段 8：群学习管理工作台 Admin API 合同测试。"""

from __future__ import annotations

import hashlib
import json


HEADERS = {"Authorization": "Bearer group-learning-test-token"}
CHAT_STREAM_ID = "qq:42:group"


def _enable_admin(monkeypatch) -> None:
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "group-learning-test-token",
    )


def _seed_candidate(db_session) -> tuple[str, int]:
    from app.group_learning.candidate_service import (
        group_learning_candidate_identity,
    )
    from core.db.models import (
        ChatLog,
        GroupLearningCandidate,
        GroupLearningEvidence,
        GroupLearningRun,
        GroupMemory,
    )

    content = "摸鱼"
    meaning = "上班时偷懒"
    (
        normalized_key,
        fingerprint,
        content_hash,
        candidate_id,
    ) = group_learning_candidate_identity(
        chat_stream_id=CHAT_STREAM_ID,
        candidate_type="slang",
        content=content,
        meaning=meaning,
    )
    run = GroupLearningRun(
        run_id="glr_admin_api",
        idempotency_key="group-learning:admin-api",
        chat_stream_id=CHAT_STREAM_ID,
        trigger="manual",
        mode="candidate_only",
        selected_aspects_json='["slang"]',
        cursor_start_chat_log_id=0,
        cursor_end_chat_log_id=1,
        context_start_chat_log_id=0,
        context_end_chat_log_id=0,
        rules_generation=1,
        status="succeeded",
        candidate_count=1,
    )
    candidate = GroupLearningCandidate(
        candidate_id=candidate_id,
        chat_stream_id=CHAT_STREAM_ID,
        candidate_type="slang",
        content=content,
        meaning=meaning,
        normalized_key=normalized_key,
        fingerprint=fingerprint,
        content_hash=content_hash,
        source="rule",
        status="conflict",
        rule_id="slang.explicit_definition.v1",
        rule_version=1,
        source_run_id=run.run_id,
        conflict_group_id="glconf_admin",
    )
    log = ChatLog(
        id=1,
        user_id="group_42",
        session_id="group_42",
        sender_name="用户甲",
        role="ambient",
        content=(
            "摸鱼的意思是上班时偷懒，"
            "api_key=super-secret-value"
        ),
        meta_json=json.dumps({"sender": {"id": "10001"}}),
    )
    evidence = GroupLearningEvidence(
        evidence_id="gle_admin_api",
        candidate_id=candidate_id,
        chat_log_id=1,
        sender_id="10001",
        source_run_id=run.run_id,
        batch_id=run.run_id,
        evidence_hash=hashlib.sha256(b"evidence").hexdigest(),
        evidence_kind="explicit_definition",
    )
    target = GroupMemory(
        chat_stream_id=CHAT_STREAM_ID,
        group_id="group_42",
        memory_type="slang",
        content="摸鱼：工作期间暂时休息",
        content_hash=hashlib.sha256(
            "摸鱼：工作期间暂时休息".encode("utf-8")
        ).hexdigest(),
        cluster_key="摸鱼",
        status="active",
        inject_policy="auto",
        approval_source="model",
        governance_mode="automatic",
        approved_content_hash=hashlib.sha256(
            "摸鱼\0工作期间暂时休息".encode("utf-8")
        ).hexdigest(),
        conflict_group_id="glconf_admin",
    )
    db_session.add_all([run, candidate, log, evidence, target])
    db_session.commit()
    return candidate_id, int(target.id)


def test_descriptors_are_the_only_web_enum_and_default_source(
    client,
    db_session,
    monkeypatch,
):
    _enable_admin(monkeypatch)

    response = client.get(
        "/api/v1/admin/group-learning/descriptors",
        headers=HEADERS,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["feature_enabled"] is False
    assert [item["aspect_id"] for item in data["aspects"]] == [
        "topics",
        "expressions",
        "slang",
        "style",
        "titles",
        "quotes",
        "quality",
    ]
    assert [
        item["aspect_id"]
        for item in data["aspects"]
        if item["schedule_default"]
    ] == ["topics", "expressions", "slang", "style"]
    assert data["candidate_types"] == [
        "topic",
        "expression",
        "slang",
        "style",
    ]
    assert "conflict" in data["candidate_statuses"]
    assert "resolve_conflict" in data["human_actions"]
    assert data["rule_registry"]["sha256"]
    assert all("globally_enabled" in item for item in data["rules"])


def test_feature_schedule_and_rule_writes_are_audited_and_replay_safe(
    client,
    db_session,
    monkeypatch,
):
    from core.db.models import AdminAuditLog, GroupLearningSchedule

    _enable_admin(monkeypatch)
    feature_body = {
        "request_id": "feature-enable-0001",
        "reason": "阶段 8 管理端测试",
        "enabled": True,
    }
    first_feature = client.put(
        "/api/v1/admin/group-learning/features",
        json=feature_body,
        headers=HEADERS,
    )
    replay_feature = client.put(
        "/api/v1/admin/group-learning/features",
        json=feature_body,
        headers=HEADERS,
    )
    assert first_feature.status_code == 200, first_feature.text
    assert replay_feature.json()["replayed"] is True

    schedule_body = {
        "request_id": "schedule-put-0001",
        "reason": "加入显式白名单",
        "enabled": True,
    }
    first_schedule = client.put(
        (
            "/api/v1/admin/group-learning/sessions/"
            "qq:42:group/schedule"
        ),
        json=schedule_body,
        headers=HEADERS,
    )
    replay_schedule = client.put(
        (
            "/api/v1/admin/group-learning/sessions/"
            "qq:42:group/schedule"
        ),
        json=schedule_body,
        headers=HEADERS,
    )
    assert first_schedule.status_code == 200, first_schedule.text
    assert first_schedule.json()["schedule"]["aspects"] == [
        "topics",
        "expressions",
        "slang",
        "style",
    ]
    assert replay_schedule.json()["replayed"] is True
    assert db_session.get(
        GroupLearningSchedule,
        CHAT_STREAM_ID,
    ).config_generation == 1

    rule_body = {
        "request_id": "rule-disable-0001",
        "reason": "会话级误报处置",
        "enabled": False,
        "chat_stream_id": CHAT_STREAM_ID,
    }
    rule_response = client.put(
        (
            "/api/v1/admin/group-learning/rules/"
            "slang.explicit_definition.v1/activation"
        ),
        json=rule_body,
        headers=HEADERS,
    )
    assert rule_response.status_code == 200, rule_response.text
    assert rule_response.json()["session_disabled"] == {
        CHAT_STREAM_ID: ["slang.explicit_definition.v1"]
    }
    overview = client.get(
        (
            "/api/v1/admin/group-learning/sessions/"
            "qq:42:group/overview"
        ),
        headers=HEADERS,
    )
    assert "slang.explicit_definition.v1" in (
        overview.json()["disabled_rule_ids"]
    )
    assert db_session.query(AdminAuditLog).filter(
        AdminAuditLog.action.in_(
            (
                "group_learning.feature.update",
                "group_learning.schedule.put",
                "group_learning.rule.activation",
            )
        )
    ).count() == 3


def test_candidate_evidence_preview_is_bounded_redacted_and_governable(
    client,
    db_session,
    monkeypatch,
):
    from core.db.models import AdminAuditLog, GroupLearningCandidate

    _enable_admin(monkeypatch)
    candidate_id, target_id = _seed_candidate(db_session)
    client.put(
        "/api/v1/admin/group-learning/features",
        json={
            "request_id": "feature-enable-0002",
            "reason": "启用人工治理测试",
            "enabled": True,
        },
        headers=HEADERS,
    )

    detail_response = client.get(
        f"/api/v1/admin/group-learning/candidates/{candidate_id}",
        headers=HEADERS,
    )

    assert detail_response.status_code == 200, detail_response.text
    evidence = detail_response.json()["evidence"][0]
    assert evidence["available"] is True
    assert "super-secret-value" not in evidence["content_preview"]
    assert "[redacted]" in evidence["content_preview"]
    assert evidence["preview_redacted"] is True
    assert evidence["sender_ref"].startswith("sender:")
    assert "10001" not in evidence["sender_ref"]

    review_body = {
        "request_id": "candidate-review-0001",
        "reason": "人工确认保留现有释义",
        "action": "resolve_conflict",
        "reviewed_content": "摸鱼",
        "reviewed_meaning": "上班时偷懒",
        "target_memory_id": target_id,
        "conflict_resolution": "keep_target",
    }
    first = client.post(
        (
            "/api/v1/admin/group-learning/candidates/"
            f"{candidate_id}/review"
        ),
        json=review_body,
        headers=HEADERS,
    )
    replay = client.post(
        (
            "/api/v1/admin/group-learning/candidates/"
            f"{candidate_id}/review"
        ),
        json=review_body,
        headers=HEADERS,
    )

    assert first.status_code == 200, first.text
    assert first.json()["result"]["merged_count"] == 1
    assert replay.json()["replayed"] is True
    candidate = db_session.query(GroupLearningCandidate).filter_by(
        candidate_id=candidate_id
    ).one()
    assert candidate.status == "merged"
    assert candidate.human_action == "resolve_conflict"
    audit = db_session.query(AdminAuditLog).filter_by(
        action="group_learning.candidate.review",
        target_id=candidate_id,
    ).one()
    detail = json.loads(audit.detail_json)
    assert "上班时偷懒" not in audit.detail_json
    assert detail["reviewed_chars"] == 7
    assert detail["reviewed_sha256"]


def test_dry_run_cannot_bypass_disabled_rule(
    client,
    db_session,
    monkeypatch,
):
    _enable_admin(monkeypatch)
    client.put(
        (
            "/api/v1/admin/group-learning/rules/"
            "slang.explicit_definition.v1/activation"
        ),
        json={
            "request_id": "rule-disable-0002",
            "reason": "验证 dry-run 边界",
            "enabled": False,
            "chat_stream_id": CHAT_STREAM_ID,
        },
        headers=HEADERS,
    )

    response = client.post(
        "/api/v1/admin/group-learning/rules/dry-run",
        json={
            "chat_stream_id": CHAT_STREAM_ID,
            "text": "摸鱼的意思是上班时偷懒",
            "rule_ids": ["slang.explicit_definition.v1"],
        },
        headers=HEADERS,
    )

    assert response.status_code == 400
    assert "不能绕过" in response.text


def test_mutation_request_id_reuse_with_changed_payload_is_rejected(
    client,
    db_session,
    monkeypatch,
):
    from core.db.models import AdminIdempotencyRecord

    _enable_admin(monkeypatch)
    first = client.put(
        "/api/v1/admin/group-learning/features",
        json={
            "request_id": "feature-payload-conflict-0001",
            "reason": "首次启用",
            "enabled": True,
        },
        headers=HEADERS,
    )
    changed = client.put(
        "/api/v1/admin/group-learning/features",
        json={
            "request_id": "feature-payload-conflict-0001",
            "reason": "改成关闭",
            "enabled": False,
        },
        headers=HEADERS,
    )

    assert first.status_code == 200, first.text
    assert changed.status_code == 409, changed.text
    assert changed.json()["detail"]["code"] == (
        "admin_idempotency_conflict"
    )
    row = db_session.get(
        AdminIdempotencyRecord,
        "feature-payload-conflict-0001",
    )
    assert row is not None
    assert row.status == "succeeded"
    assert row.request_sha256
    assert "首次启用" not in row.result_json
