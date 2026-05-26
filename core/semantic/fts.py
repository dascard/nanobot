"""SQLite FTS5 支持与安全查询构造。"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text


ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{3,64}")
CJK_RE = re.compile(r"[\u3400-\u9fff]+")
FTS_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}


def _execute(db: Any, statement: str) -> Any:
    try:
        return db.execute(text(statement))
    except TypeError:
        return db.execute(statement)


def check_fts5_available(db: Any) -> bool:
    try:
        _execute(db, "CREATE VIRTUAL TABLE temp._fts_check USING fts5(x)")
        _execute(db, "DROP TABLE temp._fts_check")
        return True
    except Exception:
        return False


def fts5_status(db: Any) -> dict[str, object]:
    available = check_fts5_available(db)
    return {
        "fts_unavailable": not available,
        "degraded": not available,
        "fallback_reason": "" if available else "fts_unavailable",
    }


def _quote_fts_token(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def _cjk_ngrams(text: str) -> list[str]:
    tokens: list[str] = []
    for match in CJK_RE.finditer(text):
        chunk = match.group(0)
        if len(chunk) < 2:
            continue
        if len(chunk) == 2:
            tokens.append(chunk)
            continue
        tokens.extend(chunk[index:index + 2] for index in range(0, len(chunk) - 1))
    return tokens


def build_fts5_match_query(raw_query: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", str(raw_query or ""))
    tokens: list[str] = []

    for token in ASCII_TOKEN_RE.findall(cleaned):
        upper = token.upper()
        if upper in FTS_KEYWORDS:
            continue
        tokens.append(token.lower())

    tokens.extend(_cjk_ngrams(cleaned))

    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(token)

    if not deduped:
        return ""
    return " OR ".join(_quote_fts_token(token) for token in deduped)
