"""业务源到 SemanticChunk 的适配器。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


_CANONICAL_RECALL_CARD_TYPES = frozenset({
    "decision",
    "fact",
    "todo",
    "preference",
    "module",
    "design_rule",
})


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


def _normalized_text_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = normalize_identity_text(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def normalize_identity_text(value: Any) -> str:
    """统一 Unicode 与空白，避免展示格式变化造成身份漂移。"""

    return re.sub(
        r"\s+",
        " ",
        unicodedata.normalize("NFKC", _stringify(value)),
    ).strip()


def canonical_card_type(value: Any) -> str:
    card_type = normalize_identity_text(value).lower()
    if card_type not in _CANONICAL_RECALL_CARD_TYPES:
        raise ValueError(f"non_canonical_recall_card_type:{card_type or 'empty'}")
    return card_type


def _canonical_evidence_log_ids(values: Any) -> list[int]:
    if values in (None, ""):
        return []
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError("invalid_recall_card_evidence_log_ids")
    result: set[int] = set()
    for item in values:
        if isinstance(item, bool):
            raise ValueError("invalid_recall_card_evidence_log_id")
        try:
            item_id = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_recall_card_evidence_log_id") from exc
        if item_id > 0:
            result.add(item_id)
    return sorted(result)


def canonical_recall_card_id(
    *,
    digest_source_id: str,
    card_type: str,
    text: str,
    evidence_log_ids: Sequence[int],
) -> str:
    identity = {
        "v": 1,
        "digest_source_id": str(digest_source_id or "").strip(),
        "type": canonical_card_type(card_type),
        "text": normalize_identity_text(text),
        "evidence_log_ids": _canonical_evidence_log_ids(evidence_log_ids),
    }
    if not identity["digest_source_id"] or not identity["text"]:
        raise ValueError("recall_card_identity_incomplete")
    return "rc_" + _stable_hash(identity)[:24]


def is_recallable_memory_digest_meta(meta: dict[str, Any]) -> bool:
    """只允许通过 LLM 审计门禁的 active digest 进入召回链路。"""

    try:
        schema_version = int(meta.get("schema_version") or 0)
    except (TypeError, ValueError):
        return False
    if schema_version != 2:
        return False
    if str(meta.get("status") or "").strip() != "active":
        return False
    if str(meta.get("generator") or "").strip() != "llm":
        return False
    if str(meta.get("llm_status") or "").strip() != "success":
        return False
    quality = meta.get("quality") if isinstance(meta.get("quality"), dict) else {}
    score = _as_float(
        meta.get("quality_score"),
        default=_as_float(quality.get("score")),
    )
    issues = meta.get("quality_issues", quality.get("issues", []))
    if not isinstance(issues, list):
        return False
    return score >= 0.7 and not issues


def is_recallable_knowledge_document(document: Any) -> bool:
    return str(getattr(document, "status", "") or "") == "active"


def is_recallable_knowledge_chunk(chunk: Any) -> bool:
    return (
        str(getattr(chunk, "status", "") or "") == "active"
        and bool(str(getattr(chunk, "text", "") or ""))
    )


def _as_digest_rows(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [row for row in value if row is not None]
    return [value]


def memory_digest_source_id(row: Any) -> str:
    meta = _safe_json(getattr(row, "meta_json", ""), {})
    return str(
        (meta.get("source_id") if isinstance(meta, dict) else "")
        or getattr(row, "id", "")
        or ""
    )


def memory_digest_source_revision(rows: Any) -> str:
    source_rows = _as_digest_rows(rows)
    payload = [
        {
            "id": int(getattr(row, "id", 0) or 0),
            "source_id": memory_digest_source_id(row),
            "level": int(getattr(row, "level", 0) or 0),
            "parent_id": int(getattr(row, "parent_id", 0) or 0),
            "content": str(getattr(row, "content", "") or ""),
            "meta": _safe_json(getattr(row, "meta_json", ""), {}),
        }
        for row in sorted(
            source_rows,
            key=lambda item: (
                int(getattr(item, "level", 0) or 0),
                int(getattr(item, "id", 0) or 0),
            ),
        )
    ]
    return _stable_hash({"v": 1, "rows": payload})


def chunks_from_memory_digest(rows: Any) -> list[SemanticChunk]:
    source_rows = _as_digest_rows(rows)
    if not source_rows:
        return []
    revisions: dict[str, str] = {}
    grouped_rows: dict[str, list[Any]] = {}
    for row in source_rows:
        grouped_rows.setdefault(memory_digest_source_id(row), []).append(row)
    for source_id, group in grouped_rows.items():
        revisions[source_id] = memory_digest_source_revision(group)

    chunks_by_identity: dict[tuple[str, str], SemanticChunk] = {}
    for row in sorted(
        source_rows,
        key=lambda item: (
            int(getattr(item, "level", 0) or 0),
            int(getattr(item, "id", 0) or 0),
        ),
    ):
        meta = _safe_json(getattr(row, "meta_json", ""), {})
        if not isinstance(meta, dict) or not is_recallable_memory_digest_meta(meta):
            continue
        level = int(getattr(row, "level", 0) or 0)
        source_id = memory_digest_source_id(row)
        quality = meta.get("quality") if isinstance(meta.get("quality"), dict) else {}
        quality_score = _as_float(
            meta.get("quality_score"),
            default=_as_float(quality.get("score")),
        )
        base_meta = {
            "user_id": getattr(row, "user_id", "") or "",
            "session_id": getattr(row, "session_id", "") or "",
            "digest_level": level,
            "digest_date": getattr(row, "digest_date", "") or "",
            "digest_row_id": int(getattr(row, "id", 0) or 0),
            "digest_source_id": source_id,
            "source_revision": revisions[source_id],
            "source_type": str(meta.get("source_type") or ""),
            "source_range": str(meta.get("source_range") or ""),
            "summary_type": str(meta.get("summary_type") or ""),
            "schema_version": int(meta.get("schema_version") or 0),
            "status": str(meta.get("status") or ""),
            "generator": str(meta.get("generator") or ""),
            "llm_status": str(meta.get("llm_status") or ""),
            "quality_score": quality_score,
            "quality_issues": list(quality.get("issues") or []),
            "prompt_template": str(meta.get("prompt_template") or ""),
            "prompt_version": (
                meta.get("prompt_version")
                if isinstance(meta.get("prompt_version"), dict)
                else {}
            ),
            "fallback_reason": meta.get("fallback_reason"),
            "message_count": int(meta.get("message_count") or 0),
            "recall_card_count": int(meta.get("recall_card_count") or 0),
        }

        if level == 2:
            cards = meta.get("recall_cards")
            if not isinstance(cards, list):
                continue
            for card in cards:
                if not isinstance(card, dict):
                    continue
                title = _stringify(card.get("title"))
                text = _stringify(card.get("text")) or _stringify(
                    card.get("summary") or card.get("content")
                )
                if not text:
                    continue
                card_type = canonical_card_type(card.get("type") or "fact")
                evidence_log_ids = _canonical_evidence_log_ids(
                    card.get("evidence_log_ids") or []
                )
                card_id = canonical_recall_card_id(
                    digest_source_id=source_id,
                    card_type=card_type,
                    text=text,
                    evidence_log_ids=evidence_log_ids,
                )
                source_sub_id = f"card:{card_id}"
                keywords = _normalized_text_list(card.get("keywords"))
                importance = _as_float(card.get("importance"))
                lexical = _join_parts(title, text, keywords)
                chunks_by_identity.setdefault((source_id, source_sub_id), SemanticChunk(
                    source_type="memory_digest",
                    source_id=source_id,
                    source_sub_id=source_sub_id,
                    title=title or f"记忆卡片：{card_type}",
                    text=text,
                    lexical_text=lexical,
                    embedding_text=lexical,
                    metadata={
                        **base_meta,
                        "card_id": card_id,
                        "recall_card_type": card_type,
                        "keywords": keywords,
                        "importance": importance,
                        "evidence_log_ids": evidence_log_ids,
                    },
                    visibility="recall",
                    quality_score=quality_score,
                    source_prior=0.65,
                ))
            continue

        source_sub_id = f"digest:level{level}"
        text = _stringify(getattr(row, "content", "") or "")
        chunks_by_identity[(source_id, source_sub_id)] = SemanticChunk(
            source_type="memory_digest",
            source_id=source_id,
            source_sub_id=source_sub_id,
            title=f"记忆摘要 L{level}",
            text=text,
            lexical_text=text,
            embedding_text=text,
            metadata=base_meta,
            visibility="expand_only",
            quality_score=quality_score,
            source_prior=0.3,
        )
    return list(chunks_by_identity.values())


def chunks_from_session_summary(row: Any) -> list[SemanticChunk]:
    if str(getattr(row, "status", "") or "active") in {"archived", "failed", "audit_rejected"}:
        return []
    if str(getattr(row, "summary_kind", "") or "").strip() == "deterministic_fallback":
        return []

    data = _safe_json(getattr(row, "summary_json", ""), {})
    document_id = int(getattr(row, "id", 0) or 0)
    source_id = str(
        getattr(row, "session_id", "")
        or document_id
        or ""
    )
    source_revision = session_summary_source_revision(row)
    participants = _normalized_text_list(data.get("participants"))
    keywords = _normalized_text_list(data.get("keywords"))
    raw_quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
    quality_score = _as_float(
        raw_quality.get("score"),
        default=float(getattr(row, "quality_score", 0.0) or 0.0),
    )
    quality = {
        **raw_quality,
        "score": quality_score,
        "issues": _normalized_text_list(raw_quality.get("issues")),
    }
    base_meta = {
        "session_id": getattr(row, "session_id", "") or "",
        "user_id": getattr(row, "user_id", "") or "",
        "summary_kind": getattr(row, "summary_kind", "") or "deterministic_fallback",
        "document_id": document_id,
        "covered_from_turn_id": int(getattr(row, "covered_from_turn_id", 0) or 0),
        "covered_until_turn_id": int(getattr(row, "covered_until_turn_id", 0) or 0),
        "source_revision": source_revision,
        "stable_hash": str(getattr(row, "stable_hash", "") or ""),
        "participants": participants,
        "keywords": keywords,
        "quality": quality,
    }
    source_prior = 0.7 if base_meta["summary_kind"] == "llm_episode" else 0.35
    chunks: list[SemanticChunk] = []

    summary_text = _stringify(data.get("summary")) or _stringify(getattr(row, "summary_text", "") or "")
    if summary_text:
        enriched_summary = _join_parts(summary_text, participants, keywords)
        chunks.append(SemanticChunk(
            source_type="session_summary",
            source_id=source_id,
            source_sub_id="section:summary",
            title="滚动摘要",
            text=summary_text,
            lexical_text=enriched_summary,
            embedding_text=enriched_summary,
            metadata={**base_meta, "section": "summary"},
            source_prior=source_prior,
            quality_score=quality_score,
        ))

    section_map = (
        ("open_threads", None, "未完成事项"),
        ("decisions", None, "决定"),
        ("important_user_requests", "requests", "用户请求"),
        ("artifacts", None, "产物"),
        ("resolved_items", "resolved", "已解决事项"),
    )
    seen_sections: set[tuple[str, str]] = set()
    for canonical_key, legacy_key, default_title in section_map:
        values = data.get(canonical_key)
        if not isinstance(values, list) and legacy_key:
            values = data.get(legacy_key)
        if not isinstance(values, list):
            continue
        for item in values:
            text = _stringify(item)
            if not text:
                continue
            normalized_text = normalize_identity_text(text)
            identity = (canonical_key, normalized_text)
            if identity in seen_sections:
                continue
            seen_sections.add(identity)
            title = _stringify(item.get("title")) if isinstance(item, dict) else default_title
            lexical = _join_parts(title, text, participants, keywords)
            sub_hash = _stable_hash({
                "v": 2,
                "section": canonical_key,
                "text": normalized_text,
            })[:16]
            chunks.append(SemanticChunk(
                source_type="session_summary",
                source_id=source_id,
                source_sub_id=f"section:{canonical_key}:{sub_hash}",
                title=title or default_title,
                text=text,
                lexical_text=lexical,
                embedding_text=lexical,
                metadata={**base_meta, "section": canonical_key},
                source_prior=source_prior,
                quality_score=quality_score,
            ))
    return chunks


def session_summary_kind_rank(value: Any) -> int:
    return {
        "llm_episode": 2,
        "llm_summary": 2,
        "deterministic_fallback": 1,
    }.get(str(value or "").strip(), 0)


def _session_summary_stable_hash(row: Any) -> str:
    saved = str(getattr(row, "stable_hash", "") or "").strip()
    if saved:
        return saved
    parsed = _safe_json(getattr(row, "summary_json", ""), {})
    return _stable_hash({
        "summary": parsed if isinstance(parsed, dict) else {},
        "summary_text": normalize_identity_text(
            getattr(row, "summary_text", "") or ""
        ),
    })


def session_summary_source_revision(row: Any) -> str:
    return _stable_hash({
        "v": 2,
        "document_id": int(getattr(row, "id", 0) or 0),
        "covered_from_turn_id": int(getattr(row, "covered_from_turn_id", 0) or 0),
        "covered_until_turn_id": int(getattr(row, "covered_until_turn_id", 0) or 0),
        "summary_kind_rank": session_summary_kind_rank(
            getattr(row, "summary_kind", "")
        ),
        "stable_hash": _session_summary_stable_hash(row),
    })


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
    from core.memory_governance import knowledge_scope_from_meta

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
    document_meta = (
        _safe_json(getattr(document, "meta_json", ""), {})
        if document is not None
        else {}
    )
    memory_scope = knowledge_scope_from_meta(document_meta)
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
            "memory_scope": memory_scope.metadata(),
        },
        trust_level=trust_level,
        source_prior=0.55,
    )
