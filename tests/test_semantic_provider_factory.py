import importlib


def test_rag_feature_flags_exist_in_config():
    import config

    for name in (
        "SEMANTIC_INDEX_ENABLED",
        "RAG_RERANKER_ENABLED",
        "RAG_ALLOW_DEGRADED",
        "MEMORY_RAG_ENABLED",
        "GROUP_MEMORY_RAG_ENABLED",
        "STICKER_RAG_ENABLED",
        "KNOWLEDGE_RAG_ENABLED",
        "GROUP_ANALYSIS_RAG_ENABLED",
    ):
        assert hasattr(config, name)


def test_provider_factory_builds_http_reranker_from_config(monkeypatch):
    monkeypatch.setenv("RAG_RERANKER_ENABLED", "1")
    monkeypatch.setenv("RAG_RERANKER_URL", "http://reranker.local/rerank")
    monkeypatch.setenv("RAG_RERANKER_MODEL", "fake-reranker")

    import core.semantic.provider_factory as provider_factory

    provider_factory = importlib.reload(provider_factory)
    provider = provider_factory.get_reranker_provider()

    assert provider is not None
    assert provider.url == "http://reranker.local/rerank"
    assert provider.model_name == "fake-reranker"


def test_runtime_config_disables_source_by_feature_flag(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_RAG_ENABLED", "0")

    import core.semantic.provider_factory as provider_factory

    provider_factory = importlib.reload(provider_factory)
    runtime = provider_factory.get_rag_runtime_config("knowledge")

    assert runtime.enabled is False
