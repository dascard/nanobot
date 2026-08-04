"""Skill Registry 描述检索、作用域隔离和版本治理测试。"""

from __future__ import annotations

from sqlalchemy import select

from core.db.models.semantic import SemanticIndexItem
from core.skills import (
    SkillLifecycleService,
    SkillResolutionContext,
    SkillScopeTarget,
    SqlAlchemySkillProvider,
    parse_skill_bundle,
    select_skills_for_query,
)


def _bundle(
    name: str,
    version: str,
    *,
    description: str,
    capabilities: str,
    dependencies: str = "",
    applies_to: str = "chat",
    body: str = "# 私有正文\n\n只在工具调用后加载。",
):
    return parse_skill_bundle(
        (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            "metadata:\n"
            f'  version: "{version}"\n'
            f'  nanobot.dependencies: "{dependencies}"\n'
            '  nanobot.permissions: ""\n'
            f'  nanobot.capabilities: "{capabilities}"\n'
            f'  nanobot.applies-to: "{applies_to}"\n'
            "---\n\n"
            f"{body}\n"
        ).encode("utf-8")
    )


def _install(db, target: SkillScopeTarget, bundle):
    return SkillLifecycleService(db).install(
        target,
        bundle,
        actor_id="governance-test",
        trusted_source=True,
    )


def test_skill_registry_rag_index_selects_dependency_closure_without_body_or_scope_leak(
    db_session,
):
    user_target = SkillScopeTarget("user", "qq:user:registry-u1")
    other_target = SkillScopeTarget("user", "qq:user:registry-u2")
    base = _install(
        db_session,
        user_target,
        _bundle(
            "base-guide",
            "1.0.0",
            description="为研究流程提供基础证据核验。",
            capabilities="证据核验",
            body="BASE_BODY_MUST_STAY_LAZY",
        ),
    )
    research = _install(
        db_session,
        user_target,
        _bundle(
            "research-guide",
            "2.0.0",
            description="执行深入研究、来源比对与结论整理。",
            capabilities="deep-research,深入研究",
            dependencies="base-guide@1.0.0",
            body="RESEARCH_BODY_MUST_STAY_LAZY",
        ),
    )
    hidden = _install(
        db_session,
        other_target,
        _bundle(
            "hidden-guide",
            "1.0.0",
            description="执行深入研究但只属于另一个用户。",
            capabilities="深入研究",
            body="OTHER_OWNER_SECRET_BODY",
        ),
    )
    db_session.commit()

    visible_lock = SqlAlchemySkillProvider(db_session).resolve_lock(
        SkillResolutionContext(
            targets=(SkillScopeTarget("builtin", "builtin"), user_target),
            executable_tool_names=frozenset(
                {"ai_daily", "schedule_task", "sql_analysis"}
            ),
        )
    )
    result = select_skills_for_query(
        db_session,
        lock=visible_lock,
        query="请做一次深入研究并核验来源",
        runtime_chat_type="private",
    )

    assert tuple(entry.name for entry in result.selected_lock.entries) == (
        "base-guide",
        "research-guide",
    )
    assert result.registry.require("research-guide").registry_dependencies == (
        "base-guide",
    )
    payload = result.registry.require("research-guide").registry_payload()
    assert payload["capability_tags"] == ("deep-research", "深入研究")
    assert payload["required_permissions"] == ()
    assert payload["body_prompt_tokens"] > 0
    assert payload["scope"] == "user"
    assert result.retrieval_mode == "fts_lexical"

    rows = db_session.execute(
        select(SemanticIndexItem).where(
            SemanticIndexItem.source_type == "skill_description"
        )
    ).scalars().all()
    indexed_package_ids = {str(row.source_id) for row in rows}
    assert base.active_package_id in indexed_package_ids
    assert research.active_package_id in indexed_package_ids
    assert hidden.active_package_id not in indexed_package_ids
    indexed_text = "\n".join(
        f"{row.title}\n{row.text}\n{row.lexical_text}\n{row.embedding_text}"
        for row in rows
    )
    assert "RESEARCH_BODY_MUST_STAY_LAZY" not in indexed_text
    assert "OTHER_OWNER_SECRET_BODY" not in indexed_text
    assert "qq:user:registry-u1" not in indexed_text


