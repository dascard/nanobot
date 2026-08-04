"""聊天内容 helper。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from core.context_builder import sanitize_prompt_text
from core.persisted_content import sanitize_persisted_content


_ARTIFACT_MARKER_RE = re.compile(r"\[artifact:art_[A-Za-z0-9_]{4,60}\]")
_ARTIFACT_URI_RE = re.compile(r"artifact://art_[A-Za-z0-9_]{4,60}")
_ASSET_URI_RE = re.compile(r"asset://sha256/[0-9a-f]{64}")


def _safe_archive_file_ref(value: str) -> str:
    ref = str(value or "").strip()
    if _ARTIFACT_MARKER_RE.fullmatch(ref):
        return ref
    if _ARTIFACT_URI_RE.fullmatch(ref) or _ASSET_URI_RE.fullmatch(ref):
        return ref
    try:
        parsed = urlsplit(ref)
    except ValueError:
        return "[本地附件引用已移除]"
    if (
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and parsed.username is None
        and parsed.password is None
    ):
        # 查询串常含短期签名；档案只保留可解释来源，不保存凭据。
        safe = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return safe[:2048]
    return "[本地附件引用已移除]"


def normalize_files(files: list[str] | None) -> list[str]:
    return [file for file in (files or []) if isinstance(file, str) and file.strip()]


def build_guardrail_input(query: str, files: list[str] | None) -> str:
    normalized_files = normalize_files(files)
    text = str(query or "").strip()
    if normalized_files and text:
        return f"{text}\n[附带图片 {len(normalized_files)} 张]"
    if normalized_files:
        return f"[图片消息，共 {len(normalized_files)} 张]"
    return query


def build_multimodal_user_input_text(
    query: str,
    files: list[str] | None,
    *,
    max_chars: int = 0,
) -> str:
    safe_query = sanitize_persisted_content(query)
    text = sanitize_prompt_text(safe_query, max_chars) if safe_query else ""
    normalized_files = normalize_files(files)
    parts: list[str] = []
    if text.strip():
        parts.append(text)
    if normalized_files:
        parts.append(f"[用户附带了 {len(normalized_files)} 张图片，请结合图片内容理解并回答]")
    return "\n".join(parts)


def build_file_archive_summary(files: list[str] | None, *, include_refs: bool) -> str:
    normalized_files = normalize_files(files)
    if not normalized_files:
        return ""

    header = f"[图片附件 {len(normalized_files)} 张]"
    if not include_refs:
        return header

    lines = [header]
    preview_limit = 3
    for idx, file_ref in enumerate(normalized_files[:preview_limit], start=1):
        lines.append(f"[图片{idx}] {_safe_archive_file_ref(file_ref)}")
    remaining = len(normalized_files) - preview_limit
    if remaining > 0:
        lines.append(f"[其余 {remaining} 张图片地址省略]")
    return "\n".join(lines)


def build_chatlog_user_content(query: str, files: list[str] | None) -> str:
    text = sanitize_persisted_content(query).strip()
    file_summary = build_file_archive_summary(files, include_refs=True)
    if text and file_summary:
        return f"{text}\n{file_summary}"
    if file_summary:
        return file_summary
    return query


def build_conversation_user_content(query: str, files: list[str] | None) -> str:
    return build_multimodal_user_input_text(
        query,
        files,
        max_chars=2000,
    )
