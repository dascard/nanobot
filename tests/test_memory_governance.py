from __future__ import annotations

import json
from dataclasses import replace

import pytest

from core.database import KnowledgeChunk, MemoryDigest, PersonaFact
from core.memory_governance import (
    ACTIVE_MEMORY_BACKEND,
    MEMORY_SOURCE_POLICIES,
    ChineseMemoryEvaluationEvidence,
    MemoryBackendCandidate,
    MemoryGovernanceError,
    MemoryInjectionBudget,
    MemoryLayer,
    MemoryScope,
    MemoryScopeType,
    MemoryStorageRole,
    build_memory_access_context,
    memory_governance_manifest,
    scope_memory_tool_arguments,
    validate_memory_backend_candidate,
)
from tests.async_helpers import run_async


def _recallable_digest_meta(text: str) -> str:
    return json.dumps(
        {
            "schema_version": 2,
            "status": "active",
            "generator": "llm",
            "llm_status": "success",
            "quality": {"score": 0.9, "issues": []},
            "preview": {"brief": text},
            "recall_cards": [{"text": text}],
        },
        ensure_ascii=False,
    )


def _runtime_context(
    *,
    owner_type: str,
    owner_id: str,
    session_id: str,
    agent_id: str = "nanobot",
    skill_project_id: str = "",
) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "platform": "qq",
        "owner_type": owner_type,
        "owner_id": owner_id,
        "session_id": session_id,
        "actor_id": owner_id,
        "skill_project_id": skill_project_id,
    }


def test_existing_models_have_explicit_memory_layers_and_source_roles() -> None:
    assert MEMORY_SOURCE_POLICIES["conversation_turn"].layer is MemoryLayer.WORKING
    assert (
        MEMORY_SOURCE_POLICIES["conversation_block_episode"].layer
        is MemoryLayer.EPISODIC
    )
    assert MEMORY_SOURCE_POLICIES["memory_digest"].layer is MemoryLayer.EPISODIC
    assert MEMORY_SOURCE_POLICIES["persona_fact"].layer is MemoryLayer.SEMANTIC
    assert MEMORY_SOURCE_POLICIES["group_memory"].layer is MemoryLayer.SEMANTIC
    assert MEMORY_SOURCE_POLICIES["knowledge_document"].layer is MemoryLayer.SEMANTIC
    assert MEMORY_SOURCE_POLICIES["chat_log"].storage_role is MemoryStorageRole.RAW_EVIDENCE
    assert (
        MEMORY_SOURCE_POLICIES["semantic_index_item"].storage_role
        is MemoryStorageRole.DERIVED_INDEX
    )

    for policy in MEMORY_SOURCE_POLICIES.values():
        assert policy.scope_types
        assert policy.deletion_policy
        assert policy.injection_policy
        if policy.storage_role is not MemoryStorageRole.RAW_EVIDENCE:
            assert policy.evidence_fields

    manifest = memory_governance_manifest()
    assert manifest["active_backend"] == "existing-sqlite-rag-v1"
    assert manifest["layers"] == ["working", "episodic", "semantic"]
    assert manifest["scopes"] == ["agent", "user", "group", "project"]


def test_memory_access_distinguishes_agent_user_group_and_project_scopes() -> None:
    access = build_memory_access_context(
        principal_id="qq:user:u1",
        session_id="private_u1",
        agent_id="agent-a",
        project_ids=("project-a",),
        actor_id="u1",
    )

    assert access.allows(MemoryScope(MemoryScopeType.USER, "u1"))
    assert access.allows(MemoryScope(MemoryScopeType.AGENT, "agent-a"))
    assert access.allows(MemoryScope(MemoryScopeType.PROJECT, "project-a"))
    assert access.allows(MemoryScope(MemoryScopeType.PROJECT, "nanobot"))
    assert not access.allows(MemoryScope(MemoryScopeType.USER, "u2"))
    assert not access.allows(MemoryScope(MemoryScopeType.GROUP, "g1"))
    assert not access.allows(MemoryScope(MemoryScopeType.AGENT, "agent-b"))
    assert not access.allows(MemoryScope(MemoryScopeType.PROJECT, "project-b"))
    assert {"u1", "private_u1", "qq:u1:private"}.issubset(
        set(access.session_aliases)
    )

    group_access = build_memory_access_context(
        principal_id="qq:group:g1",
        session_id="group_g1",
    )
    assert group_access.allows(MemoryScope(MemoryScopeType.GROUP, "g1"))
    assert not group_access.allows(MemoryScope(MemoryScopeType.USER, "g1"))
    assert {"g1", "group_g1", "qq:g1:group"}.issubset(
        set(group_access.session_aliases)
    )