def test_skill_selection_enforces_applicability_and_unmatched_query_is_empty(
    db_session,
):
    target = SkillScopeTarget("user", "qq:user:scope-u1")
    _install(
        db_session,
        target,
        _bundle(
            "group-only-guide",
            "1.0.0",
            description="处理群组专用协作流程。",
            capabilities="群组协作",
            applies_to="group",
        ),
    )
    _install(
        db_session,
        target,
        _bundle(
            "private-wrapper",
            "1.0.0",
            description="私聊包装器依赖群组专用流程。",
            capabilities="私聊包装",
            dependencies="group-only-guide@1.0.0",
            applies_to="private",
        ),
    )
    db_session.commit()
    lock = SqlAlchemySkillProvider(db_session).resolve_lock(
        SkillResolutionContext(
            targets=(SkillScopeTarget("builtin", "builtin"), target),
            executable_tool_names=frozenset(
                {"ai_daily", "schedule_task", "sql_analysis"}
            ),
        )
    )

    private_result = select_skills_for_query(
        db_session,
        lock=lock,
        query="请处理群组协作",
        runtime_chat_type="private",
    )
    group_result = select_skills_for_query(
        db_session,
        lock=lock,
        query="请处理群组协作",
        runtime_chat_type="group",
    )
    unrelated = select_skills_for_query(
        db_session,
        lock=lock,
        query="你好，介绍一下你自己",
        runtime_chat_type="private",
    )
    invalid_dependency_scope = select_skills_for_query(
        db_session,
        lock=lock,
        query="请使用私聊包装",
        runtime_chat_type="private",
    )

    assert "group-only-guide" not in {
        entry.name for entry in private_result.selected_lock.entries
    }
    assert "group-only-guide" in {
        entry.name for entry in group_result.selected_lock.entries
    }
    assert unrelated.selected_lock.entries == ()
    assert invalid_dependency_scope.selected_lock.entries == ()


def test_skill_admin_records_bundled_version_evaluation_and_cost_metrics(
    client,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "skill-governance")
    headers = {"Authorization": "Bearer skill-governance"}
    listed = client.get("/api/v1/admin/skills", headers=headers)
    assert listed.status_code == 200
    bundled = next(
        item for item in listed.json()["bundled"] if item["name"] == "ai-daily"
    )

    recorded = client.post(
        "/api/v1/admin/skills/evaluations",
        headers=headers,
        json={
            "package_id": bundled["package_id"],
            "suite_id": "skill-routing-zh-v1",
            "evaluator_id": "offline-gate",
            "evaluator_version": "1.0.0",
            "passed": True,
            "score": 0.925,
            "prompt_tokens": 321,
            "cost_microunits": 45,
            "evidence_sha256": "a" * 64,
        },
    )
    assert recorded.status_code == 200
    assert recorded.json()["version"] == "1.0.0"

    metrics = client.get("/api/v1/admin/skills/metrics", headers=headers)
    assert metrics.status_code == 200
    row = next(
        item
        for item in metrics.json()["versions"]
        if item["package_id"] == bundled["package_id"]
    )
    assert row["evaluation_count"] == 1
    assert row["latest_evaluation_passed"] is True
    assert row["latest_score"] == 0.925
    assert row["evaluation_prompt_tokens"] == 321
    assert row["evaluation_cost_microunits"] == 45

    missing = client.post(
        "/api/v1/admin/skills/evaluations",
        headers=headers,
        json={
            "package_id": "skillpkg_0123456789abcdef0123456789abcdef",
            "suite_id": "skill-routing-zh-v1",
            "evaluator_id": "offline-gate",
            "evaluator_version": "1.0.0",
            "passed": False,
            "score": 0,
            "evidence_sha256": "b" * 64,
        },
    )
    assert missing.status_code == 404
