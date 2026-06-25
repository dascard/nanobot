"""普通 API 记忆摘要路由。"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from app.memory_digest.retrieval_service import MemoryDigestRetrievalService, validate_digest_date
from core.daily_digest import generate_daily_digest_for_date
from core.database import ChatLog, MemoryDigest, get_db
from core.time_utils import db_now_naive


router = APIRouter(tags=["memory"])


class MemoryDigestRunRequest(BaseModel):
    target_date: str | None = None  # YYYY-MM-DD
    user_id: str | None = None
    force: bool = False


def _validate_memory_digest_date_filters(
    *,
    digest_date: str = "",
    date_start: str = "",
    date_end: str = "",
) -> tuple[str, str, str]:
    try:
        return (
            validate_digest_date(digest_date, "digest_date"),
            validate_digest_date(date_start, "date_start"),
            validate_digest_date(date_end, "date_end"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _short_text(text: str, limit: int = 400) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "...[截断]"


def _calc_recall_confidence(keyword: str, content: str, meta: dict) -> float:
    if not keyword.strip():
        return 0.5
    content_l = (content or "").lower()
    key_l = keyword.lower()
    hits = content_l.count(key_l)

    score = min(0.95, 0.3 + min(0.45, hits * 0.08))
    tags = (meta.get("tags") or {}) if isinstance(meta, dict) else {}
    value_signal = float((tags.get("value_signal_score") or 0))
    if value_signal > 0:
        score = min(0.98, score + min(0.2, value_signal * 0.03))
    return round(max(0.05, score), 3)


def _build_expand_chain(db: Session, base: MemoryDigest, reveal_to_level: int) -> list[MemoryDigest]:
    reveal_to_level = max(0, min(2, reveal_to_level))
    chain = [base]
    current = base

    while current.parent_id is not None and current.level > reveal_to_level:
        parent = db.query(MemoryDigest).filter(MemoryDigest.id == current.parent_id).first()
        if not parent:
            break
        chain.append(parent)
        current = parent

    return chain


@router.get("/memory/digests")
def get_memory_digests(
    user_id: str = "",
    session_id: str = "",
    digest_date: str = "",
    date_start: str = "",
    date_end: str = "",
    level: int = -1,
    limit: int = 50,
    include_content: bool = False,
    include_legacy: bool = True,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """按条件查询每日记忆摘要（支持渐进式披露层级）。"""
    digest_date, date_start, date_end = _validate_memory_digest_date_filters(
        digest_date=digest_date,
        date_start=date_start,
        date_end=date_end,
    )
    items = MemoryDigestRetrievalService(db).list_digests(
        user_id=user_id,
        session_id=session_id,
        digest_date=digest_date,
        date_start=date_start,
        date_end=date_end,
        level=level if level >= 0 else None,
        limit=limit,
        include_content=include_content,
        include_legacy=include_legacy,
    )

    return {
        "status": "ok",
        "count": len(items),
        "digests": items,
    }


@router.post("/memory/digests/run")
def run_memory_digests(
    req: MemoryDigestRunRequest,
    _auth=Depends(verify_token),
):
    """手动触发指定日期的每日记忆摘要任务。"""
    target_date = req.target_date
    if not target_date:
        target_date = (db_now_naive().date() - timedelta(days=1)).isoformat()

    created = generate_daily_digest_for_date(
        target_date=target_date,
        user_id=req.user_id,
        force=req.force,
    )
    return {
        "status": "ok",
        "target_date": target_date,
        "force": req.force,
        "created_sessions": created,
    }


@router.get("/memory/recall")
def recall_memory(
    keyword: str,
    user_id: str = "",
    session_id: str = "",
    digest_date: str = "",
    date_start: str = "",
    date_end: str = "",
    limit: int = 20,
    reveal_to_level: int = 2,
    include_content: bool = False,
    include_legacy: bool = False,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    记忆召回：优先命中 level=2（紧凑层），再按需向 level=1/0 展开。
    返回每条结果的置信度和来源日志范围。
    """
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword is required")
    digest_date, date_start, date_end = _validate_memory_digest_date_filters(
        digest_date=digest_date,
        date_start=date_start,
        date_end=date_end,
    )

    results = MemoryDigestRetrievalService(db).recall(
        keyword=keyword,
        user_id=user_id,
        session_id=session_id,
        digest_date=digest_date,
        date_start=date_start,
        date_end=date_end,
        limit=limit,
        reveal_to_level=reveal_to_level,
        include_content=include_content,
        include_legacy=include_legacy,
    )

    # Also recall AI daily artifacts from SQL tool logs.
    news_hits = (
        db.query(ChatLog)
        .filter(
            ChatLog.role == "tool",
            ChatLog.content.like("%[ai_daily]%"),
            ChatLog.content.like(f"%{keyword}%"),
        )
        .order_by(ChatLog.id.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    news_items = []
    for row in news_hits:
        news_items.append(
            {
                "log_id": row.id,
                "user_id": row.user_id,
                "session_id": row.session_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "confidence": _calc_recall_confidence(keyword, row.content or "", {}),
                "source_range": {
                    "start_log_id": row.id,
                    "end_log_id": row.id,
                },
                "content": row.content if include_content else None,
            }
        )

    return {
        "status": "ok",
        "keyword": keyword,
        "digest_hits": len(results),
        "news_hits": len(news_items),
        "items": results,
        "news_items": news_items,
    }