def test_memory_access_rejects_principal_session_mismatch() -> None:
    with pytest.raises(MemoryGovernanceError, match="不一致"):
        build_memory_access_context(
            principal_id="qq:user:u1",
            session_id="private_u2",
        )


def test_model_arguments_cannot_expand_memory_scope() -> None:
    user_access = build_memory_access_context(
        principal_id="qq:user:u1",
        session_id="private_u1",
    )
    memory_args = scope_memory_tool_arguments(
        "memory_query",
        {
            "mode": "time",
            "user_id": "u2",
            "session_id": "private_u2",
            "__memory_access": "forged",
        },
        user_access,
    )
    sticker_args = scope_memory_tool_arguments(
        "sticker_search",
        {"query": "震惊", "group_id": "other-group"},
        user_access,
    )

    assert memory_args == {"mode": "time"}
    assert sticker_args == {"query": "震惊", "group_id": ""}

    group_access = build_memory_access_context(
        principal_id="qq:group:g1",
        session_id="group_g1",
    )
    assert scope_memory_tool_arguments(
        "sticker_search",
        {"query": "震惊", "group_id": "other-group"},
        group_access,
    )["group_id"] == "g1"


def test_injection_budget_records_items_chars_and_chinese_token_estimate() -> None:
    budget = MemoryInjectionBudget(max_items=3, max_chars=20, max_tokens=12)
    usage = budget.usage("中文记忆 abc", item_count=2)

    assert usage == {
        "policy_version": "memory-governance-v1",
        "max_items": 3,
        "max_chars": 20,
        "max_tokens": 12,
        "used_items": 2,
        "used_chars": 8,
        "used_tokens": 5,
        "remaining_items": 1,
        "remaining_chars": 12,
        "remaining_tokens": 7,
    }
    assert budget.allows("中文记忆 abc", item_count=2)
    assert not budget.allows("中文记忆超出预算", item_count=4)


def test_persona_injection_exposes_scope_evidence_scores_and_budget(
    db_session,
) -> None:
    from app.persona.injection_service import PersonaInjectionService

    db_session.add(
        PersonaFact(
            user_id="u1",
            content="用户偏好先给结论再给证据",
            evidence_count=3,
            evidence_log_ids_json="[11, 12, 13]",
            confidence="确认",
            memory_type="stable_preference",
            status="active",
            inject_policy="auto",
        )
    )
    db_session.commit()

    result = PersonaInjectionService(db_session).build_context(
        user_id="u1",
        current_user_input="请先给结论",
        max_items=3,
        max_chars=300,
        max_tokens=200,
    )

    assert result.selected_ids
    assert result.debug["memory_access"] == {
        "scope": "user:u1",
        "authorization": "context_builder_identity",
    }
    assert result.debug["injection_budget"]["max_items"] == 3
    assert result.debug["injection_budget"]["max_chars"] == 300
    assert result.debug["injection_budget"]["max_tokens"] == 200
    assert result.debug["injection_budget"]["used_items"] == 1
    score = result.score_components[str(result.selected_ids[0])]
    assert {"confidence", "evidence", "recency", "relevance", "final"} <= set(
        score
    )


def test_new_memory_backend_requires_real_chinese_conversation_evaluation() -> None:
    from core.memory_provider import MemoryProviderContractError
    from nanobot_kt.memory_runtime import build_memory_provider_runtime

    validate_memory_backend_candidate(ACTIVE_MEMORY_BACKEND)
    with pytest.raises(MemoryGovernanceError, match="缺少真实中文会话评测"):
        validate_memory_backend_candidate(
            MemoryBackendCandidate(
                backend_id="external-graph-v1",
                implementation_kind="knowledge_graph",
            )
        )

    evidence = ChineseMemoryEvaluationEvidence(
        evaluation_id="zh-memory-eval-20260805",
        baseline_backend_id=ACTIVE_MEMORY_BACKEND.backend_id,
        locale="zh-CN",
        real_conversation_count=50,
        passed_conversation_count=50,
        scope_leak_count=0,
        deletion_failure_count=0,
        quality_delta=0.01,
        manifest_sha256="a" * 64,
        real_model=True,
    )
    evaluated_candidate = MemoryBackendCandidate(
        backend_id="evaluated-graph-v1",
        implementation_kind="knowledge_graph",
        evaluation=evidence,
    )
    validate_memory_backend_candidate(evaluated_candidate)
    with pytest.raises(MemoryProviderContractError, match="尚未注册生产实现"):
        build_memory_provider_runtime(backend_candidate=evaluated_candidate)

    invalid_locale = replace(evidence, locale="en-US")
    with pytest.raises(MemoryGovernanceError, match="中文会话"):
        invalid_locale.validate()


