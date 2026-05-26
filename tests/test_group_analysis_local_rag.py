from datetime import datetime, timedelta


class CountingEmbeddingProvider:
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        return [[1.0, 0.0] for _ in texts]


class DirectionalEmbeddingProvider:
    def embed(self, texts):
        vectors = []
        for text in texts:
            value = str(text)
            if "目标主题" in value or "专题查询" in value:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


class IdentityRerankerProvider:
    def __init__(self, scores):
        self.scores = scores

    def rerank(self, query, candidates, *, top_k=None):
        from core.semantic.reranker import RerankResult

        limited = candidates[:top_k] if top_k else candidates
        return sorted(
            [
                RerankResult(
                    candidate_id=candidate.candidate_id,
                    raw_score=self.scores.get(candidate.candidate_id, 0.0),
                    score=self.scores.get(candidate.candidate_id, 0.0),
                    model="identity-reranker",
                    score_mode="identity",
                )
                for candidate in limited
            ],
            key=lambda item: item.score or 0.0,
            reverse=True,
        )


def _messages(count=20, *, keyword_every=1):
    base = datetime(2026, 5, 26, 12, 0, 0)
    result = []
    for idx in range(count):
        result.append({
            "log_id": idx + 1,
            "time": (base + timedelta(minutes=idx)).strftime("%H:%M"),
            "user_id": f"u{idx % 3}",
            "content": (
                f"第{idx}条 RAG 检索 reranker 讨论"
                if idx % keyword_every == 0
                else f"第{idx}条普通闲聊"
            ),
            "hour": 12,
            "is_reply": False,
        })
    return result


def test_group_analysis_reranks_bundles_before_neighbor_expansion():
    from creatures.nanobot.prompts.skills.group_analysis.local_rag import select_group_analysis_context

    messages = _messages(12, keyword_every=1)
    reranker = IdentityRerankerProvider({
        "bundle:0": 0.1,
        "bundle:1": 0.2,
        "bundle:2": 0.95,
    })

    result = select_group_analysis_context(
        messages,
        query="RAG reranker",
        bundle_size=4,
        lexical_top_k=10,
        reranker_top_k=1,
        neighbor_radius=1,
        reranker_provider=reranker,
    )

    assert result["prompt_logs"]["hit_bundles"][0]["bundle_id"] == "bundle:2"
    assert {item["bundle_id"] for item in result["prompt_logs"]["selected_bundles"]} == {
        "bundle:1",
        "bundle:2",
    }


def test_group_analysis_builds_temporary_bundles_not_index_items(db_session):
    from core.database import SemanticIndexItem
    from creatures.nanobot.prompts.skills.group_analysis.local_rag import select_group_analysis_context

    select_group_analysis_context(_messages(30), query="RAG", bundle_size=5)

    assert db_session.query(SemanticIndexItem).count() == 0


def test_group_analysis_neighbor_expansion_preserves_context():
    from creatures.nanobot.prompts.skills.group_analysis.local_rag import select_group_analysis_context

    messages = _messages(15, keyword_every=100)
    messages[7]["content"] = "关键讨论：RAG 局部检索"

    result = select_group_analysis_context(
        messages,
        query="RAG 局部检索",
        bundle_size=3,
        lexical_top_k=10,
        reranker_top_k=1,
        neighbor_radius=1,
    )
    selected_ids = [item["log_id"] for item in result["messages"]]

    assert 7 in selected_ids
    assert 8 in selected_ids
    assert 9 in selected_ids
    assert selected_ids == sorted(selected_ids)


def test_group_analysis_does_not_change_group_stats():
    from creatures.nanobot.prompts.skills.group_analysis.preprocess import build_analysis_payload

    messages = _messages(40, keyword_every=10)
    logs = [
        type("Log", (), {
            "id": item["log_id"],
            "content": item["content"],
            "sender_name": item["user_id"],
            "created_at": datetime(2026, 5, 26, 12, 0, 0) + timedelta(minutes=idx),
        })()
        for idx, item in enumerate(messages)
    ]

    payload = build_analysis_payload(
        logs,
        prompt_budget=600,
        local_rag_query="RAG",
        enable_local_rag=True,
    )

    assert payload["group_stats"]["message_count"] == 40
    assert payload["local_rag"]["stats_logs"]["total_messages"] == 40
    assert len(payload["messages"]) <= 40


def test_group_analysis_limits_embedding_to_lexical_top_candidates():
    from creatures.nanobot.prompts.skills.group_analysis.local_rag import select_group_analysis_context

    embedding = CountingEmbeddingProvider()
    messages = _messages(350, keyword_every=1)

    result = select_group_analysis_context(
        messages,
        query="RAG",
        bundle_size=1,
        lexical_top_k=300,
        embedding_provider=embedding,
    )

    assert embedding.calls == 301
    assert result["stats_logs"]["temporary_embedding_scored"] == 300


def test_group_analysis_temporary_embedding_uses_query_cosine_score():
    from creatures.nanobot.prompts.skills.group_analysis.local_rag import select_group_analysis_context

    messages = _messages(6, keyword_every=1)
    messages[0]["content"] = "目标主题 RAG 方案"
    messages[3]["content"] = "普通 RAG 闲聊"

    result = select_group_analysis_context(
        messages,
        query="专题查询 RAG",
        bundle_size=3,
        lexical_top_k=10,
        reranker_top_k=2,
        neighbor_radius=0,
        embedding_provider=DirectionalEmbeddingProvider(),
    )
    hits = {item["bundle_id"]: item for item in result["prompt_logs"]["hit_bundles"]}

    assert hits["bundle:0"]["semantic"] > hits["bundle:1"]["semantic"]


def test_group_analysis_non_thematic_daily_does_not_enable_rag():
    from creatures.nanobot.prompts.skills.group_analysis.local_rag import should_use_group_analysis_local_rag

    assert should_use_group_analysis_local_rag("") is False
    assert should_use_group_analysis_local_rag("生成群日报") is False
    assert should_use_group_analysis_local_rag("看看今天群里聊了什么") is False


def test_group_analysis_thematic_instruction_enables_rag():
    from creatures.nanobot.prompts.skills.group_analysis.local_rag import should_use_group_analysis_local_rag

    assert should_use_group_analysis_local_rag("重点分析 RAG 检索和 reranker 的讨论") is True
    assert should_use_group_analysis_local_rag("只看关于端口冲突的部分") is True


def test_group_analysis_budget_preserves_high_score_groups():
    from creatures.nanobot.prompts.skills.group_analysis.local_rag import select_group_analysis_context

    messages = _messages(18, keyword_every=100)
    for idx in range(0, 3):
        messages[idx]["content"] = f"低优先普通 RAG {idx}"
    for idx in range(12, 15):
        messages[idx]["content"] = f"高优先关键 RAG {idx}"

    reranker = IdentityRerankerProvider({
        "bundle:0": 0.2,
        "bundle:4": 0.95,
    })
    result = select_group_analysis_context(
        messages,
        query="RAG",
        bundle_size=3,
        lexical_top_k=10,
        reranker_top_k=2,
        neighbor_radius=0,
        budget_chars=80,
        reranker_provider=reranker,
    )
    selected_text = "\n".join(item["content"] for item in result["messages"])

    assert "高优先关键" in selected_text
    assert "低优先普通" not in selected_text
    assert [item["log_id"] for item in result["messages"]] == sorted(item["log_id"] for item in result["messages"])
