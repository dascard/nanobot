import importlib
from pathlib import Path


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


def test_provider_factory_builds_local_reranker_from_model_path(monkeypatch):
    for name in (
        "RAG_RERANKER_URL",
        "RAG_RERANKER_MODEL",
        "RAG_LOCAL_RERANKER_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAG_RERANKER_ENABLED", "1")

    values = {
        "rag.reranker.model_path": "BAAI/bge-reranker-v2-m3",
        "rag.reranker.score_mode": "identity",
        "rag.reranker.max_text_chars": 256,
    }
    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda key, default=None: values.get(key, default),
    )

    import core.semantic.provider_factory as provider_factory

    provider_factory = importlib.reload(provider_factory)
    provider = provider_factory.get_reranker_provider()

    assert provider is not None
    assert provider.model_name == "BAAI/bge-reranker-v2-m3"
    assert provider.score_mode == "identity"
    assert provider.max_text_chars == 256


def test_provider_factory_default_downloads_reranker_into_models_dir(monkeypatch):
    for name in (
        "RAG_RERANKER_URL",
        "RAG_RERANKER_MODEL",
        "RAG_LOCAL_RERANKER_MODEL",
        "RAG_RERANKER_HF_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAG_RERANKER_ENABLED", "1")
    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda _key, default=None: default,
    )

    import core.semantic.provider_factory as provider_factory

    provider_factory = importlib.reload(provider_factory)
    cfg = provider_factory.get_local_reranker_model_config()
    provider = provider_factory.get_reranker_provider()

    assert cfg["model_path"] == "./models/bge-reranker-v2-m3"
    assert cfg["download_repo_id"] == "BAAI/bge-reranker-v2-m3"
    assert cfg["configured"] is True
    assert Path(provider.model_name).name == "bge-reranker-v2-m3"
    assert Path(provider.model_name).parent.name == "models"
    assert provider.download_repo_id == "BAAI/bge-reranker-v2-m3"


def test_provider_factory_missing_default_local_reranker_returns_none(monkeypatch):
    monkeypatch.delenv("RAG_RERANKER_URL", raising=False)
    monkeypatch.delenv("RAG_LOCAL_RERANKER_MODEL", raising=False)
    monkeypatch.setenv("RAG_RERANKER_ENABLED", "1")
    values = {
        "rag.reranker.model_path": "./models/not-present-reranker",
        "rag.reranker.score_mode": "sigmoid",
        "rag.reranker.max_text_chars": 1200,
    }
    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda key, default=None: values.get(key, default),
    )

    import core.semantic.provider_factory as provider_factory

    provider_factory = importlib.reload(provider_factory)
    assert provider_factory.get_reranker_provider() is None


def test_runtime_config_disables_source_by_feature_flag(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_RAG_ENABLED", "0")

    import core.semantic.provider_factory as provider_factory

    provider_factory = importlib.reload(provider_factory)
    runtime = provider_factory.get_rag_runtime_config("knowledge")

    assert runtime.enabled is False