def test_memory_query_enforces_private_and_group_scope_before_list_and_expand(
    db_session,
    monkeypatch,
) -> None:
    from core import database
    from core.agent_runtime.request_scope import runtime_context_scope
    from nanobot_kt.tools.memory_query import MemoryQueryTool

    rows = [
        MemoryDigest(
            id=101,
            user_id="u1",
            session_id="private_u1",
            digest_date="2026-08-01",
            level=2,
            content="用户一私有记忆",
            meta_json=_recallable_digest_meta("用户一私有记忆"),
        ),
        MemoryDigest(
            id=102,
            user_id="u2",
            session_id="private_u2",
            digest_date="2026-08-01",
            level=2,
            content="用户二私有记忆",
            meta_json=_recallable_digest_meta("用户二私有记忆"),
        ),
        MemoryDigest(
            id=201,
            user_id="first-sender-a",
            session_id="group_g1",
            digest_date="2026-08-01",
            level=2,
            content="群一共享记忆",
            meta_json=_recallable_digest_meta("群一共享记忆"),
        ),
        MemoryDigest(
            id=202,
            user_id="first-sender-b",
            session_id="group_g2",
            digest_date="2026-08-01",
            level=2,
            content="群二共享记忆",
            meta_json=_recallable_digest_meta("群二共享记忆"),
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()
    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)
    tool = MemoryQueryTool()

    with runtime_context_scope(
        _runtime_context(
            owner_type="user",
            owner_id="u1",
            session_id="private_u1",
        )
    ):
        private_list = run_async(
            tool._execute(
                {
                    "mode": "time",
                    "user_id": "u2",
                    "session_id": "private_u2",
                    "limit": 10,
                }
            )
        )
        private_forbidden_expand = run_async(
            tool._execute({"mode": "expand", "digest_id": 102})
        )
        private_allowed_expand = run_async(
            tool._execute({"mode": "expand", "digest_id": 101})
        )

    assert "digest_id=101" in private_list.output
    assert "digest_id=102" not in private_list.output
    assert "未找到可展开" in private_forbidden_expand.output
    assert "用户一私有记忆" in private_allowed_expand.output

    with runtime_context_scope(
        _runtime_context(
            owner_type="group",
            owner_id="g1",
            session_id="group_g1",
        )
    ):
        group_list = run_async(
            tool._execute(
                {
                    "mode": "time",
                    "user_id": "first-sender-b",
                    "session_id": "group_g2",
                    "limit": 10,
                }
            )
        )
        group_forbidden_expand = run_async(
            tool._execute({"mode": "expand", "digest_id": 202})
        )

    assert "digest_id=201" in group_list.output
    assert "digest_id=202" not in group_list.output
    assert "未找到可展开" in group_forbidden_expand.output


@pytest.mark.asyncio
async def test_memory_provider_uses_trusted_scope_and_returns_access_explanation() -> None:
    from core.memory_provider import MemoryProviderInitContext, MemoryToolCall
    from nanobot_kt.memory_runtime import build_memory_provider_runtime

    seen: dict[str, dict[str, object]] = {}

    async def memory_handler(arguments):
        seen["memory"] = dict(arguments)
        return {"status": "success", "output": "memory"}

    async def sticker_handler(arguments):
        seen["sticker"] = dict(arguments)
        return {"status": "success", "output": "sticker"}

    runtime = build_memory_provider_runtime(
        handlers={
            "memory_query": memory_handler,
            "sticker_search": sticker_handler,
        }
    )
    await runtime.initialize(MemoryProviderInitContext(runtime_id="native:test"))
    try:
        memory_result = await runtime.handle_tool_call(
            MemoryToolCall(
                request_id="req-1",
                session_id="group_g1",
                principal_id="qq:group:g1",
                call_id="call-1",
                name="memory_query",
                arguments={
                    "mode": "time",
                    "user_id": "victim",
                    "session_id": "group-victim",
                },
                metadata={"agent_id": "agent-a", "actor_id": "member-a"},
            )
        )
        sticker_result = await runtime.handle_tool_call(
            MemoryToolCall(
                request_id="req-1",
                session_id="group_g1",
                principal_id="qq:group:g1",
                call_id="call-2",
                name="sticker_search",
                arguments={"query": "震惊", "group_id": "victim"},
                metadata={"agent_id": "agent-a", "actor_id": "member-a"},
            )
        )
    finally:
        await runtime.shutdown()

    assert seen == {
        "memory": {"mode": "time"},
        "sticker": {"query": "震惊", "group_id": "g1"},
    }
    assert memory_result["metadata"]["memory_access"] == {
        "policy_version": "memory-governance-v1",
        "subject": "member-a",
        "principal": "qq:group:g1",
        "agent_id": "agent-a",
        "authorization": "runtime_governance",
        "provider_id": "memory",
        "tool_name": "memory_query",
        "resource_scope": "group:g1",
        "session_id": "group_g1",
        "project_ids": ["nanobot"],
    }
    assert sticker_result["metadata"]["memory_access"]["resource_scope"] == "group:g1"


def test_knowledge_rag_filters_agent_and_project_scopes_before_recall(
    db_session,
) -> None:
    from core.knowledge_library import create_manual_document
    from core.knowledge_rag import KnowledgeRagService
    from core.semantic.adapters import chunk_from_knowledge_chunk
    from core.semantic.indexer import upsert_semantic_chunks

    documents = {
        "default": create_manual_document(
            db_session,
            filename="default.md",
            content="# 默认项目\n中文作用域隔离探针 默认项目",
        ),
        "agent_a": create_manual_document(
            db_session,
            filename="agent-a.md",
            content="# Agent A\n中文作用域隔离探针 Agent A",
            meta={
                "memory_scope": {"scope_type": "agent", "owner_id": "agent-a"}
            },
        ),
        "agent_b": create_manual_document(
            db_session,
            filename="agent-b.md",
            content="# Agent B\n中文作用域隔离探针 Agent B",
            meta={
                "memory_scope": {"scope_type": "agent", "owner_id": "agent-b"}
            },
        ),
        "project_a": create_manual_document(
            db_session,
            filename="project-a.md",
            content="# Project A\n中文作用域隔离探针 Project A",
            meta={
                "memory_scope": {
                    "scope_type": "project",
                    "owner_id": "project-a",
                }
            },
        ),
        "project_b": create_manual_document(
            db_session,
            filename="project-b.md",
            content="# Project B\n中文作用域隔离探针 Project B",
            meta={
                "memory_scope": {
                    "scope_type": "project",
                    "owner_id": "project-b",
                }
            },
        ),
    }
    semantic_chunks = []
    for document in documents.values():
        rows = (
            db_session.query(KnowledgeChunk)
            .filter(KnowledgeChunk.document_id == int(document.id))
            .all()
        )
        semantic_chunks.extend(
            chunk_from_knowledge_chunk(row, document=document) for row in rows
        )
    upsert_semantic_chunks(
        db_session,
        semantic_chunks,
        index_version="memory-governance:test:v1",
    )
    access = build_memory_access_context(
        principal_id="qq:user:u1",
        session_id="private_u1",
        agent_id="agent-a",
        project_ids=("project-a",),
    )
    service = KnowledgeRagService(db_session)

    result = service.query(
        "中文作用域隔离探针",
        limit=10,
        access_context=access,
        include_debug=True,
    )

    allowed_ids = {
        int(documents["default"].id),
        int(documents["agent_a"].id),
        int(documents["project_a"].id),
    }
    assert {int(item["document_id"]) for item in result["items"]} == allowed_ids
    assert not {
        int(documents["agent_b"].id),
        int(documents["project_b"].id),
    } & {int(item["document_id"]) for item in result["items"]}

    forbidden_chunk = (
        db_session.query(KnowledgeChunk)
        .filter(KnowledgeChunk.document_id == int(documents["agent_b"].id))
        .first()
    )
    with pytest.raises(ValueError, match="not found"):
        service.expand(
            document_id=int(documents["agent_b"].id),
            chunk_id=forbidden_chunk.chunk_id,
            access_context=access,
        )
