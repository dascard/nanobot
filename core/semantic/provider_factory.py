"""RAG 运行时配置与 provider 工厂。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from core.semantic.reranker import HttpRerankerProvider, LocalCrossEncoderRerankerProvider


@dataclass(frozen=True)
class RagRuntimeConfig:
    source_type: str = ""
    enabled: bool = True
    semantic_index_enabled: bool = True
    reranker_enabled: bool = True
    allow_degraded: bool = False


class RagDegradedBlockedError(RuntimeError):
    def __init__(self, source_type: str, fallback_reason: str = "reranker_unavailable"):
        self.source_type = source_type
        self.fallback_reason = fallback_reason or "reranker_unavailable"
        super().__init__(degraded_error(source_type, self.fallback_reason))


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def get_rag_runtime_config(source_type: str = "") -> RagRuntimeConfig:
    source = str(source_type or "").strip()
    testing = _bool_env("NANOBOT_TESTING", False)
    semantic_index_enabled = _bool_env("SEMANTIC_INDEX_ENABLED", True)
    source_enabled = {
        "memory": _bool_env("MEMORY_RAG_ENABLED", True),
        "memory_digest": _bool_env("MEMORY_RAG_ENABLED", True),
        "session_summary": _bool_env("MEMORY_RAG_ENABLED", True),
        "group_memory": _bool_env("GROUP_MEMORY_RAG_ENABLED", True),
        "sticker": _bool_env("STICKER_RAG_ENABLED", True),
        "knowledge": _bool_env("KNOWLEDGE_RAG_ENABLED", True),
        "group_analysis": _bool_env("GROUP_ANALYSIS_RAG_ENABLED", True),
    }
    enabled = semantic_index_enabled and source_enabled.get(source, True)
    return RagRuntimeConfig(
        source_type=source,
        enabled=enabled,
        semantic_index_enabled=semantic_index_enabled,
        reranker_enabled=_bool_env("RAG_RERANKER_ENABLED", True),
        allow_degraded=_bool_env("RAG_ALLOW_DEGRADED", testing),
    )


@lru_cache(maxsize=1)
def get_embedding_provider() -> Any | None:
    provider = os.environ.get("RAG_EMBEDDING_PROVIDER", "").strip().lower()
    if provider in {"", "none", "disabled", "off"}:
        return None
    raise RuntimeError(f"Unsupported RAG_EMBEDDING_PROVIDER: {provider}")


@lru_cache(maxsize=1)
def get_reranker_provider() -> Any | None:
    if not _bool_env("RAG_RERANKER_ENABLED", True):
        return None
    url = os.environ.get("RAG_RERANKER_URL", "").strip()
    model_name = os.environ.get("RAG_RERANKER_MODEL", "http-reranker").strip() or "http-reranker"
    score_mode = os.environ.get("RAG_RERANKER_SCORE_MODE", "sigmoid").strip() or "sigmoid"
    max_text_chars = int(os.environ.get("RAG_RERANKER_MAX_TEXT_CHARS", "1200") or "1200")
    if url:
        return HttpRerankerProvider(
            url,
            timeout_ms=int(os.environ.get("RAG_RERANKER_TIMEOUT_MS", "3000") or "3000"),
            model_name=model_name,
            score_mode=score_mode,
            max_text_chars=max_text_chars,
        )
    local_model = os.environ.get("RAG_LOCAL_RERANKER_MODEL", "").strip()
    if local_model:
        return LocalCrossEncoderRerankerProvider(
            model_name=local_model,
            score_mode=score_mode,
            max_text_chars=max_text_chars,
        )
    return None


def degraded_error(source_type: str, fallback_reason: str = "") -> str:
    reason = fallback_reason or "reranker_unavailable"
    return f"{source_type} RAG degraded is not allowed: {reason}"


def is_degraded_allowed(source_type: str) -> bool:
    return get_rag_runtime_config(source_type).allow_degraded
