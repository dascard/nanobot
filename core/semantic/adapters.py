"""业务源到 SemanticChunk 的适配器。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SemanticChunk:
    source_type: str
    source_id: str
    source_sub_id: str
    title: str
    text: str
    lexical_text: str
    embedding_text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    visibility: str = "recall"
    quality_score: float = 0.0
    trust_level: str = "medium"
    source_prior: float = 0.5


def _safe_json(raw: str | None, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "content", "summary", "title"):
            text = _stringify(value.get(key))
            if text:
                return text
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _join_parts(*parts: Any) -> str:
    values: list[str] = []
    for part in parts:
        if isinstance(part, (list, tuple, set)):
            values.extend(_stringify(item) for item in part)
        else:
            values.append(_stringify(part))
    return "\n".join(value for value in values if value)


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def chunks_from_memory_digest(row: Any) -> list[SemanticChunk]:
    meta = _safe_json(getattr(row, "meta_json", ""), {})
    level = int(getattr(row, "level", 0) or 0)
    source_id = str(getattr(row, "id", "") or "")
    quality = meta.get("quality") if isinstance(meta.get("quality"), dict) else {}
    quality_score = _as_float(meta.get("quality_score"), default=_as_float(quality.get("score")))
    base_meta = {
        "user_id": getattr(row, "user_id", "") or "",
        "session_id": getattr(row, "session_id", "") or "",
        "digest_level": level,
        "digest_date": getattr(row, "digest_date", "") or "",
        "digest_source_id": str(meta.get("source_id") or ""),
        "source_type": str(meta.get("source_type") or ""),
        "source_range": str(meta.get("source_range") or ""),
        "summary_type": str(meta.get("summary_type") or ""),
        "generator": str(meta.get("generator") or ""),
        "prompt_template": str(meta.get("prompt_template") or ""),
        "prompt_version": meta.get("prompt_version") if isinstance(meta.get("prompt_version"), dict) else {},
        "fallback_reason": meta.get("fallback_reason"),
        "message_count": int(meta.get("message_count") or 0),
        "recall_card_count": int(meta.get("recall_card_count") or 0),
    }

    if level == 2:
        cards = meta.get("recall_cards")
        if isinstance(cards, list) and cards:
            chunks: list[SemanticChunk] = []
            for index, card in enumerate(cards):
                title = _stringify(card.get("title") if isinstance(card, dict) else "")
                text = _stringify(card.get("text") if isinstance(card, dict) else card)
                if not text and isinstance(card, dict):
                    text = _stringify(card.get("summary") or card.get("content"))
                keywords = card.get("keywords") if isinstance(card, dict) else []
                lexical = _join_parts(title, text, keywords)
                chunks.append(SemanticChunk(
                    source_type="memory_digest",
                    source_id=source_id,
                    source_sub_id=f"card:{index}",
                    title=title or f"记忆卡片 {index + 1}",
                    text=text,
                    lexical_text=lexical,
                    embedding_text=lexical,
                    metadata={**base_meta, "recall_card_index": index},
                    visibility="recall",
                    quality_score=quality_score,
                    source_prior=0.65,
                ))
            return chunks
        source_sub_id = "digest:level2"
        visibility = "recall"
    else:
        source_sub_id = f"digest:level{level}"
        visibility = "expand_only"

    text = _stringify(getattr(row, "content", "") or "")
    return [SemanticChunk(
        source_type="memory_digest",
        source_id=source_id,
        source_sub_id=source_sub_id,
        title=f"记忆摘要 L{level}",
        text=text,
        lexical_text=text,
        embedding_text=text,
        metadata=base_meta,
        visibility=visibility,
        quality_score=quality_score,
        source_prior=0.6 if visibility == "recall" else 0.3,
    )]


def chunks_from_session_summary(row: Any) -> list[SemanticChunk]:
    if str(getattr(row, "status", "") or "active") in {"archived", "failed", "audit_rejected"}:
        return []

    data = _safe_json(getattr(row, "summary_json", ""), {})
    source_id = str(getattr(row, "id", "") or "")
    base_meta = {
        "session_id": getattr(row, "session_id", "") or "",
        "user_id": getattr(row, "user_id", "") or "",
        "summary_kind": getattr(row, "summary_kind", "") or "deterministic_fallback",
    }
    source_prior = 0.7 if base_meta["summary_kind"] == "llm_episode" else 0.35
    chunks: list[SemanticChunk] = []

    summary_text = _stringify(data.get("summary")) or _stringify(getattr(row, "summary_text", "") or "")
    if summary_text:
        chunks.append(SemanticChunk(
            source_type="session_summary",
            source_id=source_id,
            source_sub_id="section:summary",
            title="滚动摘要",
            text=summary_text,
            lexical_text=summary_text,
            embedding_text=summary_text,
            metadata={**base_meta, "section": "summary"},
            source_prior=source_prior,
            quality_score=float(getattr(row, "quality_score", 0.0) or 0.0),
        ))

    section_map = {
        "open_threads": "open_thread",
        "decisions": "decision",
        "requests": "request",
        "artifacts": "artifact",
        "resolved": "resolved",
    }
    for json_key, sub_prefix in section_map.items():
        values = data.get(json_key)
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            text = _stringify(item)
            if not text:
                continue
            title = _stringify(item.get("title")) if isinstance(item, dict) else sub_prefix
            lexical = _join_parts(title, text)
            chunks.append(SemanticChunk(
                source_type="session_summary",
                source_id=source_id,
                source_sub_id=f"{sub_prefix}:{index}",
                title=title or sub_prefix,
                text=text,
                lexical_text=lexical,
                embedding_text=lexical,
                metadata={**base_meta, "section": sub_prefix, "section_index": index},
                source_prior=source_prior,
                quality_score=float(getattr(row, "quality_score", 0.0) or 0.0),
            ))
    return chunks


def chunk_from_group_memory(row: Any) -> SemanticChunk:
    meta = _safe_json(getattr(row, "meta_json", ""), {})
    keywords = meta.get("keywords") if isinstance(meta.get("keywords"), list) else []
    evidence_summary = _stringify(meta.get("evidence_short_summary"))
    text = _stringify(getattr(row, "content", "") or "")
    lexical = _join_parts(
        getattr(row, "memory_type", "") or "",
        text,
        getattr(row, "cluster_key", "") or "",
        keywords,
        evidence_summary,
    )
    return SemanticChunk(
        source_type="group_memory",
        source_id=str(getattr(row, "id", "") or ""),
        source_sub_id="memory",
        title=f"群体记忆：{getattr(row, 'memory_type', '') or 'memory'}",
        text=text,
        lexical_text=lexical,
        embedding_text=lexical,
        metadata={
            "group_id": getattr(row, "group_id", "") or "",
            "memory_type": getattr(row, "memory_type", "") or "",
            "cluster_key": getattr(row, "cluster_key", "") or "",
            "keywords": keywords,
            "evidence_short_summary": evidence_summary,
        },
        source_prior=0.55,
        quality_score=float(getattr(row, "decay_score", 1.0) or 0.0),
    )


def chunk_from_sticker(row: Any) -> SemanticChunk | None:
    status = str(getattr(row, "status", "") or "")
    dedupe_status = str(getattr(row, "dedupe_status", "") or "")
    describe_status = str(getattr(row, "describe_status", "") or "")
    if status != "active" or dedupe_status == "duplicate" or getattr(row, "duplicate_of_id", None):
        return None
    if describe_status != "ok":
        return None

    tags = _safe_json(getattr(row, "tags_json", ""), [])
    emotions = _safe_json(getattr(row, "emotions_json", ""), [])
    meta = _safe_json(getattr(row, "meta_json", ""), {})
    qwen_summary = _stringify(meta.get("qwen_summary"))
    title = _stringify(getattr(row, "name", "") or "")
    description = _stringify(getattr(row, "description", "") or "")
    if not any([description, tags, emotions, qwen_summary]):
        return None

    lexical = _join_parts(title, description, tags, emotions, qwen_summary)
    return SemanticChunk(
        source_type="sticker",
        source_id=str(getattr(row, "id", "") or ""),
        source_sub_id="sticker",
        title=title,
        text=description,
        lexical_text=lexical,
        embedding_text=lexical,
        metadata={
            "chat_stream_id": getattr(row, "chat_stream_id", "") or "",
            "tags": tags if isinstance(tags, list) else [],
            "emotions": emotions if isinstance(emotions, list) else [],
            "qwen_summary": qwen_summary,
        },
        source_prior=0.5,
    )


def chunk_from_ai_daily_item(item: dict[str, Any]) -> SemanticChunk:
    item_id = _stringify(item.get("id") or item.get("url") or item.get("title"))
    title = _stringify(item.get("title"))
    summary = _stringify(item.get("summary") or item.get("description"))
    source_name = _stringify(item.get("source_name") or item.get("source"))
    url = _stringify(item.get("url"))
    published_at = _stringify(item.get("published_at"))
    lexical = _join_parts(title, summary, source_name, published_at)
    return SemanticChunk(
        source_type="knowledge",
        source_id=item_id,
        source_sub_id=f"ai_daily:{item_id}",
        title=title,
        text=summary,
        lexical_text=lexical,
        embedding_text=lexical,
        metadata={
            "document_kind": "ai_daily",
            "source_name": source_name,
            "published_at": published_at,
            "citation": {
                "url": url,
                "title": title,
                "source_name": source_name,
                "published_at": published_at,
                "trust_level": item.get("trust_level") or "medium",
            },
        },
        trust_level=str(item.get("trust_level") or "medium"),
        source_prior=0.45,
    )


def chunk_from_knowledge_chunk(row: Any, *, document: Any | None = None) -> SemanticChunk:
    citation = _safe_json(getattr(row, "citation_json", ""), {})
    if not citation and document is not None:
        citation = {
            "document_id": str(getattr(document, "id", "") or ""),
            "chunk_id": getattr(row, "chunk_id", "") or "",
            "title": getattr(document, "title", "") or getattr(row, "title", "") or "",
            "trust_level": getattr(row, "trust_level", "") or getattr(document, "trust_level", "") or "medium",
            "url": getattr(document, "url", "") or "",
            "published_at": getattr(document, "published_at", "") or "",
        }
    document_id = str(getattr(row, "document_id", "") or citation.get("document_id") or "")
    chunk_id = str(getattr(row, "chunk_id", "") or citation.get("chunk_id") or "")
    title = _stringify(getattr(row, "title", "") or citation.get("title") or "")
    text = _stringify(getattr(row, "text", "") or "")
    trust_level = str(getattr(row, "trust_level", "") or citation.get("trust_level") or "medium")
    document_published_at = getattr(document, "published_at", "") if document is not None else ""
    published_at = _stringify(citation.get("published_at") or document_published_at)
    lexical = _join_parts(
        title,
        text,
        citation.get("source_name"),
        citation.get("domain"),
        published_at,
    )
    return SemanticChunk(
        source_type="knowledge",
        source_id=document_id,
        source_sub_id=chunk_id,
        title=title,
        text=text,
        lexical_text=lexical,
        embedding_text=lexical,
        metadata={
            "document_id": document_id,
            "chunk_id": chunk_id,
            "published_at": published_at,
            "citation": citation,
        },
        trust_level=trust_level,
        source_prior=0.55,
    )
