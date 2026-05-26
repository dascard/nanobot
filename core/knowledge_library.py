"""Knowledge Library 文档创建与分块。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.database import KnowledgeChunk, KnowledgeDocument


SUPPORTED_MANUAL_FILE_EXTENSIONS = {".txt", ".md"}
MAX_CHUNKS_PER_DOCUMENT = 200
MAX_CHUNK_CHARS = 1200
TARGET_CHUNK_CHARS = 900
OVERLAP_CHARS = 100


def validate_manual_filename(filename: str) -> str:
    value = str(filename or "").strip()
    suffix = Path(value).suffix.lower()
    if suffix not in SUPPORTED_MANUAL_FILE_EXTENSIONS:
        raise ValueError("manual_file 仅支持 .txt / .md")
    return value


def _normalize_text(content: str) -> str:
    return re.sub(r"\r\n?", "\n", str(content or "")).strip()


def _split_long_text(text: str) -> list[str]:
    text = _normalize_text(text)
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text) and len(chunks) < MAX_CHUNKS_PER_DOCUMENT:
        end = min(len(text), start + TARGET_CHUNK_CHARS)
        if end < len(text):
            paragraph_break = text.rfind("\n\n", start, end)
            if paragraph_break > start + 300:
                end = paragraph_break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk[:MAX_CHUNK_CHARS])
        if end >= len(text):
            break
        start = max(end - OVERLAP_CHARS, start + 1)
    return chunks


def _markdown_sections(content: str) -> list[tuple[str, str]]:
    lines = _normalize_text(content).splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = []
    heading_pattern = re.compile(r"^(#{1,3})\s+(.+)$")
    for line in lines:
        match = heading_pattern.match(line.strip())
        if match and current_lines:
            sections.append((current_title, current_lines))
            current_title = match.group(2).strip()
            current_lines = [line]
            continue
        if match and not current_lines:
            current_title = match.group(2).strip()
        current_lines.append(line)
    if current_lines:
        sections.append((current_title, current_lines))
    return [(title, "\n".join(chunk_lines).strip()) for title, chunk_lines in sections if "\n".join(chunk_lines).strip()]


def chunk_manual_content(filename: str, content: str, *, title: str = "") -> list[dict[str, Any]]:
    filename = validate_manual_filename(filename)
    suffix = Path(filename).suffix.lower()
    chunks: list[dict[str, Any]] = []
    if suffix == ".md":
        for section_title, section_text in _markdown_sections(content):
            for part in _split_long_text(section_text):
                chunks.append({"title": section_title or title, "text": part})
                if len(chunks) >= MAX_CHUNKS_PER_DOCUMENT:
                    return chunks
    else:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", _normalize_text(content)) if part.strip()]
        buffer = ""
        for paragraph in paragraphs:
            candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
            if len(candidate) <= TARGET_CHUNK_CHARS:
                buffer = candidate
                continue
            for part in _split_long_text(buffer):
                chunks.append({"title": title, "text": part})
                if len(chunks) >= MAX_CHUNKS_PER_DOCUMENT:
                    return chunks
            buffer = paragraph
        for part in _split_long_text(buffer):
            chunks.append({"title": title, "text": part})
            if len(chunks) >= MAX_CHUNKS_PER_DOCUMENT:
                return chunks
    return chunks[:MAX_CHUNKS_PER_DOCUMENT]


def create_manual_document(
    db: Session,
    *,
    filename: str,
    content: str,
    title: str = "",
    trust_level: str = "medium",
    published_at: str = "",
    created_by: str = "admin",
    meta: dict[str, Any] | None = None,
) -> KnowledgeDocument:
    filename = validate_manual_filename(filename)
    clean_title = str(title or "").strip() or Path(filename).stem
    now = datetime.now()
    document = KnowledgeDocument(
        document_kind="manual_file",
        title=clean_title,
        published_at=str(published_at or ""),
        status="active",
        trust_level=str(trust_level or "medium"),
        created_by=str(created_by or ""),
        updated_by=str(created_by or ""),
        latest_seen=now,
        meta_json=json.dumps({"filename": filename, **(meta or {})}, ensure_ascii=False, sort_keys=True),
        created_at=now,
        updated_at=now,
    )
    db.add(document)
    db.flush()

    chunks = chunk_manual_content(filename, content, title=clean_title)
    for index, chunk in enumerate(chunks):
        chunk_id = f"chunk:{index}"
        citation = {
            "document_id": str(document.id),
            "chunk_id": chunk_id,
            "title": clean_title,
            "trust_level": document.trust_level,
            "published_at": document.published_at,
            "url": document.url or "",
        }
        db.add(KnowledgeChunk(
            document_id=int(document.id),
            chunk_id=chunk_id,
            order_index=index,
            title=str(chunk.get("title") or clean_title),
            text=str(chunk.get("text") or "")[:MAX_CHUNK_CHARS],
            citation_json=json.dumps(citation, ensure_ascii=False, sort_keys=True),
            status="active",
            trust_level=document.trust_level,
            meta_json=json.dumps({"filename": filename}, ensure_ascii=False, sort_keys=True),
            created_at=now,
            updated_at=now,
        ))
    db.commit()
    db.refresh(document)
    return document
