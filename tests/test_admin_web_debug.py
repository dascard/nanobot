from pathlib import Path


def _auth_header():
    return {"Authorization": "Bearer test-token"}


def test_rag_debug_returns_score_breakdown(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={"source_type": "memory", "query": "端口", "limit": 3},
    )

    assert response.status_code == 200
    score = response.json()["response"]["score_breakdown"]
    assert "latency_ms" in score
    assert "fallback_reason" in score


def test_memory_debug_page_contains_reranker_columns():
    page_source = Path("webui/src/features/rag/RagDebugPage.jsx").read_text(encoding="utf-8")

    assert "reranker_score" in page_source
    assert "final_score" in page_source
    assert "Reranker Input" in page_source
    assert "reranker_latency_ms" in page_source
    assert "reranker 耗时" in page_source
    assert "reranker 输入" in page_source
    assert "merged_candidates" in page_source


def test_knowledge_debug_page_requires_citation_columns():
    page_source = Path("webui/src/features/rag/RagDebugPage.jsx").read_text(encoding="utf-8")

    assert "citation" in page_source
    assert "trust_level" in page_source
    assert "document_id" in page_source


def test_db_page_contains_grouped_search_pagination_and_preview_ui():
    page_source = Path("webui/src/App.jsx").read_text(encoding="utf-8")

    assert "groups" in page_source
    assert "tableSearch" in page_source
    assert "page" in page_source
    assert "limit" in page_source
    assert "上一页" in page_source
    assert "下一页" in page_source
    assert "truncated" in page_source
    assert "展开预览" in page_source
    assert "展开完整内容" not in page_source


def test_group_analysis_debug_page_contains_stats_and_prompt_logs(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    messages = [
        {"log_id": 1, "time": "12:00", "user_id": "A", "content": "RAG 检索讨论", "hour": 12},
        {"log_id": 2, "time": "12:01", "user_id": "B", "content": "reranker 分数", "hour": 12},
        {"log_id": 3, "time": "12:02", "user_id": "C", "content": "普通闲聊", "hour": 12},
    ]
    response = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={
            "source_type": "group_analysis",
            "query": "RAG reranker",
            "limit": 3,
            "filters": {"messages": messages},
        },
    )

    assert response.status_code == 200
    stages = response.json()["response"]["stages"]
    assert "stats_logs" in stages
    assert "prompt_logs" in stages
    assert stages["stats_logs"]["total_messages"] == 3


def test_rag_debug_run_can_be_saved_reopened_and_exported(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    created = client.post(
        "/api/v1/admin/rag/debug/query",
        headers=_auth_header(),
        json={"source_type": "memory", "query": "端口", "limit": 3},
    )
    run_id = created.json()["run_id"]

    detail = client.get(f"/api/v1/admin/rag/debug/runs/{run_id}", headers=_auth_header())
    exported = client.get(f"/api/v1/admin/rag/debug/runs/{run_id}/export", headers=_auth_header())

    assert detail.status_code == 200
    assert detail.json()["response"]["query"] == "端口"
    assert exported.status_code == 200
    assert exported.json()["response"]["query"] == "端口"
