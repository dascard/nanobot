"""RAG 运行时配置与 provider 工厂。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
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


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCAL_RERANKER_MODEL = "./models/bge-reranker-v2-m3"
DEFAULT_RERANKER_HF_MODEL = "BAAI/bge-reranker-v2-m3"


def _setting_text(key: str, default: str) -> str:
    from core.settings_service import settings

    value = settings.get(key, default)
    return str(value if value is not None else default).strip()


def _setting_int(key: str, default: int) -> int:
    raw = _setting_text(key, str(default))
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return int(default)


def _path_like(value: str) -> bool:
    text = str(value or "").strip()
    return bool(
        text.startswith((".", "/", "~"))
        or "\\" in text
        or (len(text) >= 3 and text[1] == ":" and text[2] in {"\\", "/"})
    )


def _resolve_model_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _same_model_path(left: str, right: str) -> bool:
    if not (_path_like(left) and _path_like(right)):
        return left == right
    return _resolve_model_path(left) == _resolve_model_path(right)


def _local_model_available(model_name: str) -> bool:
    if not model_name:
        return False
    if _path_like(model_name):
        return Path(model_name).expanduser().exists()
    return True


def get_local_reranker_model_config() -> dict[str, Any]:
    model_name = os.environ.get("RAG_LOCAL_RERANKER_MODEL", "").strip() or _setting_text(
        "rag.reranker.model_path",
        DEFAULT_LOCAL_RERANKER_MODEL,
    )
    explicit_hf_model = os.environ.get("RAG_RERANKER_HF_MODEL", "").strip() or _setting_text(
        "rag.reranker.hf_model",
        "",
    )
    default_download = DEFAULT_RERANKER_HF_MODEL if _same_model_path(model_name, DEFAULT_LOCAL_RERANKER_MODEL) else ""
    download_repo_id = explicit_hf_model or default_download
    score_mode = os.environ.get("RAG_RERANKER_SCORE_MODE", "").strip() or _setting_text(
        "rag.reranker.score_mode",
        "sigmoid",
    ) or "sigmoid"
    max_text_chars = int(
        os.environ.get("RAG_RERANKER_MAX_TEXT_CHARS", "").strip()
        or _setting_int("rag.reranker.max_text_chars", 1200)
    )
    resolved_model_path = str(_resolve_model_path(model_name)) if _path_like(model_name) else model_name
    path_exists = Path(resolved_model_path).exists() if _path_like(model_name) else None
    configured = bool(model_name) and (
        _local_model_available(resolved_model_path if _path_like(model_name) else model_name)
        or bool(download_repo_id)
    )
    return {
        "model": download_repo_id or model_name,
        "model_path": model_name,
        "resolved_model_path": resolved_model_path,
        "download_repo_id": download_repo_id,
        "configured": configured,
        "path_like": _path_like(model_name),
        "path_exists": path_exists,
        "score_mode": score_mode,
        "max_text_chars": max_text_chars,
    }


def _get_local_reranker_provider() -> LocalCrossEncoderRerankerProvider | None:
    cfg = get_local_reranker_model_config()
    if not cfg["configured"]:
        return None
    return LocalCrossEncoderRerankerProvider(
        model_name=str(cfg["resolved_model_path"] if cfg["path_like"] else cfg["model_path"]),
        download_repo_id=str(cfg["download_repo_id"]),
        score_mode=str(cfg["score_mode"]),
        max_text_chars=int(cfg["max_text_chars"]),
    )


def describe_reranker_provider_config() -> dict[str, Any]:
    """返回 reranker 配置状态，不发起网络请求。"""
    if not _bool_env("RAG_RERANKER_ENABLED", True):
        return {
            "enabled": False,
            "configured": False,
            "source": "disabled",
            "model": "",
            "url": "",
            "provider_id": "",
            "provider_enabled": False,
        }
    url = os.environ.get("RAG_RERANKER_URL", "").strip()
    if url:
        return {
            "enabled": True,
            "configured": True,
            "source": "env_http",
            "model": os.environ.get("RAG_RERANKER_MODEL", "http-reranker").strip() or "http-reranker",
            "url": url,
            "provider_id": "",
            "provider_enabled": True,
            "score_mode": os.environ.get("RAG_RERANKER_SCORE_MODE", "sigmoid").strip() or "sigmoid",
            "payload_format": "nanobot",
        }
    local = get_local_reranker_model_config()
    if local["configured"]:
        return {
            "enabled": True,
            "configured": True,
            "source": "local_model",
            "provider_id": "local",
            "provider_enabled": True,
            "model": local["model"],
            "model_path": local["model_path"],
            "resolved_model_path": local["resolved_model_path"],
            "download_repo_id": local["download_repo_id"],
            "path_exists": local["path_exists"],
            "url": "",
            "loader": "sentence-transformers CrossEncoder",
            "load_state": "not_loaded",
            "score_mode": local["score_mode"],
            "max_text_chars": local["max_text_chars"],
        }

    local_model = os.environ.get("RAG_LOCAL_RERANKER_MODEL", "").strip()
    return {
        "enabled": True,
        "configured": False,
        "source": "missing_local_model",
        "model": local_model or local["model"],
        "model_path": local_model or local["model_path"],
        "resolved_model_path": local["resolved_model_path"],
        "download_repo_id": local["download_repo_id"],
        "path_exists": local["path_exists"],
        "url": "",
        "provider_id": "local",
        "provider_enabled": False,
        "loader": "sentence-transformers CrossEncoder",
        "load_state": "unavailable",
        "score_mode": local["score_mode"],
        "max_text_chars": local["max_text_chars"],
    }


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
    local_provider = _get_local_reranker_provider()
    if local_provider is not None:
        return local_provider
    return None


def degraded_error(source_type: str, fallback_reason: str = "") -> str:
    reason = fallback_reason or "reranker_unavailable"
    return f"{source_type} RAG degraded is not allowed: {reason}"


def is_degraded_allowed(source_type: str) -> bool:
    return get_rag_runtime_config(source_type).allow_degraded
